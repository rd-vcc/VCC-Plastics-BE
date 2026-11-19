from fastapi import APIRouter, HTTPException, Query

from app.core.database import get_vcc_plastics_connection
from app.core.employee_names import attach_employee_names
from app.modules.master_data.process_parameter_standards.schemas import (
    CopyStandardRequest,
    RevisionUpdate,
    StandardCreate,
    StandardStatusUpdate,
)
from app.modules.master_data.process_parameter_standards.service import (
    check_duplicate_code,
    current_revision_row,
    get_standard,
    load_parameters,
    replace_parameters,
    write_audit,
)


router = APIRouter(prefix="/api/process-parameter-standard-master", tags=["Process Parameter Standard"])

_CURRENT_REVISION_ID_SUBQUERY = """
    (SELECT r.id FROM process_parameter_standard_revisions r
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
            SELECT
              COUNT(*) total_standards,
              SUM(cr.status='ACTIVE') active,
              SUM(cr.status<>'ACTIVE') inactive,
              COUNT(DISTINCT cr.product_id) products,
              COUNT(DISTINCT cr.process) processes,
              COUNT(DISTINCT cr.machine_type_id) machine_types
            FROM process_parameter_standards s
            JOIN process_parameter_standard_revisions cr ON cr.id = {_CURRENT_REVISION_ID_SUBQUERY}
        """)
        row = cur.fetchone()
        stats = {key: int(value) if value is not None else 0 for key, value in row.items()}
        cur.execute(f"""
            SELECT COUNT(*) total FROM process_parameter_standard_items i
            JOIN process_parameter_standard_revisions cr ON cr.id = i.revision_id
            JOIN process_parameter_standards s ON s.id = cr.standard_id
            WHERE cr.id = {_CURRENT_REVISION_ID_SUBQUERY}
        """)
        stats["parameter_items"] = cur.fetchone()["total"]
        return stats
    finally:
        cur.close(); conn.close()


