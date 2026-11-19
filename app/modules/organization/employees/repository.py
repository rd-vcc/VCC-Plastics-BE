"""Raw data access for MES Users assignment map. Ported from ``routers/user_management.py``."""

from fastapi import HTTPException

from app.core.database import get_vcc_plastics_connection


def load_assignment_map() -> dict[str, dict]:
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        select = """
            SELECT
                mu.id AS mes_user_id,
                mu.external_user_id,
                mu.employee_code,
                mu.is_active AS mes_is_active,
                mu.last_login_at,
                {profile}
                r.id AS role_id,
                r.role_code,
                r.role_name,
                r.is_active AS role_is_active
            FROM mes_users mu
            LEFT JOIN user_roles ur ON ur.user_id = mu.id
            LEFT JOIN roles r ON r.id = ur.role_id
            ORDER BY mu.employee_code, r.role_name
            """
        try:
            cur.execute(select.format(profile="mu.full_name, mu.department, mu.position, mu.password_hash IS NOT NULL AS has_password,"))
        except Exception:  # local-account columns not created yet (scripts.add_local_accounts)
            cur.execute(select.format(profile="NULL AS full_name, NULL AS department, NULL AS position, 0 AS has_password,"))
        result: dict[str, dict] = {}
        for row in cur.fetchall():
            key = str(row["employee_code"] or "").strip().casefold()
            assignment = result.setdefault(
                key,
                {
                    "mes_user_id": row["mes_user_id"],
                    "external_user_id": row["external_user_id"],
                    "employee_code": str(row["employee_code"] or "").strip(),
                    "full_name": row["full_name"],
                    "department": row["department"],
                    "position": row["position"],
                    "has_password": bool(row["has_password"]),
                    "mes_is_active": bool(row["mes_is_active"]),
                    "last_login_at": row["last_login_at"],
                    "role_ids": [],
                    "roles": [],
                },
            )
            if row["role_id"] is not None:
                assignment["role_ids"].append(row["role_id"])
                assignment["roles"].append(
                    {
                        "id": row["role_id"],
                        "role_code": row["role_code"],
                        "role_name": row["role_name"],
                        "is_active": bool(row["role_is_active"]),
                    }
                )
        return result
    finally:
        cur.close()
        conn.close()


def validate_role_ids(cur, role_ids: list[int]):
    if not role_ids:
        return
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
            detail=f"Vai trò không hợp lệ hoặc đã ngừng hoạt động: {invalid_ids}",
        )


def replace_employee_roles(
    employee_code: str,
    external_user_id,
    role_ids: list[int],
    is_active: bool,
    updated_by: str | None,
):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        validate_role_ids(cur, role_ids)

        cur.execute(
            """
            INSERT INTO mes_users
                (external_user_id, employee_code, is_active, created_by, updated_by)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                external_user_id = VALUES(external_user_id),
                is_active = VALUES(is_active),
                updated_by = VALUES(updated_by)
            """,
            (
                external_user_id,
                employee_code,
                1 if is_active else 0,
                updated_by,
                updated_by,
            ),
        )
        cur.execute(
            "SELECT id FROM mes_users WHERE employee_code = %s LIMIT 1",
            (employee_code,),
        )
        mes_user = cur.fetchone()
        mes_user_id = mes_user["id"]

        cur.execute("DELETE FROM user_roles WHERE user_id = %s", (mes_user_id,))
        if role_ids:
            cur.executemany(
                """
                INSERT INTO user_roles (user_id, role_id, created_by)
                VALUES (%s, %s, %s)
                """,
                [
                    (mes_user_id, role_id, updated_by)
                    for role_id in role_ids
                ],
            )

        conn.commit()
        return {
            "message": "Đã cập nhật vai trò cho nhân viên.",
            "mes_user_id": mes_user_id,
            "employee_code": employee_code,
            "is_active": is_active,
            "role_ids": role_ids,
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


def update_user_status(user_id: int, is_active: bool, updated_by: str | None):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE mes_users
            SET is_active = %s, updated_by = %s
            WHERE id = %s
            """,
            (1 if is_active else 0, updated_by, user_id),
        )
        if cur.rowcount == 0:
            conn.rollback()
            raise HTTPException(
                status_code=404,
                detail="Không tìm thấy người dùng VCC Plastics.",
            )
        conn.commit()
        return {
            "message": "Đã cập nhật trạng thái người dùng.",
            "mes_user_id": user_id,
            "is_active": is_active,
        }
    finally:
        cur.close()
        conn.close()


def find_mes_user(employee_code: str) -> dict | None:
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id, external_user_id, employee_code FROM mes_users WHERE TRIM(employee_code) = %s LIMIT 1", (employee_code.strip(),))
        return cur.fetchone()
    finally:
        cur.close()
        conn.close()


def create_local_account(employee_code, full_name, department, position, password_hash, role_ids, is_active, created_by,
                         external_user_id=None):
    """New MES user that signs in with a VCC Plastics password (no VCC Group profile needed)."""
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM mes_users WHERE TRIM(employee_code) = %s", (employee_code,))
        if cur.fetchone():
            raise HTTPException(status_code=409, detail=f"Mã nhân viên {employee_code} đã có tài khoản.")
        validate_role_ids(cur, role_ids)
        cur.execute(
            """INSERT INTO mes_users (external_user_id, employee_code, full_name, department, position, password_hash,
                                      password_changed_at, is_active, created_by, updated_by)
               VALUES (%s, %s, %s, %s, %s, %s, NOW(), %s, %s, %s)""",
            (external_user_id, employee_code, full_name, department, position, password_hash, 1 if is_active else 0,
             created_by, created_by),
        )
        user_id = cur.lastrowid
        if role_ids:
            cur.executemany("INSERT INTO user_roles (user_id, role_id, created_by) VALUES (%s, %s, %s)",
                            [(user_id, role_id, created_by) for role_id in role_ids])
        conn.commit()
        return {"message": "Đã thêm nhân viên và tạo tài khoản.", "mes_user_id": user_id, "employee_code": employee_code}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cur.close()
        conn.close()


def set_password(employee_code: str, password_hash: str, updated_by: str | None):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE mes_users SET password_hash = %s, password_changed_at = NOW(), updated_by = %s WHERE TRIM(employee_code) = %s",
            (password_hash, updated_by, employee_code.strip()),
        )
        if cur.rowcount == 0:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Không tìm thấy người dùng VCC Plastics.")
        conn.commit()
        return {"message": "Đã đặt mật khẩu.", "employee_code": employee_code}
    finally:
        cur.close()
        conn.close()
