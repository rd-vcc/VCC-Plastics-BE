"""One-off script: create the Traceability tables (screens 7.1 - 7.3).

- product_lots: finished-product lots, one per Work Order x production date x shift, built from the output
  reported in Production Execution (lot no. = barcode / QR content).
- trace_logs: every trace search (type, key, found, duration) for the traceability KPIs.

Run with:

    python -m scripts.create_traceability_tables
"""

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS product_lots (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        lot_no VARCHAR(40) NOT NULL,
        work_order_id BIGINT UNSIGNED NOT NULL,
        product_id BIGINT UNSIGNED NULL,
        production_date DATE NOT NULL,
        shift_id BIGINT UNSIGNED NULL,
        machine_id BIGINT UNSIGNED NULL,
        mold_id BIGINT UNSIGNED NULL,
        operator_code VARCHAR(50) NULL,
        good_qty INT NOT NULL DEFAULT 0,
        reject_qty INT NOT NULL DEFAULT 0,
        first_output_at DATETIME NULL,
        last_output_at DATETIME NULL,
        status ENUM('OPEN','CLOSED') NOT NULL DEFAULT 'OPEN',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_product_lots_no (lot_no),
        UNIQUE KEY uk_product_lots_key (work_order_id, production_date, shift_id),
        KEY idx_product_lots_date (production_date),
        KEY idx_product_lots_product (product_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS trace_logs (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        trace_type ENUM('PRODUCT','MATERIAL','GENEALOGY') NOT NULL,
        search_by VARCHAR(30) NOT NULL,
        keyword VARCHAR(100) NOT NULL,
        found TINYINT(1) NOT NULL DEFAULT 0,
        result_ref VARCHAR(60) NULL,
        duration_ms INT NOT NULL DEFAULT 0,
        actor VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_trace_logs_type_time (trace_type, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        for s in STATEMENTS:
            cur.execute(s)
        conn.commit()
        print("Traceability tables created (or already existed).")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
