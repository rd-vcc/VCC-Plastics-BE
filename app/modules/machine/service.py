"""Machine & Equipment core (screens 2.1 - 2.5): status history, downtime, alarms and machine data ingest.

Every writer (PLC / IoT gateway, simulator, MES, operators) goes through these functions so the
status history, downtime and alarm records stay consistent:
- set_machine_status closes the open status event, opens a new one and updates equipment.operational_status.
- A downtime event is OPEN until closed; while open the machine is DOWN (unplanned), MAINTENANCE
  (preventive maintenance) or IDLE (other planned stops). Closing it returns the machine to RUNNING
  when a Work Order is in production on it, otherwise IDLE.
- Alarms are de-duplicated per equipment + alarm code while not cleared; a parameter back inside its
  limits clears its alarm automatically. ACTIVE -> ACKNOWLEDGED / SHELVED -> CLEARED -> CLOSED.
Parameter limits come from the active Process Parameter Standard (product / mold / machine type),
falling back to DEFAULT_PARAMETERS.
"""

import re
from datetime import date, datetime, timedelta

from fastapi import HTTPException

STATUSES = ["RUNNING", "IDLE", "DOWN", "MAINTENANCE", "OFFLINE"]
PRIORITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
PLANNED_REASONS = {"DT-MOLD", "DT-SETUP", "DT-PM"}
MAINTENANCE_REASONS = {"DT-PM"}
ALARM_OPEN = ("ACTIVE", "ACKNOWLEDGED", "SHELVED")

# code: (name, unit, min, target, max)
DEFAULT_PARAMETERS = {
    "MACHINE": {
        "INJECTION_PRESSURE": ("Injection Pressure", "bar", 800, 1100, 1400),
        "HOLDING_PRESSURE": ("Holding Pressure", "bar", 500, 650, 800),
        "MELT_TEMPERATURE": ("Melt Temperature", "°C", 210, 230, 250),
        "MOLD_TEMPERATURE": ("Mold Temperature", "°C", 45, 60, 75),
        "SCREW_SPEED": ("Screw Speed", "rpm", 80, 120, 160),
        "COOLING_TIME": ("Cooling Time", "s", 10, 15, 20),
        "INJECTION_TIME": ("Injection Time", "s", 1.5, 3, 4.5),
        "HOLDING_TIME": ("Holding Time", "s", 2, 3, 4.5),
        "MOLD_OPEN_TIME": ("Mold Open Time", "s", 0.8, 1.2, 2),
        "EJECT_TIME": ("Eject Time", "s", 0.8, 1.5, 2.5),
        "CYCLE_TIME": ("Cycle Time", "s", None, 25, None),
    },
    "UTILITY": {
        "SUPPLY_TEMPERATURE": ("Supply Temperature", "°C", 8, 12, 16),
        "RETURN_TEMPERATURE": ("Return Temperature", "°C", 12, 17, 22),
        "FLOW_RATE": ("Flow Rate", "L/min", 60, 80, 100),
        "POWER": ("Power", "kW", 0, 25, 40),
    },
    "ROBOT": {
        "POSITION_ERROR": ("Position Error", "mm", 0, 0.05, 0.2),
        "PICK_TIME": ("Pick Time", "s", 1, 2.5, 4),
    },
}
COMPONENTS = {
    "MACHINE": ["ROBOT", "HOPPER", "MOLD", "HEATER", "HYDRAULIC", "LUBRICATION", "COOLING_WATER", "AIR_PRESSURE"],
    "UTILITY": ["COMPRESSOR", "PUMP", "FAN", "POWER"],
    "ROBOT": ["SERVO", "GRIPPER", "AIR_PRESSURE", "SAFETY"],
}


def param_code(name):
    return re.sub(r"[^A-Z0-9]+", "_", str(name or "").upper()).strip("_")


def _next_no(cur, table, column, prefix):
    stem = f"{prefix}-{date.today():%y%m%d}-"
    cur.execute(f"SELECT {column} AS n FROM {table} WHERE {column} LIKE %s ORDER BY {column} DESC LIMIT 1", (f"{stem}%",))
    row = cur.fetchone()
    tail = row["n"][len(stem):] if row else ""
    return f"{stem}{(int(tail) + 1 if tail.isdigit() else 1):04d}"


