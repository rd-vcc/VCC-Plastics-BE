"""Audit Log API (9.3). Read-only on purpose: audit rows cannot be edited or deleted (only aged out by retention)."""

import json
import time
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.core.database import get_vcc_plastics_connection
from app.modules.administration.audit import service as audit
from app.modules.production.work_orders.router import _system_status
from app.modules.reports import xlsx

router = APIRouter(prefix="/api/audit-logs", tags=["Audit Log"])
LIST_COLS = "id, event_at, user_code, user_name, role_name, module, action, data_type, record_id, description, ip_address, result, status_code, severity, source, message"
_purge = {"day": None}


def _run(action):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        r = action(cur)
        conn.commit()
        return r
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cur.close(); conn.close()


def _json(v):
    return json.loads(json.dumps(v, default=str))


def _where(f):
    start = datetime.combine(f.get("date_from") or date.today() - timedelta(days=6), datetime.min.time())
    end = datetime.combine(f.get("date_to") or date.today(), datetime.min.time()) + timedelta(days=1)
    where, params = ["event_at >= %s", "event_at < %s"], [start, end]
    for key, col in (("user", "user_code"), ("module", "module"), ("action", "action"), ("severity", "severity"), ("result", "result"), ("ip", "ip_address"),
                     ("data_type", "data_type"), ("source", "source")):
        if f.get(key):
            where.append(f"{col}=%s"); params.append(f[key])
    if f.get("record_id"):
        where.append("record_id LIKE %s"); params.append(f"%{f['record_id']}%")
    if f.get("search"):
        s = f"%{f['search']}%"
        where.append("(user_code LIKE %s OR user_name LIKE %s OR description LIKE %s OR record_id LIKE %s OR data_type LIKE %s OR message LIKE %s)")
        params += [s] * 6
    return " AND ".join(where), params, start, end


def _filters(date_from, date_to, user, module, action, severity, result, ip, data_type, record_id, search, source):
    return {"date_from": date_from, "date_to": date_to, "user": user, "module": module, "action": action, "severity": severity, "result": result, "ip": ip,
            "data_type": data_type, "record_id": record_id, "search": search, "source": source}


def _purge_old(cur):
    if _purge["day"] == date.today():
        return
    _purge["day"] = date.today()
    try:
        days = int(audit.settings().get("audit.retention_days", 730))
        cur.execute("DELETE FROM audit_logs WHERE event_at < NOW() - INTERVAL %s DAY", (max(days, 30),))
    except Exception:
        pass


@router.get("")
def list_logs(date_from: Optional[date] = None, date_to: Optional[date] = None, user: Optional[str] = None, module: Optional[str] = None, action: Optional[str] = None,
              severity: Optional[str] = None, result: Optional[str] = None, ip: Optional[str] = None, data_type: Optional[str] = None, record_id: Optional[str] = None,
              search: Optional[str] = None, source: Optional[str] = None, limit: int = 500):
    f = _filters(date_from, date_to, user, module, action, severity, result, ip, data_type, record_id, search, source)

    def act(cur):
        where, params, _, _ = _where(f)
        cur.execute(f"SELECT COUNT(*) n FROM audit_logs WHERE {where}", tuple(params))
        total = int(cur.fetchone()["n"])
        cur.execute(f"SELECT {LIST_COLS}, (before_json IS NOT NULL OR after_json IS NOT NULL) has_changes FROM audit_logs WHERE {where} ORDER BY event_at DESC, id DESC LIMIT %s",
                    (*params, min(max(limit, 1), 2000)))
        return _json({"total": total, "items": cur.fetchall()})
    return _run(act)


