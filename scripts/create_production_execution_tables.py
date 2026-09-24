"""One-off script: Production Execution (screen 1.7).

production_execution_logs: the Production Log. Every event on a running Work Order is one row
(start, output, reject, quantity adjustment, hold, resume, end, note). good_qty / reject_qty are
DELTAS; QTY_ADJUST stores the delta that was applied. Until PLC / IoT is connected the operator
reports output on the screen (source MANUAL); later the gateway writes the same events (source PLC).

Run with:

    python -m scripts.create_production_execution_tables
"""

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS production_execution_logs (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        work_order_id BIGINT UNSIGNED NOT NULL,
        event_type ENUM('START','OUTPUT','REJECT','QTY_ADJUST','HOLD','RESUME','END','NOTE') NOT NULL,
        good_qty INT NOT NULL DEFAULT 0,
        reject_qty INT NOT NULL DEFAULT 0,
        cycle_time_sec DECIMAL(10,2) NULL,
        reason_code_id BIGINT UNSIGNED NULL,
        reason_text VARCHAR(500) NULL,
        machine_id INT UNSIGNED NULL,
        mold_id BIGINT UNSIGNED NULL,
        shift_id BIGINT UNSIGNED NULL,
        operator_code VARCHAR(100) NULL,
        source ENUM('MANUAL','PLC','SYSTEM') NOT NULL DEFAULT 'MANUAL',
        actor VARCHAR(100) NULL,
        event_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_pel_wo (work_order_id, event_at),
        KEY idx_pel_time (event_at),
        KEY idx_pel_type (event_type, event_at),
        CONSTRAINT fk_pel_work_order FOREIGN KEY (work_order_id) REFERENCES production_plan_details(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

WO_COLUMNS = {
    "operator_code": "VARCHAR(100) NULL",
    "leader_code": "VARCHAR(100) NULL",
    "shift_id": "BIGINT UNSIGNED NULL",
    "last_output_at": "DATETIME NULL",
}


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        for statement in STATEMENTS:
            cur.execute(statement)
        cur.execute("SHOW COLUMNS FROM production_plan_details")
        existing = {row[0] for row in cur.fetchall()}
        for name, ddl in WO_COLUMNS.items():
            if name not in existing:
                cur.execute(f"ALTER TABLE production_plan_details ADD COLUMN {name} {ddl}")
        conn.commit()
        print("Production execution tables created.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
