"""Pagination helpers.

Extracted from the ``limit``/``offset`` pattern used in
``app.modules.master_data.machines.router.list_equipment``:

    limit: int = Query(default=500, ge=1, le=2000)
    offset: int = Query(default=0, ge=0)

The existing endpoint keeps its own inline declaration unchanged (to avoid
any risk of altering its OpenAPI schema or defaults); this helper is
available for NEW list endpoints that want the same convention.
"""

from fastapi import Query


def limit_query(default: int = 500, le: int = 2000) -> Query:
    return Query(default=default, ge=1, le=le)


def offset_query(default: int = 0) -> Query:
    return Query(default=default, ge=0)


def paginated_response(total: int, items: list, limit: int, offset: int) -> dict:
    """Matches the ``{"total": ..., "items": [...]}`` shape used by machines/list_equipment."""
    return {"total": total, "items": items, "limit": limit, "offset": offset}
