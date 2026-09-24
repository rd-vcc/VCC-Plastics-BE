"""Demo data for Maintenance Management (6.1 - 6.7). Re-runnable: rebuilds all maintenance_* rows.

- Manual PM plans for machines / equipment; AUTO plans for molds (PM interval) and calibrated tools (sync).
- About 6 weeks of completed work orders: linked to the existing DT-PM / DT-BRK downtime events of the machines and to
  the mold maintenance / repair events already recorded in Mold Management.
- Open work orders matching the current state: IMM-004 in PM (open DT-PM downtime), MLD-003 in maintenance, MLD-004 in
  repair (paused, waiting for a core insert), tools CT-004 / HD-003 in maintenance, calibration of MS-003 overdue...
- Maintenance requests in every status (some created from alarms / PM plan / quality).

Run with:

    python -m scripts.seed_maintenance_demo
"""

import json
from datetime import date, datetime, timedelta

from app.core.database import get_vcc_plastics_connection
from app.modules.machine import service as machine
from app.modules.maintenance import service as ms

TECHS = ["Nguyen Van A", "Tran Van B", "Le Thi C", "Pham Van D", "Vu Thi E"]
RATE = 120000
TODAY = date.today()
NOW = datetime.now().replace(second=0, microsecond=0)
D = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M")
T = lambda days, hh=8, mm=0: datetime.combine(TODAY + timedelta(days=days), datetime.min.time()).replace(hour=hh, minute=mm)
counters = {}


def no(prefix, when):
    stem = f"{prefix}-{when:%y%m%d}-"
    counters[stem] = counters.get(stem, 0) + 1
    return f"{stem}{counters[stem]:04d}"


def ids(cur, table, code_col, codes):
    cur.execute(f"SELECT id, {code_col} AS c FROM {table} WHERE {code_col} IN ({','.join(['%s'] * len(codes))})", tuple(codes))
    return {r["c"]: r["id"] for r in cur.fetchall()}


