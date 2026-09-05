from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from database.db import get_vcc_plastics_connection
from services.vcc_group_service import (
    find_vcc_group_employee,
    list_vcc_group_employees,
)

router = APIRouter(prefix="/api/user-management", tags=["User Management"])


class EmployeeRolesReplace(BaseModel):
    role_ids: list[int] = Field(default_factory=list)
    is_active: bool = True
    updated_by: str | None = Field(default=None, max_length=50)


class UserStatusUpdate(BaseModel):
    is_active: bool
    updated_by: str | None = Field(default=None, max_length=50)


def _load_assignment_map() -> dict[str, dict]:
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT
                mu.id AS mes_user_id,
                mu.external_user_id,
                mu.employee_code,
                mu.is_active AS mes_is_active,
                mu.last_login_at,
                r.id AS role_id,
                r.role_code,
                r.role_name,
                r.is_active AS role_is_active
            FROM mes_users mu
            LEFT JOIN user_roles ur ON ur.user_id = mu.id
            LEFT JOIN roles r ON r.id = ur.role_id
            ORDER BY mu.employee_code, r.role_name
            """
        )
        result: dict[str, dict] = {}
        for row in cur.fetchall():
            key = str(row["employee_code"] or "").strip().casefold()
            assignment = result.setdefault(
                key,
                {
                    "mes_user_id": row["mes_user_id"],
                    "external_user_id": row["external_user_id"],
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


def _build_employee_row(employee: dict, assignment: dict | None) -> dict:
    return {
        "external_user_id": employee.get("id"),
        "employee_code": str(employee.get("employee_code") or "").strip(),
        "full_name": employee.get("full_name"),
        "position": employee.get("position"),
        "organization_unit_id": employee.get("organization_unit_id"),
        "organization_unit_name": employee.get("organization_unit_name"),
        "corporation": employee.get("corporation"),
        "company": employee.get("company"),
        "factory": employee.get("factory"),
        "division": employee.get("division"),
        "sub_division": employee.get("sub_division"),
        "section": employee.get("section"),
        "group_name": employee.get("group_name"),
        "employment_status": employee.get("employment_status") or "active",
        "mes_user_id": assignment["mes_user_id"] if assignment else None,
        "mes_is_active": assignment["mes_is_active"] if assignment else False,
        "last_login_at": assignment["last_login_at"] if assignment else None,
        "role_ids": assignment["role_ids"] if assignment else [],
        "roles": assignment["roles"] if assignment else [],
    }


@router.get("/employees")
def list_employee_role_assignments(
    keyword: str | None = Query(default=None, max_length=100),
    org_id: int | None = None,
    employment_status: str = Query(default="active", pattern="^(active|inactive|all)$"),
    assigned_only: bool = False,
):
    """Ghép hồ sơ nhân viên VCC Group với vai trò của VCC Plastics."""
    employees = list_vcc_group_employees(
        org_id=org_id,
        employment_status=employment_status,
    )
    assignments = _load_assignment_map()
    normalized_keyword = (keyword or "").strip().casefold()

    rows = []
    for employee in employees:
        employee_code = str(employee.get("employee_code") or "").strip()
        if not employee_code:
            continue
        assignment = assignments.get(employee_code.casefold())
        if assigned_only and assignment is None:
            continue
        if normalized_keyword:
            searchable = " ".join(
                str(employee.get(field) or "")
                for field in (
                    "employee_code",
                    "full_name",
                    "position",
                    "organization_unit_name",
                )
            ).casefold()
            if normalized_keyword not in searchable:
                continue
        rows.append(_build_employee_row(employee, assignment))

    return {"items": rows, "total": len(rows)}


@router.put("/employees/{employee_code}/roles")
def replace_employee_roles(employee_code: str, payload: EmployeeRolesReplace):
    """Thêm/cập nhật MES user và thay toàn bộ vai trò trong một transaction."""
    employee_code = employee_code.strip()
    if not employee_code:
        raise HTTPException(status_code=400, detail="Mã nhân viên không hợp lệ.")

    employee = find_vcc_group_employee(employee_code)
    if not employee:
        raise HTTPException(
            status_code=404,
            detail="Không tìm thấy nhân viên trong VCC Group.",
        )

    role_ids = sorted(set(payload.role_ids))
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
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
                    detail=f"Vai trò không hợp lệ hoặc đã ngừng hoạt động: {invalid_ids}",
                )

        external_user_id = employee.get("id")
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
                1 if payload.is_active else 0,
                payload.updated_by,
                payload.updated_by,
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
                    (mes_user_id, role_id, payload.updated_by)
                    for role_id in role_ids
                ],
            )

        conn.commit()
        return {
            "message": "Đã cập nhật vai trò cho nhân viên.",
            "mes_user_id": mes_user_id,
            "employee_code": employee_code,
            "is_active": payload.is_active,
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


@router.patch("/users/{user_id}/status")
def update_user_status(user_id: int, payload: UserStatusUpdate):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE mes_users
            SET is_active = %s, updated_by = %s
            WHERE id = %s
            """,
            (1 if payload.is_active else 0, payload.updated_by, user_id),
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
            "is_active": payload.is_active,
        }
    finally:
        cur.close()
        conn.close()
