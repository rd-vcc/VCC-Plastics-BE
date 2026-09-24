from fastapi import HTTPException


def get_record(cur, table: str, record_id: int, label: str):
    allowed_tables = {
        "measuring_equipment_types",
        "measuring_equipment",
    }
    if table not in allowed_tables:
        raise ValueError("Invalid table.")
    cur.execute(f"SELECT * FROM {table} WHERE id = %s", (record_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail=f"{label} not found.")
    return row


def ensure_unique(cur, table: str, column: str, value, exclude_id=None):
    allowed = {
        ("measuring_equipment_types", "type_code"),
        ("measuring_equipment", "equipment_code"),
    }
    if (table, column) not in allowed:
        raise ValueError("Invalid uniqueness check.")
    sql = f"SELECT id FROM {table} WHERE {column} = %s"
    params = [value]
    if exclude_id is not None:
        sql += " AND id <> %s"
        params.append(exclude_id)
    cur.execute(sql, tuple(params))
    if cur.fetchone():
        raise HTTPException(status_code=409, detail=f"{column} already exists.")


def validate_factory_node(cur, node_id: int):
    cur.execute(
        "SELECT id, code, name, status FROM factory_structure_nodes WHERE id = %s",
        (node_id,),
    )
    node = cur.fetchone()
    if not node:
        raise HTTPException(status_code=400, detail="Factory structure node not found.")
    if node["status"] != "ACTIVE":
        raise HTTPException(status_code=400, detail="Factory structure node is inactive.")
    return node
