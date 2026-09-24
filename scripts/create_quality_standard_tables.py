"""One-off script: create the Quality Standard Master tables in the live dev database.

Mirrors scripts/create_material_master_tables.py (raw mysql.connector via
app.core.database.get_vcc_plastics_connection()).

Run with:

    python -m scripts.create_quality_standard_tables
"""

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS quality_standards (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        standard_code VARCHAR(50) NOT NULL,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_quality_standards_code (standard_code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS quality_standard_revisions (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        standard_id BIGINT UNSIGNED NOT NULL,
        revision_no INT UNSIGNED NOT NULL,
        standard_name VARCHAR(200) NOT NULL,
        product_id BIGINT UNSIGNED NULL,
        customer VARCHAR(200) NULL,
        process VARCHAR(200) NULL,
        status ENUM('DRAFT','PENDING_APPROVAL','APPROVED','ACTIVE','OBSOLETE') NOT NULL DEFAULT 'DRAFT',
        effective_date DATE NULL,
        remark TEXT NULL,
        submitted_by VARCHAR(100) NULL,
        submitted_at DATETIME NULL,
        approved_by VARCHAR(100) NULL,
        approved_at DATETIME NULL,
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_quality_standard_revisions_no (standard_id, revision_no),
        KEY idx_quality_standard_revisions_status (standard_id, status),
        KEY idx_quality_standard_revisions_product (product_id),
        CONSTRAINT fk_quality_standard_revisions_standard FOREIGN KEY (standard_id)
            REFERENCES quality_standards(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_quality_standard_revisions_product FOREIGN KEY (product_id)
            REFERENCES products(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS quality_standard_characteristics (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        revision_id BIGINT UNSIGNED NOT NULL,
        characteristic_name VARCHAR(200) NOT NULL,
        nominal_value DECIMAL(14,4) NULL,
        lsl DECIMAL(14,4) NULL,
        usl DECIMAL(14,4) NULL,
        unit VARCHAR(50) NULL,
        inspection_method VARCHAR(150) NULL,
        equipment_type_id BIGINT UNSIGNED NULL,
        inspection_frequency VARCHAR(100) NULL,
        sort_order INT NOT NULL DEFAULT 0,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_quality_standard_characteristics_revision (revision_id),
        KEY idx_quality_standard_characteristics_equipment_type (equipment_type_id),
        CONSTRAINT fk_quality_standard_characteristics_revision FOREIGN KEY (revision_id)
            REFERENCES quality_standard_revisions(id) ON UPDATE RESTRICT ON DELETE CASCADE,
        CONSTRAINT fk_quality_standard_characteristics_equipment_type FOREIGN KEY (equipment_type_id)
            REFERENCES measuring_equipment_types(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS quality_standard_audit_log (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        standard_id BIGINT UNSIGNED NOT NULL,
        revision_id BIGINT UNSIGNED NULL,
        action ENUM('CREATE','UPDATE','SUBMIT','APPROVE','REJECT','ACTIVATE','OBSOLETE','NEW_REVISION','DELETE') NOT NULL,
        from_status VARCHAR(30) NULL,
        to_status VARCHAR(30) NULL,
        actor VARCHAR(100) NOT NULL,
        acted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        remark VARCHAR(500) NULL,
        PRIMARY KEY (id),
        KEY idx_quality_standard_audit_log_standard (standard_id, acted_at),
        CONSTRAINT fk_quality_standard_audit_log_standard FOREIGN KEY (standard_id)
            REFERENCES quality_standards(id) ON UPDATE RESTRICT ON DELETE CASCADE,
        CONSTRAINT fk_quality_standard_audit_log_revision FOREIGN KEY (revision_id)
            REFERENCES quality_standard_revisions(id) ON UPDATE RESTRICT ON DELETE SET NULL
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
        print("Quality Standard Master tables created (or already existed).")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
