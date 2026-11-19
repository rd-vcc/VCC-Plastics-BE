"""One-off script: create the Shift & Calendar Master tables in the live dev database.

Mirrors scripts/create_material_master_tables.py (raw mysql.connector via
app.core.database.get_vcc_plastics_connection()).

Run with:

    python -m scripts.create_shift_calendar_tables
"""

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS shifts (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        shift_code VARCHAR(50) NOT NULL,
        shift_name VARCHAR(200) NOT NULL,
        shift_type VARCHAR(100) NULL,
        start_time TIME NOT NULL,
        end_time TIME NOT NULL,
        break_minutes INT NOT NULL DEFAULT 0,
        working_hours DECIMAL(5,2) NOT NULL,
        is_overnight TINYINT(1) NOT NULL DEFAULT 0,
        applicable_area_id BIGINT UNSIGNED NULL,
        effective_date DATE NOT NULL,
        overtime_allowed TINYINT(1) NOT NULL DEFAULT 0,
        allow_overlap TINYINT(1) NOT NULL DEFAULT 0,
        color_code VARCHAR(20) NULL,
        description TEXT NULL,
        status ENUM('ACTIVE','INACTIVE') NOT NULL DEFAULT 'ACTIVE',
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_shifts_code (shift_code),
        KEY idx_shifts_status (status),
        KEY idx_shifts_area (applicable_area_id),
        CONSTRAINT fk_shifts_area FOREIGN KEY (applicable_area_id)
            REFERENCES factory_structure_nodes(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS calendar_days (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        calendar_date DATE NOT NULL,
        day_type ENUM('HOLIDAY','WEEKEND','SPECIAL_DAY','COMPANY_EVENT','FACTORY_SHUTDOWN','OVERTIME','OTHER_HOLIDAY','OTHER_SPECIAL') NOT NULL,
        custom_type_name VARCHAR(100) NULL,
        name VARCHAR(200) NOT NULL,
        applicable_area_id BIGINT UNSIGNED NULL,
        description VARCHAR(500) NULL,
        status ENUM('ACTIVE','INACTIVE') NOT NULL DEFAULT 'ACTIVE',
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_calendar_days_date_area (calendar_date, applicable_area_id),
        KEY idx_calendar_days_type (day_type),
        CONSTRAINT fk_calendar_days_area FOREIGN KEY (applicable_area_id)
            REFERENCES factory_structure_nodes(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS shift_calendar_audit_log (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        entity_type ENUM('SHIFT','CALENDAR_DAY') NOT NULL,
        entity_id BIGINT UNSIGNED NOT NULL,
        action ENUM('CREATE','UPDATE','ACTIVATE','DEACTIVATE','DELETE') NOT NULL,
        from_status VARCHAR(30) NULL,
        to_status VARCHAR(30) NULL,
        actor VARCHAR(100) NOT NULL,
        acted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        remark VARCHAR(500) NULL,
        PRIMARY KEY (id),
        KEY idx_shift_calendar_audit_log (entity_type, entity_id, acted_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS shift_pattern_assignments (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        area_id BIGINT UNSIGNED NOT NULL,
        shift_type VARCHAR(100) NOT NULL,
        effective_from DATE NOT NULL,
        effective_to DATE NULL,
        remark VARCHAR(500) NULL,
        created_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_by VARCHAR(100) NULL,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uq_shift_pattern_area_from (area_id, effective_from),
        CONSTRAINT fk_shift_pattern_area FOREIGN KEY (area_id)
            REFERENCES factory_structure_nodes(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    # Existing databases: add the WEEKEND day type (safe to re-run).
    """
    ALTER TABLE calendar_days
        MODIFY day_type ENUM('HOLIDAY','WEEKEND','SPECIAL_DAY','COMPANY_EVENT','FACTORY_SHUTDOWN','OVERTIME','OTHER_HOLIDAY','OTHER_SPECIAL') NOT NULL
    """,
    # Existing databases: free-text name for OTHER_HOLIDAY / OTHER_SPECIAL (skipped when present).
    """
    SET @has_col = (SELECT COUNT(*) FROM information_schema.COLUMNS
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'calendar_days' AND COLUMN_NAME = 'custom_type_name')
    """,
    """
    SET @ddl = IF(@has_col = 0,
        'ALTER TABLE calendar_days ADD COLUMN custom_type_name VARCHAR(100) NULL AFTER day_type', 'DO 0')
    """,
    "PREPARE stmt FROM @ddl",
    "EXECUTE stmt",
    "DEALLOCATE PREPARE stmt",
]


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        for statement in STATEMENTS:
            cur.execute(statement)
        conn.commit()
        print("Shift & Calendar Master tables created (or already existed).")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
