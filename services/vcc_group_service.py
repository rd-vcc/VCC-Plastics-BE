from typing import Any

import requests
from fastapi import HTTPException
from core.config import VCC_GROUP_API


def _read_response_json(response: requests.Response, default_message: str) -> Any:
    try:
        data = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="Phản hồi từ VCC Group không phải JSON hợp lệ.",
        ) from exc

    if response.status_code >= 400:
        message = (
            data.get("error") or data.get("detail") or data.get("message")
            if isinstance(data, dict)
            else None
        )
        raise HTTPException(
            status_code=response.status_code,
            detail=message or default_message,
        )

    return data


def login_vcc_group(username: str, password: str) -> dict:
    url = f"{VCC_GROUP_API}/api/login"
    try:
        response = requests.post(
            url,
            json={"username": username, "password": password},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Không thể kết nối VCC Group: {exc}",
        ) from exc

    data = _read_response_json(response, "Đăng nhập VCC Group thất bại.")

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
    """
    params: dict[str, str | int] = {
        "employment_status": employment_status or "active",
    }
    if org_id is not None:
        params["org_id"] = org_id

    try:
        response = requests.get(
            f"{VCC_GROUP_API}/employees/list",
            params=params,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Không thể lấy danh sách nhân viên từ VCC Group: {exc}",
        ) from exc

    data = _read_response_json(
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
