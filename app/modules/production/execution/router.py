from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.database import get_vcc_plastics_connection
from app.core.employee_names import active_employees, attach_employee_names
from app.modules.notifications import events
from app.modules.production.execution import service as ex
from app.modules.production.work_orders import service as wo_service
from app.modules.production.work_orders.router import _system_status
from app.modules.production.work_orders.service import WO_SELECT, decorate, load_shifts


router = APIRouter(prefix="/api/production-execution", tags=["Production Execution"])


class StartIn(BaseModel):
    operator: str = Field(min_length=1, max_length=100)
    leader: Optional[str] = Field(default=None, max_length=100)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class OutputIn(BaseModel):
    good: int = Field(ge=0)
    reject: int = Field(default=0, ge=0)
    reason_code_id: Optional[int] = Field(default=None, gt=0)
    reason: Optional[str] = Field(default=None, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class RejectIn(BaseModel):
    qty: int = Field(gt=0)
    from_good: bool = False
    reason_code_id: Optional[int] = Field(default=None, gt=0)
    reason: str = Field(min_length=1, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class AdjustIn(BaseModel):
    good_total: int = Field(ge=0)
    reject_total: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class HoldIn(BaseModel):
    reason_code_id: Optional[int] = Field(default=None, gt=0)
    reason: str = Field(min_length=1, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class ActionIn(BaseModel):
    remark: Optional[str] = Field(default=None, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class NoteIn(BaseModel):
    text: str = Field(min_length=1, max_length=500)
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


@router.get("/lookups")
def lookups():
    def action(cur):
        cur.execute("""SELECT r.id, r.reason_code, r.reason_name, c.category_code FROM reason_codes r
                       LEFT JOIN reason_code_categories c ON c.id = r.category_id WHERE r.status='ACTIVE' ORDER BY r.sort_order, r.reason_code""")
        reasons = cur.fetchall()
        cur.execute(
            """SELECT e.id, e.equipment_code, e.factory_node_id FROM equipment e JOIN equipment_types t ON t.id = e.equipment_type_id
               WHERE t.category='MACHINE' AND e.asset_status='ACTIVE' ORDER BY e.equipment_code""")
        machines = cur.fetchall()
        shifts = [{k: s[k] for k in ("id", "shift_code", "shift_name")} for s in load_shifts(cur)]
        return {"reasons": reasons, "machines": machines, "shifts": shifts, "employees": active_employees(cur)}
    return _read(action)


def _day_totals(cur, day_from, day_to):
    cur.execute("""SELECT DATE(event_at) AS d, SUM(good_qty) AS good, SUM(reject_qty) AS reject,
                          AVG(CASE WHEN event_type='OUTPUT' THEN cycle_time_sec END) AS cycle
                   FROM production_execution_logs WHERE event_at >= %s AND event_at < %s
                     AND event_type IN ('OUTPUT','REJECT','QTY_ADJUST') GROUP BY DATE(event_at)""",
                (datetime.combine(day_from, datetime.min.time()), datetime.combine(day_to, datetime.min.time()) + timedelta(days=1)))
    return {r["d"]: {"good": int(r["good"] or 0), "reject": int(r["reject"] or 0), "cycle": float(r["cycle"]) if r["cycle"] else None} for r in cur.fetchall()}


@router.get("/summary")
def summary(business_date: date | None = None, machine_id: int | None = None, shift_id: int | None = None, search: str | None = None):
    day = business_date or date.today()

    def action(cur):
        now = datetime.now()
        day_start = datetime.combine(day, datetime.min.time())
        day_end = day_start + timedelta(days=1)
        cur.execute(
            f"""{WO_SELECT}
                WHERE d.status IN ('RELEASED','IN_PRODUCTION','ON_HOLD')
                   OR (d.status IN ('COMPLETED','CLOSED') AND d.completed_at >= %s AND d.completed_at < %s)
                ORDER BY FIELD(d.status,'IN_PRODUCTION','ON_HOLD','RELEASED','COMPLETED','CLOSED'), FIELD(d.priority,'URGENT','HIGH','MEDIUM','LOW'), d.planned_start""",
            (day_start, day_end))
        rows = cur.fetchall()
        shifts = load_shifts(cur)
        text = (search or "").strip().lower()
        items = []
        for row in rows:
            w = decorate(row, now, shifts)
            if (machine_id and w["machine_id"] != machine_id) or (shift_id and w["shift_id"] != shift_id):
                continue
            if text and not any(text in str(w.get(k) or "").lower() for k in ("wo_no", "order_no", "product_name", "product_code", "machine_code", "mold_code")):
                continue
            w["metrics"] = ex.compute_metrics(w, ex.logs_of(cur, w["id"]), now) if w["started_at"] else ex.compute_metrics(w, [], now)
            w["readiness"] = ex.readiness(cur, row) if w["status"] == "RELEASED" else []
            items.append(w)
        running = [w for w in items if w["status"] in {"IN_PRODUCTION", "ON_HOLD"}]
        today_items = [w for w in items if w["status"] != "RELEASED"]
        totals = _day_totals(cur, day - timedelta(days=6), day)
        t, y = totals.get(day, {"good": 0, "reject": 0, "cycle": None}), totals.get(day - timedelta(days=1), {"good": 0, "reject": 0, "cycle": None})

        def yld(v):
            return round(v["good"] / (v["good"] + v["reject"]) * 100, 2) if v["good"] + v["reject"] else None

        oees = [w["metrics"]["oee_pct"] for w in today_items if w["metrics"]["oee_pct"] is not None]
        cycles = [w["metrics"]["avg_cycle_sec"] for w in running if w["metrics"]["avg_cycle_sec"]]
        kpis = {
            "active": len([w for w in running if w["status"] == "IN_PRODUCTION"]), "on_hold": len([w for w in running if w["status"] == "ON_HOLD"]),
            "ready": len([w for w in items if w["status"] == "RELEASED"]),
            "planned_qty": sum(int(w["planned_qty"]) for w in today_items),
            "good_today": t["good"], "good_yesterday": y["good"], "reject_today": t["reject"], "reject_yesterday": y["reject"],
            "yield_today": yld(t), "yield_yesterday": yld(y),
            "oee": round(sum(oees) / len(oees), 1) if oees else None,
            "avg_cycle": round(sum(cycles) / len(cycles), 1) if cycles else None,
            "ideal_cycle": round(sum(float(w["cycle_time_sec"]) for w in running) / len(running), 1) if running else None,
        }
        days = [day - timedelta(days=6 - i) for i in range(7)]
        cur.execute("""SELECT DATE(planned_start) AS d, SUM(planned_qty) AS qty FROM production_plan_details
                       WHERE planned_start >= %s AND planned_start < %s AND status <> 'CANCELLED' GROUP BY DATE(planned_start)""",
                    (datetime.combine(days[0], datetime.min.time()), day_end))
        planned = {r["d"]: int(r["qty"] or 0) for r in cur.fetchall()}
        trend = [{"date": d.isoformat(), "planned": planned.get(d, 0), "good": totals.get(d, {}).get("good", 0),
                  "reject": totals.get(d, {}).get("reject", 0), "cycle": totals.get(d, {}).get("cycle")} for d in days]
        # stop reasons: holds of the last 7 days + what keeps released work orders from starting
        cur.execute("""SELECT COALESCE(r.reason_name, l.reason_text) AS reason, COUNT(*) AS n FROM production_execution_logs l
                       LEFT JOIN reason_codes r ON r.id = l.reason_code_id
                       WHERE l.event_type='HOLD' AND l.event_at >= %s GROUP BY COALESCE(r.reason_name, l.reason_text)""",
                    (datetime.combine(days[0], datetime.min.time()),))
        reasons = Counter({row["reason"] or "—": int(row["n"]) for row in cur.fetchall()})
        for w in items:
            for r in w["readiness"]:
                reasons[r.split(":")[0]] += 1
            if w["status"] == "ON_HOLD" and w["hold_reason"] and not reasons.get(w["hold_reason"]):
                reasons[w["hold_reason"]] += 1
        machines = defaultdict(list)
        for w in today_items:
            if w["machine_code"] and w["metrics"]["availability_pct"] is not None:
                machines[w["machine_code"]].append(w["metrics"]["availability_pct"])
        attach_employee_names(cur, items, ["operator_code", "leader_code"])
        return {
            "items": items, "kpis": kpis, "trend": trend,
            "by_status": {s: sum(1 for w in items if w["status"] == s) for s in ("IN_PRODUCTION", "ON_HOLD", "RELEASED", "COMPLETED", "CLOSED")},
            "top_reasons": reasons.most_common(6),
            "machine_utilization": sorted([[k, round(sum(v) / len(v), 1)] for k, v in machines.items()]),
            "system": _system_status(cur),
        }
    return _read(action)


@router.get("/{wo_id}")
def detail(wo_id: int):
    def action(cur):
        row = wo_service.get_wo(cur, wo_id)
        now = datetime.now()
        w = decorate(row, now, load_shifts(cur))
        logs = ex.logs_of(cur, wo_id)
        attach_employee_names(cur, [w], ["operator_code", "leader_code"])
        attach_employee_names(cur, logs, ["actor"])
        return {**w, "metrics": ex.compute_metrics(w, logs, now), "readiness": ex.readiness(cur, row) if w["status"] == "RELEASED" else [],
                "logs": list(reversed(logs))}
    return _read(action)


def _write(fn):
    def action(cur):
        result = fn(cur)
        return {"message": "OK", **(result or {})}
    return _run_write(action)


@router.post("/{wo_id}/start")
def start_wo(wo_id: int, p: StartIn):
    result = _write(lambda cur: ex.start(cur, wo_id, p.operator.strip(), (p.leader or "").strip() or None, p.actor, p.version))
    events.wo_started(wo_id, p.actor, p.operator.strip())
    return result


@router.post("/{wo_id}/output")
def output_wo(wo_id: int, p: OutputIn):
    result = _write(lambda cur: ex.output(cur, wo_id, p.good, p.reject, p.reason_code_id, (p.reason or "").strip() or None, p.actor, p.version))
    events.ng_reported(wo_id, p.reject, (p.reason or "").strip() or None, p.actor)
    return result


@router.post("/{wo_id}/reject")
def reject_wo(wo_id: int, p: RejectIn):
    result = _write(lambda cur: ex.reject(cur, wo_id, p.qty, p.from_good, p.reason_code_id, p.reason.strip(), p.actor, p.version))
    events.ng_reported(wo_id, p.qty, p.reason.strip(), p.actor)
    return result


@router.post("/{wo_id}/adjust")
def adjust_wo(wo_id: int, p: AdjustIn):
    return _write(lambda cur: ex.adjust(cur, wo_id, p.good_total, p.reject_total, p.reason.strip(), p.actor, p.version))


@router.post("/{wo_id}/hold")
def hold_wo(wo_id: int, p: HoldIn):
    result = _write(lambda cur: ex.hold(cur, wo_id, p.reason_code_id, p.reason.strip(), p.actor, p.version))
    events.wo_on_hold(wo_id, p.reason.strip(), p.actor)
    return result


@router.post("/{wo_id}/resume")
def resume_wo(wo_id: int, p: ActionIn):
    return _write(lambda cur: ex.resume(cur, wo_id, p.actor, p.version, p.remark))


@router.post("/{wo_id}/end")
def end_wo(wo_id: int, p: ActionIn):
    result = _write(lambda cur: ex.end(cur, wo_id, p.remark, p.actor, p.version))
    events.wo_completed(wo_id, p.actor)
    return result


@router.post("/{wo_id}/notes")
def add_note(wo_id: int, p: NoteIn):
    return _write(lambda cur: ex.note(cur, wo_id, p.text.strip(), p.actor))
