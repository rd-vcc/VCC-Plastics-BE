"""Login orchestration.

Ported from ``routers/auth.py``'s endpoint body: calls the VCC Group
integration, then looks up MES access, exactly as before.
"""

from app.integrations.vcc_group.service import login_vcc_group
from app.modules.auth.repository import get_mes_access


def login(username: str, password: str) -> dict:
    username = username.strip()
    if not username or not password:
        return {"detail": "Vui lòng nhập đầy đủ tài khoản và mật khẩu."}

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
