"""Work Order Management (screen 1.6): the shop-floor command center for Work Orders.

Stored status (production_plan_details.status):
    UNSCHEDULED (Created) -> SCHEDULED (Queued) -> RELEASED -> IN_PRODUCTION -> COMPLETED -> CLOSED
    ON_HOLD from Queued / Released / In Production (resumes to where it was), CANCELLED.
Derived exception states (spec 1.6.2), never stored:
    DELAYED              not finished and past its planned end / due time
    WAITING_MAINTENANCE  machine in MAINTENANCE / DOWN / OFFLINE
    WAITING_MOLD         mold missing or not AVAILABLE / IN_PRODUCTION
    WAITING_MATERIAL     a material requirement line is not reserved / ready
Every action is written to production_planning_audit_log (actor, time, from, to, reason).
"""

from datetime import datetime, timedelta

from fastapi import HTTPException

from app.modules.material.allocations.service import release as release_allocation
from app.modules.material.requirements.service import sync_work_order
from app.modules.production.orders.service import sync_order_status
from app.modules.production.planning.service import BLOCKED_MACHINE_STATUSES, USABLE_MOLD_STATUSES, write_audit

STATUSES = ["UNSCHEDULED", "SCHEDULED", "RELEASED", "IN_PRODUCTION", "ON_HOLD", "COMPLETED", "CLOSED", "CANCELLED"]
FINAL = {"COMPLETED", "CLOSED", "CANCELLED"}
OPEN = {"UNSCHEDULED", "SCHEDULED", "RELEASED", "IN_PRODUCTION", "ON_HOLD"}
PRIORITIES = ["URGENT", "HIGH", "MEDIUM", "LOW"]
STALE = "This work order was changed by another user. Reload and try again."

WO_SELECT = """
    SELECT d.*, p.product_code, p.product_name, p.image_url AS product_image,
           po.order_no, po.status AS order_status, po.customer_id, cu.customer_name, cu.short_name AS customer_short_name,
           e.equipment_code AS machine_code, e.equipment_name AS machine_name, e.operational_status AS machine_status,
           e.image_url AS machine_image, e.manufacturer AS machine_manufacturer, e.model AS machine_model,
           et.type_name AS machine_type, fn.name AS area_name, fn.id AS area_id,
           mo.mold_code, mo.mold_name, mo.status AS mold_status, mo.image_url AS mold_image, mo.mold_type,
           mo.cavity_count AS mold_cavity, mo.current_shot, mo.design_shot,
           pl.plan_code, pl.status AS plan_status,
           (SELECT COUNT(*) FROM material_requirements r WHERE r.work_order_id = d.id AND r.status NOT IN ('CANCELLED','RESERVED','READY')
               AND r.open_qty + r.adjustment_qty > 0) AS material_open_lines,
           (SELECT COUNT(*) FROM material_requirements r WHERE r.work_order_id = d.id AND r.status <> 'CANCELLED') AS material_lines
    FROM production_plan_details d
    JOIN products p ON p.id = d.product_id
    LEFT JOIN production_orders po ON po.id = d.production_order_id
    LEFT JOIN customers cu ON cu.id = po.customer_id
    LEFT JOIN equipment e ON e.id = d.machine_id
    LEFT JOIN equipment_types et ON et.id = e.equipment_type_id
    LEFT JOIN factory_structure_nodes fn ON fn.id = e.factory_node_id
    LEFT JOIN molds mo ON mo.id = d.mold_id
    LEFT JOIN production_plans pl ON pl.id = d.plan_id
"""


# =========================================================
# READ MODEL
# =========================================================


