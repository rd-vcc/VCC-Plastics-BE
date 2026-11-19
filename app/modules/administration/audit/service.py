"""Audit Log (9.3): classification of API calls and the append-only writer.

Who: header X-MES-User / X-MES-User-Name sent by the frontend (the backend has no token check yet),
otherwise the "actor" / "created_by" / "updated_by" field of the request body.
What: module / action / data type / record id derived from the URL and method.
Severity: HIGH for deletes, permission / user / configuration changes, failed logins and server errors;
MEDIUM for updates and workflow decisions (approve, reject, hold, cancel...) and master data changes; LOW otherwise.
"""

import json
import re
import time
from datetime import datetime

from app.core.database import get_vcc_plastics_connection

MODULE_BY_PREFIX = [
    ("/api/production-", "PRODUCTION"), ("/api/work-orders", "PRODUCTION"), ("/api/machine-", "MACHINE"), ("/api/alarms", "MACHINE"), ("/api/downtime", "MACHINE"),
    ("/api/mold-management", "MOLD"), ("/api/material-allocations", "MATERIAL"), ("/api/material-inventory", "MATERIAL"), ("/api/material-requirements", "MATERIAL"),
    ("/api/material-usage", "MATERIAL"), ("/api/quality-standard-master", "SYSTEM_CONFIGURATION"), ("/api/quality", "QUALITY"), ("/api/maintenance", "MAINTENANCE"),
    ("/api/traceability", "TRACEABILITY"), ("/api/reports", "REPORTS"), ("/api/dashboard", "DASHBOARD"), ("/api/user-management", "USER_MANAGEMENT"), ("/api/users", "USER_MANAGEMENT"),
    ("/api/roles", "ROLE_PERMISSION"), ("/api/permissions", "ROLE_PERMISSION"), ("/api/auth", "AUTHENTICATION"), ("/api/audit-logs", "ADMINISTRATION"), ("/api/files", "FILES"),
    ("/api/system-settings", "SYSTEM_CONFIGURATION"), ("/api/", "SYSTEM_CONFIGURATION"),
]
ACTION_WORDS = {"approve", "reject", "submit", "start", "stop", "hold", "resume", "cancel", "close", "verify", "complete", "release", "activate", "deactivate", "obsolete",
                "export", "import", "print", "run", "upload", "assign", "acknowledge", "clear", "shelve", "reopen", "pause", "end", "output", "reject_verify", "start_ca",
                "start_investigation", "submit_verify", "hold_wo", "scrap", "move", "install", "remove", "adjust", "copy", "duplicate", "new-revision", "reserve", "issue",
                "return", "reverse", "backflush", "confirm", "reschedule", "split", "unschedule", "schedule", "request_calibration", "report_broken", "repaired",
                "out_of_service", "return_to_service", "status", "favorites"}
SKIP_PATHS = ("/api/auth/login", "/api/auth/logout", "/health", "/uploads", "/docs", "/openapi",
              "/api/notifications")   # read marks are per-user housekeeping, not business changes
SENSITIVE = re.compile(r"pass(word)?|token|secret", re.I)
_roles_cache = {"at": 0.0, "map": {}}


def module_of(path):
    for prefix, module in MODULE_BY_PREFIX:
        if path.startswith(prefix):
            return module
    return "SYSTEM"


def classify(method, path):
    """(module, action, data_type, record_id_from_path)."""
    module = module_of(path)
    parts = [p for p in path.split("/") if p][1:]  # drop "api"
    record = next((p for p in reversed(parts) if p.isdigit() or re.fullmatch(r"[A-Z]{2,6}-[0-9A-Z-]{3,}", p)), None)
    last = parts[-1].lower() if parts else ""
    if method == "DELETE":
        action = "DELETE"
    elif last in ACTION_WORDS and not last.isdigit():
        action = {"status": "STATUS_CHANGE", "favorites": "FAVORITE"}.get(last, last.upper().replace("-", "_"))
    elif method in ("PUT", "PATCH"):
        action = "UPDATE"
    elif method == "POST":
        action = "CREATE"
    else:
        action = "EXPORT" if "export" in path.lower() or "download" in path.lower() else "VIEW"
    if "/favorites/" in path:
        return module, "FAVORITE", "REPORT", parts[-1]
    if "settings" in path and method in ("POST", "PUT", "PATCH"):
        action = "CONFIG_CHANGE"
    if module == "ROLE_PERMISSION" and action in ("CREATE", "UPDATE", "DELETE"):
        action = "PERMISSION_CHANGE" if "permission" in path else action
    resource = [p for p in parts[1:] if not p.isdigit() and p.lower() not in ACTION_WORDS and not re.fullmatch(r"[A-Z]{2,6}-[0-9A-Z-]{3,}", p)]
    data_type = (resource[-1] if resource else parts[0] if parts else "").replace("-", "_").upper()[:80]
    if path.startswith("/api/audit-logs"):
        data_type = "AUDIT_LOG"
    elif data_type in ("FILE", "CONFIG") and len(parts) > 1:
        data_type = parts[0].replace("-", "_").upper()
    return module, action, data_type, record


