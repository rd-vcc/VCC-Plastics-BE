"""Production Execution (screen 1.7): run Released Work Orders at the machine and record what happens.

Rules (spec 1.7.2 / 1.6.2):
- Only RELEASED Work Orders can start. Start requires the machine ready (not maintenance / down),
  the mold usable, material reserved / issued for every requirement line, and no other running
  Work Order on the same machine. First-off approval is added with the Quality module.
- Start records start time, operator, leader, machine, mold and shift. Output / reject are reported
  on the screen until PLC / IoT feed the same events; every event goes to production_execution_logs.
- Output consumes material at the BOM standard (Material Usage back-flush) and adds shots to the mold.
- Start installs the mold on the machine and End removes it (Mold Management, reason END_OF_PLAN).
- End Production completes the Work Order, releases left-over reservations and stores yield / OEE.
OEE = Availability x Performance x Quality:
    Availability = run time / (run + stop), stop = time on hold
    Performance  = ideal cycle x shots / run time (capped at 100 %)
    Quality      = good / (good + reject)
"""

import math
from app.core.employee_names import employee_label
from datetime import datetime

from fastapi import HTTPException

from app.modules.machine import service as machine
from app.modules.mold import service as mold_service
from app.modules.quality import service as quality_service
from app.modules.material.usage.service import backflush
from app.modules.production.orders.service import sync_order_status
from app.modules.material.requirements.service import sync_work_order
from app.modules.production.planning.service import BLOCKED_MACHINE_STATUSES, USABLE_MOLD_STATUSES, write_audit
from app.modules.production.work_orders import service as wo_service
from app.modules.production.work_orders.service import load_shifts, shift_of

STALE = wo_service.STALE


