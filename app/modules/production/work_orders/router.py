from collections import Counter
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.database import get_vcc_plastics_connection
from app.modules.production.work_orders.service import (
    OPEN,
    PRIORITIES,
    STATUSES,
    WO_SELECT,
    cancel,
    change_priority,
    close,
    complete,
    decorate,
    get_wo,
    hold,
    load_shifts,
    release,
    release_check,
    resume,
    status_trend,
)


router = APIRouter(prefix="/api/work-orders", tags=["Work Order Management"])
APP_VERSION = "2.5.0"


class ActionIn(BaseModel):
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)
    remark: Optional[str] = Field(default=None, max_length=500)


class ReasonIn(BaseModel):
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=500)


class PriorityIn(ReasonIn):
    priority: str


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


@router.get("/lookups")
def lookups():
    def action(cur):
        cur.execute(
            """SELECT e.id, e.equipment_code, e.equipment_name, e.operational_status, e.factory_node_id FROM equipment e
               JOIN equipment_types t ON t.id = e.equipment_type_id
               WHERE t.category='MACHINE' AND e.asset_status='ACTIVE' ORDER BY e.equipment_code""")
        machines = cur.fetchall()
        area_ids = sorted({m["factory_node_id"] for m in machines if m["factory_node_id"]})
        areas = []
        if area_ids:
            cur.execute(f"SELECT id, code, name FROM factory_structure_nodes WHERE id IN ({','.join(['%s'] * len(area_ids))}) ORDER BY sort_order, name", tuple(area_ids))
            areas = cur.fetchall()
        shifts = [{k: s[k] for k in ("id", "shift_code", "shift_name")} for s in load_shifts(cur)]
        return {"machines": machines, "areas": areas, "shifts": shifts, "priorities": PRIORITIES, "statuses": STATUSES}
    return _read(action)


def _system_status(cur):
    try:
        cur.execute("SELECT NOW() AS now")
        db_time = cur.fetchone()["now"]
        db = "ONLINE"
    except Exception:  # noqa: BLE001 - status bar only
        db_time, db = None, "OFFLINE"
    # PLC / Robot / AGV / IoT Gateway are not integrated yet: reported honestly as not connected
    return {"MES": "ONLINE", "API": "ONLINE", "DATABASE": db, "PLC": "NOT_CONNECTED", "ROBOT": "NOT_CONNECTED",
            "AGV": "NOT_CONNECTED", "IOT_GATEWAY": "NOT_CONNECTED", "server_time": db_time or datetime.now(),
            "last_sync": datetime.now(), "version": APP_VERSION}


@router.get("/summary")
def summary(date_from: date | None = None, date_to: date | None = None, machine_id: int | None = None, priority: str | None = None,
            area_id: int | None = None, shift_id: int | None = None, search: str | None = None, production_order_id: int | None = None):
    start = date_from or date.today()
    end = date_to or date.today()

    def action(cur):
        now = datetime.now()
        start_dt = datetime.combine(start, datetime.min.time())
        end_dt = datetime.combine(end, datetime.min.time()) + timedelta(days=1)
        # scope: every open Work Order + the ones finished inside the period
        cur.execute(
            f"""{WO_SELECT}
                WHERE d.status IN ('UNSCHEDULED','SCHEDULED','RELEASED','IN_PRODUCTION','ON_HOLD')
                   OR (d.status IN ('COMPLETED','CLOSED','CANCELLED')
                       AND COALESCE(d.closed_at, d.completed_at, d.updated_at) >= %s AND COALESCE(d.closed_at, d.completed_at, d.updated_at) < %s)
                ORDER BY FIELD(d.priority,'URGENT','HIGH','MEDIUM','LOW'), d.planned_start IS NULL, d.planned_start, d.wo_no""",
            (start_dt, end_dt))
        rows = cur.fetchall()
        shifts = load_shifts(cur)
        items = [decorate(w, now, shifts) for w in rows]
        text = (search or "").strip().lower()
        items = [w for w in items
                 if (not machine_id or w["machine_id"] == machine_id) and (not priority or w["priority"] == priority)
                 and (not area_id or w["area_id"] == area_id) and (not shift_id or w["shift_id"] == shift_id)
                 and (not production_order_id or w["production_order_id"] == production_order_id)
                 and (not text or any(text in str(w.get(k) or "").lower() for k in ("wo_no", "order_no", "product_code", "product_name",
                                                                                      "machine_code", "mold_code", "customer_name")))]
        by_status = Counter(w["status"] for w in items)
        open_items = [w for w in items if w["status"] in OPEN]
        today = date.today()
        trend = status_trend(cur, 7, end if end <= today else today)
        last, prev = trend[-1], trend[-2] if len(trend) > 1 else trend[-1]
        queued = by_status["UNSCHEDULED"] + by_status["SCHEDULED"]
        completed_today = sum(1 for w in items if w["status"] in {"COMPLETED", "CLOSED"} and w["completed_at"] and w["completed_at"].date() == today)
        delayed = [w for w in items if w["delayed"]]
        total = len(items)

        def pct(n):
            return round(n / total * 100, 1) if total else 0.0

        def delta(*keys):
            return sum(last[k] for k in keys) - sum(prev[k] for k in keys)

        def spark(*keys):
            return [sum(t[k] for k in keys) for t in trend]

        kpis = {
            "total": {"value": total, "delta": delta(*STATUSES[:5]), "spark": spark(*STATUSES[:5])},
            "queued": {"value": queued, "pct": pct(queued), "created": by_status["UNSCHEDULED"], "delta": delta("UNSCHEDULED", "SCHEDULED"), "spark": spark("UNSCHEDULED", "SCHEDULED")},
            "released": {"value": by_status["RELEASED"], "pct": pct(by_status["RELEASED"]), "delta": delta("RELEASED"), "spark": spark("RELEASED")},
            "in_production": {"value": by_status["IN_PRODUCTION"], "pct": pct(by_status["IN_PRODUCTION"]), "delta": delta("IN_PRODUCTION"), "spark": spark("IN_PRODUCTION")},
            "on_hold": {"value": by_status["ON_HOLD"], "pct": pct(by_status["ON_HOLD"]), "delta": delta("ON_HOLD"), "spark": spark("ON_HOLD")},
            "completed_today": {"value": completed_today, "delta": delta("COMPLETED", "CLOSED"), "spark": spark("COMPLETED", "CLOSED")},
            "delayed": {"value": len(delayed), "pct": pct(len(delayed))},
        }
        machines = Counter(w["machine_code"] or "UNASSIGNED" for w in open_items)
        top = machines.most_common(5)
        others = sum(machines.values()) - sum(n for _, n in top)
        return {
            "items": items, "kpis": kpis, "by_status": {s: by_status[s] for s in STATUSES},
            "by_priority": {p: sum(1 for w in open_items if w["priority"] == p) for p in PRIORITIES},
            "by_machine": [[k, n] for k, n in top] + ([["OTHERS", others]] if others else []),
            "trend": trend, "delayed": sorted(delayed, key=lambda w: -w["delay_minutes"])[:10],
            "system": _system_status(cur),
        }
    return _read(action)


