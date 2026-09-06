from fastapi import APIRouter, HTTPException

from database.db import get_vcc_plastics_connection
from schemas.factory_structure import (
    FactoryNodeCreate,
    FactoryNodeTypeCreate,
    FactoryNodeTypeUpdate,
    FactoryNodeUpdate,
)
from services.factory_structure_service import (
    calculate_node_type_level,
    check_duplicate_code,
    check_duplicate_node_type_code,
    get_node,
    get_node_type,
    is_descendant,
    is_node_type_descendant,
    validate_parent,
)


router = APIRouter(
    prefix="/api/factory-structure",
    tags=["Factory Structure"],
)


def update_child_type_levels(cur, parent_type_id: int, parent_level: int):
    """Cập nhật level_order cho toàn bộ loại node con."""
    cur.execute(
        """
        SELECT id
        FROM factory_node_types
        WHERE parent_type_id = %s
        """,
        (parent_type_id,),
    )
    children = cur.fetchall()

    for child in children:
        child_level = parent_level + 1
        cur.execute(
            """
            UPDATE factory_node_types
            SET level_order = %s
            WHERE id = %s
            """,
            (child_level, child["id"]),
        )
        update_child_type_levels(cur, child["id"], child_level)


def update_child_node_levels(cur, parent_node_id: int, parent_level: int):
    """Cập nhật level_no cho toàn bộ node con sau khi di chuyển node cha."""
    cur.execute(
        """
        SELECT id
        FROM factory_structure_nodes
        WHERE parent_id = %s
        """,
        (parent_node_id,),
    )
    children = cur.fetchall()

    for child in children:
        child_level = parent_level + 1
        cur.execute(
            """
            UPDATE factory_structure_nodes
            SET level_no = %s
            WHERE id = %s
            """,
            (child_level, child["id"]),
        )
        update_child_node_levels(cur, child["id"], child_level)


# =========================================================
# NODE TYPE APIs
# =========================================================


@router.get("/node-types")
def list_node_types(include_inactive: bool = True):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)

    try:
        sql = """
            SELECT
                child.id,
                child.code,
                child.name,
                child.parent_type_id,
                parent.code AS parent_type_code,
                parent.name AS parent_type_name,
                child.level_order,
                child.sort_order,
                child.icon,
                child.color,
                child.is_active,
                (
                    SELECT COUNT(*)
                    FROM factory_structure_nodes node
                    WHERE node.node_type_id = child.id
                ) AS node_count
            FROM factory_node_types child
            LEFT JOIN factory_node_types parent
                ON parent.id = child.parent_type_id
        """
        params = []

        if not include_inactive:
            sql += " WHERE child.is_active = %s"
            params.append(1)

        sql += """
            ORDER BY
                child.level_order,
                child.sort_order,
                child.name
        """

        cur.execute(sql, tuple(params))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


@router.get("/node-types/{type_id}")
def get_node_type_detail(type_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)

    try:
        cur.execute(
            """
            SELECT
                child.id,
                child.code,
                child.name,
                child.parent_type_id,
                parent.code AS parent_type_code,
                parent.name AS parent_type_name,
                child.level_order,
                child.sort_order,
                child.icon,
                child.color,
                child.is_active
            FROM factory_node_types child
            LEFT JOIN factory_node_types parent
                ON parent.id = child.parent_type_id
            WHERE child.id = %s
            """,
            (type_id,),
        )
        node_type = cur.fetchone()

        if not node_type:
            raise HTTPException(
                status_code=404,
                detail="Node type not found.",
            )

        return node_type
    finally:
        cur.close()
        conn.close()


