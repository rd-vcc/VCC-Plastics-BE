"""Alarm History (screen 2.5).

Active alarms = ACTIVE + ACKNOWLEDGED (shelved are counted separately). Clearance time = cleared_at -
raised_at. Alarm rate (%) = share of equipment-hours in a day that had at least one alarm raised.
"""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.database import get_vcc_plastics_connection
from app.modules.machine import service as ms
from app.modules.production.work_orders.router import _system_status

router = APIRouter(prefix="/api/alarms", tags=["Alarm History"])

ALARM_SELECT = """
    SELECT a.*, e.equipment_code, e.equipment_name, e.factory_node_id, fn.name AS area_name, t.category AS equipment_category, t.type_name,
           w.wo_no
    FROM machine_alarms a
    LEFT JOIN equipment e ON e.id = a.equipment_id
    LEFT JOIN equipment_types t ON t.id = e.equipment_type_id
    LEFT JOIN factory_structure_nodes fn ON fn.id = e.factory_node_id
    LEFT JOIN production_plan_details w ON w.id = a.work_order_id
"""


class ReportIn(BaseModel):
    equipment_id: int = Field(gt=0)
    message: str = Field(min_length=1, max_length=500)
    priority: str = "MEDIUM"
    note: Optional[str] = Field(default=None, max_length=1000)
    actor: Optional[str] = Field(default=None, max_length=100)


class ActionIn(BaseModel):
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)
    note: Optional[str] = Field(default=None, max_length=1000)
    hours: Optional[float] = Field(default=None, gt=0, le=72)


class BulkIn(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    actor: Optional[str] = Field(default=None, max_length=100)
    note: Optional[str] = Field(default=None, max_length=1000)
    hours: Optional[float] = Field(default=None, gt=0, le=72)


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


def _decorate(a, now):
    end = a["cleared_at"] or a["closed_at"] or now
    a["duration_min"] = round(max((end - a["raised_at"]).total_seconds(), 0) / 60, 1)
    a["value"] = float(a["value"]) if a["value"] is not None else None
    a["limit_value"] = float(a["limit_value"]) if a["limit_value"] is not None else None
    return a


def _filter(rows, f):
    return [a for a in rows if (not f.get("equipment_id") or a["equipment_id"] == f["equipment_id"])
            and (not f.get("area_id") or a["factory_node_id"] == f["area_id"])
            and (not f.get("equipment_category") or a["equipment_category"] == f["equipment_category"])
            and (not f.get("priority") or a["priority"] == f["priority"])]


@router.get("/lookups")
def lookups():
    def action(cur):
        cur.execute("""SELECT e.id, e.equipment_code, e.equipment_name, e.factory_node_id, fn.name AS area_name, t.category, t.type_name
                       FROM equipment e JOIN equipment_types t ON t.id = e.equipment_type_id LEFT JOIN factory_structure_nodes fn ON fn.id = e.factory_node_id
                       WHERE e.asset_status='ACTIVE' ORDER BY e.equipment_code""")
        equipment = cur.fetchall()
        areas = sorted({(e["factory_node_id"], e["area_name"]) for e in equipment if e["factory_node_id"]}, key=lambda x: x[1] or "")
        return {"equipment": equipment, "areas": [{"id": a, "name": n} for a, n in areas],
                "categories": sorted({e["category"] for e in equipment}), "priorities": ms.PRIORITIES}
    return _read(action)


@router.get("/summary")
def summary(date_from: date | None = None, date_to: date | None = None, equipment_id: int | None = None, area_id: int | None = None,
            equipment_category: str | None = None, priority: str | None = None, view: str = "active"):
    d1 = date_from or date.today()
    d2 = date_to or d1
    f = {"equipment_id": equipment_id, "area_id": area_id, "equipment_category": (equipment_category or "").upper() or None, "priority": (priority or "").upper() or None}

    def action(cur):
        ms.unshelve_expired(cur)
        now = datetime.now()
        start = datetime.combine(d1, datetime.min.time())
        end = datetime.combine(d2, datetime.min.time()) + timedelta(days=1)
        cur.execute(f"{ALARM_SELECT} WHERE a.status IN ('ACTIVE','ACKNOWLEDGED','SHELVED') ORDER BY FIELD(a.priority,'CRITICAL','HIGH','MEDIUM','LOW'), a.raised_at DESC")
        open_rows = _filter([_decorate(a, now) for a in cur.fetchall()], {**f, "priority": None})
        t_start = min(start, datetime.combine(d2 - timedelta(days=6), datetime.min.time()), now - timedelta(hours=24))
        cur.execute(f"{ALARM_SELECT} WHERE a.raised_at >= %s AND a.raised_at < %s ORDER BY a.raised_at DESC", (t_start, max(end, now)))
        window = _filter([_decorate(a, now) for a in cur.fetchall()], {**f, "priority": None})
        period = [a for a in window if start <= a["raised_at"] < end]
        active = [a for a in open_rows if a["status"] in {"ACTIVE", "ACKNOWLEDGED"}]
        today0 = datetime.combine(date.today(), datetime.min.time())
        today = [a for a in window if a["raised_at"] >= today0]
        yesterday = [a for a in window if today0 - timedelta(days=1) <= a["raised_at"] < today0]

        def clearance(rows):
            vals = [(a["cleared_at"] - a["raised_at"]).total_seconds() / 60 for a in rows if a["cleared_at"]]
            return round(sum(vals) / len(vals), 1) if vals else None

        by_pri = Counter(a["priority"] for a in active)
        kpis = {"active": len(active), **{p.lower(): by_pri[p] for p in ms.PRIORITIES},
                "shelved": sum(1 for a in open_rows if a["status"] == "SHELVED"), "today": len(today), "yesterday": len(yesterday),
                "clearance": clearance(today), "clearance_yesterday": clearance(yesterday), "period": len(period), "period_clearance": clearance(period)}
        listing = open_rows if view == "active" else period
        if f["priority"]:
            listing = [a for a in listing if a["priority"] == f["priority"]]
        # 24 h trend by priority (hourly)
        hours = [now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=23 - i) for i in range(24)]
        trend24 = [{"hour": h.strftime("%H:%M"), **{p: sum(1 for a in window if h <= a["raised_at"] < h + timedelta(hours=1) and a["priority"] == p) for p in ms.PRIORITIES}}
                   for h in hours]
        cur.execute("SELECT COUNT(*) AS n FROM equipment WHERE asset_status='ACTIVE'")
        eq_count = max(int(cur.fetchone()["n"]), 1)
        days = []
        day = datetime.combine(d2 - timedelta(days=6), datetime.min.time())
        while day <= datetime.combine(d2, datetime.min.time()):
            slots = {(a["equipment_id"], a["raised_at"].hour) for a in window if day <= a["raised_at"] < day + timedelta(days=1)}
            hrs = max(min(24, (now - day).total_seconds() / 3600), 0)
            days.append({"date": day.date().isoformat(), "count": sum(1 for a in window if day <= a["raised_at"] < day + timedelta(days=1)),
                         "rate": round(len(slots) / (eq_count * hrs) * 100, 2) if hrs else None})
            day += timedelta(days=1)
        by_area = Counter(a["area_name"] or "—" for a in active)
        by_type = Counter(a["type_name"] or a["equipment_category"] or "—" for a in active)
        areas = sorted({a["area_name"] or "—" for a in window})
        heat = [{"area": ar, "cells": [sum(1 for a in period if (a["area_name"] or "—") == ar and 2 * s <= a["raised_at"].hour < 2 * s + 2) for s in range(12)]} for ar in areas]
        reasons = Counter(a["message"] for a in period)
        return {"kpis": kpis, "alarms": listing, "trend24": trend24, "rate_trend": days,
                "by_priority": {p: by_pri[p] for p in ms.PRIORITIES}, "by_area": by_area.most_common(), "by_type": by_type.most_common(),
                "heatmap": heat, "top_reasons": reasons.most_common(5), "period_total": len(period), "system": _system_status(cur)}
    return _run_write(action)


