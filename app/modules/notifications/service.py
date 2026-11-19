"""In-app notifications that follow the production workflow.

Business code calls ``notify(EVENT, ...)`` right AFTER its own commit. Each event in ``EVENTS`` names the
role(s) that must act next (see the role diagram: Kế hoạch -> Kho -> Công nhân -> QC / Tổ trưởng -> Bảo trì ...).
A notification sent to a role reaches every active user holding that role; ADMIN users see every notification.

Sending is best effort: it uses its own connection and never raises, so a notification problem can never
roll back or fail the production action that triggered it.
"""

import logging
import re

from app.core.database import get_vcc_plastics_connection
from app.core.employee_names import attach_employee_names

log = logging.getLogger(__name__)

SUPER_ROLE = "ADMIN"

# event code -> (module, severity, title, recipient roles, screen to open)
EVENTS = {
    "WO_RELEASED": ("PRODUCTION", "INFO", "Lệnh công việc mới được phát hành",
                    ("OPERATOR", "SHIFT_LEADER"), "/production-management/work-orders/execution"),
    "MATERIAL_NEEDED": ("MATERIAL", "INFO", "Cần chuẩn bị vật tư cho lệnh",
                        ("WAREHOUSE",), "/material-management/allocation"),
    "MATERIAL_ISSUED": ("MATERIAL", "INFO", "Vật tư đã được cấp ra máy",
                        ("OPERATOR", "SHIFT_LEADER"), "/production-management/work-orders/execution"),
    "WO_STARTED": ("PRODUCTION", "INFO", "Lệnh đã bắt đầu chạy, cần kiểm tra đầu chuyền",
                   ("QC", "SHIFT_LEADER"), "/quality-management/inspection-management"),
    "NG_REPORTED": ("QUALITY", "WARNING", "Có hàng lỗi NG cần xử lý",
                    ("QC", "SHIFT_LEADER"), "/quality-management/ng-management"),
    "WO_ON_HOLD": ("PRODUCTION", "WARNING", "Lệnh sản xuất bị tạm dừng",
                   ("SHIFT_LEADER", "PLANNING"), "/production-management/work-orders/execution"),
    "DOWNTIME_REPORTED": ("MACHINE", "WARNING", "Máy dừng",
                          ("SHIFT_LEADER",), "/machine-equipment/downtime-management"),
    "WO_COMPLETED": ("PRODUCTION", "INFO", "Lệnh đã kết thúc, thu hồi vật tư dư",
                     ("WAREHOUSE", "PLANNING"), "/material-management/usage"),
    "MAINT_REQUESTED": ("MAINTENANCE", "WARNING", "Có yêu cầu bảo trì mới",
                        ("MAINTENANCE",), "/maintenance-management/requests"),
    "MAINT_WO_ASSIGNED": ("MAINTENANCE", "INFO", "Lệnh bảo trì được giao",
                          ("MAINTENANCE",), "/maintenance-management/machine-equipment-maintenance"),
    "MAINT_VERIFY": ("MAINTENANCE", "INFO", "Sửa chữa xong, chờ nghiệm thu",
                     ("SHIFT_LEADER",), "/maintenance-management/machine-equipment-maintenance"),
    "MAINT_DONE": ("MAINTENANCE", "INFO", "Bảo trì hoàn tất, máy sẵn sàng",
                   ("SHIFT_LEADER", "PLANNING"), "/maintenance-management/history"),
    "QUALITY_ALERT": ("QUALITY", "CRITICAL", "Cảnh báo chất lượng",
                      ("QC", "BOD_MANAGER"), "/quality-management/dashboard"),
}

MAINT_LINKS = {
    "MOLD": "/maintenance-management/mold-maintenance",
    "TOOL": "/maintenance-management/production-tool-maintenance",
}


