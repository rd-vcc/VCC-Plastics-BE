"""Report Center API (screen 8.1)."""

import shutil
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.database import get_vcc_plastics_connection
from app.modules.production.work_orders.router import _system_status
from app.modules.reports import service as rs
from app.modules.reports.catalog import DESCRIPTION_VI, MODULES, REPORTS

router = APIRouter(prefix="/api/reports", tags=["Report Center"])


def _run(action):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        result = action(cur)
        conn.commit()
        return result
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cur.close(); conn.close()


def _catalog(cur, actor):
    cur.execute("SELECT report_code FROM report_favorites WHERE user_code=%s", (actor or "",))
    favs = {r["report_code"] for r in cur.fetchall()}
    cur.execute("""SELECT report_code, MAX(CASE WHEN action IN ('EXPORT','SCHEDULE','PRINT') THEN created_at END) last_gen, MAX(created_at) last_used,
                          SUM(created_at >= DATE_FORMAT(CURDATE(), '%Y-%m-01')) uses_mtd FROM report_runs WHERE status='SUCCESS' GROUP BY report_code""")
    usage = {r["report_code"]: r for r in cur.fetchall()}
    cur.execute("SELECT report_code, COUNT(*) n FROM report_schedules WHERE status='ACTIVE' GROUP BY report_code")
    sched = {r["report_code"]: int(r["n"]) for r in cur.fetchall()}
    items = []
    for code, module, category, en, vi, desc, _ in REPORTS:
        u = usage.get(code) or {}
        items.append({"code": code, "module": module, "category": category, "name_en": en, "name_vi": vi, "description": desc, "description_vi": DESCRIPTION_VI.get(code, desc), "type": "STANDARD",
                      "formats": ["XLSX", "CSV", "PDF"], "owner": "SYSTEM", "favorite": code in favs, "last_generated": u.get("last_gen"), "last_used": u.get("last_used"),
                      "uses_mtd": int(u.get("uses_mtd") or 0), "schedules": sched.get(code, 0), "created_at": None})
    cur.execute("SELECT * FROM report_saved ORDER BY created_at DESC")
    base = {r[0]: r for r in REPORTS}
    for s in cur.fetchall():
        if s["base_code"] not in base or (not s["is_shared"] and s["owner"] != actor):
            continue
        b = base[s["base_code"]]
        u = usage.get(s["code"]) or {}
        items.append({"code": s["code"], "module": b[1], "category": b[2], "name_en": s["name"], "name_vi": s["name"], "description": s["description"] or b[5], "description_vi": s["description"] or DESCRIPTION_VI.get(b[0], b[5]), "type": "CUSTOM",
                      "base_code": s["base_code"], "range_type": s["range_type"], "formats": ["XLSX", "CSV", "PDF"], "owner": s["owner"], "favorite": s["code"] in favs,
                      "last_generated": u.get("last_gen"), "last_used": u.get("last_used"), "uses_mtd": int(u.get("uses_mtd") or 0), "schedules": sched.get(s["code"], 0),
                      "created_at": s["created_at"]})
    return items


@router.get("/catalog")
def catalog(actor: Optional[str] = None):
    return _run(lambda cur: rs.to_json({"items": _catalog(cur, actor), "modules": MODULES, "range_types": rs.RANGE_TYPES}))


