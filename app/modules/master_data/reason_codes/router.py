from datetime import date, timedelta

from fastapi import APIRouter, HTTPException, Query

from app.core.database import get_vcc_plastics_connection
from app.modules.master_data.reason_codes.schemas import (
    ActionRequest,
    CategoryCreate,
    CategoryStatusUpdate,
    CategoryUpdate,
    CopyReasonCodeRequest,
    ReasonCodeCreate,
    ReasonCodeStatusUpdate,
    ReasonCodeUpdate,
    RejectRequest,
)
from app.modules.master_data.reason_codes.service import (
    check_duplicate_category_code,
    check_duplicate_reason_code,
    get_category,
    get_reason_code,
    validate_active_category,
    write_audit,
)


router = APIRouter(prefix="/api/reason-code-master", tags=["Reason Code Master"])


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


# =========================================================
# STATISTICS
# =========================================================


@router.get("/statistics")
def statistics():
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT COUNT(*) total, SUM(status='ACTIVE') active FROM reason_codes")
        row = cur.fetchone()
        total_codes = int(row["total"] or 0)
        active_codes = int(row["active"] or 0)

        cur.execute("SELECT COUNT(*) total FROM reason_code_categories WHERE status='ACTIVE'")
        category_count = cur.fetchone()["total"]

        thirty_days_ago = date.today() - timedelta(days=30)
        cur.execute("SELECT COUNT(*) total FROM reason_codes WHERE created_at >= %s", (thirty_days_ago,))
        recently_added = cur.fetchone()["total"]

        usage_percent = round((active_codes / total_codes) * 100, 1) if total_codes else 0.0

        return {
            "total_codes": total_codes,
            "active_codes": active_codes,
            "category_count": category_count,
            "usage_percent": usage_percent,
            "recently_added": recently_added,
        }
    finally:
        cur.close(); conn.close()


# =========================================================
# CATEGORIES
# =========================================================


@router.get("/categories")
def list_categories():
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT c.*, COUNT(r.id) AS reason_code_count
               FROM reason_code_categories c
               LEFT JOIN reason_codes r ON r.category_id = c.id
               GROUP BY c.id
               ORDER BY c.sort_order, c.category_name"""
        )
        return cur.fetchall()
    finally:
        cur.close(); conn.close()


@router.post("/categories", status_code=201)
def create_category(payload: CategoryCreate):
    def action(cur):
        check_duplicate_category_code(cur, payload.category_code)
        cur.execute(
            """INSERT INTO reason_code_categories
               (category_code, category_name, description, sort_order, status, created_by)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (payload.category_code, payload.category_name, payload.description, payload.sort_order,
             payload.status, payload.created_by),
        )
        return {"message": "Category created successfully.", "id": cur.lastrowid}
    return _run_write(action)


