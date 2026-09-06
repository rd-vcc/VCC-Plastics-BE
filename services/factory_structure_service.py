from fastapi import HTTPException


def get_node_type(
    cur,
    node_type_id: int,
    active_only: bool = True,
):
    """
    Lấy thông tin một loại node.

    parent_type_id xác định loại node cha được phép.
    Nếu parent_type_id là NULL thì đây là loại node gốc.
    """
    sql = """
        SELECT
            id,
            code,
            name,
            parent_type_id,
            level_order,
            sort_order,
            icon,
            color,
            is_active
        FROM factory_node_types
        WHERE id = %s
    """

    params = [node_type_id]

    if active_only:
        sql += " AND is_active = 1"

    cur.execute(sql, tuple(params))
    node_type = cur.fetchone()

    if not node_type:
        raise HTTPException(
            status_code=404,
            detail="Node type not found or inactive.",
        )

    return node_type


def get_node(cur, node_id: int):
    """Lấy một node trong cây cấu trúc nhà máy."""
    cur.execute(
        """
        SELECT
            id,
            code,
            name,
            node_type_id,
            parent_id,
            level_no,
            sort_order,
            description,
            status
        FROM factory_structure_nodes
        WHERE id = %s
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


def validate_parent(
    cur,
    node_type_id: int,
    parent_id: int | None,
):
    """
    Kiểm tra node cha theo cấu hình parent_type_id trong database.

    Không còn sử dụng NODE_PARENT_RULES cố định trong code.
    Hàm trả về level_no của node đang được thêm hoặc cập nhật.
    """
    node_type = get_node_type(cur, node_type_id)
    required_parent_type_id = node_type["parent_type_id"]

    # Loại node gốc không được có node cha.
    if required_parent_type_id is None:
        if parent_id is not None:
            raise HTTPException(
                status_code=400,
                detail=(
                    f"{node_type['name']} is a root node type "
                    "and cannot have a parent node."
                ),
            )

        return 0

    # Loại node không phải gốc bắt buộc phải chọn node cha.
    if parent_id is None:
        raise HTTPException(
            status_code=400,
            detail=f"{node_type['name']} requires a parent node.",
        )

    parent = get_node(cur, parent_id)

    # Node cha phải thuộc đúng loại được cấu hình trong parent_type_id.
    if parent["node_type_id"] != required_parent_type_id:
        required_parent_type = get_node_type(
            cur,
            required_parent_type_id,
            active_only=False,
        )

        raise HTTPException(
            status_code=400,
            detail=(
                f"{node_type['name']} must be placed under "
                f"{required_parent_type['name']}."
            ),
        )

    if parent["status"] != "ACTIVE":
        raise HTTPException(
            status_code=400,
            detail="Cannot add a node under an inactive parent.",
        )

    return parent["level_no"] + 1


def check_duplicate_code(
    cur,
    code: str,
    exclude_id: int | None = None,
):
    """Không cho phép hai node cấu trúc có cùng mã."""
    sql = """
        SELECT id
        FROM factory_structure_nodes
        WHERE code = %s
    """
    params = [code]

    if exclude_id is not None:
        sql += " AND id <> %s"
        params.append(exclude_id)

    cur.execute(sql, tuple(params))

    if cur.fetchone():
        raise HTTPException(
            status_code=409,
            detail="Node code already exists.",
        )


def check_duplicate_node_type_code(
    cur,
    code: str,
    exclude_id: int | None = None,
):
    """Không cho phép hai loại node có cùng mã."""
    sql = """
        SELECT id
        FROM factory_node_types
        WHERE code = %s
    """
    params = [code]

    if exclude_id is not None:
        sql += " AND id <> %s"
        params.append(exclude_id)

    cur.execute(sql, tuple(params))

    if cur.fetchone():
        raise HTTPException(
            status_code=409,
            detail="Node type code already exists.",
        )


def calculate_node_type_level(
    cur,
    parent_type_id: int | None,
):
    """Tính cấp của loại node dựa trên loại cha."""
    if parent_type_id is None:
        return 0

    parent_type = get_node_type(cur, parent_type_id)
    return parent_type["level_order"] + 1


def is_descendant(
    cur,
    node_id: int,
    possible_parent_id: int,
):
    """
    Kiểm tra possible_parent_id có nằm trong cây con của node_id không.
    Dùng để ngăn di chuyển một node vào chính nó hoặc cây con của nó.
    """
    current_id = possible_parent_id
    visited_ids = set()

    while current_id is not None:
        if current_id == node_id:
            return True

        # Bảo vệ trong trường hợp database đã có dữ liệu vòng lặp.
        if current_id in visited_ids:
            return True

        visited_ids.add(current_id)

        cur.execute(
            """
            SELECT parent_id
            FROM factory_structure_nodes
            WHERE id = %s
            """,
            (current_id,),
        )

        row = cur.fetchone()

        if not row:
            return False

        current_id = row["parent_id"]

    return False


def is_node_type_descendant(
    cur,
    node_type_id: int,
    possible_parent_type_id: int,
):
    """
    Kiểm tra loại cha mới có nằm trong cây con của node_type_id không.
    Dùng khi sửa Node Type để ngăn tạo quan hệ vòng lặp.
    """
    current_id = possible_parent_type_id
    visited_ids = set()

    while current_id is not None:
        if current_id == node_type_id:
            return True

        if current_id in visited_ids:
            return True

        visited_ids.add(current_id)

        cur.execute(
            """
            SELECT parent_type_id
            FROM factory_node_types
            WHERE id = %s
            """,
            (current_id,),
        )

        row = cur.fetchone()

        if not row:
            return False

        current_id = row["parent_type_id"]

    return False