@router.get("/summary")
def summary(actor: Optional[str] = None):
    def action(cur):
        items = _catalog(cur, actor)
        today = date.today()
        m0 = today.replace(day=1)
        pm1 = m0 - timedelta(days=1)
        pm0 = pm1.replace(day=1)
        gen = "action IN ('EXPORT','SCHEDULE','PRINT') AND status='SUCCESS'"
        cur.execute(f"SELECT COUNT(*) n FROM report_runs WHERE {gen} AND created_at >= %s", (m0,))
        mtd = int(cur.fetchone()["n"])
        cur.execute(f"SELECT COUNT(*) n FROM report_runs WHERE {gen} AND created_at >= %s AND created_at < %s AND DAY(created_at) <= %s", (pm0, m0, today.day))
        prev = int(cur.fetchone()["n"])
        cur.execute(f"SELECT MAX(created_at) t FROM report_runs WHERE {gen}")
        last = cur.fetchone()["t"]
        cur.execute("SELECT COUNT(*) n FROM report_saved WHERE created_at >= %s", (m0,))
        new_month = int(cur.fetchone()["n"])
        trend = []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            cur.execute(f"SELECT COUNT(*) n FROM report_runs WHERE {gen} AND DATE(created_at)=%s", (d,))
            trend.append({"date": d, "count": int(cur.fetchone()["n"])})
        cur.execute("""SELECT report_code, MAX(report_name) name, COUNT(*) n FROM report_runs WHERE status='SUCCESS' AND created_at >= %s GROUP BY report_code ORDER BY n DESC LIMIT 5""", (m0,))
        top = cur.fetchall()
        cur.execute("SELECT module, COUNT(*) n FROM report_runs WHERE status='SUCCESS' AND created_at >= %s GROUP BY module ORDER BY n DESC", (m0,))
        by_module = [[r["module"], int(r["n"])] for r in cur.fetchall()]
        cur.execute("SELECT * FROM report_schedules ORDER BY status='ACTIVE' DESC, next_run_at")
        schedules = cur.fetchall()
        horizon = datetime.now() + timedelta(days=7)
        upcoming = [s for s in schedules if s["status"] == "ACTIVE" and s["next_run_at"] and s["next_run_at"] <= horizon]
        cur.execute("SELECT output_format, COUNT(*) files, SUM(file_size) size FROM report_runs WHERE file_url IS NOT NULL GROUP BY output_format")
        storage = [{"format": r["output_format"], "files": int(r["files"]), "bytes": int(r["size"] or 0)} for r in cur.fetchall()]
        try:
            rs.REPORT_ROOT.mkdir(parents=True, exist_ok=True)
            disk = shutil.disk_usage(rs.REPORT_ROOT)
            disk_info = {"total": disk.total, "free": disk.free}
        except Exception:
            disk_info = {"total": None, "free": None}
        cur.execute("SELECT * FROM report_runs WHERE actor=%s OR %s IS NULL ORDER BY created_at DESC LIMIT 8", (actor, actor))
        recent = cur.fetchall()
        return rs.to_json({
            "kpis": {"total": len(items), "favorites": sum(1 for i in items if i["favorite"]), "generated_mtd": mtd, "generated_prev": prev, "new_month": new_month,
                     "scheduled": sum(1 for s in schedules if s["status"] == "ACTIVE"), "last_generated": last},
            "trend": trend, "top": top, "by_module": by_module, "schedules": schedules, "upcoming": upcoming, "storage": storage, "disk": disk_info, "recent": recent,
            "system": _system_status(cur)})
    return _run(action)


@router.get("/run/{code}")
def run_report(code: str, date_from: Optional[date] = None, date_to: Optional[date] = None, range_type: Optional[str] = None, lang: str = "vi",
               actor: Optional[str] = None, log: bool = True):
    def action(cur):
        d = rs.definition(cur, code)
        rng = rs.resolve_range(range_type or (d["saved"]["range_type"] if d["saved"] else "LAST_7"), date_from, date_to)
        result, ms = rs.execute(cur, d, rng)
        if log:
            rs.log_run(cur, d, "PREVIEW", None, rng, len(result["rows"]), None, 0, ms, actor)
        return rs.to_json({"report": {k: v for k, v in d.items() if k not in ("fn", "saved")}, "range": {"from": rng["date_from"], "to": rng["date_to"]},
                           "duration_ms": ms, **result})
    return _run(action)