@router.get("/{alarm_id}")
def detail(alarm_id: int):
    def action(cur):
        cur.execute(f"{ALARM_SELECT} WHERE a.id=%s", (alarm_id,))
        a = cur.fetchone()
        if not a:
            raise HTTPException(status_code=404, detail="Alarm not found.")
        cur.execute("SELECT * FROM machine_alarm_actions WHERE alarm_id=%s ORDER BY acted_at DESC, id DESC", (alarm_id,))
        a = _decorate(a, datetime.now())
        a["actions"] = cur.fetchall()
        cur.execute("SELECT id, downtime_no, status FROM downtime_events WHERE alarm_id=%s", (alarm_id,))
        a["downtime"] = cur.fetchall()
        return a
    return _read(action)


@router.post("", status_code=201)
def report(p: ReportIn):
    def action(cur):
        alarm_id = ms.raise_alarm(cur, p.equipment_id, p.message.strip(), p.priority, "MANUAL", None, actor=p.actor, note=(p.note or "").strip() or None)
        return {"message": "Alarm reported.", "id": alarm_id}
    return _run_write(action)


@router.post("/bulk/{action_name}")
def bulk(action_name: str, p: BulkIn):
    def action(cur):
        done, skipped = 0, []
        for alarm_id in p.ids:
            cur.execute("SELECT alarm_no, version FROM machine_alarms WHERE id=%s", (alarm_id,))
            row = cur.fetchone()
            if not row:
                continue
            try:
                ms.alarm_action(cur, alarm_id, action_name, p.actor, row["version"], (p.note or "").strip() or None, p.hours)
                done += 1
            except HTTPException as exc:
                skipped.append(f"{row['alarm_no']}: {exc.detail}")
        return {"done": done, "skipped": skipped}
    return _run_write(action)


@router.post("/{alarm_id}/{action_name}")
def act(alarm_id: int, action_name: str, p: ActionIn):
    def action(cur):
        ms.alarm_action(cur, alarm_id, action_name, p.actor, p.version, (p.note or "").strip() or None, p.hours)
        return {"message": "OK"}
    return _run_write(action)
