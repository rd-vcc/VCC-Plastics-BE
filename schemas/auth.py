from typing import Any, Optional
from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class MesAccess(BaseModel):
    enabled: bool
    roles: list[str] = []
    permissions: list[str] = []


class LoginResponse(BaseModel):
    message: str
    token: str
    user: dict[str, Any]
    mes_access: MesAccess


class ErrorResponse(BaseModel):
    detail: str
    code: Optional[str] = None
