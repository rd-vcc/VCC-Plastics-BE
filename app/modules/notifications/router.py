"""In-app notifications for the signed-in user.

The user is identified by the ``X-MES-User`` header the frontend adds to every call (see FE auth/apiIdentity.js),
with ``employee_code`` as a fallback query parameter.
"""

from urllib.parse import unquote

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

from app.modules.notifications import service

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


class ReadBody(BaseModel):
    ids: list[int] | None = None   # empty / missing = mark every visible notification as read


def _who(header_user, employee_code):
    code = (unquote(header_user) if header_user else employee_code or "").strip()
    if not code:
        raise HTTPException(status_code=400, detail="Không xác định được người dùng.")
    return code


@router.get("")
def list_notifications(limit: int = Query(50, ge=1, le=200), unread: bool = False, employee_code: str | None = None,
                       x_mes_user: str | None = Header(default=None)):
    return service.list_for_user(_who(x_mes_user, employee_code), limit=limit, unread_only=unread)


@router.get("/unread-count")
def get_unread_count(employee_code: str | None = None, x_mes_user: str | None = Header(default=None)):
    return {"unread": service.unread_count(_who(x_mes_user, employee_code))}


@router.post("/read")
def mark_read(body: ReadBody, employee_code: str | None = None, x_mes_user: str | None = Header(default=None)):
    return service.mark_read(_who(x_mes_user, employee_code), body.ids or None)
