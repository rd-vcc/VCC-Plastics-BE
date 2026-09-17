import json
import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import HTTPException


VALUE_COLUMNS = {
    "TEXT": "value_text",
    "LONG_TEXT": "value_text",
    "INTEGER": "value_integer",
    "DECIMAL": "value_decimal",
    "DATE": "value_date",
    "DATETIME": "value_datetime",
    "BOOLEAN": "value_boolean",
    "SELECT": "value_text",
    "MULTI_SELECT": "value_json",
    "FILE": "value_json",
    "IMAGE": "value_json",
}


def get_family(cur, family_id: int, active_only: bool = False):
    sql = "SELECT * FROM product_families WHERE id = %s"
    params = [family_id]
    if active_only:
        sql += " AND status = 'ACTIVE'"
    cur.execute(sql, tuple(params))
    family = cur.fetchone()
    if not family:
        detail = "Product family not found."
        if active_only:
            detail = "Product family not found or inactive."
        raise HTTPException(status_code=404, detail=detail)
    return family


def get_product(cur, product_id: int):
    cur.execute("SELECT * FROM products WHERE id = %s", (product_id,))
    product = cur.fetchone()
    if not product:
        raise HTTPException(status_code=404, detail="Product not found.")
    return product


def get_field(cur, field_id: int, active_only: bool = False):
    sql = "SELECT * FROM product_field_definitions WHERE id = %s"
    params = [field_id]
    if active_only:
        sql += " AND is_active = 1"
    cur.execute(sql, tuple(params))
    field = cur.fetchone()
    if not field:
        detail = "Product field not found."
        if active_only:
            detail = "Product field not found or inactive."
        raise HTTPException(status_code=404, detail=detail)
    return field


def check_duplicate_family_code(cur, code: str, exclude_id: int | None = None):
    sql = "SELECT id FROM product_families WHERE family_code = %s"
    params: list[Any] = [code]
    if exclude_id is not None:
        sql += " AND id <> %s"
        params.append(exclude_id)
    cur.execute(sql, tuple(params))
    if cur.fetchone():
        raise HTTPException(status_code=409, detail="Product family code already exists.")


def check_duplicate_product_code(cur, code: str, exclude_id: int | None = None):
    sql = "SELECT id FROM products WHERE product_code = %s"
    params: list[Any] = [code]
    if exclude_id is not None:
        sql += " AND id <> %s"
        params.append(exclude_id)
    cur.execute(sql, tuple(params))
    if cur.fetchone():
        raise HTTPException(status_code=409, detail="Product code already exists.")


def check_duplicate_field_code(cur, code: str):
    cur.execute("SELECT id FROM product_field_definitions WHERE field_code = %s", (code,))
    if cur.fetchone():
        raise HTTPException(status_code=409, detail="Product field code already exists.")


def is_family_descendant(cur, family_id: int, possible_parent_id: int) -> bool:
    current_id: int | None = possible_parent_id
    visited: set[int] = set()
    while current_id is not None:
        if current_id == family_id or current_id in visited:
            return True
        visited.add(current_id)
        cur.execute("SELECT parent_id FROM product_families WHERE id = %s", (current_id,))
        row = cur.fetchone()
        if not row:
            return False
        current_id = row["parent_id"]
    return False


