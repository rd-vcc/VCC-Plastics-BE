"""Electronic production record for one Production Order (Order Detail screen).

Everything is derived from data the system actually stores today: the order, its
Work Orders (planning), manual good/reject quantities, the BOM / material assignment and the
status / audit history. Metrics that need data the MES does not collect yet
(machine run time, downtime, ideal cycle, cost) are returned as ``None`` with a
``reason`` so the UI can say why instead of showing invented numbers.
"""

from datetime import datetime

from app.modules.production.orders.service import decorate, get_order
from app.modules.material.requirements.service import _bom_rows
from app.modules.production.planning.service import duration_seconds

TIMELINE_STEPS = ["DRAFT", "RELEASED", "SCHEDULING", "IN_PRODUCTION", "COMPLETED", "CLOSED"]
STATUS_RANK = {s: i for i, s in enumerate(TIMELINE_STEPS)}

WORK_ORDER_SELECT = """
    SELECT d.id, d.wo_no, d.status, d.plan_id, d.machine_id, d.mold_id, d.material_id, d.planned_qty, d.actual_qty,
           d.reject_qty, d.cavity, d.cycle_time_sec, d.setup_minutes, d.part_weight_g, d.planned_start, d.planned_end,
           d.due_date, d.priority, d.version,
           e.equipment_code AS machine_code, e.equipment_name AS machine_name, e.operational_status AS machine_status,
           mo.mold_code, mo.mold_name, mo.status AS mold_status, mo.current_shot, mo.design_shot,
           mat.material_code, mat.material_name, mat.unit AS material_unit,
           pl.plan_code, pl.status AS plan_status
    FROM production_plan_details d
    LEFT JOIN equipment e ON e.id = d.machine_id
    LEFT JOIN molds mo ON mo.id = d.mold_id
    LEFT JOIN materials mat ON mat.id = d.material_id
    LEFT JOIN production_plans pl ON pl.id = d.plan_id
    WHERE d.production_order_id=%s
    ORDER BY d.planned_start IS NULL, d.planned_start, d.wo_no
"""


def _pct(value, total, digits=1):
    return round(value / total * 100, digits) if total else None


def _timeline(order, history):
    first = {}
    for entry in sorted(history, key=lambda h: (h["acted_at"], h["id"])):
        status = "DRAFT" if entry["action"] == "CREATE" else entry["to_status"]
        if status and status not in first:
            first[status] = entry
    current = order["status"]
    steps = []
    for status in TIMELINE_STEPS:
        entry = first.get(status)
        at = entry["acted_at"] if entry else None
        actor = entry["actor"] if entry else None
        if status == "DRAFT" and not at:
            at, actor = order["created_at"], order["created_by"]
        if status == "RELEASED" and not at and order["released_at"]:
            at, actor = order["released_at"], order["released_by"]
        if status == "COMPLETED" and not at and order["completed_at"]:
            at = order["completed_at"]
        if status == "CLOSED" and not at and order["closed_at"]:
            at, actor = order["closed_at"], order["closed_by"]
        reached = current != "CANCELLED" and STATUS_RANK[status] <= STATUS_RANK.get(current, -1)
        steps.append({"status": status, "at": at, "actor": actor, "done": reached and status != current,
                      "current": status == current, "skipped": reached and not at})
    if current == "CANCELLED":
        steps = [s for s in steps if s["at"]]
        steps.append({"status": "CANCELLED", "at": order["cancelled_at"], "actor": order["cancelled_by"],
                      "done": False, "current": True, "skipped": False, "remark": order["cancel_reason"]})
    return steps


