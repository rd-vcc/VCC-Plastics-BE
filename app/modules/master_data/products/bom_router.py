from datetime import date

from fastapi import APIRouter, HTTPException, Query

from app.core.database import get_vcc_plastics_connection
from app.modules.master_data.products.bom_schemas import BomAction, BomCopy, BomCreate, BomUpdate
from app.modules.master_data.products.bom_service import (
    BOM_SELECT,
    check_duplicate_revision,
    get_bom,
    get_bom_items,
    next_revision,
    replace_items,
    requirement_from_items,
    validate_items,
    write_audit,
)
from app.modules.master_data.products.service import get_product


router = APIRouter(prefix="/api/product-master", tags=["Product BOM"])

STALE = "This BOM was changed by another user. Reload and try again."


def _run_write(action):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        result = action(cur)
        conn.commit()
        return result
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        message = str(exc)
        if "Duplicate" in message:
            raise HTTPException(status_code=409, detail="Duplicate data.") from exc
        if "foreign key constraint fails" in message.lower():
            raise HTTPException(status_code=409, detail="Data is referenced or reference is invalid.") from exc
        raise HTTPException(status_code=400, detail=message) from exc
    finally:
        cur.close()
        conn.close()


def _read(action):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        return action(cur)
    finally:
        cur.close(); conn.close()


def _check_version(cur, product_id, product_version_id):
    if product_version_id:
        cur.execute("SELECT id FROM product_versions WHERE id=%s AND product_id=%s", (product_version_id, product_id))
        if not cur.fetchone():
            raise HTTPException(status_code=400, detail="Product version does not belong to this product.")


def _draft(cur, bom_id: int):
    bom = get_bom(cur, bom_id)
    if bom["status"] != "DRAFT":
        raise HTTPException(status_code=409, detail="Only Draft BOMs can be changed. Copy it to a new revision instead.")
    return bom


# =========================================================
# LOOKUPS + LISTS
# =========================================================


@router.get("/bom-lookups")
def bom_lookups():
    def action(cur):
        cur.execute(
            """SELECT id, material_code, material_name, material_type, unit
               FROM materials WHERE status='ACTIVE' ORDER BY material_code""")
        return {"materials": cur.fetchall()}
    return _read(action)


@router.get("/boms")
def list_boms(keyword: str | None = None, status: str | None = None, limit: int = Query(default=1000, ge=1, le=5000)):
    def action(cur):
        conditions, params = [], []
        if keyword:
            conditions.append("(p.product_code LIKE %s OR p.product_name LIKE %s)")
            value = f"%{keyword.strip()}%"; params += [value, value]
        if status:
            conditions.append("b.status=%s"); params.append(status.upper())
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        cur.execute(f"{BOM_SELECT}{where} ORDER BY p.product_code, b.bom_revision DESC LIMIT %s", tuple(params + [limit]))
        return cur.fetchall()
    return _read(action)


@router.get("/products/{product_id}/boms")
def product_boms(product_id: int):
    def action(cur):
        get_product(cur, product_id)
        cur.execute(f"{BOM_SELECT} WHERE b.product_id=%s ORDER BY FIELD(b.status,'ACTIVE','DRAFT','INACTIVE'), b.bom_revision DESC", (product_id,))
        return cur.fetchall()
    return _read(action)


@router.get("/boms/{bom_id}")
def bom_detail(bom_id: int):
    def action(cur):
        bom = get_bom(cur, bom_id)
        items = get_bom_items(cur, bom_id)
        cur.execute("SELECT * FROM bom_audit_log WHERE bom_id=%s ORDER BY acted_at DESC, id DESC", (bom_id,))
        return {**bom, "items": items, "history": cur.fetchall()}
    return _read(action)


@router.get("/boms/{bom_id}/requirement")
def bom_requirement(bom_id: int, qty: float = Query(gt=0)):
    def action(cur):
        bom = get_bom(cur, bom_id)
        return {"bom_id": bom_id, "production_qty": qty, "rows": requirement_from_items(get_bom_items(cur, bom_id), bom["base_qty"], qty)}
    return _read(action)


# =========================================================
# WRITE
# =========================================================


@router.post("/products/{product_id}/boms", status_code=201)
def create_bom(product_id: int, payload: BomCreate):
    def action(cur):
        get_product(cur, product_id)
        _check_version(cur, product_id, payload.product_version_id)
        revision = payload.bom_revision or next_revision(cur, product_id)
        check_duplicate_revision(cur, product_id, revision)
        validate_items(cur, payload.items)
        cur.execute(
            """INSERT INTO boms (product_id, product_version_id, bom_revision, base_qty, effective_from, effective_to,
               description, status, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,'DRAFT',%s)""",
            (product_id, payload.product_version_id, revision, payload.base_qty, payload.effective_from,
             payload.effective_to, payload.description, payload.created_by),
        )
        bom_id = cur.lastrowid
        replace_items(cur, bom_id, payload.items)
        write_audit(cur, bom_id, "CREATE", None, "DRAFT", payload.created_by)
        return {"message": "BOM created.", "id": bom_id, "bom_revision": revision}
    return _run_write(action)


