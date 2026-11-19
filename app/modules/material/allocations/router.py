from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.database import get_vcc_plastics_connection
from app.modules.material.allocations.service import (
    ALLOC_SELECT,
    auto_allocate,
    decorate,
    expire_reservations,
    extend,
    get_allocation,
    issue,
    lot_suggestions,
    release,
    requirement_state,
    reserve,
    return_material,
)
from app.modules.material.inventory.service import CATEGORIES, load_settings, material_category


router = APIRouter(prefix="/api/material-allocations", tags=["Material Allocation"])


class ReserveIn(BaseModel):
    requirement_id: int = Field(gt=0)
    lot_id: int = Field(gt=0)
    qty: float = Field(gt=0)
    actor: Optional[str] = Field(default=None, max_length=100)


class AutoIn(BaseModel):
    work_order_id: int = Field(gt=0)
    actor: Optional[str] = Field(default=None, max_length=100)


class IssueIn(BaseModel):
    qty: Optional[float] = Field(default=None, gt=0)
    machine_id: Optional[int] = Field(default=None, gt=0)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class ReasonIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class ReturnIn(ReasonIn):
    qty: float = Field(gt=0)


class ExtendIn(BaseModel):
    hours: float = Field(gt=0, le=720)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


def _read(action):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        return action(cur)
    finally:
        cur.close(); conn.close()


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
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cur.close()
        conn.close()


def _read_with_expiry(action):
    """Release expired reservations first (committed), then read."""
    def wrapped(cur):
        expire_reservations(cur)
        return action(cur)
    return _run_write(wrapped)


@router.get("/lookups")
def lookups():
    def action(cur):
        cur.execute(
            """SELECT d.id, d.wo_no, d.status, d.machine_id, d.planned_start, p.product_name, e.equipment_code AS machine_code,
                      (SELECT COUNT(*) FROM material_requirements r WHERE r.work_order_id=d.id AND r.status<>'CANCELLED') AS line_count
               FROM production_plan_details d JOIN products p ON p.id = d.product_id LEFT JOIN equipment e ON e.id = d.machine_id
               WHERE d.status IN ('UNSCHEDULED','SCHEDULED','RELEASED','IN_PRODUCTION','ON_HOLD') ORDER BY d.planned_start IS NULL, d.planned_start, d.wo_no""")
        work_orders = cur.fetchall()
        cur.execute("SELECT id, material_code, material_name, material_type, material_group, unit FROM materials WHERE status='ACTIVE' ORDER BY material_code")
        materials = [{**m, "category": material_category(m["material_type"], m["material_group"], m["material_code"])} for m in cur.fetchall()]
        cur.execute("SELECT id, location_code, location_name, location_type FROM material_locations WHERE status='ACTIVE' ORDER BY sort_order")
        locations = cur.fetchall()
        cur.execute(
            """SELECT e.id, e.equipment_code, e.equipment_name FROM equipment e JOIN equipment_types t ON t.id = e.equipment_type_id
               WHERE t.category='MACHINE' AND e.asset_status='ACTIVE' ORDER BY e.equipment_code""")
        machines = cur.fetchall()
        return {"work_orders": work_orders, "materials": materials, "locations": locations, "machines": machines,
                "categories": CATEGORIES, "settings": load_settings(cur)}
    return _read(action)


@router.get("/summary")
def summary(
    date_from: date | None = None, date_to: date | None = None, status: str | None = None, work_order_id: int | None = None,
    category: str | None = None, material_id: int | None = None, location_id: int | None = None,
    expiring_hours: float = Query(default=4, gt=0, le=720),
):
    start = date_from or date.today()
    end = date_to or date.today()

    def action(cur):
        now = datetime.now()
        start_dt = datetime.combine(start, datetime.min.time())
        end_dt = datetime.combine(end, datetime.min.time()) + timedelta(days=1)
        # period = allocations created in the range, plus every allocation still active
        cur.execute(f"{ALLOC_SELECT} WHERE (a.reserved_at >= %s AND a.reserved_at < %s) OR a.status IN ('RESERVED','IN_USE') ORDER BY a.reserved_at DESC",
                    (start_dt, end_dt))
        rows = [decorate(r, now) for r in cur.fetchall()]
        if status:
            wanted = {s.strip().upper() for s in status.split(",")}
            rows = [r for r in rows if r["status"] in wanted]
        if work_order_id:
            rows = [r for r in rows if r["work_order_id"] == work_order_id]
        if category:
            rows = [r for r in rows if r["category"] == category.upper()]
        if material_id:
            rows = [r for r in rows if r["material_id"] == material_id]
        if location_id:
            rows = [r for r in rows if r["location_id"] == location_id]
        kg = [r for r in rows if str(r["unit"] or "").upper() == "KG"]

        by_status = {}
        for r in kg:
            qty = r["initial_qty"] if r["status"] in ("COMPLETED", "CANCELLED") else r["total_qty"]
            by_status[r["status"]] = round(by_status.get(r["status"], 0.0) + qty, 3)
        by_area, by_wo = {}, {}
        for r in kg:
            if r["status"] == "CANCELLED":
                continue
            by_area[r["location_name"]] = round(by_area.get(r["location_name"], 0.0) + r["reserved_qty"] + r["issued_qty"] - r["returned_qty"], 3)
            wo = by_wo.setdefault(r["work_order_id"], {"work_order_id": r["work_order_id"], "wo_no": r["wo_no"], "product_name": r["product_name"],
                                                        "wo_status": r["wo_status"], "total": 0.0, "reserved": 0.0, "in_use": 0.0})
            wo["total"] = round(wo["total"] + r["reserved_qty"] + r["issued_qty"] - r["returned_qty"], 3)
            wo["reserved"] = round(wo["reserved"] + r["reserved_qty"], 3)
            wo["in_use"] = round(wo["in_use"] + r["in_use_qty"], 3)
        expiring = sorted([r for r in rows if r["hours_left"] is not None and r["hours_left"] <= expiring_hours], key=lambda r: r["hours_left"])
        cur.execute(
            """SELECT t.id, t.acted_at, t.txn_type, t.qty_change, t.reference_no, t.actor, t.remark, l.lot_no, m.material_code, m.unit,
                      wo.wo_no, e.equipment_code AS machine_code
               FROM material_lot_transactions t JOIN material_lots l ON l.id = t.lot_id JOIN materials m ON m.id = l.material_id
               LEFT JOIN production_plan_details wo ON wo.id = t.work_order_id LEFT JOIN equipment e ON e.id = t.machine_id
               WHERE t.reference_no LIKE 'MA-%%' ORDER BY t.acted_at DESC, t.id DESC LIMIT 20""")
        recent = cur.fetchall()
        return {
            "period": {"date_from": start, "date_to": end},
            "items": rows,
            "kpis": {
                "total": round(sum(r["initial_qty"] for r in kg if r["status"] != "CANCELLED"), 3),
                "in_use": round(sum(r["in_use_qty"] for r in kg), 3),
                "reserved": round(sum(r["reserved_qty"] for r in kg), 3),
                "completed": round(sum(r["initial_qty"] for r in kg if r["status"] == "COMPLETED"), 3),
                "cancelled": round(sum(r["initial_qty"] for r in kg if r["status"] == "CANCELLED"), 3),
                "expiring": len(expiring), "count": len(rows),
            },
            "by_status": by_status,
            "by_area": sorted(by_area.items(), key=lambda kv: -kv[1]),
            "by_work_order": sorted(by_wo.values(), key=lambda w: -w["total"])[:5],
            "expiring": expiring,
            "recent": recent,
            "settings": load_settings(cur),
            "server_time": now,
        }
    return _read_with_expiry(action)


