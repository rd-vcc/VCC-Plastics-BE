"""Local inventory provider (no WMS yet) plus shared stock helpers.

Other modules (Planning, Material Requirement, Allocation) read stock ONLY
through ``available_by_material`` / ``plan_shortages`` here, so a WMS adapter
can later replace this module without changing callers.
"""

from datetime import date, datetime, timedelta

from fastapi import HTTPException

from app.modules.master_data.products.bom_service import active_bom_requirement


DEFAULT_SETTINGS = {
    "material.critical_coverage_pct": 50.0,
    "material.critical_hours": 24.0,
    "material.reservation_hours": 24.0,
    "material.usage_tolerance_pct": 3.0,
    "material.expiry_warning_days": 30.0,
}
CATEGORIES = ["VIRGIN_RESIN", "REGRIND", "MASTERBATCH", "ADDITIVE", "PACKAGING", "OTHERS"]
USABLE_STATUSES = {"IN_STOCK"}
LIVE_STATUSES = ("WAITING_IQC", "IN_STOCK", "IN_USE", "HOLD")

LOT_SELECT = """
    SELECT l.*, m.material_code, m.material_name, m.material_type, m.material_group, m.unit,
           loc.location_code, loc.location_name, loc.location_type,
           wo.wo_no AS source_wo_no
    FROM material_lots l
    JOIN materials m ON m.id = l.material_id
    JOIN material_locations loc ON loc.id = l.location_id
    LEFT JOIN production_plan_details wo ON wo.id = l.source_work_order_id
"""


def material_category(material_type, material_group=None, material_code=None) -> str:
    code = str(material_code or "").upper()
    group = str(material_group or "").lower()
    if material_type == "RECYCLED":
        return "REGRIND"
    if material_type == "ADDITIVE":
        return "ADDITIVE"
    if material_type == "PACKAGING":
        return "PACKAGING"
    if code.startswith("MB-") or "masterbatch" in group:
        return "MASTERBATCH"
    if material_type in {"RAW_MATERIAL", "COMPOUND"}:
        return "VIRGIN_RESIN"
    return "OTHERS"


def load_settings(cur) -> dict:
    cur.execute("SELECT setting_key, setting_value FROM system_settings WHERE setting_key LIKE 'material.%%'")
    values = dict(DEFAULT_SETTINGS)
    for row in cur.fetchall():
        try:
            values[row["setting_key"]] = float(row["setting_value"])
        except (TypeError, ValueError):
            pass
    return values


def decorate_lot(lot, today: date, warning_days: float):
    days = (lot["expiry_date"] - today).days if lot["expiry_date"] else None
    effective = lot["status"]
    if lot["status"] in {"IN_STOCK", "IN_USE", "WAITING_IQC"} and days is not None and days < 0:
        effective = "EXPIRED"
    current = float(lot["current_qty"])
    reserved = float(lot["reserved_qty"] or 0)
    return {
        **lot,
        "current_qty": current, "reserved_qty": reserved, "initial_qty": float(lot["initial_qty"]),
        "free_qty": round(max(current - reserved, 0), 3) if effective == "IN_STOCK" else 0.0,
        "effective_status": effective, "days_to_expiry": days,
        "near_expiry": days is not None and 0 <= days <= warning_days,
        "category": material_category(lot["material_type"], lot["material_group"], lot["material_code"]),
    }


