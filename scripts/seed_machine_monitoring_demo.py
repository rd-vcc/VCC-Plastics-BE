"""Demo history for the Machine & Equipment module (downtime, alarms, status events, parameter trend).

Everything is labelled source SIMULATOR (or MES for the on-hold Work Order) so it can be told apart
from real PLC data later. Runs once (skips when downtime events already exist).

    python -m scripts.seed_machine_monitoring_demo
"""

import random
from datetime import datetime, timedelta

from app.core.database import get_vcc_plastics_connection
from app.modules.machine.service import get_equipment, parameter_limits

rnd = random.Random(24)
NOW = datetime.now().replace(second=0, microsecond=0)
REASONS = ["DT-MAT", "DT-MOLD", "DT-BRK", "DT-SETUP", "DT-QUAL", "DT-PWR", "DT-UTIL", "DT-PM", "DT-OTH"]
WEIGHTS = [3, 4, 2, 4, 2, 1, 1, 1, 1]
ALARMS = [
    ("E-HYD-01", "Hydraulic oil temperature high", "HIGH"), ("E-HEAT-03", "Heater zone 3 deviation", "MEDIUM"),
    ("E-ROB-12", "Robot gripper vacuum low", "HIGH"), ("E-SAFE-01", "Safety door opened during cycle", "CRITICAL"),
    ("E-LUB-02", "Lubrication pressure low", "MEDIUM"), ("E-CW-04", "Cooling water flow low", "HIGH"),
    ("E-HOP-01", "Hopper material level low", "LOW"), ("E-COM-01", "Communication timeout", "MEDIUM"),
]


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT COUNT(*) AS n FROM downtime_events")
        if cur.fetchone()["n"]:
            print("already seeded"); return
        cur.execute("SELECT id, reason_code FROM reason_codes WHERE reason_code LIKE 'DT-%%'")
        reason_ids = {r["reason_code"]: r["id"] for r in cur.fetchall()}
        cur.execute("""SELECT e.id, e.equipment_code, t.category FROM equipment e JOIN equipment_types t ON t.id = e.equipment_type_id
                       WHERE e.asset_status='ACTIVE'""")
        equipment = cur.fetchall()
        dt_seq = al_seq = 0
        for day in range(7, -1, -1):
            date0 = (NOW - timedelta(days=day)).replace(hour=6, minute=0)
            stamp = date0.strftime("%y%m%d")
            for eq in equipment:
                n = rnd.choice([0, 1, 1, 2, 3]) if eq["category"] == "MACHINE" else rnd.choice([0, 0, 1])
                for _ in range(n):
                    start = date0 + timedelta(minutes=rnd.randint(0, 22 * 60))
                    if start >= NOW - timedelta(minutes=30):
                        continue
                    code = rnd.choices(REASONS, WEIGHTS)[0]
                    minutes = {"DT-MOLD": rnd.randint(30, 90), "DT-BRK": rnd.randint(40, 180), "DT-PM": rnd.randint(60, 240)}.get(code, rnd.randint(8, 50))
                    end = min(start + timedelta(minutes=minutes), NOW - timedelta(minutes=5))
                    dt_seq += 1
                    cur.execute(
                        """INSERT INTO downtime_events (downtime_no, equipment_id, started_at, ended_at, reason_code_id, category, status, source,
                               reporter, root_cause, action_taken, closed_by)
                           VALUES (%s,%s,%s,%s,%s,%s,'CLOSED','SIMULATOR',%s,%s,%s,%s)""",
                        (f"DT-{stamp}-{dt_seq:04d}", eq["id"], start, end, reason_ids.get(code),
                         "PLANNED" if code in {"DT-MOLD", "DT-SETUP", "DT-PM"} else "UNPLANNED", rnd.choice(["00911", "00912", "00913"]),
                         "Worn seal" if code == "DT-BRK" else None, "Replaced / adjusted" if code in {"DT-BRK", "DT-QUAL"} else None, "00842"))
                for _ in range(rnd.choice([0, 1, 2, 3]) if eq["category"] == "MACHINE" else rnd.choice([0, 1])):
                    at = date0 + timedelta(minutes=rnd.randint(0, 22 * 60))
                    if at >= NOW:
                        continue
                    code, msg, prio = rnd.choice(ALARMS)
                    al_seq += 1
                    old = at < NOW - timedelta(hours=8)
                    status = "CLOSED" if old else rnd.choice(["ACTIVE", "ACKNOWLEDGED", "CLEARED", "CLOSED"])
                    cleared = at + timedelta(minutes=rnd.randint(3, 45)) if status in {"CLEARED", "CLOSED"} else None
                    cur.execute(
                        """INSERT INTO machine_alarms (alarm_no, equipment_id, alarm_code, message, priority, source, status, raised_at,
                               acknowledged_at, acknowledged_by, cleared_at, closed_at, closed_by)
                           VALUES (%s,%s,%s,%s,%s,'SIMULATOR',%s,%s,%s,%s,%s,%s,%s)""",
                        (f"AL-{stamp}-{al_seq:04d}", eq["id"], code, msg, prio, status, at,
                         at + timedelta(minutes=2) if status != "ACTIVE" else None, "00842" if status != "ACTIVE" else None,
                         cleared, cleared + timedelta(minutes=5) if status == "CLOSED" else None, "00842" if status == "CLOSED" else None))
                    alarm_id = cur.lastrowid
                    cur.execute("INSERT INTO machine_alarm_actions (alarm_id, action, from_status, to_status, actor, acted_at) VALUES (%s,'RAISE',NULL,'ACTIVE','SIMULATOR',%s)",
                                (alarm_id, at))
                    if status != "ACTIVE":
                        cur.execute("INSERT INTO machine_alarm_actions (alarm_id, action, from_status, to_status, actor, acted_at) VALUES (%s,'ACKNOWLEDGE','ACTIVE','ACKNOWLEDGED','00842',%s)",
                                    (alarm_id, at + timedelta(minutes=2)))
                    if status == "CLOSED":
                        cur.execute("INSERT INTO machine_alarm_actions (alarm_id, action, from_status, to_status, actor, acted_at, note) VALUES (%s,'CLOSE','CLEARED','CLOSED','00842',%s,'Checked, normal')",
                                    (alarm_id, cleared + timedelta(minutes=5)))
        # the Work Order on hold in Production Execution is an open downtime of its machine
        cur.execute("""SELECT d.id, d.machine_id, d.held_at, d.hold_reason FROM production_plan_details d WHERE d.status='ON_HOLD' AND d.machine_id IS NOT NULL""")
        for wo in cur.fetchall():
            dt_seq += 1
            cur.execute("""INSERT INTO downtime_events (downtime_no, equipment_id, work_order_id, started_at, reason_text, category, status, source, reporter)
                           VALUES (%s,%s,%s,%s,%s,'PLANNED','OPEN','MES','00842')""",
                        (f"DT-{NOW:%y%m%d}-{dt_seq:04d}", wo["machine_id"], wo["id"], wo["held_at"] or NOW, wo["hold_reason"]))
        # status history: current status since the start of today's shift
        for eq in equipment:
            cur.execute("SELECT operational_status FROM equipment WHERE id=%s", (eq["id"],))
            st = cur.fetchone()["operational_status"]
            cur.execute("INSERT INTO machine_status_events (equipment_id, status, started_at, source, remark) VALUES (%s,%s,%s,'SIMULATOR','Initial state')",
                        (eq["id"], st, NOW.replace(hour=6, minute=0)))
        # parameter trend for the last 24 h (every 10 min) for running equipment
        for eq in equipment:
            cur.execute("SELECT operational_status FROM equipment WHERE id=%s", (eq["id"],))
            if cur.fetchone()["operational_status"] != "RUNNING":
                continue
            full = get_equipment(cur, equipment_id=eq["id"])
            limits = parameter_limits(cur, full)
            rows = []
            for k in range(144, 0, -1):
                at = NOW - timedelta(minutes=10 * k)
                for code, lim in limits.items():
                    t = lim["target"] if lim["target"] is not None else ((lim["min"] or 0) + (lim["max"] or 0)) / 2
                    span = ((lim["max"] - lim["min"]) / 2) if (lim["min"] is not None and lim["max"] is not None) else abs(t) * 0.1 or 1
                    rows.append((eq["id"], code, lim["name"], round(t + rnd.gauss(0, span * 0.15), 2), lim["unit"], at))
            cur.executemany("""INSERT INTO machine_parameter_readings (equipment_id, parameter_code, parameter_name, value, unit, read_at, source)
                               VALUES (%s,%s,%s,%s,%s,%s,'SIMULATOR')""", rows)
        conn.commit()
        print("seeded", dt_seq, "downtime,", al_seq, "alarms")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
