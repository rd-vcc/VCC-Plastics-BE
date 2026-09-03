from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from database.db import get_vcc_plastics_connection

router = APIRouter(prefix="/api/users", tags=["MES Users"])


class MesUserCreate(BaseModel):
    external_user_id: int | None = None
    employee_code: str = Field(min_length=1, max_length=50)
    created_by: str | None = None


class MesUserUpdate(BaseModel):
    is_active: bool
    updated_by: str | None = None


class UserRolesReplace(BaseModel):
    role_ids: list[int]
    updated_by: str | None = None


@router.get("")
def list_mes_users():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT mu.*,
                   GROUP_CONCAT(DISTINCT r.role_code ORDER BY r.role_code SEPARATOR ',') AS role_codes
            FROM mes_users mu
            LEFT JOIN user_roles ur ON ur.user_id = mu.id
            LEFT JOIN roles r ON r.id = ur.role_id
            GROUP BY mu.id
            ORDER BY mu.id DESC
            """
        )
        rows = cur.fetchall()
        for row in rows:
            role_codes = row.pop("role_codes", None)
            row["roles"] = role_codes.split(",") if role_codes else []
        return rows
    finally:
        cur.close()
        conn.close()


@router.get("/{user_id}")
def get_mes_user(user_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM mes_users WHERE id = %s", (user_id,))
        user = cur.fetchone()
        if not user:
            raise HTTPException(status_code=404, detail="Khong tim thay MES user.")

        cur.execute(
            """
            SELECT r.id, r.role_code, r.role_name, r.description, r.is_active
            FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            WHERE ur.user_id = %s
            ORDER BY r.role_name
            """,
            (user_id,),
        )
        user["roles"] = cur.fetchall()
        return user
    finally:
        cur.close()
        conn.close()


@router.post("")
def create_mes_user(payload: MesUserCreate):
    employee_code = payload.employee_code.strip()
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO mes_users (external_user_id, employee_code, created_by)
            VALUES (%s, %s, %s)
            """,
            (payload.external_user_id, employee_code, payload.created_by),
        )
        conn.commit()
        return {"message": "MES user created", "id": cur.lastrowid}
    except Exception as exc:
        conn.rollback()
        if "Duplicate" in str(exc):
            raise HTTPException(
                status_code=409,
                detail="Nhan vien nay da duoc them vao VCC Plastics.",
            ) from exc
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cur.close()
        conn.close()


@router.put("/{user_id}")
def update_mes_user(user_id: int, payload: MesUserUpdate):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT id FROM mes_users WHERE id = %s", (user_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Khong tim thay MES user.")

        cur.execute(
            """
            UPDATE mes_users
            SET is_active = %s, updated_by = %s
            WHERE id = %s
            """,
            (1 if payload.is_active else 0, payload.updated_by, user_id),
        )
        conn.commit()
        return {"message": "MES user updated"}
    finally:
        cur.close()
        conn.close()


@router.get("/{user_id}/roles")
def get_user_roles(user_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM mes_users WHERE id = %s", (user_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Khong tim thay MES user.")

        cur.execute(
            """
            SELECT r.id, r.role_code, r.role_name, r.description, r.is_active
            FROM user_roles ur
            JOIN roles r ON r.id = ur.role_id
            WHERE ur.user_id = %s
            ORDER BY r.role_name
            """,
            (user_id,),
        )
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


@router.put("/{user_id}/roles")
def replace_user_roles(user_id: int, payload: UserRolesReplace):
    role_ids = sorted(set(payload.role_ids))
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM mes_users WHERE id = %s", (user_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Khong tim thay MES user.")

        if role_ids:
            placeholders = ",".join(["%s"] * len(role_ids))
            cur.execute(
                f"SELECT id FROM roles WHERE id IN ({placeholders}) AND is_active = 1",
                tuple(role_ids),
            )
            valid_ids = {row["id"] for row in cur.fetchall()}
            invalid_ids = [role_id for role_id in role_ids if role_id not in valid_ids]
            if invalid_ids:
                raise HTTPException(
                    status_code=400,
                    detail=f"Role khong hop le hoac da Inactive: {invalid_ids}",
                )

        cur.execute("DELETE FROM user_roles WHERE user_id = %s", (user_id,))
        for role_id in role_ids:
            cur.execute(
                """
                INSERT INTO user_roles (user_id, role_id, created_by)
                VALUES (%s, %s, %s)
                """,
                (user_id, role_id, payload.updated_by),
            )

        conn.commit()
        return {"message": "User roles updated", "role_ids": role_ids}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cur.close()
        conn.close()


@router.delete("/{user_id}/roles/{role_id}")
def remove_user_role(user_id: int, role_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "DELETE FROM user_roles WHERE user_id = %s AND role_id = %s",
            (user_id, role_id),
        )
        conn.commit()
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="User khong co role nay.")
        return {"message": "Role removed from user"}
    finally:
        cur.close()
        conn.close()
