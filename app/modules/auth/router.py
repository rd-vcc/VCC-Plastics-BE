from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.modules.administration.audit import service as audit
from app.modules.auth.schemas import LoginRequest
from app.modules.auth.service import login as login_service

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


def _client(request: Request):
    ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else None)
    return ip, (request.headers.get("user-agent") or "")[:300]


@router.post("/login")
def login(payload: LoginRequest, request: Request):
    ip, agent = _client(request)
    try:
        result = login_service(payload.username, payload.password)
    except HTTPException as exc:
        # failed login attempts are security events of the Audit Log
        audit.write({"user_code": payload.username.strip(), "user_name": payload.username.strip(), "module": "AUTHENTICATION", "action": "FAILED_LOGIN",
                     "data_type": "LOGIN", "description": "Failed login attempt", "method": "POST", "path": "/api/auth/login", "ip_address": ip, "user_agent": agent,
                     "result": "FAILED", "status_code": exc.status_code, "severity": "HIGH", "message": str(exc.detail)[:500], "source": "AUTH"})
        raise
    if isinstance(result, dict) and result.get("token"):
        user = result.get("user") or {}
        code = str(user.get("employee_code") or payload.username)
        name = user.get("full_name") or code
        audit.write({"user_code": code, "user_name": f"{name} ({code})" if name != code else code, "role_name": audit.roles_of(code), "module": "AUTHENTICATION",
                     "action": "LOGIN", "data_type": "LOGIN", "record_id": code, "description": "User logged in", "method": "POST", "path": "/api/auth/login",
                     "ip_address": ip, "user_agent": agent, "result": "SUCCESS", "status_code": 200, "severity": "LOW", "source": "AUTH"})
    return result


class LogoutIn(BaseModel):
    user_code: str | None = None
    user_name: str | None = None


@router.post("/logout")
def logout(payload: LogoutIn, request: Request):
    ip, agent = _client(request)
    audit.write({"user_code": payload.user_code, "user_name": payload.user_name or payload.user_code, "role_name": audit.roles_of(payload.user_code), "module": "AUTHENTICATION",
                 "action": "LOGOUT", "data_type": "LOGIN", "record_id": payload.user_code, "description": "User logged out", "method": "POST", "path": "/api/auth/logout",
                 "ip_address": ip, "user_agent": agent, "result": "SUCCESS", "status_code": 200, "severity": "LOW", "source": "AUTH"})
    return {"message": "OK"}
