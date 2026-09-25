"""Quality Management business logic (screens 5.1 - 5.6).

Definitions used everywhere (one indicator - one meaning):
- Inspected / NG samples: inspection records of Inspection Management.
- NG Rate  = NG samples / inspected samples x 100 (inspection).
- FPY      = samples OK at the first inspection / samples inspected the first time x 100 (re-inspections excluded).
- PPM      = reject pieces / produced pieces x 1,000,000 (Production Execution output).
- Defects  = NG pieces of the NG records (one NG record per incident; a product can carry several defects).

Flows:
- Inspection plan (auto from Work Order start / frequency, or manual) -> sample records with measurements -> plan result OK / NG
  -> NG record (NG Management) when NG. Invalid measuring equipment is blocked (setting quality.equipment_control).
- NG record: NEW -> INVESTIGATING -> CA_IN_PROGRESS -> WAITING_VERIFICATION -> CLOSED (REOPENED -> investigation again).
- Calibration plan (auto from the equipment cycle): PLANNED -> IN_PROGRESS -> COMPLETED (PASS / ADJUSTED extend the validity,
  FAIL locks the equipment).
"""

import json
from collections import Counter
from datetime import date, datetime, timedelta

from fastapi import HTTPException

from app.modules.machine.service import _next_no
from app.modules.production.work_orders.service import load_shifts, shift_of

INSPECTION_TYPES = ["IQC", "IPQC", "FQC", "OQC", "PATROL", "FAI", "LAST_PIECE", "AUDIT"]
PROCESSES = ["INCOMING", "INJECTION", "COOLING", "TRIMMING", "ASSEMBLY", "INSPECTION", "PACKING"]
FREQ_TYPES = ["ONCE", "TIME", "QTY", "LOT"]
PLAN_STATUSES = ["NOT_STARTED", "IN_PROGRESS", "COMPLETED", "OVERDUE", "CANCELLED"]
NG_SOURCES = ["IQC", "IPQC", "FQC", "OQC", "PATROL", "FAI", "LAST_PIECE", "AUDIT", "SPC", "VISION", "MACHINE", "CUSTOMER", "PRODUCTION"]
NG_STATUSES = ["NEW", "INVESTIGATING", "CA_IN_PROGRESS", "WAITING_VERIFICATION", "CLOSED", "REOPENED"]
NG_OPEN = ("NEW", "INVESTIGATING", "CA_IN_PROGRESS", "WAITING_VERIFICATION", "REOPENED")
PRIORITIES = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
DISPOSITIONS = ["SORT", "REWORK", "SCRAP", "USE_AS_IS", "RETURN_SUPPLIER"]
ROOT_CAUSES = ["MAN", "MACHINE", "MATERIAL", "METHOD", "MEASUREMENT", "ENVIRONMENT"]
ALERT_STATUSES = ["NEW", "ACKNOWLEDGED", "IN_PROGRESS", "RESOLVED", "CLOSED"]
ALERT_OPEN = ("NEW", "ACKNOWLEDGED", "IN_PROGRESS")
CAL_STATUSES = ["PLANNED", "IN_PROGRESS", "COMPLETED", "OVERDUE", "CANCELLED"]
CAL_RESULTS = ["PASS", "ADJUSTED", "FAIL"]
EQUIPMENT_STATES = ["IN_SERVICE", "DUE_SOON", "OVERDUE", "NOT_CALIBRATED", "IN_CALIBRATION", "UNDER_REPAIR", "OUT_OF_SERVICE", "SCRAPPED"]
USABLE_STATES = ("IN_SERVICE", "DUE_SOON")
USAGE_LOCATIONS = ["QC IPQC - Line 1", "QC IPQC - Line 2", "QC FQC - Line 1", "QC FQC - Packing", "QC Lab", "Incoming Inspection", "Production Line"]
LABS = ["QC Lab", "External Lab"]
NG_DUE_DAYS = {"CRITICAL": 1, "HIGH": 3, "MEDIUM": 7, "LOW": 14}
DEFAULT_SAMPLE = {"IQC": 5, "IPQC": 5, "FQC": 10, "OQC": 13, "PATROL": 3, "FAI": 3, "LAST_PIECE": 3, "AUDIT": 5}
TYPE_PROCESS = {"IQC": "INCOMING", "FQC": "INSPECTION", "OQC": "PACKING"}


# =========================================================
# HELPERS
# =========================================================


def setting(cur, key, default, cast=float):
    cur.execute("SELECT setting_value FROM system_settings WHERE setting_key=%s", (key,))
    row = cur.fetchone()
    if not row or row["setting_value"] in (None, ""):
        return default
    try:
        return cast(row["setting_value"])
    except (TypeError, ValueError):
        return default


def targets(cur):
    return {"fpy": setting(cur, "quality.fpy_target", 98.0), "ppm": setting(cur, "quality.ppm_target", 2000.0),
            "ng_rate": setting(cur, "quality.ng_rate_target", 2.0), "wo_attention": setting(cur, "quality.wo_ng_attention", 2.0),
            "wo_critical": setting(cur, "quality.wo_ng_critical", 3.0), "cal_compliance": setting(cur, "quality.calibration_compliance_target", 95.0),
            "cal_due_days": int(setting(cur, "quality.calibration_due_days", 7)),
            "equipment_control": setting(cur, "quality.equipment_control", "BLOCK", str).upper()}


def log(cur, entity, entity_id, action, actor, from_status=None, to_status=None, note=None):
    cur.execute("""INSERT INTO quality_events (entity_type, entity_id, action, from_status, to_status, actor, note)
                   VALUES (%s,%s,%s,%s,%s,%s,%s)""", (entity, entity_id, action, from_status, to_status, actor or "SYSTEM", (note or None) and str(note)[:1000]))


def events(cur, entity, entity_id):
    cur.execute("SELECT * FROM quality_events WHERE entity_type=%s AND entity_id=%s ORDER BY event_at DESC, id DESC", (entity, entity_id))
    return cur.fetchall()


def pct(a, b, digits=2):
    return round(a / b * 100, digits) if b else None


def _check_version(row, version):
    if version is not None and int(row["version"]) != int(version):
        raise HTTPException(status_code=409, detail="The record was changed by someone else. Refresh and try again.")


def inspectors(cur):
    from app.modules.maintenance.service import technicians
    return technicians(cur)


def defect_codes(cur):
    cur.execute("""SELECT r.id, r.reason_code, r.reason_name, r.impact_level, c.category_code FROM reason_codes r
                   JOIN reason_code_categories c ON c.id=r.category_id
                   WHERE r.status='ACTIVE' AND (r.applicable_module IN ('NG_MANAGEMENT','SCRAP_MANAGEMENT') OR c.category_code IN ('DEFECT','SCRAP'))
                   ORDER BY r.reason_code""")
    return cur.fetchall()


def active_revision(cur, product_id):
    if not product_id:
        return None
    cur.execute("""SELECT id FROM quality_standard_revisions WHERE product_id=%s AND status='ACTIVE'
                   ORDER BY revision_no DESC LIMIT 1""", (product_id,))
    row = cur.fetchone()
    return row["id"] if row else None


def characteristics(cur, revision_id):
    if not revision_id:
        return []
    cur.execute("""SELECT c.*, t.type_name AS equipment_type_name FROM quality_standard_characteristics c
                   LEFT JOIN measuring_equipment_types t ON t.id=c.equipment_type_id WHERE c.revision_id=%s ORDER BY c.sort_order, c.id""", (revision_id,))
    return cur.fetchall()


def _wo(cur, wo_id):
    cur.execute("""SELECT d.id, d.wo_no, d.product_id, d.machine_id, d.mold_id, d.status, d.cavity, d.version, d.shift_id, d.planned_qty,
                          d.actual_qty, d.reject_qty FROM production_plan_details d WHERE d.id=%s""", (wo_id,))
    wo = cur.fetchone()
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found.")
    return wo


# =========================================================
# MEASURING EQUIPMENT
# =========================================================

EQUIPMENT_SELECT = """SELECT me.*, t.type_code, t.type_name, fn.name AS node_name,
        (SELECT cp.id FROM quality_calibration_plans cp WHERE cp.equipment_id=me.id AND cp.status IN ('PLANNED','IN_PROGRESS') ORDER BY cp.planned_date LIMIT 1) AS open_plan_id
    FROM measuring_equipment me JOIN measuring_equipment_types t ON t.id=me.equipment_type_id
    LEFT JOIN factory_structure_nodes fn ON fn.id=me.factory_node_id"""


def equipment_state(e, today=None, due_days=7):
    today = today or date.today()
    if e.get("quality_state") == "SCRAPPED":
        return "SCRAPPED"
    if e.get("status") in ("OUT_OF_SERVICE", "INACTIVE"):
        return "OUT_OF_SERVICE"
    if e.get("quality_state") == "UNDER_REPAIR":
        return "UNDER_REPAIR"
    if e.get("quality_state") == "IN_CALIBRATION":
        return "IN_CALIBRATION"
    nxt = e.get("next_calibration_date")
    if not nxt:
        return "NOT_CALIBRATED"
    if nxt < today:
        return "OVERDUE"
    if nxt <= today + timedelta(days=due_days):
        return "DUE_SOON"
    return "IN_SERVICE"


