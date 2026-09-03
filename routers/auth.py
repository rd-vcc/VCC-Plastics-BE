from fastapi import APIRouter
from schemas.auth import LoginRequest
from services.vcc_group_service import login_vcc_group
from services.permission_service import get_mes_access

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/login")
def login(payload: LoginRequest):
    username = payload.username.strip()
    if not username or not payload.password:
        return {"detail": "Vui lòng nhập đầy đủ tài khoản và mật khẩu."}

    group_result = login_vcc_group(username, payload.password)
    user = group_result["user"]
    employee_code = str(user.get("employee_code") or username)
    mes_access = get_mes_access(employee_code)

    return {
        "message": "Login successful",
        "token": group_result["token"],
        "user": user,
        "mes_access": mes_access,
    }