@router.put("/categories/{category_id}")
def update_category(category_id: int, payload: CategoryUpdate):
    def action(cur):
        get_category(cur, category_id)
        check_duplicate_category_code(cur, payload.category_code, category_id)
        cur.execute(
            """UPDATE reason_code_categories SET category_code=%s, category_name=%s, description=%s,
               sort_order=%s, status=%s, updated_by=%s, version=version+1
               WHERE id=%s AND version=%s""",
            (payload.category_code, payload.category_name, payload.description, payload.sort_order,
             payload.status, payload.updated_by, category_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="This category was changed by another user. Reload and try again.")
        return {"message": "Category updated successfully.", "version": payload.version + 1}
    return _run_write(action)


@router.patch("/categories/{category_id}/status")
def update_category_status(category_id: int, payload: CategoryStatusUpdate):
    def action(cur):
        get_category(cur, category_id)
        cur.execute(
            "UPDATE reason_code_categories SET status=%s, updated_by=%s, version=version+1 WHERE id=%s AND version=%s",
            (payload.status, payload.updated_by, category_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="This category was changed by another user. Reload and try again.")
        return {"message": "Category status updated successfully.", "version": payload.version + 1}
    return _run_write(action)


@router.delete("/categories/{category_id}")
def delete_category(category_id: int):
    def action(cur):
        get_category(cur, category_id)
        cur.execute("SELECT COUNT(*) total FROM reason_codes WHERE category_id=%s", (category_id,))
        if cur.fetchone()["total"] > 0:
            raise HTTPException(status_code=409, detail="This category has reason codes assigned; set it Inactive instead of deleting.")
        cur.execute("DELETE FROM reason_code_categories WHERE id=%s", (category_id,))
        return {"message": "Category deleted successfully."}
    return _run_write(action)


# =========================================================
# REASON CODES
# =========================================================


@router.get("/reason-codes")
def list_reason_codes(
    keyword: str | None = None, category_id: int | None = None, code_type: str | None = None,
    applicable_module: str | None = None, status: str | None = None,
    page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=500),
):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        conditions, params = [], []
        if keyword:
            conditions.append("(r.reason_code LIKE %s OR r.reason_name LIKE %s)")
            value = f"%{keyword.strip()}%"; params += [value, value]
        if category_id is not None:
            conditions.append("r.category_id=%s"); params.append(category_id)
        if code_type:
            conditions.append("r.code_type=%s"); params.append(code_type.upper())
        if applicable_module:
            conditions.append("r.applicable_module=%s"); params.append(applicable_module.upper())
        if status:
            conditions.append("r.status=%s"); params.append(status.upper())
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        base = f"""
            FROM reason_codes r
            LEFT JOIN reason_code_categories c ON c.id = r.category_id
            {where}
        """
        cur.execute(f"SELECT COUNT(*) total {base}", tuple(params))
        total = cur.fetchone()["total"]
        sql = f"""
            SELECT r.*, c.category_code, c.category_name
            {base}
            ORDER BY r.sort_order, r.reason_code
            LIMIT %s OFFSET %s
        """
        cur.execute(sql, tuple(params + [page_size, (page - 1) * page_size]))
        return {"items": cur.fetchall(), "total": total, "page": page, "page_size": page_size}
    finally:
        cur.close(); conn.close()


@router.get("/reason-codes/{reason_code_id}")
def reason_code_detail(reason_code_id: int):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT r.*, c.category_code, c.category_name
               FROM reason_codes r LEFT JOIN reason_code_categories c ON c.id = r.category_id
               WHERE r.id=%s""",
            (reason_code_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Reason code not found.")
        return row
    finally:
        cur.close(); conn.close()


@router.get("/reason-codes/{reason_code_id}/audit-log")
def reason_code_audit_log(reason_code_id: int):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_reason_code(cur, reason_code_id)
        cur.execute(
            "SELECT * FROM reason_code_audit_log WHERE reason_code_id=%s ORDER BY acted_at DESC, id DESC",
            (reason_code_id,),
        )
        return cur.fetchall()
    finally:
        cur.close(); conn.close()


@router.post("/reason-codes", status_code=201)
def create_reason_code(payload: ReasonCodeCreate):
    def action(cur):
        check_duplicate_reason_code(cur, payload.reason_code)
        validate_active_category(cur, payload.category_id)
        cur.execute(
            """INSERT INTO reason_codes
               (reason_code, reason_name, category_id, code_type, applicable_module, description,
                impact_level, sort_order, status, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'DRAFT',%s)""",
            (payload.reason_code, payload.reason_name, payload.category_id, payload.code_type,
             payload.applicable_module, payload.description, payload.impact_level, payload.sort_order,
             payload.created_by),
        )
        reason_code_id = cur.lastrowid
        write_audit(cur, reason_code_id, "CREATE", None, "DRAFT", payload.created_by)
        return {"message": "Reason code created successfully.", "id": reason_code_id}
    return _run_write(action)


@router.put("/reason-codes/{reason_code_id}")
def update_reason_code(reason_code_id: int, payload: ReasonCodeUpdate):
    def action(cur):
        current = get_reason_code(cur, reason_code_id)
        if current["status"] != "DRAFT":
            raise HTTPException(status_code=409, detail="Only reason codes in Draft status can be edited.")
        check_duplicate_reason_code(cur, payload.reason_code, reason_code_id)
        validate_active_category(cur, payload.category_id)
        cur.execute(
            """UPDATE reason_codes SET reason_code=%s, reason_name=%s, category_id=%s, code_type=%s,
               applicable_module=%s, description=%s, impact_level=%s, sort_order=%s,
               updated_by=%s, version=version+1
               WHERE id=%s AND version=%s""",
            (payload.reason_code, payload.reason_name, payload.category_id, payload.code_type,
             payload.applicable_module, payload.description, payload.impact_level, payload.sort_order,
             payload.updated_by, reason_code_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="This reason code was changed by another user. Reload and try again.")
        write_audit(cur, reason_code_id, "UPDATE", "DRAFT", "DRAFT", payload.updated_by)
        return {"message": "Reason code updated successfully.", "version": payload.version + 1}
    return _run_write(action)


@router.post("/reason-codes/{reason_code_id}/submit")
def submit_reason_code(reason_code_id: int, payload: ActionRequest):
    def action(cur):
        current = get_reason_code(cur, reason_code_id)
        if current["status"] != "DRAFT":
            raise HTTPException(status_code=409, detail="Only reason codes in Draft status can be submitted for approval.")
        cur.execute(
            """UPDATE reason_codes SET status='PENDING_APPROVAL', submitted_by=%s, submitted_at=NOW(),
               updated_by=%s, version=version+1 WHERE id=%s AND version=%s""",
            (payload.actor, payload.actor, reason_code_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="This reason code was changed by another user. Reload and try again.")
        write_audit(cur, reason_code_id, "SUBMIT", "DRAFT", "PENDING_APPROVAL", payload.actor, payload.remark)
        return {"message": "Reason code submitted for approval.", "version": payload.version + 1}
    return _run_write(action)


@router.post("/reason-codes/{reason_code_id}/approve")
def approve_reason_code(reason_code_id: int, payload: ActionRequest):
    def action(cur):
        current = get_reason_code(cur, reason_code_id)
        if current["status"] != "PENDING_APPROVAL":
            raise HTTPException(status_code=409, detail="Only reason codes pending approval can be approved.")
        cur.execute(
            """UPDATE reason_codes SET status='ACTIVE', approved_by=%s, approved_at=NOW(),
               updated_by=%s, version=version+1 WHERE id=%s AND version=%s""",
            (payload.actor, payload.actor, reason_code_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="This reason code was changed by another user. Reload and try again.")
        write_audit(cur, reason_code_id, "APPROVE", "PENDING_APPROVAL", "ACTIVE", payload.actor, payload.remark)
        return {"message": "Reason code approved and released for use.", "version": payload.version + 1}
    return _run_write(action)


@router.post("/reason-codes/{reason_code_id}/reject")
def reject_reason_code(reason_code_id: int, payload: RejectRequest):
    def action(cur):
        current = get_reason_code(cur, reason_code_id)
        if current["status"] != "PENDING_APPROVAL":
            raise HTTPException(status_code=409, detail="Only reason codes pending approval can be rejected.")
        cur.execute(
            "UPDATE reason_codes SET status='DRAFT', updated_by=%s, version=version+1 WHERE id=%s AND version=%s",
            (payload.actor, reason_code_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="This reason code was changed by another user. Reload and try again.")
        write_audit(cur, reason_code_id, "REJECT", "PENDING_APPROVAL", "DRAFT", payload.actor, payload.remark)
        return {"message": "Reason code rejected and returned to Draft.", "version": payload.version + 1}
    return _run_write(action)


@router.patch("/reason-codes/{reason_code_id}/status")
def update_reason_code_status(reason_code_id: int, payload: ReasonCodeStatusUpdate):
    def action(cur):
        current = get_reason_code(cur, reason_code_id)
        if current["status"] not in {"ACTIVE", "INACTIVE"}:
            raise HTTPException(status_code=409, detail="Only Active or Inactive reason codes can be toggled directly.")
        cur.execute(
            "UPDATE reason_codes SET status=%s, updated_by=%s, version=version+1 WHERE id=%s AND version=%s",
            (payload.status, payload.updated_by, reason_code_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="This reason code was changed by another user. Reload and try again.")
        write_audit(cur, reason_code_id, "ACTIVATE" if payload.status == "ACTIVE" else "DEACTIVATE",
                    current["status"], payload.status, payload.updated_by)
        return {"message": "Reason code status updated successfully.", "version": payload.version + 1}
    return _run_write(action)


@router.post("/reason-codes/{reason_code_id}/copy", status_code=201)
def copy_reason_code(reason_code_id: int, payload: CopyReasonCodeRequest):
    def action(cur):
        source = get_reason_code(cur, reason_code_id)
        check_duplicate_reason_code(cur, payload.new_reason_code)
        cur.execute(
            """INSERT INTO reason_codes
               (reason_code, reason_name, category_id, code_type, applicable_module, description,
                impact_level, sort_order, status, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,'DRAFT',%s)""",
            (payload.new_reason_code, f"{source['reason_name']} (Copy)", source["category_id"],
             source["code_type"], source["applicable_module"], source["description"],
             source["impact_level"], source["sort_order"], payload.created_by),
        )
        new_id = cur.lastrowid
        write_audit(cur, new_id, "CREATE", None, "DRAFT", payload.created_by, remark=f"Copied from reason code {reason_code_id}")
        return {"message": "Reason code copied successfully.", "id": new_id}
    return _run_write(action)


@router.delete("/reason-codes/{reason_code_id}")
def delete_reason_code(reason_code_id: int):
    def action(cur):
        get_reason_code(cur, reason_code_id)
        cur.execute("SELECT COUNT(*) total FROM reason_code_audit_log WHERE reason_code_id=%s", (reason_code_id,))
        if cur.fetchone()["total"] > 1:
            raise HTTPException(status_code=409, detail="This reason code has activity; set it Inactive instead of deleting.")
        cur.execute("DELETE FROM reason_codes WHERE id=%s", (reason_code_id,))
        return {"message": "Reason code deleted successfully."}
    return _run_write(action)
