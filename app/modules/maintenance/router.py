"""Maintenance Management API (screens 6.1 Overview, 6.2 Requests, 6.3 Planning, 6.4 Machine & Equipment Maintenance,
6.5 Mold Maintenance, 6.6 Production Tool Maintenance, 6.7 Maintenance History)."""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.database import get_vcc_plastics_connection
from app.modules.maintenance import service as ms
from app.modules.notifications import events
from app.modules.mold import service as mold_service
from app.modules.production.work_orders.router import _system_status

router = APIRouter(prefix="/api/maintenance", tags=["Maintenance Management"])


def _read(action):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        return action(cur)
    finally:
        cur.close(); conn.close()


def _write(action):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        result = action(cur)
        conn.commit()
        return result if result is not None else {"message": "OK"}
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cur.close(); conn.close()


# ---------------------------------------------------------------- payloads

class RequestIn(BaseModel):
    asset_type: str = Field(min_length=1, max_length=20)
    asset_id: int = Field(gt=0)
    request_type: str = Field(default="CORRECTIVE", max_length=20)
    priority: str = Field(default="MEDIUM", max_length=10)
    short_description: str = Field(min_length=1, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    department: Optional[str] = Field(default=None, max_length=100)
    required_date: Optional[datetime] = None
    source: Optional[str] = Field(default="MANUAL", max_length=20)
    alarm_id: Optional[int] = None
    downtime_event_id: Optional[int] = None
    production_wo_id: Optional[int] = None
    version: Optional[int] = None
    actor: Optional[str] = Field(default=None, max_length=100)


class ActionIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=1000)
    technician: Optional[str] = Field(default=None, max_length=100)
    stage: Optional[str] = Field(default=None, max_length=30)
    version: Optional[int] = None
    actor: Optional[str] = Field(default=None, max_length=100)


class WoCreateIn(BaseModel):
    asset_type: Optional[str] = Field(default=None, max_length=20)
    asset_id: Optional[int] = Field(default=None, gt=0)
    wo_type: Optional[str] = Field(default=None, max_length=20)
    priority: Optional[str] = Field(default=None, max_length=10)
    task_name: Optional[str] = Field(default=None, max_length=255)
    description: Optional[str] = Field(default=None, max_length=5000)
    technician: Optional[str] = Field(default=None, max_length=100)
    assistant: Optional[str] = Field(default=None, max_length=100)
    planned_start: Optional[datetime] = None
    planned_finish: Optional[datetime] = None
    due_date: Optional[date] = None
    est_minutes: Optional[int] = Field(default=None, ge=0)
    est_downtime_minutes: Optional[int] = Field(default=None, ge=0)
    outsourcing_cost: Optional[float] = Field(default=0, ge=0)
    tool_cost: Optional[float] = Field(default=0, ge=0)
    other_cost: Optional[float] = Field(default=0, ge=0)
    result: Optional[str] = Field(default=None, max_length=1000)
    root_cause: Optional[str] = Field(default=None, max_length=1000)
    calibration_result: Optional[str] = Field(default=None, max_length=10)
    calibration_value: Optional[str] = Field(default=None, max_length=255)
    checklist: Optional[list[str]] = None
    version: Optional[int] = None
    actor: Optional[str] = Field(default=None, max_length=100)


class PlanIn(BaseModel):
    asset_type: str = Field(min_length=1, max_length=20)
    asset_id: int = Field(gt=0)
    task_name: str = Field(min_length=1, max_length=255)
    wo_type: str = Field(default="PM", max_length=20)
    plan_type: Optional[str] = Field(default=None, max_length=20)
    frequency_unit: str = Field(default="DAY", max_length=10)
    interval_value: int = Field(gt=0)
    next_due_date: Optional[date] = None
    next_due_shot: Optional[int] = Field(default=None, ge=0)
    priority: str = Field(default="MEDIUM", max_length=10)
    technician: Optional[str] = Field(default=None, max_length=100)
    est_minutes: Optional[int] = Field(default=None, ge=0)
    description: Optional[str] = Field(default=None, max_length=5000)
    checklist: Optional[list[str]] = None
    spare_parts: Optional[list[dict[str, Any]]] = None
    version: Optional[int] = None
    actor: Optional[str] = Field(default=None, max_length=100)


class ChecklistIn(BaseModel):
    result: Optional[str] = Field(default=None, max_length=10)
    note: Optional[str] = Field(default=None, max_length=500)
    item: Optional[str] = Field(default=None, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)


class PartIn(BaseModel):
    part_code: Optional[str] = Field(default=None, max_length=100)
    part_name: str = Field(min_length=1, max_length=255)
    qty: float = Field(gt=0)
    unit: Optional[str] = Field(default=None, max_length=20)
    unit_cost: float = Field(default=0, ge=0)
    actor: Optional[str] = Field(default=None, max_length=100)


