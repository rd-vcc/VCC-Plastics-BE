from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from app.core.database import get_vcc_plastics_connection
from app.core.employee_names import attach_employee_names
from app.modules.material.inventory.schemas import (
    AdjustIn,
    HoldIn,
    IqcIn,
    LocationIn,
    LotAction,
    ReceiveIn,
    RegrindIn,
    SafetyStockIn,
    TransferIn,
)
from app.modules.material.inventory.service import (
    CATEGORIES,
    LOT_SELECT,
    available_by_material,
    decorate_lot,
    get_active_material,
    get_location,
    get_lot,
    load_settings,
    material_category,
    next_lot_no,
    plan_shortages,
    safety_stocks,
    write_txn,
)


router = APIRouter(prefix="/api/material-inventory", tags=["Material Inventory"])

STALE = "This lot was changed by another user. Reload and try again."


def _run_write(action):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        result = action(cur)
        conn.commit()
        return result
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        message = str(exc)
        if "Duplicate" in message:
            raise HTTPException(status_code=409, detail="Duplicate data.") from exc
        if "foreign key constraint fails" in message.lower():
            raise HTTPException(status_code=409, detail="Data is referenced or reference is invalid.") from exc
        raise HTTPException(status_code=400, detail=message) from exc
    finally:
        cur.close()
        conn.close()


def _read(action):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        return action(cur)
    finally:
        cur.close(); conn.close()


def _bump(cur, lot_id: int, version: int, sets: str, params: tuple, actor):
    cur.execute(f"UPDATE material_lots SET {sets}, updated_by=%s, version=version+1 WHERE id=%s AND version=%s",
                params + (actor, lot_id, version))
    if cur.rowcount == 0:
        raise HTTPException(status_code=409, detail=STALE)


# =========================================================
# LOOKUPS + LOCATIONS + SETTINGS
# =========================================================


@router.get("/lookups")
def lookups():
    def action(cur):
        cur.execute("SELECT id, material_code, material_name, material_type, material_group, unit FROM materials WHERE status='ACTIVE' ORDER BY material_code")
        materials = [{**m, "category": material_category(m["material_type"], m["material_group"], m["material_code"])} for m in cur.fetchall()]
        cur.execute("SELECT * FROM material_locations ORDER BY sort_order, location_code")
        locations = cur.fetchall()
        cur.execute(
            """SELECT d.id, d.wo_no, d.reject_qty, p.product_name, e.equipment_code AS machine_code
               FROM production_plan_details d JOIN products p ON p.id = d.product_id
               LEFT JOIN equipment e ON e.id = d.machine_id
               WHERE d.status <> 'CANCELLED' ORDER BY d.updated_at DESC LIMIT 200""")
        work_orders = cur.fetchall()
        cur.execute(
            """SELECT DISTINCT supplier FROM (SELECT supplier FROM material_lots WHERE supplier IS NOT NULL
               UNION SELECT supplier FROM materials WHERE supplier IS NOT NULL) s ORDER BY supplier""")
        suppliers = [r["supplier"] for r in cur.fetchall()]
        cur.execute("SELECT id, equipment_code, equipment_name FROM equipment WHERE asset_status='ACTIVE' ORDER BY equipment_code")
        machines = cur.fetchall()
        return {"materials": materials, "locations": locations, "work_orders": work_orders, "suppliers": suppliers, "machines": machines,
                "settings": load_settings(cur), "categories": CATEGORIES}
    return _read(action)


