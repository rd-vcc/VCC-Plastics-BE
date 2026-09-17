"""Shared exception helpers and classes.

These are convenience utilities intended for NEW code. Existing routers
ported from the original flat layout already have their own inline
duplicate/foreign-key detection (for example ``_raise_database_error`` in
``app.modules.master_data.products.router`` or ``_db_error`` in
``app.modules.master_data.molds.router``) and are left completely untouched
to avoid any risk of changing a response shape or status code the frontend
depends on.
"""

from fastapi import HTTPException


class AppError(HTTPException):
    """Base class for application-specific HTTP errors."""


class NotFoundError(AppError):
    def __init__(self, detail: str = "Resource not found."):
        super().__init__(status_code=404, detail=detail)


class ConflictError(AppError):
    def __init__(self, detail: str = "Conflict."):
        super().__init__(status_code=409, detail=detail)


class ValidationAppError(AppError):
    def __init__(self, detail: str = "Invalid request."):
        super().__init__(status_code=400, detail=detail)


def is_duplicate_error(exc: Exception) -> bool:
    """Mirrors the ``"Duplicate" in str(exc)`` check used throughout the ported routers."""
    return "Duplicate" in str(exc)


def is_foreign_key_error(exc: Exception) -> bool:
    """Mirrors the foreign-key check used in app.modules.master_data.molds.router."""
    return "foreign key constraint fails" in str(exc).lower()
