"""One-off script: create the Report Center tables (8.1).

- report_favorites: favorite reports per user
- report_saved: saved (custom) reports = a standard report + its filters under a new name
- report_schedules: reports generated automatically (daily / weekly / monthly)
- report_runs: history of every preview / export / scheduled generation (file, size, rows, duration)

Run with:

    python -m scripts.create_report_tables
"""

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS report_favorites (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        report_code VARCHAR(60) NOT NULL,
        user_code VARCHAR(100) NOT NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_report_favorites (report_code, user_code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS report_saved (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        code VARCHAR(60) NOT NULL,
        base_code VARCHAR(60) NOT NULL,
        name VARCHAR(200) NOT NULL,
        description VARCHAR(500) NULL,
        range_type VARCHAR(20) NOT NULL DEFAULT 'LAST_7',
        owner VARCHAR(100) NULL,
        is_shared TINYINT(1) NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_report_saved_code (code)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS report_schedules (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        name VARCHAR(200) NOT NULL,
        report_code VARCHAR(60) NOT NULL,
        frequency ENUM('DAILY','WEEKLY','MONTHLY') NOT NULL DEFAULT 'DAILY',
        run_time TIME NOT NULL DEFAULT '08:00:00',
        weekday TINYINT NULL,
        month_day TINYINT NULL,
        range_type VARCHAR(20) NOT NULL DEFAULT 'YESTERDAY',
        output_format ENUM('XLSX','CSV') NOT NULL DEFAULT 'XLSX',
        language VARCHAR(5) NOT NULL DEFAULT 'vi',
        recipients VARCHAR(1000) NULL,
        status ENUM('ACTIVE','PAUSED') NOT NULL DEFAULT 'ACTIVE',
        last_run_at DATETIME NULL,
        last_status VARCHAR(20) NULL,
        next_run_at DATETIME NULL,
        created_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_report_schedules_next (status, next_run_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS report_runs (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        report_code VARCHAR(60) NOT NULL,
        report_name VARCHAR(200) NULL,
        module VARCHAR(30) NULL,
        action ENUM('PREVIEW','EXPORT','PRINT','SCHEDULE') NOT NULL,
        output_format VARCHAR(10) NULL,
        date_from DATE NULL,
        date_to DATE NULL,
        row_count INT NOT NULL DEFAULT 0,
        file_url VARCHAR(500) NULL,
        file_size BIGINT NOT NULL DEFAULT 0,
        duration_ms INT NOT NULL DEFAULT 0,
        status ENUM('SUCCESS','FAILED') NOT NULL DEFAULT 'SUCCESS',
        message VARCHAR(500) NULL,
        schedule_id BIGINT UNSIGNED NULL,
        actor VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_report_runs_time (created_at),
        KEY idx_report_runs_code (report_code, created_at)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        for s in STATEMENTS:
            cur.execute(s)
        conn.commit()
        print("Report Center tables created (or already existed).")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