@router.get("/work-orders/{work_order_id}/lines")
def work_order_lines(work_order_id: int):
    def action(cur):
        cur.execute("SELECT id FROM material_requirements WHERE work_order_id=%s AND status<>'CANCELLED' ORDER BY id", (work_order_id,))
        lines = []
        for row in cur.fetchall():
            line = requirement_state(cur, row["id"])
            lines.append({"id": line["id"], "material_id": line["material_id"], "material_code": line["material_code"], "unit": line["unit"],
                          "status": line["status"], "need_qty": line["need_qty"], "allocated_qty": line["allocated_qty"],
                          "remaining_qty": line["remaining_qty"], "lots": lot_suggestions(cur, line["material_id"], line["remaining_qty"])})
        return lines
    return _read_with_expiry(action)


@router.get("/{allocation_id}")
def allocation_detail(allocation_id: int):
    def action(cur):
        a = decorate(get_allocation(cur, allocation_id), datetime.now())
        cur.execute(
            """SELECT t.*, lf.location_name AS from_location, lt.location_name AS to_location, e.equipment_code AS machine_code
               FROM material_lot_transactions t LEFT JOIN material_locations lf ON lf.id = t.from_location_id
               LEFT JOIN material_locations lt ON lt.id = t.to_location_id LEFT JOIN equipment e ON e.id = t.machine_id
               WHERE t.reference_no=%s ORDER BY t.acted_at, t.id""", (a["allocation_no"],))
        return {**a, "transactions": cur.fetchall()}
    return _read(action)


@router.post("", status_code=201)
def create_allocation(payload: ReserveIn):
    def action(cur):
        expire_reservations(cur)
        return {"message": "Material reserved.", **reserve(cur, payload.requirement_id, payload.lot_id, payload.qty, payload.actor or "SYSTEM")}
    return _run_write(action)


@router.post("/auto", status_code=201)
def auto(payload: AutoIn):
    def action(cur):
        expire_reservations(cur)
        result = auto_allocate(cur, payload.work_order_id, payload.actor or "SYSTEM")
        return {"message": f"{len(result['created'])} allocations created.", **result}
    return _run_write(action)


@router.post("/{allocation_id}/issue")
def issue_allocation(allocation_id: int, payload: IssueIn):
    result = _run_write(lambda cur: issue(cur, allocation_id, payload.qty, payload.machine_id, payload.actor or "SYSTEM", payload.version) or {"message": "Material issued to machine."})
    from app.modules.notifications import events
    events.material_issued(allocation_id, payload.actor)
    return result


@router.post("/{allocation_id}/release")
def release_allocation(allocation_id: int, payload: ReasonIn):
    return _run_write(lambda cur: release(cur, allocation_id, payload.reason.strip(), payload.actor or "SYSTEM", payload.version) or {"message": "Reservation released."})


@router.post("/{allocation_id}/return")
def return_allocation(allocation_id: int, payload: ReturnIn):
    return _run_write(lambda cur: return_material(cur, allocation_id, payload.qty, payload.reason.strip(), payload.actor or "SYSTEM", payload.version) or {"message": "Material returned."})


@router.post("/{allocation_id}/extend")
def extend_allocation(allocation_id: int, payload: ExtendIn):
    return _run_write(lambda cur: extend(cur, allocation_id, payload.hours, payload.actor or "SYSTEM", payload.version) or {"message": "Reservation extended."})