@router.post("/node-types", status_code=201)
def create_node_type(payload: FactoryNodeTypeCreate):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)

    try:
        check_duplicate_node_type_code(cur, payload.code)

        level_order = calculate_node_type_level(
            cur,
            payload.parent_type_id,
        )

        cur.execute(
            """
            INSERT INTO factory_node_types (
                code,
                name,
                parent_type_id,
                level_order,
                sort_order,
                icon,
                color,
                is_active
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                payload.code,
                payload.name,
                payload.parent_type_id,
                level_order,
                payload.sort_order,
                payload.icon,
                payload.color,
                payload.is_active,
            ),
        )

        conn.commit()

        return {
            "message": "Node type created successfully.",
            "id": cur.lastrowid,
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    finally:
        cur.close()
        conn.close()


@router.put("/node-types/{type_id}")
def update_node_type(
    type_id: int,
    payload: FactoryNodeTypeUpdate,
):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)

    try:
        current_type = get_node_type(
            cur,
            type_id,
            active_only=False,
        )

        if payload.parent_type_id == type_id:
            raise HTTPException(
                status_code=400,
                detail="A node type cannot be its own parent.",
            )

        if (
            payload.parent_type_id is not None
            and is_node_type_descendant(
                cur,
                type_id,
                payload.parent_type_id,
            )
        ):
            raise HTTPException(
                status_code=400,
                detail="Cannot move a node type under its own child type.",
            )

        parent_changed = (
            current_type["parent_type_id"]
            != payload.parent_type_id
        )

        if parent_changed:
            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM factory_structure_nodes
                WHERE node_type_id = %s
                """,
                (type_id,),
            )

            if cur.fetchone()["total"] > 0:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Cannot change the parent of a node type "
                        "that is already being used."
                    ),
                )

        if not payload.is_active:
            cur.execute(
                """
                SELECT COUNT(*) AS total
                FROM factory_structure_nodes
                WHERE node_type_id = %s
                  AND status = 'ACTIVE'
                """,
                (type_id,),
            )

            if cur.fetchone()["total"] > 0:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "Cannot deactivate a node type that still "
                        "has active structure nodes."
                    ),
                )

        level_order = calculate_node_type_level(
            cur,
            payload.parent_type_id,
        )

        cur.execute(
            """
            UPDATE factory_node_types
            SET name = %s,
                parent_type_id = %s,
                level_order = %s,
                sort_order = %s,
                icon = %s,
                color = %s,
                is_active = %s
            WHERE id = %s
            """,
            (
                payload.name,
                payload.parent_type_id,
                level_order,
                payload.sort_order,
                payload.icon,
                payload.color,
                payload.is_active,
                type_id,
            ),
        )

        update_child_type_levels(cur, type_id, level_order)
        conn.commit()

        return {"message": "Node type updated successfully."}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    finally:
        cur.close()
        conn.close()


