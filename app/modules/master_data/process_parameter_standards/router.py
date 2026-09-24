from fastapi import APIRouter, HTTPException, Query

from app.core.database import get_vcc_plastics_connection
from app.modules.master_data.process_parameter_standards.schemas import (
    ActionRequest,
    CopyStandardRequest,
    NewRevisionCreate,
    RejectRequest,
    RevisionUpdate,
    StandardCreate,
)
from app.modules.master_data.process_parameter_standards.service import (
    check_duplicate_code,
    current_revision_row,
    get_revision,
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
        return cur.fetchall()
    finally:
        cur.close(); conn.close()


@router.post("/standards", status_code=201)
def create_standard(payload: StandardCreate):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        check_duplicate_code(cur, payload.standard_code)
        cur.execute(
            "INSERT INTO process_parameter_standards (standard_code, created_by) VALUES (%s,%s)",
            (payload.standard_code, payload.created_by),
        )
        standard_id = cur.lastrowid
        cur.execute(
            """INSERT INTO process_parameter_standard_revisions
               (standard_id, revision_no, standard_name, product_id, mold_id, machine_type_id, material_id,
                process, effective_date, remark, status, created_by)
               VALUES (%s,1,%s,%s,%s,%s,%s,%s,%s,%s,'DRAFT',%s)""",
            (standard_id, payload.standard_name, payload.product_id, payload.mold_id, payload.machine_type_id,
             payload.material_id, payload.process, payload.effective_date, payload.remark, payload.created_by),
        )
        revision_id = cur.lastrowid
        replace_parameters(cur, revision_id, payload.parameters, payload.created_by)
        write_audit(cur, standard_id, revision_id, "CREATE", None, "DRAFT", payload.created_by)
        conn.commit()
        return {"message": "Process parameter standard created successfully.", "id": standard_id, "revision_id": revision_id}
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); _db_error(exc)
    finally:
        cur.close(); conn.close()


@router.put("/standards/{standard_id}/revisions/{revision_id}")
def update_revision(standard_id: int, revision_id: int, payload: RevisionUpdate):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_standard(cur, standard_id)
        revision = get_revision(cur, revision_id, standard_id)
        if revision["status"] != "DRAFT":
            raise HTTPException(status_code=409, detail="Only a Draft revision can be edited. Create a new revision instead.")
        cur.execute(
            """UPDATE process_parameter_standard_revisions
               SET standard_name=%s, product_id=%s, mold_id=%s, machine_type_id=%s, material_id=%s,
                   process=%s, effective_date=%s, remark=%s, updated_by=%s, version=version+1
               WHERE id=%s AND version=%s""",
            (payload.standard_name, payload.product_id, payload.mold_id, payload.machine_type_id,
             payload.material_id, payload.process, payload.effective_date, payload.remark,
             payload.updated_by, revision_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="This revision was changed by another user. Reload and try again.")
        replace_parameters(cur, revision_id, payload.parameters, payload.updated_by)
        write_audit(cur, standard_id, revision_id, "UPDATE", "DRAFT", "DRAFT", payload.updated_by)
        conn.commit()
        return {"message": "Revision updated successfully.", "version": payload.version + 1}
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); _db_error(exc)
    finally:
        cur.close(); conn.close()


