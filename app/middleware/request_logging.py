"""Request logging middleware.

Logs method, path, response status code and duration for every request via
``app.core.logging``. Registered on the app in ``main.py``.
"""

import time

from fastapi import Request

from app.core.logging import logger


async def request_logging_middleware(request: Request, call_next):
    start_time = time.perf_counter()
    response = await call_next(request)
    duration_ms = (time.perf_counter() - start_time) * 1000
    logger.info(
        "%s %s -> %s (%.2fms)",
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response