@router.get("/summary")
def summary(date_from: Optional[date] = None, date_to: Optional[date] = None, user: Optional[str] = None, module: Optional[str] = None, action: Optional[str] = None,
            severity: Optional[str] = None, result: Optional[str] = None, ip: Optional[str] = None, data_type: Optional[str] = None, record_id: Optional[str] = None,
            search: Optional[str] = None, source: Optional[str] = None):
    f = _filters(date_from, date_to, user, module, action, severity, result, ip, data_type, record_id, search, source)

    def act(cur):
        _purge_old(cur)
        where, params, start, end = _where(f)
        cur.execute(f"""SELECT COUNT(*) total, COUNT(DISTINCT user_code) users, COUNT(DISTINCT module) modules, SUM(severity IN ('HIGH','CRITICAL')) critical,
                               SUM(action='FAILED_LOGIN' OR (result='FAILED' AND status_code IN (401,403))) failed_access, SUM(result='FAILED') failed,
                               SUM(result='SUCCESS' AND method IN ('POST','PUT','PATCH','DELETE') AND action NOT IN ('EXPORT','FAVORITE','RUN','PRINT') OR source='MODULE') changes
                        FROM audit_logs WHERE {where}""", tuple(params))
        k = {x: int(v or 0) for x, v in cur.fetchone().items()}
        span = end - start
        prev_params = [start - span, start] + params[2:]
        cur.execute(f"SELECT COUNT(*) n FROM audit_logs WHERE {where}", tuple(prev_params))
        k["total_prev"] = int(cur.fetchone()["n"])
        cur.execute(f"SELECT DATE(event_at) d, COUNT(*) n, SUM(result='FAILED') failed FROM audit_logs WHERE {where} GROUP BY d ORDER BY d", tuple(params))
        by_day = {r["d"]: r for r in cur.fetchall()}
        days = (end.date() - start.date()).days
        trend = [{"date": start.date() + timedelta(days=i), "count": int(by_day.get(start.date() + timedelta(days=i), {}).get("n", 0) or 0),
                  "failed": int(by_day.get(start.date() + timedelta(days=i), {}).get("failed", 0) or 0)} for i in range(days)]
        cur.execute(f"SELECT module, COUNT(*) n FROM audit_logs WHERE {where} GROUP BY module ORDER BY n DESC", tuple(params))
        by_module = [[r["module"], int(r["n"])] for r in cur.fetchall()]
        cur.execute(f"SELECT COALESCE(user_name, user_code, 'Unknown') u, MAX(user_code) code, COUNT(*) n FROM audit_logs WHERE {where} GROUP BY u ORDER BY n DESC LIMIT 5", tuple(params))
        top_users = cur.fetchall()
        cur.execute(f"SELECT severity, COUNT(*) n FROM audit_logs WHERE {where} GROUP BY severity", tuple(params))
        by_severity = {r["severity"]: int(r["n"]) for r in cur.fetchall()}
        cur.execute(f"SELECT action, COUNT(*) n FROM audit_logs WHERE {where} GROUP BY action ORDER BY n DESC", tuple(params))
        by_action = [[r["action"], int(r["n"])] for r in cur.fetchall()]
        # filter lists (whole table, not only the period)
        cur.execute("SELECT DISTINCT user_code, user_name FROM audit_logs WHERE user_code IS NOT NULL ORDER BY user_name LIMIT 500")
        users = cur.fetchall()
        cur.execute("SELECT DISTINCT module FROM audit_logs ORDER BY module")
        modules = [r["module"] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT action FROM audit_logs ORDER BY action")
        actions = [r["action"] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT data_type FROM audit_logs WHERE data_type IS NOT NULL AND data_type<>'' ORDER BY data_type")
        data_types = [r["data_type"] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT ip_address FROM audit_logs WHERE ip_address IS NOT NULL ORDER BY ip_address LIMIT 200")
        ips = [r["ip_address"] for r in cur.fetchall()]
        cur.execute("SELECT COUNT(*) n, MIN(event_at) first_at FROM audit_logs")
        overall = cur.fetchone()
        return _json({"kpis": k, "trend": trend, "by_module": by_module, "top_users": top_users, "by_severity": by_severity, "by_action": by_action,
                      "lookups": {"users": users, "modules": modules, "actions": actions, "data_types": data_types, "ips": ips},
                      "overall": overall, "settings": audit.settings(), "system": _system_status(cur)})
    return _run(act)


def _flatten(obj, prefix="", out=None, depth=0):
    out = {} if out is None else out
    if isinstance(obj, dict) and depth < 3:
        for k, v in obj.items():
            _flatten(v, f"{prefix}.{k}" if prefix else str(k), out, depth + 1)
    elif isinstance(obj, list):
        out[prefix] = f"[{len(obj)} items]"
    else:
        out[prefix] = obj
    return out


@router.get("/{log_id}")
def detail(log_id: int):
    def act(cur):
        cur.execute("SELECT * FROM audit_logs WHERE id=%s", (log_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Audit record not found.")
        load = lambda s: (json.loads(s) if s and not s.endswith("…") else s) if s else None  # noqa: E731
        try:
            before, after, req = load(row["before_json"]), load(row["after_json"]), load(row["request_json"])
        except Exception:
            before, after, req = row["before_json"], row["after_json"], row["request_json"]
        changes = []
        if isinstance(before, dict) or isinstance(after, dict):
            fb, fa = _flatten(before or {}), _flatten(after or {})
            for key in sorted(set(fb) | set(fa)):
                if key.split(".")[-1] in ("updated_at", "version", "last_update"):
                    continue
                if fb.get(key) != fa.get(key):
                    changes.append({"field": key, "before": fb.get(key), "after": fa.get(key)})
        cur.execute("SELECT id, event_at, action, user_name, result FROM audit_logs WHERE record_id=%s AND data_type <=> %s AND id<>%s ORDER BY event_at DESC LIMIT 10",
                    (row["record_id"], row["data_type"], log_id)) if row["record_id"] else None
        related = cur.fetchall() if row["record_id"] else []
        return _json({**{k: v for k, v in row.items() if k not in ("before_json", "after_json", "request_json")}, "before": before, "after": after, "request": req,
                      "changes": changes[:200], "related": related})
    return _run(act)


@router.get("/export/file")
def export(format: str = "XLSX", date_from: Optional[date] = None, date_to: Optional[date] = None, user: Optional[str] = None, module: Optional[str] = None,
           action: Optional[str] = None, severity: Optional[str] = None, result: Optional[str] = None, ip: Optional[str] = None, data_type: Optional[str] = None,
           record_id: Optional[str] = None, search: Optional[str] = None, source: Optional[str] = None, lang: str = "vi"):
    f = _filters(date_from, date_to, user, module, action, severity, result, ip, data_type, record_id, search, source)
    vi = lang.startswith("vi")

    def act(cur):
        where, params, start, end = _where(f)
        cur.execute(f"SELECT {LIST_COLS}, user_agent, duration_ms FROM audit_logs WHERE {where} ORDER BY event_at DESC LIMIT 50000", tuple(params))
        return cur.fetchall(), start, end
    rows, start, end = _run(act)
    cols = [("event_at", "Thời gian" if vi else "Time"), ("user_name", "Người dùng" if vi else "User"), ("user_code", "Mã" if vi else "Code"), ("role_name", "Vai trò" if vi else "Role"),
            ("module", "Phân hệ" if vi else "Module"), ("action", "Hành động" if vi else "Action"), ("data_type", "Loại dữ liệu" if vi else "Data type"),
            ("record_id", "Mã bản ghi" if vi else "Record ID"), ("description", "Mô tả" if vi else "Description"), ("ip_address", "Địa chỉ IP" if vi else "IP address"),
            ("result", "Kết quả" if vi else "Result"), ("severity", "Mức độ" if vi else "Severity"), ("message", "Thông báo" if vi else "Message"), ("source", "Nguồn" if vi else "Source")]
    fmt = format.upper()
    if fmt == "CSV":
        content, mime, ext = xlsx.build_csv(cols, rows), "text/csv", "csv"
    else:
        title = "Nhật ký hệ thống (Audit Log)" if vi else "Audit Log"
        sub = f"{start:%d/%m/%Y} - {(end - timedelta(days=1)):%d/%m/%Y} · {len(rows)} {'dòng' if vi else 'rows'}"
        content, mime, ext = xlsx.build(title, sub, [], cols, rows), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"
    return Response(content=content, media_type=mime, headers={"Content-Disposition": f'attachment; filename="audit-log_{start:%Y%m%d}.{ext}"'})


class SettingsIn(BaseModel):
    enabled: bool = True
    capture_changes: bool = True
    log_exports: bool = True
    retention_days: int = Field(default=730, ge=30, le=3650)


@router.put("/settings/config")
def update_settings(payload: SettingsIn):
    def act(cur):
        for key, value in (("audit.enabled", "1" if payload.enabled else "0"), ("audit.capture_changes", "1" if payload.capture_changes else "0"),
                           ("audit.log_exports", "1" if payload.log_exports else "0"), ("audit.retention_days", str(payload.retention_days))):
            cur.execute("UPDATE system_settings SET setting_value=%s WHERE setting_key=%s", (value, key))
            if not cur.rowcount:
                cur.execute("SELECT COUNT(*) n FROM system_settings WHERE setting_key=%s", (key,))
                if not cur.fetchone()["n"]:
                    cur.execute("INSERT INTO system_settings (setting_key, setting_value) VALUES (%s,%s)", (key, value))
        audit._settings["at"] = 0
        return {"message": "OK"}
    r = _run(act)
    time.sleep(0)
    return r
