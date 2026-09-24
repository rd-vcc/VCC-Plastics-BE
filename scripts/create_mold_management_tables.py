"""One-off script: Mold Management module (screens 3.1 - 3.7) data foundation.

    mold_installations        one row per mount of a mold on a machine (install -> remove), with Work Order,
                              who / how long it took, removal reason (planned / unplanned) and shots run
    mold_events               lifecycle log of a mold: status changes, location moves, install / remove,
                              maintenance / repair start + completion, lock, shot adjustments
    mold_shot_daily           shots per mold per day (Production Execution output, PLC / IoT later)
    mold_documents            drawings, BOM, cooling / ejection layout, 3D model... (uploaded in Mold Master)
    molds.*                   revision, PM interval (shots / days), last PM, current machine, status / location since,
                              lock reason
    mold_location_node_types.location_category   STORAGE / MAINTENANCE / REPAIR / SCRAP (map areas of 3.7)
    mold_location_nodes.capacity                 storage capacity (Storage Capacity of 3.7)

Status LOCKED is added to the mold status list (quality hold / special request).

Run with:

    python -m scripts.create_mold_management_tables
"""

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS mold_installations (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        mold_id BIGINT UNSIGNED NOT NULL,
        machine_id INT UNSIGNED NOT NULL,
        work_order_id BIGINT UNSIGNED NULL,
        installed_at DATETIME NOT NULL,
        installed_by VARCHAR(100) NULL,
        install_minutes DECIMAL(8,1) NULL,
        shots_at_install BIGINT UNSIGNED NULL,
        removed_at DATETIME NULL,
        removed_by VARCHAR(100) NULL,
        remove_minutes DECIMAL(8,1) NULL,
        shots_at_remove BIGINT UNSIGNED NULL,
        removal_reason VARCHAR(30) NULL,
        planned TINYINT(1) NULL,
        remark VARCHAR(500) NULL,
        source VARCHAR(20) NOT NULL DEFAULT 'MANUAL',
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_mi_mold (mold_id, installed_at),
        KEY idx_mi_machine (machine_id, installed_at),
        KEY idx_mi_open (removed_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS mold_events (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        mold_id BIGINT UNSIGNED NOT NULL,
        event_type VARCHAR(30) NOT NULL,
        from_status VARCHAR(30) NULL,
        to_status VARCHAR(30) NULL,
        from_location_id BIGINT UNSIGNED NULL,
        to_location_id BIGINT UNSIGNED NULL,
        machine_id INT UNSIGNED NULL,
        work_order_id BIGINT UNSIGNED NULL,
        maintenance_type VARCHAR(30) NULL,
        technician VARCHAR(100) NULL,
        downtime_hours DECIMAL(8,2) NULL,
        shot_before BIGINT UNSIGNED NULL,
        shot_after BIGINT UNSIGNED NULL,
        reason VARCHAR(1000) NULL,
        actor VARCHAR(100) NULL,
        event_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_me_mold (mold_id, event_at),
        KEY idx_me_time (event_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS mold_shot_daily (
        mold_id BIGINT UNSIGNED NOT NULL,
        shot_date DATE NOT NULL,
        shots INT UNSIGNED NOT NULL DEFAULT 0,
        source VARCHAR(20) NOT NULL DEFAULT 'MES',
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (mold_id, shot_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS mold_documents (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        mold_id BIGINT UNSIGNED NOT NULL,
        doc_type VARCHAR(30) NOT NULL,
        title VARCHAR(255) NOT NULL,
        file_url VARCHAR(1000) NOT NULL,
        file_name VARCHAR(255) NULL,
        file_size BIGINT UNSIGNED NULL,
        uploaded_by VARCHAR(100) NULL,
        uploaded_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_md_mold (mold_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

COLUMNS = {
    "molds": {
        "revision": "VARCHAR(30) NULL",
        "pm_interval_shot": "INT UNSIGNED NULL",
        "pm_interval_days": "INT UNSIGNED NULL",
        "last_pm_at": "DATETIME NULL",
        "last_pm_shot": "BIGINT UNSIGNED NULL",
        "current_machine_id": "INT UNSIGNED NULL",
        "status_since": "DATETIME NULL",
        "location_since": "DATETIME NULL",
        "lock_reason": "VARCHAR(500) NULL",
    },
    "mold_location_node_types": {"location_category": "VARCHAR(20) NOT NULL DEFAULT 'STORAGE'"},
    "mold_location_nodes": {"capacity": "INT UNSIGNED NULL"},
}


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        for statement in STATEMENTS:
            cur.execute(statement)
        for table, cols in COLUMNS.items():
            cur.execute(f"SHOW COLUMNS FROM {table}")
            existing = {row[0] for row in cur.fetchall()}
            for name, ddl in cols.items():
                if name not in existing:
                    cur.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
        cur.execute("""SELECT CHECK_CLAUSE FROM information_schema.CHECK_CONSTRAINTS
                       WHERE CONSTRAINT_SCHEMA=DATABASE() AND CONSTRAINT_NAME='chk_molds_status'""")
        row = cur.fetchone()
        if row and "LOCKED" not in row[0]:
            cur.execute("ALTER TABLE molds DROP CHECK chk_molds_status")
            cur.execute("""ALTER TABLE molds ADD CONSTRAINT chk_molds_status CHECK
                           (status IN ('AVAILABLE','IN_PRODUCTION','IN_MAINTENANCE','IN_REPAIR','LOCKED','RETIRED','SCRAPPED'))""")
        cur.execute("UPDATE molds SET status_since=COALESCE(status_since, updated_at, created_at), location_since=COALESCE(location_since, updated_at, created_at)")
        conn.commit()
        print("Mold management tables created.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