def enrich_equipment(e, today, due_days):
    state = equipment_state(e, today, due_days)
    nxt = e.get("next_calibration_date")
    return {**e, "state": state, "usable": state in USABLE_STATES, "days_left": (nxt - today).days if nxt else None,
            "location": e.get("usage_location") or e.get("node_name")}


def load_equipment(cur, equipment_id=None):
    t = targets(cur)
    today = date.today()
    cur.execute(EQUIPMENT_SELECT + (" WHERE me.id=%s" if equipment_id else " ORDER BY me.equipment_code"), (equipment_id,) if equipment_id else ())
    rows = [enrich_equipment(e, today, t["cal_due_days"]) for e in cur.fetchall()]
    if equipment_id:
        if not rows:
            raise HTTPException(status_code=404, detail="Measuring equipment not found.")
        return rows[0]
    return rows


def equipment_event(cur, equipment_id, action, actor, note=None):
    cur.execute("INSERT INTO quality_equipment_events (equipment_id, action, note, actor) VALUES (%s,%s,%s,%s)",
                (equipment_id, action, (note or None) and str(note)[:1000], actor or "SYSTEM"))


def _bump_equipment(cur, equipment_id, sets, params, actor):
    cur.execute(f"UPDATE measuring_equipment SET {sets}, version=version+1, updated_by=%s WHERE id=%s", (*params, actor, equipment_id))


def equipment_action(cur, equipment_id, action, data, actor):
    e = load_equipment(cur, equipment_id)
    _check_version(e, data.get("version"))
    note = (data.get("note") or "").strip() or None
    if e["state"] == "SCRAPPED" and action != "ASSIGN_OWNER":
        raise HTTPException(status_code=409, detail="The equipment is scrapped.")
    if action == "MOVE":
        loc = (data.get("usage_location") or "").strip()
        if not loc:
            raise HTTPException(status_code=400, detail="Select the new usage location.")
        _bump_equipment(cur, equipment_id, "usage_location=%s", (loc,), actor)
        equipment_event(cur, equipment_id, "MOVE", actor, f"{e['location'] or '-'} -> {loc}" + (f" · {note}" if note else ""))
    elif action == "ASSIGN_OWNER":
        _bump_equipment(cur, equipment_id, "owner_code=%s", (data.get("owner") or None,), actor)
        equipment_event(cur, equipment_id, "ASSIGN_OWNER", actor, data.get("owner"))
    elif action == "REPORT_BROKEN":
        if not note:
            raise HTTPException(status_code=400, detail="Describe the failure.")
        if e["state"] in ("UNDER_REPAIR", "OUT_OF_SERVICE"):
            raise HTTPException(status_code=409, detail="The equipment is already out of use.")
        _bump_equipment(cur, equipment_id, "quality_state='UNDER_REPAIR', lock_reason=%s", (note[:255],), actor)
        equipment_event(cur, equipment_id, "REPORT_BROKEN", actor, note)
    elif action == "REPAIRED":
        if e["quality_state"] != "UNDER_REPAIR":
            raise HTTPException(status_code=409, detail="The equipment is not under repair.")
        _bump_equipment(cur, equipment_id, "quality_state='NORMAL', lock_reason=NULL", (), actor)
        equipment_event(cur, equipment_id, "REPAIRED", actor, note)
    elif action == "OUT_OF_SERVICE":
        if not note:
            raise HTTPException(status_code=400, detail="A reason is required.")
        _bump_equipment(cur, equipment_id, "status='OUT_OF_SERVICE', lock_reason=%s", (note[:255],), actor)
        equipment_event(cur, equipment_id, "OUT_OF_SERVICE", actor, note)
    elif action == "RETURN_TO_SERVICE":
        if e["state"] != "OUT_OF_SERVICE":
            raise HTTPException(status_code=409, detail="The equipment is not out of service.")
        if (e.get("lock_reason") or "").startswith("Calibration failed"):
            raise HTTPException(status_code=409, detail="The last calibration failed: a passed calibration is required before use.")
        _bump_equipment(cur, equipment_id, "status='AVAILABLE', lock_reason=NULL", (), actor)
        equipment_event(cur, equipment_id, "RETURN_TO_SERVICE", actor, note)
    elif action == "SCRAP":
        if not note:
            raise HTTPException(status_code=400, detail="A reason is required.")
        _bump_equipment(cur, equipment_id, "quality_state='SCRAPPED', status='INACTIVE', lock_reason=%s", (note[:255],), actor)
        cur.execute("SELECT id, status FROM quality_calibration_plans WHERE equipment_id=%s AND status IN ('PLANNED','IN_PROGRESS')", (equipment_id,))
        for p in cur.fetchall():
            cur.execute("UPDATE quality_calibration_plans SET status='CANCELLED', version=version+1, updated_by=%s WHERE id=%s", (actor, p["id"]))
            log(cur, "CAL", p["id"], "CANCEL", actor, p["status"], "CANCELLED", "Equipment scrapped")
        equipment_event(cur, equipment_id, "SCRAP", actor, note)
    elif action == "REQUEST_CALIBRATION":
        return create_cal_plan(cur, {"equipment_id": equipment_id, "planned_date": data.get("planned_date") or date.today(),
                                     "priority": data.get("priority") or "HIGH", "remark": note, "source": "REQUEST"}, actor)
    else:
        raise HTTPException(status_code=400, detail="Unknown action.")
    return {"id": equipment_id}


def check_gauge(cur, equipment_id, control):
    """Validate a measuring equipment at inspection; returns a warning text in WARN mode."""
    if not equipment_id:
        return None
    e = load_equipment(cur, equipment_id)
    if e["usable"]:
        return None
    text = f"Measuring equipment {e['equipment_code']} cannot be used ({e['state']})."
    if control == "BLOCK":
        raise HTTPException(status_code=409, detail=text)
    return text


# =========================================================
# CALIBRATION
# =========================================================

CAL_SELECT = """SELECT cp.*, me.equipment_code, me.equipment_name, me.model, me.serial_number, me.usage_location, me.calibration_cycle_days,
        me.last_calibration_date, me.next_calibration_date, me.equipment_type_id, me.status AS equipment_status, me.quality_state, t.type_name, fn.name AS node_name
    FROM quality_calibration_plans cp JOIN measuring_equipment me ON me.id=cp.equipment_id
    JOIN measuring_equipment_types t ON t.id=me.equipment_type_id LEFT JOIN factory_structure_nodes fn ON fn.id=me.factory_node_id"""


def enrich_cal(p, today, due_days):
    display = p["status"]
    if p["status"] in ("PLANNED", "IN_PROGRESS") and p["planned_date"] < today:
        display = "OVERDUE"
    return {**p, "display_status": display, "location": p.get("usage_location") or p.get("node_name"),
            "days_left": (p["planned_date"] - today).days, "due_soon": p["status"] == "PLANNED" and 0 <= (p["planned_date"] - today).days <= due_days,
            "on_time": (p["calibration_date"] <= p["planned_date"]) if p["status"] == "COMPLETED" and p.get("calibration_date") else None}


def load_cal(cur, plan_id):
    cur.execute(CAL_SELECT + " WHERE cp.id=%s", (plan_id,))
    p = cur.fetchone()
    if not p:
        raise HTTPException(status_code=404, detail="Calibration plan not found.")
    t = targets(cur)
    return enrich_cal(p, date.today(), t["cal_due_days"])


