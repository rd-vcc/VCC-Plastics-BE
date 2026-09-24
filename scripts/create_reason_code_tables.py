"""One-off script: create the Reason Code Master tables in the live dev database.

Mirrors scripts/create_shift_calendar_tables.py (raw mysql.connector via
app.core.database.get_vcc_plastics_connection()).

Run with:

    python -m scripts.create_reason_code_tables
"""

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS reason_code_categories (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        category_code VARCHAR(50) NOT NULL,
        category_name VARCHAR(200) NOT NULL,
        description VARCHAR(500) NULL,
        sort_order INT NOT NULL DEFAULT 0,
        status ENUM('ACTIVE','INACTIVE') NOT NULL DEFAULT 'ACTIVE',
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_reason_code_categories_code (category_code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS reason_codes (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        reason_code VARCHAR(50) NOT NULL,
        reason_name VARCHAR(200) NOT NULL,
        category_id BIGINT UNSIGNED NOT NULL,
        code_type ENUM('SYSTEM','MANUAL','AUTO') NOT NULL DEFAULT 'MANUAL',
        applicable_module ENUM('MACHINE_MONITORING','DOWNTIME_MANAGEMENT','NG_MANAGEMENT',
            'SCRAP_MANAGEMENT','MAINTENANCE_MANAGEMENT','PRODUCTION_EXECUTION') NOT NULL,
        description TEXT NULL,
        impact_level ENUM('LOW','MEDIUM','HIGH') NOT NULL DEFAULT 'MEDIUM',
        sort_order INT NOT NULL DEFAULT 0,
        status ENUM('DRAFT','PENDING_APPROVAL','ACTIVE','INACTIVE') NOT NULL DEFAULT 'DRAFT',
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
        UNIQUE KEY uk_reason_codes_code (reason_code),
        KEY idx_reason_codes_status (status),
        KEY idx_reason_codes_category (category_id),
        CONSTRAINT fk_reason_codes_category FOREIGN KEY (category_id)
            REFERENCES reason_code_categories(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS reason_code_audit_log (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        reason_code_id BIGINT UNSIGNED NOT NULL,
        action ENUM('CREATE','UPDATE','SUBMIT','APPROVE','REJECT','ACTIVATE','DEACTIVATE','DELETE') NOT NULL,
        from_status VARCHAR(30) NULL,
        to_status VARCHAR(30) NULL,
        actor VARCHAR(100) NOT NULL,
        acted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        remark VARCHAR(500) NULL,
        PRIMARY KEY (id),
        KEY idx_reason_code_audit_log (reason_code_id, acted_at)
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
        print("Reason Code Master tables created (or already existed).")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
