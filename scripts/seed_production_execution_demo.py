"""Demo Production Log for Production Execution (screen 1.7).

Rebuilds a realistic shift for the demo Work Orders: running ones started this morning and
report output about every 30 minutes, one is on hold, and the two completed ones ran yesterday.
Only touches the demo Work Orders and only when they have no log yet.

    python -m scripts.seed_production_execution_demo
"""

import math
import random
from datetime import datetime, timedelta

from app.core.database import get_vcc_plastics_connection

random.seed(7)
ACTOR = "00842"
TODAY = datetime.combine(datetime.now().date(), datetime.min.time())

# wo_no, start, operator, leader, hold_at (minutes after start) + reason, end (None = still running)
PLAN = [
    ("WO-260923-001", TODAY + timedelta(hours=6, minutes=5), "00911", "00842", None, None),
    ("WO-260923-002", TODAY + timedelta(hours=6, minutes=35), "00912", "00842", None, None),
    ("WO-260923-003", TODAY + timedelta(hours=6, minutes=50), "00913", "00842", (95, "Mold cleaning / vệ sinh khuôn"), None),
    ("WO-260923-004", TODAY - timedelta(hours=17), "00911", "00842", (40, "Material change / đổi vật tư"), TODAY - timedelta(hours=13, minutes=20)),
    ("WO-260923-005", TODAY - timedelta(hours=16), "00912", "00842", None, TODAY - timedelta(hours=14, minutes=5)),
]


def insert(cur, wo, event, at, good=0, reject=0, cycle=None, reason=None, operator=None):
    cur.execute(
        """INSERT INTO production_execution_logs (work_order_id, event_type, good_qty, reject_qty, cycle_time_sec, reason_text,
               machine_id, mold_id, operator_code, source, actor, event_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'MANUAL',%s,%s)""",
        (wo["id"], event, good, reject, cycle, reason, wo["machine_id"], wo["mold_id"], operator, ACTOR, at))


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    now = datetime.now()
    try:
        for wo_no, start, operator, leader, hold, end in PLAN:
            cur.execute("SELECT * FROM production_plan_details WHERE wo_no=%s", (wo_no,))
            wo = cur.fetchone()
            if not wo:
                continue
            cur.execute("SELECT COUNT(*) AS n FROM production_execution_logs WHERE work_order_id=%s", (wo["id"],))
            if cur.fetchone()["n"]:
                print("already has log", wo_no)
                continue
            good_total, reject_total = int(wo["actual_qty"]), int(wo["reject_qty"])
            stop_at = end or now
            hold_start = start + timedelta(minutes=hold[0]) if hold else None
            hold_end = (hold_start + timedelta(minutes=35)) if hold and end else None
            run_until = hold_start if hold and not end else stop_at
            insert(cur, wo, "START", start, reason=f"Operator {operator}, leader {leader}", operator=operator)
            # output every ~30 min over the run window (skip the hold window)
            slots, t = [], start + timedelta(minutes=30)
            while t < run_until:
                if not (hold_start and hold_end and hold_start <= t < hold_end):
                    slots.append(t)
                t += timedelta(minutes=30)
            slots.append(run_until - timedelta(minutes=1))
            weights = [random.uniform(0.85, 1.15) for _ in slots]
            total_w = sum(weights)
            g_left, r_left = good_total, reject_total
            cavity = max(int(wo["cavity"] or 1), 1)
            prev = start
            for i, (at, w) in enumerate(zip(slots, weights)):
                last = i == len(slots) - 1
                g = g_left if last else int(good_total * w / total_w)
                r = r_left if last else int(round(reject_total * w / total_w))
                g_left -= g; r_left -= r
                shots = max(math.ceil((g + r) / cavity), 1)
                gap = (at - prev).total_seconds() - (35 * 60 if hold_start and hold_end and prev <= hold_start < at else 0)
                insert(cur, wo, "OUTPUT", at, g, r, round(max(gap, 60) / shots, 2), "Reject: short shot / thiếu nhựa" if r else None, operator)
                prev = at
            if hold:
                insert(cur, wo, "HOLD", hold_start, reason=hold[1], operator=operator)
                if hold_end:
                    insert(cur, wo, "RESUME", hold_end, reason=hold[1], operator=operator)
            if end:
                insert(cur, wo, "END", end, reason=f"good {good_total}, reject {reject_total}", operator=operator)
            cur.execute("""UPDATE production_plan_details SET started_at=%s, operator_code=%s, leader_code=%s, last_output_at=%s,
                           held_at=IF(status='ON_HOLD', %s, held_at), completed_at=COALESCE(%s, completed_at) WHERE id=%s""",
                        (start, operator, leader, run_until, hold_start, end, wo["id"]))
            print("seeded", wo_no, len(slots), "outputs")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
