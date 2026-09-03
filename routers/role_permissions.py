from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from database.db import get_vcc_plastics_connection

router = APIRouter(prefix="/api/roles", tags=["Role Permissions"])


class RolePermissionsReplace(BaseModel):
    permission_ids: list[int]
    updated_by: str | None = None


@router.get("/{role_id}/permissions")
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


@router.put("/{role_id}/permissions")
def replace_role_permissions(role_id: int, payload: RolePermissionsReplace):
    permission_ids = sorted(set(payload.permission_ids))
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
                (role_id, permission_id, payload.updated_by),
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


@router.delete("/{role_id}/permissions/{permission_id}")
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
