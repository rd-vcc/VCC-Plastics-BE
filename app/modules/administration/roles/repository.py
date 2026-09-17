"""Raw data access for Roles and Role Permissions.

Ported from ``routers/roles.py`` and ``routers/role_permissions.py`` - both
originally mounted at the ``/api/roles`` prefix, merged here into a single
module since they share the same URL prefix and resource.
"""

from fastapi import HTTPException

from app.core.database import get_vcc_plastics_connection


def list_roles():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT r.*,
                   COUNT(DISTINCT ur.user_id) AS user_count,
                   COUNT(DISTINCT rp.permission_id) AS permission_count
            FROM roles r
            LEFT JOIN user_roles ur ON ur.role_id = r.id
            LEFT JOIN role_permissions rp ON rp.role_id = r.id
            GROUP BY r.id
            ORDER BY r.role_name
            """
        )
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def get_role(role_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM roles WHERE id = %s", (role_id,))
        role = cur.fetchone()
        if not role:
            raise HTTPException(status_code=404, detail="Khong tim thay role.")
        return role
    finally:
        cur.close()
        conn.close()


def create_role(role_code: str, role_name: str, description, is_active: bool, created_by):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO roles
                (role_code, role_name, description, is_active, created_by)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (
                role_code,
                role_name,
                description,
                1 if is_active else 0,
                created_by,
            ),
        )
        conn.commit()
        return {"message": "Role created", "id": cur.lastrowid}
    except Exception as exc:
        conn.rollback()
        if "Duplicate" in str(exc):
            raise HTTPException(status_code=409, detail="Role code da ton tai.") from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cur.close()
        conn.close()


def update_role(role_id: int, role_name: str, description, is_active: bool, updated_by):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM roles WHERE id = %s", (role_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Khong tim thay role.")

        cur.execute(
            """
            UPDATE roles
            SET role_name = %s,
                description = %s,
                is_active = %s,
                updated_by = %s
            WHERE id = %s
            """,
            (
                role_name,
                description,
                1 if is_active else 0,
                updated_by,
                role_id,
            ),
        )
        conn.commit()
        return {"message": "Role updated"}
    finally:
        cur.close()
        conn.close()


def delete_role(role_id: int):
    """Soft delete: role da co lich su se duoc chuyen Inactive thay vi xoa vat ly."""
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM roles WHERE id = %s", (role_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Khong tim thay role.")

        cur.execute("UPDATE roles SET is_active = 0 WHERE id = %s", (role_id,))
        conn.commit()
        return {"message": "Role deactivated"}
    finally:
        cur.close()
        conn.close()


def get_role_permissions(role_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM roles WHERE id = %s", (role_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Khong tim thay role.")

        cur.execute(
            """
            SELECT p.id, p.permission_code, p.permission_name,
                   p.module_code, p.description
            FROM role_permissions rp
            JOIN permissions p ON p.id = rp.permission_id
            WHERE rp.role_id = %s
            ORDER BY p.module_code, p.permission_code
            """,
            (role_id,),
        )
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


def replace_role_permissions(role_id: int, permission_ids: list[int], updated_by):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM roles WHERE id = %s AND is_active = 1", (role_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Khong tim thay role Active.")

        if permission_ids:
            placeholders = ",".join(["%s"] * len(permission_ids))
            cur.execute(
                f"SELECT id FROM permissions WHERE id IN ({placeholders}) AND is_active = 1",
                tuple(permission_ids),
            )
            valid_ids = {row["id"] for row in cur.fetchall()}
            invalid_ids = [pid for pid in permission_ids if pid not in valid_ids]
            if invalid_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"Permission khong hop le hoac da Inactive: {invalid_ids}",
                )

        cur.execute("DELETE FROM role_permissions WHERE role_id = %s", (role_id,))
        for permission_id in permission_ids:
            cur.execute(
                """
                INSERT INTO role_permissions (role_id, permission_id, created_by)
                VALUES (%s, %s, %s)
                """,
                (role_id, permission_id, updated_by),
            )

        conn.commit()
        return {
            "message": "Role permissions updated",
            "permission_ids": permission_ids,
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cur.close()
        conn.close()


def remove_role_permission(role_id: int, permission_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            DELETE FROM role_permissions
            WHERE role_id = %s AND permission_id = %s
            """,
            (role_id, permission_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Role khong co permission nay.")
        return {"message": "Permission removed from role"}
    finally:
        cur.close()
        conn.close()
