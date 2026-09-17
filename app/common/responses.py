"""Optional response helpers for NEW code only.

Do NOT use these to wrap or change any EXISTING endpoint's response shape -
the frontend depends on the exact current JSON shapes returned by the ported
routers under ``app/modules`` and aggregated in ``app/api/router.py``.
"""

from typing import Any, Optional


def success_response(data: Any = None, message: Optional[str] = None) -> dict:
    payload: dict = {}
    if message is not None:
        payload["message"] = message
    if data is not None:
        payload["data"] = data
    return payload
