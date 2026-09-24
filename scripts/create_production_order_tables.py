"""One-off script: create the Production Order tables in the live dev database.

Mirrors scripts/create_production_planning_tables.py (raw mysql.connector via
app.core.database.get_vcc_plastics_connection()).

- customers: minimal customer master (roadmap step 2.1) so orders reference a
  customer by id instead of by name.
- production_orders: order header and lifecycle
  DRAFT -> RELEASED -> SCHEDULING -> IN_PRODUCTION -> COMPLETED -> CLOSED (+ CANCELLED).
- production_order_status_history: append-only status history (roadmap step 4.4).
- production_order_notes: operational notes on an order (append-only).
- production_plan_details gets production_order_id (Work Order -> Production Order)
  and reject_qty (reject pieces, entered manually until Production Result exists).

Run with:

    python -m scripts.create_production_order_tables
"""

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS customers (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        customer_code VARCHAR(50) NOT NULL,
        customer_name VARCHAR(255) NOT NULL,
        short_name VARCHAR(100) NULL,
        country VARCHAR(100) NULL,
        contact_name VARCHAR(150) NULL,
        contact_email VARCHAR(150) NULL,
        contact_phone VARCHAR(50) NULL,
        status ENUM('ACTIVE','INACTIVE') NOT NULL DEFAULT 'ACTIVE',
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_customers_code (customer_code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS production_orders (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        order_no VARCHAR(50) NOT NULL,
        customer_id BIGINT UNSIGNED NULL,
        product_id BIGINT UNSIGNED NOT NULL,
        order_qty INT UNSIGNED NOT NULL,
        priority ENUM('LOW','MEDIUM','HIGH','URGENT') NOT NULL DEFAULT 'MEDIUM',
        planned_start DATETIME NULL,
        planned_end DATETIME NULL,
        due_date DATETIME NULL,
        factory_node_id BIGINT UNSIGNED NULL,
        source ENUM('MANUAL','SALES_ORDER','FORECAST','MRP','PLANNING') NOT NULL DEFAULT 'MANUAL',
        source_ref VARCHAR(100) NULL,
        status ENUM('DRAFT','RELEASED','SCHEDULING','IN_PRODUCTION','COMPLETED','CLOSED','CANCELLED') NOT NULL DEFAULT 'DRAFT',
        remark VARCHAR(500) NULL,
        released_by VARCHAR(100) NULL,
        released_at DATETIME NULL,
        completed_at DATETIME NULL,
        closed_by VARCHAR(100) NULL,
        closed_at DATETIME NULL,
        cancelled_by VARCHAR(100) NULL,
        cancelled_at DATETIME NULL,
        cancel_reason VARCHAR(500) NULL,
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_production_orders_no (order_no),
        KEY idx_production_orders_status (status),
        KEY idx_production_orders_plan (planned_start),
        CONSTRAINT fk_production_orders_customer FOREIGN KEY (customer_id)
            REFERENCES customers(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_production_orders_product FOREIGN KEY (product_id)
            REFERENCES products(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_production_orders_factory_node FOREIGN KEY (factory_node_id)
            REFERENCES factory_structure_nodes(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS production_order_status_history (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        production_order_id BIGINT UNSIGNED NOT NULL,
        action VARCHAR(30) NOT NULL,
        from_status VARCHAR(30) NULL,
        to_status VARCHAR(30) NULL,
        actor VARCHAR(100) NOT NULL,
        acted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        remark VARCHAR(500) NULL,
        PRIMARY KEY (id),
        KEY idx_production_order_status_history (production_order_id, acted_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS production_order_notes (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        production_order_id BIGINT UNSIGNED NOT NULL,
        note_text VARCHAR(2000) NOT NULL,
        created_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_production_order_notes (production_order_id, created_at),
        CONSTRAINT fk_production_order_notes_order FOREIGN KEY (production_order_id)
            REFERENCES production_orders(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

COLUMNS = [
    ("production_plan_details", "production_order_id",
     "ALTER TABLE production_plan_details ADD COLUMN production_order_id BIGINT UNSIGNED NULL AFTER plan_id, "
     "ADD KEY idx_production_plan_details_order (production_order_id), "
     "ADD CONSTRAINT fk_ppd_production_order FOREIGN KEY (production_order_id) "
     "REFERENCES production_orders(id) ON UPDATE RESTRICT ON DELETE RESTRICT"),
    ("production_plan_details", "reject_qty",
     "ALTER TABLE production_plan_details ADD COLUMN reject_qty INT UNSIGNED NOT NULL DEFAULT 0 AFTER actual_qty"),
]


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        for statement in STATEMENTS:
            cur.execute(statement)
        for table, column, statement in COLUMNS:
            cur.execute(
                """SELECT COUNT(*) FROM information_schema.columns
                   WHERE table_schema = DATABASE() AND table_name=%s AND column_name=%s""",
                (table, column),
            )
            if cur.fetchone()[0] == 0:
                cur.execute(statement)
        conn.commit()
        print("Production Order tables created (or already existed).")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
