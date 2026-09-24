"""Downtime Management (screen 2.4).

Durations are clipped to the selected period (an event running over midnight counts in both days;
an open event counts until now). Downtime rate = downtime minutes / (equipment count x elapsed
period minutes). MTTR = average duration of closed unplanned events.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.database import get_vcc_plastics_connection
from app.modules.machine import service as ms
from app.modules.production.work_orders.router import _system_status
from app.modules.production.work_orders.service import load_shifts, shift_of

router = APIRouter(prefix="/api/downtime", tags=["Downtime Management"])

DT_SELECT = """
    SELECT d.*, e.equipment_code, e.equipment_name, e.factory_node_id, fn.name AS area_name, t.category AS equipment_category, t.type_name,
           r.reason_code, r.reason_name, w.wo_no, al.alarm_no
    FROM downtime_events d
    JOIN equipment e ON e.id = d.equipment_id
    JOIN equipment_types t ON t.id = e.equipment_type_id
    LEFT JOIN factory_structure_nodes fn ON fn.id = e.factory_node_id
    LEFT JOIN reason_codes r ON r.id = d.reason_code_id
    LEFT JOIN production_plan_details w ON w.id = d.work_order_id
    LEFT JOIN machine_alarms al ON al.id = d.alarm_id
"""


class ReportIn(BaseModel):
    equipment_id: int = Field(gt=0)
    reason_code_id: Optional[int] = Field(default=None, gt=0)
    reason_text: Optional[str] = Field(default=None, max_length=500)
    category: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    work_order_id: Optional[int] = Field(default=None, gt=0)
    alarm_id: Optional[int] = Field(default=None, gt=0)
    actor: Optional[str] = Field(default=None, max_length=100)


class CloseIn(BaseModel):
    ended_at: Optional[datetime] = None
    reason_code_id: Optional[int] = Field(default=None, gt=0)
    reason_text: Optional[str] = Field(default=None, max_length=500)
    root_cause: Optional[str] = Field(default=None, max_length=1000)
    action_taken: Optional[str] = Field(default=None, max_length=1000)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class UpdateIn(BaseModel):
    reason_code_id: Optional[int] = Field(default=None, gt=0)
    reason_text: Optional[str] = Field(default=None, max_length=500)
    category: str
    started_at: datetime
    ended_at: Optional[datetime] = None
    root_cause: Optional[str] = Field(default=None, max_length=1000)
    action_taken: Optional[str] = Field(default=None, max_length=1000)
    actor: Optional[str] = Field(default=None, max_length=100)
    version: int = Field(ge=1)


class CancelIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)
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


def _equipment_rows(cur):
    cur.execute("""SELECT e.id, e.equipment_code, e.equipment_name, e.operational_status, e.factory_node_id, fn.name AS area_name, t.category, t.type_name
                   FROM equipment e JOIN equipment_types t ON t.id = e.equipment_type_id LEFT JOIN factory_structure_nodes fn ON fn.id = e.factory_node_id
                   WHERE e.asset_status='ACTIVE' ORDER BY e.equipment_code""")
    return cur.fetchall()


@router.get("/lookups")
def lookups():
    def action(cur):
        cur.execute("""SELECT r.id, r.reason_code, r.reason_name, r.applicable_module, c.category_code FROM reason_codes r
                       LEFT JOIN reason_code_categories c ON c.id = r.category_id
                       WHERE r.status='ACTIVE' ORDER BY (c.category_code='DOWNTIME') DESC, r.sort_order, r.reason_code""")
        reasons = [{**r, "planned_default": r["reason_code"] in ms.PLANNED_REASONS} for r in cur.fetchall()]
        equipment = _equipment_rows(cur)
        areas = sorted({(e["factory_node_id"], e["area_name"]) for e in equipment if e["factory_node_id"]}, key=lambda x: x[1] or "")
        return {"reasons": reasons, "equipment": equipment, "areas": [{"id": a, "name": n} for a, n in areas],
                "shifts": [{k: s[k] for k in ("id", "shift_code", "shift_name")} for s in load_shifts(cur)]}
    return _read(action)


def _decorate(d, start, end, now):
    s = max(d["started_at"], start)
    e = min(d["ended_at"] or now, end, now)
    d["duration_min"] = round(max((e - s).total_seconds(), 0) / 60, 1)
    d["total_min"] = round(max(((d["ended_at"] or now) - d["started_at"]).total_seconds(), 0) / 60, 1)
    return d


def _period(cur, start, end, filters):
    sql = f"{DT_SELECT} WHERE d.status <> 'CANCELLED' AND d.started_at < %s AND (d.ended_at IS NULL OR d.ended_at > %s)"
    params = [end, start]
    for col, key in (("d.equipment_id", "equipment_id"), ("e.factory_node_id", "area_id"), ("d.reason_code_id", "reason_code_id")):
        if filters.get(key):
            sql += f" AND {col}=%s"; params.append(filters[key])
    if filters.get("category"):
        sql += " AND d.category=%s"; params.append(filters["category"].upper())
    if filters.get("equipment_category"):
        sql += " AND t.category=%s"; params.append(filters["equipment_category"].upper())
    cur.execute(sql + " ORDER BY d.started_at DESC", tuple(params))
    now = datetime.now()
    rows = [_decorate(r, start, end, now) for r in cur.fetchall()]
    if filters.get("shift_id"):
        shifts = load_shifts(cur)
        rows = [r for r in rows if (shift_of(r["started_at"], shifts) or {}).get("id") == filters["shift_id"]]
    return rows


def _kpis(rows, equipment_count, start, end):
    now = datetime.now()
    minutes = max((min(end, now) - start).total_seconds() / 60, 1)
    total = sum(r["duration_min"] for r in rows)
    unplanned_closed = [r["total_min"] for r in rows if r["category"] == "UNPLANNED" and r["status"] == "CLOSED"]
    return {"total_min": round(total, 1), "events": len(rows), "machines": len({r["equipment_id"] for r in rows}),
            "rate": round(total / (equipment_count * minutes) * 100, 2) if equipment_count else None,
            "mttr": round(sum(unplanned_closed) / len(unplanned_closed), 1) if unplanned_closed else None,
            "planned_min": round(sum(r["duration_min"] for r in rows if r["category"] == "PLANNED"), 1),
            "unplanned_min": round(sum(r["duration_min"] for r in rows if r["category"] == "UNPLANNED"), 1),
            "open": sum(1 for r in rows if r["status"] == "OPEN")}


@router.get("/summary")
def summary(date_from: date | None = None, date_to: date | None = None, equipment_id: int | None = None, area_id: int | None = None,
            reason_code_id: int | None = None, category: str | None = None, shift_id: int | None = None, equipment_category: str | None = None,
            status: str | None = None):
    d1 = date_from or date.today()
    d2 = date_to or d1
    filters = {"equipment_id": equipment_id, "area_id": area_id, "reason_code_id": reason_code_id, "category": category,
               "shift_id": shift_id, "equipment_category": equipment_category}

    def action(cur):
        start = datetime.combine(d1, datetime.min.time())
        end = datetime.combine(d2, datetime.min.time()) + timedelta(days=1)
        equipment = [e for e in _equipment_rows(cur) if (not area_id or e["factory_node_id"] == area_id) and (not equipment_id or e["id"] == equipment_id)
                     and (not equipment_category or e["category"] == equipment_category.upper())]
        rows = _period(cur, start, end, filters)
        span = end - start
        prev = _period(cur, start - span, start, filters)
        kpis = _kpis(rows, len(equipment), start, end)
        kpis["prev"] = _kpis(prev, len(equipment), start - span, start)

        def reason_of(r):
            return r["reason_name"] or r["reason_text"] or "UNSPECIFIED"

        by_reason = defaultdict(float)
        by_machine = defaultdict(float)
        for r in rows:
            by_reason[reason_of(r)] += r["duration_min"]
            by_machine[(r["equipment_id"], r["equipment_code"])] += r["duration_min"]
        total = kpis["total_min"] or 1
        now = datetime.now()
        minutes = max((min(end, now) - start).total_seconds() / 60, 1)
        # trend: at least 7 days ending at date_to
        t_start = datetime.combine(min(d1, d2 - timedelta(days=6)), datetime.min.time())
        trend_rows = rows if t_start == start else _period(cur, t_start, end, filters)
        trend = []
        day = t_start
        while day < end:
            nxt = day + timedelta(days=1)
            mins = sum(_decorate(dict(r), day, nxt, now)["duration_min"] for r in trend_rows)
            day_minutes = max((min(nxt, now) - day).total_seconds() / 60, 0)
            trend.append({"date": day.date().isoformat(), "minutes": round(mins, 1),
                          "rate": round(mins / (len(equipment) * day_minutes) * 100, 2) if equipment and day_minutes else None})
            day = nxt
        # heatmap: rows = days of the trend window, columns = 2-hour slots
        heat = []
        day = t_start
        while day < end:
            cells = []
            for slot in range(12):
                s = day + timedelta(hours=2 * slot)
                cells.append(round(sum(_decorate(dict(r), s, s + timedelta(hours=2), now)["duration_min"] for r in trend_rows), 1))
            heat.append({"date": day.date().isoformat(), "cells": cells})
            day += timedelta(days=1)
        filtered = [r for r in rows if not status or r["status"] == status.upper()]
        return {
            "kpis": kpis, "events": filtered, "recent": rows[:6],
            "by_reason": sorted([[k, round(v, 1), round(v / total * 100, 1)] for k, v in by_reason.items()], key=lambda x: -x[1]),
            "by_machine": sorted([{"equipment_id": k[0], "equipment_code": k[1], "minutes": round(v, 1), "rate": round(v / minutes * 100, 2)}
                                  for k, v in by_machine.items()], key=lambda x: -x["minutes"]),
            "trend": trend, "heatmap": heat,
            "category": {"PLANNED": kpis["planned_min"], "UNPLANNED": kpis["unplanned_min"]},
            "system": _system_status(cur),
        }
    return _read(action)


@router.get("/{downtime_id}")
def detail(downtime_id: int):
    def action(cur):
        cur.execute(f"{DT_SELECT} WHERE d.id=%s", (downtime_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Downtime event not found.")
        return _decorate(row, row["started_at"], datetime.max, datetime.now())
    return _read(action)


@router.post("", status_code=201)
def report(p: ReportIn):
    def action(cur):
        if not p.reason_code_id and not (p.reason_text or "").strip():
            raise HTTPException(status_code=400, detail="Choose a reason code or describe the reason.")
        started = p.started_at or datetime.now()
        if started > datetime.now() + timedelta(minutes=1):
            raise HTTPException(status_code=400, detail="Start time is in the future.")
        downtime_id = ms.open_downtime(cur, p.equipment_id, p.actor, p.reason_code_id, (p.reason_text or "").strip() or None, p.category,
                                       "MANUAL", p.work_order_id, started, p.alarm_id, set_status=not p.ended_at)
        if p.ended_at:
            ms.close_downtime(cur, downtime_id, p.actor, ended_at=p.ended_at, set_status=False)
        return {"message": "Downtime reported.", "id": downtime_id}
    return _run_write(action)


@router.post("/{downtime_id}/close")
def close(downtime_id: int, p: CloseIn):
    def action(cur):
        ms.close_downtime(cur, downtime_id, p.actor, p.ended_at, (p.root_cause or "").strip() or None, (p.action_taken or "").strip() or None,
                          p.version, reason_code_id=p.reason_code_id, reason_text=(p.reason_text or "").strip() or None)
        return {"message": "Downtime closed."}
    return _run_write(action)


@router.put("/{downtime_id}")
def update(downtime_id: int, p: UpdateIn):
    def action(cur):
        cur.execute("SELECT * FROM downtime_events WHERE id=%s FOR UPDATE", (downtime_id,))
        d = cur.fetchone()
        if not d:
            raise HTTPException(status_code=404, detail="Downtime event not found.")
        if d["status"] == "CANCELLED":
            raise HTTPException(status_code=409, detail="Cancelled events cannot be edited.")
        if p.ended_at and p.ended_at < p.started_at:
            raise HTTPException(status_code=400, detail="End time is before the start time.")
        if d["status"] == "CLOSED" and not p.ended_at:
            raise HTTPException(status_code=400, detail="A closed event needs an end time.")
        cur.execute("""UPDATE downtime_events SET reason_code_id=%s, reason_text=%s, category=%s, started_at=%s, ended_at=%s, root_cause=%s,
                       action_taken=%s, version=version+1 WHERE id=%s AND version=%s""",
                    (p.reason_code_id, (p.reason_text or "").strip() or None, p.category.upper(), p.started_at,
                     p.ended_at if d["status"] == "CLOSED" else None, p.root_cause, p.action_taken, downtime_id, p.version))
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="This downtime event was changed by another user. Reload and try again.")
        return {"message": "Downtime updated."}
    return _run_write(action)


@router.post("/{downtime_id}/cancel")
def cancel(downtime_id: int, p: CancelIn):
    def action(cur):
        cur.execute("SELECT * FROM downtime_events WHERE id=%s FOR UPDATE", (downtime_id,))
        d = cur.fetchone()
        if not d:
            raise HTTPException(status_code=404, detail="Downtime event not found.")
        if d["status"] == "CANCELLED":
            raise HTTPException(status_code=409, detail="Already cancelled.")
        if d["source"] == "MES" and d["status"] == "OPEN":
            raise HTTPException(status_code=409, detail="This stop comes from a work order on hold; resume the work order instead.")
        cur.execute("UPDATE downtime_events SET status='CANCELLED', root_cause=%s, closed_by=%s, version=version+1 WHERE id=%s AND version=%s",
                    (f"Cancelled: {p.reason}", p.actor, downtime_id, p.version))
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="This downtime event was changed by another user. Reload and try again.")
        if d["status"] == "OPEN":
            ms.set_machine_status(cur, d["equipment_id"], ms.status_after_stop(cur, d["equipment_id"]), "MES", None, f"{d['downtime_no']} cancelled")
        return {"message": "Downtime cancelled."}
    return _run_write(action)
