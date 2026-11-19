"""One-off script: create the in-app notification tables.

- notifications         one row per event (what happened, which record, where to open it)
- notification_targets  who receives it: a role (every user holding that role) or one user
- notification_reads    per-user read marks (a role notification is read separately by each person)

Run with:

    python -m scripts.create_notification_tables
"""

from app.core.database import get_vcc_plastics_connection

TABLES = [
    """
    CREATE TABLE IF NOT EXISTS notifications (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        event_code VARCHAR(40) NOT NULL,
        module VARCHAR(30) NOT NULL,
        severity ENUM('INFO','WARNING','CRITICAL') NOT NULL DEFAULT 'INFO',
        title VARCHAR(200) NOT NULL,
        message VARCHAR(500) NULL,
        ref_type VARCHAR(40) NULL,
        ref_id BIGINT NULL,
        ref_code VARCHAR(80) NULL,
        link VARCHAR(300) NULL,
        created_by VARCHAR(50) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        KEY idx_notifications_created (created_at),
        KEY idx_notifications_ref (ref_type, ref_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_targets (
        notification_id BIGINT UNSIGNED NOT NULL,
        target_type ENUM('ROLE','USER') NOT NULL,
        target_code VARCHAR(100) NOT NULL,
        PRIMARY KEY (notification_id, target_type, target_code),
        KEY idx_notification_targets_code (target_type, target_code),
        CONSTRAINT fk_notification_targets_notification FOREIGN KEY (notification_id)
            REFERENCES notifications (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS notification_reads (
        notification_id BIGINT UNSIGNED NOT NULL,
        employee_code VARCHAR(50) NOT NULL,
        read_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (notification_id, employee_code),
        KEY idx_notification_reads_user (employee_code),
        CONSTRAINT fk_notification_reads_notification FOREIGN KEY (notification_id)
            REFERENCES notifications (id) ON DELETE CASCADE
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        for ddl in TABLES:
            cur.execute(ddl)
        conn.commit()
        print("Notification tables ready.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
