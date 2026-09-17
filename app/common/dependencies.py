"""Shared FastAPI dependencies.

Not used by any existing endpoint today - every ported router opens and
closes its own ``mysql.connector`` connection per request, exactly like the
original flat-layout code did, to avoid any risk of changing transaction or
error-handling behavior. This dependency is available for NEW code that
prefers dependency-injected DB access.
"""

from typing import Generator, Tuple

from app.core.database import get_vcc_plastics_connection


def get_db() -> Generator[Tuple[object, object], None, None]:
    """Yield a ``(connection, dictionary_cursor)`` pair and always clean up."""
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        yield conn, cur
    finally:
        cur.close()
        conn.close()
