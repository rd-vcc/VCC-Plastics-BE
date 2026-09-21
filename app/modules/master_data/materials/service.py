from typing import Any

from fastapi import HTTPException


def get_node_type(cur, type_id: int, active_only: bool = False):
    sql = "SELECT * FROM material_node_types WHERE id=%s"
    if active_only:
        sql += " AND is_active=1"
    cur.execute(sql, (type_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Material node type not found.")
    return row


def get_node(cur, node_id: int, active_only: bool = False):
    sql = "SELECT * FROM material_nodes WHERE id=%s"
    if active_only:
        sql += " AND status='ACTIVE'"
    cur.execute(sql, (node_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Material node not found.")
    return row


def get_material(cur, material_id: int):
    cur.execute("SELECT * FROM materials WHERE id=%s", (material_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Material not found.")
    return row


def get_field_definition(cur, field_id: int):
    cur.execute("SELECT * FROM material_field_definitions WHERE id=%s", (field_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Material field definition not found.")
    return row


_VALUE_COLUMN_BY_TYPE = {
    "TEXT": "value_text",
    "INTEGER": "value_integer",
    "DECIMAL": "value_decimal",
    "BOOLEAN": "value_boolean",
    "DATE": "value_date",
}


def value_column_for(data_type: str) -> str:
    column = _VALUE_COLUMN_BY_TYPE.get(data_type)
    if not column:
        raise HTTPException(status_code=400, detail=f"Unsupported field data type: {data_type}.")
    return column


def serialize_field_value(row: dict) -> Any:
    column = _VALUE_COLUMN_BY_TYPE.get(row["data_type"])
    value = row.get(column) if column else None
    if row["data_type"] == "BOOLEAN" and value is not None:
        return bool(value)
    if row["data_type"] == "DATE" and value is not None:
        return value.isoformat() if hasattr(value, "isoformat") else value
    if row["data_type"] in {"INTEGER", "DECIMAL"} and value is not None:
        return float(value) if row["data_type"] == "DECIMAL" else int(value)
    return value


def check_duplicate(cur, table: str, column: str, value: str, exclude_id: int | None = None):
    allowed = {
        ("material_node_types", "code"),
        ("material_nodes", "code"),
        ("materials", "material_code"),
        ("material_field_definitions", "field_code"),
    }
    if (table, column) not in allowed:
        raise ValueError("Invalid duplicate-check target.")
    sql = f"SELECT id FROM {table} WHERE {column}=%s"
    params: list[Any] = [value]
    if exclude_id is not None:
        sql += " AND id<>%s"
        params.append(exclude_id)
    cur.execute(sql, tuple(params))
    if cur.fetchone():
        raise HTTPException(status_code=409, detail=f"{column} already exists.")


def is_node_type_descendant(cur, type_id: int, possible_parent_id: int) -> bool:
    current = possible_parent_id
    visited = set()
    while current is not None:
        if current == type_id or current in visited:
            return True
        visited.add(current)
        cur.execute("SELECT parent_type_id FROM material_node_types WHERE id=%s", (current,))
        row = cur.fetchone()
        if not row:
            return False
        current = row["parent_type_id"]
    return False


def is_node_descendant(cur, node_id: int, possible_parent_id: int) -> bool:
    current = possible_parent_id
    visited = set()
    while current is not None:
        if current == node_id or current in visited:
            return True
        visited.add(current)
        cur.execute("SELECT parent_id FROM material_nodes WHERE id=%s", (current,))
        row = cur.fetchone()
        if not row:
            return False
        current = row["parent_id"]
    return False


def calculate_level(cur, parent_id: int | None) -> int:
    if parent_id is None:
        return 0
    return int(get_node(cur, parent_id, active_only=True)["level_no"]) + 1


def update_descendant_levels(cur, parent_id: int, parent_level: int):
    cur.execute("SELECT id FROM material_nodes WHERE parent_id=%s", (parent_id,))
    for child in cur.fetchall():
        level = parent_level + 1
        cur.execute("UPDATE material_nodes SET level_no=%s WHERE id=%s", (level, child["id"]))
        update_descendant_levels(cur, child["id"], level)
