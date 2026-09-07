import json

from fastapi import APIRouter, HTTPException, Query

from database.db import get_vcc_plastics_connection
from schemas.product_master import (
    ProductCreate,
    ProductFamilyCreate,
    ProductFamilyMove,
    ProductFamilyUpdate,
    ProductFieldDefinitionCreate,
    ProductFieldDefinitionUpdate,
    ProductUpdate,
    ProductVersionCreate,
    StatusUpdate,
)
from services.product_master_service import (
    check_duplicate_family_code,
    check_duplicate_field_code,
    check_duplicate_product_code,
    get_applicable_fields,
    get_family,
    get_family_descendant_ids,
    get_field,
    get_product,
    is_family_descendant,
    replace_field_families,
    replace_field_options,
    replace_product_values,
    serialize_field_value,
    validate_product_values,
)


router = APIRouter(prefix="/api/product-master", tags=["Product Master"])


def _tree(rows: list[dict]):
    nodes = {row["id"]: {**row, "children": []} for row in rows}
    roots = []
    for node in nodes.values():
        parent = nodes.get(node["parent_id"])
        if parent:
            parent["children"].append(node)
        else:
            roots.append(node)

    def add_subtree_count(node: dict) -> int:
        total = int(node.get("direct_product_count") or 0)
        for child in node["children"]:
            total += add_subtree_count(child)
        node["product_count"] = total
        return total

    for root in roots:
        add_subtree_count(root)
    return roots


def _raise_database_error(exc: Exception):
    if "Duplicate" in str(exc):
        raise HTTPException(status_code=409, detail="Duplicate data.") from exc
    raise HTTPException(status_code=400, detail=str(exc)) from exc


def _field_detail(cur, field: dict):
    if isinstance(field.get("validation_rules"), str):
        field["validation_rules"] = json.loads(field["validation_rules"])
    cur.execute(
        """
        SELECT id, option_value, option_label, color, sort_order, is_active
        FROM product_field_options
        WHERE field_definition_id = %s
        ORDER BY sort_order, id
        """,
        (field["id"],),
    )
    field["options"] = cur.fetchall()
    cur.execute(
        """
        SELECT ff.product_family_id AS family_id,
               pf.family_code, pf.family_name,
               ff.include_descendants,
               ff.is_required_override,
               ff.sort_order_override
        FROM product_family_fields ff
        JOIN product_families pf ON pf.id = ff.product_family_id
        WHERE ff.field_definition_id = %s
        ORDER BY pf.family_code
        """,
        (field["id"],),
    )
    field["families"] = cur.fetchall()
    return field


# =========================================================
# PRODUCT FAMILY TREE
# =========================================================


@router.get("/families")
def list_families(
    status: str | None = None,
    keyword: str | None = None,
    tree: bool = True,
):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        conditions = []
        params = []
        if status:
            normalized = status.strip().upper()
            if normalized not in {"ACTIVE", "INACTIVE"}:
                raise HTTPException(status_code=400, detail="Status must be ACTIVE or INACTIVE.")
            conditions.append("f.status = %s")
            params.append(normalized)
        if keyword:
            conditions.append("(f.family_code LIKE %s OR f.family_name LIKE %s)")
            value = f"%{keyword.strip()}%"
            params.extend([value, value])
        sql = """
            SELECT f.*,
                   parent.family_code AS parent_code,
                   parent.family_name AS parent_name,
                   (SELECT COUNT(*) FROM product_families c WHERE c.parent_id = f.id) AS child_count,
                   (SELECT COUNT(*) FROM products p WHERE p.product_family_id = f.id) AS direct_product_count
            FROM product_families f
            LEFT JOIN product_families parent ON parent.id = f.parent_id
        """
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY f.sort_order, f.family_name, f.id"
        cur.execute(sql, tuple(params))
        rows = cur.fetchall()
        return _tree(rows) if tree and not keyword else rows
    finally:
        cur.close()
        conn.close()


