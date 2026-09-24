"""One-off script: Maintenance Management module (screens 6.1 - 6.7) data foundation.

    maintenance_requests        service desk: NEW -> IN_REVIEW -> APPROVED / REJECTED -> COMPLETED (when its WO completes), CANCELLED
    maintenance_plans           recurring PM / inspection / calibration per asset (days or shots); AUTO plans are synced from
                                Mold Master PM intervals and Production Tool calibration settings
    maintenance_work_orders     execution: RELEASED -> ASSIGNED -> IN_PROGRESS <-> PAUSED -> VERIFICATION -> COMPLETED -> CLOSED, CANCELLED
    maintenance_wo_checklist    checklist items of a WO (copied from the plan)
    maintenance_wo_parts        spare parts used (free lines until a spare parts master exists) -> cost
    maintenance_wo_labor        labor lines (technician, type, hours, rate) -> cost
    maintenance_wo_components   mold component inspection (core, cavity, insert, slide, lifter, ejector...)
    maintenance_events          history / timeline of requests and WOs
    maintenance_attachments     photos (before / after) and files of requests and WOs

Asset = (asset_type, asset_id): MACHINE / EQUIPMENT -> equipment.id, MOLD -> molds.id, TOOL -> production_tools.id.

Run with:

    python -m scripts.create_maintenance_tables
"""

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS maintenance_requests (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        request_no VARCHAR(30) NOT NULL,
        asset_type VARCHAR(20) NOT NULL,
        asset_id BIGINT UNSIGNED NOT NULL,
        request_type VARCHAR(20) NOT NULL DEFAULT 'CORRECTIVE',
        priority VARCHAR(10) NOT NULL DEFAULT 'MEDIUM',
        status VARCHAR(20) NOT NULL DEFAULT 'NEW',
        source VARCHAR(20) NOT NULL DEFAULT 'MANUAL',
        short_description VARCHAR(255) NOT NULL,
        description TEXT NULL,
        requested_by VARCHAR(100) NULL,
        department VARCHAR(100) NULL,
        required_date DATETIME NULL,
        alarm_id BIGINT UNSIGNED NULL,
        downtime_event_id BIGINT UNSIGNED NULL,
        production_wo_id BIGINT UNSIGNED NULL,
        work_order_id BIGINT UNSIGNED NULL,
        reviewed_by VARCHAR(100) NULL,
        approved_by VARCHAR(100) NULL,
        approved_at DATETIME NULL,
        reject_reason VARCHAR(500) NULL,
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_by VARCHAR(100) NULL,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uq_mr_no (request_no),
        KEY idx_mr_asset (asset_type, asset_id),
        KEY idx_mr_status (status, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS maintenance_plans (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        plan_no VARCHAR(30) NOT NULL,
        asset_type VARCHAR(20) NOT NULL,
        asset_id BIGINT UNSIGNED NOT NULL,
        task_name VARCHAR(255) NOT NULL,
        wo_type VARCHAR(20) NOT NULL DEFAULT 'PM',
        plan_type VARCHAR(20) NOT NULL DEFAULT 'MONTHLY',
        frequency_unit VARCHAR(10) NOT NULL DEFAULT 'DAY',
        interval_value INT UNSIGNED NOT NULL DEFAULT 30,
        next_due_date DATE NULL,
        next_due_shot BIGINT UNSIGNED NULL,
        last_done_at DATETIME NULL,
        priority VARCHAR(10) NOT NULL DEFAULT 'MEDIUM',
        technician VARCHAR(100) NULL,
        est_minutes INT UNSIGNED NULL,
        description TEXT NULL,
        checklist JSON NULL,
        spare_parts JSON NULL,
        status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
        source VARCHAR(20) NOT NULL DEFAULT 'MANUAL',
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_by VARCHAR(100) NULL,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uq_mp_no (plan_no),
        KEY idx_mp_asset (asset_type, asset_id),
        KEY idx_mp_due (status, next_due_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS maintenance_work_orders (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        wo_no VARCHAR(30) NOT NULL,
        asset_type VARCHAR(20) NOT NULL,
        asset_id BIGINT UNSIGNED NOT NULL,
        wo_type VARCHAR(20) NOT NULL DEFAULT 'CORRECTIVE',
        priority VARCHAR(10) NOT NULL DEFAULT 'MEDIUM',
        status VARCHAR(20) NOT NULL DEFAULT 'RELEASED',
        stage VARCHAR(30) NULL,
        task_name VARCHAR(255) NOT NULL,
        description TEXT NULL,
        plan_id BIGINT UNSIGNED NULL,
        request_id BIGINT UNSIGNED NULL,
        technician VARCHAR(100) NULL,
        assistant VARCHAR(100) NULL,
        requested_by VARCHAR(100) NULL,
        planned_start DATETIME NULL,
        planned_finish DATETIME NULL,
        due_date DATE NULL,
        est_minutes INT UNSIGNED NULL,
        est_downtime_minutes INT UNSIGNED NULL,
        actual_start DATETIME NULL,
        actual_finish DATETIME NULL,
        paused_at DATETIME NULL,
        paused_minutes DECIMAL(10,1) NOT NULL DEFAULT 0,
        downtime_event_id BIGINT UNSIGNED NULL,
        downtime_minutes DECIMAL(10,1) NULL,
        outsourcing_cost DECIMAL(14,2) NOT NULL DEFAULT 0,
        tool_cost DECIMAL(14,2) NOT NULL DEFAULT 0,
        other_cost DECIMAL(14,2) NOT NULL DEFAULT 0,
        result VARCHAR(1000) NULL,
        root_cause VARCHAR(1000) NULL,
        calibration_result VARCHAR(10) NULL,
        calibration_value VARCHAR(255) NULL,
        mold_shot_at_start BIGINT UNSIGNED NULL,
        verified_by VARCHAR(100) NULL,
        verified_at DATETIME NULL,
        completed_at DATETIME NULL,
        closed_by VARCHAR(100) NULL,
        closed_at DATETIME NULL,
        cancel_reason VARCHAR(500) NULL,
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_by VARCHAR(100) NULL,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uq_mwo_no (wo_no),
        KEY idx_mwo_asset (asset_type, asset_id),
        KEY idx_mwo_status (status, planned_finish),
        KEY idx_mwo_plan (plan_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS maintenance_wo_checklist (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        wo_id BIGINT UNSIGNED NOT NULL,
        seq INT UNSIGNED NOT NULL DEFAULT 1,
        item VARCHAR(500) NOT NULL,
        result VARCHAR(10) NULL,
        note VARCHAR(500) NULL,
        done_by VARCHAR(100) NULL,
        done_at DATETIME NULL,
        PRIMARY KEY (id),
        KEY idx_mwc_wo (wo_id, seq)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS maintenance_wo_parts (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        wo_id BIGINT UNSIGNED NOT NULL,
        part_code VARCHAR(100) NULL,
        part_name VARCHAR(255) NOT NULL,
        qty DECIMAL(12,3) NOT NULL DEFAULT 1,
        unit VARCHAR(20) NULL,
        unit_cost DECIMAL(14,2) NOT NULL DEFAULT 0,
        created_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_mwp_wo (wo_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS maintenance_wo_labor (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        wo_id BIGINT UNSIGNED NOT NULL,
        technician VARCHAR(100) NOT NULL,
        labor_type VARCHAR(30) NOT NULL DEFAULT 'REPAIR',
        hours DECIMAL(8,2) NOT NULL DEFAULT 0,
        rate DECIMAL(14,2) NOT NULL DEFAULT 0,
        created_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_mwl_wo (wo_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS maintenance_wo_components (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        wo_id BIGINT UNSIGNED NOT NULL,
        component VARCHAR(40) NOT NULL,
        condition_code VARCHAR(20) NOT NULL DEFAULT 'GOOD',
        action_code VARCHAR(20) NOT NULL DEFAULT 'NONE',
        note VARCHAR(500) NULL,
        updated_by VARCHAR(100) NULL,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uq_mwcomp (wo_id, component)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS maintenance_events (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        owner_type VARCHAR(10) NOT NULL,
        owner_id BIGINT UNSIGNED NOT NULL,
        action VARCHAR(30) NOT NULL,
        from_status VARCHAR(20) NULL,
        to_status VARCHAR(20) NULL,
        note VARCHAR(1000) NULL,
        actor VARCHAR(100) NULL,
        event_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_me_owner (owner_type, owner_id, event_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS maintenance_attachments (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        owner_type VARCHAR(10) NOT NULL,
        owner_id BIGINT UNSIGNED NOT NULL,
        kind VARCHAR(20) NOT NULL DEFAULT 'FILE',
        file_url VARCHAR(1000) NOT NULL,
        file_name VARCHAR(255) NULL,
        file_size BIGINT UNSIGNED NULL,
        uploaded_by VARCHAR(100) NULL,
        uploaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_ma_owner (owner_type, owner_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

SETTINGS = {"maintenance.labor_rate_per_hour": "120000", "maintenance.currency": "VND", "maintenance.auto_request_critical_alarm": "1"}


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        for statement in STATEMENTS:
            cur.execute(statement)
        for key, value in SETTINGS.items():
            cur.execute("SELECT 1 FROM system_settings WHERE setting_key=%s", (key,))
            if not cur.fetchone():
                cur.execute("INSERT INTO system_settings (setting_key, setting_value) VALUES (%s,%s)", (key, value))
        conn.commit()
        print("Maintenance tables created.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
