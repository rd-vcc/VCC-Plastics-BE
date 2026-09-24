"""One-off script: create the Measuring Equipment Master tables in the live dev database.

Mirrors scripts/create_material_master_tables.py (raw mysql.connector via
app.core.database.get_vcc_plastics_connection()).

Run with:

    python -m scripts.create_measuring_equipment_tables
"""

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS measuring_equipment_types (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        type_code VARCHAR(50) NOT NULL,
        type_name VARCHAR(150) NOT NULL,
        category ENUM('DIMENSIONAL','ELECTRICAL','PHYSICAL_TEST','VISION_OPTICAL','OTHER') NOT NULL DEFAULT 'DIMENSIONAL',
        description VARCHAR(500) NULL,
        is_active TINYINT(1) NOT NULL DEFAULT 1,
        sort_order INT NOT NULL DEFAULT 0,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_measuring_equipment_types_code (type_code),
        KEY idx_measuring_equipment_types_category (category),
        KEY idx_measuring_equipment_types_active (is_active)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS measuring_equipment (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        equipment_code VARCHAR(50) NOT NULL,
        equipment_name VARCHAR(200) NOT NULL,
        equipment_type_id BIGINT UNSIGNED NOT NULL,
        factory_node_id BIGINT UNSIGNED NOT NULL,
        manufacturer VARCHAR(150) NULL,
        model VARCHAR(150) NULL,
        serial_number VARCHAR(150) NULL,
        measurement_range VARCHAR(100) NULL,
        resolution VARCHAR(100) NULL,
        accuracy VARCHAR(100) NULL,
        calibration_cycle_days INT NOT NULL,
        last_calibration_date DATE NULL,
        next_calibration_date DATE NULL,
        status ENUM('AVAILABLE','IN_USE','OUT_OF_SERVICE','INACTIVE') NOT NULL DEFAULT 'AVAILABLE',
        description TEXT NULL,
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_measuring_equipment_code (equipment_code),
        KEY idx_measuring_equipment_name (equipment_name),
        KEY idx_measuring_equipment_type (equipment_type_id),
        KEY idx_measuring_equipment_node (factory_node_id),
        KEY idx_measuring_equipment_status (status),
        KEY idx_measuring_equipment_next_cal (next_calibration_date),
        CONSTRAINT fk_measuring_equipment_type FOREIGN KEY (equipment_type_id)
            REFERENCES measuring_equipment_types(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_measuring_equipment_node FOREIGN KEY (factory_node_id)
            REFERENCES factory_structure_nodes(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS measuring_equipment_calibration_history (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        equipment_id BIGINT UNSIGNED NOT NULL,
        calibration_date DATE NOT NULL,
        next_due_date DATE NOT NULL,
        result ENUM('PASS','FAIL','ADJUSTED') NOT NULL DEFAULT 'PASS',
        calibrated_by VARCHAR(150) NULL,
        certificate_no VARCHAR(100) NULL,
        remark VARCHAR(500) NULL,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_measuring_equipment_cal_history (equipment_id, calibration_date),
        CONSTRAINT fk_measuring_equipment_cal_history_equipment FOREIGN KEY (equipment_id)
            REFERENCES measuring_equipment(id) ON UPDATE RESTRICT ON DELETE CASCADE
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
        print("Measuring Equipment Master tables created (or already existed).")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
