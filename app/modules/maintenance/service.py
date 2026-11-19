"""Maintenance Management core (screens 6.1 - 6.7).

Flow: Maintenance Request (NEW -> IN_REVIEW -> APPROVED / REJECTED) -> Maintenance Work Order
(RELEASED -> ASSIGNED -> IN_PROGRESS <-> PAUSED -> VERIFICATION -> COMPLETED -> CLOSED, or CANCELLED before start).
Maintenance Plans (PM / inspection / calibration by days or shots) create Work Orders; AUTO plans follow the
Mold Master PM intervals and the Production Tool calibration settings.

Effects on the asset:
- Machine / equipment: Start opens a downtime event (DT-PM planned -> machine MAINTENANCE, DT-BRK unplanned -> DOWN)
  or links the open one; Submit (work done) closes it. A running production Work Order on the machine blocks Start.
- Mold: Start sends the mold to maintenance (PM / inspection) or repair (corrective / breakdown) in Mold Management;
  Verify completes it there (last PM date / shot updated for PM). A mounted mold must be removed first.
- Production tool: Start -> IN_MAINTENANCE; Verify -> AVAILABLE, calibration PASS moves the calibration dates,
  FAIL -> OUT_OF_SERVICE.
Verify also completes the request and moves the plan to its next due date.

KPIs:
    MTTR       = average repair time (start -> finish minus pauses) of corrective / breakdown WOs, hours
    MTBF       = machine RUNNING hours / number of corrective + breakdown WOs on machines and equipment, hours
    PM compl.  = PM / inspection / calibration WOs due in the period and completed on or before the due date / all due
    Cost       = spare parts (qty x unit cost) + labor (hours x rate) + outsourcing + tools + others
"""

import json
import math
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import HTTPException

from app.core.employee_names import attach_employee_names
from app.modules.machine import service as machine
from app.modules.mold import service as mold_service

ASSET_TYPES = ["MACHINE", "EQUIPMENT", "MOLD", "TOOL"]
REQUEST_TYPES = ["CORRECTIVE", "PREVENTIVE", "INSPECTION", "CALIBRATION", "OTHER"]
PRIORITIES = ["HIGH", "MEDIUM", "LOW"]
REQUEST_STATUSES = ["NEW", "IN_REVIEW", "APPROVED", "REJECTED", "COMPLETED", "CANCELLED"]
SOURCES = ["MANUAL", "ALARM", "PM_PLAN", "IOT", "SHOT_COUNTER", "QUALITY"]
WO_TYPES = ["PM", "CORRECTIVE", "BREAKDOWN", "INSPECTION", "CALIBRATION"]
PREVENTIVE_TYPES = {"PM", "INSPECTION", "CALIBRATION"}
REPAIR_TYPES = {"CORRECTIVE", "BREAKDOWN"}
WO_STATUSES = ["RELEASED", "ASSIGNED", "IN_PROGRESS", "PAUSED", "VERIFICATION", "COMPLETED", "CLOSED", "CANCELLED"]
OPEN_WO = {"RELEASED", "ASSIGNED", "IN_PROGRESS", "PAUSED", "VERIFICATION"}
DONE_WO = {"COMPLETED", "CLOSED"}
PLAN_TYPES = ["DAILY", "WEEKLY", "MONTHLY", "QUARTERLY", "YEARLY", "SHUTDOWN", "SHOT"]
STAGES = {
    "MOLD": ["DISASSEMBLY", "INSPECTION", "CLEANING_REPAIR", "ASSEMBLY", "TESTING"],
    "TOOL": ["INSPECTION", "MAINTENANCE_CALIBRATION", "VERIFICATION"],
}
MOLD_COMPONENTS = ["CORE", "CAVITY", "INSERT", "SLIDE", "LIFTER", "EJECTOR", "COOLING_CHANNEL", "HOT_RUNNER", "VENT"]
COMPONENT_CONDITIONS = ["NOT_CHECKED", "GOOD", "MONITOR", "ATTENTION", "CRITICAL"]
COMPONENT_ACTIONS = ["NONE", "CLEAN", "ADJUST", "REPAIR", "REPLACE"]
LABOR_TYPES = ["REPAIR", "PM", "INSPECTION", "CALIBRATION", "OUTSOURCE"]
REQUEST_TO_WO = {"CORRECTIVE": "CORRECTIVE", "PREVENTIVE": "PM", "INSPECTION": "INSPECTION", "CALIBRATION": "CALIBRATION", "OTHER": "CORRECTIVE"}
WO_PREFIX = {"MACHINE": "MWO", "EQUIPMENT": "MWO", "MOLD": "MO", "TOOL": "TWO"}
DEFAULT_CHECKLIST = {
    ("MOLD", "PM"): ["Clean parting line and vents", "Check cooling channels (flow / leakage)", "Inspect ejector pins and return springs",
                     "Lubricate guide pins, bushes and slides", "Check hot runner heaters and thermocouples", "Test run and first-off check"],
    ("MOLD", "REPAIR"): ["Disassemble and locate the damage", "Repair or replace the damaged part", "Reassemble and check fit",
                         "Test run and first-off check"],
    ("MACHINE", "PM"): ["Check hydraulic oil level and leaks", "Lubricate toggle / tie bars", "Inspect heater bands and thermocouples",
                        "Check safety door interlocks and emergency stop", "Clean oil / air filters"],
    ("EQUIPMENT", "PM"): ["Visual inspection and cleaning", "Check motor / pump current", "Check filters, belts and hoses", "Functional test"],
    ("TOOL", "CALIBRATION"): ["Visual inspection and cleaning", "Calibrate against the reference standard", "Record measured values",
                              "Attach calibration label"],
    ("TOOL", "PM"): ["Visual inspection and cleaning", "Check wear and tightness", "Lubricate moving parts", "Functional test"],
    ("*", "REPAIR"): ["Diagnose the failure", "Repair or replace parts", "Test after repair"],
    ("*", "INSPECTION"): ["Visual inspection", "Measure key parameters", "Record findings"],
}


def _now():
    return datetime.now().replace(microsecond=0)


def _setting(cur, key, default):
    cur.execute("SELECT setting_value FROM system_settings WHERE setting_key=%s", (key,))
    row = cur.fetchone()
    return row["setting_value"] if row and row["setting_value"] not in (None, "") else default


def labor_rate(cur):
    try:
        return float(_setting(cur, "maintenance.labor_rate_per_hour", 120000))
    except (TypeError, ValueError):
        return 120000.0


def log(cur, owner_type, owner_id, action, actor, from_status=None, to_status=None, note=None, at=None):
    cur.execute("""INSERT INTO maintenance_events (owner_type, owner_id, action, from_status, to_status, note, actor, event_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""", (owner_type, owner_id, action, from_status, to_status, note, actor or "SYSTEM", at or _now()))


def events_of(cur, owner_type, owner_id):
    cur.execute("SELECT * FROM maintenance_events WHERE owner_type=%s AND owner_id=%s ORDER BY event_at, id", (owner_type, owner_id))
    return cur.fetchall()


# =========================================================
# ASSETS
# =========================================================


def asset_key(asset_type, asset_id):
    return ("EQ" if asset_type in ("MACHINE", "EQUIPMENT") else asset_type, int(asset_id))