def create_cal_plan(cur, data, actor):
    e = load_equipment(cur, int(data["equipment_id"]))
    if e["state"] == "SCRAPPED":
        raise HTTPException(status_code=409, detail="The equipment is scrapped.")
    cur.execute("SELECT plan_no FROM quality_calibration_plans WHERE equipment_id=%s AND status IN ('PLANNED','IN_PROGRESS')", (e["id"],))
    row = cur.fetchone()
    if row:
        raise HTTPException(status_code=409, detail=f"Calibration plan {row['plan_no']} is already open for this equipment.")
    planned = data.get("planned_date") or e.get("next_calibration_date") or date.today()
    if isinstance(planned, str):
        planned = date.fromisoformat(planned[:10])
    no = _next_no(cur, "quality_calibration_plans", "plan_no", "CAL")
    cur.execute("""INSERT INTO quality_calibration_plans (plan_no, equipment_id, planned_date, lab, owner, priority, source, remark, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (no, e["id"], planned, data.get("lab") or e.get("calibration_lab") or LABS[0], data.get("owner") or e.get("owner_code"),
                 data.get("priority") or "MEDIUM", data.get("source") or "MANUAL", data.get("remark"), actor))
    pid = cur.lastrowid
    log(cur, "CAL", pid, "CREATE", actor, None, "PLANNED", data.get("remark"))
    if data.get("source") == "REQUEST":
        equipment_event(cur, e["id"], "REQUEST_CALIBRATION", actor, f"{no}" + (f" · {data['remark']}" if data.get("remark") else ""))
    return {"id": pid, "plan_no": no}


def sync_cal_plans(cur):
    """Create the next calibration plan of every equipment whose due date enters the planning window."""
    ahead = int(setting(cur, "quality.calibration_plan_days", 30))
    due_days = int(setting(cur, "quality.calibration_due_days", 7))
    today = date.today()
    cur.execute("""SELECT me.id, me.next_calibration_date, me.calibration_lab, me.owner_code FROM measuring_equipment me
                   WHERE me.quality_state<>'SCRAPPED' AND me.status<>'INACTIVE' AND me.next_calibration_date IS NOT NULL
                     AND me.next_calibration_date <= %s
                     AND NOT EXISTS (SELECT 1 FROM quality_calibration_plans cp WHERE cp.equipment_id=me.id AND cp.status IN ('PLANNED','IN_PROGRESS'))
                     AND NOT EXISTS (SELECT 1 FROM quality_calibration_plans cp WHERE cp.equipment_id=me.id AND cp.status='CANCELLED'
                                     AND cp.planned_date=me.next_calibration_date)""", (today + timedelta(days=ahead),))
    for e in cur.fetchall():
        nxt = e["next_calibration_date"]
        prio = "HIGH" if nxt < today else "MEDIUM" if nxt <= today + timedelta(days=due_days) else "LOW"
        no = _next_no(cur, "quality_calibration_plans", "plan_no", "CAL")
        cur.execute("""INSERT INTO quality_calibration_plans (plan_no, equipment_id, planned_date, lab, owner, priority, source, created_by)
                       VALUES (%s,%s,%s,%s,%s,%s,'AUTO','SYSTEM')""", (no, e["id"], nxt, e["calibration_lab"] or LABS[0], e["owner_code"], prio))
        log(cur, "CAL", cur.lastrowid, "CREATE", "SYSTEM", None, "PLANNED", "Calibration cycle")


def update_cal_plan(cur, plan_id, data, actor):
    p = load_cal(cur, plan_id)
    _check_version(p, data.get("version"))
    if p["status"] not in ("PLANNED", "IN_PROGRESS"):
        raise HTTPException(status_code=409, detail="Only open plans can be edited.")
    planned = data.get("planned_date") or p["planned_date"]
    cur.execute("""UPDATE quality_calibration_plans SET planned_date=%s, lab=%s, owner=%s, priority=%s, remark=%s, version=version+1, updated_by=%s
                   WHERE id=%s""", (planned, data.get("lab") or p["lab"], data.get("owner") or None, data.get("priority") or p["priority"],
                                    data.get("remark"), actor, plan_id))
    changes = f"planned {p['planned_date']} -> {planned}" if str(planned)[:10] != str(p["planned_date"]) else None
    log(cur, "CAL", plan_id, "UPDATE", actor, p["status"], p["status"], changes)
    return {"id": plan_id}


def cal_action(cur, plan_id, action, data, actor):
    p = load_cal(cur, plan_id)
    _check_version(p, data.get("version"))
    note = (data.get("note") or "").strip() or None
    if action == "START":
        if p["status"] != "PLANNED":
            raise HTTPException(status_code=409, detail="Only planned calibrations can start.")
        cur.execute("UPDATE quality_calibration_plans SET status='IN_PROGRESS', sent_at=NOW(), lab=COALESCE(%s, lab), version=version+1, updated_by=%s WHERE id=%s",
                    (data.get("lab"), actor, plan_id))
        _bump_equipment(cur, p["equipment_id"], "quality_state='IN_CALIBRATION'", (), actor) if p["quality_state"] == "NORMAL" else None
        equipment_event(cur, p["equipment_id"], "SENT_CALIBRATION", actor, f"{p['plan_no']} · {data.get('lab') or p['lab'] or ''}")
        log(cur, "CAL", plan_id, "START", actor, "PLANNED", "IN_PROGRESS", note)
    elif action == "COMPLETE":
        if p["status"] not in ("PLANNED", "IN_PROGRESS"):
            raise HTTPException(status_code=409, detail="Only open calibrations can be completed.")
        result = (data.get("result") or "").upper()
        if result not in CAL_RESULTS:
            raise HTTPException(status_code=400, detail="Select the calibration result.")
        cal_date = data.get("calibration_date") or date.today()
        if isinstance(cal_date, str):
            cal_date = date.fromisoformat(cal_date[:10])
        if cal_date > date.today():
            raise HTTPException(status_code=400, detail="The calibration date cannot be in the future.")
        cert = (data.get("certificate_no") or "").strip() or None
        if result != "FAIL" and not cert:
            raise HTTPException(status_code=400, detail="The certificate number is required.")
        lab = data.get("lab") or p["lab"]
        next_due = cal_date + timedelta(days=int(p["calibration_cycle_days"] or 365))
        cur.execute("""INSERT INTO measuring_equipment_calibration_history (equipment_id, plan_id, calibration_date, next_due_date, result, calibrated_by, lab,
                       certificate_no, certificate_url, remark, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (p["equipment_id"], plan_id, cal_date, next_due, result, data.get("calibrated_by"), lab, cert, data.get("certificate_url"), note, actor))
        hid = cur.lastrowid
        cur.execute("""UPDATE quality_calibration_plans SET status='COMPLETED', calibration_date=%s, result=%s, certificate_no=%s, certificate_url=%s,
                       calibrated_by=%s, lab=%s, history_id=%s, remark=COALESCE(%s, remark), version=version+1, updated_by=%s WHERE id=%s""",
                    (cal_date, result, cert, data.get("certificate_url"), data.get("calibrated_by"), lab, hid, note, actor, plan_id))
        if result == "FAIL":
            _bump_equipment(cur, p["equipment_id"], "status='OUT_OF_SERVICE', quality_state='NORMAL', lock_reason=%s",
                            (f"Calibration failed ({p['plan_no']})",), actor)
        else:
            unlock = ", status='AVAILABLE', lock_reason=NULL" if p["equipment_status"] == "OUT_OF_SERVICE" else ""
            _bump_equipment(cur, p["equipment_id"], f"last_calibration_date=%s, next_calibration_date=%s, quality_state='NORMAL'{unlock}",
                            (cal_date, next_due), actor)
        equipment_event(cur, p["equipment_id"], f"CALIBRATION_{result}", actor, f"{p['plan_no']} · {cert or '-'}")
        log(cur, "CAL", plan_id, "COMPLETE", actor, p["status"], "COMPLETED", f"{result}" + (f" · {cert}" if cert else "") + (f" · {note}" if note else ""))
    elif action == "CANCEL":
        if p["status"] not in ("PLANNED", "IN_PROGRESS"):
            raise HTTPException(status_code=409, detail="Only open calibrations can be cancelled.")
        if not note:
            raise HTTPException(status_code=400, detail="A reason is required.")
        cur.execute("UPDATE quality_calibration_plans SET status='CANCELLED', version=version+1, updated_by=%s WHERE id=%s", (actor, plan_id))
        if p["quality_state"] == "IN_CALIBRATION":
            _bump_equipment(cur, p["equipment_id"], "quality_state='NORMAL'", (), actor)
        log(cur, "CAL", plan_id, "CANCEL", actor, p["status"], "CANCELLED", note)
    else:
        raise HTTPException(status_code=400, detail="Unknown action.")
    return {"id": plan_id}


def cal_compliance(cur, start, end):
    """On-time calibrations / calibrations due in [start, end] (due dates up to today only)."""
    end = min(end, date.today())
    cur.execute("""SELECT status, planned_date, calibration_date FROM quality_calibration_plans
                   WHERE status<>'CANCELLED' AND planned_date BETWEEN %s AND %s""", (start, end))
    rows = cur.fetchall()
    on_time = sum(1 for r in rows if r["status"] == "COMPLETED" and r["calibration_date"] and r["calibration_date"] <= r["planned_date"])
    return {"due": len(rows), "on_time": on_time, "rate": pct(on_time, len(rows), 1)}


# =========================================================
# INSPECTION PLANS
# =========================================================

PLAN_SELECT = """SELECT ip.*, d.wo_no, d.status AS wo_status, p.product_code, p.product_name, eq.equipment_code AS machine_code, mo.mold_code,
        l.lot_no, mat.material_code, mat.material_name, r.standard_name, r.revision_no, s.shift_name,
        COALESCE(agg.done_qty, 0) AS done_qty, COALESCE(agg.ng_qty, 0) AS ng_qty
    FROM quality_inspection_plans ip
    LEFT JOIN production_plan_details d ON d.id=ip.work_order_id
    LEFT JOIN products p ON p.id=ip.product_id
    LEFT JOIN equipment eq ON eq.id=ip.machine_id
    LEFT JOIN molds mo ON mo.id=ip.mold_id
    LEFT JOIN material_lots l ON l.id=ip.material_lot_id
    LEFT JOIN materials mat ON mat.id=l.material_id
    LEFT JOIN quality_standard_revisions r ON r.id=ip.standard_revision_id
    LEFT JOIN shifts s ON s.id=ip.shift_id
    LEFT JOIN (SELECT plan_id, COUNT(*) AS done_qty, SUM(result='NG') AS ng_qty FROM quality_inspection_records GROUP BY plan_id) agg ON agg.plan_id=ip.id"""


def enrich_plan(p, now):
    done, ng = int(p["done_qty"] or 0), int(p["ng_qty"] or 0)
    display = p["status"]
    if p["status"] in ("NOT_STARTED", "IN_PROGRESS") and p["due_at"] < now:
        display = "OVERDUE"
    return {**p, "done_qty": done, "ng_qty": ng, "display_status": display, "ok_rate": pct(done - ng, done), "ng_rate": pct(ng, done),
            "overdue_minutes": int((now - p["due_at"]).total_seconds() // 60) if display == "OVERDUE" else 0}


def load_plan(cur, plan_id, for_update=False):
    if for_update:
        cur.execute("SELECT id FROM quality_inspection_plans WHERE id=%s FOR UPDATE", (plan_id,))
        cur.fetchall()
    cur.execute(PLAN_SELECT + " WHERE ip.id=%s", (plan_id,))
    p = cur.fetchone()
    if not p:
        raise HTTPException(status_code=404, detail="Inspection plan not found.")
    return enrich_plan(p, datetime.now())


def _as_dt(v, default):
    if not v:
        return default
    return v if isinstance(v, datetime) else datetime.fromisoformat(str(v))


def create_plan(cur, data, actor, source="MANUAL"):
    itype = (data.get("inspection_type") or "").upper()
    if itype not in INSPECTION_TYPES:
        raise HTTPException(status_code=400, detail="Select the inspection type.")
    wo = _wo(cur, int(data["work_order_id"])) if data.get("work_order_id") else None
    if itype == "IQC" and not data.get("material_lot_id"):
        raise HTTPException(status_code=400, detail="Select the material lot for incoming inspection.")
    if itype != "IQC" and not wo and not data.get("product_id"):
        raise HTTPException(status_code=400, detail="Select a work order or a product.")
    product_id = data.get("product_id") or (wo and wo["product_id"])
    freq_type = (data.get("frequency_type") or ("TIME" if itype in ("IPQC", "PATROL") else "LOT" if itype in ("FQC", "OQC", "IQC") else "ONCE")).upper()
    freq_value = data.get("frequency_value") or (int(setting(cur, "quality.ipqc_interval_min", 30)) if freq_type == "TIME" else None)
    sample = int(data.get("sample_size") or (setting(cur, "quality.ipqc_sample_size", 5, int) if itype == "IPQC" else DEFAULT_SAMPLE[itype]))
    if sample < 1 or sample > 200:
        raise HTTPException(status_code=400, detail="Sample size must be between 1 and 200.")
    planned = _as_dt(data.get("planned_at"), datetime.now().replace(microsecond=0))
    window = timedelta(minutes=freq_value) if freq_type == "TIME" and freq_value else timedelta(hours=4 if freq_type == "LOT" else 1)
    due = _as_dt(data.get("due_at"), planned + window)
    if due < planned:
        raise HTTPException(status_code=400, detail="The due time must be after the planned time.")
    shift = shift_of(planned, load_shifts(cur))
    revision = data.get("standard_revision_id") or active_revision(cur, product_id)
    no = _next_no(cur, "quality_inspection_plans", "plan_no", "IP")
    cur.execute(
        """INSERT INTO quality_inspection_plans (plan_no, inspection_type, work_order_id, product_id, machine_id, mold_id, material_lot_id,
               standard_revision_id, process, frequency_type, frequency_value, sample_size, planned_at, due_at, shift_id, inspector, source,
               parent_plan_id, remark, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (no, itype, wo and wo["id"], product_id, data.get("machine_id") or (wo and wo["machine_id"]), data.get("mold_id") or (wo and wo["mold_id"]),
         data.get("material_lot_id"), revision, data.get("process") or TYPE_PROCESS.get(itype, "INJECTION"), freq_type, freq_value, sample,
         planned, due, shift["id"] if shift else None, data.get("inspector") or None, source, data.get("parent_plan_id"), data.get("remark"), actor))
    pid = cur.lastrowid
    log(cur, "PLAN", pid, "CREATE", actor, None, "NOT_STARTED", data.get("remark") or (source if source != "MANUAL" else None))
    return {"id": pid, "plan_no": no}


def update_plan(cur, plan_id, data, actor):
    p = load_plan(cur, plan_id, for_update=True)
    _check_version(p, data.get("version"))
    if p["status"] != "NOT_STARTED":
        raise HTTPException(status_code=409, detail="Only plans not started yet can be edited.")
    planned = _as_dt(data.get("planned_at"), p["planned_at"])
    due = _as_dt(data.get("due_at"), p["due_at"])
    if due < planned:
        raise HTTPException(status_code=400, detail="The due time must be after the planned time.")
    sample = int(data.get("sample_size") or p["sample_size"])
    cur.execute("""UPDATE quality_inspection_plans SET sample_size=%s, planned_at=%s, due_at=%s, inspector=%s, process=%s, remark=%s,
                   frequency_value=%s, version=version+1, updated_by=%s WHERE id=%s""",
                (sample, planned, due, data.get("inspector") or None, data.get("process") or p["process"], data.get("remark"),
                 data.get("frequency_value") or p["frequency_value"], actor, plan_id))
    log(cur, "PLAN", plan_id, "UPDATE", actor, p["status"], p["status"])
    return {"id": plan_id}


def plan_action(cur, plan_id, action, data, actor):
    p = load_plan(cur, plan_id, for_update=True)
    _check_version(p, data.get("version"))
    note = (data.get("note") or "").strip() or None
    if action == "START":
        if p["status"] != "NOT_STARTED":
            raise HTTPException(status_code=409, detail="The plan has already started.")
        cur.execute("UPDATE quality_inspection_plans SET status='IN_PROGRESS', started_at=NOW(), inspector=COALESCE(%s, inspector, %s), version=version+1, updated_by=%s WHERE id=%s",
                    (data.get("inspector"), actor, actor, plan_id))
        log(cur, "PLAN", plan_id, "START", actor, "NOT_STARTED", "IN_PROGRESS", note)
    elif action == "CANCEL":
        if p["status"] not in ("NOT_STARTED", "IN_PROGRESS"):
            raise HTTPException(status_code=409, detail="Only open plans can be cancelled.")
        if not note:
            raise HTTPException(status_code=400, detail="A reason is required.")
        cur.execute("UPDATE quality_inspection_plans SET status='CANCELLED', version=version+1, updated_by=%s WHERE id=%s", (actor, plan_id))
        log(cur, "PLAN", plan_id, "CANCEL", actor, p["status"], "CANCELLED", note)
    elif action == "COMPLETE":
        # close a round with fewer samples than planned (reason required, kept in the audit trail)
        if p["status"] != "IN_PROGRESS" or not p["done_qty"]:
            raise HTTPException(status_code=409, detail="Record at least one sample first.")
        if p["done_qty"] < p["sample_size"] and not note:
            raise HTTPException(status_code=400, detail="Samples are missing: a reason is required.")
        return _complete_plan(cur, p, actor, note)
    else:
        raise HTTPException(status_code=400, detail="Unknown action.")
    return {"id": plan_id}


def record_sample(cur, plan_id, data, actor):
    """Record one inspected sample with its measured values; completes the round at the sample size."""
    p = load_plan(cur, plan_id, for_update=True)
    if p["status"] not in ("NOT_STARTED", "IN_PROGRESS"):
        raise HTTPException(status_code=409, detail="The plan is not open.")
    if p["done_qty"] >= p["sample_size"]:
        raise HTTPException(status_code=409, detail="All samples have been recorded.")
    t = targets(cur)
    chars = {c["id"]: c for c in characteristics(cur, p["standard_revision_id"])}
    given = {int(m["characteristic_id"]): m for m in data.get("measurements") or [] if m.get("characteristic_id")}
    default_gauge = data.get("equipment_id")
    warnings, rows, ng_chars = [], [], []
    for cid, c in chars.items():
        m = given.get(cid) or {}
        if (c.get("characteristic_type") or "VARIABLE") == "ATTRIBUTE":
            attr = (m.get("attr_result") or "").upper()
            if attr not in ("OK", "NG"):
                raise HTTPException(status_code=400, detail=f"Enter the result of {c['characteristic_name']}.")
            rows.append((cid, None, attr, attr, m.get("equipment_id") or None))
            if attr == "NG":
                ng_chars.append(c)
            continue
        if m.get("value") in (None, ""):
            raise HTTPException(status_code=400, detail=f"Enter the measured value of {c['characteristic_name']}.")
        value = float(m["value"])
        gauge = m.get("equipment_id") or default_gauge
        if not gauge:
            raise HTTPException(status_code=400, detail=f"Select the measuring equipment used for {c['characteristic_name']}.")
        w = check_gauge(cur, int(gauge), t["equipment_control"])
        if w and w not in warnings:
            warnings.append(w)
        bad = (c["lsl"] is not None and value < float(c["lsl"])) or (c["usl"] is not None and value > float(c["usl"]))
        rows.append((cid, value, None, "NG" if bad else "OK", int(gauge)))
        if bad:
            ng_chars.append(c)
    if not chars:
        manual = (data.get("result") or "").upper()
        if manual not in ("OK", "NG"):
            raise HTTPException(status_code=400, detail="Select the result of the sample.")
        result = manual
    else:
        result = "NG" if ng_chars or (data.get("result") or "").upper() == "NG" else "OK"
    defect = data.get("defect_code_id") or None
    if result == "NG" and not defect:
        if any((c.get("characteristic_type") or "VARIABLE") == "VARIABLE" for c in ng_chars):
            cur.execute("SELECT id FROM reason_codes WHERE reason_code='DEF-010'")
            row = cur.fetchone()
            defect = row["id"] if row else None
        if not defect:
            raise HTTPException(status_code=400, detail="Select the defect of the NG sample.")
    remark = "; ".join(filter(None, [data.get("remark"), *warnings])) or None
    cur.execute("""INSERT INTO quality_inspection_records (plan_id, sample_no, inspected_at, inspector, position, result, defect_code_id, is_reinspection, remark)
                   VALUES (%s,%s,NOW(),%s,%s,%s,%s,%s,%s)""",
                (plan_id, p["done_qty"] + 1, data.get("inspector") or p["inspector"] or actor, data.get("position"), result,
                 defect if result == "NG" else None, 1 if data.get("is_reinspection") else 0, remark and remark[:500]))
    rid = cur.lastrowid
    for cid, value, attr, res, gauge in rows:
        cur.execute("INSERT INTO quality_measurements (record_id, characteristic_id, measured_value, attr_result, result, equipment_id) VALUES (%s,%s,%s,%s,%s,%s)",
                    (rid, cid, value, attr, res, gauge))
    gauges = {g for *_, g in rows if g}
    if gauges:
        cur.execute(f"UPDATE measuring_equipment SET last_used_at=NOW() WHERE id IN ({','.join(['%s'] * len(gauges))})", tuple(gauges))
    if p["status"] == "NOT_STARTED":
        cur.execute("UPDATE quality_inspection_plans SET status='IN_PROGRESS', started_at=NOW(), inspector=COALESCE(inspector, %s), version=version+1, updated_by=%s WHERE id=%s",
                    (data.get("inspector") or actor, actor, plan_id))
        log(cur, "PLAN", plan_id, "START", actor, "NOT_STARTED", "IN_PROGRESS")
    out = {"record_id": rid, "result": result, "warnings": warnings}
    if p["done_qty"] + 1 >= p["sample_size"]:
        out.update(_complete_plan(cur, load_plan(cur, plan_id), actor))
    return out


def _complete_plan(cur, p, actor, note=None):
    cur.execute("SELECT id, result, defect_code_id FROM quality_inspection_records WHERE plan_id=%s", (p["id"],))
    recs = cur.fetchall()
    ng = [r for r in recs if r["result"] == "NG"]
    result = "NG" if ng else "OK"
    cur.execute("UPDATE quality_inspection_plans SET status='COMPLETED', result=%s, completed_at=NOW(), version=version+1, updated_by=%s WHERE id=%s",
                (result, actor, p["id"]))
    log(cur, "PLAN", p["id"], "COMPLETE", actor, p["status"], "COMPLETED", f"{result} · {len(recs)}/{p['sample_size']}" + (f" · {note}" if note else ""))
    out = {"plan_status": "COMPLETED", "plan_result": result}
    if ng:
        cur.execute("""SELECT c.id, c.is_critical FROM quality_measurements m JOIN quality_standard_characteristics c ON c.id=m.characteristic_id
                       WHERE m.record_id IN (%s) AND m.result='NG'""" % ",".join(str(r["id"]) for r in ng))
        bad = cur.fetchall()
        critical = any(b["is_critical"] for b in bad)
        ratio = len(ng) / max(len(recs), 1)
        priority = "CRITICAL" if critical else "HIGH" if p["inspection_type"] in ("FAI", "OQC", "FQC") or ratio >= 0.4 else "MEDIUM"
        defect = Counter(r["defect_code_id"] for r in ng if r["defect_code_id"]).most_common(1)
        created = create_ng(cur, {
            "source": p["inspection_type"], "inspection_plan_id": p["id"], "work_order_id": p["work_order_id"], "product_id": p["product_id"],
            "machine_id": p["machine_id"], "mold_id": p["mold_id"], "material_lot_id": p["material_lot_id"],
            "characteristic_id": bad[0]["id"] if bad else None, "defect_code_id": defect[0][0] if defect else None, "process": p["process"],
            "ng_qty": len(ng), "inspected_qty": len(recs), "priority": priority,
            "description": f"{p['inspection_type']} {p['plan_no']}: {len(ng)}/{len(recs)} NG samples"}, actor)
        out["ng"] = created
    if p["frequency_type"] == "TIME" and p["frequency_value"] and p["work_order_id"]:
        _next_round(cur, p, actor)
    return out


def _next_round(cur, p, actor):
    """Schedule the next frequency round while the Work Order keeps running."""
    cur.execute("SELECT status FROM production_plan_details WHERE id=%s", (p["work_order_id"],))
    wo = cur.fetchone()
    if not wo or wo["status"] != "IN_PRODUCTION":
        return
    cur.execute("""SELECT COUNT(*) n FROM quality_inspection_plans WHERE work_order_id=%s AND inspection_type=%s AND status IN ('NOT_STARTED','IN_PROGRESS')""",
                (p["work_order_id"], p["inspection_type"]))
    if cur.fetchone()["n"]:
        return
    planned = max(datetime.now().replace(microsecond=0), p["planned_at"] + timedelta(minutes=int(p["frequency_value"])))
    create_plan(cur, {"inspection_type": p["inspection_type"], "work_order_id": p["work_order_id"], "frequency_type": "TIME",
                      "frequency_value": p["frequency_value"], "sample_size": p["sample_size"], "planned_at": planned, "process": p["process"],
                      "inspector": p["inspector"], "parent_plan_id": p["id"]}, actor, "AUTO_FREQUENCY")


def on_wo_start(cur, wo, actor):
    """Work Order start: first article inspection + IPQC rounds."""
    cur.execute("SELECT inspection_type FROM quality_inspection_plans WHERE work_order_id=%s AND status<>'CANCELLED'", (wo["id"],))
    have = {r["inspection_type"] for r in cur.fetchall()}
    if "FAI" not in have:
        create_plan(cur, {"inspection_type": "FAI", "work_order_id": wo["id"], "frequency_type": "ONCE",
                          "sample_size": min(max(int(wo.get("cavity") or 1), 1), 8) if int(wo.get("cavity") or 1) > 1 else DEFAULT_SAMPLE["FAI"]}, actor, "AUTO_WO")
    if "IPQC" not in have:
        interval = int(setting(cur, "quality.ipqc_interval_min", 30))
        create_plan(cur, {"inspection_type": "IPQC", "work_order_id": wo["id"], "frequency_type": "TIME", "frequency_value": interval,
                          "planned_at": datetime.now().replace(microsecond=0) + timedelta(minutes=interval)}, actor, "AUTO_WO")


def on_wo_end(cur, wo, actor):
    """Work Order end: open IPQC / patrol rounds not started are cancelled, the final inspection is planned."""
    cur.execute("""SELECT id, status FROM quality_inspection_plans WHERE work_order_id=%s AND inspection_type IN ('IPQC','PATROL') AND status='NOT_STARTED'""", (wo["id"],))
    for r in cur.fetchall():
        cur.execute("UPDATE quality_inspection_plans SET status='CANCELLED', version=version+1, updated_by=%s WHERE id=%s", (actor, r["id"]))
        log(cur, "PLAN", r["id"], "CANCEL", actor, r["status"], "CANCELLED", "Work order ended")
    cur.execute("SELECT COUNT(*) n FROM quality_inspection_plans WHERE work_order_id=%s AND inspection_type='FQC' AND status<>'CANCELLED'", (wo["id"],))
    if not cur.fetchone()["n"]:
        create_plan(cur, {"inspection_type": "FQC", "work_order_id": wo["id"], "frequency_type": "LOT"}, actor, "AUTO_WO")


def sync_plans(cur):
    """Running Work Orders always have their next IPQC round planned."""
    interval = int(setting(cur, "quality.ipqc_interval_min", 30))
    cur.execute("""SELECT d.id FROM production_plan_details d WHERE d.status='IN_PRODUCTION'
                   AND NOT EXISTS (SELECT 1 FROM quality_inspection_plans ip WHERE ip.work_order_id=d.id AND ip.inspection_type='IPQC'
                                   AND ip.status IN ('NOT_STARTED','IN_PROGRESS'))""")
    for row in cur.fetchall():
        cur.execute("""SELECT planned_at, frequency_value, sample_size FROM quality_inspection_plans WHERE work_order_id=%s AND inspection_type='IPQC'
                       AND status='COMPLETED' ORDER BY planned_at DESC LIMIT 1""", (row["id"],))
        last = cur.fetchone()
        every = int(last["frequency_value"] or interval) if last else interval
        planned = max(datetime.now().replace(microsecond=0), last["planned_at"] + timedelta(minutes=every)) if last else datetime.now().replace(microsecond=0)
        create_plan(cur, {"inspection_type": "IPQC", "work_order_id": row["id"], "frequency_type": "TIME", "frequency_value": every,
                          "sample_size": last["sample_size"] if last else None, "planned_at": planned}, "SYSTEM", "AUTO_FREQUENCY")


def plan_records(cur, plan_id):
    cur.execute("""SELECT r.*, rc.reason_code AS defect_code, rc.reason_name AS defect_name FROM quality_inspection_records r
                   LEFT JOIN reason_codes rc ON rc.id=r.defect_code_id WHERE r.plan_id=%s ORDER BY r.sample_no""", (plan_id,))
    recs = cur.fetchall()
    if recs:
        cur.execute(f"""SELECT m.*, me.equipment_code AS gauge_code FROM quality_measurements m LEFT JOIN measuring_equipment me ON me.id=m.equipment_id
                        WHERE m.record_id IN ({','.join(['%s'] * len(recs))})""", tuple(r["id"] for r in recs))
        by = {}
        for m in cur.fetchall():
            by.setdefault(m["record_id"], []).append(m)
        for r in recs:
            r["measurements"] = by.get(r["id"], [])
    return recs


# =========================================================
# NG RECORDS
# =========================================================

NG_SELECT = """SELECT n.*, d.wo_no, p.product_code, p.product_name, eq.equipment_code AS machine_code, mo.mold_code, l.lot_no,
        rc.reason_code AS defect_code, rc.reason_name AS defect_name, c.characteristic_name, ip.plan_no, rp.ng_no AS repeat_of_no
    FROM quality_ng_records n
    LEFT JOIN production_plan_details d ON d.id=n.work_order_id
    LEFT JOIN products p ON p.id=n.product_id
    LEFT JOIN equipment eq ON eq.id=n.machine_id
    LEFT JOIN molds mo ON mo.id=n.mold_id
    LEFT JOIN material_lots l ON l.id=n.material_lot_id
    LEFT JOIN reason_codes rc ON rc.id=n.defect_code_id
    LEFT JOIN quality_standard_characteristics c ON c.id=n.characteristic_id
    LEFT JOIN quality_inspection_plans ip ON ip.id=n.inspection_plan_id
    LEFT JOIN quality_ng_records rp ON rp.id=n.repeat_of_id"""

PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def enrich_ng(n, today):
    overdue = n["status"] in NG_OPEN and n["due_date"] and n["due_date"] < today
    return {**n, "ng_rate": pct(n["ng_qty"], n["inspected_qty"]) if n.get("inspected_qty") else None, "overdue": bool(overdue),
            "is_repeat": bool(n.get("repeat_of_id"))}


def load_ng(cur, ng_id, for_update=False):
    if for_update:
        cur.execute("SELECT id FROM quality_ng_records WHERE id=%s FOR UPDATE", (ng_id,))
        cur.fetchall()
    cur.execute(NG_SELECT + " WHERE n.id=%s", (ng_id,))
    n = cur.fetchone()
    if not n:
        raise HTTPException(status_code=404, detail="NG record not found.")
    return enrich_ng(n, date.today())


def ng_event(cur, ng_id, action, actor, from_status=None, to_status=None, note=None):
    cur.execute("INSERT INTO quality_ng_events (ng_id, action, from_status, to_status, actor, note) VALUES (%s,%s,%s,%s,%s,%s)",
                (ng_id, action, from_status, to_status, actor or "SYSTEM", (note or None) and str(note)[:1000]))


def find_repeat(cur, defect_code_id, product_id, mold_id=None, exclude_id=None):
    if not defect_code_id:
        return None
    window = int(setting(cur, "quality.repeat_window_days", 30))
    # a repeat = the same defect on the same product and mold again after an earlier NG record was closed (corrective action not effective)
    cur.execute("""SELECT id FROM quality_ng_records WHERE defect_code_id=%s AND product_id <=> %s AND mold_id <=> %s AND id<>%s AND status='CLOSED'
                   AND closed_at >= NOW() - INTERVAL %s DAY ORDER BY closed_at DESC LIMIT 1""", (defect_code_id, product_id, mold_id, exclude_id or 0, window))
    row = cur.fetchone()
    return row["id"] if row else None


def create_ng(cur, data, actor):
    source = (data.get("source") or "").upper()
    if source not in NG_SOURCES:
        raise HTTPException(status_code=400, detail="Select the NG source.")
    qty = int(data.get("ng_qty") or 0)
    if qty <= 0:
        raise HTTPException(status_code=400, detail="The NG quantity must be greater than zero.")
    wo = _wo(cur, int(data["work_order_id"])) if data.get("work_order_id") else None
    product_id = data.get("product_id") or (wo and wo["product_id"])
    if not (wo or product_id or data.get("material_lot_id")):
        raise HTTPException(status_code=400, detail="Select a work order, a product or a material lot.")
    priority = (data.get("priority") or ("CRITICAL" if source == "CUSTOMER" else "HIGH" if qty >= 100 else "MEDIUM")).upper()
    if priority not in PRIORITIES:
        raise HTTPException(status_code=400, detail="Invalid priority.")
    detected = _as_dt(data.get("detected_at"), datetime.now().replace(microsecond=0))
    due = data.get("due_date") or (detected.date() + timedelta(days=NG_DUE_DAYS[priority]))
    repeat = find_repeat(cur, data.get("defect_code_id"), product_id, data.get("mold_id") or (wo and wo["mold_id"]))
    no = _next_no(cur, "quality_ng_records", "ng_no", "NG")
    cur.execute(
        """INSERT INTO quality_ng_records (ng_no, detected_at, source, inspection_plan_id, work_order_id, product_id, machine_id, mold_id, material_lot_id,
               characteristic_id, defect_code_id, process, ng_qty, inspected_qty, description, priority, owner, detected_by, due_date, repeat_of_id, created_by)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (no, detected, source, data.get("inspection_plan_id"), wo and wo["id"], product_id, data.get("machine_id") or (wo and wo["machine_id"]),
         data.get("mold_id") or (wo and wo["mold_id"]), data.get("material_lot_id"), data.get("characteristic_id"), data.get("defect_code_id"),
         data.get("process") or TYPE_PROCESS.get(source, "INJECTION"), qty, data.get("inspected_qty"), data.get("description"), priority,
         data.get("owner") or None, data.get("detected_by") or actor, due, repeat, actor))
    nid = cur.lastrowid
    ng_event(cur, nid, "CREATE", actor, None, "NEW", data.get("description"))
    if repeat:
        cur.execute("SELECT ng_no FROM quality_ng_records WHERE id=%s", (repeat,))
        ng_event(cur, nid, "REPEAT", "SYSTEM", note=f"Repeat of {cur.fetchone()['ng_no']}")
    if wo and (data.get("hold_wo") or (priority == "CRITICAL" and setting(cur, "quality.auto_hold_critical", 0, int))):
        _hold_wo(cur, nid, no, wo["id"], actor)
    return {"id": nid, "ng_no": no}


def _hold_wo(cur, ng_id, ng_no, wo_id, actor):
    from app.modules.production.execution import service as execution
    wo = _wo(cur, wo_id)
    if wo["status"] not in ("IN_PRODUCTION", "RELEASED", "SCHEDULED"):
        raise HTTPException(status_code=409, detail=f"Work order {wo['wo_no']} cannot be held ({wo['status']}).")
    cur.execute("SELECT id FROM reason_codes WHERE reason_code='DT-QUAL'")
    reason = cur.fetchone()
    execution.hold(cur, wo_id, reason["id"] if reason else None, f"Quality hold {ng_no}", actor, wo["version"])
    cur.execute("UPDATE quality_ng_records SET wo_held=1 WHERE id=%s", (ng_id,))
    ng_event(cur, ng_id, "HOLD_WO", actor, note=wo["wo_no"])


NG_FIELDS = ["description", "priority", "owner", "due_date", "defect_code_id", "process", "ng_qty", "disposition", "rework_qty", "scrap_qty",
             "containment", "root_cause_category", "root_cause", "corrective_action", "preventive_action", "verification_result"]


def _save_ng_fields(cur, n, data, actor):
    sets, params = [], []
    for f in NG_FIELDS:
        if f in data and data[f] is not None:
            v = data[f]
            if f in ("priority", "disposition", "root_cause_category") and v == "":
                continue
            if f in ("rework_qty", "scrap_qty", "ng_qty") and int(v) < 0:
                raise HTTPException(status_code=400, detail="Quantities cannot be negative.")
            sets.append(f"{f}=%s"); params.append(v if v != "" else None)
    merged = {**n, **{f: data[f] for f in NG_FIELDS if f in data and data[f] is not None}}
    if int(merged.get("rework_qty") or 0) + int(merged.get("scrap_qty") or 0) > int(merged.get("ng_qty") or 0):
        raise HTTPException(status_code=400, detail="Rework + scrap cannot exceed the NG quantity.")
    if sets:
        cur.execute(f"UPDATE quality_ng_records SET {', '.join(sets)}, version=version+1, updated_by=%s WHERE id=%s", (*params, actor, n["id"]))
    return merged


def update_ng(cur, ng_id, data, actor):
    n = load_ng(cur, ng_id, for_update=True)
    _check_version(n, data.get("version"))
    if n["status"] == "CLOSED":
        raise HTTPException(status_code=409, detail="Closed NG records cannot be edited: reopen it first.")
    _save_ng_fields(cur, n, data, actor)
    if data.get("defect_code_id") and data.get("defect_code_id") != n["defect_code_id"] and not n["repeat_of_id"]:
        rep = find_repeat(cur, data["defect_code_id"], n["product_id"], n["mold_id"], ng_id)
        if rep:
            cur.execute("UPDATE quality_ng_records SET repeat_of_id=%s WHERE id=%s", (rep, ng_id))
    ng_event(cur, ng_id, "UPDATE", actor, n["status"], n["status"], data.get("note"))
    return {"id": ng_id}


def ng_action(cur, ng_id, action, data, actor):
    n = load_ng(cur, ng_id, for_update=True)
    _check_version(n, data.get("version"))
    note = (data.get("note") or "").strip() or None
    status = n["status"]
    if action == "HOLD_WO":
        if not n["work_order_id"]:
            raise HTTPException(status_code=409, detail="The NG record has no work order.")
        if n["wo_held"]:
            raise HTTPException(status_code=409, detail="The work order is already held.")
        _hold_wo(cur, ng_id, n["ng_no"], n["work_order_id"], actor)
        return {"id": ng_id}
    if status == "CLOSED" and action != "REOPEN":
        raise HTTPException(status_code=409, detail="The NG record is closed.")
    merged = _save_ng_fields(cur, n, data, actor)
    transitions = {
        "START_INVESTIGATION": (("NEW", "REOPENED"), "INVESTIGATING"),
        "START_CA": (("INVESTIGATING", "REOPENED"), "CA_IN_PROGRESS"),
        "SUBMIT_VERIFY": (("CA_IN_PROGRESS",), "WAITING_VERIFICATION"),
        "VERIFY": (("WAITING_VERIFICATION",), "CLOSED"),
        "REJECT_VERIFY": (("WAITING_VERIFICATION",), "CA_IN_PROGRESS"),
        "REOPEN": (("CLOSED",), "REOPENED"),
    }
    if action not in transitions:
        raise HTTPException(status_code=400, detail="Unknown action.")
    allowed, target = transitions[action]
    if status not in allowed:
        raise HTTPException(status_code=409, detail=f"Not allowed from status {status}.")
    if action == "START_INVESTIGATION" and not merged.get("owner"):
        raise HTTPException(status_code=400, detail="Assign an owner first.")
    if action == "START_CA":
        missing = [k for k in ("root_cause_category", "root_cause", "corrective_action") if not merged.get(k)]
        if missing:
            raise HTTPException(status_code=400, detail="Root cause (4M) and corrective action are required.")
    if action == "SUBMIT_VERIFY" and not merged.get("disposition"):
        raise HTTPException(status_code=400, detail="Record the disposition of the NG pieces first.")
    if action == "VERIFY" and not merged.get("verification_result"):
        raise HTTPException(status_code=400, detail="Record the verification result first.")
    if action in ("REJECT_VERIFY", "REOPEN") and not note:
        raise HTTPException(status_code=400, detail="A reason is required.")
    cur.execute(f"UPDATE quality_ng_records SET status=%s{', closed_at=NOW()' if target == 'CLOSED' else ', closed_at=NULL' if action == 'REOPEN' else ''}, version=version+1, updated_by=%s WHERE id=%s",
                (target, actor, ng_id))
    ng_event(cur, ng_id, action, actor, status, target, note)
    return {"id": ng_id}


DEFECT_KEYWORDS = {"DEF-001": ("flash", "bavia", "ba via"), "DEF-002": ("short shot", "thiếu nhựa", "thieu nhua"), "DEF-003": ("burr",),
                   "DEF-004": ("warpage", "deform", "cong vênh", "biến dạng"), "DEF-005": ("color", "màu"), "DEF-006": ("sink", "lõm"),
                   "DEF-007": ("silver",), "DEF-008": ("black spot", "chấm đen"), "DEF-009": ("weld line",), "DEF-011": ("crack", "nứt"),
                   "DEF-012": ("scratch", "trầy", "xước"), "DEF-013": ("burn", "cháy")}


def defect_from_text(cur, text):
    """Map a free-text reject reason to a defect code (keywords of the defect name, EN / VI)."""
    low = (text or "").lower()
    for code, words in DEFECT_KEYWORDS.items():
        if any(w in low for w in words):
            cur.execute("SELECT id FROM reason_codes WHERE reason_code=%s", (code,))
            row = cur.fetchone()
            return row["id"] if row else None
    return None


def on_production_reject(cur, wo, qty, reason_code_id, reason_text, actor):
    """Reject pieces reported at the machine: one open NG record per Work Order + defect + day (quantities accumulate)."""
    if not qty or qty <= 0:
        return
    reason_code_id = reason_code_id or defect_from_text(cur, reason_text)
    cur.execute("""SELECT id, ng_qty FROM quality_ng_records WHERE source='PRODUCTION' AND work_order_id=%s AND defect_code_id <=> %s
                   AND status='NEW' AND DATE(detected_at)=CURDATE() ORDER BY id DESC LIMIT 1""", (wo["id"], reason_code_id))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE quality_ng_records SET ng_qty=ng_qty+%s, priority=IF(ng_qty>=100 AND priority IN ('LOW','MEDIUM'),'HIGH',priority), updated_by=%s WHERE id=%s",
                    (qty, actor, row["id"]))
        ng_event(cur, row["id"], "ADD_QTY", actor, note=f"+{qty}" + (f" · {reason_text}" if reason_text else ""))
        return
    create_ng(cur, {"source": "PRODUCTION", "work_order_id": wo["id"], "machine_id": wo.get("machine_id"), "mold_id": wo.get("mold_id"),
                    "defect_code_id": reason_code_id, "ng_qty": qty, "priority": "HIGH" if qty >= 100 else "MEDIUM",
                    "description": reason_text or "Reject reported at the machine"}, actor)


def ng_timeline(cur, ng_id):
    cur.execute("SELECT * FROM quality_ng_events WHERE ng_id=%s ORDER BY event_at DESC, id DESC", (ng_id,))
    return cur.fetchall()


# =========================================================
# ALERTS
# =========================================================

AUTO_KINDS = ("SPC_OOC", "SPC_TREND", "WO_NG", "MACHINE_NG", "MOLD_REPEAT", "NG_CRITICAL", "NG_OVERDUE", "NG_CUSTOMER", "CAL_OVERDUE",
              "CAL_DUE", "NOT_CALIBRATED", "GAUGE_LOCKED", "INSPECTION_OVERDUE", "FPY_LOW", "PPM_HIGH")


def raise_alert(cur, key, kind, severity, message, ref_type=None, ref_id=None, ref_code=None):
    cur.execute(f"SELECT id, status FROM quality_alerts WHERE alert_key=%s AND status IN ({','.join(['%s'] * len(ALERT_OPEN))}) LIMIT 1", (key, *ALERT_OPEN))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE quality_alerts SET severity=%s, message=%s WHERE id=%s", (severity, message[:500], row["id"]))
        return row["id"]
    cur.execute("""INSERT INTO quality_alerts (alert_key, kind, severity, message, ref_type, ref_id, ref_code) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                (key, kind, severity, message[:500], ref_type, ref_id, ref_code))
    return cur.lastrowid


def alert_action(cur, alert_id, action, data, actor):
    cur.execute("SELECT * FROM quality_alerts WHERE id=%s FOR UPDATE", (alert_id,))
    a = cur.fetchone()
    if not a:
        raise HTTPException(status_code=404, detail="Alert not found.")
    flow = {"ACKNOWLEDGE": (("NEW",), "ACKNOWLEDGED"), "START": (("NEW", "ACKNOWLEDGED"), "IN_PROGRESS"),
            "RESOLVE": (ALERT_OPEN, "RESOLVED"), "CLOSE": (ALERT_OPEN + ("RESOLVED",), "CLOSED")}
    if action == "ASSIGN":
        cur.execute("UPDATE quality_alerts SET owner=%s, updated_by=%s WHERE id=%s", (data.get("owner") or None, actor, alert_id))
        log(cur, "ALERT", alert_id, "ASSIGN", actor, a["status"], a["status"], data.get("owner"))
        return {"id": alert_id}
    if action not in flow:
        raise HTTPException(status_code=400, detail="Unknown action.")
    allowed, target = flow[action]
    if a["status"] not in allowed:
        raise HTTPException(status_code=409, detail=f"Not allowed from status {a['status']}.")
    cur.execute(f"UPDATE quality_alerts SET status=%s, owner=COALESCE(owner, %s){', resolved_at=NOW()' if target in ('RESOLVED', 'CLOSED') else ''}, updated_by=%s WHERE id=%s",
                (target, actor, actor, alert_id))
    log(cur, "ALERT", alert_id, action, actor, a["status"], target, data.get("note"))
    return {"id": alert_id}


def sync_alerts(cur, spc_states):
    """Raise the alerts of every current exception and auto-resolve those whose condition disappeared.
    spc_states: [{product_id, product_code, characteristic_id, name, status, is_critical}]"""
    t = targets(cur)
    now, today = datetime.now(), date.today()
    active = set()

    def add(key, kind, severity, message, ref_type=None, ref_id=None, ref_code=None):
        active.add(key)
        raise_alert(cur, key, kind, severity, message, ref_type, ref_id, ref_code)

    for s in spc_states:
        if s["status"] == "OUT_OF_CONTROL":
            add(f"SPC_OOC:{s['characteristic_id']}", "SPC_OOC", "CRITICAL" if s["is_critical"] else "HIGH",
                f"SPC out of control: {s['name']} - {s['product_code']}", "PRODUCT", s["product_id"], s["product_code"])
        elif s["status"] == "WARNING":
            add(f"SPC_TREND:{s['characteristic_id']}", "SPC_TREND", "MEDIUM", f"SPC trend / capability warning: {s['name']} - {s['product_code']}",
                "PRODUCT", s["product_id"], s["product_code"])
    # Work Orders: inspection NG rate of today
    cur.execute("""SELECT ip.work_order_id, d.wo_no, COUNT(*) n, SUM(r.result='NG') ng FROM quality_inspection_records r
                   JOIN quality_inspection_plans ip ON ip.id=r.plan_id JOIN production_plan_details d ON d.id=ip.work_order_id
                   WHERE r.inspected_at >= CURDATE() AND d.status IN ('IN_PRODUCTION','ON_HOLD') GROUP BY ip.work_order_id, d.wo_no""")
    for w in cur.fetchall():
        rate = pct(int(w["ng"] or 0), int(w["n"]))
        if w["n"] >= 5 and rate is not None and rate >= t["wo_critical"]:
            add(f"WO_NG:{w['work_order_id']}", "WO_NG", "HIGH", f"NG rate {rate}% on work order {w['wo_no']}", "WO", w["work_order_id"], w["wo_no"])
    cur.execute("""SELECT ip.machine_id, eq.equipment_code, COUNT(*) n, SUM(r.result='NG') ng FROM quality_inspection_records r
                   JOIN quality_inspection_plans ip ON ip.id=r.plan_id JOIN equipment eq ON eq.id=ip.machine_id
                   WHERE r.inspected_at >= CURDATE() GROUP BY ip.machine_id, eq.equipment_code""")
    for m in cur.fetchall():
        rate = pct(int(m["ng"] or 0), int(m["n"]))
        if m["n"] >= 10 and rate is not None and rate >= t["wo_attention"]:
            add(f"MACHINE_NG:{m['machine_id']}", "MACHINE_NG", "HIGH" if rate >= t["wo_critical"] else "MEDIUM",
                f"Machine {m['equipment_code']} NG rate {rate}% today", "MACHINE", m["machine_id"], m["equipment_code"])
    cur.execute("""SELECT n.mold_id, mo.mold_code, rc.reason_name, COUNT(*) c FROM quality_ng_records n JOIN molds mo ON mo.id=n.mold_id
                   JOIN reason_codes rc ON rc.id=n.defect_code_id WHERE n.detected_at >= NOW() - INTERVAL 7 DAY
                   GROUP BY n.mold_id, mo.mold_code, rc.reason_name HAVING c >= 3""")
    for m in cur.fetchall():
        add(f"MOLD_REPEAT:{m['mold_id']}:{m['reason_name']}", "MOLD_REPEAT", "MEDIUM", f"Mold {m['mold_code']} repeats defect {m['reason_name']} ({m['c']}x / 7 days)",
            "MOLD", m["mold_id"], m["mold_code"])
    cur.execute("SELECT id, ng_no, priority, status, source, due_date FROM quality_ng_records WHERE status IN %s" % str(NG_OPEN))
    for n in cur.fetchall():
        if n["source"] == "CUSTOMER":
            add(f"NG_CUSTOMER:{n['id']}", "NG_CUSTOMER", "CRITICAL", f"Customer complaint {n['ng_no']} is open", "NG", n["id"], n["ng_no"])
        elif n["priority"] == "CRITICAL" and n["status"] == "NEW":
            add(f"NG_CRITICAL:{n['id']}", "NG_CRITICAL", "CRITICAL", f"Critical NG {n['ng_no']} not acknowledged", "NG", n["id"], n["ng_no"])
        if n["due_date"] and n["due_date"] < today:
            add(f"NG_OVERDUE:{n['id']}", "NG_OVERDUE", "MEDIUM", f"NG {n['ng_no']} is past its due date", "NG", n["id"], n["ng_no"])
    for e in load_equipment(cur):
        if e["state"] == "OVERDUE":
            add(f"CAL_OVERDUE:{e['id']}", "CAL_OVERDUE", "HIGH", f"{e['equipment_code']} calibration overdue by {-e['days_left']} days", "GAUGE", e["id"], e["equipment_code"])
        elif e["state"] == "DUE_SOON":
            add(f"CAL_DUE:{e['id']}", "CAL_DUE", "LOW", f"{e['equipment_code']} calibration due in {e['days_left']} days", "GAUGE", e["id"], e["equipment_code"])
        elif e["state"] == "NOT_CALIBRATED":
            add(f"NOT_CALIBRATED:{e['id']}", "NOT_CALIBRATED", "MEDIUM", f"{e['equipment_code']} has never been calibrated", "GAUGE", e["id"], e["equipment_code"])
        if e["state"] not in USABLE_STATES and e["state"] != "SCRAPPED" and e["last_used_at"] and e["last_used_at"] >= now - timedelta(days=1):
            add(f"GAUGE_LOCKED:{e['id']}", "GAUGE_LOCKED", "CRITICAL", f"{e['equipment_code']} used for inspection while not valid", "GAUGE", e["id"], e["equipment_code"])
    cur.execute("""SELECT id, plan_no, inspection_type, due_at FROM quality_inspection_plans WHERE status IN ('NOT_STARTED','IN_PROGRESS') AND due_at < NOW()""")
    for p in cur.fetchall():
        add(f"INSPECTION_OVERDUE:{p['id']}", "INSPECTION_OVERDUE", "MEDIUM", f"{p['inspection_type']} {p['plan_no']} not done in time", "PLAN", p["id"], p["plan_no"])
    # plant level of today
    cur.execute("SELECT COUNT(*) n, SUM(result='OK') ok FROM quality_inspection_records WHERE inspected_at >= CURDATE() AND is_reinspection=0")
    r = cur.fetchone()
    fpy = pct(int(r["ok"] or 0), int(r["n"] or 0))
    if r["n"] and r["n"] >= 20 and fpy is not None and fpy < t["fpy"]:
        add("FPY_LOW", "FPY_LOW", "HIGH", f"FPY today {fpy}% below target {t['fpy']:g}%")
    cur.execute("SELECT SUM(good_qty) g, SUM(reject_qty) r FROM production_execution_logs WHERE event_at >= CURDATE()")
    r = cur.fetchone()
    produced = int(r["g"] or 0) + int(r["r"] or 0)
    if produced >= 500:
        ppm = int(r["r"] or 0) / produced * 1e6
        if ppm > t["ppm"]:
            add("PPM_HIGH", "PPM_HIGH", "HIGH", f"PPM today {ppm:,.0f} above target {t['ppm']:,.0f}")
    # conditions gone: open alerts not yet taken over are resolved by the system
    cur.execute(f"SELECT id, alert_key, kind FROM quality_alerts WHERE status IN ('NEW','ACKNOWLEDGED') AND kind IN ({','.join(['%s'] * len(AUTO_KINDS))})", AUTO_KINDS)
    for a in cur.fetchall():
        if a["alert_key"] not in active:
            cur.execute("UPDATE quality_alerts SET status='RESOLVED', resolved_at=NOW(), updated_by='SYSTEM' WHERE id=%s", (a["id"],))


def list_alerts(cur, limit=50, kinds=None):
    where = f"WHERE a.kind IN ({','.join(['%s'] * len(kinds))})" if kinds else ""
    cur.execute(f"""SELECT a.* FROM quality_alerts a {where}
                    ORDER BY (a.status IN ('NEW','ACKNOWLEDGED','IN_PROGRESS')) DESC, FIELD(a.severity,'CRITICAL','HIGH','MEDIUM','LOW'), a.raised_at DESC
                    LIMIT %s""", (*(kinds or ()), limit))
    return cur.fetchall()


def spc_json(v):
    return json.loads(json.dumps(v, default=str))