@router.get("/families/{family_id}")
def get_family_detail(family_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        family = get_family(cur, family_id)
        descendant_ids = get_family_descendant_ids(cur, family_id)
        placeholders = ",".join(["%s"] * len(descendant_ids))
        cur.execute(f"SELECT COUNT(*) AS total FROM products WHERE product_family_id IN ({placeholders})", tuple(descendant_ids))
        family["subtree_product_count"] = cur.fetchone()["total"]
        return family
    finally:
        cur.close()
        conn.close()


@router.post("/families", status_code=201)
def create_family(payload: ProductFamilyCreate):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        check_duplicate_family_code(cur, payload.family_code)
        if payload.parent_id is not None:
            get_family(cur, payload.parent_id, active_only=True)
        cur.execute(
            """
            INSERT INTO product_families (
                family_code, family_name, parent_id, sort_order,
                description, status, created_by
            ) VALUES (%s, %s, %s, %s, %s, %s, %s)
            """,
            (payload.family_code, payload.family_name, payload.parent_id, payload.sort_order,
             payload.description, payload.status, payload.created_by),
        )
        conn.commit()
        return {"message": "Product family created successfully.", "id": cur.lastrowid}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        _raise_database_error(exc)
    finally:
        cur.close()
        conn.close()


@router.put("/families/{family_id}")
def update_family(family_id: int, payload: ProductFamilyUpdate):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        get_family(cur, family_id)
        if payload.parent_id == family_id:
            raise HTTPException(status_code=400, detail="A family cannot be its own parent.")
        if payload.parent_id is not None:
            get_family(cur, payload.parent_id, active_only=True)
            if is_family_descendant(cur, family_id, payload.parent_id):
                raise HTTPException(status_code=400, detail="Cannot move a family into its own descendant.")
        if payload.status == "INACTIVE":
            cur.execute("SELECT COUNT(*) AS total FROM product_families WHERE parent_id = %s AND status = 'ACTIVE'", (family_id,))
            if cur.fetchone()["total"]:
                raise HTTPException(status_code=409, detail="Deactivate active child families first.")
            cur.execute("SELECT COUNT(*) AS total FROM products WHERE product_family_id = %s AND status = 'ACTIVE'", (family_id,))
            if cur.fetchone()["total"]:
                raise HTTPException(status_code=409, detail="Deactivate or move active products first.")
        cur.execute(
            """
            UPDATE product_families
            SET family_name=%s, parent_id=%s, sort_order=%s,
                description=%s, status=%s, updated_by=%s
            WHERE id=%s
            """,
            (payload.family_name, payload.parent_id, payload.sort_order,
             payload.description, payload.status, payload.updated_by, family_id),
        )
        conn.commit()
        return {"message": "Product family updated successfully."}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        _raise_database_error(exc)
    finally:
        cur.close()
        conn.close()


@router.post("/families/{family_id}/move")
def move_family(family_id: int, payload: ProductFamilyMove):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        get_family(cur, family_id)
        if payload.parent_id == family_id:
            raise HTTPException(status_code=400, detail="A family cannot be its own parent.")
        if payload.parent_id is not None:
            get_family(cur, payload.parent_id, active_only=True)
            if is_family_descendant(cur, family_id, payload.parent_id):
                raise HTTPException(status_code=400, detail="Cannot move a family into its own descendant.")
        cur.execute(
            "UPDATE product_families SET parent_id=%s, sort_order=%s, updated_by=%s WHERE id=%s",
            (payload.parent_id, payload.sort_order, payload.updated_by, family_id),
        )
        conn.commit()
        return {"message": "Product family moved successfully."}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        _raise_database_error(exc)
    finally:
        cur.close()
        conn.close()


@router.patch("/families/{family_id}/status")
def update_family_status(family_id: int, payload: StatusUpdate):
    current = get_family_detail(family_id)
    return update_family(
        family_id,
        ProductFamilyUpdate(
            family_name=current["family_name"],
            parent_id=current["parent_id"],
            sort_order=current["sort_order"],
            description=current["description"],
            status=payload.status,
            updated_by=payload.updated_by,
        ),
    )


@router.delete("/families/{family_id}")
def delete_family(family_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        get_family(cur, family_id)
        checks = [
            ("product_families", "parent_id", "Cannot delete a family that has child families."),
            ("products", "product_family_id", "Cannot delete a family that has products."),
            ("product_family_fields", "product_family_id", "Cannot delete a family assigned to dynamic fields."),
        ]
        for table, column, detail in checks:
            cur.execute(f"SELECT COUNT(*) AS total FROM {table} WHERE {column} = %s", (family_id,))
            if cur.fetchone()["total"]:
                raise HTTPException(status_code=409, detail=detail)
        cur.execute("DELETE FROM product_families WHERE id = %s", (family_id,))
        conn.commit()
        return {"message": "Product family deleted successfully."}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        _raise_database_error(exc)
    finally:
        cur.close()
        conn.close()


# =========================================================
# DYNAMIC FIELD CONFIGURATION
# =========================================================


@router.get("/fields")
def list_fields(family_id: int | None = None, include_inactive: bool = True):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        if family_id is not None:
            fields = get_applicable_fields(cur, family_id, include_inactive)
        else:
            sql = "SELECT * FROM product_field_definitions"
            if not include_inactive:
                sql += " WHERE is_active = 1"
            sql += " ORDER BY field_group, sort_order, id"
            cur.execute(sql)
            fields = cur.fetchall()
        return [_field_detail(cur, field) for field in fields]
    finally:
        cur.close()
        conn.close()


@router.get("/fields/{field_id}")
def get_field_detail(field_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        return _field_detail(cur, get_field(cur, field_id))
    finally:
        cur.close()
        conn.close()


@router.post("/fields", status_code=201)
def create_field(payload: ProductFieldDefinitionCreate):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        check_duplicate_field_code(cur, payload.field_code)
        cur.execute(
            """
            INSERT INTO product_field_definitions (
                field_code, field_name, data_type, field_group, description,
                placeholder, default_value, unit_label, validation_rules,
                is_required, is_unique, is_filterable, is_list_visible,
                applies_to_all_families, is_system, is_active, sort_order,
                created_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,1,%s,%s)
            """,
            (payload.field_code, payload.field_name, payload.data_type, payload.field_group,
             payload.description, payload.placeholder, payload.default_value, payload.unit_label,
             json.dumps(payload.validation_rules) if payload.validation_rules else None,
             payload.is_required, payload.is_unique, payload.is_filterable,
             payload.is_list_visible, payload.applies_to_all_families,
             payload.sort_order, payload.created_by),
        )
        field_id = cur.lastrowid
        replace_field_options(cur, field_id, payload.options)
        if not payload.applies_to_all_families:
            replace_field_families(cur, field_id, payload.family_ids, payload.include_descendants)
        conn.commit()
        return {"message": "Product field created successfully.", "id": field_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        _raise_database_error(exc)
    finally:
        cur.close()
        conn.close()


@router.put("/fields/{field_id}")
def update_field(field_id: int, payload: ProductFieldDefinitionUpdate):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        field = get_field(cur, field_id)
        if field["data_type"] in {"SELECT", "MULTI_SELECT"} and not payload.options:
            raise HTTPException(status_code=422, detail="SELECT and MULTI_SELECT fields require options.")
        if field["data_type"] not in {"SELECT", "MULTI_SELECT"} and payload.options:
            raise HTTPException(status_code=422, detail="Only SELECT and MULTI_SELECT fields can have options.")
        if not payload.applies_to_all_families and not payload.family_ids:
            raise HTTPException(status_code=422, detail="At least one family is required.")
        cur.execute(
            """
            UPDATE product_field_definitions
            SET field_name=%s, field_group=%s, description=%s,
                placeholder=%s, default_value=%s, unit_label=%s,
                validation_rules=%s, is_required=%s, is_unique=%s,
                is_filterable=%s, is_list_visible=%s,
                applies_to_all_families=%s, is_active=%s,
                sort_order=%s, updated_by=%s
            WHERE id=%s
            """,
            (payload.field_name, payload.field_group, payload.description,
             payload.placeholder, payload.default_value, payload.unit_label,
             json.dumps(payload.validation_rules) if payload.validation_rules else None,
             payload.is_required, payload.is_unique, payload.is_filterable,
             payload.is_list_visible, payload.applies_to_all_families,
             payload.is_active, payload.sort_order, payload.updated_by, field_id),
        )
        replace_field_options(cur, field_id, payload.options)
        replace_field_families(
            cur, field_id,
            [] if payload.applies_to_all_families else payload.family_ids,
            payload.include_descendants,
        )
        conn.commit()
        return {"message": "Product field updated successfully."}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        _raise_database_error(exc)
    finally:
        cur.close()
        conn.close()


@router.delete("/fields/{field_id}")
def delete_field(field_id: int, updated_by: str | None = None):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        field = get_field(cur, field_id)
        cur.execute("SELECT COUNT(*) AS total FROM product_field_values WHERE field_definition_id = %s", (field_id,))
        has_values = cur.fetchone()["total"] > 0
        if field["is_system"] or has_values:
            cur.execute(
                "UPDATE product_field_definitions SET is_active=0, updated_by=%s WHERE id=%s",
                (updated_by, field_id),
            )
            message = "Product field deactivated because it is protected or has data."
        else:
            cur.execute("DELETE FROM product_family_fields WHERE field_definition_id = %s", (field_id,))
            cur.execute("DELETE FROM product_field_options WHERE field_definition_id = %s", (field_id,))
            cur.execute("DELETE FROM product_field_definitions WHERE id = %s", (field_id,))
            message = "Product field deleted successfully."
        conn.commit()
        return {"message": message, "deactivated": bool(field["is_system"] or has_values)}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        _raise_database_error(exc)
    finally:
        cur.close()
        conn.close()


# =========================================================
# PRODUCTS
# =========================================================


@router.get("/products/kpis")
def product_kpis():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT COUNT(*) AS total_products,
                   SUM(status='ACTIVE') AS active_products,
                   SUM(status='INACTIVE') AS inactive_products,
                   SUM(created_at >= DATE_FORMAT(CURRENT_DATE, '%Y-%m-01')) AS new_this_month
            FROM products
            """
        )
        result = cur.fetchone()
        cur.execute("SELECT COUNT(*) AS total FROM product_families WHERE status='ACTIVE'")
        result["product_families"] = cur.fetchone()["total"]
        return result
    finally:
        cur.close()
        conn.close()


@router.get("/products")
def list_products(
    keyword: str | None = None,
    family_id: int | None = None,
    include_descendants: bool = True,
    status: str | None = None,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=500),
):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        conditions = []
        params = []
        if keyword:
            conditions.append("(p.product_code LIKE %s OR p.product_name LIKE %s)")
            value = f"%{keyword.strip()}%"
            params.extend([value, value])
        if family_id is not None:
            family_ids = get_family_descendant_ids(cur, family_id) if include_descendants else [family_id]
            conditions.append(f"p.product_family_id IN ({','.join(['%s'] * len(family_ids))})")
            params.extend(family_ids)
        if status:
            normalized = status.strip().upper()
            if normalized not in {"ACTIVE", "INACTIVE"}:
                raise HTTPException(status_code=400, detail="Status must be ACTIVE or INACTIVE.")
            conditions.append("p.status = %s")
            params.append(normalized)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        cur.execute(f"SELECT COUNT(*) AS total FROM products p{where}", tuple(params))
        total = cur.fetchone()["total"]
        sql = f"""
            SELECT p.*, f.family_code, f.family_name
            FROM products p
            JOIN product_families f ON f.id = p.product_family_id
            {where}
            ORDER BY p.updated_at DESC, p.id DESC
            LIMIT %s OFFSET %s
        """
        cur.execute(sql, tuple(params + [page_size, (page - 1) * page_size]))
        items = cur.fetchall()
        product_map = {item["id"]: item for item in items}
        for item in items:
            item["dynamic_values"] = {}
        if product_map:
            placeholders = ",".join(["%s"] * len(product_map))
            cur.execute(
                f"""
                SELECT v.*, d.field_code, d.field_name, d.data_type, d.unit_label
                FROM product_field_values v
                JOIN product_field_definitions d ON d.id=v.field_definition_id
                WHERE v.product_id IN ({placeholders})
                  AND d.is_active=1
                  AND d.is_list_visible=1
                ORDER BY d.sort_order, d.id
                """,
                tuple(product_map),
            )
            for row in cur.fetchall():
                product_map[row["product_id"]]["dynamic_values"][row["field_code"]] = {
                    "field_id": row["field_definition_id"],
                    "field_name": row["field_name"],
                    "data_type": row["data_type"],
                    "unit_label": row["unit_label"],
                    "value": serialize_field_value(row),
                }
        return {"items": items, "total": total, "page": page, "page_size": page_size}
    finally:
        cur.close()
        conn.close()


@router.get("/products/{product_id}")
def get_product_detail(product_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT p.*, f.family_code, f.family_name
            FROM products p
            JOIN product_families f ON f.id=p.product_family_id
            WHERE p.id=%s
            """,
            (product_id,),
        )
        product = cur.fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found.")
        cur.execute(
            """
            SELECT v.*, d.field_code, d.field_name, d.data_type,
                   d.field_group, d.unit_label, d.sort_order
            FROM product_field_values v
            JOIN product_field_definitions d ON d.id=v.field_definition_id
            WHERE v.product_id=%s
            ORDER BY d.field_group, d.sort_order, d.id
            """,
            (product_id,),
        )
        product["values"] = [
            {
                "field_id": row["field_definition_id"],
                "field_code": row["field_code"],
                "field_name": row["field_name"],
                "data_type": row["data_type"],
                "field_group": row["field_group"],
                "unit_label": row["unit_label"],
                "value": serialize_field_value(row),
            }
            for row in cur.fetchall()
        ]
        product["field_definitions"] = [
            _field_detail(cur, field)
            for field in get_applicable_fields(cur, product["product_family_id"])
        ]
        return product
    finally:
        cur.close()
        conn.close()


@router.post("/products", status_code=201)
def create_product(payload: ProductCreate):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        check_duplicate_product_code(cur, payload.product_code)
        get_family(cur, payload.product_family_id, active_only=True)
        normalized = validate_product_values(cur, payload.product_family_id, payload.values)
        cur.execute(
            """
            INSERT INTO products (
                product_code, product_name, product_family_id,
                status, created_by
            ) VALUES (%s,%s,%s,%s,%s)
            """,
            (payload.product_code, payload.product_name, payload.product_family_id,
             payload.status, payload.created_by),
        )
        product_id = cur.lastrowid
        replace_product_values(cur, product_id, normalized, payload.created_by)
        conn.commit()
        return {"message": "Product created successfully.", "id": product_id}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        _raise_database_error(exc)
    finally:
        cur.close()
        conn.close()


@router.put("/products/{product_id}")
def update_product(product_id: int, payload: ProductUpdate):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        get_product(cur, product_id)
        get_family(cur, payload.product_family_id, active_only=True)
        normalized = validate_product_values(cur, payload.product_family_id, payload.values, product_id)
        cur.execute(
            """
            UPDATE products
            SET product_name=%s, product_family_id=%s,
                status=%s, updated_by=%s
            WHERE id=%s
            """,
            (payload.product_name, payload.product_family_id,
             payload.status, payload.updated_by, product_id),
        )
        replace_product_values(cur, product_id, normalized, payload.updated_by)
        conn.commit()
        return {"message": "Product updated successfully."}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        _raise_database_error(exc)
    finally:
        cur.close()
        conn.close()


@router.patch("/products/{product_id}/status")
def update_product_status(product_id: int, payload: StatusUpdate):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        get_product(cur, product_id)
        cur.execute(
            "UPDATE products SET status=%s, updated_by=%s WHERE id=%s",
            (payload.status, payload.updated_by, product_id),
        )
        conn.commit()
        return {"message": "Product status updated successfully."}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        _raise_database_error(exc)
    finally:
        cur.close()
        conn.close()


@router.delete("/products/{product_id}")
def delete_product(product_id: int, updated_by: str | None = None):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        get_product(cur, product_id)
        cur.execute(
            "UPDATE products SET status='INACTIVE', updated_by=%s WHERE id=%s",
            (updated_by, product_id),
        )
        conn.commit()
        return {"message": "Product deactivated successfully."}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        _raise_database_error(exc)
    finally:
        cur.close()
        conn.close()


# =========================================================
# PRODUCT REVISIONS
# =========================================================


@router.get("/products/{product_id}/versions")
def list_product_versions(product_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        get_product(cur, product_id)
        cur.execute(
            "SELECT * FROM product_versions WHERE product_id=%s ORDER BY effective_from DESC, id DESC",
            (product_id,),
        )
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


@router.post("/products/{product_id}/versions", status_code=201)
def create_product_version(product_id: int, payload: ProductVersionCreate):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        get_product(cur, product_id)
        if payload.status == "ACTIVE":
            cur.execute("UPDATE product_versions SET status='INACTIVE' WHERE product_id=%s AND status='ACTIVE'", (product_id,))
        cur.execute(
            """
            INSERT INTO product_versions (
                product_id, revision_code, effective_from, effective_to,
                status, description, created_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            """,
            (product_id, payload.revision_code, payload.effective_from,
             payload.effective_to, payload.status, payload.description,
             payload.created_by),
        )
        conn.commit()
        return {"message": "Product revision created successfully.", "id": cur.lastrowid}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        _raise_database_error(exc)
    finally:
        cur.close()
        conn.close()
