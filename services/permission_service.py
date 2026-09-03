from fastapi import HTTPException
from database.db import get_vcc_plastics_connection


def get_mes_access(employee_code: str) -> dict:
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT id, employee_code, is_active
            FROM mes_users
            WHERE employee_code = %s
            LIMIT 1
            """,
            (employee_code,),
        )
        mes_user = cur.fetchone()

        if not mes_user:
            raise HTTPException(
                status_code=403,
                detail="Bạn chưa được cấp quyền sử dụng VCC Plastics.",
            )

        if not mes_user["is_active"]:
            raise HTTPException(
                status_code=403,
                detail="Tài khoản VCC Plastics của bạn đang bị khóa.",
            )

        user_id = mes_user["id"]

        cur.execute(
            """
            SELECT DISTINCT r.role_code
            FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            WHERE ur.user_id = %s
              AND r.is_active = 1
            ORDER BY r.role_code
            """,
            (user_id,),
        )
        roles = [row["role_code"] for row in cur.fetchall()]

        cur.execute(
            """
            SELECT DISTINCT p.permission_code
            FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id AND r.is_active = 1
            JOIN role_permissions rp ON rp.role_id = r.id
            JOIN permissions p ON p.id = rp.permission_id AND p.is_active = 1
            WHERE ur.user_id = %s
            ORDER BY p.permission_code
            """,
            (user_id,),
        )
        permissions = [row["permission_code"] for row in cur.fetchall()]

        cur.execute(
            """
            UPDATE mes_users
            SET last_login_at = NOW()
            WHERE id = %s
            """,
            (user_id,),
        )
        conn.commit()

        return {
            "enabled": True,
            "roles": roles,
            "permissions": permissions,
        }
    finally:
        cur.close()
        conn.close()
