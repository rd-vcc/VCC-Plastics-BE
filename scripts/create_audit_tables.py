"""One-off script: create the Audit Log table (9.3) and import the existing module histories.

audit_logs is append-only (no update / delete endpoint). Rows come from:
- the audit middleware: every data-changing API call (+ exports), login / logout / failed login
- this import: the history tables the modules already kept (status changes with from / to), source='MODULE'

Run with:

    python -m scripts.create_audit_tables
"""

from app.core.database import get_vcc_plastics_connection

TABLE = """
CREATE TABLE IF NOT EXISTS audit_logs (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    event_at DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),
    user_code VARCHAR(100) NULL,
    user_name VARCHAR(200) NULL,
    role_name VARCHAR(200) NULL,
    module VARCHAR(40) NOT NULL,
    action VARCHAR(40) NOT NULL,
    data_type VARCHAR(80) NULL,
    record_id VARCHAR(100) NULL,
    description VARCHAR(500) NULL,
    method VARCHAR(10) NULL,
    path VARCHAR(500) NULL,
    ip_address VARCHAR(64) NULL,
    user_agent VARCHAR(300) NULL,
    session_id VARCHAR(64) NULL,
    result ENUM('SUCCESS','FAILED') NOT NULL DEFAULT 'SUCCESS',
    status_code SMALLINT NULL,
    severity ENUM('LOW','MEDIUM','HIGH','CRITICAL') NOT NULL DEFAULT 'LOW',
    message VARCHAR(500) NULL,
    before_json MEDIUMTEXT NULL,
    after_json MEDIUMTEXT NULL,
    request_json MEDIUMTEXT NULL,
    duration_ms INT NULL,
    source ENUM('API','AUTH','MODULE','SYSTEM') NOT NULL DEFAULT 'API',
    source_ref VARCHAR(80) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uk_audit_logs_source_ref (source_ref),
    KEY idx_audit_logs_time (event_at),
    KEY idx_audit_logs_user (user_code, event_at),
    KEY idx_audit_logs_module (module, event_at),
    KEY idx_audit_logs_record (record_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
"""

SETTINGS = [
    ("audit.enabled", "1", "Audit Log: record data-changing API calls (1/0)"),
    ("audit.capture_changes", "1", "Audit Log: store before / after values of updates (1/0)"),
    ("audit.log_exports", "1", "Audit Log: record exports and downloads (1/0)"),
    ("audit.retention_days", "730", "Audit Log: keep records this many days"),
]

# (source table, module, data type, id column, action column, time column, note column, extra id / entity column)
IMPORTS = [
    ("production_planning_audit_log", "PRODUCTION", "entity_type", "entity_id", "action", "acted_at", "remark"),
    ("production_order_status_history", "PRODUCTION", "'PRODUCTION_ORDER'", "production_order_id", "action", "acted_at", "remark"),
    ("quality_standard_audit_log", "SYSTEM_CONFIGURATION", "'QUALITY_STANDARD'", "standard_id", "action", "acted_at", "remark"),
    ("reason_code_audit_log", "SYSTEM_CONFIGURATION", "'REASON_CODE'", "reason_code_id", "action", "acted_at", "remark"),
    ("bom_audit_log", "SYSTEM_CONFIGURATION", "'BOM'", "bom_id", "action", "acted_at", "remark"),
    ("shift_calendar_audit_log", "SYSTEM_CONFIGURATION", "entity_type", "entity_id", "action", "acted_at", "remark"),
    ("process_parameter_standard_audit_log", "SYSTEM_CONFIGURATION", "'PROCESS_PARAMETER_STANDARD'", "standard_id", "action", "acted_at", "remark"),
    ("maintenance_events", "MAINTENANCE", "owner_type", "owner_id", "action", "event_at", "note"),
    ("quality_ng_events", "QUALITY", "'NG_RECORD'", "ng_id", "action", "event_at", "note"),
    ("quality_events", "QUALITY", "entity_type", "entity_id", "action", "event_at", "note"),
    ("quality_equipment_events", "QUALITY", "'MEASURING_EQUIPMENT'", "equipment_id", "action", "event_at", "note"),
    ("mold_events", "MOLD", "'MOLD'", "mold_id", "event_type", "event_at", "reason"),
    ("machine_alarm_actions", "MACHINE", "'ALARM'", "alarm_id", "action", "acted_at", "note"),
]

SEVERITY = {"DELETE": "HIGH", "REJECT": "MEDIUM", "CANCEL": "MEDIUM", "HOLD": "MEDIUM", "REOPEN": "MEDIUM", "APPROVE": "MEDIUM", "OBSOLETE": "MEDIUM", "SCRAP": "HIGH"}


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        cur.execute(TABLE)
        cur.execute("SHOW COLUMNS FROM system_settings")
        has_desc = "description" in {r[0] for r in cur.fetchall()}
        for key, value, desc in SETTINGS:
            cur.execute("SELECT COUNT(*) FROM system_settings WHERE setting_key=%s", (key,))
            if not cur.fetchone()[0]:
                if has_desc:
                    cur.execute("INSERT INTO system_settings (setting_key, setting_value, description) VALUES (%s,%s,%s)", (key, value, desc))
                else:
                    cur.execute("INSERT INTO system_settings (setting_key, setting_value) VALUES (%s,%s)", (key, value))
        total = 0
        for table, module, dtype, idcol, actcol, timecol, notecol in IMPORTS:
            cur.execute(f"SHOW COLUMNS FROM {table}")
            cols = {r[0] for r in cur.fetchall()}
            fs = "from_status" if "from_status" in cols else "NULL"
            ts = "to_status" if "to_status" in cols else "NULL"
            cur.execute(f"SELECT id, {dtype}, {idcol}, {actcol}, {fs}, {ts}, actor, {timecol}, {notecol} FROM {table}")
            for rid, data_type, entity_id, action, from_s, to_s, actor, at, note in cur.fetchall():
                act = str(action or "EVENT").upper()[:40]
                before = f'{{"status": "{from_s}"}}' if from_s else None
                after = f'{{"status": "{to_s}"}}' if to_s else None
                cur.execute("""INSERT IGNORE INTO audit_logs (event_at, user_code, user_name, module, action, data_type, record_id, description, result, severity,
                               before_json, after_json, source, source_ref) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'SUCCESS',%s,%s,%s,'MODULE',%s)""",
                            (at, actor, actor, module, act, str(data_type or "")[:80].upper(), str(entity_id), (note or None) and str(note)[:500],
                             SEVERITY.get(act, "LOW"), before, after, f"{table}:{rid}"))
                total += cur.rowcount
        conn.commit()
        print(f"Audit Log table ready; {total} module history rows imported.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