@router.delete("/node-types/{type_id}")
def delete_node_type(type_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)

    try:
        get_node_type(cur, type_id, active_only=False)

        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM factory_structure_nodes
            WHERE node_type_id = %s
            """,
            (type_id,),
        )

        if cur.fetchone()["total"] > 0:
            raise HTTPException(
                status_code=409,
                detail="Cannot delete a node type that is being used.",
            )

        cur.execute(
            """
            SELECT COUNT(*) AS total
            FROM factory_node_types
            WHERE parent_type_id = %s
            """,
            (type_id,),
        )

        if cur.fetchone()["total"] > 0:
            raise HTTPException(
                status_code=409,
                detail="Cannot delete a node type that has child types.",
            )

        cur.execute(
            """
            DELETE FROM factory_node_types
            WHERE id = %s
            """,
            (type_id,),
        )
        conn.commit()

        return {"message": "Node type deleted successfully."}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    finally:
        cur.close()
        conn.close()


# =========================================================
# FACTORY STRUCTURE NODE APIs
# =========================================================


@router.get("/nodes")
def list_nodes(
    keyword: str | None = None,
    node_type_id: int | None = None,
    status: str | None = None,
    parent_id: int | None = None,
):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)

    try:
        conditions = []
        params = []

        sql = """
            SELECT
                n.id,
                n.code,
                n.name,
                n.node_type_id,
                nt.code AS node_type_code,
                nt.name AS node_type_name,
                nt.color,
                n.parent_id,
                p.code AS parent_code,
                p.name AS parent_name,
                n.level_no,
                n.sort_order,
                n.description,
                n.status,
                n.created_by,
                n.updated_by,
                n.created_at,
                n.updated_at
            FROM factory_structure_nodes n
            JOIN factory_node_types nt
                ON nt.id = n.node_type_id
            LEFT JOIN factory_structure_nodes p
                ON p.id = n.parent_id
        """

        if keyword:
            conditions.append("(n.code LIKE %s OR n.name LIKE %s)")
            search_value = f"%{keyword.strip()}%"
            params.extend([search_value, search_value])

        if node_type_id is not None:
            conditions.append("n.node_type_id = %s")
            params.append(node_type_id)

        if status:
            normalized_status = status.strip().upper()

            if normalized_status not in {"ACTIVE", "INACTIVE"}:
                raise HTTPException(
                    status_code=400,
                    detail="Status must be ACTIVE or INACTIVE.",
                )

            conditions.append("n.status = %s")
            params.append(normalized_status)

        if parent_id is not None:
            conditions.append("n.parent_id = %s")
            params.append(parent_id)

        if conditions:
            sql += " WHERE " + " AND ".join(conditions)

        sql += """
            ORDER BY n.level_no, n.parent_id, n.sort_order, n.name
        """

        cur.execute(sql, tuple(params))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


@router.get("/nodes/{node_id}")
def get_node_detail(node_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)

    try:
        cur.execute(
            """
            SELECT
                n.*,
                nt.code AS node_type_code,
                nt.name AS node_type_name,
                nt.color,
                p.code AS parent_code,
                p.name AS parent_name
            FROM factory_structure_nodes n
            JOIN factory_node_types nt
                ON nt.id = n.node_type_id
            LEFT JOIN factory_structure_nodes p
                ON p.id = n.parent_id
            WHERE n.id = %s
            """,
            (node_id,),
        )
        node = cur.fetchone()

        if not node:
            raise HTTPException(
                status_code=404,
                detail="Factory structure node not found.",
            )

        return node
    finally:
        cur.close()
        conn.close()


@router.post("/nodes", status_code=201)
def create_node(payload: FactoryNodeCreate):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)

    try:
        check_duplicate_code(cur, payload.code)

        level_no = validate_parent(
            cur,
            payload.node_type_id,
            payload.parent_id,
        )

        cur.execute(
            """
            INSERT INTO factory_structure_nodes (
                code,
                name,
                node_type_id,
                parent_id,
                level_no,
                sort_order,
                description,
                status,
                created_by
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                payload.code,
                payload.name,
                payload.node_type_id,
                payload.parent_id,
                level_no,
                payload.sort_order,
                payload.description,
                payload.status,
                payload.created_by,
            ),
        )
        conn.commit()

        return {
            "message": "Factory structure node created.",
            "id": cur.lastrowid,
        }
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    finally:
        cur.close()
        conn.close()


@router.put("/nodes/{node_id}")
def update_node(
    node_id: int,
    payload: FactoryNodeUpdate,
):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)

    try:
        get_node(cur, node_id)

        if payload.parent_id == node_id:
            raise HTTPException(
                status_code=400,
                detail="A node cannot be its own parent.",
            )

        if (
            payload.parent_id is not None
            and is_descendant(cur, node_id, payload.parent_id)
        ):
            raise HTTPException(
                status_code=400,
                detail="Cannot move a node into its own child.",
            )

        level_no = validate_parent(
            cur,
            payload.node_type_id,
            payload.parent_id,
        )

        cur.execute(
            """
            UPDATE factory_structure_nodes
            SET name = %s,
                node_type_id = %s,
                parent_id = %s,
                level_no = %s,
                sort_order = %s,
                description = %s,
                status = %s,
                updated_by = %s
            WHERE id = %s
            """,
            (
                payload.name,
                payload.node_type_id,
                payload.parent_id,
                level_no,
                payload.sort_order,
                payload.description,
                payload.status,
                payload.updated_by,
                node_id,
            ),
        )

        update_child_node_levels(cur, node_id, level_no)
        conn.commit()

        return {"message": "Factory structure node updated."}
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(
            status_code=400,
            detail=str(exc),
        ) from exc
    finally:
        cur.close()
        conn.close()
