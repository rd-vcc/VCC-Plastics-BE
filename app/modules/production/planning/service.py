"""Production Planning: data loading plus the pure board computation.

``build_board`` takes already-loaded rows and returns every block shown on the
Production Planning screen (KPIs, Gantt rows, capacity, material readiness,
alerts, backlog recommendations). It performs no I/O, so the same function
serves both the live board and the what-if Simulation.
"""

import math
from datetime import datetime, time, timedelta

from fastapi import HTTPException

from app.modules.master_data.products.bom_service import get_bom_items, requirement_from_items
from app.modules.material.inventory.service import available_by_material, safety_stocks


BLOCKED_MACHINE_STATUSES = {"MAINTENANCE", "DOWN", "OFFLINE"}
USABLE_MOLD_STATUSES = {"AVAILABLE", "IN_PRODUCTION"}
ACTIVE_WO_STATUSES = ("SCHEDULED", "RELEASED", "IN_PRODUCTION", "ON_HOLD", "COMPLETED", "CLOSED")
PRIORITY_RANK = {"URGENT": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
SEVERITY_RANK = {"ERROR": 0, "WARNING": 1, "INFO": 2}

ITEM_SELECT = """
    SELECT d.*, p.product_code, p.product_name,
           mo.mold_code, mo.mold_name,
           e.equipment_code AS machine_code, e.equipment_name AS machine_name,
           e.equipment_group_id AS machine_group_id,
           mat.material_code, mat.material_name, mat.unit AS material_unit,
           po.order_no, po.status AS order_status, cu.customer_name, cu.short_name AS customer_short_name
    FROM production_plan_details d
    JOIN products p ON p.id = d.product_id
    LEFT JOIN molds mo ON mo.id = d.mold_id
    LEFT JOIN equipment e ON e.id = d.machine_id
    LEFT JOIN materials mat ON mat.id = d.material_id
    LEFT JOIN production_orders po ON po.id = d.production_order_id
    LEFT JOIN customers cu ON cu.id = po.customer_id
"""


# =========================================================
# LOOKUPS / GUARDS
# =========================================================


def get_plan(cur, plan_id: int):
    cur.execute("SELECT * FROM production_plans WHERE id=%s", (plan_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Production plan not found.")
    return row


def get_work_order(cur, wo_id: int):
    cur.execute(f"{ITEM_SELECT} WHERE d.id=%s", (wo_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Work order not found.")
    return row


def get_shift(cur, shift_id: int):
    cur.execute("SELECT * FROM shifts WHERE id=%s", (shift_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Shift not found.")
    return row


def check_duplicate_wo_no(cur, value: str, exclude_id: int | None = None):
    sql = "SELECT id FROM production_plan_details WHERE wo_no=%s"
    params = [value]
    if exclude_id is not None:
        sql += " AND id<>%s"
        params.append(exclude_id)
    cur.execute(sql, tuple(params))
    if cur.fetchone():
        raise HTTPException(status_code=409, detail="Work order number already exists.")


def ensure_plan_editable(plan):
    if plan["status"] != "DRAFT":
        raise HTTPException(status_code=409, detail="Only Draft plans can be changed. Reopen the plan first.")


def write_audit(cur, entity_type: str, entity_id: int, action: str, from_status, to_status, actor, remark=None):
    cur.execute(
        """INSERT INTO production_planning_audit_log
           (entity_type, entity_id, action, from_status, to_status, actor, remark)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (entity_type, entity_id, action, from_status, to_status, actor or "SYSTEM", remark),
    )


# =========================================================
# TIME HELPERS
# =========================================================


def _as_timedelta(value) -> timedelta:
    if isinstance(value, timedelta):
        return value
    if isinstance(value, time):
        return timedelta(hours=value.hour, minutes=value.minute, seconds=value.second)
    hours, minutes, *rest = str(value).split(":")
    return timedelta(hours=int(hours), minutes=int(minutes), seconds=int(rest[0]) if rest else 0)


def shift_window(plan_date, shift):
    day = datetime.combine(plan_date, time())
    start = day + _as_timedelta(shift["start_time"])
    end = day + _as_timedelta(shift["end_time"])
    if end <= start:
        end += timedelta(days=1)
    return start, end


def duration_seconds(planned_qty, cavity, cycle_time_sec, setup_minutes) -> int:
    shots = math.ceil(int(planned_qty) / max(int(cavity or 1), 1))
    return int(shots * float(cycle_time_sec) + int(setup_minutes or 0) * 60)


def compute_end(start: datetime, item) -> datetime:
    return start + timedelta(seconds=duration_seconds(
        item["planned_qty"], item["cavity"], item["cycle_time_sec"], item["setup_minutes"]))


def _overlap_seconds(start, end, win_start, win_end) -> float:
    if not start or not end:
        return 0.0
    return max(0.0, (min(end, win_end) - max(start, win_start)).total_seconds())


def _fmt_time(value: datetime) -> str:
    return value.strftime("%H:%M")


# =========================================================
# LOADERS
# =========================================================


def subtree_ids(nodes, root_id):
    if root_id is None:
        return None
    children = {}
    for node in nodes:
        children.setdefault(node["parent_id"], []).append(node["id"])
    result, stack = set(), [int(root_id)]
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children.get(current, []))
    return result


def load_context(cur, plan, area_id=None):
    shift = get_shift(cur, plan["shift_id"])
    win_start, win_end = shift_window(plan["plan_date"], shift)

    cur.execute("SELECT id, code, name, parent_id FROM factory_structure_nodes")
    nodes = cur.fetchall()
    scope = subtree_ids(nodes, area_id or plan["factory_node_id"])

    cur.execute(
        """SELECT e.id, e.equipment_code, e.equipment_name, e.equipment_group_id, e.factory_node_id,
                  e.operational_status, g.group_code, g.group_name
           FROM equipment e
           JOIN equipment_types t ON t.id = e.equipment_type_id
           LEFT JOIN equipment_groups g ON g.id = e.equipment_group_id
           WHERE t.category = 'MACHINE' AND e.asset_status = 'ACTIVE'
           ORDER BY g.sort_order, g.group_code, e.equipment_code"""
    )
    machines = [m for m in cur.fetchall() if scope is None or m["factory_node_id"] in scope]

    cur.execute("SELECT id, mold_code, mold_name, status, is_active, cavity_count FROM molds")
    molds = {m["id"]: m for m in cur.fetchall()}

    cur.execute("SELECT mold_id, product_id FROM mold_products WHERE status='ACTIVE'")
    mold_products = {(r["mold_id"], r["product_id"]) for r in cur.fetchall()}

    cur.execute("SELECT id, material_code, material_name, unit FROM materials")
    materials = {m["id"]: dict(m) for m in cur.fetchall()}
    stock, safety = available_by_material(cur), safety_stocks(cur)
    for material_id, material in materials.items():
        material["available_qty"] = stock[material_id]["available"] if material_id in stock else None
        material["safety_stock_qty"] = safety.get(material_id, 0.0)

    # active BOM per product + manual requirement adjustments (Material Requirement, step 2)
    cur.execute("SELECT id, product_id, base_qty, bom_revision FROM boms WHERE status='ACTIVE'")
    boms = {b["product_id"]: {**b} for b in cur.fetchall()}
    for bom in boms.values():
        bom["items"] = get_bom_items(cur, bom["id"])
    cur.execute("SELECT work_order_id, material_id, adjustment_qty FROM material_requirements WHERE status<>'CANCELLED' AND adjustment_qty<>0")
    adjustments = {(r["work_order_id"], r["material_id"]): float(r["adjustment_qty"]) for r in cur.fetchall()}
    cur.execute("SHOW TABLES LIKE 'material_allocations'")
    allocated = {}
    if cur.fetchone():
        cur.execute("""SELECT work_order_id, material_id, SUM(reserved_qty + issued_qty - returned_qty - consumed_qty) AS qty
                       FROM material_allocations WHERE status<>'CANCELLED' GROUP BY work_order_id, material_id""")
        allocated = {(r["work_order_id"], r["material_id"]): float(r["qty"] or 0) for r in cur.fetchall()}

    return {
        "plan": plan, "shift": shift, "window_start": win_start, "window_end": win_end,
        "machines": machines, "molds": molds, "mold_products": mold_products, "materials": materials,
        "scope": scope, "boms": boms, "adjustments": adjustments, "allocated": allocated,
    }


def load_plan_items(cur, plan_id: int):
    cur.execute(f"{ITEM_SELECT} WHERE d.plan_id=%s AND d.status<>'CANCELLED' ORDER BY d.planned_start, d.wo_no", (plan_id,))
    return cur.fetchall()


def load_backlog(cur):
    cur.execute(f"{ITEM_SELECT} WHERE d.status='UNSCHEDULED'")
    rows = cur.fetchall()
    rows.sort(key=lambda r: (PRIORITY_RANK.get(r["priority"], 9), r["due_date"] or datetime.max, r["wo_no"]))
    return rows


def load_scheduled_between(cur, start: datetime, end: datetime, exclude_plan_id=None):
    placeholders = ",".join(["%s"] * len(ACTIVE_WO_STATUSES))
    sql = f"""{ITEM_SELECT}
        WHERE d.status IN ({placeholders}) AND d.planned_start < %s AND d.planned_end > %s"""
    params = list(ACTIVE_WO_STATUSES) + [end, start]
    if exclude_plan_id is not None:
        sql += " AND (d.plan_id IS NULL OR d.plan_id<>%s)"
        params.append(exclude_plan_id)
    cur.execute(sql + " ORDER BY d.planned_start", tuple(params))
    return cur.fetchall()


# =========================================================
# BOARD COMPUTATION (pure)
# =========================================================


def progress_state(item, now: datetime) -> str:
    planned, actual = int(item["planned_qty"]), int(item["actual_qty"] or 0)
    start, end, due = item["planned_start"], item["planned_end"], item["due_date"]
    if item["status"] in {"COMPLETED", "CLOSED"} or actual >= planned:
        return "COMPLETED"
    if not start or not end or now < start:
        state = "NOT_STARTED"
    elif now >= end:
        return "DELAY"
    else:
        expected = planned * (now - start).total_seconds() / max((end - start).total_seconds(), 1)
        ratio = actual / expected if expected >= 1 else 1
        state = "AHEAD" if ratio >= 1.05 else "ON_TRACK" if ratio >= 0.9 else "AT_RISK" if ratio >= 0.75 else "DELAY"
    if state in {"NOT_STARTED", "ON_TRACK", "AHEAD"} and due and end and end > due:
        return "AT_RISK"
    return state


def expected_qty(item, now: datetime) -> float:
    start, end = item["planned_start"], item["planned_end"]
    if not start or not end or now <= start:
        return 0.0
    if now >= end:
        return float(item["planned_qty"])
    return item["planned_qty"] * (now - start).total_seconds() / max((end - start).total_seconds(), 1)


def _alert(severity, code, wo_no=None, ref=None, **params):
    return {"severity": severity, "code": code, "wo_no": wo_no, "ref": ref, "params": params}


def _group_key(machine):
    return machine["equipment_group_id"] or 0


def build_board(ctx, items, backlog, others, now: datetime):
    plan, shift = ctx["plan"], ctx["shift"]
    win_start, win_end = ctx["window_start"], ctx["window_end"]
    machines = ctx["machines"]
    machine_by_id = {m["id"]: m for m in machines}
    working_hours = float(shift["working_hours"] or 0) or (win_end - win_start).total_seconds() / 3600
    alerts = []

    # ---- Work order rows with progress state --------------------------------
    rows = []
    for item in items:
        state = progress_state(item, now)
        rows.append({**item, "progress_state": state, "expected_qty": round(expected_qty(item, now))})

    # ---- Capacity by machine group ------------------------------------------
    groups = {}
    for machine in machines:
        key = _group_key(machine)
        group = groups.setdefault(key, {
            "group_id": machine["equipment_group_id"], "group_code": machine["group_code"] or "-",
            "group_name": machine["group_name"] or "Ungrouped", "total_machines": 0, "available_machines": 0,
            "machine_ids": [], "planned_hours": 0.0, "planned_qty": 0,
        })
        group["total_machines"] += 1
        group["machine_ids"].append(machine["id"])
        if machine["operational_status"] not in BLOCKED_MACHINE_STATUSES:
            group["available_machines"] += 1

    machine_hours = {}
    for item in items:
        machine = machine_by_id.get(item["machine_id"])
        if not machine:
            continue
        hours = _overlap_seconds(item["planned_start"], item["planned_end"], win_start, win_end) / 3600
        machine_hours[machine["id"]] = machine_hours.get(machine["id"], 0) + hours
        group = groups[_group_key(machine)]
        group["planned_hours"] += hours
        group["planned_qty"] += int(item["planned_qty"])

    capacity = []
    for group in groups.values():
        available_hours = group["available_machines"] * working_hours
        planned_hours = group["planned_hours"]
        utilization = (planned_hours / available_hours * 100) if available_hours else (100.0 if planned_hours else 0.0)
        capacity_qty = round(group["planned_qty"] * available_hours / planned_hours) if planned_hours else None
        capacity.append({
            "group_id": group["group_id"], "group_code": group["group_code"], "group_name": group["group_name"],
            "total_machines": group["total_machines"], "available_machines": group["available_machines"],
            "available_hours": round(available_hours, 2), "planned_hours": round(planned_hours, 2),
            "planned_qty": group["planned_qty"], "capacity_qty": capacity_qty,
            "utilization": round(utilization, 1), "machine_ids": group["machine_ids"],
        })
        if utilization > 100:
            alerts.append(_alert("WARNING", "CAPACITY_OVERLOAD", ref=group["group_code"],
                                 group=group["group_name"], utilization=round(utilization, 1)))

    total_available_hours = sum(c["available_hours"] for c in capacity)
    total_planned_hours = sum(c["planned_hours"] for c in capacity)
    total_planned_qty_on_machines = sum(c["planned_qty"] for c in capacity)
    total_capacity_qty = round(total_planned_qty_on_machines * total_available_hours / total_planned_hours) if total_planned_hours else None
    capacity_totals = {
        "total_machines": sum(c["total_machines"] for c in capacity),
        "available_machines": sum(c["available_machines"] for c in capacity),
        "available_hours": round(total_available_hours, 2), "planned_hours": round(total_planned_hours, 2),
        "planned_qty": total_planned_qty_on_machines, "capacity_qty": total_capacity_qty,
        "utilization": round(total_planned_hours / total_available_hours * 100, 1) if total_available_hours else 0.0,
    }

    machine_load = [{
        "id": m["id"], "equipment_code": m["equipment_code"], "equipment_name": m["equipment_name"],
        "group_name": m["group_name"], "operational_status": m["operational_status"],
        "available": m["operational_status"] not in BLOCKED_MACHINE_STATUSES,
        "planned_hours": round(machine_hours.get(m["id"], 0), 2),
    } for m in machines]

    # ---- Capacity vs demand per hour (machine-hours) -------------------------
    available_count = capacity_totals["available_machines"]
    hourly, slot = [], win_start
    while slot < win_end:
        slot_end = min(slot + timedelta(hours=1), win_end)
        demand = sum(_overlap_seconds(i["planned_start"], i["planned_end"], slot, slot_end) for i in items if i["machine_id"]) / 3600
        slot_hours = (slot_end - slot).total_seconds() / 3600
        hourly.append({"hour": _fmt_time(slot), "capacity": round(available_count * slot_hours, 2), "demand": round(demand, 2)})
        slot = slot_end

    # ---- Material readiness --------------------------------------------------
    need = {}
    for item in items:
        bom = ctx.get("boms", {}).get(item["product_id"])
        if not bom:
            continue
        remaining = max(int(item["planned_qty"]) - int(item["actual_qty"] or 0), 0)
        for row in requirement_from_items(bom["items"], bom["base_qty"], remaining):
            entry = need.setdefault(row["material_id"], {"required": 0.0, "wo_nos": [], "missing_weight": False})
            entry["wo_nos"].append(item["wo_no"])
            key = (item["id"], row["material_id"])
            entry["required"] += max(row["required_qty"] + ctx.get("adjustments", {}).get(key, 0.0) - ctx.get("allocated", {}).get(key, 0.0), 0.0)

    material_rows = []
    for material_id, entry in need.items():
        material = ctx["materials"].get(material_id) or {}
        available = float(material.get("available_qty") or 0)
        safety = float(material.get("safety_stock_qty") or 0)
        required = round(entry["required"], 3)
        if available < required:
            status = "SHORTAGE"
        elif available - required < safety or available == 0:
            status = "RUNNING_LOW"
        else:
            status = "ENOUGH"
        material_rows.append({
            "material_id": material_id, "material_code": material.get("material_code"),
            "material_name": material.get("material_name"), "unit": material.get("unit") or "KG",
            "required_qty": required, "available_qty": round(available, 3), "safety_stock_qty": round(safety, 3),
            "has_stock_record": material.get("available_qty") is not None,
            "missing_weight": entry["missing_weight"], "status": status, "wo_nos": entry["wo_nos"],
        })
        params = {"material": material.get("material_code"), "required": required, "available": round(available, 3),
                  "unit": material.get("unit") or "KG"}
        if status == "SHORTAGE":
            alerts.append(_alert("ERROR", "MATERIAL_SHORTAGE", ref=material.get("material_code"), **params))
        elif status == "RUNNING_LOW":
            alerts.append(_alert("WARNING", "MATERIAL_LOW", ref=material.get("material_code"), **params))
    material_rows.sort(key=lambda r: ({"SHORTAGE": 0, "RUNNING_LOW": 1, "ENOUGH": 2}[r["status"]], r["material_code"] or ""))
    total_required = sum(r["required_qty"] for r in material_rows)
    covered = sum(min(r["available_qty"], r["required_qty"]) for r in material_rows)
    material_summary = {
        "availability_pct": round(covered / total_required * 100, 1) if total_required else 100.0,
        "enough": sum(r["status"] == "ENOUGH" for r in material_rows),
        "running_low": sum(r["status"] == "RUNNING_LOW" for r in material_rows),
        "shortage": sum(r["status"] == "SHORTAGE" for r in material_rows),
    }

    # ---- Per-work-order checks ------------------------------------------------
    for row in rows:
        wo = row["wo_no"]
        machine = machine_by_id.get(row["machine_id"])
        if not row["machine_id"]:
            alerts.append(_alert("ERROR", "MISSING_MACHINE", wo, wo=wo))
        elif machine and machine["operational_status"] in BLOCKED_MACHINE_STATUSES:
            alerts.append(_alert("ERROR", "MACHINE_UNAVAILABLE", wo, row["machine_code"], wo=wo,
                                 machine=row["machine_code"], status=machine["operational_status"]))
        if not row["mold_id"]:
            alerts.append(_alert("WARNING", "MISSING_MOLD", wo, wo=wo))
        else:
            mold = ctx["molds"].get(row["mold_id"]) or {}
            if not mold.get("is_active") or mold.get("status") not in USABLE_MOLD_STATUSES:
                alerts.append(_alert("ERROR", "MOLD_UNAVAILABLE", wo, row["mold_code"], wo=wo,
                                     mold=row["mold_code"], status=mold.get("status")))
            if (row["mold_id"], row["product_id"]) not in ctx["mold_products"]:
                alerts.append(_alert("WARNING", "MOLD_PRODUCT_MISMATCH", wo, row["mold_code"], wo=wo,
                                     mold=row["mold_code"], product=row["product_code"]))
        if row["planned_start"] and row["planned_end"] and (row["planned_start"] < win_start or row["planned_end"] > win_end):
            alerts.append(_alert("WARNING", "OUTSIDE_SHIFT", wo, wo=wo, start=_fmt_time(row["planned_start"]),
                                 end=_fmt_time(row["planned_end"])))
        if row["due_date"] and row["planned_end"] and row["planned_end"] > row["due_date"]:
            alerts.append(_alert("WARNING", "DUE_DATE_RISK", wo, wo=wo,
                                 due=row["due_date"].strftime("%d/%m %H:%M"),
                                 late_minutes=round((row["planned_end"] - row["due_date"]).total_seconds() / 60)))
        if row["progress_state"] == "DELAY":
            alerts.append(_alert("WARNING", "BEHIND_SCHEDULE", wo, wo=wo, actual=int(row["actual_qty"] or 0),
                                 expected=row["expected_qty"]))
        if row["product_id"] not in ctx.get("boms", {}):
            alerts.append(_alert("WARNING", "NO_BOM", wo, wo=wo, product=row["product_code"]))

    # ---- Machine / mold double booking (incl. neighbouring plans) ------------
    plan_ids = {r["id"] for r in rows}
    pool = rows + [o for o in others if o["id"] not in plan_ids]
    for field, code, label in (("machine_id", "MACHINE_OVERLAP", "machine_code"), ("mold_id", "MOLD_OVERLAP", "mold_code")):
        by_resource = {}
        for entry in pool:
            if entry[field] and entry["planned_start"] and entry["planned_end"]:
                by_resource.setdefault(entry[field], []).append(entry)
        for entries in by_resource.values():
            entries.sort(key=lambda e: e["planned_start"])
            for index, first in enumerate(entries):
                for second in entries[index + 1:]:
                    if second["planned_start"] >= first["planned_end"]:
                        break
                    if first["id"] in plan_ids or second["id"] in plan_ids:
                        alerts.append(_alert("ERROR", code, first["wo_no"], first[label], wo=first["wo_no"],
                                             other=second["wo_no"], resource=first[label]))

    # ---- Backlog + recommended slot ------------------------------------------
    free_at = {}
    for machine in machines:
        if machine["operational_status"] in BLOCKED_MACHINE_STATUSES:
            continue
        ends = [i["planned_end"] for i in pool if i["machine_id"] == machine["id"] and i["planned_end"]
                and i["planned_start"] < win_end and i["planned_end"] > win_start]
        free_at[machine["id"]] = max([win_start, now if win_start < now < win_end else win_start] + ends)
    backlog_rows = []
    for item in backlog:
        seconds = duration_seconds(item["planned_qty"], item["cavity"], item["cycle_time_sec"], item["setup_minutes"])
        recommendation = {"type": "NEXT_SHIFT"}
        if free_at:
            machine_id = min(free_at, key=free_at.get)
            start = free_at[machine_id]
            end = start + timedelta(seconds=seconds)
            if end <= win_end:
                machine = machine_by_id[machine_id]
                recommendation = {"type": "SLOT", "machine_id": machine_id, "machine_code": machine["equipment_code"],
                                  "planned_start": start, "planned_end": end}
                free_at[machine_id] = end
        backlog_rows.append({**item, "duration_minutes": round(seconds / 60), "recommendation": recommendation})
        if item["priority"] in {"HIGH", "URGENT"} and item["due_date"] and item["due_date"] <= win_end:
            alerts.append(_alert("WARNING", "BACKLOG_URGENT", item["wo_no"], wo=item["wo_no"],
                                 due=item["due_date"].strftime("%d/%m %H:%M")))

    alerts.sort(key=lambda a: (SEVERITY_RANK[a["severity"]], a["code"], a["wo_no"] or ""))

    # ---- Summary / KPI -------------------------------------------------------
    buckets = {"ON_TRACK": 0, "AT_RISK": 0, "DELAY": 0, "NOT_STARTED": 0}
    for row in rows:
        state = row["progress_state"]
        buckets["ON_TRACK" if state in {"ON_TRACK", "AHEAD", "COMPLETED"} else state] += 1
    total_orders = len(rows)
    planned_qty = sum(int(r["planned_qty"]) for r in rows)
    actual_qty = sum(int(r["actual_qty"] or 0) for r in rows)
    expected_now = sum(r["expected_qty"] for r in rows)
    errors = sum(a["severity"] == "ERROR" for a in alerts if a["code"] != "BACKLOG_URGENT")

    def pct(value, total):
        return round(value / total * 100, 1) if total else 0.0

    return {
        "plan": plan,
        "shift": {**shift, "start_time": str(shift["start_time"]), "end_time": str(shift["end_time"])},
        "window": {"start": win_start, "end": win_end, "working_hours": working_hours},
        "server_time": now,
        "kpis": {
            "planned_qty": planned_qty, "actual_qty": actual_qty,
            "capacity_qty": total_capacity_qty, "capacity_utilization": capacity_totals["utilization"],
            "planned_orders": total_orders,
            "on_track": buckets["ON_TRACK"], "on_track_pct": pct(buckets["ON_TRACK"], total_orders),
            "at_risk": buckets["AT_RISK"], "at_risk_pct": pct(buckets["AT_RISK"], total_orders),
            "delayed": buckets["DELAY"], "delayed_pct": pct(buckets["DELAY"], total_orders),
            "not_started": buckets["NOT_STARTED"], "not_started_pct": pct(buckets["NOT_STARTED"], total_orders),
        },
        "work_orders": rows,
        "capacity": capacity,
        "capacity_totals": capacity_totals,
        "machines": machine_load,
        "hourly": hourly,
        "materials": material_rows,
        "material_summary": material_summary,
        "achievement": {
            "planned_qty": planned_qty, "actual_qty": actual_qty, "expected_qty": round(expected_now),
            "remaining_qty": max(planned_qty - actual_qty, 0),
            "achievement_pct": pct(actual_qty, planned_qty),
            "schedule_adherence_pct": pct(actual_qty, expected_now) if expected_now >= 1 else None,
        },
        "alerts": alerts,
        "backlog": backlog_rows,
        "checks": {
            "error_count": errors,
            "warning_count": sum(a["severity"] == "WARNING" for a in alerts),
            "can_confirm": plan["status"] == "DRAFT" and total_orders > 0 and errors == 0,
            "can_publish": plan["status"] == "CONFIRMED" and total_orders > 0 and errors == 0,
        },
    }