@router.get("/export/{code}")
def export_report(code: str, format: str = "XLSX", date_from: Optional[date] = None, date_to: Optional[date] = None, range_type: Optional[str] = None,
                  lang: str = "vi", actor: Optional[str] = None):
    fmt = format.upper()
    if fmt not in ("XLSX", "CSV"):
        raise HTTPException(status_code=400, detail="Format must be XLSX or CSV (PDF: print the preview).")

    def action(cur):
        d = rs.definition(cur, code)
        rng = rs.resolve_range(range_type or (d["saved"]["range_type"] if d["saved"] else "LAST_7"), date_from, date_to)
        result, ms = rs.execute(cur, d, rng)
        content, mime, ext = rs.build_file(d, result, rng, fmt, lang)
        url, size = rs.store(content, d["code"], ext)
        rs.log_run(cur, d, "EXPORT", fmt, rng, len(result["rows"]), url, size, ms, actor)
        return content, mime, f"{d['code']}_{rng['date_from']:%Y%m%d}-{rng['date_to']:%Y%m%d}.{ext}"
    content, mime, name = _run(action)
    return Response(content=content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="{name}"'})


class LogIn(BaseModel):
    date_from: Optional[date] = None
    date_to: Optional[date] = None
    rows: int = 0
    actor: Optional[str] = Field(default=None, max_length=100)


@router.post("/print/{code}")
def log_print(code: str, payload: LogIn):
    def action(cur):
        d = rs.definition(cur, code)
        rng = {"date_from": payload.date_from, "date_to": payload.date_to} if payload.date_from else None
        rs.log_run(cur, d, "PRINT", "PDF", rng, payload.rows, None, 0, 0, payload.actor)
        return {"message": "OK"}
    return _run(action)


class ActorIn(BaseModel):
    actor: Optional[str] = Field(default=None, max_length=100)


@router.post("/favorites/{code}")
def toggle_favorite(code: str, payload: ActorIn):
    user = payload.actor or "SYSTEM"

    def action(cur):
        rs.definition(cur, code)
        cur.execute("SELECT id FROM report_favorites WHERE report_code=%s AND user_code=%s", (code, user))
        row = cur.fetchone()
        if row:
            cur.execute("DELETE FROM report_favorites WHERE id=%s", (row["id"],))
            return {"favorite": False}
        cur.execute("INSERT INTO report_favorites (report_code, user_code) VALUES (%s,%s)", (code, user))
        return {"favorite": True}
    return _run(action)


@router.get("/history")
def history(limit: int = 200, code: Optional[str] = None, action: Optional[str] = None):
    def act(cur):
        where, params = ["1=1"], []
        if code:
            where.append("report_code=%s"); params.append(code)
        if action:
            where.append("action=%s"); params.append(action.upper())
        cur.execute(f"SELECT * FROM report_runs WHERE {' AND '.join(where)} ORDER BY created_at DESC LIMIT %s", (*params, min(limit, 1000)))
        return rs.to_json(cur.fetchall())
    return _run(act)


class SavedIn(BaseModel):
    base_code: str = Field(min_length=1, max_length=60)
    name: str = Field(min_length=1, max_length=200)
    description: Optional[str] = Field(default=None, max_length=500)
    range_type: str = Field(default="LAST_7", max_length=20)
    is_shared: bool = True
    actor: Optional[str] = Field(default=None, max_length=100)


@router.post("/saved")
def create_saved(payload: SavedIn):
    if payload.range_type.upper() not in rs.RANGE_TYPES:
        raise HTTPException(status_code=400, detail="Invalid range type.")

    def action(cur):
        rs.definition(cur, payload.base_code)
        cur.execute("SELECT COUNT(*) n FROM report_saved WHERE name=%s", (payload.name.strip(),))
        if cur.fetchone()["n"]:
            raise HTTPException(status_code=409, detail="A report with this name already exists.")
        cur.execute("INSERT INTO report_saved (code, base_code, name, description, range_type, owner, is_shared) VALUES ('TMP',%s,%s,%s,%s,%s,%s)",
                    (payload.base_code, payload.name.strip(), payload.description, payload.range_type.upper(), payload.actor, 1 if payload.is_shared else 0))
        sid = cur.lastrowid
        code = f"CUSTOM-{sid:04d}"
        cur.execute("UPDATE report_saved SET code=%s WHERE id=%s", (code, sid))
        return {"code": code}
    return _run(action)


@router.delete("/saved/{code}")
def delete_saved(code: str, actor: Optional[str] = None):
    def action(cur):
        cur.execute("SELECT * FROM report_saved WHERE code=%s", (code,))
        s = cur.fetchone()
        if not s:
            raise HTTPException(status_code=404, detail="Report not found.")
        cur.execute("SELECT COUNT(*) n FROM report_schedules WHERE report_code=%s", (code,))
        if cur.fetchone()["n"]:
            raise HTTPException(status_code=409, detail="Delete its schedules first.")
        cur.execute("DELETE FROM report_favorites WHERE report_code=%s", (code,))
        cur.execute("DELETE FROM report_saved WHERE id=%s", (s["id"],))
        return {"message": "OK"}
    return _run(action)


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    report_code: str = Field(min_length=1, max_length=60)
    frequency: str = Field(default="DAILY", pattern="^(DAILY|WEEKLY|MONTHLY)$")
    run_time: str = Field(default="08:00", pattern=r"^\d{2}:\d{2}$")
    weekday: Optional[int] = Field(default=None, ge=0, le=6)
    month_day: Optional[int] = Field(default=None, ge=1, le=28)
    range_type: str = Field(default="YESTERDAY", max_length=20)
    output_format: str = Field(default="XLSX", pattern="^(XLSX|CSV)$")
    language: str = Field(default="vi", max_length=5)
    recipients: Optional[str] = Field(default=None, max_length=1000)
    status: str = Field(default="ACTIVE", pattern="^(ACTIVE|PAUSED)$")
    actor: Optional[str] = Field(default=None, max_length=100)


def _schedule_values(cur, p):
    rs.definition(cur, p.report_code)
    if p.range_type.upper() not in rs.RANGE_TYPES:
        raise HTTPException(status_code=400, detail="Invalid range type.")
    if p.frequency == "WEEKLY" and p.weekday is None:
        raise HTTPException(status_code=400, detail="Select the weekday.")
    if p.frequency == "MONTHLY" and p.month_day is None:
        raise HTTPException(status_code=400, detail="Select the day of month.")
    s = {"frequency": p.frequency, "run_time": datetime.strptime(p.run_time, "%H:%M").time(), "weekday": p.weekday, "month_day": p.month_day}
    return s, rs.next_run(s) if p.status == "ACTIVE" else None


@router.post("/schedules")
def create_schedule(payload: ScheduleIn):
    def action(cur):
        s, nxt = _schedule_values(cur, payload)
        cur.execute("""INSERT INTO report_schedules (name, report_code, frequency, run_time, weekday, month_day, range_type, output_format, language, recipients, status, next_run_at, created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (payload.name.strip(), payload.report_code, payload.frequency, s["run_time"], payload.weekday, payload.month_day, payload.range_type.upper(),
                     payload.output_format, payload.language, payload.recipients, payload.status, nxt, payload.actor))
        return {"id": cur.lastrowid, "next_run_at": str(nxt) if nxt else None}
    return _run(action)


@router.put("/schedules/{schedule_id}")
def update_schedule(schedule_id: int, payload: ScheduleIn):
    def action(cur):
        s, nxt = _schedule_values(cur, payload)
        cur.execute("""UPDATE report_schedules SET name=%s, report_code=%s, frequency=%s, run_time=%s, weekday=%s, month_day=%s, range_type=%s, output_format=%s, language=%s,
                       recipients=%s, status=%s, next_run_at=%s WHERE id=%s""",
                    (payload.name.strip(), payload.report_code, payload.frequency, s["run_time"], payload.weekday, payload.month_day, payload.range_type.upper(),
                     payload.output_format, payload.language, payload.recipients, payload.status, nxt, schedule_id))
        if not cur.rowcount:
            cur.execute("SELECT id FROM report_schedules WHERE id=%s", (schedule_id,))
            if not cur.fetchone():
                raise HTTPException(status_code=404, detail="Schedule not found.")
        return {"id": schedule_id}
    return _run(action)


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: int):
    return _run(lambda cur: (cur.execute("DELETE FROM report_schedules WHERE id=%s", (schedule_id,)), {"message": "OK"})[1])


@router.post("/schedules/{schedule_id}/run")
def run_schedule_now(schedule_id: int, payload: ActorIn):
    def action(cur):
        cur.execute("SELECT * FROM report_schedules WHERE id=%s", (schedule_id,))
        s = cur.fetchone()
        if not s:
            raise HTTPException(status_code=404, detail="Schedule not found.")
        return {"status": rs.run_schedule(cur, s, payload.actor or "SYSTEM")}
    return _run(action)
