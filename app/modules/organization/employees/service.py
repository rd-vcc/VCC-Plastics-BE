"""Business logic combining the VCC Group employee directory with MES role
assignment. Ported from ``routers/user_management.py``.
"""

import logging

from fastapi import HTTPException

from app.core.passwords import hash_password
from app.integrations.vcc_group.service import (
    find_vcc_group_employee,
    list_vcc_group_employees,
)
from app.modules.organization.employees import repository

log = logging.getLogger(__name__)

# Password of a newly added employee until it is changed with "Đổi mật khẩu".
DEFAULT_PASSWORD = "1"


def _build_employee_row(employee: dict, assignment: dict | None) -> dict:
    return {
        "external_user_id": employee.get("id"),
        "employee_code": str(employee.get("employee_code") or "").strip(),
        "full_name": employee.get("full_name") or (assignment or {}).get("full_name"),
        "position": employee.get("position") or (assignment or {}).get("position"),
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
        "has_password": bool(assignment and assignment.get("has_password")),
        "auth_source": "LOCAL" if assignment and assignment.get("has_password") else "VCC_GROUP",
    }


def _local_employee(assignment: dict) -> dict:
    """Employee-like profile of a MES user that is not in the VCC Group directory (local account)."""
    return {
        "employee_code": assignment["employee_code"],
        "full_name": assignment.get("full_name"),
        "position": assignment.get("position"),
        "organization_unit_name": assignment.get("department"),
        "employment_status": "active",
    }


def list_employee_role_assignments(
    keyword: str | None,
    org_id: int | None,
    employment_status: str,
    assigned_only: bool,
):
    """Ghép hồ sơ nhân viên VCC Group với vai trò của VCC Plastics."""
    assignments = repository.load_assignment_map()
    try:
        employees = list_vcc_group_employees(
            org_id=org_id,
            employment_status=employment_status,
        )
    except HTTPException as exc:  # VCC Group unreachable (e.g. on the plant server): local accounts only
        log.warning("VCC Group employee list unavailable: %s", exc.detail)
        employees = []
    directory_codes = {str(e.get("employee_code") or "").strip().casefold() for e in employees}
    employees = employees + [
        _local_employee(a) for key, a in assignments.items() if key and key not in directory_codes
    ]
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

    existing = repository.find_mes_user(employee_code)
    if existing:  # already a MES user (local or VCC Group): no directory lookup needed
        external_user_id = existing["external_user_id"]
    else:
        employee = find_vcc_group_employee(employee_code)
        if not employee:
            raise HTTPException(
                status_code=404,
                detail="Không tìm thấy nhân viên trong VCC Group.",
            )
        external_user_id = employee.get("id")

    role_ids = sorted(set(role_ids))
    return repository.replace_employee_roles(
        employee_code, external_user_id, role_ids, is_active, updated_by
    )


def _clean(value):
    value = (value or "").strip()
    return value or None


def create_local_account(payload):
    """Account that signs in with a VCC Plastics password, independent of VCC Group."""
    code = payload.employee_code.strip()
    if not code or not payload.full_name.strip():
        raise HTTPException(status_code=400, detail="Vui lòng nhập mã nhân viên và họ tên.")
    return repository.create_local_account(
        code, payload.full_name.strip(), _clean(payload.department), _clean(payload.position),
        hash_password(payload.password or DEFAULT_PASSWORD), sorted(set(payload.role_ids)), payload.is_active,
        payload.created_by, payload.external_user_id,
    )


def set_password(employee_code: str, password: str, updated_by: str | None):
    if not password:
        raise HTTPException(status_code=400, detail="Vui lòng nhập mật khẩu.")
    return repository.set_password(employee_code, hash_password(password), updated_by)


def update_user_status(user_id: int, is_active: bool, updated_by: str | None):
    return repository.update_user_status(user_id, is_active, updated_by)
