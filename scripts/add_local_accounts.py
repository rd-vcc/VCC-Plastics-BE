"""One-off script: local login accounts for VCC Plastics (no VCC Group needed).

- Adds profile + password columns to ``mes_users`` (full_name, department, position,
  password_hash, password_changed_at). Safe to run again: existing columns are skipped.
- Copies names / departments of existing users from the employee directory when it is reachable.
- Creates (or resets) one ADMIN account so the system can be entered on a server that
  cannot reach VCC Group. Default: employee code 00034, password 1.

Run with:

    python -m scripts.add_local_accounts
    python -m scripts.add_local_accounts --admin 00034 --password 1 --name "Hoàng Minh Thảo"
"""

import argparse

from app.core.database import get_vcc_plastics_connection
from app.core.passwords import hash_password

COLUMNS = [
    ("full_name", "VARCHAR(150) NULL AFTER employee_code"),
    ("department", "VARCHAR(150) NULL AFTER full_name"),
    ("position", "VARCHAR(150) NULL AFTER department"),
    ("password_hash", "VARCHAR(255) NULL AFTER position"),
    ("password_changed_at", "DATETIME NULL AFTER password_hash"),
]


def add_columns(cur):
    cur.execute("SHOW COLUMNS FROM mes_users")
    existing = {row[0] for row in cur.fetchall()}
    for name, ddl in COLUMNS:
        if name not in existing:
            cur.execute(f"ALTER TABLE mes_users ADD COLUMN {name} {ddl}")
            print(f"  + mes_users.{name}")


def copy_profiles(cur):
    """Fill empty names from the employee directory (nsh.employees2026_base) if it exists here."""
    try:
        cur.execute(
            """UPDATE mes_users mu
               JOIN nsh.employees2026_base e
                 ON TRIM(LEADING '0' FROM e.employee_code) COLLATE utf8mb4_unicode_ci = TRIM(LEADING '0' FROM mu.employee_code)
               SET mu.full_name = COALESCE(mu.full_name, TRIM(e.full_name)),
                   mu.position = COALESCE(mu.position, TRIM(e.position))
             WHERE mu.full_name IS NULL OR mu.position IS NULL"""
        )
        print(f"  profiles copied from employee directory: {cur.rowcount}")
    except Exception as exc:  # directory not on this server: names are typed in when accounts are created
        print(f"  employee directory not available ({exc.__class__.__name__}), skipped")


def ensure_admin(cur, code, password, name):
    cur.execute("SELECT id FROM roles WHERE role_code = 'ADMIN'")
    role = cur.fetchone()
    if not role:
        cur.execute("INSERT INTO roles (role_code, role_name, description, is_active, created_by) "
                    "VALUES ('ADMIN', 'ADMIN hệ thống', 'Toàn quyền hệ thống', 1, 'SYSTEM_SEED')")
        role_id = cur.lastrowid
        cur.execute("INSERT IGNORE INTO role_permissions (role_id, permission_id, created_by) "
                    "SELECT %s, id, 'SYSTEM_SEED' FROM permissions WHERE is_active = 1", (role_id,))
    else:
        role_id = role[0]

    cur.execute(
        """INSERT INTO mes_users (employee_code, full_name, password_hash, password_changed_at, is_active, created_by, updated_by)
           VALUES (%s, %s, %s, NOW(), 1, 'SYSTEM_SEED', 'SYSTEM_SEED')
           ON DUPLICATE KEY UPDATE password_hash = VALUES(password_hash), password_changed_at = NOW(),
                                   full_name = COALESCE(mes_users.full_name, VALUES(full_name)),
                                   is_active = 1, updated_by = 'SYSTEM_SEED'""",
        (code, name, hash_password(password)),
    )
    cur.execute("SELECT id FROM mes_users WHERE employee_code = %s", (code,))
    user_id = cur.fetchone()[0]
    cur.execute("INSERT IGNORE INTO user_roles (user_id, role_id, created_by) VALUES (%s, %s, 'SYSTEM_SEED')", (user_id, role_id))
    print(f"  admin account {code}: password set, ADMIN role assigned")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--admin", default="00034", help="employee code of the admin account (default 00034)")
    parser.add_argument("--password", default="1", help="password of the admin account (default 1)")
    parser.add_argument("--name", default=None, help="full name, used only when the account has no name yet")
    args = parser.parse_args()

    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        add_columns(cur)
        copy_profiles(cur)
        ensure_admin(cur, args.admin.strip(), args.password, args.name or f"Quản trị {args.admin.strip()}")
        conn.commit()
        print("Done.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