@router.post("/standards/{standard_id}/revisions", status_code=201)
def create_new_revision(standard_id: int, payload: NewRevisionCreate):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_standard(cur, standard_id)
        source = get_revision(cur, payload.source_revision_id, standard_id)
        cur.execute(
            "SELECT COUNT(*) total FROM process_parameter_standard_revisions WHERE standard_id=%s AND status IN ('DRAFT','PENDING_APPROVAL')",
            (standard_id,),
        )
        if cur.fetchone()["total"]:
            raise HTTPException(status_code=409, detail="This standard already has an open Draft or Pending Approval revision.")
        cur.execute("SELECT COALESCE(MAX(revision_no),0) AS max_no FROM process_parameter_standard_revisions WHERE standard_id=%s", (standard_id,))
        next_no = cur.fetchone()["max_no"] + 1
        cur.execute(
            """INSERT INTO process_parameter_standard_revisions
               (standard_id, revision_no, standard_name, product_id, mold_id, machine_type_id, material_id,
                process, effective_date, remark, status, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'DRAFT',%s)""",
            (standard_id, next_no, source["standard_name"], source["product_id"], source["mold_id"],
             source["machine_type_id"], source["material_id"], source["process"], source["effective_date"],
             source["remark"], payload.created_by),
        )
        new_revision_id = cur.lastrowid
        parameters = load_parameters(cur, source["id"])
        cur.executemany(
            """INSERT INTO process_parameter_standard_items
               (revision_id, parameter_name, min_value, target_value, max_value, unit, sort_order, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            [
                (new_revision_id, p["parameter_name"], p["min_value"], p["target_value"], p["max_value"],
                 p["unit"], p["sort_order"], payload.created_by)
                for p in parameters
            ],
        ) if parameters else None
        write_audit(cur, standard_id, new_revision_id, "NEW_REVISION", source["status"], "DRAFT", payload.created_by,
                    remark=f"Copied from revision {source['revision_no']}")
        conn.commit()
        return {"message": "New revision created successfully.", "id": new_revision_id, "revision_no": next_no}
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); _db_error(exc)
    finally:
        cur.close(); conn.close()


@router.post("/standards/{standard_id}/revisions/{revision_id}/submit")
def submit_revision(standard_id: int, revision_id: int, payload: ActionRequest):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_standard(cur, standard_id)
        revision = get_revision(cur, revision_id, standard_id)
        if revision["status"] != "DRAFT":
            raise HTTPException(status_code=409, detail="Only a Draft revision can be submitted for approval.")
        cur.execute("SELECT COUNT(*) total FROM process_parameter_standard_items WHERE revision_id=%s", (revision_id,))
        if not cur.fetchone()["total"]:
            raise HTTPException(status_code=400, detail="Add at least one parameter item before submitting.")
        cur.execute(
            """UPDATE process_parameter_standard_revisions
               SET status='PENDING_APPROVAL', submitted_by=%s, submitted_at=NOW(), version=version+1
               WHERE id=%s""",
            (payload.actor, revision_id),
        )
        write_audit(cur, standard_id, revision_id, "SUBMIT", "DRAFT", "PENDING_APPROVAL", payload.actor, payload.remark)
        conn.commit()
        return {"message": "Submitted for approval."}
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); _db_error(exc)
    finally:
        cur.close(); conn.close()


@router.post("/standards/{standard_id}/revisions/{revision_id}/approve")
def approve_revision(standard_id: int, revision_id: int, payload: ActionRequest):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_standard(cur, standard_id)
        revision = get_revision(cur, revision_id, standard_id)
        if revision["status"] != "PENDING_APPROVAL":
            raise HTTPException(status_code=409, detail="Only a revision Pending Approval can be approved.")
        cur.execute(
            """UPDATE process_parameter_standard_revisions
               SET status='APPROVED', approved_by=%s, approved_at=NOW(), version=version+1
               WHERE id=%s""",
            (payload.actor, revision_id),
        )
        write_audit(cur, standard_id, revision_id, "APPROVE", "PENDING_APPROVAL", "APPROVED", payload.actor, payload.remark)
        conn.commit()
        return {"message": "Revision approved."}
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); _db_error(exc)
    finally:
        cur.close(); conn.close()


@router.post("/standards/{standard_id}/revisions/{revision_id}/reject")
def reject_revision(standard_id: int, revision_id: int, payload: RejectRequest):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_standard(cur, standard_id)
        revision = get_revision(cur, revision_id, standard_id)
        if revision["status"] != "PENDING_APPROVAL":
            raise HTTPException(status_code=409, detail="Only a revision Pending Approval can be rejected.")
        cur.execute(
            "UPDATE process_parameter_standard_revisions SET status='DRAFT', version=version+1 WHERE id=%s",
            (revision_id,),
        )
        write_audit(cur, standard_id, revision_id, "REJECT", "PENDING_APPROVAL", "DRAFT", payload.actor, payload.remark)
        conn.commit()
        return {"message": "Revision rejected and returned to Draft."}
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); _db_error(exc)
    finally:
        cur.close(); conn.close()


@router.post("/standards/{standard_id}/revisions/{revision_id}/activate")
def activate_revision(standard_id: int, revision_id: int, payload: ActionRequest):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_standard(cur, standard_id)
        revision = get_revision(cur, revision_id, standard_id)
        if revision["status"] != "APPROVED":
            raise HTTPException(status_code=409, detail="Only an Approved revision can be activated.")
        cur.execute(
            "SELECT id FROM process_parameter_standard_revisions WHERE standard_id=%s AND status='ACTIVE' AND id<>%s",
            (standard_id, revision_id),
        )
        previously_active = cur.fetchall()
        for row in previously_active:
            cur.execute(
                "UPDATE process_parameter_standard_revisions SET status='OBSOLETE', version=version+1 WHERE id=%s",
                (row["id"],),
            )
            write_audit(cur, standard_id, row["id"], "OBSOLETE", "ACTIVE", "OBSOLETE", payload.actor,
                        remark=f"Superseded by revision {revision['revision_no']}")
        cur.execute(
            "UPDATE process_parameter_standard_revisions SET status='ACTIVE', version=version+1 WHERE id=%s",
            (revision_id,),
        )
        write_audit(cur, standard_id, revision_id, "ACTIVATE", "APPROVED", "ACTIVE", payload.actor, payload.remark)
        conn.commit()
        return {"message": "Revision activated."}
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); _db_error(exc)
    finally:
        cur.close(); conn.close()


@router.post("/standards/{standard_id}/revisions/{revision_id}/obsolete")
def obsolete_revision(standard_id: int, revision_id: int, payload: ActionRequest):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_standard(cur, standard_id)
        revision = get_revision(cur, revision_id, standard_id)
        if revision["status"] != "ACTIVE":
            raise HTTPException(status_code=409, detail="Only an Active revision can be made Obsolete.")
        cur.execute(
            "UPDATE process_parameter_standard_revisions SET status='OBSOLETE', version=version+1 WHERE id=%s",
            (revision_id,),
        )
        write_audit(cur, standard_id, revision_id, "OBSOLETE", "ACTIVE", "OBSOLETE", payload.actor, payload.remark)
        conn.commit()
        return {"message": "Revision marked Obsolete."}
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
        cur.execute(
            "INSERT INTO process_parameter_standards (standard_code, created_by) VALUES (%s,%s)",
            (payload.new_standard_code, payload.created_by),
        )
        new_standard_id = cur.lastrowid
        cur.execute(
            """INSERT INTO process_parameter_standard_revisions
               (standard_id, revision_no, standard_name, product_id, mold_id, machine_type_id, material_id,
                process, effective_date, remark, status, created_by)
               VALUES (%s,1,%s,%s,%s,%s,%s,%s,%s,%s,'DRAFT',%s)""",
            (new_standard_id, source["standard_name"], source["product_id"], source["mold_id"],
             source["machine_type_id"], source["material_id"], source["process"], source["effective_date"],
             source["remark"], payload.created_by),
        )
        new_revision_id = cur.lastrowid
        parameters = load_parameters(cur, source["id"])
        cur.executemany(
            """INSERT INTO process_parameter_standard_items
               (revision_id, parameter_name, min_value, target_value, max_value, unit, sort_order, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            [
                (new_revision_id, p["parameter_name"], p["min_value"], p["target_value"], p["max_value"],
                 p["unit"], p["sort_order"], payload.created_by)
                for p in parameters
            ],
        ) if parameters else None
        write_audit(cur, new_standard_id, new_revision_id, "CREATE", None, "DRAFT", payload.created_by,
                    remark=f"Copied from standard {standard_id}")
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
        cur.execute("SELECT COUNT(*) total FROM process_parameter_standard_revisions WHERE standard_id=%s", (standard_id,))
        revision_count = cur.fetchone()["total"]
        cur.execute("SELECT COUNT(*) total FROM process_parameter_standard_audit_log WHERE standard_id=%s", (standard_id,))
        audit_count = cur.fetchone()["total"]
        cur.execute("SELECT status FROM process_parameter_standard_revisions WHERE standard_id=%s", (standard_id,))
        statuses = [row["status"] for row in cur.fetchall()]
        if revision_count != 1 or statuses != ["DRAFT"] or audit_count > 1:
            raise HTTPException(status_code=409, detail="This standard has activity; set it Obsolete instead of deleting.")
        cur.execute("DELETE FROM process_parameter_standards WHERE id=%s", (standard_id,))
        conn.commit()
        return {"message": "Process parameter standard deleted successfully."}
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); _db_error(exc)
    finally:
        cur.close(); conn.close()