class LaborIn(BaseModel):
    technician: str = Field(min_length=1, max_length=100)
    labor_type: str = Field(default="REPAIR", max_length=30)
    hours: float = Field(gt=0, le=1000)
    rate: Optional[float] = Field(default=None, ge=0)
    actor: Optional[str] = Field(default=None, max_length=100)


class ComponentIn(BaseModel):
    condition_code: str = Field(min_length=1, max_length=20)
    action_code: str = Field(default="NONE", max_length=20)
    note: Optional[str] = Field(default=None, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)


class AttachmentIn(BaseModel):
    kind: str = Field(default="FILE", max_length=20)
    file_url: str = Field(min_length=1, max_length=1000)
    file_name: Optional[str] = Field(default=None, max_length=255)
    file_size: Optional[int] = Field(default=None, ge=0)
    actor: Optional[str] = Field(default=None, max_length=100)


# ---------------------------------------------------------------- helpers

def _sync():
    """AUTO plans follow Mold Master / Production Tool settings: refresh them before reading plans."""
    _write(lambda cur: ms.sync_auto_plans(cur))


def _month_start(d):
    return d.replace(day=1)


def _prev_month(d):
    first = d.replace(day=1)
    last_prev = first - timedelta(days=1)
    return last_prev.replace(day=1), first


def _dt(d):
    return datetime.combine(d, datetime.min.time())


def _attachments(cur, owner_type, owner_id):
    cur.execute("SELECT * FROM maintenance_attachments WHERE owner_type=%s AND owner_id=%s ORDER BY uploaded_at", (owner_type, owner_id))
    return cur.fetchall()


def _requests(cur):
    cur.execute("SELECT * FROM maintenance_requests ORDER BY created_at DESC, id DESC")
    rows = cur.fetchall()
    assets = ms.asset_map(cur, [(r["asset_type"], r["asset_id"]) for r in rows])
    wo_ids = [r["work_order_id"] for r in rows if r["work_order_id"]]
    wos = {}
    if wo_ids:
        cur.execute(f"SELECT id, wo_no, status FROM maintenance_work_orders WHERE id IN ({','.join(['%s'] * len(wo_ids))})", tuple(wo_ids))
        wos = {w["id"]: w for w in cur.fetchall()}
    for r in rows:
        a = assets.get(ms.asset_key(r["asset_type"], r["asset_id"])) or {}
        r["asset_code"], r["asset_name"], r["asset_sub"], r["asset_location"], r["asset_image"] = a.get("code"), a.get("name"), a.get("sub"), a.get("location"), a.get("image_url")
        w = wos.get(r["work_order_id"])
        r["wo_no"], r["wo_status"] = (w["wo_no"], w["status"]) if w else (None, None)
    return rows


def _weeks(n=6):
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return [(monday - timedelta(weeks=i), monday - timedelta(weeks=i) + timedelta(days=6)) for i in range(n - 1, -1, -1)]


# ---------------------------------------------------------------- lookups / overview

@router.get("/lookups")
def lookups():
    def action(cur):
        return {"assets": ms.all_assets(cur), "technicians": ms.technicians(cur), "asset_types": ms.ASSET_TYPES, "request_types": ms.REQUEST_TYPES,
                "priorities": ms.PRIORITIES, "request_statuses": ms.REQUEST_STATUSES, "sources": ms.SOURCES, "wo_types": ms.WO_TYPES, "wo_statuses": ms.WO_STATUSES,
                "plan_types": ms.PLAN_TYPES, "stages": ms.STAGES, "components": ms.MOLD_COMPONENTS, "component_conditions": ms.COMPONENT_CONDITIONS,
                "component_actions": ms.COMPONENT_ACTIONS, "labor_types": ms.LABOR_TYPES, "labor_rate": ms.labor_rate(cur),
                "currency": ms._setting(cur, "maintenance.currency", "VND")}
    return _read(action)


