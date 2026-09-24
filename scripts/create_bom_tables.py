"""One-off script: create the Product BOM tables in the live dev database (roadmap step 2.2).

- boms: one BOM revision of a product (optionally tied to a product version).
  Quantities in bom_items are "per base_qty pieces" in the material's own unit,
  e.g. 13.3 KG per 1000 PCS, so no unit conversion is needed.
  Only one ACTIVE BOM per product; activating a new revision retires the old one.
  ACTIVE / INACTIVE revisions are read-only: changes go through a new revision.
- bom_items: material lines of a BOM.
- bom_audit_log: status history of BOM revisions.

Run with:

    python -m scripts.create_bom_tables
"""

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS boms (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        product_id BIGINT UNSIGNED NOT NULL,
        product_version_id BIGINT UNSIGNED NULL,
        bom_revision VARCHAR(20) NOT NULL,
        base_qty DECIMAL(14,3) NOT NULL DEFAULT 1000,
        status ENUM('DRAFT','ACTIVE','INACTIVE') NOT NULL DEFAULT 'DRAFT',
        effective_from DATE NULL,
        effective_to DATE NULL,
        description VARCHAR(500) NULL,
        activated_by VARCHAR(100) NULL,
        activated_at DATETIME NULL,
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_boms_product_revision (product_id, bom_revision),
        KEY idx_boms_status (product_id, status),
        CONSTRAINT fk_boms_product FOREIGN KEY (product_id)
            REFERENCES products(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_boms_product_version FOREIGN KEY (product_version_id)
            REFERENCES product_versions(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS bom_items (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        bom_id BIGINT UNSIGNED NOT NULL,
        line_no INT UNSIGNED NOT NULL,
        material_id BIGINT UNSIGNED NOT NULL,
        component_type ENUM('RESIN','MASTERBATCH','ADDITIVE','INSERT','PACKAGING','CONSUMABLE','OTHER') NOT NULL DEFAULT 'RESIN',
        qty_per_base DECIMAL(18,6) NOT NULL,
        scrap_pct DECIMAL(6,2) NOT NULL DEFAULT 0,
        is_critical TINYINT(1) NOT NULL DEFAULT 1,
        remark VARCHAR(500) NULL,
        PRIMARY KEY (id),
        UNIQUE KEY uk_bom_items_material (bom_id, material_id),
        KEY idx_bom_items_material (material_id),
        CONSTRAINT fk_bom_items_bom FOREIGN KEY (bom_id)
            REFERENCES boms(id) ON UPDATE RESTRICT ON DELETE CASCADE,
        CONSTRAINT fk_bom_items_material FOREIGN KEY (material_id)
            REFERENCES materials(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS bom_audit_log (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        bom_id BIGINT UNSIGNED NOT NULL,
        action VARCHAR(30) NOT NULL,
        from_status VARCHAR(30) NULL,
        to_status VARCHAR(30) NULL,
        actor VARCHAR(100) NOT NULL,
        acted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        remark VARCHAR(500) NULL,
        PRIMARY KEY (id),
        KEY idx_bom_audit_log (bom_id, acted_at)
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
        print("BOM tables created (or already existed).")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
