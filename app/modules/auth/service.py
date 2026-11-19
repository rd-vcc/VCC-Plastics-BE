"""Login orchestration.

1. Local account: the employee has a VCC Plastics password (``mes_users.password_hash``) ->
   checked here, VCC Group is not called (works on a server that cannot reach VCC Group).
2. Otherwise: VCC Group login as before, then MES access lookup.
"""

import secrets

from fastapi import HTTPException

from app.core.passwords import verify_password
from app.integrations.vcc_group.service import login_vcc_group
from app.modules.auth.repository import find_local_account, get_mes_access


def login(username: str, password: str) -> dict:
    username = username.strip()
    if not username or not password:
        return {"detail": "Vui lòng nhập đầy đủ tài khoản và mật khẩu."}

    account = find_local_account(username)
    if account and account.get("password_hash"):
        if not verify_password(password, account["password_hash"]):
            raise HTTPException(status_code=401, detail="Tài khoản hoặc mật khẩu không đúng.")
        employee_code = str(account["employee_code"]).strip()
        user = {
            "employee_code": employee_code,
            "full_name": account.get("full_name") or employee_code,
            "name": account.get("full_name") or employee_code,
            "position": account.get("position"),
            "department": account.get("department"),
            "organization_unit_name": account.get("department"),
            "auth_source": "LOCAL",
        }
        return {
            "message": "Login successful",
            "token": secrets.token_urlsafe(32),
            "user": user,
            "mes_access": get_mes_access(employee_code),
        }

    group_result = login_vcc_group(username, password)
    user = group_result["user"]
    employee_code = str(user.get("employee_code") or username)
    mes_access = get_mes_access(employee_code)

    return {
        "message": "Login successful",
        "token": group_result["token"],
        "user": user,
        "mes_access": mes_access,
    }
