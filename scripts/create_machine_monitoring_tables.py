"""One-off script: Machine & Equipment module (screens 2.1 - 2.5) data foundation.

Sources: PLC / IoT Gateway post to /api/machine-data/ingest (source PLC / IOT). Until the gateway is
connected, a simulator (system setting iot.simulator_enabled) posts the same data with source
SIMULATOR, and operators report downtime / alarms manually (source MANUAL). Work Order hold /
resume in Production Execution opens / closes a downtime event automatically (source MES).

    machine_status_events        every status change of a machine / equipment (RUNNING, IDLE, DOWN, MAINTENANCE, OFFLINE)
    machine_parameter_readings   time series of process parameters (pressure, temperature, speed, cycle...)
    machine_component_status     latest state of machine components (robot, hopper, cooling water...)
    downtime_events              stop events with reason code, planned / unplanned, duration, root cause
    machine_alarms               alarm lifecycle ACTIVE -> ACKNOWLEDGED / SHELVED -> CLEARED -> CLOSED
    machine_alarm_actions        who did what on an alarm (acknowledge, shelve, clear, close) with note
    equipment.layout_x/layout_y  position (0-100 %) on the area layout image (Factory Structure image)

Run with:

    python -m scripts.create_machine_monitoring_tables
"""

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS machine_status_events (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        equipment_id INT UNSIGNED NOT NULL,
        status VARCHAR(20) NOT NULL,
        previous_status VARCHAR(20) NULL,
        started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        ended_at DATETIME NULL,
        work_order_id BIGINT UNSIGNED NULL,
        source VARCHAR(20) NOT NULL DEFAULT 'MES',
        remark VARCHAR(500) NULL,
        PRIMARY KEY (id),
        KEY idx_mse_equipment (equipment_id, started_at),
        KEY idx_mse_open (equipment_id, ended_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS machine_parameter_readings (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        equipment_id INT UNSIGNED NOT NULL,
        parameter_code VARCHAR(50) NOT NULL,
        parameter_name VARCHAR(100) NULL,
        value DECIMAL(14,4) NOT NULL,
        unit VARCHAR(20) NULL,
        read_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        source VARCHAR(20) NOT NULL DEFAULT 'PLC',
        PRIMARY KEY (id),
        KEY idx_mpr_equipment (equipment_id, parameter_code, read_at),
        KEY idx_mpr_time (read_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS machine_component_status (
        equipment_id INT UNSIGNED NOT NULL,
        component_code VARCHAR(40) NOT NULL,
        status VARCHAR(20) NOT NULL,
        value VARCHAR(100) NULL,
        message VARCHAR(255) NULL,
        source VARCHAR(20) NOT NULL DEFAULT 'PLC',
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (equipment_id, component_code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS downtime_events (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        downtime_no VARCHAR(50) NOT NULL,
        equipment_id INT UNSIGNED NOT NULL,
        work_order_id BIGINT UNSIGNED NULL,
        started_at DATETIME NOT NULL,
        ended_at DATETIME NULL,
        reason_code_id BIGINT UNSIGNED NULL,
        reason_text VARCHAR(500) NULL,
        category ENUM('PLANNED','UNPLANNED') NOT NULL DEFAULT 'UNPLANNED',
        status ENUM('OPEN','CLOSED','CANCELLED') NOT NULL DEFAULT 'OPEN',
        source VARCHAR(20) NOT NULL DEFAULT 'MANUAL',
        reporter VARCHAR(100) NULL,
        root_cause VARCHAR(1000) NULL,
        action_taken VARCHAR(1000) NULL,
        closed_by VARCHAR(100) NULL,
        alarm_id BIGINT UNSIGNED NULL,
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_downtime_no (downtime_no),
        KEY idx_dt_equipment (equipment_id, started_at),
        KEY idx_dt_time (started_at),
        KEY idx_dt_status (status),
        KEY idx_dt_wo (work_order_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS machine_alarms (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        alarm_no VARCHAR(50) NOT NULL,
        equipment_id INT UNSIGNED NULL,
        alarm_code VARCHAR(50) NULL,
        message VARCHAR(500) NOT NULL,
        priority ENUM('CRITICAL','HIGH','MEDIUM','LOW') NOT NULL DEFAULT 'MEDIUM',
        source VARCHAR(20) NOT NULL DEFAULT 'PLC',
        parameter_code VARCHAR(50) NULL,
        value DECIMAL(14,4) NULL,
        limit_value DECIMAL(14,4) NULL,
        status ENUM('ACTIVE','ACKNOWLEDGED','SHELVED','CLEARED','CLOSED') NOT NULL DEFAULT 'ACTIVE',
        raised_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        acknowledged_at DATETIME NULL,
        acknowledged_by VARCHAR(100) NULL,
        shelved_until DATETIME NULL,
        cleared_at DATETIME NULL,
        closed_at DATETIME NULL,
        closed_by VARCHAR(100) NULL,
        work_order_id BIGINT UNSIGNED NULL,
        note VARCHAR(1000) NULL,
        version INT UNSIGNED NOT NULL DEFAULT 1,
        PRIMARY KEY (id),
        UNIQUE KEY uk_alarm_no (alarm_no),
        KEY idx_alarm_equipment (equipment_id, raised_at),
        KEY idx_alarm_status (status, priority),
        KEY idx_alarm_time (raised_at),
        KEY idx_alarm_code (equipment_id, alarm_code, status)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS machine_alarm_actions (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        alarm_id BIGINT UNSIGNED NOT NULL,
        action VARCHAR(20) NOT NULL,
        from_status VARCHAR(20) NULL,
        to_status VARCHAR(20) NULL,
        actor VARCHAR(100) NULL,
        note VARCHAR(1000) NULL,
        acted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_maa_alarm (alarm_id, acted_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

EQUIPMENT_COLUMNS = {"layout_x": "DECIMAL(6,2) NULL", "layout_y": "DECIMAL(6,2) NULL"}

# standard downtime reasons of spec 2.4.2 (admin can edit them later in Reason Code Master)
DOWNTIME_REASONS = [
    ("DT-MAT", "Material Shortage"), ("DT-MOLD", "Mold Change"), ("DT-BRK", "Machine Breakdown"),
    ("DT-SETUP", "Setup / Adjustment"), ("DT-QUAL", "Quality Issue"), ("DT-PWR", "Power Failure"),
    ("DT-UTIL", "Utility Failure"), ("DT-PM", "Preventive Maintenance"), ("DT-OTH", "Others"),
]

SETTINGS = {"iot.simulator_enabled": "1", "iot.simulator_interval_sec": "10", "iot.reading_retention_days": "30"}


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        for statement in STATEMENTS:
            cur.execute(statement)
        cur.execute("SHOW COLUMNS FROM equipment")
        existing = {row[0] for row in cur.fetchall()}
        for name, ddl in EQUIPMENT_COLUMNS.items():
            if name not in existing:
                cur.execute(f"ALTER TABLE equipment ADD COLUMN {name} {ddl}")
        cur.execute("SELECT id FROM reason_code_categories WHERE category_code='DOWNTIME'")
        row = cur.fetchone()
        if row:
            category_id = row[0]
            for code, name in DOWNTIME_REASONS:
                cur.execute("SELECT id FROM reason_codes WHERE reason_code=%s", (code,))
                if not cur.fetchone():
                    cur.execute("""INSERT INTO reason_codes (reason_code, reason_name, category_id, code_type, applicable_module, impact_level, status, created_by)
                                   VALUES (%s,%s,%s,'MANUAL','DOWNTIME_MANAGEMENT','MEDIUM','ACTIVE','SYSTEM')""", (code, name, category_id))
        for key, value in SETTINGS.items():
            cur.execute("SELECT 1 FROM system_settings WHERE setting_key=%s", (key,))
            if not cur.fetchone():
                cur.execute("INSERT INTO system_settings (setting_key, setting_value) VALUES (%s,%s)", (key, value))
        conn.commit()
        print("Machine monitoring tables created.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