def asset_map(cur, pairs):
    """{asset_key: info} for (asset_type, asset_id) pairs; info has code, name, image, model, status, location..."""
    ids = defaultdict(set)
    for t, i in pairs:
        ids[asset_key(t, i)[0]].add(int(i))
    out = {}
    if ids["EQ"]:
        marks = ",".join(["%s"] * len(ids["EQ"]))
        cur.execute(f"""SELECT e.id, e.equipment_code AS code, e.equipment_name AS name, e.image_url, e.model, e.serial_number, e.manufacturer,
                               e.operational_status AS status, t.category, t.type_name, fn.name AS location
                        FROM equipment e JOIN equipment_types t ON t.id=e.equipment_type_id LEFT JOIN factory_structure_nodes fn ON fn.id=e.factory_node_id
                        WHERE e.id IN ({marks})""", tuple(ids["EQ"]))
        for r in cur.fetchall():
            r["asset_type"] = "MACHINE" if r["category"] == "MACHINE" else "EQUIPMENT"
            r["sub"] = r["type_name"]
            out[("EQ", r["id"])] = r
    if ids["MOLD"]:
        marks = ",".join(["%s"] * len(ids["MOLD"]))
        cur.execute(f"""SELECT m.id, m.mold_code AS code, m.mold_name AS name, m.image_url, m.mold_type AS model, m.mold_group, m.status, m.cavity_count,
                               m.current_shot, m.design_shot, m.current_machine_id, e.equipment_code AS machine_code, n.node_name AS location_name
                        FROM molds m LEFT JOIN equipment e ON e.id=m.current_machine_id LEFT JOIN mold_location_nodes n ON n.id=m.current_location_id
                        WHERE m.id IN ({marks})""", tuple(ids["MOLD"]))
        for r in cur.fetchall():
            r["asset_type"], r["sub"], r["location"] = "MOLD", r["model"], r["machine_code"] or r["location_name"]
            out[("MOLD", r["id"])] = r
    if ids["TOOL"]:
        marks = ",".join(["%s"] * len(ids["TOOL"]))
        cur.execute(f"""SELECT t.id, t.tool_code AS code, t.tool_name AS name, t.image_url, t.model, t.serial_number, t.manufacturer, t.tool_type, t.tool_category,
                               t.status, t.calibration_required, t.calibration_due_date, t.last_calibration_date, t.calibration_interval_days, n.name AS location
                        FROM production_tools t LEFT JOIN production_tool_nodes n ON n.id=t.production_tool_node_id WHERE t.id IN ({marks})""", tuple(ids["TOOL"]))
        for r in cur.fetchall():
            r["asset_type"], r["sub"] = "TOOL", r["tool_type"]
            out[("TOOL", r["id"])] = r
    return out


def resolve_asset(cur, asset_type, asset_id):
    asset_type = (asset_type or "").upper()
    if asset_type not in ASSET_TYPES:
        raise HTTPException(status_code=400, detail="Unknown asset type.")
    info = asset_map(cur, [(asset_type, asset_id)]).get(asset_key(asset_type, asset_id))
    if not info:
        raise HTTPException(status_code=404, detail="Asset not found.")
    return info["asset_type"], info


def all_assets(cur):
    cur.execute("""SELECT e.id, e.equipment_code AS code, e.equipment_name AS name, t.category, e.operational_status AS status FROM equipment e
                   JOIN equipment_types t ON t.id=e.equipment_type_id WHERE e.asset_status='ACTIVE' ORDER BY t.category='MACHINE' DESC, e.equipment_code""")
    rows = [{"asset_type": "MACHINE" if r["category"] == "MACHINE" else "EQUIPMENT", "id": r["id"], "code": r["code"], "name": r["name"], "status": r["status"]} for r in cur.fetchall()]
    cur.execute("SELECT id, mold_code AS code, mold_name AS name, status FROM molds WHERE is_active=1 ORDER BY mold_code")
    rows += [{"asset_type": "MOLD", **r} for r in cur.fetchall()]
    cur.execute("SELECT id, tool_code AS code, tool_name AS name, status FROM production_tools WHERE status<>'INACTIVE' ORDER BY tool_code")
    rows += [{"asset_type": "TOOL", **r} for r in cur.fetchall()]
    return rows


def technicians(cur):
    # name from the account itself, else from the HR directory when this server has it
    try:
        cur.execute("SELECT employee_code, full_name FROM mes_users WHERE is_active=1 ORDER BY employee_code")
    except Exception:  # local-account columns not created yet
        cur.execute("SELECT employee_code, NULL AS full_name FROM mes_users WHERE is_active=1 ORDER BY employee_code")
    rows = attach_employee_names(cur, cur.fetchall(), ["employee_code"])
    for r in rows:
        r["full_name"] = r["full_name"] or r.get("employee_code_name")
    clean = lambda v: str(v).encode("utf-8", "replace").decode("utf-8").replace("?", "").strip() if v else v
    names = {f"{clean(r['full_name'])} ({r['employee_code']})" if r["full_name"] else r["employee_code"] for r in rows}
    cur.execute("SELECT DISTINCT technician AS t FROM maintenance_work_orders WHERE technician IS NOT NULL UNION SELECT DISTINCT technician FROM maintenance_plans WHERE technician IS NOT NULL")
    names |= {r["t"] for r in cur.fetchall() if r["t"]}
    return sorted(names)


# =========================================================
# REQUESTS
# =========================================================


