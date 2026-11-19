"""Low-level HTTP calls to the external VCC Group API.

Ported from ``services/vcc_group_service.py``. This module only wraps
``requests`` calls and raw response handling; higher-level orchestration
(``login_vcc_group``, ``list_vcc_group_employees``,
``find_vcc_group_employee``) lives in ``app.integrations.vcc_group.service``.
"""

from typing import Any

import requests
from fastapi import HTTPException

from app.core.config import VCC_GROUP_API


def read_response_json(response: requests.Response, default_message: str) -> Any:
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


def post_login(username: str, password: str) -> requests.Response:
    url = f"{VCC_GROUP_API}/api/login"
    try:
        return requests.post(
            url,
            json={"username": username, "password": password},
            timeout=(3, 15),  # give up quickly when VCC Group is unreachable
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Không thể kết nối VCC Group: {exc}",
        ) from exc


def get_employees_list(params: dict) -> requests.Response:
    try:
        return requests.get(
            f"{VCC_GROUP_API}/employees/list",
            params=params,
            timeout=(3, 30),
        )
    except requests.RequestException as exc:
        raise HTTPException(
            status_code=503,
            detail=f"Không thể lấy danh sách nhân viên từ VCC Group: {exc}",
        ) from exc