def ev(cur, owner, oid, action, actor, frm, to, at, note=None):
    cur.execute("INSERT INTO maintenance_events (owner_type, owner_id, action, from_status, to_status, note, actor, event_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (owner, oid, action, frm, to, note, actor, at))


def plan(cur, asset_type, asset_id, task, plan_type, interval, due, tech, est, wo_type="PM", priority="MEDIUM", checklist=None, parts=None):
    created = NOW - timedelta(days=60)
    cur.execute("""INSERT INTO maintenance_plans (plan_no, asset_type, asset_id, task_name, wo_type, plan_type, frequency_unit, interval_value, next_due_date,
                   priority, technician, est_minutes, checklist, spare_parts, source, created_by, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,'DAY',%s,%s,%s,%s,%s,%s,%s,'MANUAL','Maintenance Planner',%s)""",
                (no("PM", created), asset_type, asset_id, task, wo_type, plan_type, interval, due, priority, tech, est,
                 json.dumps(checklist or ms.default_checklist(asset_type, wo_type)), json.dumps(parts or []), created))
    pid = cur.lastrowid
    ev(cur, "PLAN", pid, "CREATE", "Maintenance Planner", None, "ACTIVE", created)
    return pid


def wo(cur, asset_type, asset_id, wo_type, task, status, tech, created, *, priority="MEDIUM", plan_id=None, request_id=None, start=None, finish=None,
       due=None, planned_start=None, planned_finish=None, est=None, downtime_id=None, paused_min=0, paused_at=None, stage=None, parts=(), checklist_done=None,
       result=None, root_cause=None, requested_by="Production", calibration=None, description=None, extra_cost=0, verify_at=None, close=False):
    prefix = ms.WO_PREFIX[asset_type]
    completed = verify_at or (finish + timedelta(minutes=20) if finish and status in ("COMPLETED", "CLOSED") else None)
    cur.execute("""INSERT INTO maintenance_work_orders (wo_no, asset_type, asset_id, wo_type, priority, status, stage, task_name, description, plan_id, request_id,
                   technician, requested_by, planned_start, planned_finish, due_date, est_minutes, actual_start, actual_finish, paused_at, paused_minutes, downtime_event_id,
                   downtime_minutes, outsourcing_cost, result, root_cause, calibration_result, verified_by, verified_at, completed_at, closed_by, closed_at, created_by, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (no(prefix, created), asset_type, asset_id, wo_type, priority, status, stage, task, description, plan_id, request_id, tech, requested_by,
                 planned_start or start or created, planned_finish or (finish if finish else (start or created) + timedelta(minutes=est or 120)),
                 due or (planned_start or start or created).date(), est, start, finish, paused_at, paused_min, downtime_id,
                 round((finish - start).total_seconds() / 60 - paused_min, 1) if start and finish else None, extra_cost, result, root_cause, calibration,
                 "Maintenance Supervisor" if completed else None, completed, completed, "Maintenance Supervisor" if close else None,
                 completed + timedelta(hours=2) if close and completed else None, "Maintenance Planner", created))
    wid = cur.lastrowid
    items = ms.default_checklist(asset_type, wo_type)
    done_n = len(items) if status in ("VERIFICATION", "COMPLETED", "CLOSED") else (checklist_done or 0)
    for i, item in enumerate(items, 1):
        done = i <= done_n
        cur.execute("INSERT INTO maintenance_wo_checklist (wo_id, seq, item, result, done_by, done_at) VALUES (%s,%s,%s,%s,%s,%s)",
                    (wid, i, item, "OK" if done else None, tech if done else None, (start + timedelta(minutes=15 * i)) if done and start else None))
    for code, name, qty, unit, cost in parts:
        cur.execute("INSERT INTO maintenance_wo_parts (wo_id, part_code, part_name, qty, unit, unit_cost, created_by, created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (wid, code, name, qty, unit, cost, tech, start or created))
    if asset_type == "MOLD":
        for c in ms.MOLD_COMPONENTS:
            cur.execute("INSERT INTO maintenance_wo_components (wo_id, component, condition_code, action_code) VALUES (%s,%s,%s,%s)",
                        (wid, c, "GOOD" if status in ("COMPLETED", "CLOSED", "VERIFICATION") else "NOT_CHECKED", "CLEAN" if status in ("COMPLETED", "CLOSED") and c in ("VENT", "COOLING_CHANNEL") else "NONE"))
    if start and finish and tech:
        hours = round(((finish - start).total_seconds() / 60 - paused_min) / 60, 2)
        cur.execute("INSERT INTO maintenance_wo_labor (wo_id, technician, labor_type, hours, rate, created_by, created_at) VALUES (%s,%s,%s,%s,%s,'SYSTEM',%s)",
                    (wid, tech, "REPAIR" if wo_type in ms.REPAIR_TYPES else wo_type if wo_type in ms.LABOR_TYPES else "PM", hours, RATE, finish))
    # timeline
    ev(cur, "WO", wid, "CREATE", "Maintenance Planner", None, "RELEASED", created)
    if tech:
        ev(cur, "WO", wid, "ASSIGN", "Maintenance Supervisor", "RELEASED", "ASSIGNED", created + timedelta(minutes=5), tech)
    if start:
        ev(cur, "WO", wid, "START", tech, "ASSIGNED", "IN_PROGRESS", start)
    if paused_at:
        ev(cur, "WO", wid, "PAUSE", tech, "IN_PROGRESS", "PAUSED", paused_at, "Waiting for spare part")
    if finish:
        ev(cur, "WO", wid, "SUBMIT", tech, "IN_PROGRESS", "VERIFICATION", finish)
    if completed:
        ev(cur, "WO", wid, "VERIFY", "Maintenance Supervisor", "VERIFICATION", "COMPLETED", completed)
    if close and completed:
        ev(cur, "WO", wid, "CLOSE", "Maintenance Supervisor", "COMPLETED", "CLOSED", completed + timedelta(hours=2))
    return wid


def request(cur, asset_type, asset_id, rtype, priority, status, short, created, by="Production", source="MANUAL", desc=None, wo_id=None, dept="Production",
            reject=None, alarm_id=None, required=None):
    cur.execute("""INSERT INTO maintenance_requests (request_no, asset_type, asset_id, request_type, priority, status, source, short_description, description, requested_by,
                   department, required_date, alarm_id, work_order_id, reviewed_by, approved_by, approved_at, reject_reason, created_by, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (no("MR", created), asset_type, asset_id, rtype, priority, status, source, short, desc, by, dept, required or created + timedelta(hours=8), alarm_id, wo_id,
                 "Maintenance Supervisor" if status != "NEW" else None, "Maintenance Supervisor" if status in ("APPROVED", "COMPLETED") else None,
                 created + timedelta(minutes=40) if status in ("APPROVED", "COMPLETED") else None, reject, by, created))
    rid = cur.lastrowid
    ev(cur, "REQ", rid, "CREATE", by, None, "NEW", created, short)
    if status != "NEW":
        ev(cur, "REQ", rid, "REVIEW", "Maintenance Supervisor", "NEW", "IN_REVIEW", created + timedelta(minutes=20))
    if status in ("APPROVED", "COMPLETED"):
        ev(cur, "REQ", rid, "APPROVE", "Maintenance Supervisor", "IN_REVIEW", "APPROVED", created + timedelta(minutes=40))
    if status in ("REJECTED", "CANCELLED"):
        ev(cur, "REQ", rid, "REJECT" if status == "REJECTED" else "CANCEL", "Maintenance Supervisor", "IN_REVIEW", status, created + timedelta(minutes=45), reject)
    if wo_id:
        cur.execute("UPDATE maintenance_work_orders SET request_id=%s WHERE id=%s", (rid, wo_id))
        cur.execute("SELECT wo_no FROM maintenance_work_orders WHERE id=%s", (wo_id,))
        ev(cur, "REQ", rid, "CREATE_WO", "Maintenance Supervisor", "APPROVED", "APPROVED", created + timedelta(minutes=50), cur.fetchone()["wo_no"])
    if status == "COMPLETED":
        ev(cur, "REQ", rid, "COMPLETE", "Maintenance Supervisor", "APPROVED", "COMPLETED", created + timedelta(hours=6))
    return rid


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        for t in ("maintenance_wo_checklist", "maintenance_wo_parts", "maintenance_wo_labor", "maintenance_wo_components", "maintenance_events",
                  "maintenance_attachments", "maintenance_work_orders", "maintenance_requests", "maintenance_plans"):
            cur.execute(f"DELETE FROM {t}")
        # an open maintenance downtime from an earlier run of this script is closed first
        cur.execute("SELECT id FROM downtime_events WHERE status='OPEN' AND reason_text LIKE 'MWO-%%'")
        for r in cur.fetchall():
            cur.execute("DELETE FROM downtime_events WHERE id=%s", (r["id"],))
        eq = ids(cur, "equipment", "equipment_code", ["IMM-001", "IMM-002", "IMM-003", "IMM-004", "ROB-001", "CHL-001"])
        mo = ids(cur, "molds", "mold_code", ["MLD-001", "MLD-002", "MLD-003", "MLD-004", "MLD-005", "MLD-006"])
        tl = ids(cur, "production_tools", "tool_code", ["CT-004", "HD-003", "MS-001", "MS-002", "MS-003", "AS-002", "HD-002"])
        cur.execute("SELECT id, reason_code FROM reason_codes WHERE reason_code IN ('DT-PM','DT-BRK')")
        rid = {r["reason_code"]: r["id"] for r in cur.fetchall()}

        # ---- manual plans
        p_imm1_daily = plan(cur, "MACHINE", eq["IMM-001"], "Hydraulic oil & lubrication check", "DAILY", 1, TODAY, TECHS[1], 30, priority="HIGH")
        p_imm2_week = plan(cur, "MACHINE", eq["IMM-002"], "Heater band & thermocouple check", "WEEKLY", 7, TODAY - timedelta(days=1), TECHS[3], 60)
        p_imm3_month = plan(cur, "MACHINE", eq["IMM-003"], "Full PM - injection unit", "MONTHLY", 30, TODAY - timedelta(days=3), TECHS[0], 180, priority="HIGH")
        p_imm4_month = plan(cur, "MACHINE", eq["IMM-004"], "Hydraulic system PM", "MONTHLY", 30, TODAY, TECHS[2], 150, priority="HIGH",
                            parts=[{"part_code": "HO-46", "part_name": "Hydraulic oil ISO VG46", "qty": 20, "unit": "L", "unit_cost": 95000}])
        p_rob_week = plan(cur, "EQUIPMENT", eq["ROB-001"], "Robot joints greasing", "WEEKLY", 7, TODAY + timedelta(days=1), TECHS[1], 45)
        p_chl_month = plan(cur, "EQUIPMENT", eq["CHL-001"], "Chiller filter & condenser cleaning", "MONTHLY", 30, TODAY + timedelta(days=5), TECHS[3], 90)
        plan(cur, "MACHINE", eq["IMM-001"], "Screw & barrel wear inspection", "QUARTERLY", 90, TODAY + timedelta(days=20), TECHS[0], 240, wo_type="INSPECTION")
        plan(cur, "MACHINE", eq["IMM-002"], "Annual overhaul", "SHUTDOWN", 365, TODAY + timedelta(days=60), TECHS[0], 960, priority="HIGH")
        p_hd3 = plan(cur, "TOOL", tl["HD-003"], "Pallet truck PM", "MONTHLY", 30, TODAY, TECHS[4], 60)
        plan(cur, "TOOL", tl["HD-002"], "Chain hoist safety inspection", "QUARTERLY", 90, TODAY + timedelta(days=12), TECHS[4], 60, wo_type="INSPECTION")
        ms.sync_auto_plans(cur)
        cur.execute("SELECT id, asset_type, asset_id FROM maintenance_plans WHERE source<>'MANUAL'")
        auto = {(r["asset_type"], r["asset_id"]): r["id"] for r in cur.fetchall()}

        # ---- history: machine WOs linked to the recorded PM / breakdown downtime events
        cur.execute("""SELECT d.id, d.equipment_id, d.started_at, d.ended_at, r.reason_code FROM downtime_events d JOIN reason_codes r ON r.id=d.reason_code_id
                       WHERE d.status='CLOSED' AND r.reason_code IN ('DT-PM','DT-BRK') ORDER BY d.started_at""")
        linked = cur.fetchall()
        for i, d in enumerate(linked):
            brk = d["reason_code"] == "DT-BRK"
            t = TECHS[i % len(TECHS)]
            wid = wo(cur, "MACHINE", d["equipment_id"], "BREAKDOWN" if brk else "PM", "Nozzle heater failure" if brk else "Planned PM - lubrication & filters",
                     "CLOSED" if d["ended_at"] < NOW - timedelta(days=1) else "COMPLETED", t, d["started_at"] - timedelta(hours=2), priority="HIGH" if brk else "MEDIUM",
                     start=d["started_at"], finish=d["ended_at"], downtime_id=d["id"], est=150, due=d["started_at"].date(),
                     parts=[("HB-60", "Nozzle heater band 60x40", 1, "pcs", 1250000)] if brk else [("FLT-OIL", "Oil filter element", 1, "pcs", 650000)],
                     result="Replaced heater band, checked thermocouple" if brk else "Lubricated, filters cleaned", root_cause="Heater band burnt out" if brk else None,
                     close=d["ended_at"] < NOW - timedelta(days=1))
            if brk:
                request(cur, "MACHINE", d["equipment_id"], "CORRECTIVE", "HIGH", "COMPLETED", "Nozzle temperature cannot reach set point", d["started_at"] - timedelta(hours=2, minutes=30),
                        by="Le Van C", wo_id=wid)
        # history: older machine / equipment WOs (6 weeks) without recorded downtime
        hist = [
            (-40, "MACHINE", "IMM-001", "PM", "Monthly PM - clamping unit", 8, 150, 0, [("GR-EP2", "Grease EP2", 2, "kg", 180000)], None),
            (-38, "EQUIPMENT", "CHL-001", "PM", "Chiller filter & condenser cleaning", 9, 90, 0, [("FLT-W", "Water filter cartridge", 2, "pcs", 420000)], None),
            (-35, "MACHINE", "IMM-002", "CORRECTIVE", "Hydraulic hose leakage", 13, 95, 0, [("HS-12", "Hydraulic hose 1/2 inch", 1, "pcs", 850000)], "Hose worn at bend"),
            (-33, "EQUIPMENT", "ROB-001", "PM", "Robot joints greasing", 10, 45, 0, [], None),
            (-31, "MACHINE", "IMM-003", "PM", "Full PM - injection unit", 7, 180, 1, [("OR-KIT", "O-ring kit", 1, "set", 350000)], None),
            (-28, "MACHINE", "IMM-004", "BREAKDOWN", "Safety door switch failure", 15, 70, 0, [("SW-SF", "Safety limit switch", 1, "pcs", 980000)], "Switch contact worn"),
            (-26, "EQUIPMENT", "ROB-001", "PM", "Robot joints greasing", 10, 45, 0, [], None),
            (-24, "MACHINE", "IMM-001", "INSPECTION", "Tie bar & platen parallelism inspection", 9, 120, 0, [], None),
            (-21, "MACHINE", "IMM-002", "PM", "Heater band & thermocouple check", 8, 60, 2, [], None),
            (-19, "EQUIPMENT", "ROB-001", "PM", "Robot joints greasing", 10, 45, 0, [], None),
            (-17, "MACHINE", "IMM-003", "CORRECTIVE", "Screw speed unstable", 14, 110, 0, [("ENC-01", "Encoder coupling", 1, "pcs", 1450000)], "Encoder coupling loose"),
            (-14, "MACHINE", "IMM-002", "PM", "Heater band & thermocouple check", 8, 60, 0, [("TC-J", "Thermocouple type J", 2, "pcs", 380000)], None),
            (-12, "EQUIPMENT", "ROB-001", "PM", "Robot joints greasing", 10, 45, 0, [], None),
            (-10, "EQUIPMENT", "CHL-001", "CORRECTIVE", "Chiller low flow alarm", 11, 80, 0, [("PMP-S", "Pump mechanical seal", 1, "pcs", 1650000)], "Pump seal leaking"),
            (-7, "MACHINE", "IMM-002", "PM", "Heater band & thermocouple check", 8, 60, 1, [], None),
            (-5, "EQUIPMENT", "ROB-001", "PM", "Robot joints greasing", 10, 45, 0, [], None),
            (-4, "MACHINE", "IMM-001", "CORRECTIVE", "Mold clamp pressure drop", 16, 75, 0, [("VLV-P", "Pressure relief valve seal", 1, "set", 720000)], "Valve seal worn"),
        ]
        plan_of = {("MACHINE", "IMM-002", "Heater band & thermocouple check"): p_imm2_week, ("EQUIPMENT", "ROB-001", "Robot joints greasing"): p_rob_week,
                   ("MACHINE", "IMM-003", "Full PM - injection unit"): p_imm3_month, ("EQUIPMENT", "CHL-001", "Chiller filter & condenser cleaning"): p_chl_month}
        for i, (day, at, code, wt, task, hh, dur, late, parts, cause) in enumerate(hist):
            start = T(day + late, hh)
            finish = start + timedelta(minutes=dur)
            t = TECHS[(i + 2) % len(TECHS)]
            wid = wo(cur, at, eq[code], wt, task, "CLOSED", t, T(day, 7), priority="HIGH" if wt in ms.REPAIR_TYPES else "MEDIUM", plan_id=plan_of.get((at, code, task)),
                     start=start, finish=finish, due=T(day, 8).date(), est=dur, parts=parts, result="Done, tested OK", root_cause=cause, close=True)
            if wt in ms.REPAIR_TYPES:
                request(cur, at, eq[code], "CORRECTIVE", "HIGH", "COMPLETED", task, T(day, 6, 30), by="Production Supervisor", wo_id=wid)
        # history: mold WOs matching the Mold Management maintenance / repair events
        mold_hist = [
            ("MLD-002", "PM", "Mold PM - check ejection, lubrication", "2026-08-20 07:30", "2026-08-20 10:00", TECHS[2], []),
            ("MLD-006", "PM", "Mold PM - clean and rust protection", "2026-08-12 08:10", "2026-08-12 10:00", TECHS[3], [("RP-SPR", "Rust preventive spray", 2, "can", 145000)]),
            ("MLD-001", "PM", "Mold PM at 90k shots", "2026-09-05 11:40", "2026-09-05 14:00", TECHS[0], [("OR-M", "Cooling O-ring set", 1, "set", 280000)]),
            ("MLD-005", "PM", "Mold PM at 180k shots", "2026-09-09 08:50", "2026-09-10 09:00", TECHS[2], []),
            ("MLD-004", "CORRECTIVE", "Ejector pins broken", "2026-09-10 14:20", "2026-09-11 10:00", TECHS[1], [("EJP-6", "Ejector pin D6 x 150", 8, "pcs", 185000)]),
        ]
        for code, wt, task, a, b, t, parts in mold_hist:
            start, finish = D(a), D(b)
            wid = wo(cur, "MOLD", mo[code], wt, task, "CLOSED", t, start - timedelta(hours=1), priority="HIGH" if wt == "CORRECTIVE" else "MEDIUM",
                     plan_id=auto.get(("MOLD", mo[code])) if wt == "PM" else None, start=start, finish=finish, est=150, due=start.date(), parts=parts,
                     result="Maintenance completed, first-off OK", requested_by="Tooling", close=True)
            if wt == "CORRECTIVE":
                request(cur, "MOLD", mo[code], "CORRECTIVE", "HIGH", "COMPLETED", task, start - timedelta(hours=1, minutes=20), by="Tooling", dept="Tooling", wo_id=wid)
        # history: tool calibration
        for code, a, b, t in [("MS-001", "2026-04-07 09:00", "2026-04-07 10:00", TECHS[4]), ("MS-002", "2026-04-15 09:00", "2026-04-15 10:10", TECHS[4])]:
            wo(cur, "TOOL", tl[code], "CALIBRATION", "Calibration", "CLOSED", t, D(a) - timedelta(days=1), plan_id=auto.get(("TOOL", tl[code])), start=D(a), finish=D(b),
               est=60, calibration="PASS", result="Within tolerance", requested_by="QA", close=True)
        wo(cur, "TOOL", tl["AS-002"], "CALIBRATION", "Torque wrench calibration check", "CLOSED", TECHS[4], T(-9, 7), start=T(-9, 9), finish=T(-9, 9, 50),
           est=45, calibration="PASS", result="Torque within ±4 %", requested_by="QA", close=True)

        # ---- open work orders (current state)
        # IMM-004 hydraulic PM in progress with an open DT-PM downtime (machine already in MAINTENANCE)
        dt4 = machine.open_downtime(cur, eq["IMM-004"], TECHS[2], rid["DT-PM"], "MWO · Hydraulic system PM", source="MES", started_at=T(0, 8, 5))
        cur.execute("UPDATE downtime_events SET started_at=%s WHERE id=%s", (T(0, 8, 5), dt4))
        w_imm4 = wo(cur, "MACHINE", eq["IMM-004"], "PM", "Hydraulic system PM", "IN_PROGRESS", TECHS[2], T(-1, 16), priority="HIGH", plan_id=p_imm4_month,
                    start=T(0, 8, 5), planned_start=T(0, 8), planned_finish=T(0, 10, 30), est=150, downtime_id=dt4, checklist_done=2,
                    parts=[("HO-46", "Hydraulic oil ISO VG46", 20, "L", 95000)], requested_by="Maintenance Planning")
        cur.execute("SELECT wo_no FROM maintenance_work_orders WHERE id=%s", (w_imm4,))
        cur.execute("UPDATE downtime_events SET reason_text=%s WHERE id=%s", (f"{cur.fetchone()['wo_no']} · Hydraulic system PM", dt4))
        # MLD-003 PM in progress (mold in maintenance since 23/09 11:00)
        cur.execute("SELECT status_since FROM molds WHERE id=%s", (mo["MLD-003"],))
        since = cur.fetchone()["status_since"] or T(-1, 11)
        w_m3 = wo(cur, "MOLD", mo["MLD-003"], "PM", "Mold PM at 612k shots", "IN_PROGRESS", TECHS[3], since - timedelta(minutes=30), priority="HIGH",
                  plan_id=auto.get(("MOLD", mo["MLD-003"])), start=since, planned_start=since, planned_finish=since + timedelta(hours=26), est=240, stage="INSPECTION",
                  checklist_done=3, requested_by="Tooling", description="Shot counter reached the PM interval.")
        cur.execute("UPDATE maintenance_wo_components SET condition_code=%s, action_code=%s WHERE wo_id=%s AND component=%s", ("GOOD", "CLEAN", w_m3, "VENT"))
        cur.execute("UPDATE maintenance_wo_components SET condition_code=%s, action_code=%s WHERE wo_id=%s AND component=%s", ("MONITOR", "CLEAN", w_m3, "COOLING_CHANNEL"))
        cur.execute("UPDATE maintenance_wo_components SET condition_code=%s WHERE wo_id=%s AND component IN ('CORE','CAVITY','INSERT')", ("GOOD", w_m3))
        cur.execute("UPDATE maintenance_wo_components SET condition_code=%s, action_code=%s, note=%s WHERE wo_id=%s AND component=%s",
                    ("ATTENTION", "REPLACE", "2 pins scratched", w_m3, "EJECTOR"))
        # MLD-004 repair paused, waiting for the core insert
        cur.execute("SELECT status_since FROM molds WHERE id=%s", (mo["MLD-004"],))
        since4 = cur.fetchone()["status_since"] or T(-5, 15, 30)
        w_m4 = wo(cur, "MOLD", mo["MLD-004"], "CORRECTIVE", "Core insert cracked - replace insert", "PAUSED", TECHS[1], since4 - timedelta(minutes=20), priority="HIGH",
                  start=since4 + timedelta(minutes=30), planned_finish=T(1, 17), est=960, stage="CLEANING_REPAIR", checklist_done=1, paused_at=T(-4, 10),
                  parts=[("CI-004-2", "Core insert #2 (Legacy Cover)", 1, "pcs", 18500000)], requested_by="Tooling", description="Crack found on core insert #2 after breakdown removal.")
        cur.execute("UPDATE maintenance_wo_components SET condition_code='CRITICAL', action_code='REPLACE', note='Crack 12 mm' WHERE wo_id=%s AND component='INSERT'", (w_m4,))
        cur.execute("UPDATE maintenance_wo_components SET condition_code='GOOD' WHERE wo_id=%s AND component IN ('CAVITY','CORE','EJECTOR','VENT')", (w_m4,))
        request(cur, "MOLD", mo["MLD-004"], "CORRECTIVE", "HIGH", "APPROVED", "Core insert cracked", since4 - timedelta(minutes=40), by="Tooling", dept="Tooling", wo_id=w_m4)
        # tools
        w_hd3 = wo(cur, "TOOL", tl["HD-003"], "PM", "Pallet truck PM", "IN_PROGRESS", TECHS[4], T(-1, 15), plan_id=p_hd3, start=T(0, 9), planned_start=T(0, 9),
                   planned_finish=T(0, 10), est=60, stage="INSPECTION", checklist_done=2, requested_by="Maintenance Planning")
        w_ct4 = wo(cur, "TOOL", tl["CT-004"], "CORRECTIVE", "Saw blade chipped - resharpen", "ASSIGNED", TECHS[4], T(-1, 13), planned_start=T(0, 13), planned_finish=T(0, 15),
                   est=90, requested_by="Production")
        request(cur, "TOOL", tl["CT-004"], "CORRECTIVE", "MEDIUM", "APPROVED", "Saw blade chipped", T(-1, 12, 40), by="Pham Van D", wo_id=w_ct4)
        cur.execute("SELECT calibration_due_date FROM production_tools WHERE id=%s", (tl["MS-003"],))
        due3 = cur.fetchone()["calibration_due_date"] or T(-12).date()
        wo(cur, "TOOL", tl["MS-003"], "CALIBRATION", "Dial indicator calibration", "ASSIGNED", TECHS[4], datetime.combine(due3, datetime.min.time()) - timedelta(days=3),
           plan_id=auto.get(("TOOL", tl["MS-003"])), planned_start=datetime.combine(due3, datetime.min.time()).replace(hour=9), due=due3, est=60, requested_by="QA", priority="HIGH")
        # machines / equipment
        w_imm3 = wo(cur, "MACHINE", eq["IMM-003"], "CORRECTIVE", "Oil leakage from hydraulic pump", "RELEASED", None, T(0, 9, 30), priority="HIGH",
                    planned_start=T(0, 13), planned_finish=T(0, 16), est=120, requested_by="Production")
        request(cur, "MACHINE", eq["IMM-003"], "CORRECTIVE", "HIGH", "APPROVED", "Oil leakage from hydraulic pump", T(0, 9, 10), by="Tran Van B (Operator)",
                desc="Hydraulic pump area found oil leakage. Need to check seal and replace if damaged.", wo_id=w_imm3)
        wo(cur, "MACHINE", eq["IMM-002"], "PM", "Heater band & thermocouple check", "RELEASED", TECHS[3], T(-2, 15), plan_id=p_imm2_week, planned_start=T(-1, 8),
           planned_finish=T(-1, 9), due=T(-1).date(), est=60, requested_by="Maintenance Planning")
        wo(cur, "EQUIPMENT", eq["CHL-001"], "INSPECTION", "Outlet temperature higher than normal", "VERIFICATION", TECHS[3], T(0, 6, 30), start=T(0, 7), finish=T(0, 8, 10),
           planned_start=T(0, 7), est=60, requested_by="Utility", result="Cleaned condenser fins, outlet temperature back to 12 °C")
        wo(cur, "EQUIPMENT", eq["ROB-001"], "PM", "Robot joints greasing", "ASSIGNED", TECHS[1], T(0, 7), plan_id=p_rob_week, planned_start=T(1, 10), planned_finish=T(1, 10, 45),
           due=T(1).date(), est=45, requested_by="Maintenance Planning")
        wo(cur, "MACHINE", eq["IMM-001"], "PM", "Hydraulic oil & lubrication check", "ASSIGNED", TECHS[1], T(-1, 16), plan_id=p_imm1_daily, planned_start=T(0, 14),
           planned_finish=T(0, 14, 30), est=30, requested_by="Maintenance Planning", priority="HIGH")
        w_m6 = wo(cur, "MOLD", mo["MLD-006"], "INSPECTION", "Life 88 % - condition inspection", "RELEASED", None, T(0, 8, 20), planned_start=T(1, 8), planned_finish=T(1, 11),
                  est=180, requested_by="Tooling")
        request(cur, "MOLD", mo["MLD-006"], "INSPECTION", "MEDIUM", "APPROVED", "Mold life above 85 % - inspect before next run", T(0, 8), by="Tooling", dept="Tooling",
                source="SHOT_COUNTER", wo_id=w_m6)

        # ---- other requests
        cur.execute("SELECT id, equipment_id, message FROM machine_alarms WHERE status IN ('ACTIVE','ACKNOWLEDGED') ORDER BY raised_at DESC LIMIT 1")
        al = cur.fetchone()
        if al:
            request(cur, "MACHINE", al["equipment_id"], "CORRECTIVE", "HIGH", "NEW", al["message"][:255], T(0, 9, 45), by="SYSTEM", source="ALARM", alarm_id=al["id"])
        request(cur, "MACHINE", eq["IMM-001"], "CORRECTIVE", "MEDIUM", "NEW", "Abnormal noise at clamping unit", T(0, 10, 5), by="Nguyen Van H (Operator)",
                desc="Knocking sound when the mold closes at high speed.")
        request(cur, "EQUIPMENT", eq["ROB-001"], "PREVENTIVE", "LOW", "NEW", "Gripper pads worn - replace at next PM", T(0, 8, 40), by="Le Thi C")
        request(cur, "EQUIPMENT", eq["CHL-001"], "CORRECTIVE", "MEDIUM", "IN_REVIEW", "Chiller alarm high temperature", T(-1, 21, 15), by="Utility", dept="Utility")
        request(cur, "MOLD", mo["MLD-002"], "CORRECTIVE", "HIGH", "IN_REVIEW", "Flash on part - parting line", T(0, 7, 50), by="QC Inspector", source="QUALITY", dept="Quality")
        request(cur, "TOOL", tl["MS-002"], "CALIBRATION", "MEDIUM", "NEW", "Micrometer reading drift - check calibration", T(-1, 14), by="QC Inspector", dept="Quality")
        request(cur, "MACHINE", eq["IMM-002"], "CORRECTIVE", "LOW", "REJECTED", "Screen brightness low", T(-3, 9), by="Operator", reject="Not a maintenance issue - adjusted HMI settings")
        request(cur, "MACHINE", eq["IMM-003"], "CORRECTIVE", "MEDIUM", "CANCELLED", "Duplicate: oil leakage", T(0, 9, 20), by="Operator", reject="Duplicate request")
        request(cur, "MOLD", mo["MLD-001"], "PREVENTIVE", "MEDIUM", "COMPLETED", "PM due at 90k shots", D("2026-09-05 10:30"), by="SYSTEM", source="PM_PLAN", dept="Tooling")
        conn.commit()
        cur.execute("SELECT status, COUNT(*) n FROM maintenance_work_orders GROUP BY status")
        print("work orders:", {r["status"]: r["n"] for r in cur.fetchall()})
        cur.execute("SELECT status, COUNT(*) n FROM maintenance_requests GROUP BY status")
        print("requests:", {r["status"]: r["n"] for r in cur.fetchall()})
        cur.execute("SELECT COUNT(*) n FROM maintenance_plans")
        print("plans:", cur.fetchone()["n"])
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
