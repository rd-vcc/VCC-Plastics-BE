from fastapi import APIRouter, HTTPException, Query

from app.core.database import get_vcc_plastics_connection
from app.core.employee_names import attach_employee_names
from app.modules.master_data.quality_standards.schemas import (
    CopyStandardRequest,
    RevisionUpdate,
    StandardCreate,
    StandardStatusUpdate,
)
from app.modules.master_data.quality_standards.service import (
    check_duplicate_code,
    current_revision_row,
    get_standard,
    load_characteristics,
    replace_characteristics,
    write_audit,
)


router = APIRouter(prefix="/api/quality-standard-master", tags=["Quality Standard Master"])

# The "current revision" of a standard is its ACTIVE row if one exists, otherwise
# whichever row has the highest revision_no. This correlated subquery picks that
# single row's id so it can be joined into both the list and statistics queries.
_CURRENT_REVISION_ID_SUBQUERY = """
    (SELECT r.id FROM quality_standard_revisions r
     WHERE r.standard_id = s.id
     ORDER BY (r.status='ACTIVE') DESC, r.revision_no DESC
     LIMIT 1)
"""


def _db_error(exc: Exception):
    message = str(exc)
    if "Duplicate" in message:
        raise HTTPException(status_code=409, detail="Duplicate data.") from exc
    if "foreign key constraint fails" in message.lower():
        raise HTTPException(status_code=409, detail="Data is referenced or reference is invalid.") from exc
    raise HTTPException(status_code=400, detail=message) from exc


@router.get("/statistics")
def statistics():
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        cur.execute(f"""
            SELECT COUNT(*) total_standards,
                   SUM(cr.status='ACTIVE') active,
                   SUM(cr.status<>'ACTIVE') inactive,
                   COUNT(DISTINCT cr.product_id) products_covered
            FROM quality_standards s
            JOIN quality_standard_revisions cr ON cr.id = {_CURRENT_REVISION_ID_SUBQUERY}
        """)
        row = cur.fetchone()
        return {key: int(value) if value is not None else 0 for key, value in row.items()}
    finally:
        cur.close(); conn.close()


@router.get("/standards")
def list_standards(
    keyword: str | None = None, product_id: int | None = None, customer: str | None = None,
    process: str | None = None, status: str | None = None,
    page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=500),
):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        conditions, params = [], []
        if keyword:
            conditions.append("(s.standard_code LIKE %s OR cr.standard_name LIKE %s)")
            value = f"%{keyword.strip()}%"; params += [value, value]
        if product_id is not None:
            conditions.append("cr.product_id=%s"); params.append(product_id)
        if customer:
            conditions.append("cr.customer=%s"); params.append(customer)
        if process:
            conditions.append("cr.process=%s"); params.append(process)
        if status:
            conditions.append("cr.status=%s"); params.append(status.upper())
        where = " WHERE " + " AND ".join(conditions) if conditions else ""

        base = f"""
            FROM quality_standards s
            JOIN quality_standard_revisions cr ON cr.id = {_CURRENT_REVISION_ID_SUBQUERY}
            LEFT JOIN products p ON p.id = cr.product_id
            {where}
        """
        cur.execute(f"SELECT COUNT(*) total {base}", tuple(params))
        total = cur.fetchone()["total"]
        sql = f"""
            SELECT s.id, s.standard_code, cr.id AS revision_id, cr.revision_no, cr.standard_name,
                   cr.product_id, p.product_code, p.product_name, cr.customer, cr.process, cr.status,
                   cr.effective_date, cr.submitted_by, cr.submitted_at, cr.approved_by, cr.approved_at,
                   cr.version, cr.updated_at
            {base}
            ORDER BY s.standard_code
            LIMIT %s OFFSET %s
        """
        cur.execute(sql, tuple(params + [page_size, (page - 1) * page_size]))
        return {"items": cur.fetchall(), "total": total, "page": page, "page_size": page_size}
    finally:
        cur.close(); conn.close()


