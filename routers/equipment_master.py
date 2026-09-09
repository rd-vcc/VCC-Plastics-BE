from fastapi import APIRouter, HTTPException, Query

from database.db import get_vcc_plastics_connection
from schemas.equipment_master import (
    EquipmentCreate,
    EquipmentGroupCreate,
    EquipmentGroupUpdate,
    EquipmentStatusUpdate,
    EquipmentTypeCreate,
    EquipmentTypeUpdate,
    EquipmentUpdate,
    SpecDefinitionCreate,
    SpecDefinitionUpdate,
    SpecValueUpsert,
)
from services.equipment_master_service import (
    ensure_unique,
    get_record,
    validate_factory_node,
    validate_group_for_type,
    validate_spec_value_type,
)


router = APIRouter(prefix="/api/equipment-master", tags=["Machine & Equipment Master"])


def _run_write(action):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        result = action(cur)
        conn.commit()
        return result
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cur.close()
        conn.close()


# =========================================================
# EQUIPMENT TYPE
# =========================================================


@router.get("/types")
def list_types(include_inactive: bool = True):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        sql = """
            SELECT t.*,
                   (SELECT COUNT(*) FROM equipment e WHERE e.equipment_type_id = t.id) AS equipment_count,
                   (SELECT COUNT(*) FROM equipment_spec_definitions s
                    WHERE s.equipment_type_id = t.id) AS specification_count
            FROM equipment_types t
        """
        params = []
        if not include_inactive:
            sql += " WHERE t.is_active = %s"
            params.append(1)
        sql += " ORDER BY t.sort_order, t.type_name"
        cur.execute(sql, tuple(params))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