@router.post("/locations", status_code=201)
def create_location(payload: LocationIn):
    def action(cur):
        cur.execute(
            """INSERT INTO material_locations (location_code, location_name, location_type, equipment_id, capacity_kg,
               sort_order, status, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (payload.location_code, payload.location_name.strip(), payload.location_type, payload.equipment_id,
             payload.capacity_kg, payload.sort_order, payload.status, payload.actor))
        return {"message": "Location created.", "id": cur.lastrowid}
    return _run_write(action)


@router.put("/locations/{location_id}")
def update_location(location_id: int, payload: LocationIn):
    def action(cur):
        if payload.status == "INACTIVE":
            cur.execute(f"SELECT COUNT(*) total FROM material_lots WHERE location_id=%s AND status IN ('WAITING_IQC','IN_STOCK','IN_USE','HOLD')", (location_id,))
            if cur.fetchone()["total"]:
                raise HTTPException(status_code=409, detail="The location still holds lots; move them before deactivating it.")
        cur.execute(
            """UPDATE material_locations SET location_code=%s, location_name=%s, location_type=%s, equipment_id=%s,
               capacity_kg=%s, sort_order=%s, status=%s, updated_by=%s, version=version+1 WHERE id=%s AND version=%s""",
            (payload.location_code, payload.location_name.strip(), payload.location_type, payload.equipment_id,
             payload.capacity_kg, payload.sort_order, payload.status, payload.actor, location_id, payload.version or 0))
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="Location not found or changed by another user. Reload and try again.")
        return {"message": "Location updated."}
    return _run_write(action)


@router.get("/stock")
def stock_by_material():
    def action(cur):
        cur.execute("SELECT id, material_code, material_name, material_type, material_group, unit FROM materials WHERE status='ACTIVE' ORDER BY material_code")
        materials = cur.fetchall()
        stock = available_by_material(cur)
        safety = safety_stocks(cur)
        empty = {"available": 0.0, "reserved": 0.0, "in_use": 0.0, "waiting_iqc": 0.0, "hold": 0.0, "expired": 0.0, "total": 0.0}
        return [{**m, **stock.get(m["id"], empty), "safety_stock_qty": safety.get(m["id"], 0.0),
                 "category": material_category(m["material_type"], m["material_group"], m["material_code"])} for m in materials]
    return _read(action)


@router.put("/safety-stock/{material_id}")
def set_safety_stock(material_id: int, payload: SafetyStockIn):
    def action(cur):
        get_active_material(cur, material_id)
        cur.execute(
            """INSERT INTO material_stock_settings (material_id, safety_stock_qty, updated_by) VALUES (%s,%s,%s)
               ON DUPLICATE KEY UPDATE safety_stock_qty=VALUES(safety_stock_qty), updated_by=VALUES(updated_by)""",
            (material_id, payload.safety_stock_qty, payload.actor))
        return {"message": "Safety stock updated."}
    return _run_write(action)


# =========================================================
# LOTS
# =========================================================


@router.get("/lots")
def list_lots(
    keyword: str | None = None, material_id: int | None = None, category: str | None = None, status: str | None = None,
    location_id: int | None = None, supplier: str | None = None, received_from: date | None = None,
    received_to: date | None = None, include_closed: bool = False, limit: int = Query(default=2000, ge=1, le=10000),
):
    def action(cur):
        settings = load_settings(cur)
        conditions, params = [], []
        if not include_closed:
            conditions.append("l.status NOT IN ('CONSUMED','CLOSED')")
        if keyword:
            conditions.append("(l.lot_no LIKE %s OR m.material_code LIKE %s OR m.material_name LIKE %s OR l.supplier_lot_no LIKE %s)")
            value = f"%{keyword.strip()}%"; params += [value] * 4
        for field, value in (("l.material_id", material_id), ("l.location_id", location_id)):
            if value is not None:
                conditions.append(f"{field}=%s"); params.append(value)
        if supplier:
            conditions.append("l.supplier=%s"); params.append(supplier)
        if received_from:
            conditions.append("l.received_at >= %s"); params.append(received_from)
        if received_to:
            conditions.append("l.received_at < DATE_ADD(%s, INTERVAL 1 DAY)"); params.append(received_to)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        cur.execute(f"{LOT_SELECT}{where} ORDER BY l.received_at DESC, l.lot_no DESC LIMIT %s", tuple(params + [limit]))
        today = date.today()
        rows = [decorate_lot(r, today, settings["material.expiry_warning_days"]) for r in cur.fetchall()]
        if status:
            wanted = {s.strip().upper() for s in status.split(",")}
            rows = [r for r in rows if r["effective_status"] in wanted or ("NEAR_EXPIRY" in wanted and r["near_expiry"])]
        if category:
            rows = [r for r in rows if r["category"] == category.upper()]
        states = {}
        for r in rows:
            states[r["effective_status"]] = states.get(r["effective_status"], 0) + 1
        return {
            "items": rows,
            "summary": {
                "total": len(rows), "in_stock": states.get("IN_STOCK", 0), "in_use": states.get("IN_USE", 0),
                "waiting_iqc": states.get("WAITING_IQC", 0), "hold": states.get("HOLD", 0), "expired": states.get("EXPIRED", 0),
                "near_expiry": sum(1 for r in rows if r["near_expiry"] and r["effective_status"] != "EXPIRED"),
                "by_status": states,
            },
        }
    return _read(action)


@router.get("/lots/{lot_id}")
def lot_detail(lot_id: int):
    def action(cur):
        settings = load_settings(cur)
        lot = decorate_lot(get_lot(cur, lot_id), date.today(), settings["material.expiry_warning_days"])
        cur.execute(
            """SELECT t.*, lf.location_name AS from_location, lt.location_name AS to_location,
                      wo.wo_no, e.equipment_code AS machine_code
               FROM material_lot_transactions t
               LEFT JOIN material_locations lf ON lf.id = t.from_location_id
               LEFT JOIN material_locations lt ON lt.id = t.to_location_id
               LEFT JOIN production_plan_details wo ON wo.id = t.work_order_id
               LEFT JOIN equipment e ON e.id = t.machine_id
               WHERE t.lot_id=%s ORDER BY t.acted_at, t.id""",
            (lot_id,))
        transactions = attach_employee_names(cur, cur.fetchall(), ["actor"])
        lot = attach_employee_names(cur, [lot], ["iqc_by", "created_by"])[0]
        return {**lot, "transactions": transactions}
    return _read(action)


@router.post("/lots/receive", status_code=201)
def receive_lot(payload: ReceiveIn):
    def action(cur):
        get_active_material(cur, payload.material_id)
        location = get_location(cur, payload.location_id)
        if location["location_type"] == "MACHINE":
            raise HTTPException(status_code=400, detail="Receive into a warehouse, silo or IQC area, not directly to a machine.")
        lot_no = (payload.lot_no or "").strip().upper() or next_lot_no(cur, "L")
        status = "WAITING_IQC" if payload.iqc_required else "IN_STOCK"
        cur.execute(
            """INSERT INTO material_lots (lot_no, material_id, source, supplier, supplier_lot_no, received_at, manufacture_date,
               expiry_date, initial_qty, current_qty, location_id, status, iqc_result, remark, created_by)
               VALUES (%s,%s,'RECEIVING',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (lot_no, payload.material_id, payload.supplier, payload.supplier_lot_no, payload.received_at or datetime.now(),
             payload.manufacture_date, payload.expiry_date, payload.qty, payload.qty, payload.location_id, status,
             "PENDING" if payload.iqc_required else "NOT_REQUIRED", payload.remark, payload.actor))
        lot_id = cur.lastrowid
        write_txn(cur, lot_id, "RECEIVING", payload.qty, payload.qty, status, payload.actor, to_location_id=payload.location_id,
                  reference_no=payload.reference_no, remark=payload.remark)
        return {"message": "Lot received.", "id": lot_id, "lot_no": lot_no}
    return _run_write(action)


@router.post("/lots/regrind", status_code=201)
def create_regrind_lot(payload: RegrindIn):
    def action(cur):
        material = get_active_material(cur, payload.material_id)
        if material["material_type"] != "RECYCLED":
            raise HTTPException(status_code=400, detail="Regrind lots must use a recycled (regrind) material.")
        location = get_location(cur, payload.location_id)
        if location["location_type"] == "MACHINE":
            raise HTTPException(status_code=400, detail="Store regrind in a warehouse, silo or IQC area.")
        reference = None
        if payload.source_work_order_id:
            cur.execute("SELECT wo_no FROM production_plan_details WHERE id=%s", (payload.source_work_order_id,))
            wo = cur.fetchone()
            if not wo:
                raise HTTPException(status_code=404, detail="Source work order not found.")
            reference = wo["wo_no"]
        lot_no = next_lot_no(cur, "RG")
        status = "WAITING_IQC" if payload.iqc_required else "IN_STOCK"
        cur.execute(
            """INSERT INTO material_lots (lot_no, material_id, source, source_work_order_id, supplier, received_at, initial_qty,
               current_qty, location_id, status, iqc_result, remark, created_by)
               VALUES (%s,%s,'REGRIND',%s,'VCC Plastics (internal)',NOW(),%s,%s,%s,%s,%s,%s,%s)""",
            (lot_no, payload.material_id, payload.source_work_order_id, payload.qty, payload.qty, payload.location_id, status,
             "PENDING" if payload.iqc_required else "NOT_REQUIRED", payload.remark, payload.actor))
        lot_id = cur.lastrowid
        write_txn(cur, lot_id, "REGRIND_CREATE", payload.qty, payload.qty, status, payload.actor, to_location_id=payload.location_id,
                  work_order_id=payload.source_work_order_id, reference_no=reference, remark=payload.remark)
        return {"message": "Regrind lot created.", "id": lot_id, "lot_no": lot_no}
    return _run_write(action)


@router.post("/lots/{lot_id}/iqc")
def iqc_lot(lot_id: int, payload: IqcIn):
    def action(cur):
        lot = get_lot(cur, lot_id, for_update=True)
        if lot["status"] != "WAITING_IQC":
            raise HTTPException(status_code=409, detail="Only lots waiting for IQC can receive an IQC result.")
        to_location = lot["location_id"]
        if payload.result == "PASS":
            if payload.to_location_id:
                to_location = get_location(cur, payload.to_location_id)["id"]
            new_status, hold = "IN_STOCK", None
        else:
            cur.execute("SELECT id FROM material_locations WHERE location_type='HOLD_AREA' AND status='ACTIVE' ORDER BY sort_order LIMIT 1")
            hold_area = cur.fetchone()
            to_location = payload.to_location_id or (hold_area["id"] if hold_area else lot["location_id"])
            new_status, hold = "HOLD", payload.remark or "IQC failed"
        _bump(cur, lot_id, payload.version, "status=%s, iqc_result=%s, iqc_by=%s, iqc_at=NOW(), location_id=%s, hold_reason=%s",
              (new_status, payload.result, payload.actor, to_location, hold), payload.actor)
        write_txn(cur, lot_id, "IQC_PASS" if payload.result == "PASS" else "IQC_FAIL", 0, float(lot["current_qty"]), new_status,
                  payload.actor, from_location_id=lot["location_id"], to_location_id=to_location, remark=payload.remark)
        return {"message": "IQC result recorded.", "version": payload.version + 1}
    return _run_write(action)


@router.post("/lots/{lot_id}/transfer")
def transfer_lot(lot_id: int, payload: TransferIn):
    def action(cur):
        lot = get_lot(cur, lot_id, for_update=True)
        if lot["status"] in {"CONSUMED", "CLOSED"}:
            raise HTTPException(status_code=409, detail="Consumed or closed lots cannot be moved.")
        if float(lot["reserved_qty"] or 0) > 0:
            raise HTTPException(status_code=409, detail="Release the lot's reservations before moving it.")
        location = get_location(cur, payload.to_location_id)
        if location["id"] == lot["location_id"]:
            raise HTTPException(status_code=400, detail="The lot is already at this location.")
        if location["location_type"] == "MACHINE":
            raise HTTPException(status_code=400, detail="Issue to a machine through Material Allocation, not a transfer.")
        _bump(cur, lot_id, payload.version, "location_id=%s", (location["id"],), payload.actor)
        write_txn(cur, lot_id, "TRANSFER", 0, float(lot["current_qty"]), lot["status"], payload.actor,
                  from_location_id=lot["location_id"], to_location_id=location["id"], remark=payload.remark)
        return {"message": "Lot moved.", "version": payload.version + 1}
    return _run_write(action)


@router.post("/lots/{lot_id}/hold")
def hold_lot(lot_id: int, payload: HoldIn):
    def action(cur):
        lot = get_lot(cur, lot_id, for_update=True)
        if lot["status"] not in {"IN_STOCK", "WAITING_IQC"}:
            raise HTTPException(status_code=409, detail="Only lots in stock or waiting for IQC can be put on hold.")
        if float(lot["reserved_qty"] or 0) > 0:
            raise HTTPException(status_code=409, detail="Release the lot's reservations before putting it on hold.")
        _bump(cur, lot_id, payload.version, "status='HOLD', hold_reason=%s", (payload.reason.strip(),), payload.actor)
        write_txn(cur, lot_id, "HOLD", 0, float(lot["current_qty"]), "HOLD", payload.actor, remark=payload.reason.strip())
        return {"message": "Lot put on hold.", "version": payload.version + 1}
    return _run_write(action)


@router.post("/lots/{lot_id}/release")
def release_lot(lot_id: int, payload: LotAction):
    def action(cur):
        lot = get_lot(cur, lot_id, for_update=True)
        if lot["status"] != "HOLD":
            raise HTTPException(status_code=409, detail="Only lots on hold can be released.")
        if lot["iqc_result"] == "FAIL" and not (payload.remark or "").strip():
            raise HTTPException(status_code=400, detail="This lot failed IQC; enter the disposition reason to release it.")
        _bump(cur, lot_id, payload.version, "status='IN_STOCK', hold_reason=NULL", (), payload.actor)
        write_txn(cur, lot_id, "RELEASE", 0, float(lot["current_qty"]), "IN_STOCK", payload.actor, remark=payload.remark)
        return {"message": "Lot released.", "version": payload.version + 1}
    return _run_write(action)


@router.post("/lots/{lot_id}/adjust")
def adjust_lot(lot_id: int, payload: AdjustIn):
    def action(cur):
        lot = get_lot(cur, lot_id, for_update=True)
        if lot["status"] in {"CONSUMED", "CLOSED"}:
            raise HTTPException(status_code=409, detail="Consumed or closed lots cannot be adjusted.")
        if payload.new_qty < float(lot["reserved_qty"] or 0):
            raise HTTPException(status_code=409, detail="The new quantity is below the quantity reserved for work orders.")
        change = round(payload.new_qty - float(lot["current_qty"]), 3)
        if change == 0:
            raise HTTPException(status_code=400, detail="The quantity did not change.")
        new_status = "CLOSED" if payload.new_qty == 0 else lot["status"]
        _bump(cur, lot_id, payload.version, "current_qty=%s, status=%s", (payload.new_qty, new_status), payload.actor)
        write_txn(cur, lot_id, "ADJUSTMENT", change, payload.new_qty, new_status, payload.actor, remark=payload.reason.strip())
        return {"message": "Lot quantity adjusted.", "version": payload.version + 1}
    return _run_write(action)


@router.get("/transactions")
def recent_transactions(limit: int = Query(default=50, ge=1, le=1000), lot_id: int | None = None):
    def action(cur):
        where = "WHERE t.lot_id=%s" if lot_id else ""
        params = (lot_id, limit) if lot_id else (limit,)
        cur.execute(
            f"""SELECT t.*, l.lot_no, m.material_code, m.unit, lf.location_name AS from_location, lt.location_name AS to_location,
                       wo.wo_no, e.equipment_code AS machine_code
                FROM material_lot_transactions t
                JOIN material_lots l ON l.id = t.lot_id
                JOIN materials m ON m.id = l.material_id
                LEFT JOIN material_locations lf ON lf.id = t.from_location_id
                LEFT JOIN material_locations lt ON lt.id = t.to_location_id
                LEFT JOIN production_plan_details wo ON wo.id = t.work_order_id
                LEFT JOIN equipment e ON e.id = t.machine_id
                {where} ORDER BY t.acted_at DESC, t.id DESC LIMIT %s""",
            params)
        return attach_employee_names(cur, cur.fetchall(), ["actor"])
    return _read(action)


# =========================================================
# MATERIAL STATUS (screen 4.1)
# =========================================================


@router.get("/status-summary")
def status_summary(expiry_window_days: int = Query(default=90, ge=1, le=730)):
    def action(cur):
        settings = load_settings(cur)
        today = date.today()
        cur.execute(f"{LOT_SELECT} WHERE l.status IN ('WAITING_IQC','IN_STOCK','IN_USE','HOLD')")
        lots = [decorate_lot(r, today, settings["material.expiry_warning_days"]) for r in cur.fetchall()]
        cur.execute("SELECT id, material_code, material_name, material_type, material_group, unit FROM materials WHERE status='ACTIVE'")
        materials = {m["id"]: m for m in cur.fetchall()}
        safety = safety_stocks(cur)
        stock = available_by_material(cur)

        # weight totals (shown in tonnes) only add up materials stocked in KG; other units are counted per lot
        weight = {mid for mid, m in materials.items() if str(m["unit"] or "").upper() == "KG"}
        buckets = {"available": 0.0, "reserved": 0.0, "in_use": 0.0, "waiting_iqc": 0.0, "hold": 0.0, "expired": 0.0}
        for mid, b in stock.items():
            if mid in weight:
                for key in buckets:
                    buckets[key] += b[key]
        by_location, by_category = {}, {c: 0.0 for c in CATEGORIES}
        for lot in lots:
            loc = by_location.setdefault(lot["location_id"], {"location_id": lot["location_id"], "location_name": lot["location_name"],
                                                              "location_type": lot["location_type"], "qty": 0.0, "lots": 0})
            loc["lots"] += 1
            if lot["material_id"] in weight:
                loc["qty"] += lot["current_qty"]
                by_category[lot["category"]] += lot["current_qty"]

        low = []
        for mid, level in safety.items():
            if mid not in materials or level <= 0:
                continue
            available = stock.get(mid, {}).get("available", 0.0)
            if available < level:
                m = materials[mid]
                low.append({"material_id": mid, "material_code": m["material_code"], "material_name": m["material_name"], "unit": m["unit"],
                            "available": round(available, 3), "safety_stock_qty": level, "coverage_pct": round(available / level * 100, 1)})
        low.sort(key=lambda r: r["coverage_pct"])
        shortages, risky_work_orders = plan_shortages(cur)
        near = sorted([l for l in lots if l["days_to_expiry"] is not None and 0 <= l["days_to_expiry"] <= expiry_window_days],
                      key=lambda l: l["days_to_expiry"])
        expired = [l for l in lots if l["effective_status"] == "EXPIRED"]
        alerts = {
            "low_stock": len(low), "near_expiry": sum(1 for l in lots if l["near_expiry"]), "expired": len(expired),
            "hold": sum(1 for l in lots if l["status"] == "HOLD"), "waiting_iqc": sum(1 for l in lots if l["status"] == "WAITING_IQC"),
            "shortage": len(shortages),
        }
        return {
            "settings": settings,
            "kpis": {"material_types": len(materials), "stocked_materials": len({l["material_id"] for l in lots}),
                     **{k: round(v, 3) for k, v in buckets.items()}, "low_stock": len(low), "shortage": len(shortages),
                     "near_expiry_lots": alerts["near_expiry"]},
            "by_status": {k: round(v, 3) for k, v in buckets.items()},
            "by_location": sorted(by_location.values(), key=lambda r: -r["qty"]),
            "weight_unit": "KG",
            "by_category": {k: round(v, 3) for k, v in by_category.items()},
            "alerts": alerts,
            "low_stock": low[:20],
            "shortages": shortages[:20],
            "risky_work_orders": risky_work_orders[:20],
            "near_expiry_lots": near[:20],
            "server_time": datetime.now(),
        }
    return _read(action)