def get_lot(cur, lot_id: int, for_update=False):
    cur.execute(f"{LOT_SELECT} WHERE l.id=%s{' FOR UPDATE' if for_update else ''}", (lot_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Lot not found.")
    return row


def get_location(cur, location_id: int):
    cur.execute("SELECT * FROM material_locations WHERE id=%s", (location_id,))
    row = cur.fetchone()
    if not row or row["status"] != "ACTIVE":
        raise HTTPException(status_code=400, detail="Location is missing or inactive.")
    return row


def get_active_material(cur, material_id: int):
    cur.execute("SELECT * FROM materials WHERE id=%s", (material_id,))
    row = cur.fetchone()
    if not row or row["status"] != "ACTIVE":
        raise HTTPException(status_code=400, detail="Material is missing or inactive.")
    return row


def next_lot_no(cur, prefix: str) -> str:
    stem = f"{prefix}{date.today():%y%m%d}-"
    cur.execute("SELECT lot_no FROM material_lots WHERE lot_no LIKE %s ORDER BY lot_no DESC LIMIT 1", (f"{stem}%",))
    row = cur.fetchone()
    tail = row["lot_no"][len(stem):] if row else ""
    return f"{stem}{(int(tail) + 1 if tail.isdigit() else 1):03d}"


def write_txn(cur, lot_id, txn_type, qty_change, qty_after, status_after, actor, *, from_location_id=None,
              to_location_id=None, work_order_id=None, machine_id=None, reference_no=None, remark=None):
    cur.execute(
        """INSERT INTO material_lot_transactions
           (lot_id, txn_type, qty_change, qty_after, from_location_id, to_location_id, status_after,
            work_order_id, machine_id, reference_no, actor, remark)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (lot_id, txn_type, qty_change, qty_after, from_location_id, to_location_id, status_after,
         work_order_id, machine_id, reference_no, actor or "SYSTEM", remark),
    )


# =========================================================
# SHARED STOCK READERS (inventory provider contract)
# =========================================================


def available_by_material(cur, material_ids=None):
    """Stock buckets per material in the material's unit.

    available = IN_STOCK, not expired, minus reserved. Other buckets for dashboards.
    """
    today = date.today()
    sql = f"SELECT material_id, status, current_qty, reserved_qty, expiry_date FROM material_lots WHERE status IN {LIVE_STATUSES}"
    params = []
    if material_ids:
        sql += f" AND material_id IN ({','.join(['%s'] * len(material_ids))})"
        params = list(material_ids)
    cur.execute(sql, tuple(params))
    result = {}
    for lot in cur.fetchall():
        bucket = result.setdefault(lot["material_id"], {"available": 0.0, "reserved": 0.0, "in_use": 0.0, "waiting_iqc": 0.0,
                                                        "hold": 0.0, "expired": 0.0, "total": 0.0})
        qty, reserved = float(lot["current_qty"]), float(lot["reserved_qty"] or 0)
        expired = lot["expiry_date"] is not None and lot["expiry_date"] < today
        bucket["total"] += qty
        if expired:
            bucket["expired"] += qty
        elif lot["status"] == "IN_STOCK":
            bucket["available"] += max(qty - reserved, 0)
            bucket["reserved"] += min(reserved, qty)
        elif lot["status"] == "IN_USE":
            bucket["available"] += max(qty - reserved, 0)
        elif lot["status"] == "WAITING_IQC":
            bucket["waiting_iqc"] += qty
        elif lot["status"] == "HOLD":
            bucket["hold"] += qty
    # material handed out to machines (Material Allocation) is "in use" until returned / consumed
    cur.execute("SHOW TABLES LIKE 'material_allocations'")
    if cur.fetchone():
        sql = """SELECT material_id, SUM(issued_qty - returned_qty - consumed_qty) AS in_use FROM material_allocations
                 WHERE status IN ('RESERVED','IN_USE')"""
        params = []
        if material_ids:
            sql += f" AND material_id IN ({','.join(['%s'] * len(material_ids))})"
            params = list(material_ids)
        cur.execute(sql + " GROUP BY material_id", tuple(params))
        for row in cur.fetchall():
            qty = float(row["in_use"] or 0)
            if qty <= 0:
                continue
            bucket = result.setdefault(row["material_id"], {"available": 0.0, "reserved": 0.0, "in_use": 0.0, "waiting_iqc": 0.0,
                                                             "hold": 0.0, "expired": 0.0, "total": 0.0})
            bucket["in_use"] += qty
            bucket["total"] += qty
    return {k: {n: round(v, 3) for n, v in b.items()} for k, b in result.items()}


def safety_stocks(cur):
    cur.execute("SELECT material_id, safety_stock_qty FROM material_stock_settings")
    return {r["material_id"]: float(r["safety_stock_qty"]) for r in cur.fetchall()}


def plan_shortages(cur, horizon_days: int = 30):
    """Walk open Work Orders in start order and consume available stock per material (active BOM).

    Returns one entry per material that runs short: shortage quantity and the first
    date it runs short, plus the Work Orders that cannot be covered.
    """
    now = datetime.now()
    cur.execute(
        """SELECT d.id, d.wo_no, d.product_id, d.planned_qty, d.actual_qty, d.planned_start, d.due_date, d.status,
                  p.product_name
           FROM production_plan_details d JOIN products p ON p.id = d.product_id
           WHERE d.status IN ('UNSCHEDULED','SCHEDULED','RELEASED','IN_PRODUCTION','ON_HOLD')""")
    work_orders = cur.fetchall()
    horizon = now + timedelta(days=horizon_days)

    def start_of(wo):
        return wo["planned_start"] or wo["due_date"] or horizon

    work_orders = [w for w in work_orders if start_of(w) <= horizon]
    work_orders.sort(key=start_of)
    needs, material_ids = [], set()
    for wo in work_orders:
        remaining = max(int(wo["planned_qty"]) - int(wo["actual_qty"] or 0), 0)
        if not remaining:
            continue
        requirement = active_bom_requirement(cur, wo["product_id"], remaining)
        if not requirement:
            continue
        for row in requirement["rows"]:
            needs.append((wo, row))
            material_ids.add(row["material_id"])
    stock = available_by_material(cur, list(material_ids)) if material_ids else {}
    left = {m: stock.get(m, {}).get("available", 0.0) for m in material_ids}
    shortages, risky = {}, {}
    for wo, row in needs:
        mid = row["material_id"]
        take = row["required_qty"]
        if left[mid] >= take:
            left[mid] -= take
            continue
        missing = take - max(left[mid], 0)
        left[mid] = 0.0
        entry = shortages.setdefault(mid, {"material_id": mid, "material_code": row["material_code"], "material_name": row["material_name"],
                                           "unit": row["unit"], "shortage_qty": 0.0, "expected_date": start_of(wo), "work_orders": []})
        entry["shortage_qty"] = round(entry["shortage_qty"] + missing, 3)
        entry["work_orders"].append(wo["wo_no"])
        risk = risky.setdefault(wo["id"], {"work_order_id": wo["id"], "wo_no": wo["wo_no"], "product_name": wo["product_name"],
                                           "planned_start": wo["planned_start"], "materials": []})
        risk["materials"].append(row["material_code"])
    return sorted(shortages.values(), key=lambda e: e["expected_date"]), list(risky.values())