def log_event(cur, wo, event_type, actor, good=0, reject=0, cycle=None, reason_code_id=None, reason_text=None, source="MANUAL"):
    cur.execute(
        """INSERT INTO production_execution_logs (work_order_id, event_type, good_qty, reject_qty, cycle_time_sec, reason_code_id, reason_text,
               machine_id, mold_id, shift_id, operator_code, source, actor)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (wo["id"], event_type, good, reject, cycle, reason_code_id, reason_text, wo["machine_id"], wo["mold_id"], wo.get("shift_id"),
         wo.get("operator_code"), source, actor or "SYSTEM"))


def _set_machine(cur, machine_id, status, wo_id=None, remark=None):
    """Machine status from MES events; maintenance / down / offline set by other sources are kept."""
    if not machine_id:
        return
    if status == "IDLE":
        cur.execute("SELECT COUNT(*) AS n FROM production_plan_details WHERE machine_id=%s AND status='IN_PRODUCTION'", (machine_id,))
        if cur.fetchone()["n"]:
            return
    cur.execute("SELECT operational_status FROM equipment WHERE id=%s", (machine_id,))
    row = cur.fetchone()
    if row and row["operational_status"] in {"MAINTENANCE", "OFFLINE"}:
        return
    cur.execute("SELECT COUNT(*) AS n FROM downtime_events WHERE equipment_id=%s AND status='OPEN'", (machine_id,))
    if cur.fetchone()["n"] and status in {"RUNNING", "IDLE"}:
        return
    machine.set_machine_status(cur, machine_id, status, "MES", wo_id, remark)


def _bump(cur, wo, version, sets, params, actor):
    cur.execute(f"UPDATE production_plan_details SET {sets}, updated_by=%s, version=version+1 WHERE id=%s AND version=%s",
                params + (actor, wo["id"], version))
    if cur.rowcount == 0:
        raise HTTPException(status_code=409, detail=STALE)


def _sync(cur, wo, actor):
    sync_order_status(cur, wo["production_order_id"], actor or "SYSTEM")
    sync_work_order(cur, wo["id"], actor or "SYSTEM")


def readiness(cur, wo):
    """Blocking reasons for starting production."""
    reasons = []
    if not wo["machine_id"]:
        reasons.append("NO_MACHINE")
    elif wo["machine_status"] in BLOCKED_MACHINE_STATUSES:
        reasons.append("MACHINE_NOT_READY")
    if not wo["mold_id"]:
        reasons.append("NO_MOLD")
    elif wo["mold_status"] not in USABLE_MOLD_STATUSES:
        reasons.append("MOLD_NOT_READY")
    elif wo["machine_id"]:
        elsewhere = mold_service.busy_elsewhere(cur, wo["mold_id"], wo["machine_id"])
        if elsewhere:
            reasons.append(f"MOLD_BUSY:{elsewhere}")
    if not wo["material_lines"] or wo["material_open_lines"]:
        reasons.append("MATERIAL_NOT_READY")
    if wo["machine_id"]:
        cur.execute("SELECT wo_no FROM production_plan_details WHERE machine_id=%s AND status='IN_PRODUCTION' AND id<>%s LIMIT 1", (wo["machine_id"], wo["id"]))
        other = cur.fetchone()
        if other:
            reasons.append(f"MACHINE_BUSY:{other['wo_no']}")
    return reasons


# =========================================================
# ACTIONS
# =========================================================


def start(cur, wo_id, operator, leader, actor, version):
    wo = wo_service.get_wo(cur, wo_id, for_update=True)
    if wo["status"] != "RELEASED":
        raise HTTPException(status_code=409, detail="Only released work orders can start production.")
    blocked = readiness(cur, wo)
    if blocked:
        raise HTTPException(status_code=409, detail=f"Not ready to start: {', '.join(blocked)}.")
    shift = shift_of(datetime.now(), load_shifts(cur))
    _bump(cur, wo, version, "status='IN_PRODUCTION', started_at=COALESCE(started_at, NOW()), operator_code=%s, leader_code=%s, shift_id=%s, last_output_at=NOW()",
          (operator, leader, shift["id"] if shift else None), actor)
    wo.update(operator_code=operator, shift_id=shift["id"] if shift else None)
    operator_text, leader_text = employee_label(cur, operator), employee_label(cur, leader)
    log_event(cur, wo, "START", actor, reason_text=f"Operator {operator_text}" + (f", leader {leader_text}" if leader else ""))
    write_audit(cur, "WORK_ORDER", wo_id, "START", "RELEASED", "IN_PRODUCTION", actor, f"Operator {operator_text}")
    _set_machine(cur, wo["machine_id"], "RUNNING", wo_id, f"{wo['wo_no']} started")
    mold_service.on_wo_start(cur, wo, operator, actor)
    quality_service.on_wo_start(cur, wo, actor)
    _sync(cur, wo, actor)


def output(cur, wo_id, good, reject, reason_code_id, reason_text, actor, version):
    wo = wo_service.get_wo(cur, wo_id, for_update=True)
    if wo["status"] != "IN_PRODUCTION":
        raise HTTPException(status_code=409, detail="Output can only be reported while the work order is in production.")
    if good < 0 or reject < 0 or good + reject == 0:
        raise HTTPException(status_code=400, detail="Enter a good or reject quantity.")
    if reject and not (reason_text or reason_code_id):
        raise HTTPException(status_code=400, detail="A reject reason is required.")
    cavity = max(int(wo["cavity"] or 1), 1)
    shots = math.ceil((good + reject) / cavity)
    since = wo["last_output_at"] or wo["started_at"]
    cycle = round((datetime.now() - since).total_seconds() / shots, 2) if since and shots else None
    _bump(cur, wo, version, "actual_qty=actual_qty+%s, reject_qty=reject_qty+%s, last_output_at=NOW()", (good, reject), actor)
    mold_service.add_shots(cur, wo["mold_id"], shots)
    log_event(cur, wo, "OUTPUT" if good else "REJECT", actor, good, reject, cycle if cycle and cycle < 3600 else None, reason_code_id, reason_text)
    quality_service.on_production_reject(cur, wo, reject, reason_code_id, reason_text, actor)
    _sync(cur, wo, actor)
    return backflush(cur, wo_id, actor)


def reject(cur, wo_id, qty, from_good, reason_code_id, reason_text, actor, version):
    """Record reject pieces; from_good moves pieces already counted as good (found at inspection)."""
    wo = wo_service.get_wo(cur, wo_id, for_update=True)
    if wo["status"] not in {"IN_PRODUCTION", "ON_HOLD"}:
        raise HTTPException(status_code=409, detail="Reject can only be recorded on a running or on-hold work order.")
    if qty <= 0:
        raise HTTPException(status_code=400, detail="Reject quantity must be greater than zero.")
    if from_good:
        if qty > int(wo["actual_qty"] or 0):
            raise HTTPException(status_code=409, detail="Cannot move more pieces than the good quantity.")
        _bump(cur, wo, version, "actual_qty=actual_qty-%s, reject_qty=reject_qty+%s", (qty, qty), actor)
        log_event(cur, wo, "REJECT", actor, -qty, qty, None, reason_code_id, reason_text)
        quality_service.on_production_reject(cur, wo, qty, reason_code_id, reason_text, actor)
        _sync(cur, wo, actor)
        return {"created": [], "not_at_machine": []}
    if wo["status"] != "IN_PRODUCTION":
        raise HTTPException(status_code=409, detail="New reject pieces can only be reported while in production.")
    return output(cur, wo_id, 0, qty, reason_code_id, reason_text, actor, version)


def adjust(cur, wo_id, good_total, reject_total, reason, actor, version):
    wo = wo_service.get_wo(cur, wo_id, for_update=True)
    if wo["status"] not in {"IN_PRODUCTION", "ON_HOLD", "COMPLETED"}:
        raise HTTPException(status_code=409, detail="Quantities can only be adjusted on running, on-hold or completed work orders.")
    if good_total < 0 or reject_total < 0:
        raise HTTPException(status_code=400, detail="Quantities cannot be negative.")
    dg, dr = good_total - int(wo["actual_qty"] or 0), reject_total - int(wo["reject_qty"] or 0)
    if dg == 0 and dr == 0:
        return {"created": [], "not_at_machine": []}
    _bump(cur, wo, version, "actual_qty=%s, reject_qty=%s", (good_total, reject_total), actor)
    log_event(cur, wo, "QTY_ADJUST", actor, dg, dr, None, None, reason)
    write_audit(cur, "WORK_ORDER", wo_id, "QTY_ADJUST", wo["status"], wo["status"], actor,
                f"good {wo['actual_qty']} -> {good_total}, reject {wo['reject_qty']} -> {reject_total}: {reason}")
    _sync(cur, wo, actor)
    return backflush(cur, wo_id, actor) if dg > 0 or dr > 0 else {"created": [], "not_at_machine": []}


def hold(cur, wo_id, reason_code_id, reason_text, actor, version):
    wo = wo_service.get_wo(cur, wo_id)
    wo_service.hold(cur, wo_id, reason_text, actor, version)
    log_event(cur, wo, "HOLD", actor, reason_code_id=reason_code_id, reason_text=reason_text)
    if wo["status"] == "IN_PRODUCTION" and wo["machine_id"]:
        # a stopped running Work Order is a downtime of its machine (Downtime Management)
        machine.open_downtime(cur, wo["machine_id"], actor, reason_code_id, reason_text, source="MES", work_order_id=wo_id)
    else:
        _set_machine(cur, wo["machine_id"], "IDLE")


def resume(cur, wo_id, actor, version, remark=None):
    wo = wo_service.get_wo(cur, wo_id)
    if wo["status"] == "ON_HOLD" and wo["hold_from_status"] == "IN_PRODUCTION":
        blocked = [r for r in readiness(cur, wo) if r.startswith(("MACHINE", "NO_", "MOLD"))]
        if blocked:
            raise HTTPException(status_code=409, detail=f"Not ready to resume: {', '.join(blocked)}.")
    wo_service.resume(cur, wo_id, actor, version, remark)
    machine.close_open_downtime_for_wo(cur, wo_id, actor)
    after = wo_service.get_wo(cur, wo_id)
    cur.execute("UPDATE production_plan_details SET last_output_at=NOW() WHERE id=%s", (wo_id,))
    log_event(cur, after, "RESUME", actor, reason_text=remark or wo["hold_reason"])
    if after["status"] == "IN_PRODUCTION":
        _set_machine(cur, wo["machine_id"], "RUNNING")


def end(cur, wo_id, remark, actor, version):
    wo = wo_service.get_wo(cur, wo_id, for_update=True)
    if wo["status"] not in {"IN_PRODUCTION", "ON_HOLD"}:
        raise HTTPException(status_code=409, detail="Only running or on-hold work orders can end production.")
    if int(wo["actual_qty"] or 0) + int(wo["reject_qty"] or 0) == 0:
        raise HTTPException(status_code=409, detail="No output reported yet.")
    result = backflush(cur, wo_id, actor)
    wo_service._release_reservations(cur, wo_id, "Production ended", actor)
    _bump(cur, wo, version, "status='COMPLETED', completed_at=NOW(), hold_from_status=NULL", (), actor)
    metrics = compute_metrics({**wo, "status": "COMPLETED", "completed_at": datetime.now()}, logs_of(cur, wo_id), datetime.now())
    summary = (f"good {wo['actual_qty']}, reject {wo['reject_qty']}, yield {metrics['yield_pct']}%, OEE {metrics['oee_pct']}%"
               + (f" · {remark}" if remark else ""))
    log_event(cur, wo, "END", actor, reason_text=summary)
    machine.close_open_downtime_for_wo(cur, wo_id, actor)
    write_audit(cur, "WORK_ORDER", wo_id, "COMPLETE", wo["status"], "COMPLETED", actor, summary)
    _set_machine(cur, wo["machine_id"], "IDLE")
    mold_service.on_wo_end(cur, wo, actor)
    quality_service.on_wo_end(cur, wo, actor)
    _sync(cur, wo, actor)
    return result


def note(cur, wo_id, text, actor):
    wo = wo_service.get_wo(cur, wo_id)
    log_event(cur, wo, "NOTE", actor, reason_text=text)


# =========================================================
# METRICS
# =========================================================


def logs_of(cur, wo_id):
    cur.execute("""SELECT l.*, r.reason_code, r.reason_name FROM production_execution_logs l LEFT JOIN reason_codes r ON r.id = l.reason_code_id
                   WHERE l.work_order_id=%s ORDER BY l.event_at, l.id""", (wo_id,))
    return cur.fetchall()


def compute_metrics(wo, logs, now):
    start = wo["started_at"]
    good, rej = int(wo.get("good_qty", wo["actual_qty"]) or 0), int(wo["reject_qty"] or 0)
    base = {"run_seconds": 0, "stop_seconds": 0, "availability_pct": None, "performance_pct": None, "quality_pct": None,
            "oee_pct": None, "yield_pct": round(good / (good + rej) * 100, 2) if good + rej else None, "avg_cycle_sec": None, "shots": 0}
    if not start:
        return base
    end = wo["completed_at"] if wo["status"] in {"COMPLETED", "CLOSED"} and wo["completed_at"] else now
    stop, hold_at = 0.0, None
    for ev in logs:
        if ev["event_type"] == "HOLD" and hold_at is None:
            hold_at = ev["event_at"]
        elif ev["event_type"] in {"RESUME", "END"} and hold_at is not None:
            stop += (ev["event_at"] - hold_at).total_seconds()
            hold_at = None
    if hold_at is not None:
        stop += (end - hold_at).total_seconds()
    elapsed = max((end - start).total_seconds(), 1)
    run = max(elapsed - stop, 0)
    shots = math.ceil((good + rej) / max(int(wo["cavity"] or 1), 1))
    availability = run / elapsed
    performance = min(float(wo["cycle_time_sec"]) * shots / run, 1.0) if run and shots else None
    quality = good / (good + rej) if good + rej else None
    oee = availability * performance * quality if performance is not None and quality is not None else None
    return {**base, "run_seconds": int(run), "stop_seconds": int(stop), "shots": shots,
            "availability_pct": round(availability * 100, 1), "performance_pct": round(performance * 100, 1) if performance is not None else None,
            "quality_pct": round(quality * 100, 2) if quality is not None else None, "oee_pct": round(oee * 100, 1) if oee is not None else None,
            "avg_cycle_sec": round(run / shots, 1) if shots else None}
