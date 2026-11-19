from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.database import get_vcc_plastics_connection
from app.core.employee_names import attach_employee_names
from app.modules.material.inventory.service import CATEGORIES, load_settings, material_category
from app.modules.material.usage.service import (
    USAGE_SELECT,
    backflush,
    close_out,
    decorate_usage,
    in_use_allocations,
    manual,
    reverse,
    work_order_lines,
)


router = APIRouter(prefix="/api/material-usage", tags=["Material Usage"])


class ManualIn(BaseModel):
    allocation_id: int = Field(gt=0)
    qty: float = Field(gt=0)
    remark: str = Field(min_length=1, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)


class ActorIn(BaseModel):
    actor: Optional[str] = Field(default=None, max_length=100)
    remark: Optional[str] = Field(default=None, max_length=500)


class ReverseIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
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


def _is_kg(row):
    return str(row.get("unit") or "").upper() == "KG"


@router.get("/lookups")
def lookups():
    def action(cur):
        cur.execute(
            """SELECT d.id, d.wo_no, d.status, d.planned_qty, d.actual_qty, d.reject_qty, p.product_name, e.equipment_code AS machine_code
               FROM production_plan_details d JOIN products p ON p.id = d.product_id LEFT JOIN equipment e ON e.id = d.machine_id
               WHERE EXISTS (SELECT 1 FROM material_allocations a WHERE a.work_order_id = d.id AND a.status <> 'CANCELLED')
                  OR EXISTS (SELECT 1 FROM material_usages u WHERE u.work_order_id = d.id)
               ORDER BY d.wo_no DESC""")
        work_orders = cur.fetchall()
        cur.execute("SELECT id, material_code, material_name, material_type, material_group, unit FROM materials WHERE status='ACTIVE' ORDER BY material_code")
        materials = [{**m, "category": material_category(m["material_type"], m["material_group"], m["material_code"])} for m in cur.fetchall()]
        cur.execute(
            """SELECT e.id, e.equipment_code, e.equipment_name FROM equipment e JOIN equipment_types t ON t.id = e.equipment_type_id
               WHERE t.category='MACHINE' AND e.asset_status='ACTIVE' ORDER BY e.equipment_code""")
        return {"work_orders": work_orders, "materials": materials, "machines": cur.fetchall(), "categories": CATEGORIES,
                "settings": load_settings(cur)}
    return _read(action)


def _usages(cur, start_dt, end_dt, work_order_id=None, material_id=None, machine_id=None, category=None):
    sql = f"{USAGE_SELECT} WHERE u.status='ACTIVE' AND u.used_at >= %s AND u.used_at < %s"
    params = [start_dt, end_dt]
    for column, value in (("u.work_order_id", work_order_id), ("u.material_id", material_id), ("u.machine_id", machine_id)):
        if value:
            sql += f" AND {column}=%s"
            params.append(value)
    cur.execute(sql + " ORDER BY u.used_at DESC, u.id DESC", tuple(params))
    rows = [decorate_usage(u) for u in cur.fetchall()]
    return [u for u in rows if not category or u["category"] == category]


def _filter_lines(lines, category, material_id, machine_id):
    return [l for l in lines if (not category or l["category"] == category) and (not material_id or l["material_id"] == material_id)
            and (not machine_id or l["machine_id"] == machine_id)]


