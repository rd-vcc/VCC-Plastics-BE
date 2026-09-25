from fastapi import HTTPException


def get_standard(cur, standard_id: int):
    cur.execute("SELECT * FROM quality_standards WHERE id=%s", (standard_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Quality standard not found.")
    return row


def get_revision(cur, revision_id: int, standard_id: int | None = None):
    cur.execute("SELECT * FROM quality_standard_revisions WHERE id=%s", (revision_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Revision not found.")
    if standard_id is not None and row["standard_id"] != standard_id:
        raise HTTPException(status_code=400, detail="Revision does not belong to this standard.")
    return row


def check_duplicate_code(cur, value: str, exclude_id: int | None = None):
    sql = "SELECT id FROM quality_standards WHERE standard_code=%s"
    params = [value]
    if exclude_id is not None:
        sql += " AND id<>%s"
        params.append(exclude_id)
    cur.execute(sql, tuple(params))
    if cur.fetchone():
        raise HTTPException(status_code=409, detail="Standard code already exists.")


def current_revision_row(cur, standard_id: int):
    cur.execute(
        "SELECT * FROM quality_standard_revisions WHERE standard_id=%s AND status='ACTIVE' LIMIT 1",
        (standard_id,),
    )
    row = cur.fetchone()
    if row:
        return row
    cur.execute(
        "SELECT * FROM quality_standard_revisions WHERE standard_id=%s ORDER BY revision_no DESC LIMIT 1",
        (standard_id,),
    )
    return cur.fetchone()


def write_audit(cur, standard_id: int, revision_id, action: str, from_status, to_status, actor: str | None, remark=None):
    cur.execute(
        """INSERT INTO quality_standard_audit_log
           (standard_id, revision_id, action, from_status, to_status, actor, remark)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (standard_id, revision_id, action, from_status, to_status, actor or "SYSTEM", remark),
    )


def replace_characteristics(cur, revision_id: int, characteristics, actor: str | None):
    cur.execute("DELETE FROM quality_standard_characteristics WHERE revision_id=%s", (revision_id,))
    for item in characteristics:
        cur.execute(
            """INSERT INTO quality_standard_characteristics
               (revision_id, characteristic_name, nominal_value, lsl, usl, unit, inspection_method,
                equipment_type_id, inspection_frequency, characteristic_type, is_spc, chart_type, subgroup_size,
                cpk_target, is_critical, sort_order, created_by, updated_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (revision_id, item.characteristic_name, item.nominal_value, item.lsl, item.usl, item.unit,
             item.inspection_method, item.equipment_type_id, item.inspection_frequency, item.characteristic_type,
             int(item.is_spc), item.chart_type, item.subgroup_size, item.cpk_target, int(item.is_critical), item.sort_order,
             actor, actor),
        )


def load_characteristics(cur, revision_id: int):
    cur.execute(
        """SELECT c.*, t.type_name AS equipment_type_name
           FROM quality_standard_characteristics c
           LEFT JOIN measuring_equipment_types t ON t.id = c.equipment_type_id
           WHERE c.revision_id=%s ORDER BY c.sort_order, c.id""",
        (revision_id,),
    )
    return cur.fetchall()
