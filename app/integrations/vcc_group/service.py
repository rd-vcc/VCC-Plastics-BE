"""Higher-level VCC Group integration functions.

Ported from ``services/vcc_group_service.py``. ``app.modules.auth`` and
``app.modules.organization.employees`` import from here (previously they
imported directly from ``services.vcc_group_service``).
"""

from app.integrations.vcc_group.client import (
    get_employees_list,
    post_login,
    read_response_json,
)
from fastapi import HTTPException

from app.core.config import VCC_GROUP_API


def vcc_group_enabled() -> bool:
    """False in standalone mode (VCC_GROUP_API left empty in .env): VCC Group is never called."""
    return bool(VCC_GROUP_API)


def login_vcc_group(username: str, password: str) -> dict:
    if not vcc_group_enabled():
        raise HTTPException(status_code=401, detail="Tài khoản hoặc mật khẩu không đúng.")
    response = post_login(username, password)
    data = read_response_json(response, "Đăng nhập VCC Group thất bại.")

    if not data.get("token") or not data.get("user"):
        raise HTTPException(status_code=502, detail="Phản hồi đăng nhập từ VCC Group không hợp lệ.")

    return data


def list_vcc_group_employees(
    org_id: int | None = None,
    employment_status: str = "active",
) -> list[dict]:
    """Lấy danh mục nhân viên chuẩn từ VCC Group.

    VCC Plastics không sao chép hồ sơ nhân sự. Dữ liệu tên, chức danh và
    đơn vị tổ chức luôn được đọc từ API /employees/list của VCC Group.
    Chế độ độc lập (không cấu hình VCC_GROUP_API): trả về danh sách rỗng.
    """
    if not vcc_group_enabled():
        return []
    params: dict[str, str | int] = {
        "employment_status": employment_status or "active",
    }
    if org_id is not None:
        params["org_id"] = org_id

    response = get_employees_list(params)
    data = read_response_json(
        response,
        "Không thể lấy danh sách nhân viên từ VCC Group.",
    )
    if not isinstance(data, list):
        raise HTTPException(
            status_code=502,
            detail="VCC Group trả về danh sách nhân viên không đúng định dạng.",
        )
    return data


def find_vcc_group_employee(employee_code: str) -> dict | None:
    normalized_code = employee_code.strip().casefold()
    employees = list_vcc_group_employees(employment_status="all")
    return next(
        (
            employee
            for employee in employees
            if str(employee.get("employee_code") or "").strip().casefold()
            == normalized_code
        ),
        None,
    )
