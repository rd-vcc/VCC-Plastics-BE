from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from database.db import get_vcc_plastics_connection

router = APIRouter(prefix="/api/roles", tags=["Roles"])


class RoleCreate(BaseModel):
    role_code: str = Field(min_length=1, max_length=100)
    role_name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = True
    created_by: str | None = None


class RoleUpdate(BaseModel):
    role_name: str = Field(min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=500)
    is_active: bool = True
    updated_by: str | None = None


@router.get("")
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


@router.get("/{role_id}")
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


@router.post("")
def create_role(payload: RoleCreate):
    role_code = payload.role_code.strip().upper()
    role_name = payload.role_name.strip()

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
                payload.description,
                1 if payload.is_active else 0,
                payload.created_by,
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


@router.put("/{role_id}")
def update_role(role_id: int, payload: RoleUpdate):
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
                payload.role_name.strip(),
                payload.description,
                1 if payload.is_active else 0,
                payload.updated_by,
                role_id,
            ),
        )
        conn.commit()
        return {"message": "Role updated"}
    finally:
        cur.close()
        conn.close()


@router.delete("/{role_id}")
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