def get_request(cur, request_id, for_update=False):
    cur.execute("SELECT * FROM maintenance_requests WHERE id=%s" + (" FOR UPDATE" if for_update else ""), (request_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Maintenance request not found.")
    return row


def _check_version(row, version, what="record"):
    if version is not None and int(version) != int(row["version"]):
        raise HTTPException(status_code=409, detail=f"This {what} was changed by someone else. Reload and try again.")


def create_request(cur, data, actor, source="MANUAL"):
    asset_type, _ = resolve_asset(cur, data["asset_type"], data["asset_id"])
    no = machine._next_no(cur, "maintenance_requests", "request_no", "MR")
    cur.execute("""INSERT INTO maintenance_requests (request_no, asset_type, asset_id, request_type, priority, status, source, short_description, description,
                   requested_by, department, required_date, alarm_id, downtime_event_id, production_wo_id, created_by)
                   VALUES (%s,%s,%s,%s,%s,'NEW',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (no, asset_type, data["asset_id"], data.get("request_type") or "CORRECTIVE", data.get("priority") or "MEDIUM", source,
                 data["short_description"].strip(), data.get("description"), data.get("requested_by") or actor, data.get("department"), data.get("required_date"),
                 data.get("alarm_id"), data.get("downtime_event_id"), data.get("production_wo_id"), actor))
    rid = cur.lastrowid
    log(cur, "REQ", rid, "CREATE", actor, None, "NEW", data["short_description"].strip())
    return rid


def update_request(cur, request_id, data, actor):
    req = get_request(cur, request_id, True)
    _check_version(req, data.get("version"), "request")
    if req["status"] not in ("NEW", "IN_REVIEW"):
        raise HTTPException(status_code=409, detail="Only new or in-review requests can be edited.")
    asset_type, _ = resolve_asset(cur, data["asset_type"], data["asset_id"])
    cur.execute("""UPDATE maintenance_requests SET asset_type=%s, asset_id=%s, request_type=%s, priority=%s, short_description=%s, description=%s,
                   department=%s, required_date=%s, updated_by=%s, version=version+1 WHERE id=%s""",
                (asset_type, data["asset_id"], data["request_type"], data["priority"], data["short_description"].strip(), data.get("description"),
                 data.get("department"), data.get("required_date"), actor, request_id))
    log(cur, "REQ", request_id, "EDIT", actor, req["status"], req["status"])


REQUEST_ACTIONS = {
    "REVIEW": ({"NEW"}, "IN_REVIEW"),
    "APPROVE": ({"NEW", "IN_REVIEW"}, "APPROVED"),
    "REJECT": ({"NEW", "IN_REVIEW"}, "REJECTED"),
    "CANCEL": ({"NEW", "IN_REVIEW", "APPROVED"}, "CANCELLED"),
}


def request_action(cur, request_id, action, actor, note=None, version=None):
    action = (action or "").upper()
    if action not in REQUEST_ACTIONS:
        raise HTTPException(status_code=400, detail="Unknown action.")
    req = get_request(cur, request_id, True)
    _check_version(req, version, "request")
    allowed, to = REQUEST_ACTIONS[action]
    if req["status"] not in allowed:
        raise HTTPException(status_code=409, detail=f"Action not allowed while the request is {req['status']}.")
    if action in ("REJECT", "CANCEL") and not (note or "").strip():
        raise HTTPException(status_code=400, detail="A reason is required.")
    if action == "CANCEL" and req["work_order_id"]:
        raise HTTPException(status_code=409, detail="A work order already exists for this request. Cancel the work order instead.")
    extra = {"REVIEW": ", reviewed_by=%s", "APPROVE": ", approved_by=%s, approved_at=NOW()", "REJECT": ", reject_reason=%s", "CANCEL": ", reject_reason=%s"}[action]
    param = note.strip() if action in ("REJECT", "CANCEL") else actor
    cur.execute(f"UPDATE maintenance_requests SET status=%s{extra}, updated_by=%s, version=version+1 WHERE id=%s", (to, param, actor, request_id))
    log(cur, "REQ", request_id, action, actor, req["status"], to, note)


def wo_from_request(cur, request_id, data, actor):
    req = get_request(cur, request_id, True)
    if req["work_order_id"]:
        raise HTTPException(status_code=409, detail="A work order already exists for this request.")
    if req["status"] in ("NEW", "IN_REVIEW"):
        request_action(cur, request_id, "APPROVE", actor, "Approved when creating the work order")
    elif req["status"] != "APPROVED":
        raise HTTPException(status_code=409, detail="Only approved requests can create a work order.")
    wo_id = create_wo(cur, {"asset_type": req["asset_type"], "asset_id": req["asset_id"], "wo_type": data.get("wo_type") or REQUEST_TO_WO[req["request_type"]],
                            "priority": data.get("priority") or req["priority"], "task_name": data.get("task_name") or req["short_description"],
                            "description": data.get("description") or req["description"], "request_id": request_id, "requested_by": req["requested_by"],
                            "technician": data.get("technician"), "assistant": data.get("assistant"), "planned_start": data.get("planned_start"),
                            "planned_finish": data.get("planned_finish") or req["required_date"], "est_minutes": data.get("est_minutes"),
                            "est_downtime_minutes": data.get("est_downtime_minutes")}, actor)
    cur.execute("UPDATE maintenance_requests SET work_order_id=%s, updated_by=%s, version=version+1 WHERE id=%s", (wo_id, actor, request_id))
    cur.execute("SELECT wo_no FROM maintenance_work_orders WHERE id=%s", (wo_id,))
    log(cur, "REQ", request_id, "CREATE_WO", actor, "APPROVED", "APPROVED", cur.fetchone()["wo_no"])
    return wo_id


def request_from_alarm(cur, alarm_id):
    """CRITICAL machine alarm -> maintenance request (source ALARM), once per alarm (setting maintenance.auto_request_critical_alarm)."""
    if str(_setting(cur, "maintenance.auto_request_critical_alarm", "1")) != "1":
        return None
    cur.execute("SELECT id FROM maintenance_requests WHERE alarm_id=%s LIMIT 1", (alarm_id,))
    if cur.fetchone():
        return None
    cur.execute("SELECT id, alarm_no, equipment_id, message FROM machine_alarms WHERE id=%s", (alarm_id,))
    al = cur.fetchone()
    if not al or not al["equipment_id"]:
        return None
    return create_request(cur, {"asset_type": "MACHINE", "asset_id": al["equipment_id"], "request_type": "CORRECTIVE", "priority": "HIGH",
                                "short_description": f"{al['alarm_no']}: {al['message']}"[:255], "alarm_id": alarm_id, "requested_by": "SYSTEM"}, "SYSTEM", source="ALARM")


# =========================================================
# PLANS
# =========================================================


def plan_type_of(unit, interval):
    if unit == "SHOT":
        return "SHOT"
    interval = int(interval or 0)
    return "DAILY" if interval <= 1 else "WEEKLY" if interval <= 7 else "MONTHLY" if interval <= 31 else "QUARTERLY" if interval <= 92 else "YEARLY"


def get_plan(cur, plan_id, for_update=False):
    cur.execute("SELECT * FROM maintenance_plans WHERE id=%s" + (" FOR UPDATE" if for_update else ""), (plan_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Maintenance plan not found.")
    for k in ("checklist", "spare_parts"):
        if isinstance(row.get(k), str):
            row[k] = json.loads(row[k])
    return row


def save_plan(cur, data, actor, plan_id=None):
    asset_type, _ = resolve_asset(cur, data["asset_type"], data["asset_id"])
    unit = (data.get("frequency_unit") or "DAY").upper()
    if unit not in ("DAY", "SHOT"):
        raise HTTPException(status_code=400, detail="Frequency unit must be DAY or SHOT.")
    if unit == "SHOT" and asset_type != "MOLD":
        raise HTTPException(status_code=400, detail="Shot-based plans are only for molds.")
    interval = int(data.get("interval_value") or 0)
    if interval <= 0:
        raise HTTPException(status_code=400, detail="Interval must be greater than zero.")
    plan_type = data.get("plan_type") or plan_type_of(unit, interval)
    vals = (asset_type, data["asset_id"], data["task_name"].strip(), data.get("wo_type") or "PM", plan_type, unit, interval, data.get("next_due_date"),
            data.get("next_due_shot"), data.get("priority") or "MEDIUM", data.get("technician"), data.get("est_minutes"), data.get("description"),
            json.dumps(data.get("checklist") or []), json.dumps(data.get("spare_parts") or []))
    if plan_id is None:
        no = machine._next_no(cur, "maintenance_plans", "plan_no", "PM")
        cur.execute("""INSERT INTO maintenance_plans (asset_type, asset_id, task_name, wo_type, plan_type, frequency_unit, interval_value, next_due_date, next_due_shot,
                       priority, technician, est_minutes, description, checklist, spare_parts, plan_no, source, created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'MANUAL',%s)""", vals + (no, actor))
        pid = cur.lastrowid
        log(cur, "PLAN", pid, "CREATE", actor, None, "ACTIVE")
        return pid
    plan = get_plan(cur, plan_id, True)
    _check_version(plan, data.get("version"), "plan")
    cur.execute("""UPDATE maintenance_plans SET asset_type=%s, asset_id=%s, task_name=%s, wo_type=%s, plan_type=%s, frequency_unit=%s, interval_value=%s,
                   next_due_date=%s, next_due_shot=%s, priority=%s, technician=%s, est_minutes=%s, description=%s, checklist=%s, spare_parts=%s,
                   updated_by=%s, version=version+1 WHERE id=%s""", vals + (actor, plan_id))
    log(cur, "PLAN", plan_id, "EDIT", actor)
    return plan_id


def sync_auto_plans(cur):
    """Upsert AUTO plans from Mold Master PM intervals and Production Tool calibration settings; deactivate the ones no longer needed."""
    keep = set()
    molds = mold_service.load_molds(cur)
    for m in molds:
        if m["status"] in mold_service.OUT_OF_SERVICE or not (m["pm_interval_shot"] or m["pm_interval_days"]):
            continue
        unit = "SHOT" if m["pm_interval_shot"] else "DAY"
        interval = int(m["pm_interval_shot"] or m["pm_interval_days"])
        due = m["pm_due_date"]
        cur.execute("SELECT id FROM maintenance_plans WHERE source='AUTO_MOLD' AND asset_type='MOLD' AND asset_id=%s", (m["id"],))
        row = cur.fetchone()
        name = f"Mold PM - {m['mold_code']}" + (f" (every {interval:,} shots)" if unit == "SHOT" else f" (every {interval} days)")
        if row:
            cur.execute("""UPDATE maintenance_plans SET frequency_unit=%s, interval_value=%s, plan_type=%s, next_due_date=%s, next_due_shot=%s, task_name=%s,
                           status='ACTIVE' WHERE id=%s""", (unit, interval, plan_type_of(unit, interval), due, m["next_pm_shot"], name, row["id"]))
            keep.add(row["id"])
        else:
            no = machine._next_no(cur, "maintenance_plans", "plan_no", "PM")
            cur.execute("""INSERT INTO maintenance_plans (plan_no, asset_type, asset_id, task_name, wo_type, plan_type, frequency_unit, interval_value, next_due_date,
                           next_due_shot, priority, est_minutes, checklist, source, created_by) VALUES (%s,'MOLD',%s,%s,'PM',%s,%s,%s,%s,%s,'MEDIUM',120,%s,'AUTO_MOLD','SYSTEM')""",
                        (no, m["id"], name, plan_type_of(unit, interval), unit, interval, due, m["next_pm_shot"], json.dumps(DEFAULT_CHECKLIST[("MOLD", "PM")])))
            keep.add(cur.lastrowid)
    cur.execute("""SELECT id, tool_code, calibration_due_date, calibration_interval_days FROM production_tools
                   WHERE calibration_required=1 AND status NOT IN ('INACTIVE') AND calibration_interval_days IS NOT NULL""")
    for t in cur.fetchall():
        interval = int(t["calibration_interval_days"] or 365)
        name = f"Calibration - {t['tool_code']} (every {interval} days)"
        cur.execute("SELECT id FROM maintenance_plans WHERE source='AUTO_CALIBRATION' AND asset_type='TOOL' AND asset_id=%s", (t["id"],))
        row = cur.fetchone()
        if row:
            cur.execute("""UPDATE maintenance_plans SET interval_value=%s, plan_type=%s, next_due_date=%s, task_name=%s, status='ACTIVE' WHERE id=%s""",
                        (interval, plan_type_of("DAY", interval), t["calibration_due_date"], name, row["id"]))
            keep.add(row["id"])
        else:
            no = machine._next_no(cur, "maintenance_plans", "plan_no", "PM")
            cur.execute("""INSERT INTO maintenance_plans (plan_no, asset_type, asset_id, task_name, wo_type, plan_type, frequency_unit, interval_value, next_due_date,
                           priority, est_minutes, checklist, source, created_by) VALUES (%s,'TOOL',%s,%s,'CALIBRATION',%s,'DAY',%s,%s,'MEDIUM',60,%s,'AUTO_CALIBRATION','SYSTEM')""",
                        (no, t["id"], name, plan_type_of("DAY", interval), interval, t["calibration_due_date"], json.dumps(DEFAULT_CHECKLIST[("TOOL", "CALIBRATION")])))
            keep.add(cur.lastrowid)
    cur.execute("SELECT id FROM maintenance_plans WHERE source IN ('AUTO_MOLD','AUTO_CALIBRATION') AND status='ACTIVE'")
    for r in cur.fetchall():
        if r["id"] not in keep:
            cur.execute("UPDATE maintenance_plans SET status='INACTIVE' WHERE id=%s", (r["id"],))


def plan_status(plan, open_wo, today=None):
    today = today or date.today()
    if plan["status"] != "ACTIVE":
        return "INACTIVE"
    if open_wo:
        return "IN_PROGRESS" if open_wo["status"] in ("IN_PROGRESS", "PAUSED", "VERIFICATION") else "SCHEDULED"
    due = plan["next_due_date"]
    if due and due < today:
        return "OVERDUE"
    if due and (due - today).days <= 7:
        return "UPCOMING"
    return "PLANNED"


def wo_from_plan(cur, plan_id, data, actor):
    plan = get_plan(cur, plan_id, True)
    if plan["status"] != "ACTIVE":
        raise HTTPException(status_code=409, detail="The plan is inactive.")
    cur.execute(f"SELECT wo_no FROM maintenance_work_orders WHERE plan_id=%s AND status IN ({','.join(['%s'] * len(OPEN_WO))}) LIMIT 1", (plan_id, *OPEN_WO))
    open_wo = cur.fetchone()
    if open_wo:
        raise HTTPException(status_code=409, detail=f"Work order {open_wo['wo_no']} is already open for this plan.")
    due = plan["next_due_date"] or date.today()
    start = data.get("planned_start") or datetime.combine(max(due, date.today()), datetime.min.time()).replace(hour=8)
    wo_id = create_wo(cur, {"asset_type": plan["asset_type"], "asset_id": plan["asset_id"], "wo_type": plan["wo_type"], "priority": data.get("priority") or plan["priority"],
                            "task_name": plan["task_name"], "description": plan["description"], "plan_id": plan_id, "requested_by": "Maintenance Planning",
                            "technician": data.get("technician") or plan["technician"], "planned_start": start,
                            "planned_finish": data.get("planned_finish") or (start + timedelta(minutes=int(plan["est_minutes"] or 120)) if isinstance(start, datetime) else None),
                            "due_date": due, "est_minutes": plan["est_minutes"], "checklist": plan["checklist"] or None,
                            "parts": [{"part_code": p.get("part_code"), "part_name": p.get("part_name") or p.get("name"), "qty": p.get("qty") or 1, "unit": p.get("unit"),
                                       "unit_cost": p.get("unit_cost") or 0} for p in (plan["spare_parts"] or []) if p.get("part_name") or p.get("name")]}, actor)
    cur.execute("SELECT wo_no FROM maintenance_work_orders WHERE id=%s", (wo_id,))
    log(cur, "PLAN", plan_id, "CREATE_WO", actor, note=cur.fetchone()["wo_no"])
    return wo_id


def _advance_plan(cur, wo, actor):
    if not wo["plan_id"]:
        return
    plan = get_plan(cur, wo["plan_id"], True)
    if plan["source"] in ("AUTO_MOLD", "AUTO_CALIBRATION"):
        cur.execute("UPDATE maintenance_plans SET last_done_at=NOW() WHERE id=%s", (plan["id"],))   # next due follows the asset (sync)
    elif plan["frequency_unit"] == "SHOT":
        cur.execute("SELECT current_shot FROM molds WHERE id=%s", (plan["asset_id"],))
        shot = int((cur.fetchone() or {}).get("current_shot") or 0)
        cur.execute("UPDATE maintenance_plans SET last_done_at=NOW(), next_due_shot=%s WHERE id=%s", (shot + int(plan["interval_value"]), plan["id"]))
    else:
        nxt = date.today() + timedelta(days=int(plan["interval_value"]))
        cur.execute("UPDATE maintenance_plans SET last_done_at=NOW(), next_due_date=%s WHERE id=%s", (nxt, plan["id"]))
    log(cur, "PLAN", plan["id"], "DONE", actor, note=wo["wo_no"])


# =========================================================
# WORK ORDERS
# =========================================================


def get_wo(cur, wo_id, for_update=False):
    cur.execute("SELECT * FROM maintenance_work_orders WHERE id=%s" + (" FOR UPDATE" if for_update else ""), (wo_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Maintenance work order not found.")
    return row


def default_checklist(asset_type, wo_type):
    kind = "REPAIR" if wo_type in REPAIR_TYPES else "PM" if wo_type == "PM" else wo_type
    return DEFAULT_CHECKLIST.get((asset_type, kind)) or DEFAULT_CHECKLIST.get(("*", kind)) or DEFAULT_CHECKLIST.get((asset_type, "PM")) or DEFAULT_CHECKLIST[("*", "INSPECTION")]


def create_wo(cur, data, actor):
    asset_type, info = resolve_asset(cur, data["asset_type"], data["asset_id"])
    wo_type = (data.get("wo_type") or "CORRECTIVE").upper()
    if wo_type not in WO_TYPES:
        raise HTTPException(status_code=400, detail="Unknown work order type.")
    if wo_type == "CALIBRATION" and asset_type != "TOOL":
        raise HTTPException(status_code=400, detail="Calibration work orders are for production tools.")
    tech = (data.get("technician") or "").strip() or None
    no = machine._next_no(cur, "maintenance_work_orders", "wo_no", WO_PREFIX[asset_type])
    due = data.get("due_date") or (data["planned_finish"].date() if isinstance(data.get("planned_finish"), datetime) else None)
    cur.execute("""INSERT INTO maintenance_work_orders (wo_no, asset_type, asset_id, wo_type, priority, status, task_name, description, plan_id, request_id, technician,
                   assistant, requested_by, planned_start, planned_finish, due_date, est_minutes, est_downtime_minutes, outsourcing_cost, tool_cost, other_cost, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (no, asset_type, data["asset_id"], wo_type, data.get("priority") or "MEDIUM", "ASSIGNED" if tech else "RELEASED", data["task_name"].strip(),
                 data.get("description"), data.get("plan_id"), data.get("request_id"), tech, data.get("assistant"), data.get("requested_by") or actor,
                 data.get("planned_start"), data.get("planned_finish"), due, data.get("est_minutes"), data.get("est_downtime_minutes"),
                 data.get("outsourcing_cost") or 0, data.get("tool_cost") or 0, data.get("other_cost") or 0, actor))
    wo_id = cur.lastrowid
    items = data.get("checklist") or default_checklist(asset_type, wo_type)
    for i, item in enumerate(items, 1):
        cur.execute("INSERT INTO maintenance_wo_checklist (wo_id, seq, item) VALUES (%s,%s,%s)", (wo_id, i, str(item)[:500]))
    for p in data.get("parts") or []:
        cur.execute("INSERT INTO maintenance_wo_parts (wo_id, part_code, part_name, qty, unit, unit_cost, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                    (wo_id, p.get("part_code"), p["part_name"], p.get("qty") or 1, p.get("unit"), p.get("unit_cost") or 0, actor))
    if asset_type == "MOLD":
        for c in MOLD_COMPONENTS:
            cur.execute("INSERT INTO maintenance_wo_components (wo_id, component, condition_code, action_code) VALUES (%s,%s,'NOT_CHECKED','NONE')", (wo_id, c))
    log(cur, "WO", wo_id, "CREATE", actor, None, "ASSIGNED" if tech else "RELEASED", f"{info['code']} · {data['task_name'].strip()}")
    return wo_id


def update_wo(cur, wo_id, data, actor):
    wo = get_wo(cur, wo_id, True)
    _check_version(wo, data.get("version"), "work order")
    if wo["status"] not in OPEN_WO:
        raise HTTPException(status_code=409, detail="Completed, closed or cancelled work orders cannot be edited.")
    tech = (data.get("technician") or "").strip() or None
    status = wo["status"]
    if status in ("RELEASED", "ASSIGNED"):
        status = "ASSIGNED" if tech else "RELEASED"
    cur.execute("""UPDATE maintenance_work_orders SET priority=%s, task_name=%s, description=%s, technician=%s, assistant=%s, planned_start=%s, planned_finish=%s,
                   due_date=COALESCE(%s, due_date), est_minutes=%s, est_downtime_minutes=%s, outsourcing_cost=%s, tool_cost=%s, other_cost=%s, result=%s, root_cause=%s,
                   calibration_result=%s, calibration_value=%s, status=%s, updated_by=%s, version=version+1 WHERE id=%s""",
                (data.get("priority") or wo["priority"], (data.get("task_name") or wo["task_name"]).strip(), data.get("description"), tech, data.get("assistant"),
                 data.get("planned_start"), data.get("planned_finish"), data.get("due_date"), data.get("est_minutes"), data.get("est_downtime_minutes"),
                 data.get("outsourcing_cost") or 0, data.get("tool_cost") or 0, data.get("other_cost") or 0, data.get("result"), data.get("root_cause"),
                 data.get("calibration_result"), data.get("calibration_value"), status, actor, wo_id))
    log(cur, "WO", wo_id, "EDIT", actor, wo["status"], status)


def _reason_id(cur, code):
    cur.execute("SELECT id FROM reason_codes WHERE reason_code=%s", (code,))
    row = cur.fetchone()
    return row["id"] if row else None


def _start_asset(cur, wo, actor):
    t = wo["asset_type"]
    if t in ("MACHINE", "EQUIPMENT"):
        run = machine.running_work_order(cur, wo["asset_id"])
        if run:
            raise HTTPException(status_code=409, detail=f"Production work order {run['wo_no']} is running on this machine. Hold or end it first.")
        cur.execute("SELECT id FROM downtime_events WHERE equipment_id=%s AND status='OPEN' LIMIT 1", (wo["asset_id"],))
        row = cur.fetchone()
        if row:
            dt_id = row["id"]
        else:
            code = "DT-BRK" if wo["wo_type"] in REPAIR_TYPES else "DT-PM"
            dt_id = machine.open_downtime(cur, wo["asset_id"], actor, _reason_id(cur, code), f"{wo['wo_no']} · {wo['task_name']}", source="MES")
        cur.execute("UPDATE maintenance_work_orders SET downtime_event_id=%s WHERE id=%s", (dt_id, wo["id"]))
    elif t == "MOLD":
        m = mold_service.get_mold(cur, wo["asset_id"])
        if m["current_machine_id"]:
            raise HTTPException(status_code=409, detail=f"Mold is mounted on {m['machine_code']}. Remove it in Mold Management first.")
        if m["status"] in mold_service.OUT_OF_SERVICE:
            raise HTTPException(status_code=409, detail="The mold is out of service.")
        repair = wo["wo_type"] in REPAIR_TYPES
        target = "IN_REPAIR" if repair else "IN_MAINTENANCE"
        if m["status"] != target and not (m["status"] in ("IN_MAINTENANCE", "IN_REPAIR")):
            mtype = "REPAIR" if repair else "INSPECTION" if wo["wo_type"] == "INSPECTION" else "PM_SHOT" if m["pm_interval_shot"] else "PM_TIME"
            mold_service.change_status(cur, wo["asset_id"], "SEND_REPAIR" if repair else "SEND_MAINTENANCE", actor, reason=f"{wo['wo_no']} · {wo['task_name']}",
                                       maintenance_type=mtype, technician=wo["technician"])
        elif m["status"] == "IN_MAINTENANCE" and repair:
            mold_service.change_status(cur, wo["asset_id"], "SEND_REPAIR", actor, reason=f"{wo['wo_no']} · {wo['task_name']}", technician=wo["technician"])
        cur.execute("UPDATE maintenance_work_orders SET mold_shot_at_start=%s WHERE id=%s", (m["current_shot"] or 0, wo["id"]))
    elif t == "TOOL":
        cur.execute("UPDATE production_tools SET status='IN_MAINTENANCE', updated_by=%s WHERE id=%s", (actor, wo["asset_id"]))


def _work_minutes(wo, end=None):
    if not wo["actual_start"]:
        return 0.0
    end = end or wo["actual_finish"] or _now()
    paused = float(wo["paused_minutes"] or 0) + ((_now() - wo["paused_at"]).total_seconds() / 60 if wo["paused_at"] and wo["status"] == "PAUSED" else 0)
    return max((end - wo["actual_start"]).total_seconds() / 60 - paused, 0)


def wo_action(cur, wo_id, action, actor, note=None, version=None, technician=None, stage=None):
    action = (action or "").upper()
    wo = get_wo(cur, wo_id, True)
    _check_version(wo, version, "work order")
    s = wo["status"]

    def move(to, sets="", params=()):
        cur.execute(f"UPDATE maintenance_work_orders SET status=%s{sets}, updated_by=%s, version=version+1 WHERE id=%s", (to, *params, actor, wo_id))
        log(cur, "WO", wo_id, action, actor, s, to, note)

    if action == "ASSIGN":
        if s not in ("RELEASED", "ASSIGNED", "IN_PROGRESS", "PAUSED"):
            raise HTTPException(status_code=409, detail="The work order can no longer be reassigned.")
        tech = (technician or "").strip()
        if not tech:
            raise HTTPException(status_code=400, detail="Choose a technician.")
        move("ASSIGNED" if s in ("RELEASED", "ASSIGNED") else s, ", technician=%s", (tech,))
    elif action == "START":
        if s not in ("RELEASED", "ASSIGNED"):
            raise HTTPException(status_code=409, detail="Only released or assigned work orders can start.")
        _start_asset(cur, wo, actor)
        move("IN_PROGRESS", ", actual_start=NOW(), technician=COALESCE(technician, %s)", (actor,))
    elif action == "PAUSE":
        if s != "IN_PROGRESS":
            raise HTTPException(status_code=409, detail="Only work in progress can be paused.")
        if not (note or "").strip():
            raise HTTPException(status_code=400, detail="A reason is required.")
        move("PAUSED", ", paused_at=NOW()")
    elif action == "RESUME":
        if s != "PAUSED":
            raise HTTPException(status_code=409, detail="The work order is not paused.")
        extra = (_now() - wo["paused_at"]).total_seconds() / 60 if wo["paused_at"] else 0
        move("IN_PROGRESS", ", paused_at=NULL, paused_minutes=paused_minutes+%s", (round(extra, 1),))
    elif action == "STAGE":
        if s not in ("IN_PROGRESS", "PAUSED"):
            raise HTTPException(status_code=409, detail="Stages are updated while the work is in progress.")
        if stage not in STAGES.get(wo["asset_type"], []):
            raise HTTPException(status_code=400, detail="Unknown stage.")
        cur.execute("UPDATE maintenance_work_orders SET stage=%s, updated_by=%s, version=version+1 WHERE id=%s", (stage, actor, wo_id))
        log(cur, "WO", wo_id, "STAGE", actor, s, s, stage)
    elif action == "SUBMIT":
        if s not in ("IN_PROGRESS", "PAUSED"):
            raise HTTPException(status_code=409, detail="Only work in progress can be submitted for verification.")
        cur.execute("SELECT COUNT(*) AS n FROM maintenance_wo_checklist WHERE wo_id=%s AND result IS NULL", (wo_id,))
        if cur.fetchone()["n"]:
            raise HTTPException(status_code=409, detail="Complete every checklist item (OK / NG / N/A) first.")
        if wo["wo_type"] == "CALIBRATION" and wo["calibration_result"] not in ("PASS", "FAIL"):
            raise HTTPException(status_code=409, detail="Enter the calibration result (PASS / FAIL) first.")
        paused_extra = (_now() - wo["paused_at"]).total_seconds() / 60 if s == "PAUSED" and wo["paused_at"] else 0
        dt_min = None
        if wo["downtime_event_id"]:
            cur.execute("SELECT status, started_at FROM downtime_events WHERE id=%s", (wo["downtime_event_id"],))
            dt = cur.fetchone()
            if dt and dt["status"] == "OPEN":
                machine.close_downtime(cur, wo["downtime_event_id"], actor, root_cause=wo["root_cause"], action_taken=wo["result"] or wo["task_name"])
            if dt:
                dt_min = round((_now() - dt["started_at"]).total_seconds() / 60, 1)
        move("VERIFICATION", ", actual_finish=NOW(), paused_at=NULL, paused_minutes=paused_minutes+%s", (round(paused_extra, 1),))
        wo = get_wo(cur, wo_id)
        work = round(_work_minutes(wo), 1)
        cur.execute("UPDATE maintenance_work_orders SET downtime_minutes=%s WHERE id=%s", (dt_min if dt_min is not None else work, wo_id))
        cur.execute("SELECT COUNT(*) AS n FROM maintenance_wo_labor WHERE wo_id=%s", (wo_id,))
        if not cur.fetchone()["n"] and wo["technician"]:
            cur.execute("INSERT INTO maintenance_wo_labor (wo_id, technician, labor_type, hours, rate, created_by) VALUES (%s,%s,%s,%s,%s,'SYSTEM')",
                        (wo_id, wo["technician"], "REPAIR" if wo["wo_type"] in REPAIR_TYPES else wo["wo_type"] if wo["wo_type"] in LABOR_TYPES else "PM",
                         round(work / 60, 2), labor_rate(cur)))
    elif action == "REJECT":
        if s != "VERIFICATION":
            raise HTTPException(status_code=409, detail="Only work orders waiting for verification can be sent back.")
        if not (note or "").strip():
            raise HTTPException(status_code=400, detail="A reason is required.")
        move("IN_PROGRESS", ", actual_finish=NULL")
        if wo["asset_type"] in ("MACHINE", "EQUIPMENT"):
            code = "DT-BRK" if wo["wo_type"] in REPAIR_TYPES else "DT-PM"
            dt_id = machine.open_downtime(cur, wo["asset_id"], actor, _reason_id(cur, code), f"{wo['wo_no']} · rework", source="MES")
            cur.execute("UPDATE maintenance_work_orders SET downtime_event_id=%s WHERE id=%s", (dt_id, wo_id))
    elif action == "VERIFY":
        if s != "VERIFICATION":
            raise HTTPException(status_code=409, detail="Only work orders waiting for verification can be verified.")
        move("COMPLETED", ", verified_by=%s, verified_at=NOW(), completed_at=NOW()", (actor,))
        _complete_asset(cur, get_wo(cur, wo_id), actor)
    elif action == "CLOSE":
        if s != "COMPLETED":
            raise HTTPException(status_code=409, detail="Only completed work orders can be closed.")
        move("CLOSED", ", closed_by=%s, closed_at=NOW()", (actor,))
    elif action == "CANCEL":
        if s not in ("RELEASED", "ASSIGNED"):
            raise HTTPException(status_code=409, detail="Only work orders that have not started can be cancelled.")
        if not (note or "").strip():
            raise HTTPException(status_code=400, detail="A reason is required.")
        move("CANCELLED", ", cancel_reason=%s", (note.strip(),))
        if wo["request_id"]:
            cur.execute("UPDATE maintenance_requests SET work_order_id=NULL, version=version+1 WHERE id=%s", (wo["request_id"],))
            log(cur, "REQ", wo["request_id"], "WO_CANCELLED", actor, note=f"{wo['wo_no']}: {note.strip()}")
    else:
        raise HTTPException(status_code=400, detail="Unknown action.")


def _complete_asset(cur, wo, actor):
    work_h = round(_work_minutes(wo) / 60, 2)
    t = wo["asset_type"]
    if t == "MOLD":
        m = mold_service.get_mold(cur, wo["asset_id"])
        if m["status"] in ("IN_MAINTENANCE", "IN_REPAIR") and not m["current_machine_id"]:
            mtype = "REPAIR" if wo["wo_type"] in REPAIR_TYPES else "INSPECTION" if wo["wo_type"] == "INSPECTION" else "PM_SHOT" if m["pm_interval_shot"] else "PM_TIME"
            mold_service.change_status(cur, wo["asset_id"], "COMPLETE", actor, reason=f"{wo['wo_no']} · {wo['result'] or wo['task_name']}",
                                       maintenance_type=mtype, technician=wo["technician"], downtime_hours=work_h, record_pm=wo["wo_type"] != "INSPECTION")
    elif t == "TOOL":
        if wo["wo_type"] == "CALIBRATION":
            if wo["calibration_result"] == "FAIL":
                cur.execute("UPDATE production_tools SET status='OUT_OF_SERVICE', updated_by=%s WHERE id=%s", (actor, wo["asset_id"]))
            else:
                cur.execute("""UPDATE production_tools SET status='AVAILABLE', last_calibration_date=CURDATE(),
                               calibration_due_date=DATE_ADD(CURDATE(), INTERVAL COALESCE(calibration_interval_days, 365) DAY), updated_by=%s WHERE id=%s""", (actor, wo["asset_id"]))
        else:
            cur.execute("UPDATE production_tools SET status='AVAILABLE', updated_by=%s WHERE id=%s AND status='IN_MAINTENANCE'", (actor, wo["asset_id"]))
    if wo["request_id"]:
        cur.execute("UPDATE maintenance_requests SET status='COMPLETED', version=version+1 WHERE id=%s AND status='APPROVED'", (wo["request_id"],))
        log(cur, "REQ", wo["request_id"], "COMPLETE", actor, "APPROVED", "COMPLETED", wo["wo_no"])
    _advance_plan(cur, wo, actor)


# ---- checklist / parts / labor / components ----

def _editable(cur, wo_id):
    wo = get_wo(cur, wo_id)
    if wo["status"] not in OPEN_WO:
        raise HTTPException(status_code=409, detail="The work order is completed, closed or cancelled.")
    return wo


def set_checklist(cur, wo_id, item_id, result, note, actor):
    _editable(cur, wo_id)
    if result not in (None, "OK", "NG", "NA"):
        raise HTTPException(status_code=400, detail="Result must be OK, NG or NA.")
    cur.execute("""UPDATE maintenance_wo_checklist SET result=%s, note=%s, done_by=IF(%s IS NULL, NULL, %s), done_at=IF(%s IS NULL, NULL, NOW())
                   WHERE id=%s AND wo_id=%s""", (result, note, result, actor, result, item_id, wo_id))
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="Checklist item not found.")


def add_checklist(cur, wo_id, item, actor):
    _editable(cur, wo_id)
    cur.execute("SELECT COALESCE(MAX(seq),0)+1 AS n FROM maintenance_wo_checklist WHERE wo_id=%s", (wo_id,))
    cur.execute("INSERT INTO maintenance_wo_checklist (wo_id, seq, item) VALUES (%s,%s,%s)", (wo_id, cur.fetchone()["n"], item.strip()[:500]))
    log(cur, "WO", wo_id, "CHECKLIST_ADD", actor, note=item.strip()[:200])


def add_part(cur, wo_id, p, actor):
    _editable(cur, wo_id)
    cur.execute("INSERT INTO maintenance_wo_parts (wo_id, part_code, part_name, qty, unit, unit_cost, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (wo_id, p.get("part_code"), p["part_name"].strip(), p["qty"], p.get("unit"), p.get("unit_cost") or 0, actor))
    log(cur, "WO", wo_id, "PART_ADD", actor, note=f"{p['part_name'].strip()} x {p['qty']}")


def delete_line(cur, table, wo_id, line_id, actor):
    _editable(cur, wo_id)
    cur.execute(f"DELETE FROM {table} WHERE id=%s AND wo_id=%s", (line_id, wo_id))
    if not cur.rowcount:
        raise HTTPException(status_code=404, detail="Line not found.")


def add_labor(cur, wo_id, l, actor):
    _editable(cur, wo_id)
    cur.execute("INSERT INTO maintenance_wo_labor (wo_id, technician, labor_type, hours, rate, created_by) VALUES (%s,%s,%s,%s,%s,%s)",
                (wo_id, l["technician"].strip(), l.get("labor_type") or "REPAIR", l["hours"], l.get("rate") if l.get("rate") is not None else labor_rate(cur), actor))
    log(cur, "WO", wo_id, "LABOR_ADD", actor, note=f"{l['technician'].strip()} {l['hours']} h")


def set_component(cur, wo_id, component, condition, action, note, actor):
    _editable(cur, wo_id)
    if condition not in COMPONENT_CONDITIONS or action not in COMPONENT_ACTIONS:
        raise HTTPException(status_code=400, detail="Unknown condition or action.")
    cur.execute("""INSERT INTO maintenance_wo_components (wo_id, component, condition_code, action_code, note, updated_by) VALUES (%s,%s,%s,%s,%s,%s)
                   ON DUPLICATE KEY UPDATE condition_code=VALUES(condition_code), action_code=VALUES(action_code), note=VALUES(note), updated_by=VALUES(updated_by)""",
                (wo_id, component, condition, action, note, actor))


# =========================================================
# ENRICH / METRICS
# =========================================================


def costs(cur, wo_ids):
    out = defaultdict(lambda: {"parts": 0.0, "labor": 0.0, "labor_hours": 0.0})
    if not wo_ids:
        return out
    marks = ",".join(["%s"] * len(wo_ids))
    cur.execute(f"SELECT wo_id, SUM(qty*unit_cost) AS c FROM maintenance_wo_parts WHERE wo_id IN ({marks}) GROUP BY wo_id", tuple(wo_ids))
    for r in cur.fetchall():
        out[r["wo_id"]]["parts"] = float(r["c"] or 0)
    cur.execute(f"SELECT wo_id, SUM(hours*rate) AS c, SUM(hours) AS h FROM maintenance_wo_labor WHERE wo_id IN ({marks}) GROUP BY wo_id", tuple(wo_ids))
    for r in cur.fetchall():
        out[r["wo_id"]]["labor"], out[r["wo_id"]]["labor_hours"] = float(r["c"] or 0), float(r["h"] or 0)
    return out


def enrich_wos(cur, wos):
    if not wos:
        return wos
    ids = [w["id"] for w in wos]
    marks = ",".join(["%s"] * len(ids))
    assets = asset_map(cur, [(w["asset_type"], w["asset_id"]) for w in wos])
    cur.execute(f"""SELECT wo_id, COUNT(*) AS n, SUM(result IS NOT NULL) AS done, SUM(result='NG') AS ng FROM maintenance_wo_checklist
                    WHERE wo_id IN ({marks}) GROUP BY wo_id""", tuple(ids))
    checks = {r["wo_id"]: r for r in cur.fetchall()}
    c = costs(cur, ids)
    dt_ids = [w["downtime_event_id"] for w in wos if w["downtime_event_id"]]
    dts = {}
    if dt_ids:
        cur.execute(f"SELECT id, downtime_no, status, started_at, ended_at FROM downtime_events WHERE id IN ({','.join(['%s'] * len(dt_ids))})", tuple(dt_ids))
        dts = {r["id"]: r for r in cur.fetchall()}
    cur.execute(f"SELECT id, request_no FROM maintenance_requests WHERE id IN (SELECT request_id FROM maintenance_work_orders WHERE id IN ({marks}))", tuple(ids))
    reqs = {r["id"]: r["request_no"] for r in cur.fetchall()}
    cur.execute(f"SELECT id, plan_no FROM maintenance_plans WHERE id IN (SELECT plan_id FROM maintenance_work_orders WHERE id IN ({marks}))", tuple(ids))
    plans = {r["id"]: r["plan_no"] for r in cur.fetchall()}
    now, today = _now(), date.today()
    for w in wos:
        a = assets.get(asset_key(w["asset_type"], w["asset_id"])) or {}
        w["asset_code"], w["asset_name"], w["asset_image"], w["asset_sub"], w["asset_location"], w["asset_status"] = \
            a.get("code"), a.get("name"), a.get("image_url"), a.get("sub"), a.get("location"), a.get("status")
        w["asset_shot"], w["asset_design_shot"], w["asset_group"] = a.get("current_shot"), a.get("design_shot"), a.get("mold_group") or a.get("tool_category")
        ck = checks.get(w["id"]) or {"n": 0, "done": 0, "ng": 0}
        w["checklist_total"], w["checklist_done"], w["checklist_ng"] = int(ck["n"]), int(ck["done"] or 0), int(ck["ng"] or 0)
        w["progress"] = 100 if w["status"] in DONE_WO or w["status"] == "VERIFICATION" else round(w["checklist_done"] / w["checklist_total"] * 100) if w["checklist_total"] and w["status"] in ("IN_PROGRESS", "PAUSED") else 0
        w["work_minutes"] = round(_work_minutes(w), 1)
        w["remaining_minutes"] = max(int(w["est_minutes"] or 0) - w["work_minutes"], 0) if w["est_minutes"] and w["status"] in ("IN_PROGRESS", "PAUSED") else None
        deadline = w["planned_finish"] or (datetime.combine(w["due_date"], datetime.max.time()) if w["due_date"] else None)
        w["overdue"] = bool(w["status"] in OPEN_WO and deadline and deadline < now)
        w["overdue_days"] = (today - deadline.date()).days if w["overdue"] else 0
        dt = dts.get(w["downtime_event_id"])
        if dt:
            w["downtime_no"] = dt["downtime_no"]
            w["downtime_actual_minutes"] = round(((dt["ended_at"] or now) - dt["started_at"]).total_seconds() / 60, 1)
        else:
            w["downtime_no"] = None
            w["downtime_actual_minutes"] = float(w["downtime_minutes"]) if w["downtime_minutes"] is not None else (w["work_minutes"] if w["actual_start"] and w["asset_type"] in ("MOLD", "TOOL") else None)
        cc = c[w["id"]]
        w["parts_cost"], w["labor_cost"], w["labor_hours"] = round(cc["parts"], 2), round(cc["labor"], 2), round(cc["labor_hours"], 2)
        w["total_cost"] = round(cc["parts"] + cc["labor"] + float(w["outsourcing_cost"] or 0) + float(w["tool_cost"] or 0) + float(w["other_cost"] or 0), 2)
        w["request_no"], w["plan_no"] = reqs.get(w["request_id"]), plans.get(w["plan_id"])
    return wos


def load_wos(cur, where="1=1", params=()):
    cur.execute(f"SELECT * FROM maintenance_work_orders w WHERE {where} ORDER BY w.created_at DESC, w.id DESC", tuple(params))
    return enrich_wos(cur, cur.fetchall())


def repair_hours(w):
    return w["work_minutes"] / 60 if w["actual_start"] and w["actual_finish"] else None


def mttr(wos):
    vals = [repair_hours(w) for w in wos if w["wo_type"] in REPAIR_TYPES and w["status"] in DONE_WO | {"VERIFICATION"} and repair_hours(w) is not None]
    return round(sum(vals) / len(vals), 2) if vals else None


def pm_compliance(wos, start, end):
    """PM-type WOs due in [start, end] (and already due): % completed on or before the due date."""
    today = date.today()
    due = [w for w in wos if w["wo_type"] in PREVENTIVE_TYPES and w["status"] != "CANCELLED" and w["due_date"] and start <= w["due_date"] <= min(end, today)]
    if not due:
        return None
    ok = sum(1 for w in due if w["completed_at"] and w["completed_at"].date() <= w["due_date"])
    return round(ok / len(due) * 100, 1)


def mtbf(cur, wos, start_dt, end_dt):
    from app.modules.machine.monitoring_router import status_seconds
    cur.execute("SELECT e.id, e.operational_status FROM equipment e WHERE e.asset_status='ACTIVE'")
    run = sum(status_seconds(cur, r["id"], start_dt, end_dt, r["operational_status"]).get("RUNNING", 0) for r in cur.fetchall()) / 3600
    fails = sum(1 for w in wos if w["asset_type"] in ("MACHINE", "EQUIPMENT") and w["wo_type"] in REPAIR_TYPES and w["created_at"] >= start_dt and w["created_at"] < end_dt)
    return (round(run / max(fails, 1), 1) if run else None), fails


def cost_breakdown(wos):
    b = {"PARTS": 0.0, "LABOR": 0.0, "OUTSOURCING": 0.0, "TOOLS": 0.0, "OTHERS": 0.0}
    for w in wos:
        b["PARTS"] += w["parts_cost"]
        b["LABOR"] += w["labor_cost"]
        b["OUTSOURCING"] += float(w["outsourcing_cost"] or 0)
        b["TOOLS"] += float(w["tool_cost"] or 0)
        b["OTHERS"] += float(w["other_cost"] or 0)
    return {k: round(v, 2) for k, v in b.items()}