def notify(event, message=None, *, ref_type=None, ref_id=None, ref_code=None, actor=None,
           link=None, severity=None, title=None, roles=None, users=None):
    """Record one notification for the roles of ``event`` (plus optional extra roles / users). Never raises."""
    try:
        module, sev, default_title, event_roles, default_link = EVENTS[event]
        targets = [("ROLE", r) for r in dict.fromkeys(tuple(event_roles) + tuple(roles or ()))]
        targets += [("USER", employee_code_of(u)) for u in dict.fromkeys(users or ()) if u and str(u).strip()]
        conn = get_vcc_plastics_connection()
        cur = conn.cursor()
        try:
            cur.execute(
                """INSERT INTO notifications (event_code, module, severity, title, message, ref_type, ref_id, ref_code, link, created_by)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (event, module, severity or sev, (title or default_title)[:200], (message or None) and str(message)[:500],
                 ref_type, ref_id, ref_code and str(ref_code)[:80], link or default_link, actor and str(actor)[:50]),
            )
            nid = cur.lastrowid
            cur.executemany(
                "INSERT IGNORE INTO notification_targets (notification_id, target_type, target_code) VALUES (%s, %s, %s)",
                [(nid, t, c) for t, c in targets],
            )
            conn.commit()
            return nid
        finally:
            cur.close()
            conn.close()
    except Exception:  # noqa: BLE001 - a notification must never break the business action
        log.exception("notify(%s, %s) failed", event, ref_code)
        return None


def employee_code_of(value):
    """Person fields may hold "Full name (00001)" or "Full name - 00001"; return the bare employee code."""
    text = str(value or "").strip()
    m = re.search(r"(?:\(([^()]+)\)|-\s*([^\s-]+))\s*$", text)
    return (m.group(1) or m.group(2)).strip() if m else text


def maint_link(asset_type):
    return MAINT_LINKS.get(str(asset_type or "").upper(), EVENTS["MAINT_VERIFY"][4])


# ---------- reading ----------
def _user_roles(cur, employee_code):
    cur.execute(
        """SELECT DISTINCT r.role_code FROM mes_users u
           JOIN user_roles ur ON ur.user_id = u.id
           JOIN roles r ON r.id = ur.role_id AND r.is_active = 1
           WHERE TRIM(u.employee_code) = %s AND u.is_active = 1""",
        (employee_code,),
    )
    return [row["role_code"] for row in cur.fetchall()]


def _visible_clause(roles, employee_code):
    """SQL filter + params: notifications addressed to the user or one of their roles (ADMIN: all)."""
    if SUPER_ROLE in roles:
        return "1 = 1", []
    role_sql = ",".join(["%s"] * len(roles)) if roles else "NULL"
    clause = (
        "EXISTS (SELECT 1 FROM notification_targets t WHERE t.notification_id = n.id AND "
        f"((t.target_type = 'ROLE' AND t.target_code IN ({role_sql})) OR (t.target_type = 'USER' AND t.target_code = %s)))"
        " AND (n.created_by IS NULL OR n.created_by <> %s)"   # the person who acted is not told about it
    )
    return clause, [*roles, employee_code, employee_code]


def list_for_user(employee_code, limit=50, unread_only=False, days=30):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        roles = _user_roles(cur, employee_code)
        where, params = _visible_clause(roles, employee_code)
        base = f"FROM notifications n LEFT JOIN notification_reads rd ON rd.notification_id = n.id AND rd.employee_code = %s " \
               f"WHERE n.created_at >= NOW() - INTERVAL %s DAY AND {where}"
        base_params = [employee_code, days, *params]
        cur.execute(f"SELECT COUNT(*) AS c {base} AND rd.read_at IS NULL", base_params)
        unread = cur.fetchone()["c"]
        cur.execute(
            f"""SELECT n.id, n.event_code, n.module, n.severity, n.title, n.message, n.ref_type, n.ref_id, n.ref_code,
                       n.link, n.created_by, n.created_at, rd.read_at
                {base} {"AND rd.read_at IS NULL" if unread_only else ""}
                ORDER BY n.id DESC LIMIT %s""",
            [*base_params, int(limit)],
        )
        items = cur.fetchall()
        attach_employee_names(cur, items, ["created_by"])
        for it in items:
            it["is_read"] = it.pop("read_at") is not None
            # "Full name - code" like everywhere else in the app
            it["created_by_label"] = f"{it['created_by_name']} - {it['created_by_code']}" if it.get("created_by_name") else it.get("created_by")
        return {"unread": unread, "items": items}
    finally:
        cur.close()
        conn.close()


def unread_count(employee_code, days=30):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        roles = _user_roles(cur, employee_code)
        where, params = _visible_clause(roles, employee_code)
        cur.execute(
            f"""SELECT COUNT(*) AS c FROM notifications n
                LEFT JOIN notification_reads rd ON rd.notification_id = n.id AND rd.employee_code = %s
                WHERE n.created_at >= NOW() - INTERVAL %s DAY AND rd.read_at IS NULL AND {where}""",
            [employee_code, days, *params],
        )
        return cur.fetchone()["c"]
    finally:
        cur.close()
        conn.close()


def mark_read(employee_code, ids=None, days=30):
    """Mark the given notifications (or every visible unread one) as read for this user."""
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        roles = _user_roles(cur, employee_code)
        where, params = _visible_clause(roles, employee_code)
        id_sql, id_params = "", []
        if ids:
            id_sql = f" AND n.id IN ({','.join(['%s'] * len(ids))})"
            id_params = [int(i) for i in ids]
        cur.execute(
            f"""INSERT IGNORE INTO notification_reads (notification_id, employee_code)
                SELECT n.id, %s FROM notifications n
                WHERE n.created_at >= NOW() - INTERVAL %s DAY AND {where}{id_sql}""",
            [employee_code, days, *params, *id_params],
        )
        conn.commit()
        return {"marked": cur.rowcount}
    finally:
        cur.close()
        conn.close()