def load_shifts(cur):
    cur.execute("SELECT id, shift_code, shift_name, start_time, end_time, is_overnight FROM shifts WHERE status='ACTIVE' ORDER BY start_time")
    rows = []
    for s in cur.fetchall():
        rows.append({**s, "start_min": int(s["start_time"].total_seconds() // 60), "end_min": int(s["end_time"].total_seconds() // 60)})
    return rows


def shift_of(moment, shifts):
    if not moment:
        return None
    minute = moment.hour * 60 + moment.minute
    for s in shifts:
        a, b = s["start_min"], s["end_min"]
        if (a <= minute < b) if a < b else (minute >= a or minute < b):
            return s
    return None


def _exceptions(wo, now):
    """Derived exception states of one Work Order."""
    out = []
    if wo["status"] in FINAL:
        return out
    end = wo["planned_end"] or wo["due_date"]
    if end and now > end:
        out.append("DELAYED")
    elif wo["due_date"] and wo["planned_end"] and wo["planned_end"] > wo["due_date"]:
        out.append("DELAYED")
    if wo["status"] in {"SCHEDULED", "RELEASED", "ON_HOLD", "IN_PRODUCTION"}:
        if wo["machine_id"] and wo["machine_status"] in BLOCKED_MACHINE_STATUSES:
            out.append("WAITING_MAINTENANCE")
        if not wo["mold_id"] or (wo["mold_status"] and wo["mold_status"] not in USABLE_MOLD_STATUSES):
            out.append("WAITING_MOLD")
        if wo["status"] != "IN_PRODUCTION" and (wo["material_open_lines"] or not wo["material_lines"]):
            out.append("WAITING_MATERIAL")
    return out


def decorate(wo, now, shifts):
    planned, good, reject = int(wo["planned_qty"]), int(wo["actual_qty"] or 0), int(wo["reject_qty"] or 0)
    exceptions = _exceptions(wo, now)
    end = wo["planned_end"] or wo["due_date"]
    delay_minutes = 0
    if "DELAYED" in exceptions:
        if end and now > end:
            delay_minutes = int((now - end).total_seconds() // 60)
        elif wo["due_date"] and wo["planned_end"]:
            delay_minutes = int((wo["planned_end"] - wo["due_date"]).total_seconds() // 60)
    reason = None
    if "DELAYED" in exceptions:
        waiting = [e for e in exceptions if e.startswith("WAITING_")]
        reason = wo["hold_reason"] if wo["status"] == "ON_HOLD" and wo["hold_reason"] else (waiting[0] if waiting else
                  ("NOT_STARTED" if good == 0 else "BEHIND_PLAN"))
    shift = shift_of(wo["planned_start"], shifts)
    return {
        **wo,
        "cycle_time_sec": float(wo["cycle_time_sec"]), "part_weight_g": float(wo["part_weight_g"]) if wo["part_weight_g"] is not None else None,
        "good_qty": good, "reject_qty": reject, "remaining_qty": max(planned - good, 0),
        "progress_pct": round(min(good / planned * 100, 100), 1) if planned else 0.0,
        "exceptions": exceptions, "delayed": "DELAYED" in exceptions, "delay_minutes": delay_minutes, "delay_reason": reason,
        "shift_code": shift["shift_code"] if shift else None, "shift_name": shift["shift_name"] if shift else None,
        "shift_id": shift["id"] if shift else None,
        "mold_usage_pct": round(int(wo["current_shot"] or 0) / int(wo["design_shot"]) * 100, 1) if wo.get("design_shot") else None,
    }


def get_wo(cur, wo_id, for_update=False):
    cur.execute(f"{WO_SELECT} WHERE d.id=%s{' FOR UPDATE' if for_update else ''}", (wo_id,))
    wo = cur.fetchone()
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found.")
    return wo


def status_trend(cur, days, today):
    """Status counts at the end of each day, rebuilt from the audit log."""
    start = datetime.combine(today - timedelta(days=days - 1), datetime.min.time())
    cur.execute("""SELECT entity_id, to_status, acted_at FROM production_planning_audit_log
                   WHERE entity_type='WORK_ORDER' AND to_status IS NOT NULL ORDER BY acted_at, id""")
    events = cur.fetchall()
    cur.execute("SELECT id, status, created_at FROM production_plan_details")
    current = {r["id"]: r for r in cur.fetchall()}
    out = []
    for i in range(days):
        day_end = start + timedelta(days=i + 1)
        state = {}
        for ev in events:
            if ev["acted_at"] >= day_end:
                break
            state[ev["entity_id"]] = ev["to_status"]
        # Work Orders without any audit row: count them in their current status from creation
        for wo_id, wo in current.items():
            if wo_id not in state and wo["created_at"] < day_end:
                state[wo_id] = wo["status"]
        counts = {s: 0 for s in STATUSES}
        for s in state.values():
            if s in counts:
                counts[s] += 1
        out.append({"date": (start + timedelta(days=i)).date().isoformat(), **counts})
    return out


# =========================================================
# ACTIONS
# =========================================================


def _update(cur, wo, version, sets, params, actor):
    cur.execute(f"UPDATE production_plan_details SET {sets}, updated_by=%s, version=version+1 WHERE id=%s AND version=%s",
                params + (actor, wo["id"], version))
    if cur.rowcount == 0:
        raise HTTPException(status_code=409, detail=STALE)


def _after(cur, wo, actor):
    sync_order_status(cur, wo["production_order_id"], actor or "SYSTEM")
    sync_work_order(cur, wo["id"], actor or "SYSTEM")


def release_check(wo):
    """Fields that must be defined before release (spec 1.6.2)."""
    missing = [name for name, value in (("machine", wo["machine_id"]), ("mold", wo["mold_id"]), ("product", wo["product_id"]),
                                         ("planned_qty", wo["planned_qty"]), ("planned_start", wo["planned_start"]), ("due_time", wo["due_date"])) if not value]
    return missing


def release(cur, wo_id, actor, version, remark=None):
    wo = get_wo(cur, wo_id, for_update=True)
    if wo["status"] != "SCHEDULED":
        raise HTTPException(status_code=409, detail="Only queued (scheduled) work orders can be released.")
    missing = release_check(wo)
    if missing:
        raise HTTPException(status_code=409, detail=f"Define before release: {', '.join(missing)}.")
    if wo["order_status"] not in {"RELEASED", "SCHEDULING", "IN_PRODUCTION"}:
        raise HTTPException(status_code=409, detail="The production order is not released.")
    _update(cur, wo, version, "status='RELEASED', released_at=NOW()", (), actor)
    write_audit(cur, "WORK_ORDER", wo_id, "RELEASE", wo["status"], "RELEASED", actor, remark)
    _after(cur, wo, actor)


def hold(cur, wo_id, reason, actor, version):
    wo = get_wo(cur, wo_id, for_update=True)
    if wo["status"] not in {"SCHEDULED", "RELEASED", "IN_PRODUCTION"}:
        raise HTTPException(status_code=409, detail="Only queued, released or running work orders can be put on hold.")
    _update(cur, wo, version, "status='ON_HOLD', hold_from_status=%s, hold_reason=%s, held_at=NOW()", (wo["status"], reason), actor)
    write_audit(cur, "WORK_ORDER", wo_id, "HOLD", wo["status"], "ON_HOLD", actor, reason)
    _after(cur, wo, actor)


def resume(cur, wo_id, actor, version, remark=None):
    wo = get_wo(cur, wo_id, for_update=True)
    if wo["status"] != "ON_HOLD":
        raise HTTPException(status_code=409, detail="The work order is not on hold.")
    target = wo["hold_from_status"] if wo["hold_from_status"] in {"SCHEDULED", "RELEASED", "IN_PRODUCTION"} else "RELEASED"
    if target == "RELEASED" and int(wo["actual_qty"] or 0) > 0:
        target = "IN_PRODUCTION"
    _update(cur, wo, version, "status=%s, hold_from_status=NULL, held_at=NULL", (target,), actor)
    write_audit(cur, "WORK_ORDER", wo_id, "RESUME", "ON_HOLD", target, actor, remark or wo["hold_reason"])
    _after(cur, wo, actor)


def _material_at_machine(cur, wo_id):
    cur.execute("""SELECT COALESCE(SUM(issued_qty - returned_qty - consumed_qty), 0) AS qty FROM material_allocations
                   WHERE work_order_id=%s AND status='IN_USE'""", (wo_id,))
    return float(cur.fetchone()["qty"] or 0)


def _release_reservations(cur, wo_id, reason, actor):
    cur.execute("SELECT id, version FROM material_allocations WHERE work_order_id=%s AND reserved_qty > 0 AND status IN ('RESERVED','IN_USE')", (wo_id,))
    for a in cur.fetchall():
        release_allocation(cur, a["id"], reason, actor, a["version"])


def cancel(cur, wo_id, reason, actor, version):
    wo = get_wo(cur, wo_id, for_update=True)
    if wo["status"] not in {"UNSCHEDULED", "SCHEDULED", "RELEASED", "ON_HOLD"}:
        raise HTTPException(status_code=409, detail="Only created, queued, released or on-hold work orders can be cancelled.")
    if int(wo["actual_qty"] or 0) > 0:
        raise HTTPException(status_code=409, detail="The work order already reported output; complete it instead of cancelling.")
    if _material_at_machine(cur, wo_id) > 1e-3:
        raise HTTPException(status_code=409, detail="Material is still at the machine. Return it on the Material Allocation screen first.")
    _release_reservations(cur, wo_id, f"Work order cancelled: {reason}", actor)
    _update(cur, wo, version, "status='CANCELLED', cancel_reason=%s", (reason,), actor)
    write_audit(cur, "WORK_ORDER", wo_id, "CANCEL", wo["status"], "CANCELLED", actor, reason)
    _after(cur, wo, actor)


def change_priority(cur, wo_id, priority, reason, actor, version):
    wo = get_wo(cur, wo_id, for_update=True)
    if wo["status"] in FINAL:
        raise HTTPException(status_code=409, detail="Finished work orders keep their priority.")
    if priority not in PRIORITIES:
        raise HTTPException(status_code=400, detail="Unknown priority.")
    if priority == wo["priority"]:
        return
    _update(cur, wo, version, "priority=%s", (priority,), actor)
    write_audit(cur, "WORK_ORDER", wo_id, "PRIORITY", wo["status"], wo["status"], actor, f"{wo['priority']} -> {priority}: {reason}")


def complete(cur, wo_id, reason, actor, version):
    """Complete short (planned quantity not reached)."""
    wo = get_wo(cur, wo_id, for_update=True)
    if wo["status"] not in {"IN_PRODUCTION", "ON_HOLD", "RELEASED"}:
        raise HTTPException(status_code=409, detail="Only released, running or on-hold work orders can be completed.")
    if wo["status"] != "IN_PRODUCTION" and int(wo["actual_qty"] or 0) == 0:
        raise HTTPException(status_code=409, detail="No output reported yet; cancel the work order instead.")
    _release_reservations(cur, wo_id, f"Work order completed: {reason}", actor)
    _update(cur, wo, version, "status='COMPLETED', completed_at=NOW(), hold_from_status=NULL", (), actor)
    write_audit(cur, "WORK_ORDER", wo_id, "COMPLETE", wo["status"], "COMPLETED", actor, reason)
    _after(cur, wo, actor)


def close(cur, wo_id, actor, version, remark=None):
    wo = get_wo(cur, wo_id, for_update=True)
    if wo["status"] != "COMPLETED":
        raise HTTPException(status_code=409, detail="Only completed work orders can be closed.")
    if _material_at_machine(cur, wo_id) > 1e-3:
        raise HTTPException(status_code=409, detail="Material is still at the machine. Return it or close out usage on the Material Usage screen first.")
    _release_reservations(cur, wo_id, "Work order closed", actor)
    _update(cur, wo, version, "status='CLOSED', closed_at=NOW()", (), actor)
    write_audit(cur, "WORK_ORDER", wo_id, "CLOSE", "COMPLETED", "CLOSED", actor, remark)
    _after(cur, wo, actor)
