from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.core.database import get_vcc_plastics_connection
from app.modules.material.inventory.service import CATEGORIES, load_settings, material_category
from app.modules.material.requirements.service import LINE_SELECT, build_summary, open_lines, refresh_statuses, sync_all, walk_coverage
from app.modules.material.inventory.service import available_by_material


router = APIRouter(prefix="/api/material-requirements", tags=["Material Requirement"])


class AdjustIn(BaseModel):
    adjustment_qty: float
    reason: str = Field(min_length=1, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)


class RecalcIn(BaseModel):
    actor: Optional[str] = Field(default=None, max_length=100)


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


def _period(date_from, date_to):
    start = date_from or (date.today() - timedelta(days=date.today().weekday()))
    end = date_to or (start + timedelta(days=6))
    if end < start:
        raise HTTPException(status_code=400, detail="The end date must be on or after the start date.")
    return start, end


@router.get("/lookups")
def lookups():
    def action(cur):
        cur.execute("SELECT id, material_code, material_name, material_type, material_group, unit FROM materials WHERE status='ACTIVE' ORDER BY material_code")
        materials = [{**m, "category": material_category(m["material_type"], m["material_group"], m["material_code"])} for m in cur.fetchall()]
        cur.execute(
            """SELECT d.id, d.wo_no, p.product_name FROM production_plan_details d JOIN products p ON p.id = d.product_id
               WHERE d.status IN ('UNSCHEDULED','SCHEDULED','RELEASED','IN_PRODUCTION','ON_HOLD') ORDER BY d.wo_no""")
        work_orders = cur.fetchall()
        cur.execute(
            """SELECT p.id, p.product_code, p.product_name FROM products p
               WHERE p.status='ACTIVE' AND NOT EXISTS (SELECT 1 FROM boms b WHERE b.product_id=p.id AND b.status='ACTIVE')""")
        without_bom = cur.fetchall()
        return {"materials": materials, "work_orders": work_orders, "categories": CATEGORIES, "settings": load_settings(cur),
                "products_without_bom": without_bom}
    return _read(action)


@router.get("/summary")
def summary(
    date_from: date | None = None, date_to: date | None = None, category: str | None = None, material_id: int | None = None,
    work_order_id: int | None = None, priority: str | None = None, include_unscheduled: bool = True,
):
    start, end = _period(date_from, date_to)
    return _read(lambda cur: build_summary(cur, start, end, category.upper() if category else None, material_id, work_order_id,
                                          priority, include_unscheduled))


@router.get("/lines")
def lines(material_id: int = Query(gt=0), date_from: date | None = None, date_to: date | None = None, include_unscheduled: bool = True):
    start, end = _period(date_from, date_to)

    def action(cur):
        rows = open_lines(cur)
        stock = {mid: b["available"] for mid, b in available_by_material(cur, list({r["material_id"] for r in rows})).items()} if rows else {}
        walk_coverage(rows, stock)
        from datetime import datetime
        start_dt = datetime.combine(start, datetime.min.time())
        end_dt = datetime.combine(end, datetime.min.time()) + timedelta(days=1)
        return [r for r in rows if r["material_id"] == material_id and (
            (r["required_date"] is None and include_unscheduled) or (r["required_date"] is not None and start_dt <= r["required_date"] < end_dt))]
    return _read(action)


@router.put("/lines/{line_id}/adjust")
def adjust_line(line_id: int, payload: AdjustIn):
    def action(cur):
        cur.execute(f"{LINE_SELECT} WHERE r.id=%s", (line_id,))
        line = cur.fetchone()
        if not line:
            raise HTTPException(status_code=404, detail="Requirement line not found.")
        if line["status"] == "CANCELLED" or line["wo_status"] not in ("UNSCHEDULED", "SCHEDULED", "RELEASED", "IN_PRODUCTION", "ON_HOLD"):
            raise HTTPException(status_code=409, detail="Only open requirement lines can be adjusted.")
        if float(line["open_qty"]) + payload.adjustment_qty < 0:
            raise HTTPException(status_code=400, detail="The adjustment cannot make the requirement negative.")
        if round(float(line["adjustment_qty"]), 4) == round(payload.adjustment_qty, 4):
            raise HTTPException(status_code=400, detail="The adjustment did not change.")
        cur.execute("UPDATE material_requirements SET adjustment_qty=%s, updated_by=%s WHERE id=%s", (payload.adjustment_qty, payload.actor, line_id))
        cur.execute(
            "INSERT INTO material_requirement_adjustments (requirement_id, old_qty, new_qty, reason, actor) VALUES (%s,%s,%s,%s,%s)",
            (line_id, line["adjustment_qty"], payload.adjustment_qty, payload.reason.strip(), payload.actor or "SYSTEM"))
        refresh_statuses(cur)
        return {"message": "Requirement adjusted."}
    return _run_write(action)


@router.get("/adjustments")
def adjustments(limit: int = Query(default=100, ge=1, le=1000)):
    def action(cur):
        cur.execute(
            """SELECT a.*, d.wo_no, m.material_code, m.unit FROM material_requirement_adjustments a
               JOIN material_requirements r ON r.id = a.requirement_id
               JOIN production_plan_details d ON d.id = r.work_order_id
               JOIN materials m ON m.id = r.material_id
               ORDER BY a.acted_at DESC, a.id DESC LIMIT %s""", (limit,))
        return cur.fetchall()
    return _read(action)


@router.post("/recalculate")
def recalculate(payload: RecalcIn):
    return _run_write(lambda cur: {"message": "Requirements recalculated.", "work_orders": sync_all(cur, payload.actor or "SYSTEM")})