@router.get("/standards")
def list_standards(
    keyword: str | None = None, product_id: int | None = None, mold_id: int | None = None,
    machine_type_id: int | None = None, material_id: int | None = None, process: str | None = None,
    status: str | None = None,
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
        if mold_id is not None:
            conditions.append("cr.mold_id=%s"); params.append(mold_id)
        if machine_type_id is not None:
            conditions.append("cr.machine_type_id=%s"); params.append(machine_type_id)
        if material_id is not None:
            conditions.append("cr.material_id=%s"); params.append(material_id)
        if process:
            conditions.append("cr.process=%s"); params.append(process)
        if status:
            conditions.append("cr.status=%s"); params.append(status.upper())
        where = " WHERE " + " AND ".join(conditions) if conditions else ""

        base = f"""
            FROM process_parameter_standards s
            JOIN process_parameter_standard_revisions cr ON cr.id = {_CURRENT_REVISION_ID_SUBQUERY}
            LEFT JOIN products p ON p.id = cr.product_id
            LEFT JOIN molds m ON m.id = cr.mold_id
            LEFT JOIN equipment_types t ON t.id = cr.machine_type_id
            LEFT JOIN materials mt ON mt.id = cr.material_id
            {where}
        """
        cur.execute(f"SELECT COUNT(*) total {base}", tuple(params))
        total = cur.fetchone()["total"]
        sql = f"""
            SELECT s.id, s.standard_code, cr.id AS revision_id, cr.revision_no, cr.standard_name,
                   cr.product_id, p.product_code, p.product_name,
                   cr.mold_id, m.mold_code, m.mold_name,
                   cr.machine_type_id, t.type_code, t.type_name,
                   cr.material_id, mt.material_code, mt.material_name,
                   cr.process, cr.status, cr.effective_date,
                   cr.submitted_by, cr.submitted_at, cr.approved_by, cr.approved_at,
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
            "SELECT * FROM process_parameter_standard_revisions WHERE standard_id=%s ORDER BY revision_no DESC",
            (standard_id,),
        )
        revisions = cur.fetchall()
        if revision_id is not None:
            revision = next((r for r in revisions if r["id"] == revision_id), None)
            if not revision:
                raise HTTPException(status_code=404, detail="Revision not found.")
        else:
            revision = current_revision_row(cur, standard_id)
        if revision:
            for column, table, key in (
                ("product_id", "products", "product_code, product_name"),
                ("mold_id", "molds", "mold_code, mold_name"),
                ("machine_type_id", "equipment_types", "type_code, type_name"),
                ("material_id", "materials", "material_code, material_name"),
            ):
                if revision.get(column):
                    cur.execute(f"SELECT {key} FROM {table} WHERE id=%s", (revision[column],))
                    extra = cur.fetchone()
                    if extra:
                        revision = {**revision, **extra}
        parameters = load_parameters(cur, revision["id"]) if revision else []
        if revision:
            revision = attach_employee_names(cur, [revision], ["created_by", "updated_by"])[0]
        return {**standard, "revisions": revisions, "revision": revision, "parameters": parameters}
    finally:
        cur.close(); conn.close()


@router.get("/standards/{standard_id}/audit-log")
def audit_log(standard_id: int):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_standard(cur, standard_id)
        cur.execute(
            "SELECT * FROM process_parameter_standard_audit_log WHERE standard_id=%s ORDER BY acted_at DESC, id DESC",
            (standard_id,),
        )
        return attach_employee_names(cur, cur.fetchall(), ["actor"])
    finally:
        cur.close(); conn.close()


# ---------------------------------------------------------------------------
# Writes. A process parameter standard is configuration: it takes effect as soon as it is saved
# (no submit / approve / activate). Machine monitoring reads the active standard live and keeps no
# reference to it, so edits are applied in place; every change is recorded in the audit log.
# ---------------------------------------------------------------------------

def _insert_revision(cur, standard_id: int, data, actor):
    cur.execute(
        """INSERT INTO process_parameter_standard_revisions
           (standard_id, revision_no, standard_name, product_id, mold_id, machine_type_id, material_id,
            process, effective_date, remark, status, created_by)
           VALUES (%s,1,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s)""",
        (standard_id, data["standard_name"], data["product_id"], data["mold_id"], data["machine_type_id"],
         data["material_id"], data["process"], data["effective_date"], data["remark"], actor),
    )
    return cur.lastrowid


@router.post("/standards", status_code=201)
def create_standard(payload: StandardCreate):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        check_duplicate_code(cur, payload.standard_code)
        cur.execute("INSERT INTO process_parameter_standards (standard_code, created_by) VALUES (%s,%s)", (payload.standard_code, payload.created_by))
        standard_id = cur.lastrowid
        revision_id = _insert_revision(cur, standard_id, payload.model_dump(), payload.created_by)
        replace_parameters(cur, revision_id, payload.parameters, payload.created_by)
        write_audit(cur, standard_id, revision_id, "CREATE", None, "ACTIVE", payload.created_by)
        conn.commit()
        return {"message": "Process parameter standard created successfully.", "id": standard_id, "revision_id": revision_id}
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
        cur.execute(
            """UPDATE process_parameter_standard_revisions
               SET standard_name=%s, product_id=%s, mold_id=%s, machine_type_id=%s, material_id=%s,
                   process=%s, effective_date=%s, remark=%s, updated_by=%s, version=version+1
               WHERE id=%s AND version=%s""",
            (payload.standard_name, payload.product_id, payload.mold_id, payload.machine_type_id, payload.material_id,
             payload.process, payload.effective_date, payload.remark, payload.updated_by, current["id"], payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="This standard was changed by another user. Reload and try again.")
        replace_parameters(cur, current["id"], payload.parameters, payload.updated_by)
        write_audit(cur, standard_id, current["id"], "UPDATE", current["status"], current["status"], payload.updated_by)
        conn.commit()
        return {"message": "Process parameter standard updated successfully."}
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
            cur.execute("UPDATE process_parameter_standard_revisions SET status=%s, updated_by=%s, version=version+1 WHERE id=%s",
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
        cur.execute("INSERT INTO process_parameter_standards (standard_code, created_by) VALUES (%s,%s)", (payload.new_standard_code, payload.created_by))
        new_standard_id = cur.lastrowid
        new_revision_id = _insert_revision(cur, new_standard_id, source, payload.created_by)
        parameters = load_parameters(cur, source["id"])
        if parameters:
            cur.executemany(
                """INSERT INTO process_parameter_standard_items
                   (revision_id, parameter_name, min_value, target_value, max_value, unit, sort_order, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                [(new_revision_id, p["parameter_name"], p["min_value"], p["target_value"], p["max_value"], p["unit"], p["sort_order"], payload.created_by)
                 for p in parameters],
            )
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
        # revisions first (their FK is RESTRICT); items cascade, audit rows follow the standard
        cur.execute("DELETE FROM process_parameter_standard_revisions WHERE standard_id=%s", (standard_id,))
        cur.execute("DELETE FROM process_parameter_standards WHERE id=%s", (standard_id,))
        conn.commit()
        return {"message": "Process parameter standard deleted successfully."}
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); _db_error(exc)
    finally:
        cur.close(); conn.close()