@router.put("/boms/{bom_id}")
def update_bom(bom_id: int, payload: BomUpdate):
    def action(cur):
        bom = _draft(cur, bom_id)
        _check_version(cur, bom["product_id"], payload.product_version_id)
        revision = payload.bom_revision or bom["bom_revision"]
        check_duplicate_revision(cur, bom["product_id"], revision, bom_id)
        validate_items(cur, payload.items)
        cur.execute(
            """UPDATE boms SET bom_revision=%s, product_version_id=%s, base_qty=%s, effective_from=%s, effective_to=%s,
               description=%s, updated_by=%s, version=version+1 WHERE id=%s AND version=%s""",
            (revision, payload.product_version_id, payload.base_qty, payload.effective_from, payload.effective_to,
             payload.description, payload.updated_by, bom_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail=STALE)
        replace_items(cur, bom_id, payload.items)
        write_audit(cur, bom_id, "UPDATE", "DRAFT", "DRAFT", payload.updated_by, f"{len(payload.items)} lines")
        return {"message": "BOM saved.", "version": payload.version + 1}
    return _run_write(action)


@router.post("/boms/{bom_id}/activate")
def activate_bom(bom_id: int, payload: BomAction):
    def action(cur):
        bom = _draft(cur, bom_id)
        if not bom["item_count"]:
            raise HTTPException(status_code=409, detail="Add at least one material line before activating the BOM.")
        effective_from = payload.effective_from or bom["effective_from"] or date.today()
        cur.execute("SELECT id FROM boms WHERE product_id=%s AND status='ACTIVE' AND id<>%s", (bom["product_id"], bom_id))
        for old in cur.fetchall():
            cur.execute("UPDATE boms SET status='INACTIVE', effective_to=COALESCE(effective_to, %s), updated_by=%s, version=version+1 WHERE id=%s",
                        (effective_from, payload.actor, old["id"]))
            write_audit(cur, old["id"], "SUPERSEDE", "ACTIVE", "INACTIVE", payload.actor, f"Replaced by revision {bom['bom_revision']}")
        cur.execute(
            """UPDATE boms SET status='ACTIVE', effective_from=%s, activated_by=%s, activated_at=NOW(), updated_by=%s,
               version=version+1 WHERE id=%s AND version=%s""",
            (effective_from, payload.actor, payload.actor, bom_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail=STALE)
        write_audit(cur, bom_id, "ACTIVATE", "DRAFT", "ACTIVE", payload.actor, payload.remark)
        return {"message": "BOM activated.", "version": payload.version + 1}
    return _run_write(action)


@router.post("/boms/{bom_id}/deactivate")
def deactivate_bom(bom_id: int, payload: BomAction):
    def action(cur):
        bom = get_bom(cur, bom_id)
        if bom["status"] != "ACTIVE":
            raise HTTPException(status_code=409, detail="Only the active BOM can be deactivated.")
        cur.execute(
            "UPDATE boms SET status='INACTIVE', effective_to=COALESCE(effective_to, CURDATE()), updated_by=%s, version=version+1 WHERE id=%s AND version=%s",
            (payload.actor, bom_id, payload.version))
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail=STALE)
        write_audit(cur, bom_id, "DEACTIVATE", "ACTIVE", "INACTIVE", payload.actor, payload.remark)
        return {"message": "BOM deactivated.", "version": payload.version + 1}
    return _run_write(action)


@router.post("/boms/{bom_id}/copy", status_code=201)
def copy_bom(bom_id: int, payload: BomCopy):
    def action(cur):
        source = get_bom(cur, bom_id)
        revision = (payload.new_revision or "").strip().upper() or next_revision(cur, source["product_id"])
        check_duplicate_revision(cur, source["product_id"], revision)
        cur.execute(
            """INSERT INTO boms (product_id, product_version_id, bom_revision, base_qty, description, status, created_by)
               VALUES (%s,%s,%s,%s,%s,'DRAFT',%s)""",
            (source["product_id"], source["product_version_id"], revision, source["base_qty"], source["description"], payload.created_by),
        )
        new_id = cur.lastrowid
        cur.execute(
            """INSERT INTO bom_items (bom_id, line_no, material_id, component_type, qty_per_base, scrap_pct, is_critical, remark)
               SELECT %s, line_no, material_id, component_type, qty_per_base, scrap_pct, is_critical, remark FROM bom_items WHERE bom_id=%s""",
            (new_id, bom_id),
        )
        write_audit(cur, new_id, "COPY", None, "DRAFT", payload.created_by, f"Copied from revision {source['bom_revision']}")
        return {"message": "BOM copied.", "id": new_id, "bom_revision": revision}
    return _run_write(action)


@router.delete("/boms/{bom_id}")
def delete_bom(bom_id: int):
    def action(cur):
        _draft(cur, bom_id)
        cur.execute("DELETE FROM bom_audit_log WHERE bom_id=%s", (bom_id,))
        cur.execute("DELETE FROM boms WHERE id=%s", (bom_id,))
        return {"message": "BOM deleted."}
    return _run_write(action)