@router.get("/{wo_id}")
def detail(wo_id: int):
    def action(cur):
        row = get_wo(cur, wo_id)
        wo = decorate(row, datetime.now(), load_shifts(cur))
        cur.execute("""SELECT action, from_status, to_status, actor, acted_at, remark FROM production_planning_audit_log
                       WHERE entity_type='WORK_ORDER' AND entity_id=%s ORDER BY acted_at DESC, id DESC""", (wo_id,))
        history = cur.fetchall()
        cur.execute(
            """SELECT r.id, r.status, r.required_qty, r.open_qty, r.adjustment_qty, m.material_code, m.material_name, m.unit,
                      COALESCE((SELECT SUM(a.reserved_qty + a.issued_qty - a.returned_qty - a.consumed_qty) FROM material_allocations a
                                WHERE a.requirement_id = r.id AND a.status <> 'CANCELLED'), 0) AS allocated_qty
               FROM material_requirements r JOIN materials m ON m.id = r.material_id
               WHERE r.work_order_id=%s AND r.status <> 'CANCELLED' ORDER BY m.material_code""", (wo_id,))
        materials = [{**m, "required_qty": float(m["required_qty"]), "open_qty": float(m["open_qty"]),
                      "adjustment_qty": float(m["adjustment_qty"]), "allocated_qty": float(m["allocated_qty"])} for m in cur.fetchall()]
        return {**wo, "history": history, "materials": materials, "release_missing": release_check(wo)}
    return _read(action)


def _write(fn):
    def action(cur):
        fn(cur)
        return {"message": "OK"}
    return _run_write(action)


@router.post("/{wo_id}/release")
def release_wo(wo_id: int, payload: ActionIn):
    return _write(lambda cur: release(cur, wo_id, payload.actor, payload.version, payload.remark))


@router.post("/{wo_id}/hold")
def hold_wo(wo_id: int, payload: ReasonIn):
    return _write(lambda cur: hold(cur, wo_id, payload.reason.strip(), payload.actor, payload.version))


@router.post("/{wo_id}/resume")
def resume_wo(wo_id: int, payload: ActionIn):
    return _write(lambda cur: resume(cur, wo_id, payload.actor, payload.version, payload.remark))


@router.post("/{wo_id}/cancel")
def cancel_wo(wo_id: int, payload: ReasonIn):
    return _write(lambda cur: cancel(cur, wo_id, payload.reason.strip(), payload.actor, payload.version))


@router.post("/{wo_id}/priority")
def priority_wo(wo_id: int, payload: PriorityIn):
    return _write(lambda cur: change_priority(cur, wo_id, payload.priority.upper(), payload.reason.strip(), payload.actor, payload.version))


@router.post("/{wo_id}/complete")
def complete_wo(wo_id: int, payload: ReasonIn):
    return _write(lambda cur: complete(cur, wo_id, payload.reason.strip(), payload.actor, payload.version))


@router.post("/{wo_id}/close")
def close_wo(wo_id: int, payload: ActionIn):
    return _write(lambda cur: close(cur, wo_id, payload.actor, payload.version, payload.remark))


