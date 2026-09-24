"""Demo states for Work Order Management (screen 1.6), applied through the real actions.

    python -m scripts.seed_work_order_demo
"""

from app.core.database import get_vcc_plastics_connection
from app.modules.production.planning.service import write_audit
from app.modules.production.work_orders.service import close, hold, release

ACTOR = "00842"


def version(cur, wo_no):
    cur.execute("SELECT id, version, status FROM production_plan_details WHERE wo_no=%s", (wo_no,))
    return cur.fetchone()


def start(cur, wo_no):
    w = version(cur, wo_no)
    cur.execute("UPDATE production_plan_details SET status='IN_PRODUCTION', started_at=COALESCE(started_at, planned_start), version=version+1 WHERE id=%s", (w["id"],))
    write_audit(cur, "WORK_ORDER", w["id"], "START", "RELEASED", "IN_PRODUCTION", ACTOR, "Output reported")


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        for wo_no in ("WO-260923-001", "WO-260923-002", "WO-260923-003"):
            w = version(cur, wo_no)
            if w and w["status"] == "SCHEDULED":
                release(cur, w["id"], ACTOR, w["version"], "Demo release")
        for wo_no in ("WO-260923-001", "WO-260923-002", "WO-260923-003"):
            if version(cur, wo_no)["status"] == "RELEASED":
                start(cur, wo_no)
        w = version(cur, "WO-260923-003")
        if w["status"] == "IN_PRODUCTION":
            hold(cur, w["id"], "Mold cleaning / vệ sinh khuôn", ACTOR, w["version"])
        w = version(cur, "WO-260923-005")
        if w["status"] == "COMPLETED":
            close(cur, w["id"], ACTOR, w["version"], "Demo close")
        conn.commit()
        cur.execute("SELECT wo_no, status FROM production_plan_details ORDER BY wo_no")
        print(cur.fetchall())
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