@router.get("/overview")
def overview():
    _sync()

    def action(cur):
        from app.modules.machine.monitoring_router import machine_oee
        today = date.today()
        now = datetime.now()
        ms_start = _month_start(today)
        pm_start, pm_end = _prev_month(today)
        wos = ms.load_wos(cur)
        mtd = [w for w in wos if w["created_at"] >= _dt(ms_start)]
        prev = [w for w in wos if _dt(pm_start) <= w["created_at"] < _dt(pm_end)]
        done_mtd = [w for w in wos if w["completed_at"] and w["completed_at"] >= _dt(ms_start)]
        done_prev = [w for w in wos if w["completed_at"] and _dt(pm_start) <= w["completed_at"] < _dt(pm_end)]
        cost = sum(w["total_cost"] for w in done_mtd)
        cost_prev = sum(w["total_cost"] for w in done_prev)
        mtbf, fails = ms.mtbf(cur, wos, _dt(ms_start), now)
        mtbf_prev, _ = ms.mtbf(cur, wos, _dt(pm_start), _dt(pm_end))
        cur.execute("SELECT e.id, e.operational_status, t.category FROM equipment e JOIN equipment_types t ON t.id=e.equipment_type_id WHERE e.asset_status='ACTIVE'")
        eq = cur.fetchall()
        oees = [machine_oee(cur, e["id"], _dt(today), now, e["operational_status"])["oee"] for e in eq if e["category"] == "MACHINE"]
        oees = [v for v in oees if v is not None]
        kpis = {"oee": round(sum(oees) / len(oees), 1) if oees else None,
                "pm_compliance": ms.pm_compliance(wos, ms_start, today), "pm_compliance_prev": ms.pm_compliance(wos, pm_start, pm_end - timedelta(days=1)),
                "cost": round(cost, 0), "cost_prev": round(cost_prev, 0),
                "breakdown": sum(1 for w in mtd if w["wo_type"] in ms.REPAIR_TYPES), "breakdown_prev": sum(1 for w in prev if w["wo_type"] in ms.REPAIR_TYPES),
                "mttr": ms.mttr(done_mtd), "mttr_prev": ms.mttr(done_prev), "mtbf": mtbf, "mtbf_prev": mtbf_prev, "failures": fails,
                "currency": ms._setting(cur, "maintenance.currency", "VND")}
        status_counts = Counter(e["operational_status"] or "OFFLINE" for e in eq)
        mold_counts = Counter(m["status"] for m in mold_service.load_molds(cur))
        cur.execute("SELECT status, COUNT(*) AS n FROM production_tools GROUP BY status")
        tool_counts = {r["status"]: int(r["n"]) for r in cur.fetchall()}
        today_dt = _dt(today)
        work = {"planned_pm": sum(1 for w in wos if w["wo_type"] in ms.PREVENTIVE_TYPES and w["status"] != "CANCELLED" and (w["due_date"] == today or (w["planned_start"] and w["planned_start"].date() == today))),
                "preventive": sum(1 for w in wos if w["wo_type"] in ms.PREVENTIVE_TYPES and w["status"] in ms.OPEN_WO),
                "corrective": sum(1 for w in wos if w["wo_type"] in ms.REPAIR_TYPES and w["status"] in ms.OPEN_WO),
                "in_progress": sum(1 for w in wos if w["status"] in ("IN_PROGRESS", "PAUSED", "VERIFICATION")),
                "completed": sum(1 for w in wos if w["completed_at"] and w["completed_at"] >= today_dt),
                "pending": sum(1 for w in wos if w["status"] in ("RELEASED", "ASSIGNED"))}
        overdue = sorted([w for w in wos if w["overdue"]], key=lambda w: (ms.PRIORITIES.index(w["priority"]) if w["priority"] in ms.PRIORITIES else 9, -w["overdue_days"]))[:8]
        trend = []
        for a, b in _weeks(6):
            trend.append({"week": f"W{a.isocalendar()[1]:02d}", "from": a.isoformat(), "to": b.isoformat(),
                          "planned_pm": sum(1 for w in wos if w["wo_type"] in ms.PREVENTIVE_TYPES and w["status"] != "CANCELLED" and w["due_date"] and a <= w["due_date"] <= b),
                          "corrective": sum(1 for w in wos if w["wo_type"] in ms.REPAIR_TYPES and a <= w["created_at"].date() <= b),
                          "total": sum(1 for w in wos if w["status"] != "CANCELLED" and a <= w["created_at"].date() <= b)})
        cur.execute("SELECT * FROM maintenance_plans WHERE status='ACTIVE' AND next_due_date IS NOT NULL AND next_due_date <= %s ORDER BY next_due_date", (today + timedelta(days=7),))
        plans = cur.fetchall()
        passets = ms.asset_map(cur, [(p["asset_type"], p["asset_id"]) for p in plans])
        open_by_plan = {w["plan_id"]: w for w in wos if w["plan_id"] and w["status"] in ms.OPEN_WO}
        upcoming = []
        for p in plans:
            a = passets.get(ms.asset_key(p["asset_type"], p["asset_id"])) or {}
            w = open_by_plan.get(p["id"])
            upcoming.append({"date": p["next_due_date"], "plan_id": p["id"], "plan_no": p["plan_no"], "wo_id": w["id"] if w else None, "wo_no": w["wo_no"] if w else None,
                             "asset_type": p["asset_type"], "asset_id": p["asset_id"], "asset_code": a.get("code"), "type": p["wo_type"], "task_name": p["task_name"],
                             "status": ms.plan_status(p, w)})
        alerts = []
        cur.execute("""SELECT a.id, a.alarm_no, a.message, a.priority, a.raised_at, e.id AS equipment_id, e.equipment_code, a.parameter_code FROM machine_alarms a
                       JOIN equipment e ON e.id=a.equipment_id WHERE a.status IN ('ACTIVE','ACKNOWLEDGED') ORDER BY FIELD(a.priority,'CRITICAL','HIGH','MEDIUM','LOW'), a.raised_at DESC LIMIT 8""")
        for a in cur.fetchall():
            alerts.append({"asset_type": "MACHINE", "asset_id": a["equipment_id"], "asset_code": a["equipment_code"], "kind": a["parameter_code"] or "ALARM",
                           "message": a["message"], "time": a["raised_at"], "priority": "HIGH" if a["priority"] in ("CRITICAL", "HIGH") else "MEDIUM"})
        for m in mold_service.load_molds(cur):
            for al in m["alerts"]:
                if al["code"] in ("PM_OVERDUE", "LIFE_90", "OVER_LIFE", "REPAIR_LONG") and m["status"] not in mold_service.OUT_OF_SERVICE:
                    alerts.append({"asset_type": "MOLD", "asset_id": m["id"], "asset_code": m["mold_code"], "kind": al["code"], "message": al["code"], "time": None,
                                   "priority": "HIGH" if al["level"] == "CRITICAL" else "MEDIUM"})
        cur.execute("SELECT id, tool_code, calibration_due_date FROM production_tools WHERE calibration_required=1 AND calibration_due_date < CURDATE() AND status<>'INACTIVE'")
        for t in cur.fetchall():
            alerts.append({"asset_type": "TOOL", "asset_id": t["id"], "asset_code": t["tool_code"], "kind": "CALIBRATION_OVERDUE", "message": "CALIBRATION_OVERDUE",
                           "time": t["calibration_due_date"], "priority": "MEDIUM"})
        return {"kpis": kpis, "equipment_status": dict(status_counts), "mold_status": dict(mold_counts), "tool_status": tool_counts, "work": work,
                "overdue": overdue, "trend": trend, "upcoming": upcoming[:10], "cost_breakdown": ms.cost_breakdown(done_mtd), "alerts": alerts[:12],
                "system": _system_status(cur)}
    return _read(action)


