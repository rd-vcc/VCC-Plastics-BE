import json
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException


VALUE_COLUMNS = {
    "TEXT": "value_text",
    "LONG_TEXT": "value_text",
    "INTEGER": "value_number",
    "DECIMAL": "value_number",
    "BOOLEAN": "value_boolean",
    "DATE": "value_date",
    "DATETIME": "value_datetime",
    "SELECT": "value_text",
    "MULTI_SELECT": "value_json",
}


def get_node_type(cur, type_id: int, active_only: bool = False):
    sql = "SELECT * FROM mold_location_node_types WHERE id=%s"
    if active_only:
        sql += " AND is_active=1"
    cur.execute(sql, (type_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Mold location node type not found.")
    return row


def get_node(cur, node_id: int, active_only: bool = False):
    sql = "SELECT * FROM mold_location_nodes WHERE id=%s"
    if active_only:
        sql += " AND status='ACTIVE'"
    cur.execute(sql, (node_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Mold location node not found.")
    return row


def get_mold(cur, mold_id: int):
    cur.execute("SELECT * FROM molds WHERE id=%s", (mold_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Mold not found.")
    return row


def get_attribute(cur, attribute_id: int, active_only: bool = False):
    sql = "SELECT * FROM mold_attribute_definitions WHERE id=%s"
    if active_only:
        sql += " AND is_active=1"
    cur.execute(sql, (attribute_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Mold attribute definition not found.")
    return row


def check_duplicate(cur, table: str, column: str, value: str, exclude_id: int | None = None):
    allowed = {
        ("mold_location_node_types", "type_code"),
        ("mold_location_nodes", "node_code"),
        ("molds", "mold_code"),
        ("mold_attribute_definitions", "attribute_code"),
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


def is_node_descendant(cur, node_id: int, possible_parent_id: int) -> bool:
    current = possible_parent_id
    visited = set()
    while current is not None:
        if current == node_id or current in visited:
            return True
        visited.add(current)
        cur.execute("SELECT parent_id FROM mold_location_nodes WHERE id=%s", (current,))
        row = cur.fetchone()
        if not row:
            return False
        current = row["parent_id"]
    return False


def calculate_level(cur, parent_id: int | None) -> int:
    if parent_id is None:
        return 1
    return int(get_node(cur, parent_id, active_only=True)["level_no"]) + 1


def update_descendant_levels(cur, parent_id: int, parent_level: int):
    cur.execute("SELECT id FROM mold_location_nodes WHERE parent_id=%s", (parent_id,))
    for child in cur.fetchall():
        level = parent_level + 1
        cur.execute("UPDATE mold_location_nodes SET level_no=%s WHERE id=%s", (level, child["id"]))
        update_descendant_levels(cur, child["id"], level)


def normalize_value(field: dict, value: Any):
    if value is None or value == "" or value == []:
        return None
    data_type = field["data_type"]
    try:
        if data_type in {"TEXT", "LONG_TEXT", "SELECT"}:
            result: Any = str(value).strip()
        elif data_type == "INTEGER":
            if isinstance(value, bool):
                raise ValueError
            result = int(value)
        elif data_type == "DECIMAL":
            result = Decimal(str(value))
        elif data_type == "BOOLEAN":
            if isinstance(value, bool):
                result = value
            elif str(value).lower() in {"1", "true"}:
                result = True
            elif str(value).lower() in {"0", "false"}:
                result = False
            else:
                raise ValueError
        elif data_type == "DATE":
            result = value if isinstance(value, date) else date.fromisoformat(str(value))
        elif data_type == "DATETIME":
            result = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        elif data_type == "MULTI_SELECT":
            if not isinstance(value, list):
                raise ValueError
            result = [str(item) for item in value]
        else:
            raise ValueError
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid value for '{field['attribute_name']}'.") from exc

    options = field.get("select_options")
    if isinstance(options, str):
        options = json.loads(options)
    if data_type in {"SELECT", "MULTI_SELECT"}:
        allowed = {str(item.get("value")) for item in (options or [])}
        submitted = result if isinstance(result, list) else [result]
        if any(str(item) not in allowed for item in submitted):
            raise HTTPException(status_code=422, detail=f"Invalid option for '{field['attribute_name']}'.")

    rules = field.get("validation_rules") or {}
    if isinstance(rules, str):
        rules = json.loads(rules)
    if isinstance(result, str):
        if rules.get("min_length") is not None and len(result) < int(rules["min_length"]):
            raise HTTPException(status_code=422, detail=f"'{field['attribute_name']}' is too short.")
        if rules.get("max_length") is not None and len(result) > int(rules["max_length"]):
            raise HTTPException(status_code=422, detail=f"'{field['attribute_name']}' is too long.")
    if isinstance(result, (int, float, Decimal)) and not isinstance(result, bool):
        if rules.get("min") is not None and Decimal(str(result)) < Decimal(str(rules["min"])):
            raise HTTPException(status_code=422, detail=f"'{field['attribute_name']}' is below minimum.")
        if rules.get("max") is not None and Decimal(str(result)) > Decimal(str(rules["max"])):
            raise HTTPException(status_code=422, detail=f"'{field['attribute_name']}' exceeds maximum.")
    return result


def replace_attribute_values(cur, mold_id: int, values: list, actor: str | None):
    submitted = {item.attribute_definition_id: item.value for item in values}
    if len(submitted) != len(values):
        raise HTTPException(status_code=422, detail="Duplicate attribute_definition_id.")
    cur.execute("SELECT * FROM mold_attribute_definitions WHERE is_active=1 ORDER BY sort_order,id")
    fields = cur.fetchall()
    by_id = {row["id"]: row for row in fields}
    unknown = set(submitted) - set(by_id)
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown or inactive attributes: {sorted(unknown)}")
    for field in fields:
        raw = submitted.get(field["id"], field.get("default_value"))
        if field["is_required"] and (raw is None or raw == "" or raw == []):
            raise HTTPException(status_code=422, detail=f"'{field['attribute_name']}' is required.")
    cur.execute("DELETE FROM mold_attribute_values WHERE mold_id=%s", (mold_id,))
    for attribute_id, raw in submitted.items():
        field = by_id[attribute_id]
        value = normalize_value(field, raw)
        if value is None:
            continue
        columns = {key: None for key in set(VALUE_COLUMNS.values())}
        target = VALUE_COLUMNS[field["data_type"]]
        columns[target] = json.dumps(value, ensure_ascii=False) if target == "value_json" else value
        cur.execute(
            """INSERT INTO mold_attribute_values
               (mold_id,attribute_definition_id,value_text,value_number,value_boolean,
                value_date,value_datetime,value_json,created_by,updated_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (mold_id, attribute_id, columns["value_text"], columns["value_number"],
             columns["value_boolean"], columns["value_date"], columns["value_datetime"],
             columns["value_json"], actor, actor),
        )


def replace_products(cur, mold_id: int, products: list, actor: str | None):
    ids = [item.product_id for item in products]
    if len(set(ids)) != len(ids):
        raise HTTPException(status_code=422, detail="Duplicate product_id.")
    if sum(1 for item in products if item.is_primary) > 1:
        raise HTTPException(status_code=422, detail="Only one primary product is allowed per mold.")
    if ids:
        placeholders = ",".join(["%s"] * len(ids))
        cur.execute(f"SELECT id FROM products WHERE id IN ({placeholders})", tuple(ids))
        found = {row["id"] for row in cur.fetchall()}
        missing = set(ids) - found
        if missing:
            raise HTTPException(status_code=422, detail=f"Products not found: {sorted(missing)}")
    cur.execute("DELETE FROM mold_products WHERE mold_id=%s", (mold_id,))
    for item in products:
        cur.execute(
            """INSERT INTO mold_products
               (mold_id,product_id,cavity_quantity,is_primary,valid_from,valid_to,status,
                remark,created_by,updated_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (mold_id, item.product_id, item.cavity_quantity, item.is_primary,
             item.valid_from, item.valid_to, item.status, item.remark, actor, actor),
        )


def serialize_value(row: dict):
    data_type = row["data_type"]
    column = VALUE_COLUMNS[data_type]
    value = row.get(column)
    if column == "value_json" and isinstance(value, str):
        value = json.loads(value)
    if isinstance(value, Decimal):
        value = int(value) if data_type == "INTEGER" else float(value)
    return value