@router.get("/standards/{standard_id}")
def standard_detail(standard_id: int, revision_id: int | None = None):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        standard = get_standard(cur, standard_id)
        cur.execute(
            "SELECT * FROM quality_standard_revisions WHERE standard_id=%s ORDER BY revision_no DESC",
            (standard_id,),
        )
        revisions = cur.fetchall()
        if revision_id is not None:
            revision = next((r for r in revisions if r["id"] == revision_id), None)
            if not revision:
                raise HTTPException(status_code=404, detail="Revision not found.")
        else:
            revision = current_revision_row(cur, standard_id)
        if revision and revision.get("product_id"):
            cur.execute("SELECT product_code, product_name FROM products WHERE id=%s", (revision["product_id"],))
            product = cur.fetchone()
            if product:
                revision = {**revision, **product}
        characteristics = load_characteristics(cur, revision["id"]) if revision else []
        if revision:
            revision = attach_employee_names(cur, [revision], ["created_by", "updated_by", "submitted_by", "approved_by"])[0]
        standard = attach_employee_names(cur, [standard], ["created_by", "updated_by"])[0]
        return {**standard, "revisions": revisions, "revision": revision, "characteristics": characteristics}
    finally:
        cur.close(); conn.close()


@router.get("/standards/{standard_id}/audit-log")
def audit_log(standard_id: int):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_standard(cur, standard_id)
        cur.execute(
            "SELECT * FROM quality_standard_audit_log WHERE standard_id=%s ORDER BY acted_at DESC, id DESC",
            (standard_id,),
        )
        return attach_employee_names(cur, cur.fetchall(), ["actor"])
    finally:
        cur.close(); conn.close()


# ---------------------------------------------------------------------------
# Writes. A quality standard is configuration: it takes effect as soon as it is saved
# (no submit / approve / activate). Its revisions are kept only so that inspection results
# stay tied to the limits they were measured against: editing a revision that inspections
# already use creates the next revision automatically and retires the old one.
# ---------------------------------------------------------------------------

_CHAR_INSERT = """INSERT INTO quality_standard_characteristics
    (revision_id, characteristic_name, nominal_value, lsl, usl, unit, inspection_method,
     equipment_type_id, inspection_frequency, characteristic_type, is_spc, chart_type, subgroup_size,
     cpk_target, is_critical, sort_order, created_by)
    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"""


def _copy_characteristics(cur, source_revision_id: int, target_revision_id: int, actor):
    rows = load_characteristics(cur, source_revision_id)
    if rows:
        cur.executemany(_CHAR_INSERT, [
            (target_revision_id, c["characteristic_name"], c["nominal_value"], c["lsl"], c["usl"], c["unit"],
             c["inspection_method"], c["equipment_type_id"], c["inspection_frequency"], c["characteristic_type"],
             c["is_spc"], c["chart_type"], c["subgroup_size"], c["cpk_target"], c["is_critical"], c["sort_order"], actor)
            for c in rows
        ])


def _revision_in_use(cur, revision_id: int) -> int:
    cur.execute("SELECT COUNT(*) total FROM quality_inspection_plans WHERE standard_revision_id=%s", (revision_id,))
    return cur.fetchone()["total"]


