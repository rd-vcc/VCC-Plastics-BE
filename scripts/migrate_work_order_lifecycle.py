"""One-off migration: Work Order lifecycle for Work Order Management (screen 1.6).

Status flow (spec 1.6.2), mapped onto production_plan_details.status:
    UNSCHEDULED  = Created   (not on the plan yet)
    SCHEDULED    = Queued    (machine + start time on a plan)
    RELEASED     = Released  (handed to the shop floor)
    IN_PRODUCTION            (first output reported / started at the machine)
    ON_HOLD                  (stopped with a reason; hold_from_status remembers where to resume)
    COMPLETED                (planned quantity reached or completed short)
    CLOSED                   (material closed out, record locked)
    CANCELLED
Delayed / Waiting Material / Mold / Maintenance are derived exception states, not stored.

Run with:

    python -m scripts.migrate_work_order_lifecycle
"""

from app.core.database import get_vcc_plastics_connection

COLUMNS = {
    "hold_reason": "VARCHAR(500) NULL",
    "hold_from_status": "VARCHAR(30) NULL",
    "held_at": "DATETIME NULL",
    "cancel_reason": "VARCHAR(500) NULL",
    "released_at": "DATETIME NULL",
    "started_at": "DATETIME NULL",
    "completed_at": "DATETIME NULL",
    "closed_at": "DATETIME NULL",
}


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """ALTER TABLE production_plan_details MODIFY status
               ENUM('UNSCHEDULED','SCHEDULED','RELEASED','IN_PRODUCTION','ON_HOLD','COMPLETED','CLOSED','CANCELLED')
               NOT NULL DEFAULT 'UNSCHEDULED'""")
        cur.execute("SHOW COLUMNS FROM production_plan_details")
        existing = {row[0] for row in cur.fetchall()}
        for name, ddl in COLUMNS.items():
            if name not in existing:
                cur.execute(f"ALTER TABLE production_plan_details ADD COLUMN {name} {ddl}")
        # released Work Orders that already reported output are in production
        cur.execute("UPDATE production_plan_details SET status='IN_PRODUCTION', started_at=COALESCE(started_at, planned_start) WHERE status='RELEASED' AND actual_qty > 0")
        cur.execute("UPDATE production_plan_details SET completed_at=COALESCE(completed_at, updated_at) WHERE status='COMPLETED'")
        conn.commit()
        print("Work order lifecycle migrated.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
