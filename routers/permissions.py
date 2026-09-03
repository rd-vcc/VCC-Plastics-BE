from collections import defaultdict

from fastapi import APIRouter

from database.db import get_vcc_plastics_connection

router = APIRouter(prefix="/api/permissions", tags=["Permissions"])


@router.get("")
def list_permissions():
    """Danh sach permission co dinh cua he thong. Khong cho phep tao/sua/xoa qua API."""
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT id, permission_code, permission_name, module_code,
                   description, is_active, created_at, updated_at
            FROM permissions
            WHERE is_active = 1
            ORDER BY module_code, permission_code
            """
        )
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


@router.get("/grouped")
def list_permissions_grouped():
    """Tra permission theo module de FE Role & Permission render checkbox theo nhom."""
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT id, permission_code, permission_name, module_code, description
            FROM permissions
            WHERE is_active = 1
            ORDER BY module_code, permission_code
            """
        )
        rows = cur.fetchall()

        grouped = defaultdict(list)
        for row in rows:
            module_code = row.get("module_code") or "OTHER"
            grouped[module_code].append(row)

        return [
            {"module_code": module_code, "permissions": items}
            for module_code, items in grouped.items()
        ]
    finally:
        cur.close()
        conn.close()
