"""Material Allocation (screen 4.3): reserve lots for Work Orders, issue to machines, return.

Rules (spec 4.3 / 4.5):
- Only lots IN_STOCK, not expired and IQC PASS / NOT_REQUIRED can be reserved (Hold / expired / waiting IQC never).
- Total reservations on a lot never exceed its free quantity (current - reserved), so a lot
  cannot be allocated to two Work Orders at the same time (row lock on the lot).
- Suggestions follow FEFO (earliest expiry first), then FIFO (earliest received).
- Reservations expire after material.reservation_hours and are released automatically.
"""

from datetime import date, datetime, timedelta

from fastapi import HTTPException

from app.modules.material.inventory.service import load_settings, material_category, write_txn
from app.modules.material.requirements.service import refresh_statuses

EPS = 1e-6

ALLOC_SELECT = """
    SELECT a.*, d.wo_no, d.status AS wo_status, d.machine_id AS wo_machine_id, d.production_order_id, d.planned_start,
           p.product_code, p.product_name, po.order_no,
           m.material_code, m.material_name, m.material_type, m.material_group, m.unit,
           l.lot_no, l.expiry_date AS lot_expiry, loc.location_name, loc.location_type,
           e.equipment_code AS machine_code
    FROM material_allocations a
    JOIN production_plan_details d ON d.id = a.work_order_id
    JOIN products p ON p.id = d.product_id
    LEFT JOIN production_orders po ON po.id = d.production_order_id
    JOIN materials m ON m.id = a.material_id
    JOIN material_lots l ON l.id = a.lot_id
    JOIN material_locations loc ON loc.id = a.location_id
    LEFT JOIN equipment e ON e.id = a.machine_id
"""


def decorate(a, now: datetime):
    reserved, issued = float(a["reserved_qty"]), float(a["issued_qty"])
    returned, consumed = float(a["returned_qty"]), float(a["consumed_qty"])
    in_use = max(issued - returned - consumed, 0.0)
    hours_left = (a["expires_at"] - now).total_seconds() / 3600 if a["expires_at"] and reserved > EPS else None
    return {**a, "reserved_qty": reserved, "issued_qty": issued, "returned_qty": returned, "consumed_qty": consumed,
            "initial_qty": float(a["initial_qty"]), "in_use_qty": round(in_use, 3), "total_qty": round(reserved + in_use, 3),
            "hours_left": round(hours_left, 2) if hours_left is not None else None,
            "category": material_category(a["material_type"], a["material_group"], a["material_code"])}