def _insert_revision(cur, standard_id: int, revision_no: int, data, status: str, actor):
    cur.execute(
        """INSERT INTO quality_standard_revisions
           (standard_id, revision_no, standard_name, product_id, customer, process,
            effective_date, remark, status, created_by)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (standard_id, revision_no, data["standard_name"], data["product_id"], data["customer"], data["process"],
         data["effective_date"], data["remark"], status, actor),
    )
    return cur.lastrowid


@router.post("/standards", status_code=201)
def create_standard(payload: StandardCreate):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        check_duplicate_code(cur, payload.standard_code)
        cur.execute("INSERT INTO quality_standards (standard_code, created_by) VALUES (%s,%s)", (payload.standard_code, payload.created_by))
        standard_id = cur.lastrowid
        revision_id = _insert_revision(cur, standard_id, 1, payload.model_dump(), "ACTIVE", payload.created_by)
        replace_characteristics(cur, revision_id, payload.characteristics, payload.created_by)
        write_audit(cur, standard_id, revision_id, "CREATE", None, "ACTIVE", payload.created_by)
        conn.commit()
        return {"message": "Quality standard created successfully.", "id": standard_id, "revision_id": revision_id}
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); _db_error(exc)
    finally:
        cur.close(); conn.close()


@router.put("/standards/{standard_id}")
def update_standard(standard_id: int, payload: RevisionUpdate):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_standard(cur, standard_id)
        current = current_revision_row(cur, standard_id)
        if not current:
            raise HTTPException(status_code=404, detail="Revision not found.")
        if current["version"] != payload.version:
            raise HTTPException(status_code=409, detail="This standard was changed by another user. Reload and try again.")
        if _revision_in_use(cur, current["id"]):
            # inspections already measured against these limits: keep them, continue on a new revision
            cur.execute("SELECT COALESCE(MAX(revision_no),0) max_no FROM quality_standard_revisions WHERE standard_id=%s", (standard_id,))
            next_no = cur.fetchone()["max_no"] + 1
            new_id = _insert_revision(cur, standard_id, next_no, payload.model_dump(), current["status"], payload.updated_by)
            replace_characteristics(cur, new_id, payload.characteristics, payload.updated_by)
            cur.execute("UPDATE quality_standard_revisions SET status='OBSOLETE', version=version+1 WHERE id=%s", (current["id"],))
            write_audit(cur, standard_id, new_id, "NEW_REVISION", current["status"], current["status"], payload.updated_by,
                        remark=f"Revision {current['revision_no']} is used by inspections; changes saved as revision {next_no}")
            conn.commit()
            return {"message": f"Saved as revision {next_no} (revision {current['revision_no']} is used by inspections).", "revision_id": new_id}
        cur.execute(
            """UPDATE quality_standard_revisions
               SET standard_name=%s, product_id=%s, customer=%s, process=%s, effective_date=%s,
                   remark=%s, updated_by=%s, version=version+1
               WHERE id=%s""",
            (payload.standard_name, payload.product_id, payload.customer, payload.process,
             payload.effective_date, payload.remark, payload.updated_by, current["id"]),
        )
        replace_characteristics(cur, current["id"], payload.characteristics, payload.updated_by)
        write_audit(cur, standard_id, current["id"], "UPDATE", current["status"], current["status"], payload.updated_by)
        conn.commit()
        return {"message": "Quality standard updated successfully.", "revision_id": current["id"]}
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); _db_error(exc)
    finally:
        cur.close(); conn.close()


@router.patch("/standards/{standard_id}/status")
def set_standard_status(standard_id: int, payload: StandardStatusUpdate):
    """Active ↔ inactive (stored as ACTIVE / OBSOLETE on the current revision)."""
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_standard(cur, standard_id)
        current = current_revision_row(cur, standard_id)
        target = "ACTIVE" if payload.status == "ACTIVE" else "OBSOLETE"
        if current["status"] != target:
            cur.execute("UPDATE quality_standard_revisions SET status=%s, updated_by=%s, version=version+1 WHERE id=%s",
                        (target, payload.actor, current["id"]))
            write_audit(cur, standard_id, current["id"], "ACTIVATE" if target == "ACTIVE" else "OBSOLETE", current["status"], target, payload.actor)
        conn.commit()
        return {"message": "Status updated."}
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); _db_error(exc)
    finally:
        cur.close(); conn.close()


@router.post("/standards/{standard_id}/copy", status_code=201)
def copy_standard(standard_id: int, payload: CopyStandardRequest):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_standard(cur, standard_id)
        source = current_revision_row(cur, standard_id)
        if not source:
            raise HTTPException(status_code=400, detail="Source standard has no revision to copy.")
        check_duplicate_code(cur, payload.new_standard_code)
        cur.execute("INSERT INTO quality_standards (standard_code, created_by) VALUES (%s,%s)", (payload.new_standard_code, payload.created_by))
        new_standard_id = cur.lastrowid
        new_revision_id = _insert_revision(cur, new_standard_id, 1, source, "ACTIVE", payload.created_by)
        _copy_characteristics(cur, source["id"], new_revision_id, payload.created_by)
        write_audit(cur, new_standard_id, new_revision_id, "CREATE", None, "ACTIVE", payload.created_by, remark=f"Copied from standard {standard_id}")
        conn.commit()
        return {"message": "Standard copied successfully.", "id": new_standard_id, "revision_id": new_revision_id}
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); _db_error(exc)
    finally:
        cur.close(); conn.close()


@router.delete("/standards/{standard_id}")
def delete_standard(standard_id: int):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_standard(cur, standard_id)
        cur.execute("""SELECT COUNT(*) total FROM quality_inspection_plans p
                       JOIN quality_standard_revisions r ON r.id=p.standard_revision_id WHERE r.standard_id=%s""", (standard_id,))
        if cur.fetchone()["total"]:
            raise HTTPException(status_code=409, detail="This standard is used by inspections; deactivate it instead of deleting.")
        # revisions first (their FK is RESTRICT); characteristics cascade, audit rows follow the standard
        cur.execute("DELETE FROM quality_standard_revisions WHERE standard_id=%s", (standard_id,))
        cur.execute("DELETE FROM quality_standards WHERE id=%s", (standard_id,))
        conn.commit()
        return {"message": "Quality standard deleted successfully."}
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); _db_error(exc)
    finally:
        cur.close(); conn.close()
