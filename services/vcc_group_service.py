import requests
from fastapi import HTTPException
from core.config import VCC_GROUP_API


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

    try:
        data = response.json()
    except ValueError:
        data = {}

    if response.status_code >= 400:
        message = data.get("error") or data.get("message") or "Đăng nhập VCC Group thất bại."
        raise HTTPException(status_code=response.status_code, detail=message)

    if not data.get("token") or not data.get("user"):
        raise HTTPException(status_code=502, detail="Phản hồi đăng nhập từ VCC Group không hợp lệ.")

    return data