def build_record(cur, order_id: int):
    now = datetime.now()
    order = decorate(get_order(cur, order_id), now)

    cur.execute(WORK_ORDER_SELECT, (order_id,))
    work_orders = []
    for w in cur.fetchall():
        produced = int(w["actual_qty"] or 0) + int(w["reject_qty"] or 0)
        work_orders.append({
            **w,
            "progress_pct": round(min(int(w["actual_qty"] or 0) / max(int(w["planned_qty"]), 1) * 100, 100), 1),
            "produced_qty": produced,
            "duration_minutes": round(duration_seconds(w["planned_qty"], w["cavity"], w["cycle_time_sec"], w["setup_minutes"]) / 60),
        })
    active = [w for w in work_orders if w["status"] != "CANCELLED"]
    active_wos = list(active)

    cur.execute("SELECT * FROM production_order_status_history WHERE production_order_id=%s ORDER BY acted_at DESC, id DESC", (order_id,))
    history = cur.fetchall()
    cur.execute("SELECT * FROM production_order_notes WHERE production_order_id=%s ORDER BY created_at DESC, id DESC", (order_id,))
    notes = cur.fetchall()
    wo_ids = [w["id"] for w in work_orders]
    wo_log = []
    if wo_ids:
        cur.execute(
            f"""SELECT a.*, d.wo_no FROM production_planning_audit_log a
                JOIN production_plan_details d ON d.id = a.entity_id
                WHERE a.entity_type='WORK_ORDER' AND a.entity_id IN ({','.join(['%s'] * len(wo_ids))})""",
            tuple(wo_ids),
        )
        wo_log = cur.fetchall()
    activity = sorted(
        [{"source": "ORDER", "wo_no": None, "action": h["action"], "from_status": h["from_status"], "to_status": h["to_status"],
          "actor": h["actor"], "at": h["acted_at"], "remark": h["remark"]} for h in history]
        + [{"source": "WORK_ORDER", "wo_no": a["wo_no"], "action": a["action"], "from_status": a["from_status"],
            "to_status": a["to_status"], "actor": a["actor"], "at": a["acted_at"], "remark": a["remark"]} for a in wo_log],
        key=lambda x: x["at"], reverse=True,
    )

    # ---- output / quality ----------------------------------------------------
    qty = int(order["order_qty"])
    good, reject = order["good_qty"], order["reject_qty"]
    total = good + reject
    output = {
        "order_qty": qty, "good_qty": good, "reject_qty": reject, "total_qty": total,
        "remaining_qty": max(qty - good, 0), "progress_pct": order["progress_pct"], "yield_pct": _pct(good, total),
    }
    quality = {
        "defect_rate_pct": _pct(reject, total, 2),
        "ppm": round(reject / total * 1_000_000) if total else None,
        "fpy_pct": _pct(good, total),
        "target_defect_rate_pct": None, "target_ppm": None,
        "reason": "No product quality target is defined in Quality Standard Master yet.",
        "by_work_order": [{"wo_no": w["wo_no"], "good_qty": int(w["actual_qty"] or 0), "reject_qty": int(w["reject_qty"] or 0),
                           "defect_rate_pct": _pct(int(w["reject_qty"] or 0), w["produced_qty"], 2)} for w in active],
    }

    # ---- material: plan = order material assignment (4.6); usage = BOM standard x produced ----
    cur.execute(
        """SELECT pom.*, m.material_code, m.material_name, m.unit AS material_unit
           FROM production_order_materials pom JOIN materials m ON m.id = pom.material_id
           WHERE pom.production_order_id=%s AND pom.status<>'CANCELLED' ORDER BY m.material_code""",
        (order_id,))
    assigned = cur.fetchall()
    bom_id = order.get("bom_id")
    if not bom_id:
        cur.execute("SELECT id FROM boms WHERE product_id=%s AND status='ACTIVE' LIMIT 1", (order["product_id"],))
        active_bom = cur.fetchone()
        bom_id = active_bom["id"] if active_bom else None
    produced_total = sum(w["produced_qty"] for w in active_wos)
    standard = {r["material_id"]: r for r in (_bom_rows(cur, bom_id, produced_total) if bom_id and produced_total else [])}
    preview = {r["material_id"]: r for r in (_bom_rows(cur, bom_id, int(order["order_qty"])) if bom_id and not assigned else [])}
    # actual consumption recorded in Material Usage (all active Work Orders of the order)
    recorded = {}
    wo_ids = [w["id"] for w in active_wos]
    cur.execute("SHOW TABLES LIKE 'material_usages'")
    if wo_ids and cur.fetchone():
        cur.execute(f"""SELECT material_id, SUM(qty) AS qty FROM material_usages WHERE status='ACTIVE'
                        AND work_order_id IN ({','.join(['%s'] * len(wo_ids))}) GROUP BY material_id""", tuple(wo_ids))
        recorded = {r["material_id"]: float(r["qty"]) for r in cur.fetchall()}
    material_rows = []
    source = assigned or [{"material_id": mid, "material_code": r["material_code"], "material_name": r["material_name"],
                           "material_unit": r["unit"], "required_qty": r["required_qty"], "status": "PREVIEW"} for mid, r in preview.items()]
    for line in source:
        planned = round(float(line["required_qty"]), 3)
        std = round(standard.get(line["material_id"], {}).get("required_qty", 0.0), 3)
        used = round(recorded.get(line["material_id"], 0.0), 3) if recorded else std
        material_rows.append({
            "material_id": line["material_id"], "material_code": line["material_code"], "material_name": line["material_name"],
            "unit": line["material_unit"] or "KG", "planned_qty": planned, "actual_qty": used,
            "standard_qty": std, "diff_qty": round(used - std, 3) if line["material_id"] in recorded else None,
            "diff_pct": _pct(used - std, std, 2) if line["material_id"] in recorded and std else None,
            "remaining_qty": round(max(planned - used, 0), 3), "usage_pct": _pct(used, planned),
            "assignment_status": line.get("status"), "missing_weight": False,
            "wo_nos": [w["wo_no"] for w in active_wos],
        })
    weight_rows = [m for m in material_rows if str(m["unit"]).upper() == "KG"]
    planned_total = round(sum(m["planned_qty"] for m in weight_rows), 3)
    actual_total = round(sum(m["actual_qty"] for m in weight_rows), 3)
    material = {
        "planned_qty": planned_total, "actual_qty": actual_total, "remaining_qty": round(max(planned_total - actual_total, 0), 3),
        "usage_pct": _pct(actual_total, planned_total), "unit": "KG", "rows": material_rows,
        "basis": "ACTUAL" if recorded else "BOM_STANDARD", "bom_revision": order.get("bom_revision"), "has_assignment": bool(assigned),
        "reason": "Usage is the BOM standard for the pieces produced until actual consumption is recorded (Material Usage).",
    }

    # ---- machines / molds ---------------------------------------------------------
    machines, molds = {}, {}
    for w in active:
        if w["machine_id"]:
            m = machines.setdefault(w["machine_id"], {"id": w["machine_id"], "code": w["machine_code"], "name": w["machine_name"],
                                                      "status": w["machine_status"], "planned_minutes": 0, "good_qty": 0, "reject_qty": 0, "wo_nos": []})
            m["planned_minutes"] += w["duration_minutes"]; m["good_qty"] += int(w["actual_qty"] or 0); m["reject_qty"] += int(w["reject_qty"] or 0)
            m["wo_nos"].append(w["wo_no"])
        if w["mold_id"]:
            md = molds.setdefault(w["mold_id"], {"id": w["mold_id"], "code": w["mold_code"], "name": w["mold_name"], "status": w["mold_status"],
                                                 "current_shot": w["current_shot"], "design_shot": w["design_shot"], "cavity": w["cavity"], "wo_nos": []})
            md["wo_nos"].append(w["wo_no"])
    for md in molds.values():
        md["shot_usage_pct"] = _pct(int(md["current_shot"] or 0), int(md["design_shot"] or 0))
    performance = {
        "quality_pct": _pct(good, total), "availability_pct": None, "performance_pct": None, "oee_pct": None,
        "reason": "Availability, Performance and OEE need machine run time and downtime from Production Execution / IoT.",
    }

    wo_summary = {s: sum(w["status"] == s for w in work_orders) for s in ("UNSCHEDULED", "SCHEDULED", "RELEASED", "IN_PRODUCTION", "ON_HOLD", "COMPLETED", "CLOSED", "CANCELLED")}
    wo_summary["total"] = len(work_orders)
    schedule = {
        "start": min((w["planned_start"] for w in active if w["planned_start"]), default=None),
        "end": max((w["planned_end"] for w in active if w["planned_end"]), default=None),
        "planned_qty": sum(int(w["planned_qty"]) for w in active),
        "unplanned_qty": order["unplanned_qty"],
    }

    return {
        "order": order, "timeline": _timeline(order, history), "output": output, "quality": quality, "material": material,
        "performance": performance, "machines": list(machines.values()), "molds": list(molds.values()),
        "work_orders": work_orders, "wo_summary": wo_summary, "schedule": schedule,
        "notes": notes, "activity": activity, "server_time": now,
    }