def get_allocation(cur, allocation_id, for_update=False):
    cur.execute(f"{ALLOC_SELECT} WHERE a.id=%s{' FOR UPDATE' if for_update else ''}", (allocation_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Allocation not found.")
    return row


def next_allocation_no(cur) -> str:
    stem = f"MA-{date.today():%y%m%d}-"
    cur.execute("SELECT allocation_no FROM material_allocations WHERE allocation_no LIKE %s ORDER BY allocation_no DESC LIMIT 1", (f"{stem}%",))
    row = cur.fetchone()
    tail = row["allocation_no"][len(stem):] if row else ""
    return f"{stem}{(int(tail) + 1 if tail.isdigit() else 1):03d}"


def _lock_lot(cur, lot_id):
    cur.execute("SELECT * FROM material_lots WHERE id=%s FOR UPDATE", (lot_id,))
    lot = cur.fetchone()
    if not lot:
        raise HTTPException(status_code=404, detail="Lot not found.")
    return lot


def _machine_location(cur, machine_id):
    if not machine_id:
        return None
    cur.execute("SELECT id FROM material_locations WHERE equipment_id=%s AND location_type='MACHINE' AND status='ACTIVE' LIMIT 1", (machine_id,))
    row = cur.fetchone()
    return row["id"] if row else None


def _bump(cur, allocation_id, version, sets, params, actor):
    cur.execute(f"UPDATE material_allocations SET {sets}, updated_by=%s, version=version+1 WHERE id=%s AND version=%s",
                params + (actor, allocation_id, version))
    if cur.rowcount == 0:
        raise HTTPException(status_code=409, detail="This allocation was changed by another user. Reload and try again.")


def refresh_order_assignment(cur, work_order_id):
    cur.execute("SELECT production_order_id FROM production_plan_details WHERE id=%s", (work_order_id,))
    row = cur.fetchone()
    if not row or not row["production_order_id"]:
        return
    order_id = row["production_order_id"]
    cur.execute(
        """SELECT a.material_id, SUM(a.reserved_qty + a.issued_qty - a.returned_qty) AS allocated, SUM(a.issued_qty - a.returned_qty) AS issued
           FROM material_allocations a JOIN production_plan_details d ON d.id = a.work_order_id
           WHERE d.production_order_id=%s AND a.status<>'CANCELLED' GROUP BY a.material_id""", (order_id,))
    totals = {r["material_id"]: r for r in cur.fetchall()}
    cur.execute("SELECT id, material_id, required_qty, status FROM production_order_materials WHERE production_order_id=%s AND status<>'CANCELLED'", (order_id,))
    for line in cur.fetchall():
        t = totals.get(line["material_id"])
        required = float(line["required_qty"])
        allocated = float(t["allocated"]) if t else 0.0
        issued = float(t["issued"]) if t else 0.0
        tol = 1e-3
        status = "READY" if required > 0 and issued >= required - tol else "RESERVED" if required > 0 and allocated >= required - tol else "ASSIGNED"
        if status != line["status"]:
            cur.execute("UPDATE production_order_materials SET status=%s WHERE id=%s", (status, line["id"]))


def _after_change(cur, work_order_id):
    refresh_statuses(cur)
    refresh_order_assignment(cur, work_order_id)


# =========================================================
# EXPIRY
# =========================================================


def expire_reservations(cur, actor="SYSTEM"):
    cur.execute("SELECT id FROM material_allocations WHERE reserved_qty > 0 AND expires_at IS NOT NULL AND expires_at < NOW() AND status IN ('RESERVED','IN_USE')")
    ids = [r["id"] for r in cur.fetchall()]
    touched = set()
    for allocation_id in ids:
        a = get_allocation(cur, allocation_id, for_update=True)
        reserved = float(a["reserved_qty"])
        if reserved <= EPS:
            continue
        lot = _lock_lot(cur, a["lot_id"])
        cur.execute("UPDATE material_lots SET reserved_qty=GREATEST(reserved_qty-%s,0), version=version+1 WHERE id=%s", (reserved, lot["id"]))
        status = "CANCELLED" if float(a["issued_qty"]) <= EPS else a["status"]
        cur.execute(
            """UPDATE material_allocations SET reserved_qty=0, status=%s, cancelled_at=IF(%s='CANCELLED',NOW(),cancelled_at),
               cancel_reason=COALESCE(cancel_reason,'Reservation expired'), updated_by=%s, version=version+1 WHERE id=%s""",
            (status, status, actor, allocation_id))
        write_txn(cur, lot["id"], "RESERVATION_RELEASE", 0, float(lot["current_qty"]), lot["status"], actor,
                  work_order_id=a["work_order_id"], reference_no=a["allocation_no"], remark=f"Reservation expired ({reserved:g} {a['unit']})")
        touched.add(a["work_order_id"])
    for wo_id in touched:
        refresh_order_assignment(cur, wo_id)
    if touched:
        refresh_statuses(cur)
    return len(touched)


# =========================================================
# REQUIREMENT LINES / FEFO SUGGESTIONS
# =========================================================


def requirement_state(cur, requirement_id, for_update=False):
    cur.execute(
        f"""SELECT r.*, d.wo_no, d.status AS wo_status, d.machine_id AS wo_machine_id, m.material_code, m.unit
            FROM material_requirements r JOIN production_plan_details d ON d.id = r.work_order_id
            JOIN materials m ON m.id = r.material_id WHERE r.id=%s{' FOR UPDATE' if for_update else ''}""", (requirement_id,))
    line = cur.fetchone()
    if not line:
        raise HTTPException(status_code=404, detail="Requirement line not found.")
    cur.execute(
        """SELECT COALESCE(SUM(reserved_qty + issued_qty - returned_qty - consumed_qty), 0) AS allocated
           FROM material_allocations WHERE requirement_id=%s AND status<>'CANCELLED'""", (requirement_id,))
    allocated = float(cur.fetchone()["allocated"])
    need = max(float(line["open_qty"]) + float(line["adjustment_qty"]), 0.0)
    remaining = round(max(need - allocated, 0.0), 3)
    return {**line, "need_qty": round(need, 4), "allocated_qty": round(allocated, 4), "remaining_qty": remaining if remaining >= 1e-3 else 0.0}


def lot_suggestions(cur, material_id, qty=None):
    cur.execute(
        """SELECT l.id, l.lot_no, l.current_qty, l.reserved_qty, l.expiry_date, l.received_at, l.iqc_result,
                  loc.location_name, loc.location_type
           FROM material_lots l JOIN material_locations loc ON loc.id = l.location_id
           WHERE l.material_id=%s AND l.status='IN_STOCK' AND l.iqc_result IN ('PASS','NOT_REQUIRED')
             AND (l.expiry_date IS NULL OR l.expiry_date >= CURDATE()) AND l.current_qty - l.reserved_qty > 0
           ORDER BY l.expiry_date IS NULL, l.expiry_date, l.received_at, l.lot_no""", (material_id,))
    rows, left = [], qty
    for lot in cur.fetchall():
        free = round(float(lot["current_qty"]) - float(lot["reserved_qty"]), 3)
        take = round(min(free, left), 3) if left is not None and left > EPS else 0.0
        if left is not None:
            left = max(left - take, 0.0)
        rows.append({**lot, "current_qty": float(lot["current_qty"]), "reserved_qty": float(lot["reserved_qty"]), "free_qty": free, "suggested_qty": take})
    return rows


# =========================================================
# ACTIONS
# =========================================================


def reserve(cur, requirement_id, lot_id, qty, actor, hours=None):
    if qty <= EPS:
        raise HTTPException(status_code=400, detail="Quantity must be greater than zero.")
    line = requirement_state(cur, requirement_id, for_update=True)
    if line["status"] == "CANCELLED" or line["wo_status"] not in ("UNSCHEDULED", "SCHEDULED", "RELEASED", "IN_PRODUCTION", "ON_HOLD"):
        raise HTTPException(status_code=409, detail="Only open work order requirement lines can be allocated.")
    if qty > line["remaining_qty"] + 1e-3:
        raise HTTPException(status_code=409, detail=f"Only {line['remaining_qty']:g} {line['unit']} remain to be allocated for this line.")
    lot = _lock_lot(cur, lot_id)
    if lot["material_id"] != line["material_id"]:
        raise HTTPException(status_code=400, detail="The lot is a different material from the requirement.")
    if lot["status"] != "IN_STOCK":
        raise HTTPException(status_code=409, detail="Only lots in stock can be reserved (not Hold, waiting IQC, in use or closed).")
    if lot["iqc_result"] not in ("PASS", "NOT_REQUIRED"):
        raise HTTPException(status_code=409, detail="The lot has not passed IQC.")
    if lot["expiry_date"] and lot["expiry_date"] < date.today():
        raise HTTPException(status_code=409, detail="Expired lots cannot be reserved.")
    free = float(lot["current_qty"]) - float(lot["reserved_qty"])
    if qty > free + 1e-3:
        raise HTTPException(status_code=409, detail=f"The lot only has {free:g} free (not reserved).")
    hours = hours or load_settings(cur)["material.reservation_hours"]
    allocation_no = next_allocation_no(cur)
    cur.execute("UPDATE material_lots SET reserved_qty=reserved_qty+%s, version=version+1 WHERE id=%s", (qty, lot_id))
    cur.execute(
        """INSERT INTO material_allocations (allocation_no, work_order_id, requirement_id, material_id, lot_id, location_id, machine_id,
           reserved_qty, initial_qty, status, reserved_at, expires_at, created_by)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'RESERVED',NOW(),%s,%s)""",
        (allocation_no, line["work_order_id"], requirement_id, line["material_id"], lot_id, lot["location_id"], line["wo_machine_id"],
         qty, qty, datetime.now() + timedelta(hours=hours), actor))
    allocation_id = cur.lastrowid
    write_txn(cur, lot_id, "RESERVATION", 0, float(lot["current_qty"]), lot["status"], actor, work_order_id=line["work_order_id"],
              reference_no=allocation_no, remark=f"Reserved {qty:g} {line['unit']} for {line['wo_no']}")
    _after_change(cur, line["work_order_id"])
    return {"id": allocation_id, "allocation_no": allocation_no}


def auto_allocate(cur, work_order_id, actor):
    cur.execute("SELECT id FROM material_requirements WHERE work_order_id=%s AND status<>'CANCELLED'", (work_order_id,))
    created, missing = [], []
    for row in cur.fetchall():
        line = requirement_state(cur, row["id"])
        if line["remaining_qty"] <= EPS:
            continue
        left = line["remaining_qty"]
        for lot in lot_suggestions(cur, line["material_id"], left):
            if lot["suggested_qty"] <= EPS:
                continue
            created.append(reserve(cur, row["id"], lot["id"], lot["suggested_qty"], actor))
            left -= lot["suggested_qty"]
        if left > 1e-3:
            missing.append({"material_code": line["material_code"], "unit": line["unit"], "missing_qty": round(left, 3)})
    return {"created": created, "missing": missing}


def issue(cur, allocation_id, qty, machine_id, actor, version):
    a = get_allocation(cur, allocation_id, for_update=True)
    reserved = float(a["reserved_qty"])
    qty = reserved if qty is None else qty
    if qty <= EPS or qty > reserved + 1e-3:
        raise HTTPException(status_code=409, detail=f"Issue between 0 and the reserved quantity ({reserved:g} {a['unit']}).")
    lot = _lock_lot(cur, a["lot_id"])
    machine_id = machine_id or a["machine_id"] or a["wo_machine_id"]
    if not machine_id:
        raise HTTPException(status_code=400, detail="Choose the machine the material is issued to.")
    new_current = round(float(lot["current_qty"]) - qty, 3)
    lot_status = "IN_USE" if new_current <= EPS else lot["status"]
    cur.execute("UPDATE material_lots SET current_qty=%s, reserved_qty=GREATEST(reserved_qty-%s,0), status=%s, version=version+1 WHERE id=%s",
                (max(new_current, 0), qty, lot_status, lot["id"]))
    _bump(cur, allocation_id, version, "reserved_qty=reserved_qty-%s, issued_qty=issued_qty+%s, status='IN_USE', issued_at=COALESCE(issued_at,NOW()), machine_id=%s",
          (qty, qty, machine_id), actor)
    write_txn(cur, lot["id"], "ISSUE_TO_MACHINE", -qty, max(new_current, 0), lot_status, actor, from_location_id=lot["location_id"],
              to_location_id=_machine_location(cur, machine_id), work_order_id=a["work_order_id"], machine_id=machine_id,
              reference_no=a["allocation_no"])
    _after_change(cur, a["work_order_id"])


def release(cur, allocation_id, reason, actor, version):
    a = get_allocation(cur, allocation_id, for_update=True)
    reserved = float(a["reserved_qty"])
    if reserved <= EPS:
        raise HTTPException(status_code=409, detail="Nothing is reserved on this allocation.")
    lot = _lock_lot(cur, a["lot_id"])
    cur.execute("UPDATE material_lots SET reserved_qty=GREATEST(reserved_qty-%s,0), version=version+1 WHERE id=%s", (reserved, lot["id"]))
    in_use = float(a["issued_qty"]) - float(a["returned_qty"]) - float(a["consumed_qty"])
    status = "CANCELLED" if float(a["issued_qty"]) <= EPS else ("IN_USE" if in_use > EPS else "COMPLETED")
    _bump(cur, allocation_id, version, "reserved_qty=0, status=%s, cancelled_at=IF(%s='CANCELLED',NOW(),cancelled_at), cancel_reason=%s",
          (status, status, reason), actor)
    write_txn(cur, lot["id"], "RESERVATION_RELEASE", 0, float(lot["current_qty"]), lot["status"], actor, work_order_id=a["work_order_id"],
              reference_no=a["allocation_no"], remark=reason)
    _after_change(cur, a["work_order_id"])


def return_material(cur, allocation_id, qty, reason, actor, version):
    a = get_allocation(cur, allocation_id, for_update=True)
    in_use = float(a["issued_qty"]) - float(a["returned_qty"]) - float(a["consumed_qty"])
    if qty <= EPS or qty > in_use + 1e-3:
        raise HTTPException(status_code=409, detail=f"Return between 0 and the quantity at the machine ({in_use:g} {a['unit']}).")
    lot = _lock_lot(cur, a["lot_id"])
    new_current = round(float(lot["current_qty"]) + qty, 3)
    lot_status = "IN_STOCK" if lot["status"] == "IN_USE" else lot["status"]
    cur.execute("UPDATE material_lots SET current_qty=%s, status=%s, version=version+1 WHERE id=%s", (new_current, lot_status, lot["id"]))
    left_in_use = in_use - qty
    status = "IN_USE" if left_in_use > EPS else ("RESERVED" if float(a["reserved_qty"]) > EPS else "COMPLETED")
    _bump(cur, allocation_id, version, "returned_qty=returned_qty+%s, status=%s, completed_at=IF(%s='COMPLETED',NOW(),completed_at)",
          (qty, status, status), actor)
    write_txn(cur, lot["id"], "RETURN", qty, new_current, lot_status, actor, from_location_id=_machine_location(cur, a["machine_id"]),
              to_location_id=lot["location_id"], work_order_id=a["work_order_id"], machine_id=a["machine_id"],
              reference_no=a["allocation_no"], remark=reason)
    _after_change(cur, a["work_order_id"])


def extend(cur, allocation_id, hours, actor, version):
    a = get_allocation(cur, allocation_id, for_update=True)
    if float(a["reserved_qty"]) <= EPS:
        raise HTTPException(status_code=409, detail="Only allocations with a reserved quantity can be extended.")
    base = max(a["expires_at"] or datetime.now(), datetime.now())
    _bump(cur, allocation_id, version, "expires_at=%s", (base + timedelta(hours=hours),), actor)
