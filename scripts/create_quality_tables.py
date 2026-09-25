"""One-off script: create the Quality Management tables (screens 5.1 - 5.6) in the live dev database.

- Inspection plans / sample records / measurements (Inspection Management, source of SPC data)
- NG records + history (NG Management)
- Quality alerts (Quality Dashboard)
- Calibration plans (Calibration Management) and measuring-equipment events (Measuring Equipment)
- SPC settings on Quality Standard characteristics, operational state on measuring equipment
- Defect codes (Reason Code Master, category DEFECT) and quality settings

Run with:

    python -m scripts.create_quality_tables
"""

from app.core.database import get_vcc_plastics_connection

TABLES = [
    """
    CREATE TABLE IF NOT EXISTS quality_inspection_plans (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        plan_no VARCHAR(30) NOT NULL,
        inspection_type ENUM('IQC','IPQC','FQC','OQC','PATROL','FAI','LAST_PIECE','AUDIT') NOT NULL,
        work_order_id BIGINT UNSIGNED NULL,
        product_id BIGINT UNSIGNED NULL,
        machine_id BIGINT UNSIGNED NULL,
        mold_id BIGINT UNSIGNED NULL,
        material_lot_id BIGINT UNSIGNED NULL,
        standard_revision_id BIGINT UNSIGNED NULL,
        process VARCHAR(30) NOT NULL DEFAULT 'INJECTION',
        frequency_type ENUM('ONCE','TIME','QTY','LOT') NOT NULL DEFAULT 'ONCE',
        frequency_value INT NULL,
        sample_size INT NOT NULL DEFAULT 5,
        planned_at DATETIME NOT NULL,
        due_at DATETIME NOT NULL,
        shift_id BIGINT UNSIGNED NULL,
        inspector VARCHAR(100) NULL,
        status ENUM('NOT_STARTED','IN_PROGRESS','COMPLETED','CANCELLED') NOT NULL DEFAULT 'NOT_STARTED',
        result ENUM('OK','NG') NULL,
        started_at DATETIME NULL,
        completed_at DATETIME NULL,
        source ENUM('AUTO_WO','AUTO_FREQUENCY','MANUAL','CUSTOMER') NOT NULL DEFAULT 'MANUAL',
        parent_plan_id BIGINT UNSIGNED NULL,
        remark VARCHAR(1000) NULL,
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_quality_inspection_plans_no (plan_no),
        KEY idx_qip_wo (work_order_id),
        KEY idx_qip_product (product_id),
        KEY idx_qip_status_due (status, due_at),
        KEY idx_qip_planned (planned_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS quality_inspection_records (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        plan_id BIGINT UNSIGNED NOT NULL,
        sample_no INT NOT NULL,
        inspected_at DATETIME NOT NULL,
        inspector VARCHAR(100) NULL,
        position VARCHAR(100) NULL,
        result ENUM('OK','NG') NOT NULL,
        defect_code_id BIGINT UNSIGNED NULL,
        is_reinspection TINYINT(1) NOT NULL DEFAULT 0,
        remark VARCHAR(500) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_qir_plan (plan_id, sample_no),
        KEY idx_qir_time (inspected_at),
        CONSTRAINT fk_qir_plan FOREIGN KEY (plan_id) REFERENCES quality_inspection_plans(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS quality_measurements (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        record_id BIGINT UNSIGNED NOT NULL,
        characteristic_id BIGINT UNSIGNED NOT NULL,
        measured_value DECIMAL(14,4) NULL,
        attr_result ENUM('OK','NG') NULL,
        result ENUM('OK','NG') NOT NULL,
        equipment_id BIGINT UNSIGNED NULL,
        PRIMARY KEY (id),
        KEY idx_qm_record (record_id),
        KEY idx_qm_char (characteristic_id),
        KEY idx_qm_equipment (equipment_id),
        CONSTRAINT fk_qm_record FOREIGN KEY (record_id) REFERENCES quality_inspection_records(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS quality_ng_records (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        ng_no VARCHAR(30) NOT NULL,
        detected_at DATETIME NOT NULL,
        source ENUM('IQC','IPQC','FQC','OQC','PATROL','FAI','LAST_PIECE','AUDIT','SPC','VISION','MACHINE','CUSTOMER','PRODUCTION') NOT NULL,
        inspection_plan_id BIGINT UNSIGNED NULL,
        work_order_id BIGINT UNSIGNED NULL,
        product_id BIGINT UNSIGNED NULL,
        machine_id BIGINT UNSIGNED NULL,
        mold_id BIGINT UNSIGNED NULL,
        material_lot_id BIGINT UNSIGNED NULL,
        characteristic_id BIGINT UNSIGNED NULL,
        defect_code_id BIGINT UNSIGNED NULL,
        process VARCHAR(30) NOT NULL DEFAULT 'INJECTION',
        ng_qty INT NOT NULL DEFAULT 0,
        inspected_qty INT NULL,
        description VARCHAR(1000) NULL,
        priority ENUM('LOW','MEDIUM','HIGH','CRITICAL') NOT NULL DEFAULT 'MEDIUM',
        status ENUM('NEW','INVESTIGATING','CA_IN_PROGRESS','WAITING_VERIFICATION','CLOSED','REOPENED') NOT NULL DEFAULT 'NEW',
        owner VARCHAR(100) NULL,
        detected_by VARCHAR(100) NULL,
        due_date DATE NULL,
        disposition ENUM('SORT','REWORK','SCRAP','USE_AS_IS','RETURN_SUPPLIER') NULL,
        rework_qty INT NOT NULL DEFAULT 0,
        scrap_qty INT NOT NULL DEFAULT 0,
        containment VARCHAR(1000) NULL,
        wo_held TINYINT(1) NOT NULL DEFAULT 0,
        root_cause_category ENUM('MAN','MACHINE','MATERIAL','METHOD','MEASUREMENT','ENVIRONMENT') NULL,
        root_cause VARCHAR(2000) NULL,
        corrective_action VARCHAR(2000) NULL,
        preventive_action VARCHAR(2000) NULL,
        verification_result VARCHAR(1000) NULL,
        repeat_of_id BIGINT UNSIGNED NULL,
        closed_at DATETIME NULL,
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_quality_ng_records_no (ng_no),
        KEY idx_qng_detected (detected_at),
        KEY idx_qng_status (status),
        KEY idx_qng_wo (work_order_id),
        KEY idx_qng_defect (defect_code_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS quality_ng_events (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        ng_id BIGINT UNSIGNED NOT NULL,
        action VARCHAR(30) NOT NULL,
        from_status VARCHAR(30) NULL,
        to_status VARCHAR(30) NULL,
        actor VARCHAR(100) NULL,
        note VARCHAR(1000) NULL,
        event_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_qnge_ng (ng_id, event_at),
        CONSTRAINT fk_qnge_ng FOREIGN KEY (ng_id) REFERENCES quality_ng_records(id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS quality_alerts (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        alert_key VARCHAR(120) NOT NULL,
        kind VARCHAR(30) NOT NULL,
        severity ENUM('LOW','MEDIUM','HIGH','CRITICAL') NOT NULL DEFAULT 'MEDIUM',
        message VARCHAR(500) NOT NULL,
        ref_type VARCHAR(20) NULL,
        ref_id BIGINT UNSIGNED NULL,
        ref_code VARCHAR(60) NULL,
        status ENUM('NEW','ACKNOWLEDGED','IN_PROGRESS','RESOLVED','CLOSED') NOT NULL DEFAULT 'NEW',
        owner VARCHAR(100) NULL,
        raised_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        resolved_at DATETIME NULL,
        updated_by VARCHAR(100) NULL,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_qa_key (alert_key, status),
        KEY idx_qa_status (status, raised_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS quality_calibration_plans (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        plan_no VARCHAR(30) NOT NULL,
        equipment_id BIGINT UNSIGNED NOT NULL,
        planned_date DATE NOT NULL,
        lab VARCHAR(150) NULL,
        owner VARCHAR(100) NULL,
        priority ENUM('LOW','MEDIUM','HIGH') NOT NULL DEFAULT 'MEDIUM',
        status ENUM('PLANNED','IN_PROGRESS','COMPLETED','CANCELLED') NOT NULL DEFAULT 'PLANNED',
        source ENUM('AUTO','MANUAL','REQUEST') NOT NULL DEFAULT 'AUTO',
        sent_at DATETIME NULL,
        calibration_date DATE NULL,
        result ENUM('PASS','FAIL','ADJUSTED') NULL,
        certificate_no VARCHAR(100) NULL,
        certificate_url VARCHAR(1000) NULL,
        calibrated_by VARCHAR(150) NULL,
        history_id BIGINT UNSIGNED NULL,
        remark VARCHAR(1000) NULL,
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_quality_calibration_plans_no (plan_no),
        KEY idx_qcp_equipment (equipment_id, status),
        KEY idx_qcp_planned (planned_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS quality_events (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        entity_type VARCHAR(20) NOT NULL,
        entity_id BIGINT UNSIGNED NOT NULL,
        action VARCHAR(30) NOT NULL,
        from_status VARCHAR(30) NULL,
        to_status VARCHAR(30) NULL,
        actor VARCHAR(100) NULL,
        note VARCHAR(1000) NULL,
        event_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_qe_entity (entity_type, entity_id, event_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS quality_equipment_events (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        equipment_id BIGINT UNSIGNED NOT NULL,
        action VARCHAR(30) NOT NULL,
        note VARCHAR(1000) NULL,
        actor VARCHAR(100) NULL,
        event_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_qee_equipment (equipment_id, event_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

# (table, column, definition)
COLUMNS = [
    ("quality_standard_characteristics", "characteristic_type", "ENUM('VARIABLE','ATTRIBUTE') NOT NULL DEFAULT 'VARIABLE' AFTER inspection_frequency"),
    ("quality_standard_characteristics", "is_spc", "TINYINT(1) NOT NULL DEFAULT 0 AFTER characteristic_type"),
    ("quality_standard_characteristics", "chart_type", "ENUM('XBAR_R','IMR') NOT NULL DEFAULT 'XBAR_R' AFTER is_spc"),
    ("quality_standard_characteristics", "subgroup_size", "INT NOT NULL DEFAULT 5 AFTER chart_type"),
    ("quality_standard_characteristics", "cpk_target", "DECIMAL(6,3) NULL DEFAULT 1.330 AFTER subgroup_size"),
    ("quality_standard_characteristics", "is_critical", "TINYINT(1) NOT NULL DEFAULT 0 AFTER cpk_target"),
    # operational state kept apart from the master status so the admin master screen keeps working
    ("measuring_equipment", "quality_state", "ENUM('NORMAL','UNDER_REPAIR','IN_CALIBRATION','SCRAPPED') NOT NULL DEFAULT 'NORMAL' AFTER status"),
    ("measuring_equipment", "usage_location", "VARCHAR(100) NULL AFTER factory_node_id"),
    ("measuring_equipment", "owner_code", "VARCHAR(100) NULL AFTER usage_location"),
    ("measuring_equipment", "required_cpk", "DECIMAL(6,3) NULL AFTER accuracy"),
    ("measuring_equipment", "calibration_lab", "VARCHAR(150) NULL AFTER calibration_cycle_days"),
    ("measuring_equipment", "lock_reason", "VARCHAR(255) NULL AFTER quality_state"),
    ("measuring_equipment", "last_used_at", "DATETIME NULL AFTER lock_reason"),
    ("measuring_equipment_calibration_history", "plan_id", "BIGINT UNSIGNED NULL AFTER equipment_id"),
    ("measuring_equipment_calibration_history", "lab", "VARCHAR(150) NULL AFTER calibrated_by"),
    ("measuring_equipment_calibration_history", "certificate_url", "VARCHAR(1000) NULL AFTER certificate_no"),
]

SETTINGS = [
    ("quality.fpy_target", "98", "Quality: FPY target (%)"),
    ("quality.ppm_target", "2000", "Quality: PPM target"),
    ("quality.ng_rate_target", "2", "Quality: NG rate target (%)"),
    ("quality.wo_ng_attention", "2", "Quality: Work Order NG rate needing attention (%)"),
    ("quality.wo_ng_critical", "3", "Quality: Work Order NG rate considered critical (%)"),
    ("quality.equipment_control", "BLOCK", "Quality: invalid measuring equipment at inspection (BLOCK / WARN)"),
    ("quality.calibration_due_days", "7", "Quality: calibration due-soon window (days)"),
    ("quality.calibration_plan_days", "30", "Quality: create calibration plans this many days ahead"),
    ("quality.calibration_compliance_target", "95", "Quality: calibration compliance target (%)"),
    ("quality.repeat_window_days", "30", "Quality: repeat NG detection window (days)"),
    ("quality.ipqc_interval_min", "30", "Quality: IPQC frequency for running Work Orders (minutes)"),
    ("quality.ipqc_sample_size", "5", "Quality: IPQC sample size"),
    ("quality.auto_hold_critical", "0", "Quality: hold the Work Order automatically on a CRITICAL NG (1/0)"),
]

DEFECTS = [
    ("DEF-001", "Flash", "HIGH"), ("DEF-002", "Short Shot", "HIGH"), ("DEF-003", "Burr", "MEDIUM"),
    ("DEF-004", "Deformation", "MEDIUM"), ("DEF-005", "Color Defect", "LOW"), ("DEF-006", "Sink Mark", "MEDIUM"),
    ("DEF-007", "Silver Streak", "LOW"), ("DEF-008", "Black Spot", "LOW"), ("DEF-009", "Weld Line", "LOW"),
    ("DEF-010", "Out of Tolerance", "HIGH"), ("DEF-011", "Crack", "HIGH"), ("DEF-012", "Scratch", "LOW"),
    ("DEF-013", "Burn Mark", "MEDIUM"),
]


def _has_column(cur, table, column):
    cur.execute("""SELECT COUNT(*) FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=DATABASE() AND TABLE_NAME=%s AND COLUMN_NAME=%s""", (table, column))
    return cur.fetchone()[0] > 0


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        for statement in TABLES:
            cur.execute(statement)
        for table, column, definition in COLUMNS:
            if not _has_column(cur, table, column):
                cur.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        for key, value, desc in SETTINGS:
            cur.execute("SELECT COUNT(*) FROM system_settings WHERE setting_key=%s", (key,))
            if not cur.fetchone()[0]:
                cur.execute("SHOW COLUMNS FROM system_settings")
                cols = {r[0] for r in cur.fetchall()}
                if "description" in cols:
                    cur.execute("INSERT INTO system_settings (setting_key, setting_value, description) VALUES (%s,%s,%s)", (key, value, desc))
                else:
                    cur.execute("INSERT INTO system_settings (setting_key, setting_value) VALUES (%s,%s)", (key, value))
        # defect codes live in the Reason Code Master (category DEFECT, module NG_MANAGEMENT)
        cur.execute("SELECT id FROM reason_code_categories WHERE category_code='DEFECT'")
        row = cur.fetchone()
        if row:
            category_id = row[0]
        else:
            cur.execute("""INSERT INTO reason_code_categories (category_code, category_name, sort_order, status, created_by)
                           VALUES ('DEFECT','Quality Defect',0,'ACTIVE','SYSTEM')""")
            category_id = cur.lastrowid
        for code, name, impact in DEFECTS:
            cur.execute("SELECT COUNT(*) FROM reason_codes WHERE reason_code=%s", (code,))
            if not cur.fetchone()[0]:
                cur.execute("""INSERT INTO reason_codes (reason_code, reason_name, category_id, code_type, applicable_module, impact_level, status, created_by)
                               VALUES (%s,%s,%s,'MANUAL','NG_MANAGEMENT',%s,'ACTIVE','SYSTEM')""", (code, name, category_id, impact))
        conn.commit()
        print("Quality Management tables created / updated.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