def severity(module, action, result, status_code):
    if result == "FAILED" and (status_code or 0) >= 500:
        return "HIGH"
    if action in ("FAILED_LOGIN",):
        return "HIGH"
    if action == "DELETE" or module in ("USER_MANAGEMENT", "ROLE_PERMISSION") and action not in ("VIEW", "EXPORT"):
        return "HIGH"
    if action in ("PERMISSION_CHANGE", "CONFIG_CHANGE", "SCRAP"):
        return "HIGH"
    if action in ("UPDATE", "STATUS_CHANGE", "APPROVE", "REJECT", "CANCEL", "HOLD", "REOPEN", "OBSOLETE", "REJECT_VERIFY", "HOLD_WO", "ADJUST", "REVERSE", "OUT_OF_SERVICE") \
            or module == "SYSTEM_CONFIGURATION" and action not in ("VIEW", "EXPORT"):
        return "MEDIUM"
    if result == "FAILED":
        return "MEDIUM"
    return "LOW"


def sanitize(obj, depth=0):
    if depth > 6:
        return "…"
    if isinstance(obj, dict):
        return {k: ("***" if SENSITIVE.search(str(k)) else sanitize(v, depth + 1)) for k, v in obj.items() if k not in ("data",) or not isinstance(v, str) or len(v) < 500}
    if isinstance(obj, list):
        return [sanitize(v, depth + 1) for v in obj[:200]]
    if isinstance(obj, str) and len(obj) > 2000:
        return obj[:2000] + "…"
    return obj


def dumps(obj, limit=60000):
    if obj is None:
        return None
    text = json.dumps(sanitize(obj), ensure_ascii=False, default=str)
    return text if len(text) <= limit else text[:limit] + "…"


def actor_from_body(body):
    if isinstance(body, dict):
        for k in ("actor", "updated_by", "created_by", "user", "operator"):
            v = body.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    return None


def record_from_response(data):
    if isinstance(data, dict):
        for k in ("code", "id", "ng_no", "plan_no", "wo_no", "order_no", "request_no", "lot_no", "usage_no", "allocation_no", "downtime_no", "alarm_no"):
            if data.get(k) not in (None, ""):
                return str(data[k])
    return None


def roles_of(user_code):
    if not user_code:
        return None
    now = time.time()
    if now - _roles_cache["at"] > 300:
        try:
            conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
            try:
                cur.execute("""SELECT u.employee_code, GROUP_CONCAT(r.role_name ORDER BY r.id SEPARATOR ', ') roles FROM mes_users u
                               LEFT JOIN user_roles ur ON ur.user_id=u.id LEFT JOIN roles r ON r.id=ur.role_id GROUP BY u.employee_code""")
                _roles_cache["map"] = {r["employee_code"]: r["roles"] for r in cur.fetchall()}
                _roles_cache["at"] = now
            finally:
                cur.close(); conn.close()
        except Exception:
            pass
    code = re.search(r"\((\w+)\)\s*$", user_code)
    return _roles_cache["map"].get(code.group(1) if code else user_code)


_settings = {"at": 0.0, "v": {}}


def settings():
    now = time.time()
    if now - _settings["at"] > 30:
        try:
            conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
            try:
                cur.execute("SELECT setting_key, setting_value FROM system_settings WHERE setting_key LIKE 'audit.%%'")
                _settings["v"] = {r["setting_key"]: r["setting_value"] for r in cur.fetchall()}
                _settings["at"] = now
            finally:
                cur.close(); conn.close()
        except Exception:
            pass
    return _settings["v"]


def enabled(key, default="1"):
    return str(settings().get(key, default)) == "1"


def write(entry):
    """Insert one audit row (never raises: auditing must not break the business call)."""
    try:
        conn = get_vcc_plastics_connection(); cur = conn.cursor()
        try:
            cols = ["event_at", "user_code", "user_name", "role_name", "module", "action", "data_type", "record_id", "description", "method", "path", "ip_address",
                    "user_agent", "session_id", "result", "status_code", "severity", "message", "before_json", "after_json", "request_json", "duration_ms", "source"]
            vals = [entry.get(c) for c in cols]
            vals[0] = vals[0] or datetime.now()
            cur.execute(f"INSERT INTO audit_logs ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))})", vals)
            conn.commit()
        finally:
            cur.close(); conn.close()
    except Exception:
        pass
