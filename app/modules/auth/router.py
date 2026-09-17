from fastapi import APIRouter

from app.modules.auth.schemas import LoginRequest
from app.modules.auth.service import login as login_service

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.post("/login")
def login(payload: LoginRequest):
    return login_service(payload.username, payload.password)
