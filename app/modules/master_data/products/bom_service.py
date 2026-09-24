"""Product BOM helpers, including the material requirement calculation other modules reuse.

Requirement per line = production_qty / base_qty * qty_per_base * (1 + scrap_pct / 100),
expressed in the material's own unit.
"""

from fastapi import HTTPException


BOM_SELECT = """
    SELECT b.*, p.product_code, p.product_name, pv.revision_code AS product_revision,
           (SELECT COUNT(*) FROM bom_items i WHERE i.bom_id = b.id) AS item_count
    FROM boms b
    JOIN products p ON p.id = b.product_id
    LEFT JOIN product_versions pv ON pv.id = b.product_version_id
"""

ITEM_SELECT = """
    SELECT i.*, m.material_code, m.material_name, m.material_type, m.unit, m.status AS material_status
    FROM bom_items i JOIN materials m ON m.id = i.material_id
    WHERE i.bom_id=%s ORDER BY i.line_no
"""


def get_bom(cur, bom_id: int):
    cur.execute(f"{BOM_SELECT} WHERE b.id=%s", (bom_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="BOM not found.")
    return row


def get_bom_items(cur, bom_id: int):
    cur.execute(ITEM_SELECT, (bom_id,))
    return cur.fetchall()


def next_revision(cur, product_id: int) -> str:
    cur.execute("SELECT bom_revision FROM boms WHERE product_id=%s", (product_id,))
    numbers = [int(r["bom_revision"]) for r in cur.fetchall() if str(r["bom_revision"]).isdigit()]
    return f"{(max(numbers) + 1 if numbers else 1):02d}"


def check_duplicate_revision(cur, product_id: int, revision: str, exclude_id: int | None = None):
    sql = "SELECT id FROM boms WHERE product_id=%s AND bom_revision=%s"
    params = [product_id, revision]
    if exclude_id is not None:
        sql += " AND id<>%s"
        params.append(exclude_id)
    cur.execute(sql, tuple(params))
    if cur.fetchone():
        raise HTTPException(status_code=409, detail="This BOM revision already exists for the product.")


def validate_items(cur, items):
    if not items:
        return
    ids = [i.material_id for i in items]
    cur.execute(f"SELECT id, status FROM materials WHERE id IN ({','.join(['%s'] * len(ids))})", tuple(ids))
    found = {r["id"]: r["status"] for r in cur.fetchall()}
    missing = [i for i in ids if i not in found]
    if missing:
        raise HTTPException(status_code=400, detail="One or more materials do not exist.")
    inactive = [i for i in ids if found[i] != "ACTIVE"]
    if inactive:
        raise HTTPException(status_code=400, detail="Inactive materials cannot be added to a BOM.")


def replace_items(cur, bom_id: int, items):
    cur.execute("DELETE FROM bom_items WHERE bom_id=%s", (bom_id,))
    for line_no, item in enumerate(items, start=1):
        cur.execute(
            """INSERT INTO bom_items (bom_id, line_no, material_id, component_type, qty_per_base, scrap_pct, is_critical, remark)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (bom_id, line_no, item.material_id, item.component_type, item.qty_per_base, item.scrap_pct,
             int(item.is_critical), item.remark),
        )


def write_audit(cur, bom_id: int, action: str, from_status, to_status, actor, remark=None):
    cur.execute(
        """INSERT INTO bom_audit_log (bom_id, action, from_status, to_status, actor, remark)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (bom_id, action, from_status, to_status, actor or "SYSTEM", remark),
    )


def requirement_from_items(items, base_qty, production_qty):
    base = float(base_qty) or 1.0
    rows = []
    for item in items:
        net = float(production_qty) / base * float(item["qty_per_base"])
        gross = net * (1 + float(item["scrap_pct"] or 0) / 100)
        rows.append({
            "material_id": item["material_id"], "material_code": item["material_code"], "material_name": item["material_name"],
            "component_type": item["component_type"], "unit": item["unit"], "is_critical": bool(item["is_critical"]),
            "qty_per_base": float(item["qty_per_base"]), "scrap_pct": float(item["scrap_pct"] or 0),
            "net_qty": round(net, 4), "required_qty": round(gross, 4),
        })
    return rows


def active_bom_requirement(cur, product_id: int, production_qty: float):
    """Material requirement of the product's ACTIVE BOM, or None when it has no active BOM.

    Shared entry point for Material Requirement / Production Planning.
    """
    cur.execute("SELECT id, base_qty, bom_revision FROM boms WHERE product_id=%s AND status='ACTIVE' LIMIT 1", (product_id,))
    bom = cur.fetchone()
    if not bom:
        return None
    return {"bom_id": bom["id"], "bom_revision": bom["bom_revision"],
            "rows": requirement_from_items(get_bom_items(cur, bom["id"]), bom["base_qty"], production_qty)}