# ---------------------------------------------------------------- requests

@router.get("/requests")
def list_requests():
    def action(cur):
        rows = _requests(cur)
        today = date.today()
        st = Counter(r["status"] for r in rows)
        created_today = sum(1 for r in rows if r["created_at"].date() == today)
        return {"items": rows, "kpis": {"total": len(rows), "today": created_today, **{s.lower(): st[s] for s in ms.REQUEST_STATUSES}},
                "by_type": dict(Counter(r["request_type"] for r in rows)), "by_priority": dict(Counter(r["priority"] for r in rows)),
                "by_source": dict(Counter(r["source"] for r in rows)), "system": _system_status(cur)}
    return _read(action)


@router.get("/requests/{request_id}")
def request_detail(request_id: int):
    def action(cur):
        ms.get_request(cur, request_id)
        row = next(r for r in _requests(cur) if r["id"] == request_id)
        return {**row, "events": ms.events_of(cur, "REQ", request_id), "attachments": _attachments(cur, "REQ", request_id)}
    return _read(action)


@router.post("/requests")
def create_request(p: RequestIn):
    def action(cur):
        rid = ms.create_request(cur, p.model_dump(), p.actor, source=(p.source or "MANUAL").upper())
        return {"message": "Request created.", "id": rid}
    result = _write(action)
    events.maint_requested(result["id"], p.actor)
    return result


@router.put("/requests/{request_id}")
def update_request(request_id: int, p: RequestIn):
    return _write(lambda cur: (ms.update_request(cur, request_id, p.model_dump(), p.actor), {"message": "Request updated."})[1])


