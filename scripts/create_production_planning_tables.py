"""One-off script: create the Production Planning tables in the live dev database.

Mirrors scripts/create_reason_code_tables.py (raw mysql.connector via
app.core.database.get_vcc_plastics_connection()).

- production_plans: one plan per business date + shift + plant (factory node).
- production_plan_details: the Work Orders being planned. plan_id NULL means the
  Work Order is still in the backlog (unscheduled).
- material_inventory_balances: minimal on-hand stock per material, used by the
  planning Material Check until the Material Management / inventory module exists.
- production_planning_audit_log: status history for plans and Work Orders.

Run with:

    python -m scripts.create_production_planning_tables
"""

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS production_plans (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        plan_code VARCHAR(100) NOT NULL,
        plan_date DATE NOT NULL,
        shift_id BIGINT UNSIGNED NOT NULL,
        factory_node_id BIGINT UNSIGNED NULL,
        status ENUM('DRAFT','CONFIRMED','PUBLISHED','CANCELLED') NOT NULL DEFAULT 'DRAFT',
        remark VARCHAR(500) NULL,
        confirmed_by VARCHAR(100) NULL,
        confirmed_at DATETIME NULL,
        published_by VARCHAR(100) NULL,
        published_at DATETIME NULL,
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_production_plans_code (plan_code),
        KEY idx_production_plans_date (plan_date, shift_id),
        CONSTRAINT fk_production_plans_shift FOREIGN KEY (shift_id)
            REFERENCES shifts(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_production_plans_factory_node FOREIGN KEY (factory_node_id)
            REFERENCES factory_structure_nodes(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS production_plan_details (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        wo_no VARCHAR(50) NOT NULL,
        plan_id BIGINT UNSIGNED NULL,
        product_id BIGINT UNSIGNED NOT NULL,
        mold_id BIGINT UNSIGNED NULL,
        machine_id INT UNSIGNED NULL,
        material_id BIGINT UNSIGNED NULL,
        planned_qty INT UNSIGNED NOT NULL,
        actual_qty INT UNSIGNED NOT NULL DEFAULT 0,
        cavity INT UNSIGNED NOT NULL DEFAULT 1,
        cycle_time_sec DECIMAL(8,2) NOT NULL,
        setup_minutes INT UNSIGNED NOT NULL DEFAULT 0,
        part_weight_g DECIMAL(10,3) NULL,
        planned_start DATETIME NULL,
        planned_end DATETIME NULL,
        due_date DATETIME NULL,
        priority ENUM('LOW','MEDIUM','HIGH','URGENT') NOT NULL DEFAULT 'MEDIUM',
        source ENUM('MANUAL','SALES_ORDER','FORECAST','STOCK') NOT NULL DEFAULT 'MANUAL',
        source_ref VARCHAR(100) NULL,
        backlog_reason VARCHAR(200) NULL,
        status ENUM('UNSCHEDULED','SCHEDULED','RELEASED','COMPLETED','CANCELLED') NOT NULL DEFAULT 'UNSCHEDULED',
        remark VARCHAR(500) NULL,
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_production_plan_details_wo (wo_no),
        KEY idx_production_plan_details_plan (plan_id),
        KEY idx_production_plan_details_status (status),
        KEY idx_production_plan_details_machine_time (machine_id, planned_start),
        CONSTRAINT fk_ppd_plan FOREIGN KEY (plan_id)
            REFERENCES production_plans(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_ppd_product FOREIGN KEY (product_id)
            REFERENCES products(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_ppd_mold FOREIGN KEY (mold_id)
            REFERENCES molds(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_ppd_machine FOREIGN KEY (machine_id)
            REFERENCES equipment(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_ppd_material FOREIGN KEY (material_id)
            REFERENCES materials(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS material_inventory_balances (
        material_id BIGINT UNSIGNED NOT NULL,
        available_qty DECIMAL(14,3) NOT NULL DEFAULT 0,
        safety_stock_qty DECIMAL(14,3) NOT NULL DEFAULT 0,
        updated_by VARCHAR(100) NULL,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (material_id),
        CONSTRAINT fk_material_inventory_balances_material FOREIGN KEY (material_id)
            REFERENCES materials(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS production_planning_audit_log (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        entity_type ENUM('PLAN','WORK_ORDER') NOT NULL,
        entity_id BIGINT UNSIGNED NOT NULL,
        action VARCHAR(30) NOT NULL,
        from_status VARCHAR(30) NULL,
        to_status VARCHAR(30) NULL,
        actor VARCHAR(100) NOT NULL,
        acted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        remark VARCHAR(500) NULL,
        PRIMARY KEY (id),
        KEY idx_production_planning_audit_log (entity_type, entity_id, acted_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        for statement in STATEMENTS:
            cur.execute(statement)
        conn.commit()
        print("Production Planning tables created (or already existed).")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
