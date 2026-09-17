"""Business logic combining the VCC Group employee directory with MES role
assignment. Ported from ``routers/user_management.py``.
"""

from fastapi import HTTPException

from app.integrations.vcc_group.service import (
    find_vcc_group_employee,
    list_vcc_group_employees,
)
from app.modules.organization.employees import repository


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


def list_employee_role_assignments(
    keyword: str | None,
    org_id: int | None,
    employment_status: str,
    assigned_only: bool,
):
    """Ghép hồ sơ nhân viên VCC Group với vai trò của VCC Plastics."""
    employees = list_vcc_group_employees(
        org_id=org_id,
        employment_status=employment_status,
    )
    assignments = repository.load_assignment_map()
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


def replace_employee_roles(employee_code: str, role_ids: list[int], is_active: bool, updated_by: str | None):
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

    role_ids = sorted(set(role_ids))
    return repository.replace_employee_roles(
        employee_code, employee.get("id"), role_ids, is_active, updated_by
    )


def update_user_status(user_id: int, is_active: bool, updated_by: str | None):
    return repository.update_user_status(user_id, is_active, updated_by)
