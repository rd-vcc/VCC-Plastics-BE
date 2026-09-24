"""One-off script: Material Management step 2 (Material Requirement).

- material_requirements (data standard 3.5 Material Planning): need per Work Order x
  material from the product's ACTIVE BOM. required_qty = BOM x planned qty,
  open_qty = BOM x (planned - produced good qty). Kept in sync by
  app.modules.material.requirements.service.sync_work_order.
- material_requirement_adjustments: history of manual adjustments (who/when/why).
- production_order_materials (data standard 4.6 Material Assignment): materials
  assigned to a Production Order from its BOM when the order is released.
- production_orders gets bom_id / bom_revision: the BOM revision the order was released with.

Run with:

    python -m scripts.create_material_requirement_tables
"""

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS material_requirements (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        work_order_id BIGINT UNSIGNED NOT NULL,
        material_id BIGINT UNSIGNED NOT NULL,
        bom_id BIGINT UNSIGNED NULL,
        bom_revision VARCHAR(20) NULL,
        required_qty DECIMAL(16,4) NOT NULL DEFAULT 0,
        open_qty DECIMAL(16,4) NOT NULL DEFAULT 0,
        adjustment_qty DECIMAL(16,4) NOT NULL DEFAULT 0,
        required_date DATETIME NULL,
        status ENUM('PLANNED','RESERVED','READY','SHORTAGE','CANCELLED') NOT NULL DEFAULT 'PLANNED',
        remark VARCHAR(500) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_material_requirements (work_order_id, material_id),
        KEY idx_material_requirements_material (material_id, status),
        KEY idx_material_requirements_date (required_date),
        CONSTRAINT fk_mr_work_order FOREIGN KEY (work_order_id)
            REFERENCES production_plan_details(id) ON UPDATE RESTRICT ON DELETE CASCADE,
        CONSTRAINT fk_mr_material FOREIGN KEY (material_id)
            REFERENCES materials(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_mr_bom FOREIGN KEY (bom_id)
            REFERENCES boms(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS material_requirement_adjustments (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        requirement_id BIGINT UNSIGNED NOT NULL,
        old_qty DECIMAL(16,4) NOT NULL,
        new_qty DECIMAL(16,4) NOT NULL,
        reason VARCHAR(500) NOT NULL,
        actor VARCHAR(100) NOT NULL,
        acted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_mra_requirement (requirement_id, acted_at),
        CONSTRAINT fk_mra_requirement FOREIGN KEY (requirement_id)
            REFERENCES material_requirements(id) ON UPDATE RESTRICT ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS production_order_materials (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        production_order_id BIGINT UNSIGNED NOT NULL,
        material_id BIGINT UNSIGNED NOT NULL,
        bom_id BIGINT UNSIGNED NULL,
        bom_revision VARCHAR(20) NULL,
        required_qty DECIMAL(16,4) NOT NULL,
        unit VARCHAR(20) NULL,
        status ENUM('ASSIGNED','RESERVED','READY','CANCELLED') NOT NULL DEFAULT 'ASSIGNED',
        remark VARCHAR(500) NULL,
        created_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_production_order_materials (production_order_id, material_id),
        CONSTRAINT fk_pom_order FOREIGN KEY (production_order_id)
            REFERENCES production_orders(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_pom_material FOREIGN KEY (material_id)
            REFERENCES materials(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_pom_bom FOREIGN KEY (bom_id)
            REFERENCES boms(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

COLUMNS = [
    ("production_orders", "bom_id",
     "ALTER TABLE production_orders ADD COLUMN bom_id BIGINT UNSIGNED NULL AFTER product_id, "
     "ADD COLUMN bom_revision VARCHAR(20) NULL AFTER bom_id, "
     "ADD CONSTRAINT fk_production_orders_bom FOREIGN KEY (bom_id) REFERENCES boms(id) ON UPDATE RESTRICT ON DELETE RESTRICT"),
]


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        for statement in STATEMENTS:
            cur.execute(statement)
        for table, column, statement in COLUMNS:
            cur.execute("""SELECT COUNT(*) FROM information_schema.columns
                           WHERE table_schema=DATABASE() AND table_name=%s AND column_name=%s""", (table, column))
            if cur.fetchone()[0] == 0:
                cur.execute(statement)
        conn.commit()
        print("Material requirement tables created (or already existed).")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
