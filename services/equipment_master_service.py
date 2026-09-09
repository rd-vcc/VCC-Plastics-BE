from fastapi import HTTPException


def get_record(cur, table: str, record_id: int, label: str):
    allowed_tables = {
        "equipment_types",
        "equipment_groups",
        "equipment",
        "equipment_spec_definitions",
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
        ("equipment_types", "type_code"),
        ("equipment_groups", "group_code"),
        ("equipment", "equipment_code"),
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


def validate_group_for_type(cur, group_id: int | None, type_id: int):
    if group_id is None:
        return None
    group = get_record(cur, "equipment_groups", group_id, "Equipment group")
    if not group["is_active"]:
        raise HTTPException(status_code=400, detail="Equipment group is inactive.")
    if group["equipment_type_id"] not in (None, type_id):
        raise HTTPException(status_code=400, detail="Equipment group does not match equipment type.")
    return group


def validate_spec_value_type(definition: dict, payload):
    field_by_type = {
        "TEXT": "value_text",
        "INTEGER": "value_integer",
        "DECIMAL": "value_decimal",
        "BOOLEAN": "value_boolean",
        "DATE": "value_date",
    }
    expected = field_by_type[definition["data_type"]]
    supplied = next(
        name for name in field_by_type.values()
        if getattr(payload, name) is not None
    )
    if supplied != expected:
        raise HTTPException(
            status_code=400,
            detail=f"Specification {definition['spec_code']} requires {expected}.",
        )
