from fastapi import HTTPException


def get_standard(cur, standard_id: int):
    cur.execute("SELECT * FROM process_parameter_standards WHERE id=%s", (standard_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Process parameter standard not found.")
    return row


def get_revision(cur, revision_id: int, standard_id: int | None = None):
    cur.execute("SELECT * FROM process_parameter_standard_revisions WHERE id=%s", (revision_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Revision not found.")
    if standard_id is not None and row["standard_id"] != standard_id:
        raise HTTPException(status_code=400, detail="Revision does not belong to this standard.")
    return row


def check_duplicate_code(cur, value: str, exclude_id: int | None = None):
    sql = "SELECT id FROM process_parameter_standards WHERE standard_code=%s"
    params = [value]
    if exclude_id is not None:
        sql += " AND id<>%s"
        params.append(exclude_id)
    cur.execute(sql, tuple(params))
    if cur.fetchone():
        raise HTTPException(status_code=409, detail="Standard code already exists.")


def current_revision_row(cur, standard_id: int):
    cur.execute(
        "SELECT * FROM process_parameter_standard_revisions WHERE standard_id=%s AND status='ACTIVE' LIMIT 1",
        (standard_id,),
    )
    row = cur.fetchone()
    if row:
        return row
    cur.execute(
        "SELECT * FROM process_parameter_standard_revisions WHERE standard_id=%s ORDER BY revision_no DESC LIMIT 1",
        (standard_id,),
    )
    return cur.fetchone()


def write_audit(cur, standard_id: int, revision_id, action: str, from_status, to_status, actor: str | None, remark=None):
    cur.execute(
        """INSERT INTO process_parameter_standard_audit_log
           (standard_id, revision_id, action, from_status, to_status, actor, remark)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (standard_id, revision_id, action, from_status, to_status, actor or "SYSTEM", remark),
    )


def replace_parameters(cur, revision_id: int, parameters, actor: str | None):
    cur.execute("DELETE FROM process_parameter_standard_items WHERE revision_id=%s", (revision_id,))
    for item in parameters:
        cur.execute(
            """INSERT INTO process_parameter_standard_items
               (revision_id, parameter_name, min_value, target_value, max_value, unit, sort_order,
                created_by, updated_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (revision_id, item.parameter_name, item.min_value, item.target_value, item.max_value,
             item.unit, item.sort_order, actor, actor),
        )


def load_parameters(cur, revision_id: int):
    cur.execute(
        "SELECT * FROM process_parameter_standard_items WHERE revision_id=%s ORDER BY sort_order, id",
        (revision_id,),
    )
    return cur.fetchall()