@router.get("/types/{type_id}")
def get_type(type_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        return get_record(cur, "equipment_types", type_id, "Equipment type")
    finally:
        cur.close()
        conn.close()


@router.post("/types", status_code=201)
def create_type(payload: EquipmentTypeCreate):
    def action(cur):
        ensure_unique(cur, "equipment_types", "type_code", payload.type_code)
        cur.execute(
            """INSERT INTO equipment_types
               (type_code, type_name, category, description, is_active, sort_order, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (payload.type_code, payload.type_name, payload.category, payload.description,
             payload.is_active, payload.sort_order, payload.created_by),
        )
        return {"message": "Equipment type created successfully.", "id": cur.lastrowid}
    return _run_write(action)


@router.put("/types/{type_id}")
def update_type(type_id: int, payload: EquipmentTypeUpdate):
    def action(cur):
        get_record(cur, "equipment_types", type_id, "Equipment type")
        cur.execute(
            """UPDATE equipment_types
               SET type_name=%s, category=%s, description=%s, is_active=%s,
                   sort_order=%s, updated_by=%s
               WHERE id=%s""",
            (payload.type_name, payload.category, payload.description, payload.is_active,
             payload.sort_order, payload.updated_by, type_id),
        )
        return {"message": "Equipment type updated successfully."}
    return _run_write(action)


@router.delete("/types/{type_id}")
def delete_type(type_id: int):
    def action(cur):
        get_record(cur, "equipment_types", type_id, "Equipment type")
        cur.execute("SELECT COUNT(*) total FROM equipment WHERE equipment_type_id=%s", (type_id,))
        equipment_count = cur.fetchone()["total"]
        cur.execute("SELECT COUNT(*) total FROM equipment_spec_definitions WHERE equipment_type_id=%s", (type_id,))
        spec_count = cur.fetchone()["total"]
        if equipment_count or spec_count:
            raise HTTPException(status_code=409, detail="Equipment type is in use; set it inactive instead.")
        cur.execute("DELETE FROM equipment_types WHERE id=%s", (type_id,))
        return {"message": "Equipment type deleted successfully."}
    return _run_write(action)


# =========================================================
# EQUIPMENT GROUP
# =========================================================


@router.get("/groups")
def list_groups(equipment_type_id: int | None = None, include_inactive: bool = True):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        sql = """
            SELECT g.*, t.type_code, t.type_name,
                   (SELECT COUNT(*) FROM equipment e WHERE e.equipment_group_id=g.id) equipment_count
            FROM equipment_groups g
            LEFT JOIN equipment_types t ON t.id=g.equipment_type_id
            WHERE 1=1
        """
        params = []
        if equipment_type_id is not None:
            sql += " AND (g.equipment_type_id=%s OR g.equipment_type_id IS NULL)"
            params.append(equipment_type_id)
        if not include_inactive:
            sql += " AND g.is_active=1"
        sql += " ORDER BY g.sort_order, g.group_name"
        cur.execute(sql, tuple(params))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


@router.get("/groups/{group_id}")
def get_group(group_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        return get_record(cur, "equipment_groups", group_id, "Equipment group")
    finally:
        cur.close()
        conn.close()


@router.post("/groups", status_code=201)
def create_group(payload: EquipmentGroupCreate):
    def action(cur):
        ensure_unique(cur, "equipment_groups", "group_code", payload.group_code)
        if payload.equipment_type_id is not None:
            get_record(cur, "equipment_types", payload.equipment_type_id, "Equipment type")
        cur.execute(
            """INSERT INTO equipment_groups
               (group_code, group_name, equipment_type_id, description, is_active, sort_order, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (payload.group_code, payload.group_name, payload.equipment_type_id,
             payload.description, payload.is_active, payload.sort_order, payload.created_by),
        )
        return {"message": "Equipment group created successfully.", "id": cur.lastrowid}
    return _run_write(action)


@router.put("/groups/{group_id}")
def update_group(group_id: int, payload: EquipmentGroupUpdate):
    def action(cur):
        get_record(cur, "equipment_groups", group_id, "Equipment group")
        if payload.equipment_type_id is not None:
            get_record(cur, "equipment_types", payload.equipment_type_id, "Equipment type")
            cur.execute("SELECT COUNT(*) total FROM equipment WHERE equipment_group_id=%s AND equipment_type_id<>%s",
                        (group_id, payload.equipment_type_id))
            if cur.fetchone()["total"]:
                raise HTTPException(status_code=409, detail="Existing equipment does not match the new group type.")
        cur.execute(
            """UPDATE equipment_groups SET group_name=%s, equipment_type_id=%s,
               description=%s, is_active=%s, sort_order=%s, updated_by=%s WHERE id=%s""",
            (payload.group_name, payload.equipment_type_id, payload.description,
             payload.is_active, payload.sort_order, payload.updated_by, group_id),
        )
        return {"message": "Equipment group updated successfully."}
    return _run_write(action)


@router.delete("/groups/{group_id}")
def delete_group(group_id: int):
    def action(cur):
        get_record(cur, "equipment_groups", group_id, "Equipment group")
        cur.execute("SELECT COUNT(*) total FROM equipment WHERE equipment_group_id=%s", (group_id,))
        if cur.fetchone()["total"]:
            raise HTTPException(status_code=409, detail="Equipment group is in use; set it inactive instead.")
        cur.execute("DELETE FROM equipment_groups WHERE id=%s", (group_id,))
        return {"message": "Equipment group deleted successfully."}
    return _run_write(action)


# =========================================================
# EQUIPMENT
# =========================================================


@router.get("/equipment")
def list_equipment(
    keyword: str | None = None,
    equipment_type_id: int | None = None,
    equipment_group_id: int | None = None,
    factory_node_id: int | None = None,
    asset_status: str | None = None,
    operational_status: str | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        base = """
            FROM equipment e
            JOIN equipment_types t ON t.id=e.equipment_type_id
            LEFT JOIN equipment_groups g ON g.id=e.equipment_group_id
            LEFT JOIN factory_structure_nodes n ON n.id=e.factory_node_id
            WHERE 1=1
        """
        filters, params = "", []
        if keyword:
            filters += " AND (e.equipment_code LIKE %s OR e.equipment_name LIKE %s OR e.model LIKE %s OR e.serial_number LIKE %s)"
            term = f"%{keyword.strip()}%"
            params.extend([term, term, term, term])
        for column, value in (
            ("e.equipment_type_id", equipment_type_id),
            ("e.equipment_group_id", equipment_group_id),
            ("e.factory_node_id", factory_node_id),
        ):
            if value is not None:
                filters += f" AND {column}=%s"
                params.append(value)
        if asset_status:
            filters += " AND e.asset_status=%s"
            params.append(asset_status.strip().upper())
        if operational_status:
            filters += " AND e.operational_status=%s"
            params.append(operational_status.strip().upper())

        cur.execute("SELECT COUNT(*) total " + base + filters, tuple(params))
        total = cur.fetchone()["total"]
        sql = """SELECT e.*, t.type_code, t.type_name, t.category,
                        g.group_code, g.group_name,
                        n.code factory_node_code, n.name factory_node_name
                 """ + base + filters + " ORDER BY e.equipment_code LIMIT %s OFFSET %s"
        cur.execute(sql, tuple(params + [limit, offset]))
        return {"total": total, "items": cur.fetchall()}
    finally:
        cur.close()
        conn.close()


@router.get("/equipment/{equipment_id}")
def get_equipment_detail(equipment_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT e.*, t.type_code, t.type_name, t.category,
                      g.group_code, g.group_name,
                      n.code factory_node_code, n.name factory_node_name
               FROM equipment e
               JOIN equipment_types t ON t.id=e.equipment_type_id
               LEFT JOIN equipment_groups g ON g.id=e.equipment_group_id
               LEFT JOIN factory_structure_nodes n ON n.id=e.factory_node_id
               WHERE e.id=%s""",
            (equipment_id,),
        )
        equipment = cur.fetchone()
        if not equipment:
            raise HTTPException(status_code=404, detail="Equipment not found.")
        cur.execute(
            """SELECT d.id spec_definition_id, d.spec_code, d.spec_name, d.data_type,
                      d.unit, d.is_required, v.id value_id, v.value_text,
                      v.value_integer, v.value_decimal, v.value_boolean, v.value_date
               FROM equipment_spec_definitions d
               LEFT JOIN equipment_spec_values v
                 ON v.spec_definition_id=d.id AND v.equipment_id=%s
               WHERE d.equipment_type_id=%s AND d.is_active=1
               ORDER BY d.sort_order, d.spec_name""",
            (equipment_id, equipment["equipment_type_id"]),
        )
        equipment["specifications"] = cur.fetchall()
        return equipment
    finally:
        cur.close()
        conn.close()


@router.post("/equipment", status_code=201)
def create_equipment(payload: EquipmentCreate):
    def action(cur):
        ensure_unique(cur, "equipment", "equipment_code", payload.equipment_code)
        equipment_type = get_record(cur, "equipment_types", payload.equipment_type_id, "Equipment type")
        if not equipment_type["is_active"]:
            raise HTTPException(status_code=400, detail="Equipment type is inactive.")
        validate_group_for_type(cur, payload.equipment_group_id, payload.equipment_type_id)
        validate_factory_node(cur, payload.factory_node_id)
        cur.execute(
            """INSERT INTO equipment
               (equipment_code,equipment_name,equipment_type_id,equipment_group_id,
                factory_node_id,manufacturer,model,serial_number,manufacturing_year,
                installation_date,commissioning_date,image_url,asset_status,
                operational_status,description,created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (payload.equipment_code,payload.equipment_name,payload.equipment_type_id,
             payload.equipment_group_id,payload.factory_node_id,payload.manufacturer,
             payload.model,payload.serial_number,payload.manufacturing_year,
             payload.installation_date,payload.commissioning_date,payload.image_url,
             payload.asset_status,payload.operational_status,payload.description,payload.created_by),
        )
        return {"message": "Equipment created successfully.", "id": cur.lastrowid}
    return _run_write(action)


@router.put("/equipment/{equipment_id}")
def update_equipment(equipment_id: int, payload: EquipmentUpdate):
    def action(cur):
        current = get_record(cur, "equipment", equipment_id, "Equipment")
        code = payload.equipment_code or current["equipment_code"]
        ensure_unique(cur, "equipment", "equipment_code", code, equipment_id)
        get_record(cur, "equipment_types", payload.equipment_type_id, "Equipment type")
        validate_group_for_type(cur, payload.equipment_group_id, payload.equipment_type_id)
        validate_factory_node(cur, payload.factory_node_id)
        cur.execute(
            """UPDATE equipment SET equipment_code=%s,equipment_name=%s,equipment_type_id=%s,
               equipment_group_id=%s,factory_node_id=%s,manufacturer=%s,model=%s,
               serial_number=%s,manufacturing_year=%s,installation_date=%s,
               commissioning_date=%s,image_url=%s,asset_status=%s,operational_status=%s,
               description=%s,updated_by=%s,version=version+1
               WHERE id=%s AND version=%s""",
            (code,payload.equipment_name,payload.equipment_type_id,payload.equipment_group_id,
             payload.factory_node_id,payload.manufacturer,payload.model,payload.serial_number,
             payload.manufacturing_year,payload.installation_date,payload.commissioning_date,
             payload.image_url,payload.asset_status,payload.operational_status,payload.description,
             payload.updated_by,equipment_id,payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="Equipment was changed by another user. Reload and try again.")
        return {"message": "Equipment updated successfully.", "version": payload.version + 1}
    return _run_write(action)


@router.patch("/equipment/{equipment_id}/status")
def update_equipment_status(equipment_id: int, payload: EquipmentStatusUpdate):
    def action(cur):
        current = get_record(cur, "equipment", equipment_id, "Equipment")
        asset = payload.asset_status or current["asset_status"]
        operational = payload.operational_status or current["operational_status"]
        cur.execute(
            """UPDATE equipment SET asset_status=%s, operational_status=%s,
               updated_by=%s, version=version+1 WHERE id=%s AND version=%s""",
            (asset, operational, payload.updated_by, equipment_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="Equipment was changed by another user. Reload and try again.")
        return {"message": "Equipment status updated successfully.", "version": payload.version + 1}
    return _run_write(action)


@router.delete("/equipment/{equipment_id}")
def delete_equipment(equipment_id: int):
    def action(cur):
        current = get_record(cur, "equipment", equipment_id, "Equipment")
        if current["asset_status"] != "DRAFT":
            raise HTTPException(status_code=409, detail="Only DRAFT equipment can be deleted; set other equipment inactive.")
        cur.execute("DELETE FROM equipment WHERE id=%s", (equipment_id,))
        return {"message": "Equipment deleted successfully."}
    return _run_write(action)


# =========================================================
# SPECIFICATION DEFINITION AND VALUES
# =========================================================


@router.get("/spec-definitions")
def list_spec_definitions(equipment_type_id: int | None = None, include_inactive: bool = True):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        sql = """SELECT d.*, t.type_code, t.type_name
                 FROM equipment_spec_definitions d
                 JOIN equipment_types t ON t.id=d.equipment_type_id WHERE 1=1"""
        params = []
        if equipment_type_id is not None:
            sql += " AND d.equipment_type_id=%s"
            params.append(equipment_type_id)
        if not include_inactive:
            sql += " AND d.is_active=1"
        sql += " ORDER BY t.sort_order, d.sort_order, d.spec_name"
        cur.execute(sql, tuple(params))
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


@router.get("/spec-definitions/{definition_id}")
def get_spec_definition(definition_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        return get_record(
            cur,
            "equipment_spec_definitions",
            definition_id,
            "Specification definition",
        )
    finally:
        cur.close()
        conn.close()


@router.post("/spec-definitions", status_code=201)
def create_spec_definition(payload: SpecDefinitionCreate):
    def action(cur):
        get_record(cur, "equipment_types", payload.equipment_type_id, "Equipment type")
        cur.execute("SELECT id FROM equipment_spec_definitions WHERE equipment_type_id=%s AND spec_code=%s",
                    (payload.equipment_type_id, payload.spec_code))
        if cur.fetchone():
            raise HTTPException(status_code=409, detail="Specification code already exists for this equipment type.")
        cur.execute(
            """INSERT INTO equipment_spec_definitions
               (equipment_type_id,spec_code,spec_name,data_type,unit,is_required,is_active,
                sort_order,description,created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (payload.equipment_type_id,payload.spec_code,payload.spec_name,payload.data_type,
             payload.unit,payload.is_required,payload.is_active,payload.sort_order,
             payload.description,payload.created_by),
        )
        return {"message": "Specification definition created successfully.", "id": cur.lastrowid}
    return _run_write(action)


@router.put("/spec-definitions/{definition_id}")
def update_spec_definition(definition_id: int, payload: SpecDefinitionUpdate):
    def action(cur):
        current = get_record(cur, "equipment_spec_definitions", definition_id, "Specification definition")
        if current["data_type"] != payload.data_type:
            cur.execute("SELECT COUNT(*) total FROM equipment_spec_values WHERE spec_definition_id=%s", (definition_id,))
            if cur.fetchone()["total"]:
                raise HTTPException(status_code=409, detail="Cannot change data type after values have been entered.")
        cur.execute(
            """UPDATE equipment_spec_definitions SET spec_name=%s,data_type=%s,unit=%s,
               is_required=%s,is_active=%s,sort_order=%s,description=%s,updated_by=%s WHERE id=%s""",
            (payload.spec_name,payload.data_type,payload.unit,payload.is_required,payload.is_active,
             payload.sort_order,payload.description,payload.updated_by,definition_id),
        )
        return {"message": "Specification definition updated successfully."}
    return _run_write(action)


@router.delete("/spec-definitions/{definition_id}")
def delete_spec_definition(definition_id: int):
    def action(cur):
        get_record(cur, "equipment_spec_definitions", definition_id, "Specification definition")
        cur.execute("SELECT COUNT(*) total FROM equipment_spec_values WHERE spec_definition_id=%s", (definition_id,))
        if cur.fetchone()["total"]:
            raise HTTPException(status_code=409, detail="Specification is in use; set it inactive instead.")
        cur.execute("DELETE FROM equipment_spec_definitions WHERE id=%s", (definition_id,))
        return {"message": "Specification definition deleted successfully."}
    return _run_write(action)


@router.get("/equipment/{equipment_id}/spec-values")
def list_spec_values(equipment_id: int):
    detail = get_equipment_detail(equipment_id)
    return detail["specifications"]


@router.put("/equipment/{equipment_id}/spec-values/{definition_id}")
def upsert_spec_value(equipment_id: int, definition_id: int, payload: SpecValueUpsert):
    if payload.spec_definition_id != definition_id:
        raise HTTPException(status_code=400, detail="Specification definition IDs do not match.")

    def action(cur):
        equipment = get_record(cur, "equipment", equipment_id, "Equipment")
        definition = get_record(cur, "equipment_spec_definitions", definition_id, "Specification definition")
        if definition["equipment_type_id"] != equipment["equipment_type_id"]:
            raise HTTPException(status_code=400, detail="Specification does not belong to this equipment type.")
        if not definition["is_active"]:
            raise HTTPException(status_code=400, detail="Specification definition is inactive.")
        validate_spec_value_type(definition, payload)
        cur.execute(
            """INSERT INTO equipment_spec_values
               (equipment_id,spec_definition_id,value_text,value_integer,value_decimal,
                value_boolean,value_date,created_by,updated_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON DUPLICATE KEY UPDATE value_text=VALUES(value_text),
                 value_integer=VALUES(value_integer),value_decimal=VALUES(value_decimal),
                 value_boolean=VALUES(value_boolean),value_date=VALUES(value_date),
                 updated_by=VALUES(updated_by)""",
            (equipment_id,definition_id,payload.value_text,payload.value_integer,
             payload.value_decimal,payload.value_boolean,payload.value_date,
             payload.updated_by,payload.updated_by),
        )
        return {"message": "Specification value saved successfully."}
    return _run_write(action)


@router.delete("/equipment/{equipment_id}/spec-values/{definition_id}")
def delete_spec_value(equipment_id: int, definition_id: int):
    def action(cur):
        get_record(cur, "equipment", equipment_id, "Equipment")
        cur.execute("DELETE FROM equipment_spec_values WHERE equipment_id=%s AND spec_definition_id=%s",
                    (equipment_id, definition_id))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Specification value not found.")
        return {"message": "Specification value deleted successfully."}
    return _run_write(action)
