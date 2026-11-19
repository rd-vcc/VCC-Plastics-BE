"""Production workflow -> notifications. Routers call these AFTER their write has committed.

Each function re-reads the record it needs on its own connection, builds the message and calls ``notify``.
Everything is best effort: any error is logged and swallowed, so the business action is never affected.

Flow (role diagram):
  Kế hoạch phát hành lệnh  -> Công nhân, Tổ trưởng (lệnh mới) + Kho (chuẩn bị vật tư)
  Kho cấp vật tư ra máy    -> Công nhân, Tổ trưởng
  Công nhân bắt đầu lệnh   -> QC (kiểm tra đầu chuyền), Tổ trưởng
  Báo lỗi NG               -> QC, Tổ trưởng
  Lệnh tạm dừng / máy dừng -> Tổ trưởng (+ Kế hoạch khi lệnh dừng)
  Kết thúc lệnh            -> Kho (thu hồi vật tư dư), Kế hoạch
  Yêu cầu bảo trì          -> Bảo trì
  Lệnh bảo trì được giao   -> Bảo trì + kỹ thuật viên được giao
  Sửa xong, chờ nghiệm thu -> Tổ trưởng
  Bảo trì hoàn tất         -> Tổ trưởng, Kế hoạch
  Cảnh báo chất lượng mới  -> QC, Ban giám đốc
"""

import functools
import logging

from app.core.database import get_vcc_plastics_connection
from app.modules.notifications.service import maint_link, notify

log = logging.getLogger(__name__)

EXEC = "/production-management/work-orders/execution"