def get_equipment(cur, equipment_id=None, code=None):
    cur.execute(
        f"""SELECT e.*, t.type_code, t.type_name, t.category, fn.name AS area_name
            FROM equipment e JOIN equipment_types t ON t.id = e.equipment_type_id LEFT JOIN factory_structure_nodes fn ON fn.id = e.factory_node_id
            WHERE {'e.id=%s' if equipment_id else 'e.equipment_code=%s'}""", (equipment_id or code,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Equipment not found.")
    return row


def running_work_order(cur, equipment_id):
    cur.execute("""SELECT id, wo_no, product_id, mold_id, cycle_time_sec FROM production_plan_details
                   WHERE machine_id=%s AND status='IN_PRODUCTION' ORDER BY started_at DESC LIMIT 1""", (equipment_id,))
    return cur.fetchone()


# =========================================================
# STATUS
# =========================================================


def set_machine_status(cur, equipment_id, status, source="MES", work_order_id=None, remark=None, force=False):
    status = status.upper()
    if status not in STATUSES:
        raise HTTPException(status_code=400, detail="Unknown machine status.")
    cur.execute("SELECT operational_status FROM equipment WHERE id=%s FOR UPDATE", (equipment_id,))
    row = cur.fetchone()
    if not row:
        return
    previous = row["operational_status"]
    if previous == status and not force:
        return
    now = datetime.now()
    cur.execute("UPDATE machine_status_events SET ended_at=%s WHERE equipment_id=%s AND ended_at IS NULL", (now, equipment_id))
    cur.execute("""INSERT INTO machine_status_events (equipment_id, status, previous_status, started_at, work_order_id, source, remark)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""", (equipment_id, status, previous, now, work_order_id, source, remark))
    cur.execute("UPDATE equipment SET operational_status=%s WHERE id=%s", (status, equipment_id))


def status_after_stop(cur, equipment_id):
    return "RUNNING" if running_work_order(cur, equipment_id) else "IDLE"


# =========================================================
# DOWNTIME
# =========================================================


def _reason(cur, reason_code_id):
    if not reason_code_id:
        return None
    cur.execute("SELECT id, reason_code, reason_name FROM reason_codes WHERE id=%s", (reason_code_id,))
    return cur.fetchone()


def open_downtime(cur, equipment_id, reporter, reason_code_id=None, reason_text=None, category=None, source="MANUAL",
                  work_order_id=None, started_at=None, alarm_id=None, set_status=True):
    cur.execute("SELECT id, downtime_no FROM downtime_events WHERE equipment_id=%s AND status='OPEN' LIMIT 1", (equipment_id,))
    existing = cur.fetchone()
    if existing:
        if source == "MANUAL":
            raise HTTPException(status_code=409, detail=f"The machine already has an open downtime ({existing['downtime_no']}).")
        return existing["id"]
    reason = _reason(cur, reason_code_id)
    code = reason["reason_code"] if reason else None
    category = (category or ("PLANNED" if code in PLANNED_REASONS else "UNPLANNED")).upper()
    if not work_order_id:
        wo = running_work_order(cur, equipment_id)
        work_order_id = wo["id"] if wo else None
    no = _next_no(cur, "downtime_events", "downtime_no", "DT")
    cur.execute(
        """INSERT INTO downtime_events (downtime_no, equipment_id, work_order_id, started_at, reason_code_id, reason_text, category, status, source, reporter, alarm_id)
           VALUES (%s,%s,%s,%s,%s,%s,%s,'OPEN',%s,%s,%s)""",
        (no, equipment_id, work_order_id, started_at or datetime.now(), reason_code_id, reason_text, category, source, reporter or "SYSTEM", alarm_id))
    downtime_id = cur.lastrowid
    if set_status:
        status = "MAINTENANCE" if code in MAINTENANCE_REASONS else "DOWN" if category == "UNPLANNED" else "IDLE"
        set_machine_status(cur, equipment_id, status, source, work_order_id, f"{no} {reason['reason_name'] if reason else reason_text or ''}".strip())
    return downtime_id


def close_downtime(cur, downtime_id, actor, ended_at=None, root_cause=None, action_taken=None, version=None, set_status=True, reason_code_id=None, reason_text=None):
    cur.execute("SELECT * FROM downtime_events WHERE id=%s FOR UPDATE", (downtime_id,))
    dt = cur.fetchone()
    if not dt:
        raise HTTPException(status_code=404, detail="Downtime event not found.")
    if dt["status"] != "OPEN":
        raise HTTPException(status_code=409, detail="The downtime event is already closed.")
    if version is not None and int(version) != int(dt["version"]):
        raise HTTPException(status_code=409, detail="This downtime event was changed by another user. Reload and try again.")
    end = ended_at or datetime.now()
    if end < dt["started_at"]:
        raise HTTPException(status_code=400, detail="End time is before the start time.")
    cur.execute("""UPDATE downtime_events SET status='CLOSED', ended_at=%s, root_cause=COALESCE(%s, root_cause), action_taken=COALESCE(%s, action_taken),
                   reason_code_id=COALESCE(%s, reason_code_id), reason_text=COALESCE(%s, reason_text), closed_by=%s, version=version+1 WHERE id=%s""",
                (end, root_cause, action_taken, reason_code_id, reason_text, actor or "SYSTEM", downtime_id))
    if set_status:
        set_machine_status(cur, dt["equipment_id"], status_after_stop(cur, dt["equipment_id"]), "MES", dt["work_order_id"], f"{dt['downtime_no']} closed")
    return dt


def close_open_downtime_for_wo(cur, work_order_id, actor, source="MES"):
    cur.execute("SELECT id FROM downtime_events WHERE work_order_id=%s AND status='OPEN' AND source=%s", (work_order_id, source))
    for row in cur.fetchall():
        close_downtime(cur, row["id"], actor, set_status=False)


# =========================================================
# ALARMS
# =========================================================


def _alarm_action(cur, alarm_id, action, from_status, to_status, actor, note=None):
    cur.execute("INSERT INTO machine_alarm_actions (alarm_id, action, from_status, to_status, actor, note) VALUES (%s,%s,%s,%s,%s,%s)",
                (alarm_id, action, from_status, to_status, actor or "SYSTEM", note))


def raise_alarm(cur, equipment_id, message, priority="MEDIUM", source="PLC", alarm_code=None, parameter_code=None,
                value=None, limit_value=None, actor=None, note=None):
    priority = (priority or "MEDIUM").upper()
    if priority not in PRIORITIES:
        raise HTTPException(status_code=400, detail="Unknown alarm priority.")
    if alarm_code and equipment_id:
        cur.execute(f"""SELECT id FROM machine_alarms WHERE equipment_id=%s AND alarm_code=%s AND status IN {ALARM_OPEN} LIMIT 1""",
                    (equipment_id, alarm_code))
        row = cur.fetchone()
        if row:
            cur.execute("UPDATE machine_alarms SET value=COALESCE(%s, value) WHERE id=%s", (value, row["id"]))
            return row["id"]
    wo = running_work_order(cur, equipment_id) if equipment_id else None
    no = _next_no(cur, "machine_alarms", "alarm_no", "AL")
    cur.execute(
        """INSERT INTO machine_alarms (alarm_no, equipment_id, alarm_code, message, priority, source, parameter_code, value, limit_value, work_order_id, note)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (no, equipment_id, alarm_code, message, priority, source, parameter_code, value, limit_value, wo["id"] if wo else None, note))
    alarm_id = cur.lastrowid
    _alarm_action(cur, alarm_id, "RAISE", None, "ACTIVE", actor or source, note)
    if priority == "CRITICAL":   # critical alarm -> maintenance request (Maintenance Management 6.2)
        from app.modules.maintenance.service import request_from_alarm
        try:
            request_from_alarm(cur, alarm_id)
            # The request is not committed yet (caller commits), so read it on this cursor and notify Maintenance now.
            cur.execute("""SELECT r.id, r.request_no, r.short_description, e.equipment_code FROM maintenance_requests r
                           LEFT JOIN equipment e ON e.id = %s WHERE r.alarm_id=%s ORDER BY r.id DESC LIMIT 1""", (equipment_id, alarm_id))
            req = cur.fetchone()
            if req:
                from app.modules.notifications.service import notify
                notify("MAINT_REQUESTED", f"{req['equipment_code'] or ''}: {req['short_description'] or message}".strip(": "),
                       ref_type="MAINT_REQUEST", ref_id=req["id"], ref_code=req["request_no"], actor="SYSTEM", severity="CRITICAL",
                       title="Cảnh báo nghiêm trọng, đã tạo yêu cầu bảo trì", link=f"/maintenance-management/requests?request={req['id']}")
        except Exception:  # never block the alarm itself
            pass
    return alarm_id


def auto_clear(cur, equipment_id, alarm_code, source):
    cur.execute(f"SELECT id, status FROM machine_alarms WHERE equipment_id=%s AND alarm_code=%s AND status IN {ALARM_OPEN}", (equipment_id, alarm_code))
    for row in cur.fetchall():
        cur.execute("UPDATE machine_alarms SET status='CLEARED', cleared_at=NOW(), version=version+1 WHERE id=%s", (row["id"],))
        _alarm_action(cur, row["id"], "CLEAR", row["status"], "CLEARED", source, "Back within limits")


def alarm_action(cur, alarm_id, action, actor, version, note=None, hours=None):
    cur.execute("SELECT * FROM machine_alarms WHERE id=%s FOR UPDATE", (alarm_id,))
    a = cur.fetchone()
    if not a:
        raise HTTPException(status_code=404, detail="Alarm not found.")
    if int(version) != int(a["version"]):
        raise HTTPException(status_code=409, detail="This alarm was changed by another user. Reload and try again.")
    s = a["status"]
    if action == "acknowledge":
        if s not in {"ACTIVE", "SHELVED"}:
            raise HTTPException(status_code=409, detail="Only active or shelved alarms can be acknowledged.")
        target, sets, params = "ACKNOWLEDGED", "acknowledged_at=NOW(), acknowledged_by=%s, shelved_until=NULL", (actor,)
    elif action == "shelve":
        if s not in {"ACTIVE", "ACKNOWLEDGED"}:
            raise HTTPException(status_code=409, detail="Only active or acknowledged alarms can be shelved.")
        if not note:
            raise HTTPException(status_code=400, detail="A reason is required to shelve an alarm.")
        target, sets, params = "SHELVED", "shelved_until=%s", (datetime.now() + timedelta(hours=hours or 4),)
    elif action == "clear":
        if s not in ALARM_OPEN:
            raise HTTPException(status_code=409, detail="The alarm is not open.")
        target, sets, params = "CLEARED", "cleared_at=NOW()", ()
    elif action == "close":
        if s == "CLOSED":
            raise HTTPException(status_code=409, detail="The alarm is already closed.")
        if s != "CLEARED" and not note:
            raise HTTPException(status_code=400, detail="A note is required to close an alarm that is not cleared.")
        target, sets, params = "CLOSED", "closed_at=NOW(), closed_by=%s, cleared_at=COALESCE(cleared_at, NOW())", (actor,)
    else:
        raise HTTPException(status_code=400, detail="Unknown alarm action.")
    cur.execute(f"UPDATE machine_alarms SET status=%s, {sets}, note=COALESCE(%s, note), version=version+1 WHERE id=%s",
                (target, *params, note, alarm_id))
    _alarm_action(cur, alarm_id, action.upper(), s, target, actor, note)


def unshelve_expired(cur):
    cur.execute("SELECT id FROM machine_alarms WHERE status='SHELVED' AND shelved_until IS NOT NULL AND shelved_until < NOW()")
    for row in cur.fetchall():
        cur.execute("UPDATE machine_alarms SET status='ACTIVE', shelved_until=NULL, version=version+1 WHERE id=%s", (row["id"],))
        _alarm_action(cur, row["id"], "UNSHELVE", "SHELVED", "ACTIVE", "SYSTEM", "Shelve time expired")


# =========================================================
# PARAMETER LIMITS + INGEST
# =========================================================


def parameter_limits(cur, equipment):
    """{code: {name, unit, min, target, max}} for the equipment now (standard of the running WO, else defaults)."""
    category = equipment["category"] if equipment["category"] in DEFAULT_PARAMETERS else "MACHINE"
    limits = {code: {"name": n, "unit": u, "min": lo, "target": t, "max": hi} for code, (n, u, lo, t, hi) in DEFAULT_PARAMETERS[category].items()}
    wo = running_work_order(cur, equipment["id"]) if category == "MACHINE" else None
    if wo:
        limits["CYCLE_TIME"].update(target=float(wo["cycle_time_sec"]), max=round(float(wo["cycle_time_sec"]) * 1.15, 2))
    cur.execute(
        """SELECT i.parameter_name, i.min_value, i.target_value, i.max_value, i.unit,
                  (r.product_id <=> %s) + (r.mold_id <=> %s) AS score
           FROM process_parameter_standard_items i JOIN process_parameter_standard_revisions r ON r.id = i.revision_id
           WHERE r.status IN ('ACTIVE','APPROVED','EFFECTIVE') AND (r.machine_type_id IS NULL OR r.machine_type_id=%s)
             AND (r.product_id IS NULL OR r.product_id <=> %s) AND (r.mold_id IS NULL OR r.mold_id <=> %s)
           ORDER BY score""",
        (wo["product_id"] if wo else None, wo["mold_id"] if wo else None, equipment["equipment_type_id"],
         wo["product_id"] if wo else None, wo["mold_id"] if wo else None))
    for row in cur.fetchall():  # most specific last wins
        code = param_code(row["parameter_name"])
        limits[code] = {"name": row["parameter_name"], "unit": row["unit"],
                        "min": float(row["min_value"]) if row["min_value"] is not None else None,
                        "target": float(row["target_value"]) if row["target_value"] is not None else None,
                        "max": float(row["max_value"]) if row["max_value"] is not None else None, "standard": True}
    return limits


def ingest(cur, payload, source):
    """One message from a gateway (or the simulator) for one equipment."""
    eq = get_equipment(cur, code=payload["equipment_code"])
    at = payload.get("timestamp") or datetime.now()
    limits = parameter_limits(cur, eq) if payload.get("readings") else {}
    stored = 0
    for r in payload.get("readings") or []:
        code = param_code(r.get("code") or r.get("name"))
        lim = limits.get(code, {})
        value = float(r["value"])
        cur.execute("""INSERT INTO machine_parameter_readings (equipment_id, parameter_code, parameter_name, value, unit, read_at, source)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""", (eq["id"], code, r.get("name") or lim.get("name") or code, value, r.get("unit") or lim.get("unit"), at, source))
        stored += 1
        lo, hi = lim.get("min"), lim.get("max")
        alarm_code = f"PARAM_{code}"
        if hi is not None and value > hi:
            over = (value - hi) / (abs(hi) or 1)
            raise_alarm(cur, eq["id"], f"{lim.get('name') or code} high: {value:g} {lim.get('unit') or ''} > {hi:g}".strip(),
                        "CRITICAL" if over > 0.1 else "HIGH", source, alarm_code, code, value, hi)
        elif lo is not None and value < lo:
            raise_alarm(cur, eq["id"], f"{lim.get('name') or code} low: {value:g} {lim.get('unit') or ''} < {lo:g}".strip(),
                        "MEDIUM", source, alarm_code, code, value, lo)
        else:
            auto_clear(cur, eq["id"], alarm_code, source)
    for c in payload.get("components") or []:
        cur.execute("""INSERT INTO machine_component_status (equipment_id, component_code, status, value, message, source) VALUES (%s,%s,%s,%s,%s,%s)
                       ON DUPLICATE KEY UPDATE status=VALUES(status), value=VALUES(value), message=VALUES(message), source=VALUES(source), updated_at=NOW()""",
                    (eq["id"], param_code(c["code"]), str(c.get("status") or "OK").upper(), c.get("value"), c.get("message"), source))
        comp_alarm = f"COMP_{param_code(c['code'])}"
        if str(c.get("status") or "OK").upper() in {"ERROR", "FAULT"}:
            raise_alarm(cur, eq["id"], c.get("message") or f"{c['code']} fault", "HIGH", source, comp_alarm)
        elif str(c.get("status") or "OK").upper() == "OK":
            auto_clear(cur, eq["id"], comp_alarm, source)
    for a in payload.get("alarms") or []:
        raise_alarm(cur, eq["id"], a["message"], a.get("priority", "MEDIUM"), source, a.get("code"))
    status = payload.get("status")
    if status and status.upper() != eq["operational_status"]:
        status = status.upper()
        if status == "DOWN":
            open_downtime(cur, eq["id"], source, reason_text=payload.get("reason") or "Machine stopped (signal)", source=source, set_status=False)
        if eq["operational_status"] == "DOWN" and status in {"RUNNING", "IDLE"}:
            cur.execute("SELECT id FROM downtime_events WHERE equipment_id=%s AND status='OPEN' AND source=%s", (eq["id"], source))
            for row in cur.fetchall():
                close_downtime(cur, row["id"], source, set_status=False)
        set_machine_status(cur, eq["id"], status, source)
    return {"equipment": eq["equipment_code"], "readings": stored}