def get_family_ancestor_ids(cur, family_id: int) -> list[int]:
    result: list[int] = []
    current_id: int | None = family_id
    visited: set[int] = set()
    while current_id is not None:
        if current_id in visited:
            raise HTTPException(status_code=409, detail="Product family tree contains a cycle.")
        visited.add(current_id)
        result.append(current_id)
        cur.execute("SELECT parent_id FROM product_families WHERE id = %s", (current_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Product family not found.")
        current_id = row["parent_id"]
    return result


def get_family_descendant_ids(cur, family_id: int) -> list[int]:
    cur.execute(
        """
        WITH RECURSIVE family_tree AS (
            SELECT id FROM product_families WHERE id = %s
            UNION ALL
            SELECT child.id
            FROM product_families child
            JOIN family_tree parent ON child.parent_id = parent.id
        )
        SELECT id FROM family_tree
        """,
        (family_id,),
    )
    return [row["id"] for row in cur.fetchall()]


def get_applicable_fields(cur, family_id: int, include_inactive: bool = False):
    ancestor_ids = get_family_ancestor_ids(cur, family_id)
    placeholders = ", ".join(["%s"] * len(ancestor_ids))
    active_clause = "" if include_inactive else "AND f.is_active = 1"
    sql = f"""
        SELECT DISTINCT f.*
        FROM product_field_definitions f
        LEFT JOIN product_family_fields ff
            ON ff.field_definition_id = f.id
        WHERE (
            f.applies_to_all_families = 1
            OR ff.product_family_id = %s
            OR (
                ff.include_descendants = 1
                AND ff.product_family_id IN ({placeholders})
            )
        )
        {active_clause}
        ORDER BY f.field_group, f.sort_order, f.id
    """
    cur.execute(sql, tuple([family_id] + ancestor_ids))
    fields = cur.fetchall()
    for field in fields:
        if isinstance(field.get("validation_rules"), str):
            field["validation_rules"] = json.loads(field["validation_rules"])
    return fields


def _load_allowed_options(cur, field_id: int) -> set[str]:
    cur.execute(
        """
        SELECT option_value
        FROM product_field_options
        WHERE field_definition_id = %s AND is_active = 1
        """,
        (field_id,),
    )
    return {row["option_value"] for row in cur.fetchall()}


def _is_empty(value: Any) -> bool:
    return value is None or value == "" or value == []


def normalize_dynamic_value(cur, field: dict, value: Any):
    if _is_empty(value):
        return None

    data_type = field["data_type"]
    rules = field.get("validation_rules") or {}
    if isinstance(rules, str):
        rules = json.loads(rules)

    try:
        if data_type in {"TEXT", "LONG_TEXT", "SELECT"}:
            normalized: Any = str(value).strip()
        elif data_type == "INTEGER":
            if isinstance(value, bool):
                raise ValueError
            normalized = int(value)
        elif data_type == "DECIMAL":
            normalized = Decimal(str(value))
        elif data_type == "DATE":
            normalized = value if isinstance(value, date) else date.fromisoformat(str(value))
        elif data_type == "DATETIME":
            normalized = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
        elif data_type == "BOOLEAN":
            if isinstance(value, bool):
                normalized = value
            elif str(value).lower() in {"1", "true"}:
                normalized = True
            elif str(value).lower() in {"0", "false"}:
                normalized = False
            else:
                raise ValueError
        elif data_type == "MULTI_SELECT":
            if not isinstance(value, list):
                raise ValueError
            normalized = [str(item) for item in value]
        elif data_type in {"FILE", "IMAGE"}:
            if not isinstance(value, (dict, list, str)):
                raise ValueError
            normalized = value
        else:
            raise ValueError
    except (ValueError, TypeError, InvalidOperation) as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid value for field '{field['field_name']}' ({data_type}).",
        ) from exc

    if data_type == "SELECT":
        allowed = _load_allowed_options(cur, field["id"])
        if normalized not in allowed:
            raise HTTPException(status_code=422, detail=f"Invalid option for field '{field['field_name']}'.")
    elif data_type == "MULTI_SELECT":
        allowed = _load_allowed_options(cur, field["id"])
        if any(item not in allowed for item in normalized):
            raise HTTPException(status_code=422, detail=f"Invalid option for field '{field['field_name']}'.")

    if isinstance(normalized, str):
        if rules.get("min_length") is not None and len(normalized) < int(rules["min_length"]):
            raise HTTPException(status_code=422, detail=f"'{field['field_name']}' is too short.")
        if rules.get("max_length") is not None and len(normalized) > int(rules["max_length"]):
            raise HTTPException(status_code=422, detail=f"'{field['field_name']}' is too long.")
        if rules.get("pattern") and not re.fullmatch(str(rules["pattern"]), normalized):
            raise HTTPException(status_code=422, detail=f"'{field['field_name']}' has an invalid format.")
    if isinstance(normalized, (int, Decimal)) and not isinstance(normalized, bool):
        if rules.get("min") is not None and normalized < Decimal(str(rules["min"])):
            raise HTTPException(status_code=422, detail=f"'{field['field_name']}' is below the minimum.")
        if rules.get("max") is not None and normalized > Decimal(str(rules["max"])):
            raise HTTPException(status_code=422, detail=f"'{field['field_name']}' exceeds the maximum.")
    return normalized


def validate_product_values(cur, family_id: int, submitted_values: list, product_id: int | None = None):
    fields = get_applicable_fields(cur, family_id)
    fields_by_id = {field["id"]: field for field in fields}
    submitted_by_id = {item.field_id: item.value for item in submitted_values}

    unknown_ids = set(submitted_by_id) - set(fields_by_id)
    if unknown_ids:
        raise HTTPException(status_code=422, detail=f"Fields are not applicable to this product family: {sorted(unknown_ids)}")

    normalized: list[tuple[dict, Any]] = []
    for field in fields:
        raw_value = submitted_by_id.get(field["id"], field.get("default_value"))
        if field["is_required"] and _is_empty(raw_value):
            raise HTTPException(status_code=422, detail=f"Field '{field['field_name']}' is required.")
        value = normalize_dynamic_value(cur, field, raw_value)
        if value is not None and field["is_unique"]:
            column = VALUE_COLUMNS[field["data_type"]]
            lookup_value = json.dumps(value, ensure_ascii=False) if column == "value_json" else value
            sql = f"SELECT product_id FROM product_field_values WHERE field_definition_id = %s AND {column} = %s"
            params: list[Any] = [field["id"], lookup_value]
            if product_id is not None:
                sql += " AND product_id <> %s"
                params.append(product_id)
            cur.execute(sql, tuple(params))
            if cur.fetchone():
                raise HTTPException(status_code=409, detail=f"Field '{field['field_name']}' must be unique.")
        normalized.append((field, value))
    return normalized


def replace_product_values(cur, product_id: int, normalized_values: list[tuple[dict, Any]], actor: str | None):
    cur.execute("DELETE FROM product_field_values WHERE product_id = %s", (product_id,))
    for field, value in normalized_values:
        if value is None:
            continue
        columns = {
            "value_text": None,
            "value_integer": None,
            "value_decimal": None,
            "value_date": None,
            "value_datetime": None,
            "value_boolean": None,
            "value_json": None,
        }
        target_column = VALUE_COLUMNS[field["data_type"]]
        if target_column == "value_json":
            columns[target_column] = json.dumps(value, ensure_ascii=False)
        else:
            columns[target_column] = value
        cur.execute(
            """
            INSERT INTO product_field_values (
                product_id, field_definition_id, value_text,
                value_integer, value_decimal, value_date,
                value_datetime, value_boolean, value_json,
                created_by, updated_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                product_id,
                field["id"],
                columns["value_text"],
                columns["value_integer"],
                columns["value_decimal"],
                columns["value_date"],
                columns["value_datetime"],
                columns["value_boolean"],
                columns["value_json"],
                actor,
                actor,
            ),
        )


def serialize_field_value(row: dict):
    data_type = row["data_type"]
    value = row.get(VALUE_COLUMNS[data_type])
    if VALUE_COLUMNS[data_type] == "value_json" and isinstance(value, str):
        return json.loads(value)
    return value


def replace_field_options(cur, field_id: int, options: list):
    cur.execute("DELETE FROM product_field_options WHERE field_definition_id = %s", (field_id,))
    for option in options:
        cur.execute(
            """
            INSERT INTO product_field_options (
                field_definition_id, option_value, option_label,
                color, sort_order, is_active
            ) VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (field_id, option.option_value, option.option_label, option.color, option.sort_order, option.is_active),
        )


def replace_field_families(cur, field_id: int, family_ids: list[int], include_descendants: bool):
    cur.execute("DELETE FROM product_family_fields WHERE field_definition_id = %s", (field_id,))
    for family_id in set(family_ids):
        get_family(cur, family_id)
        cur.execute(
            """
            INSERT INTO product_family_fields (
                product_family_id, field_definition_id, include_descendants
            ) VALUES (%s, %s, %s)
            """,
            (family_id, field_id, include_descendants),
        )