@router.post("/{owner}/{owner_id}/attachments")
def attachment_add(owner: str, owner_id: int, p: AttachmentIn):
    def action(cur):
        owner_type = {"requests": "REQ", "work-orders": "WO"}.get(owner)
        if not owner_type:
            raise HTTPException(status_code=404, detail="Not found.")
        if not p.file_url.startswith("/uploads/"):
            raise HTTPException(status_code=400, detail="Upload the file first.")
        (ms.get_request if owner_type == "REQ" else ms.get_wo)(cur, owner_id)
        cur.execute("INSERT INTO maintenance_attachments (owner_type, owner_id, kind, file_url, file_name, file_size, uploaded_by) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (owner_type, owner_id, p.kind.upper(), p.file_url, p.file_name, p.file_size, p.actor))
        ms.log(cur, owner_type, owner_id, "ATTACH", p.actor, note=p.file_name)
        return {"message": "Attachment added.", "id": cur.lastrowid}
    return _write(action)


@router.delete("/{owner}/{owner_id}/attachments/{att_id}")
def attachment_delete(owner: str, owner_id: int, att_id: int):
    def action(cur):
        owner_type = {"requests": "REQ", "work-orders": "WO"}.get(owner)
        cur.execute("DELETE FROM maintenance_attachments WHERE id=%s AND owner_type=%s AND owner_id=%s", (att_id, owner_type, owner_id))
        if not cur.rowcount:
            raise HTTPException(status_code=404, detail="Attachment not found.")
        return {"message": "Deleted."}
    return _write(action)


@router.post("/requests/{request_id}/work-order")
def request_work_order(request_id: int, p: WoCreateIn):
    result = _write(lambda cur: {"message": "Work order created.", "id": ms.wo_from_request(cur, request_id, p.model_dump(), p.actor)})
    events.maint_wo_created(result["id"], p.actor)
    return result


@router.post("/requests/{request_id}/{req_action}")
def request_action(request_id: int, req_action: str, p: ActionIn):
    return _write(lambda cur: (ms.request_action(cur, request_id, req_action, p.actor, p.note, p.version), {"message": "Request updated."})[1])


# ---------------------------------------------------------------- plans

@router.get("/plans")
def list_plans():
    _sync()

    def action(cur):
        today = date.today()
        cur.execute("SELECT * FROM maintenance_plans ORDER BY status='ACTIVE' DESC, next_due_date IS NULL, next_due_date, plan_no")
        plans = cur.fetchall()
        assets = ms.asset_map(cur, [(p["asset_type"], p["asset_id"]) for p in plans])
        wos = ms.load_wos(cur, "w.plan_id IS NOT NULL")
        open_by_plan, done_by_plan = {}, defaultdict(list)
        for w in wos:
            if w["status"] in ms.OPEN_WO:
                open_by_plan.setdefault(w["plan_id"], w)
            elif w["status"] in ms.DONE_WO:
                done_by_plan[w["plan_id"]].append(w)
        cur.execute("SELECT id, current_shot FROM molds")
        shots = {r["id"]: int(r["current_shot"] or 0) for r in cur.fetchall()}
        for p in plans:
            a = assets.get(ms.asset_key(p["asset_type"], p["asset_id"])) or {}
            p["asset_code"], p["asset_name"], p["asset_image"], p["asset_sub"], p["asset_location"] = a.get("code"), a.get("name"), a.get("image_url"), a.get("sub"), a.get("location")
            w = open_by_plan.get(p["id"])
            p["open_wo_id"], p["open_wo_no"], p["open_wo_status"], p["open_wo_technician"] = (w["id"], w["wo_no"], w["status"], w["technician"]) if w else (None, None, None, None)
            p["plan_status"] = ms.plan_status(p, w, today)
            p["days_left"] = (p["next_due_date"] - today).days if p["next_due_date"] else None
            p["shots_left"] = (int(p["next_due_shot"]) - shots.get(p["asset_id"], 0)) if p["frequency_unit"] == "SHOT" and p["next_due_shot"] else None
            p["done_count"] = len(done_by_plan[p["id"]])
            for k in ("checklist", "spare_parts"):
                if isinstance(p.get(k), str):
                    import json
                    p[k] = json.loads(p[k])
        active = [p for p in plans if p["status"] == "ACTIVE"]
        st = Counter(p["plan_status"] for p in active)
        d30 = datetime.now() - timedelta(days=30)
        completed_30 = sum(1 for w in wos if w["completed_at"] and w["completed_at"] >= d30)
        all_wos = ms.load_wos(cur)
        overdue_plans = [p for p in active if p["plan_status"] == "OVERDUE"]
        comp = ms.pm_compliance(all_wos, _month_start(today), today)
        trend = []
        for a, b in _weeks(6):
            due = [w for w in all_wos if w["wo_type"] in ms.PREVENTIVE_TYPES and w["status"] != "CANCELLED" and w["due_date"] and a <= w["due_date"] <= b]
            trend.append({"week": f"W{a.isocalendar()[1]:02d}", "from": a.isoformat(), "to": b.isoformat(), "planned": len(due),
                          "completed": sum(1 for w in due if w["status"] in ms.DONE_WO | {"VERIFICATION"}),
                          "overdue": sum(1 for w in due if w["status"] in ms.OPEN_WO and w["due_date"] < today)})
        upcoming = []
        for i in range(7):
            d = today + timedelta(days=i)
            upcoming.append({"date": d.isoformat(), "count": sum(1 for p in active if p["next_due_date"] == d)})
        return {"items": plans, "kpis": {"total": len(active), "completed_30d": completed_30, "in_progress": st["IN_PROGRESS"] + st["SCHEDULED"],
                                         "upcoming": st["UPCOMING"], "overdue": st["OVERDUE"], "pm_compliance": comp},
                "plan_type_counts": dict(Counter(p["plan_type"] for p in active)), "by_asset_type": dict(Counter(p["asset_type"] for p in active)),
                "overdue_by_priority": dict(Counter(p["priority"] for p in overdue_plans)), "trend": trend, "upcoming": upcoming, "system": _system_status(cur)}
    return _read(action)


@router.get("/plans/{plan_id}")
def plan_detail(plan_id: int):
    def action(cur):
        p = ms.get_plan(cur, plan_id)
        a = ms.asset_map(cur, [(p["asset_type"], p["asset_id"])]).get(ms.asset_key(p["asset_type"], p["asset_id"])) or {}
        wos = ms.load_wos(cur, "w.plan_id=%s", (plan_id,))
        open_wo = next((w for w in wos if w["status"] in ms.OPEN_WO), None)
        return {**p, "asset": a, "work_orders": wos[:20], "plan_status": ms.plan_status(p, open_wo), "events": ms.events_of(cur, "PLAN", plan_id)}
    return _read(action)


@router.post("/plans")
def create_plan(p: PlanIn):
    return _write(lambda cur: {"message": "Plan created.", "id": ms.save_plan(cur, p.model_dump(), p.actor)})


@router.put("/plans/{plan_id}")
def update_plan(plan_id: int, p: PlanIn):
    def action(cur):
        plan = ms.get_plan(cur, plan_id)
        if plan["source"] != "MANUAL":
            raise HTTPException(status_code=409, detail="Automatic plans follow Mold Master / Production Tool settings and cannot be edited here.")
        ms.save_plan(cur, p.model_dump(), p.actor, plan_id)
        return {"message": "Plan updated."}
    return _write(action)


@router.post("/plans/{plan_id}/status")
def plan_status(plan_id: int, p: ActionIn):
    def action(cur):
        plan = ms.get_plan(cur, plan_id, True)
        to = (p.note or "").upper()
        if to not in ("ACTIVE", "INACTIVE"):
            raise HTTPException(status_code=400, detail="Status must be ACTIVE or INACTIVE.")
        if plan["source"] != "MANUAL":
            raise HTTPException(status_code=409, detail="Automatic plans follow Mold Master / Production Tool settings.")
        cur.execute("UPDATE maintenance_plans SET status=%s, updated_by=%s, version=version+1 WHERE id=%s", (to, p.actor, plan_id))
        ms.log(cur, "PLAN", plan_id, "STATUS", p.actor, plan["status"], to)
        return {"message": "Plan updated."}
    return _write(action)


@router.post("/plans/{plan_id}/duplicate")
def duplicate_plan(plan_id: int, p: ActionIn):
    def action(cur):
        plan = ms.get_plan(cur, plan_id)
        data = {k: plan[k] for k in ("asset_type", "asset_id", "wo_type", "plan_type", "frequency_unit", "interval_value", "next_due_date", "next_due_shot",
                                     "priority", "technician", "est_minutes", "description", "checklist", "spare_parts")}
        data["task_name"] = f"{plan['task_name']} (copy)"[:255]
        return {"message": "Plan duplicated.", "id": ms.save_plan(cur, data, p.actor)}
    return _write(action)


@router.post("/plans/{plan_id}/work-order")
def plan_work_order(plan_id: int, p: WoCreateIn):
    result = _write(lambda cur: {"message": "Work order created.", "id": ms.wo_from_plan(cur, plan_id, p.model_dump(), p.actor)})
    events.maint_wo_created(result["id"], p.actor)
    return result


# ---------------------------------------------------------------- work orders

SCOPE_TYPES = {"MACHINE": ("MACHINE", "EQUIPMENT"), "MOLD": ("MOLD",), "TOOL": ("TOOL",), "ALL": tuple(ms.ASSET_TYPES)}


@router.get("/work-orders")
def list_work_orders(scope: str = "ALL", history: bool = False, date_from: date | None = None, date_to: date | None = None):
    """Execution screens (scope MACHINE / MOLD / TOOL) and Maintenance History (history=true: completed / closed WOs)."""
    def action(cur):
        types = SCOPE_TYPES.get(scope.upper())
        if not types:
            raise HTTPException(status_code=400, detail="Unknown scope.")
        marks = ",".join(["%s"] * len(types))
        wos = ms.load_wos(cur, f"w.asset_type IN ({marks})", types)
        today = date.today()
        now = datetime.now()
        today_dt = _dt(today)
        ms_start = _month_start(today)
        if history:
            d_to = date_to or today
            d_from = date_from or (d_to - timedelta(days=29))
            items = [w for w in wos if w["status"] in ms.DONE_WO and w["completed_at"] and _dt(d_from) <= w["completed_at"] < _dt(d_to + timedelta(days=1))]
            dt_total = sum(w["downtime_actual_minutes"] or 0 for w in items)
            prev_from = d_from - (d_to - d_from) - timedelta(days=1)
            prev = [w for w in wos if w["status"] in ms.DONE_WO and w["completed_at"] and _dt(prev_from) <= w["completed_at"] < _dt(d_from)]
            by_asset = defaultdict(float)
            for w in items:
                by_asset[w["asset_code"] or "—"] += (w["downtime_actual_minutes"] or 0) / 60
            days = (d_to - d_from).days + 1
            dtrend, mtrend = [], []
            for i in range(days):
                d = d_from + timedelta(days=i)
                day = [w for w in items if w["completed_at"].date() == d]
                dtrend.append({"date": d.isoformat(), "hours": round(sum(w["downtime_actual_minutes"] or 0 for w in day) / 60, 2)})
                mtrend.append({"date": d.isoformat(), "mttr": ms.mttr(day)})
            return {"period": {"from": d_from.isoformat(), "to": d_to.isoformat()}, "items": items,
                    "kpis": {"total": len(items), "completed": len(items), "preventive": sum(1 for w in items if w["wo_type"] in ms.PREVENTIVE_TYPES),
                             "corrective": sum(1 for w in items if w["wo_type"] in ms.REPAIR_TYPES), "downtime_hours": round(dt_total / 60, 2),
                             "mttr": ms.mttr(items), "pm_compliance": ms.pm_compliance(wos, d_from, d_to), "cost": round(sum(w["total_cost"] for w in items), 0),
                             "total_prev": len(prev), "mttr_prev": ms.mttr(prev), "downtime_prev": round(sum(w["downtime_actual_minutes"] or 0 for w in prev) / 60, 2)},
                    "by_asset_type": dict(Counter(w["asset_type"] for w in items)), "by_type": dict(Counter(w["wo_type"] for w in items)),
                    "by_priority": dict(Counter(w["priority"] for w in items)),
                    "top_downtime": sorted([[k, round(v, 2)] for k, v in by_asset.items()], key=lambda x: -x[1])[:6],
                    "downtime_trend": dtrend, "mttr_trend": mtrend, "cost_breakdown": ms.cost_breakdown(items), "system": _system_status(cur)}
        visible = [w for w in wos if w["status"] in ms.OPEN_WO or (w["completed_at"] and w["completed_at"] >= now - timedelta(days=7))
                   or (w["status"] == "CANCELLED" and w["updated_at"] >= now - timedelta(days=7))]
        if date_from:
            visible = [w for w in visible if (w["planned_start"] or w["created_at"]).date() >= date_from]
        if date_to:
            visible = [w for w in visible if (w["planned_start"] or w["created_at"]).date() <= date_to]
        done_today = [w for w in wos if w["completed_at"] and w["completed_at"] >= today_dt]
        repair_today = [w for w in wos if w["wo_type"] in ms.REPAIR_TYPES and (w["created_at"] >= today_dt or (w["actual_start"] and w["actual_start"] >= today_dt))]
        worked_today = [w for w in wos if w["actual_start"] and (w["actual_finish"] is None or w["actual_finish"] >= today_dt)]

        def downtime_today(ws):
            total = 0.0
            for w in ws:
                start = max(w["actual_start"], today_dt)
                end = w["actual_finish"] or now
                total += max((end - start).total_seconds() / 60, 0)
            return round(total / 60, 2)
        workload = Counter(w["technician"] for w in wos if w["status"] in ms.OPEN_WO and w["technician"])
        mtrend = []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            mtrend.append({"date": d.isoformat(), "mttr": ms.mttr([w for w in wos if w["completed_at"] and w["completed_at"].date() == d])})
        open_wos = [w for w in wos if w["status"] in ms.OPEN_WO]
        return {"items": visible, "kpis": {"total": len(open_wos) + len(done_today), "open": len(open_wos),
                                           "in_progress": sum(1 for w in wos if w["status"] in ("IN_PROGRESS", "PAUSED", "VERIFICATION")),
                                           "completed_today": len(done_today), "overdue": sum(1 for w in wos if w["overdue"]),
                                           "breakdown_today": len(repair_today), "mttr_today": ms.mttr(done_today), "mttr_7d": ms.mttr([w for w in wos if w["completed_at"] and w["completed_at"] >= now - timedelta(days=7)]),
                                           "pm_compliance": ms.pm_compliance(wos, ms_start, today), "downtime_today_hours": downtime_today(worked_today),
                                           "breakdown_downtime_today_hours": downtime_today([w for w in worked_today if w["wo_type"] in ms.REPAIR_TYPES])},
                "by_status": dict(Counter(("OVERDUE" if w["overdue"] else w["status"]) for w in visible)), "by_type": dict(Counter(w["wo_type"] for w in visible)),
                "by_priority": dict(Counter(w["priority"] for w in visible)), "workload": workload.most_common(8), "mttr_trend": mtrend, "system": _system_status(cur)}
    return _read(action)


@router.get("/work-orders/{wo_id}")
def wo_detail(wo_id: int):
    def action(cur):
        wo = ms.enrich_wos(cur, [ms.get_wo(cur, wo_id)])[0]
        a = ms.asset_map(cur, [(wo["asset_type"], wo["asset_id"])]).get(ms.asset_key(wo["asset_type"], wo["asset_id"])) or {}
        extra = {}
        if wo["asset_type"] == "MOLD":
            m = mold_service.enrich(cur, [mold_service.get_mold(cur, wo["asset_id"])])[0]
            extra = {"life_used_pct": m["life_used_pct"], "remaining_shot": m["remaining_shot"], "eol_date": m["eol_date"], "health": m["health"],
                     "health_breakdown": m["health_breakdown"], "pm_due_date": m["pm_due_date"], "mold_group": m["mold_group"], "machine_code": m["machine_code"],
                     "location_label": m["location_label"] or m["machine_code"], "daily_avg_shot": m["daily_avg_shot"]}
        if wo["asset_type"] == "TOOL" and a.get("calibration_required"):
            due = a.get("calibration_due_date")
            extra = {"calibration_days_left": (due - date.today()).days if due else None}
        cur.execute("SELECT * FROM maintenance_wo_checklist WHERE wo_id=%s ORDER BY seq, id", (wo_id,))
        checklist = cur.fetchall()
        cur.execute("SELECT *, qty*unit_cost AS amount FROM maintenance_wo_parts WHERE wo_id=%s ORDER BY id", (wo_id,))
        parts = cur.fetchall()
        cur.execute("SELECT *, hours*rate AS amount FROM maintenance_wo_labor WHERE wo_id=%s ORDER BY id", (wo_id,))
        labor = cur.fetchall()
        cur.execute("SELECT * FROM maintenance_wo_components WHERE wo_id=%s ORDER BY FIELD(component,%s)" % ("%s", ",".join(f"'{c}'" for c in ms.MOLD_COMPONENTS)), (wo_id,))
        components = cur.fetchall()
        req = ms.get_request(cur, wo["request_id"]) if wo["request_id"] else None
        return {"wo": wo, "asset": {**a, **extra}, "checklist": checklist, "parts": parts, "labor": labor, "components": components,
                "attachments": _attachments(cur, "WO", wo_id), "events": ms.events_of(cur, "WO", wo_id), "request": req, "stages": ms.STAGES.get(wo["asset_type"], [])}
    return _read(action)


@router.post("/work-orders")
def create_wo(p: WoCreateIn):
    def action(cur):
        if not p.asset_type or not p.asset_id or not (p.task_name or "").strip():
            raise HTTPException(status_code=400, detail="Asset and task are required.")
        return {"message": "Work order created.", "id": ms.create_wo(cur, p.model_dump(), p.actor)}
    result = _write(action)
    events.maint_wo_created(result["id"], p.actor)
    return result


@router.put("/work-orders/{wo_id}")
def update_wo(wo_id: int, p: WoCreateIn):
    return _write(lambda cur: (ms.update_wo(cur, wo_id, p.model_dump(), p.actor), {"message": "Work order updated."})[1])


@router.put("/work-orders/{wo_id}/checklist/{item_id}")
def checklist_item(wo_id: int, item_id: int, p: ChecklistIn):
    return _write(lambda cur: (ms.set_checklist(cur, wo_id, item_id, (p.result or None) and p.result.upper(), p.note, p.actor), {"message": "Saved."})[1])


@router.post("/work-orders/{wo_id}/checklist")
def checklist_add(wo_id: int, p: ChecklistIn):
    def action(cur):
        if not (p.item or "").strip():
            raise HTTPException(status_code=400, detail="Enter the checklist item.")
        ms.add_checklist(cur, wo_id, p.item, p.actor)
        return {"message": "Saved."}
    return _write(action)


@router.post("/work-orders/{wo_id}/parts")
def part_add(wo_id: int, p: PartIn):
    return _write(lambda cur: (ms.add_part(cur, wo_id, p.model_dump(), p.actor), {"message": "Saved."})[1])


@router.delete("/work-orders/{wo_id}/parts/{line_id}")
def part_delete(wo_id: int, line_id: int):
    return _write(lambda cur: (ms.delete_line(cur, "maintenance_wo_parts", wo_id, line_id, None), {"message": "Deleted."})[1])


@router.post("/work-orders/{wo_id}/labor")
def labor_add(wo_id: int, p: LaborIn):
    return _write(lambda cur: (ms.add_labor(cur, wo_id, p.model_dump(), p.actor), {"message": "Saved."})[1])


@router.delete("/work-orders/{wo_id}/labor/{line_id}")
def labor_delete(wo_id: int, line_id: int):
    return _write(lambda cur: (ms.delete_line(cur, "maintenance_wo_labor", wo_id, line_id, None), {"message": "Deleted."})[1])


@router.put("/work-orders/{wo_id}/components/{component}")
def component_set(wo_id: int, component: str, p: ComponentIn):
    return _write(lambda cur: (ms.set_component(cur, wo_id, component.upper(), p.condition_code.upper(), p.action_code.upper(), p.note, p.actor), {"message": "Saved."})[1])


@router.post("/work-orders/{wo_id}/{wo_action}")
def wo_action(wo_id: int, wo_action: str, p: ActionIn):
    result = _write(lambda cur: (ms.wo_action(cur, wo_id, wo_action, p.actor, p.note, p.version, p.technician, p.stage), {"message": "Work order updated."})[1])
    events.maint_wo_action(wo_id, wo_action, p.actor)
    return result
