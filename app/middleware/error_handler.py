"""Generic unhandled-exception safety net.

Registers a handler for the broad ``Exception`` class only. FastAPI's own,
more specific default handler for ``HTTPException`` (and Starlette's
``HTTPException``) is still used for those - Starlette dispatches to the most
specific registered handler, so this does NOT change the response
shape/status code for any existing ``raise HTTPException(...)`` call
anywhere in the ported routers. It only prevents a truly unhandled exception
from producing an unformatted 500 traceback, mapping it instead to the same
default FastAPI-style ``{"detail": str(exc)}`` body with status 500.
"""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.core.logging import logger


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": str(exc)})
