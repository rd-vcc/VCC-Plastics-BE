from fastapi import HTTPException


def get_category(cur, category_id: int):
    cur.execute("SELECT * FROM reason_code_categories WHERE id=%s", (category_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Reason code category not found.")
    return row


def validate_active_category(cur, category_id: int):
    category = get_category(cur, category_id)
    if category["status"] != "ACTIVE":
        raise HTTPException(status_code=400, detail="Reason code category is inactive.")
    return category


def check_duplicate_category_code(cur, value: str, exclude_id: int | None = None):
    sql = "SELECT id FROM reason_code_categories WHERE category_code=%s"
    params = [value]
    if exclude_id is not None:
        sql += " AND id<>%s"
        params.append(exclude_id)
    cur.execute(sql, tuple(params))
    if cur.fetchone():
        raise HTTPException(status_code=409, detail="Category code already exists.")


def get_reason_code(cur, reason_code_id: int):
    cur.execute("SELECT * FROM reason_codes WHERE id=%s", (reason_code_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Reason code not found.")
    return row


def check_duplicate_reason_code(cur, value: str, exclude_id: int | None = None):
    sql = "SELECT id FROM reason_codes WHERE reason_code=%s"
    params = [value]
    if exclude_id is not None:
        sql += " AND id<>%s"
        params.append(exclude_id)
    cur.execute(sql, tuple(params))
    if cur.fetchone():
        raise HTTPException(status_code=409, detail="Reason code already exists.")


def write_audit(cur, reason_code_id: int, action: str, from_status, to_status, actor: str | None, remark=None):
    cur.execute(
        """INSERT INTO reason_code_audit_log
           (reason_code_id, action, from_status, to_status, actor, remark)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (reason_code_id, action, from_status, to_status, actor or "SYSTEM", remark),
    )