@router.get("/summary")
def summary(date_from: date | None = None, date_to: date | None = None, work_order_id: int | None = None,
            category: str | None = None, material_id: int | None = None, machine_id: int | None = None):
    start = date_from or date.today()
    end = date_to or date.today()

    def action(cur):
        settings = load_settings(cur)
        tolerance = settings["material.usage_tolerance_pct"]
        start_dt = datetime.combine(start, datetime.min.time())
        end_dt = datetime.combine(end, datetime.min.time()) + timedelta(days=1)
        usages = _usages(cur, start_dt, end_dt, work_order_id, material_id, machine_id, category)
        # every Work Order with consumption in the period is compared in full (standard vs actual so far)
        wo_ids = sorted({u["work_order_id"] for u in usages})
        lines = _filter_lines(work_order_lines(cur, wo_ids, tolerance), category, material_id, machine_id)
        kg = [l for l in lines if _is_kg(l) and l["status"] != "PENDING"]
        standard = sum(l["standard_qty"] for l in kg)
        actual = sum(l["actual_qty"] for l in kg)
        over = [l for l in kg if l["diff_qty"] > 0]
        saving = [l for l in kg if l["diff_qty"] < 0]
        over_qty = sum(l["diff_qty"] for l in over)
        saving_qty = sum(l["diff_qty"] for l in saving)
        kpis = {
            "consumed": round(sum(u["qty"] for u in usages if _is_kg(u)), 3),
            "standard": round(standard, 3), "actual": round(actual, 3), "diff": round(actual - standard, 3),
            "over_pct": round((actual - standard) / standard * 100, 2) if standard else 0.0,
            "completed_wo": len({l["work_order_id"] for l in lines if l["wo_status"] == "COMPLETED"}),
            "work_orders": len({l["work_order_id"] for l in lines}),
        }
        variance = {
            "over_qty": round(over_qty, 3), "over_pct": round(over_qty / standard * 100, 2) if standard else 0.0,
            "over_wo": len({l["work_order_id"] for l in lines if l["status"] == "OVER"}),
            "saving_qty": round(saving_qty, 3), "saving_pct": round(saving_qty / standard * 100, 2) if standard else 0.0,
            "saving_wo": len({l["work_order_id"] for l in lines if l["status"] == "SAVING"}),
            "diff_qty": round(actual - standard, 3), "diff_pct": kpis["over_pct"],
        }

        # trend: at least 7 days ending at date_to; each WO x material counted on the day it was last consumed
        trend_start = min(start, end - timedelta(days=6))
        trend_usages = _usages(cur, datetime.combine(trend_start, datetime.min.time()), end_dt, work_order_id, material_id, machine_id, category)
        trend_lines = _filter_lines(work_order_lines(cur, sorted({u["work_order_id"] for u in trend_usages}), tolerance), category, material_id, machine_id)
        days = [trend_start + timedelta(days=i) for i in range((end - trend_start).days + 1)]
        by_day = {d: {"standard": 0.0, "actual": 0.0} for d in days}
        for l in trend_lines:
            if _is_kg(l) and l["last_used"] and l["last_used"].date() in by_day:
                by_day[l["last_used"].date()]["standard"] += l["standard_qty"]
                by_day[l["last_used"].date()]["actual"] += l["actual_qty"]
        trend = [{"date": d.isoformat(), "standard": round(v["standard"], 3), "actual": round(v["actual"], 3),
                  "diff": round(v["actual"] - v["standard"], 3)} for d, v in by_day.items()]

        by_category = defaultdict(float)
        by_material = {}
        for l in kg:
            by_category[l["category"]] += l["actual_qty"]
            m = by_material.setdefault(l["material_id"], {"material_id": l["material_id"], "material_code": l["material_code"],
                                                          "material_name": l["material_name"], "unit": l["unit"], "qty": 0.0})
            m["qty"] += l["actual_qty"]
        top = sorted(by_material.values(), key=lambda m: -m["qty"])[:6]
        for m in top:
            m["qty"] = round(m["qty"], 3)
            m["ratio"] = round(m["qty"] / actual * 100, 2) if actual else 0.0
        return {
            "kpis": kpis, "lines": lines, "trend": trend, "variance": variance, "top_materials": top,
            "by_category": {c: round(v, 3) for c, v in by_category.items()},
            "recent": attach_employee_names(cur, usages[:30], ["actor"]), "settings": settings,
        }
    return _read(action)


@router.get("/work-orders/{wo_id}")
def work_order_detail(wo_id: int):
    def action(cur):
        cur.execute(
            """SELECT d.id, d.wo_no, d.status, d.planned_qty, d.actual_qty, d.reject_qty, d.planned_start, d.planned_end,
                      p.product_code, p.product_name, e.equipment_code AS machine_code, po.order_no
               FROM production_plan_details d JOIN products p ON p.id = d.product_id LEFT JOIN equipment e ON e.id = d.machine_id
               LEFT JOIN production_orders po ON po.id = d.production_order_id WHERE d.id=%s""", (wo_id,))
        wo = cur.fetchone()
        if not wo:
            raise HTTPException(status_code=404, detail="Work order not found.")
        tolerance = load_settings(cur)["material.usage_tolerance_pct"]
        cur.execute(f"{USAGE_SELECT} WHERE u.work_order_id=%s ORDER BY u.used_at DESC, u.id DESC", (wo_id,))
        usages = [decorate_usage(u) for u in cur.fetchall()]
        return {**wo, "lines": work_order_lines(cur, [wo_id], tolerance), "usages": usages,
                "in_use": in_use_allocations(cur, wo_id), "tolerance_pct": tolerance}
    return _read(action)


@router.post("")
def record_manual(payload: ManualIn):
    return _run_write(lambda cur: {"message": "Usage recorded.", **manual(cur, payload.allocation_id, payload.qty, payload.actor, payload.remark.strip())})


@router.post("/work-orders/{wo_id}/backflush")
def run_backflush(wo_id: int, payload: ActorIn):
    return _run_write(lambda cur: backflush(cur, wo_id, payload.actor))


@router.post("/work-orders/{wo_id}/close-out")
def run_close_out(wo_id: int, payload: ActorIn):
    return _run_write(lambda cur: close_out(cur, wo_id, payload.actor, payload.remark))


@router.post("/{usage_id}/reverse")
def reverse_usage(usage_id: int, payload: ReverseIn):
    def action(cur):
        reverse(cur, usage_id, payload.reason.strip(), payload.actor)
        return {"message": "Usage reversed."}
    return _run_write(action)