def _safe(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            fn(*args, **kwargs)
        except Exception:  # noqa: BLE001
            log.exception("notification event %s failed", fn.__name__)
    return wrapper


def _one(sql, params):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        return cur.fetchone()
    finally:
        cur.close()
        conn.close()


def _all(sql, params):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(sql, params)
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


WO_SQL = """SELECT d.id, d.wo_no, d.planned_qty, d.status, p.product_code, p.product_name, e.equipment_code AS machine_code,
                   mo.mold_code, po.order_no
            FROM production_plan_details d JOIN products p ON p.id = d.product_id
            LEFT JOIN equipment e ON e.id = d.machine_id LEFT JOIN molds mo ON mo.id = d.mold_id
            LEFT JOIN production_orders po ON po.id = d.production_order_id
            WHERE d.id IN ({ids})"""


def _wos(ids):
    ids = [int(i) for i in ids if i]
    return _all(WO_SQL.format(ids=",".join(["%s"] * len(ids))), ids) if ids else []


def _wo_text(w):
    parts = [w.get("product_name") or w.get("product_code"), w.get("machine_code") and f"máy {w['machine_code']}",
             w.get("mold_code") and f"khuôn {w['mold_code']}"]
    return ", ".join(p for p in parts if p)


def _qty(v):
    try:
        return f"{float(v):,.0f}".replace(",", ".")
    except (TypeError, ValueError):
        return str(v)


# ---------- production ----------
@_safe
def wo_released(wo_ids, actor):
    for w in _wos(wo_ids):
        text = _wo_text(w) + (f", SL {_qty(w['planned_qty'])}" if w.get("planned_qty") else "")
        notify("WO_RELEASED", text, ref_type="WORK_ORDER", ref_id=w["id"], ref_code=w["wo_no"], actor=actor, link=f"{EXEC}?wo={w['id']}")
        notify("MATERIAL_NEEDED", f"Giữ chỗ và cấp vật tư cho {_wo_text(w)}", ref_type="WORK_ORDER", ref_id=w["id"], ref_code=w["wo_no"],
               actor=actor, link=f"/material-management/allocation?order={w['order_no']}" if w.get("order_no") else None)


@_safe
def plan_published(plan_id, actor):
    rows = _all("SELECT id FROM production_plan_details WHERE plan_id=%s AND status='RELEASED' AND released_at >= NOW() - INTERVAL 5 MINUTE", (plan_id,))
    wo_released([r["id"] for r in rows], actor)


@_safe
def wo_started(wo_id, actor, operator=None):
    for w in _wos([wo_id]):
        who = f", công nhân {operator}" if operator else ""
        notify("WO_STARTED", f"{_wo_text(w)}{who}. Thực hiện kiểm tra FAI / IPQC.", ref_type="WORK_ORDER", ref_id=w["id"],
               ref_code=w["wo_no"], actor=actor, link=f"/quality-management/inspection-management?wo={w['id']}")


@_safe
def ng_reported(wo_id, qty, reason, actor):
    if not qty or float(qty) <= 0:
        return
    for w in _wos([wo_id]):
        why = f": {reason}" if reason else ""
        notify("NG_REPORTED", f"{_qty(qty)} sản phẩm lỗi{why} ({_wo_text(w)})", ref_type="WORK_ORDER", ref_id=w["id"], ref_code=w["wo_no"],
               actor=actor, link=f"/quality-management/ng-management?wo={w['id']}")


@_safe
def wo_on_hold(wo_id, reason, actor):
    for w in _wos([wo_id]):
        notify("WO_ON_HOLD", f"{_wo_text(w)}" + (f". Lý do: {reason}" if reason else ""), ref_type="WORK_ORDER", ref_id=w["id"],
               ref_code=w["wo_no"], actor=actor, link=f"{EXEC}?wo={w['id']}")


@_safe
def wo_completed(wo_id, actor):
    for w in _wos([wo_id]):
        notify("WO_COMPLETED", f"{_wo_text(w)}. Thu hồi nhựa dư tại máy và đóng lệnh.", ref_type="WORK_ORDER", ref_id=w["id"],
               ref_code=w["wo_no"], actor=actor, link=f"/material-management/usage?wo={w['id']}")


# ---------- material ----------
@_safe
def material_issued(allocation_id, actor):
    a = _one("""SELECT a.allocation_no, a.work_order_id, a.issued_qty, m.material_code, m.material_name, l.lot_no, e.equipment_code AS machine_code,
                       d.wo_no
                FROM material_allocations a LEFT JOIN materials m ON m.id = a.material_id LEFT JOIN material_lots l ON l.id = a.lot_id
                LEFT JOIN equipment e ON e.id = a.machine_id LEFT JOIN production_plan_details d ON d.id = a.work_order_id
                WHERE a.id=%s""", (allocation_id,))
    if not a:
        return
    text = f"{a.get('material_name') or a.get('material_code')} lô {a.get('lot_no') or '-'}, {_qty(a['issued_qty'])}" + \
           (f" ra máy {a['machine_code']}" if a.get("machine_code") else "")
    notify("MATERIAL_ISSUED", text, ref_type="WORK_ORDER", ref_id=a["work_order_id"], ref_code=a.get("wo_no") or a["allocation_no"], actor=actor,
           link=f"{EXEC}?wo={a['work_order_id']}" if a.get("work_order_id") else None)


# ---------- machine ----------
@_safe
def downtime_reported(downtime_id, actor):
    d = _one("""SELECT t.downtime_no, t.equipment_id, t.reason_text, t.category, e.equipment_code, rc.reason_name, w.wo_no
                FROM downtime_events t LEFT JOIN equipment e ON e.id = t.equipment_id
                LEFT JOIN reason_codes rc ON rc.id = t.reason_code_id LEFT JOIN production_plan_details w ON w.id = t.work_order_id
                WHERE t.id=%s""", (downtime_id,))
    if not d:
        return
    why = d.get("reason_name") or d.get("reason_text") or d.get("category") or ""
    notify("DOWNTIME_REPORTED", f"Máy {d.get('equipment_code') or d['equipment_id']}" + (f": {why}" if why else "") +
           (f" (lệnh {d['wo_no']})" if d.get("wo_no") else ""), ref_type="DOWNTIME", ref_id=downtime_id, ref_code=d["downtime_no"],
           actor=actor, title=f"Máy {d.get('equipment_code')} dừng" if d.get("equipment_code") else None,
           link=f"/machine-equipment/downtime-management?machine={d['equipment_id']}")


# ---------- maintenance ----------
def _asset_code(asset_type, asset_id):
    from app.modules.maintenance.service import resolve_asset
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        _, info = resolve_asset(cur, asset_type, asset_id)
        return (info or {}).get("code") or str(asset_id)
    except Exception:  # noqa: BLE001
        return str(asset_id)
    finally:
        cur.close()
        conn.close()


@_safe
def maint_requested(request_id, actor=None):
    r = _one("SELECT id, request_no, asset_type, asset_id, priority, short_description, source FROM maintenance_requests WHERE id=%s", (request_id,))
    if not r:
        return
    sev = "CRITICAL" if str(r.get("priority")).upper() in ("URGENT", "CRITICAL", "HIGH") else None
    notify("MAINT_REQUESTED", f"{_asset_code(r['asset_type'], r['asset_id'])}: {r.get('short_description') or ''}".strip(": "),
           ref_type="MAINT_REQUEST", ref_id=r["id"], ref_code=r["request_no"], actor=actor, severity=sev,
           link=f"/maintenance-management/requests?request={r['id']}")


@_safe
def maint_alarm_request(alarm_id):
    """A CRITICAL alarm opened a maintenance request automatically (simulator / PLC / manual alarm)."""
    r = _one("SELECT id FROM maintenance_requests WHERE alarm_id=%s ORDER BY id DESC LIMIT 1", (alarm_id,))
    if r:
        maint_requested(r["id"], "SYSTEM")


def _mwo(wo_id):
    return _one("SELECT id, wo_no, asset_type, asset_id, status, technician, task_name FROM maintenance_work_orders WHERE id=%s", (wo_id,))


@_safe
def maint_wo_created(wo_id, actor):
    w = _mwo(wo_id)
    if not w or not w.get("technician"):
        return
    notify("MAINT_WO_ASSIGNED", f"{_asset_code(w['asset_type'], w['asset_id'])}: {w.get('task_name') or ''}. Kỹ thuật viên {w['technician']}",
           ref_type="MAINT_WO", ref_id=w["id"], ref_code=w["wo_no"], actor=actor, users=[w["technician"]],
           link=f"{maint_link(w['asset_type'])}?wo={w['id']}")


@_safe
def maint_wo_action(wo_id, action, actor):
    action = str(action or "").upper()
    w = _mwo(wo_id)
    if not w:
        return
    asset = _asset_code(w["asset_type"], w["asset_id"])
    if action == "ASSIGN":
        maint_wo_created(wo_id, actor)
    elif action == "SUBMIT" and w["status"] == "VERIFICATION":
        notify("MAINT_VERIFY", f"{asset}: {w.get('task_name') or ''}. Kiểm tra và nghiệm thu.", ref_type="MAINT_WO", ref_id=w["id"],
               ref_code=w["wo_no"], actor=actor, link=f"{maint_link(w['asset_type'])}?wo={w['id']}")
    elif action == "VERIFY" and w["status"] in ("COMPLETED", "CLOSED"):
        notify("MAINT_DONE", f"{asset} đã sẵn sàng chạy lại.", ref_type="MAINT_WO", ref_id=w["id"], ref_code=w["wo_no"], actor=actor,
               users=[w["technician"]] if w.get("technician") else None, link=f"/maintenance-management/history?wo={w['id']}")


# ---------- quality ----------
@_safe
def quality_alert_raised(alert_id, kind, severity, message, ref_code=None):
    if str(severity).upper() not in ("CRITICAL", "HIGH"):
        return
    notify("QUALITY_ALERT", message, ref_type="QUALITY_ALERT", ref_id=alert_id, ref_code=ref_code or kind, actor="SYSTEM",
           severity="CRITICAL" if str(severity).upper() == "CRITICAL" else "WARNING")
