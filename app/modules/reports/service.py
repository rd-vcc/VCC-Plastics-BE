"""Report Center logic: date ranges, running a report, files, schedules and the background scheduler."""

import json
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import HTTPException

from app.core.database import get_vcc_plastics_connection
from app.modules.reports import xlsx
from app.modules.reports.catalog import BY_CODE, MODULES

REPORT_ROOT = Path(__file__).resolve().parents[3] / "storage" / "uploads" / "reports"
RANGE_TYPES = ["TODAY", "YESTERDAY", "LAST_7", "LAST_30", "MTD", "LAST_MONTH"]


def resolve_range(range_type, date_from=None, date_to=None, today=None):
    today = today or date.today()
    if date_from and date_to:
        a, b = date_from, date_to
    else:
        rt = (range_type or "LAST_7").upper()
        if rt == "TODAY":
            a = b = today
        elif rt == "YESTERDAY":
            a = b = today - timedelta(days=1)
        elif rt == "LAST_30":
            a, b = today - timedelta(days=29), today
        elif rt == "MTD":
            a, b = today.replace(day=1), today
        elif rt == "LAST_MONTH":
            b = today.replace(day=1) - timedelta(days=1)
            a = b.replace(day=1)
        else:
            a, b = today - timedelta(days=6), today
    if b < a:
        raise HTTPException(status_code=400, detail="The end date must be after the start date.")
    return {"date_from": a, "date_to": b, "start": datetime.combine(a, datetime.min.time()), "end": datetime.combine(b, datetime.min.time()) + timedelta(days=1)}


def definition(cur, code):
    """Standard report or saved report (CUSTOM-*): returns (base_code, name_en, name_vi, module, category, saved_row)."""
    if code in BY_CODE:
        c, module, category, en, vi, desc, fn = BY_CODE[code]
        return {"code": c, "base": c, "module": module, "category": category, "en": en, "vi": vi, "description": desc, "fn": fn, "saved": None}
    cur.execute("SELECT * FROM report_saved WHERE code=%s", (code,))
    s = cur.fetchone()
    if not s or s["base_code"] not in BY_CODE:
        raise HTTPException(status_code=404, detail="Report not found.")
    c, module, category, en, vi, desc, fn = BY_CODE[s["base_code"]]
    return {"code": code, "base": c, "module": module, "category": category, "en": s["name"], "vi": s["name"], "description": s["description"] or desc, "fn": fn, "saved": s}


def execute(cur, d, rng):
    started = time.perf_counter()
    result = d["fn"](cur, rng)
    return result, int((time.perf_counter() - started) * 1000)


def headers(result, lang):
    vi = str(lang or "vi").lower().startswith("vi")
    return [(c["key"], c["vi"] if vi else c["en"]) for c in result["columns"]]


def summary_lines(result, lang):
    vi = str(lang or "vi").lower().startswith("vi")
    return [(s["vi"] if vi else s["en"], s["value"]) for s in result["summary"]]


def build_file(d, result, rng, fmt, lang):
    vi = str(lang or "vi").lower().startswith("vi")
    title = d["vi"] if vi else d["en"]
    period = f"{rng['date_from']:%d/%m/%Y} - {rng['date_to']:%d/%m/%Y}"
    subtitle = f"{'Kỳ báo cáo' if vi else 'Period'}: {period} · {'Tạo lúc' if vi else 'Generated'}: {datetime.now():%d/%m/%Y %H:%M} · VCC Plastics MES"
    cols = headers(result, lang)
    if fmt == "CSV":
        return xlsx.build_csv(cols, result["rows"]), "text/csv", "csv"
    return xlsx.build(title, subtitle, summary_lines(result, lang), cols, result["rows"]), \
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"


def store(content, code, ext):
    folder = REPORT_ROOT / f"{date.today():%Y%m}"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{code}_{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:6]}.{ext}"
    (folder / name).write_bytes(content)
    return f"/uploads/reports/{date.today():%Y%m}/{name}", len(content)


def log_run(cur, d, action, fmt, rng, rows, url, size, ms, actor, status="SUCCESS", message=None, schedule_id=None):
    cur.execute("""INSERT INTO report_runs (report_code, report_name, module, action, output_format, date_from, date_to, row_count, file_url, file_size, duration_ms,
                   status, message, schedule_id, actor) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (d["code"], d["en"], d["module"], action, fmt, rng["date_from"] if rng else None, rng["date_to"] if rng else None, rows, url, size, ms, status,
                 (message or None) and str(message)[:500], schedule_id, actor))


# =========================================================
# SCHEDULES
# =========================================================


def next_run(s, after=None):
    """Next run time of a schedule strictly after `after`."""
    after = after or datetime.now()
    t = s["run_time"]
    if isinstance(t, timedelta):
        secs = int(t.total_seconds())
        hh, mm = secs // 3600, (secs % 3600) // 60
    else:
        hh, mm = t.hour, t.minute
    day = after.date()
    for _ in range(0, 400):
        cand = datetime.combine(day, datetime.min.time()).replace(hour=hh, minute=mm)
        ok = (s["frequency"] == "DAILY" or (s["frequency"] == "WEEKLY" and cand.weekday() == int(s["weekday"] or 0))
              or (s["frequency"] == "MONTHLY" and cand.day == min(int(s["month_day"] or 1), 28)))
        if ok and cand > after:
            return cand
        day += timedelta(days=1)
    return None


def run_schedule(cur, s, actor="SCHEDULER"):
    d = definition(cur, s["report_code"])
    rng = resolve_range(s["range_type"])
    try:
        result, ms = execute(cur, d, rng)
        content, _, ext = build_file(d, result, rng, s["output_format"], s["language"])
        url, size = store(content, d["code"], ext)
        log_run(cur, d, "SCHEDULE", s["output_format"], rng, len(result["rows"]), url, size, ms, actor, schedule_id=s["id"],
                message=f"Recipients: {s['recipients']} (e-mail delivery not configured)" if s.get("recipients") else None)
        status = "SUCCESS"
    except Exception as exc:  # keep the schedule alive, record the failure
        log_run(cur, d, "SCHEDULE", s["output_format"], rng, 0, None, 0, 0, actor, "FAILED", str(exc), s["id"])
        status = "FAILED"
    nxt = next_run(s, datetime.now())
    cur.execute("UPDATE report_schedules SET last_run_at=NOW(), last_status=%s, next_run_at=%s WHERE id=%s", (status, nxt, s["id"]))
    return status


_thread = {"t": None, "stop": False}


def _loop():
    while not _thread["stop"]:
        try:
            conn = get_vcc_plastics_connection()
            cur = conn.cursor(dictionary=True)
            try:
                cur.execute("SELECT * FROM report_schedules WHERE status='ACTIVE' AND next_run_at IS NOT NULL AND next_run_at <= NOW()")
                for s in cur.fetchall():
                    run_schedule(cur, s)
                    conn.commit()
            finally:
                cur.close(); conn.close()
        except Exception:  # database unavailable / tables missing: try again next minute
            pass
        for _ in range(60):
            if _thread["stop"]:
                break
            time.sleep(1)


def start_scheduler():
    if _thread["t"] and _thread["t"].is_alive():
        return
    _thread["stop"] = False
    _thread["t"] = threading.Thread(target=_loop, name="report-scheduler", daemon=True)
    _thread["t"].start()


def stop_scheduler():
    _thread["stop"] = True


def to_json(v):
    return json.loads(json.dumps(v, default=str))


__all__ = ["MODULES", "RANGE_TYPES"]
