from fastapi import APIRouter, HTTPException, Query

from app.core.database import get_vcc_plastics_connection
from app.modules.master_data.production_tools.schemas import (
    ProductionToolFieldDefinitionCreate, ProductionToolFieldDefinitionUpdate,
    ProductionToolNodeCreate, ProductionToolNodeMove,
    ProductionToolNodeTypeCreate, ProductionToolNodeTypeUpdate, ProductionToolNodeUpdate,
    StatusUpdate, ToolCreate, ToolStatusUpdate, ToolUpdate,
)
from app.modules.master_data.production_tools.service import (
    calculate_level, check_duplicate, get_field_definition, get_node,
    get_node_type, get_tool, is_node_descendant, is_node_type_descendant, serialize_field_value,
    update_descendant_levels, value_column_for,
)


router = APIRouter(prefix="/api/production-tool-master", tags=["Production Tool Master"])


def _db_error(exc: Exception):
    message = str(exc)
    if "Duplicate" in message:
        raise HTTPException(status_code=409, detail="Duplicate data.") from exc
    if "foreign key constraint fails" in message.lower():
        raise HTTPException(status_code=409, detail="Data is referenced or reference is invalid.") from exc
    raise HTTPException(status_code=400, detail=message) from exc


def _tree(rows):
    items = {row["id"]: {**row, "children": []} for row in rows}
    roots = []
    for item in items.values():
        parent = items.get(item["parent_id"])
        (parent["children"] if parent else roots).append(item)
    return roots


# ------------------------- Custom field definitions (Settings) -------------------------

@router.get("/field-definitions")
def list_field_definitions(include_inactive: bool = True):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        sql = "SELECT * FROM production_tool_field_definitions"
        if not include_inactive:
            sql += " WHERE is_active=1"
        sql += " ORDER BY sort_order, id"
        cur.execute(sql); return cur.fetchall()
    finally: cur.close(); conn.close()


@router.get("/field-definitions/{field_id}")
def field_definition_detail(field_id: int):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try: return get_field_definition(cur, field_id)
    finally: cur.close(); conn.close()


@router.post("/field-definitions", status_code=201)
def create_field_definition(payload: ProductionToolFieldDefinitionCreate):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        check_duplicate(cur, "production_tool_field_definitions", "field_code", payload.field_code)
        cur.execute(
            """INSERT INTO production_tool_field_definitions
               (field_code, field_name, data_type, unit, is_required, is_active, sort_order,
                description, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (payload.field_code, payload.field_name, payload.data_type, payload.unit,
             payload.is_required, payload.is_active, payload.sort_order, payload.description,
             payload.created_by),
        )
        conn.commit(); return {"message": "Production tool field created successfully.", "id": cur.lastrowid}
    except HTTPException: conn.rollback(); raise
    except Exception as exc: conn.rollback(); _db_error(exc)
    finally: cur.close(); conn.close()


@router.put("/field-definitions/{field_id}")
def update_field_definition(field_id: int, payload: ProductionToolFieldDefinitionUpdate):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_field_definition(cur, field_id)
        cur.execute(
            """UPDATE production_tool_field_definitions
               SET field_name=%s, unit=%s, is_required=%s, is_active=%s, sort_order=%s,
                   description=%s, updated_by=%s
               WHERE id=%s""",
            (payload.field_name, payload.unit, payload.is_required, payload.is_active,
             payload.sort_order, payload.description, payload.updated_by, field_id),
        )
        conn.commit(); return {"message": "Production tool field updated successfully."}
    except HTTPException: conn.rollback(); raise
    except Exception as exc: conn.rollback(); _db_error(exc)
    finally: cur.close(); conn.close()


@router.delete("/field-definitions/{field_id}")
def delete_field_definition(field_id: int):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_field_definition(cur, field_id)
        cur.execute("SELECT COUNT(*) total FROM production_tool_field_values WHERE field_definition_id=%s", (field_id,))
        if cur.fetchone()["total"]:
            raise HTTPException(status_code=409, detail="Field is in use; set it inactive instead.")
        cur.execute("DELETE FROM production_tool_field_definitions WHERE id=%s", (field_id,))
        conn.commit(); return {"message": "Production tool field deleted successfully."}
    except HTTPException: conn.rollback(); raise
    except Exception as exc: conn.rollback(); _db_error(exc)
    finally: cur.close(); conn.close()


def _load_custom_fields(cur, tool_id: int) -> dict:
    cur.execute(
        """SELECT v.*, d.field_code, d.data_type
           FROM production_tool_field_values v
           JOIN production_tool_field_definitions d ON d.id = v.field_definition_id
           WHERE v.tool_id = %s""",
        (tool_id,),
    )
    return {row["field_code"]: serialize_field_value(row) for row in cur.fetchall()}


def _save_custom_fields(cur, tool_id: int, custom_fields: dict, actor: str | None):
    if not custom_fields:
        return
    cur.execute("SELECT id, field_code, data_type FROM production_tool_field_definitions WHERE is_active=1")
    definitions = {row["field_code"]: row for row in cur.fetchall()}
    for field_code, raw_value in custom_fields.items():
        definition = definitions.get(field_code)
        if not definition:
            continue
        column = value_column_for(definition["data_type"])
        columns = ["value_text", "value_integer", "value_decimal", "value_boolean", "value_date"]
        values = [raw_value if column == name else None for name in columns]
        cur.execute(
            f"""INSERT INTO production_tool_field_values
                (tool_id, field_definition_id, {", ".join(columns)}, created_by, updated_by)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                ON DUPLICATE KEY UPDATE {", ".join(f"{c}=VALUES({c})" for c in columns)}, updated_by=VALUES(updated_by)""",
            (tool_id, definition["id"], *values, actor, actor),
        )


# ------------------------- Node types -------------------------

@router.get("/node-types")
def list_node_types(include_inactive: bool = True):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        sql = """SELECT t.*,
                 (SELECT COUNT(*) FROM production_tool_nodes n WHERE n.node_type_id=t.id) AS node_count,
                 (SELECT COUNT(*) FROM production_tool_node_types c WHERE c.parent_type_id=t.id) AS child_type_count
                 FROM production_tool_node_types t"""
        params = []
        if not include_inactive:
            sql += " WHERE t.is_active=1"
        sql += " ORDER BY t.level_order,t.sort_order,t.id"
        cur.execute(sql, tuple(params)); return cur.fetchall()
    finally:
        cur.close(); conn.close()


@router.get("/node-types/{type_id}")
def node_type_detail(type_id: int):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try: return get_node_type(cur, type_id)
    finally: cur.close(); conn.close()


@router.post("/node-types", status_code=201)
def create_node_type(payload: ProductionToolNodeTypeCreate):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        check_duplicate(cur, "production_tool_node_types", "code", payload.code)
        if payload.parent_type_id:
            get_node_type(cur, payload.parent_type_id)
        cur.execute("""INSERT INTO production_tool_node_types
            (code,name,parent_type_id,level_order,color,icon,sort_order,is_active,created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (payload.code,payload.name,payload.parent_type_id,payload.level_order,payload.color,
             payload.icon,payload.sort_order,payload.is_active,payload.created_by))
        conn.commit(); return {"message":"Production tool node type created successfully.","id":cur.lastrowid}
    except HTTPException: conn.rollback(); raise
    except Exception as exc: conn.rollback(); _db_error(exc)
    finally: cur.close(); conn.close()


@router.put("/node-types/{type_id}")
def update_node_type(type_id: int, payload: ProductionToolNodeTypeUpdate):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_node_type(cur,type_id)
        if payload.parent_type_id:
            get_node_type(cur, payload.parent_type_id)
            if payload.parent_type_id==type_id or is_node_type_descendant(cur,type_id,payload.parent_type_id):
                raise HTTPException(400,"Cannot move a node type into itself or its descendant.")
        if not payload.is_active:
            cur.execute("SELECT COUNT(*) total FROM production_tool_nodes WHERE node_type_id=%s AND status='ACTIVE'",(type_id,))
            if cur.fetchone()["total"]: raise HTTPException(409,"Deactivate nodes of this type first.")
        cur.execute("""UPDATE production_tool_node_types SET name=%s,parent_type_id=%s,level_order=%s,color=%s,icon=%s,
            sort_order=%s,is_active=%s,updated_by=%s WHERE id=%s""",
            (payload.name,payload.parent_type_id,payload.level_order,payload.color,payload.icon,
             payload.sort_order,payload.is_active,payload.updated_by,type_id))
        conn.commit(); return {"message":"Production tool node type updated successfully."}
    except HTTPException: conn.rollback(); raise
    except Exception as exc: conn.rollback(); _db_error(exc)
    finally: cur.close(); conn.close()


@router.delete("/node-types/{type_id}")
def delete_node_type(type_id: int):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_node_type(cur,type_id)
        cur.execute("SELECT COUNT(*) total FROM production_tool_node_types WHERE parent_type_id=%s",(type_id,))
        if cur.fetchone()["total"]: raise HTTPException(409,"Cannot delete a node type that has child node types.")
        cur.execute("SELECT COUNT(*) total FROM production_tool_nodes WHERE node_type_id=%s",(type_id,))
        if cur.fetchone()["total"]: raise HTTPException(409,"Cannot delete a node type that is in use.")
        cur.execute("DELETE FROM production_tool_node_types WHERE id=%s",(type_id,)); conn.commit()
        return {"message":"Production tool node type deleted successfully."}
    except HTTPException: conn.rollback(); raise
    except Exception as exc: conn.rollback(); _db_error(exc)
    finally: cur.close(); conn.close()


# ------------------------- Production tool node tree -------------------------

@router.get("/nodes")
def list_nodes(keyword: str | None=None, node_type_id: int | None=None,
               status: str | None=None, parent_id: int | None=None, tree: bool=False):
    conn=get_vcc_plastics_connection(); cur=conn.cursor(dictionary=True)
    try:
        conditions=[]; params=[]
        if keyword:
            conditions.append("(n.code LIKE %s OR n.name LIKE %s)"); value=f"%{keyword.strip()}%"; params += [value,value]
        if node_type_id: conditions.append("n.node_type_id=%s"); params.append(node_type_id)
        if status: conditions.append("n.status=%s"); params.append(status.upper())
        if parent_id is not None: conditions.append("n.parent_id=%s"); params.append(parent_id)
        sql="""SELECT n.*,t.code AS type_code,t.name AS type_name,
               (SELECT COUNT(*) FROM production_tool_nodes c WHERE c.parent_id=n.id) child_count,
               (SELECT COUNT(*) FROM production_tools m WHERE m.production_tool_node_id=n.id) tool_count
               FROM production_tool_nodes n JOIN production_tool_node_types t ON t.id=n.node_type_id"""
        if conditions: sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY n.level_no,n.sort_order,n.name,n.id"
        cur.execute(sql,tuple(params)); rows=cur.fetchall()
        return _tree(rows) if tree and not keyword and parent_id is None else rows
    finally: cur.close(); conn.close()


@router.get("/nodes/{node_id}")
def node_detail(node_id: int):
    conn=get_vcc_plastics_connection(); cur=conn.cursor(dictionary=True)
    try:
        get_node(cur,node_id); cur.execute("""SELECT n.*,t.code AS type_code,t.name AS type_name,
            p.code AS parent_code,p.name AS parent_name,
            (SELECT COUNT(*) FROM production_tools m WHERE m.production_tool_node_id=n.id) tool_count
            FROM production_tool_nodes n JOIN production_tool_node_types t ON t.id=n.node_type_id
            LEFT JOIN production_tool_nodes p ON p.id=n.parent_id WHERE n.id=%s""",(node_id,)); return cur.fetchone()
    finally: cur.close(); conn.close()


@router.post("/nodes", status_code=201)
def create_node(payload: ProductionToolNodeCreate):
    conn=get_vcc_plastics_connection(); cur=conn.cursor(dictionary=True)
    try:
        check_duplicate(cur,"production_tool_nodes","code",payload.code); get_node_type(cur,payload.node_type_id,True)
        level=calculate_level(cur,payload.parent_id)
        cur.execute("""INSERT INTO production_tool_nodes
            (code,name,node_type_id,parent_id,level_no,sort_order,description,status,created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (payload.code,payload.name,payload.node_type_id,payload.parent_id,level,
             payload.sort_order,payload.description,payload.status,payload.created_by))
        conn.commit(); return {"message":"Production tool node created successfully.","id":cur.lastrowid}
    except HTTPException: conn.rollback(); raise
    except Exception as exc: conn.rollback(); _db_error(exc)
    finally: cur.close(); conn.close()


@router.put("/nodes/{node_id}")
def update_node(node_id: int,payload:ProductionToolNodeUpdate):
    conn=get_vcc_plastics_connection(); cur=conn.cursor(dictionary=True)
    try:
        get_node(cur,node_id); get_node_type(cur,payload.node_type_id,True)
        if payload.parent_id==node_id or (payload.parent_id and is_node_descendant(cur,node_id,payload.parent_id)):
            raise HTTPException(400,"Cannot move a node into itself or its descendant.")
        level=calculate_level(cur,payload.parent_id)
        cur.execute("""UPDATE production_tool_nodes SET name=%s,node_type_id=%s,parent_id=%s,level_no=%s,
            sort_order=%s,description=%s,status=%s,updated_by=%s WHERE id=%s""",
            (payload.name,payload.node_type_id,payload.parent_id,level,payload.sort_order,
             payload.description,payload.status,payload.updated_by,node_id))
        update_descendant_levels(cur,node_id,level); conn.commit(); return {"message":"Production tool node updated successfully."}
    except HTTPException: conn.rollback(); raise
    except Exception as exc: conn.rollback(); _db_error(exc)
    finally: cur.close(); conn.close()


@router.post("/nodes/{node_id}/move")
def move_node(node_id:int,payload:ProductionToolNodeMove):
    current=node_detail(node_id)
    return update_node(node_id,ProductionToolNodeUpdate(name=current["name"],node_type_id=current["node_type_id"],
        parent_id=payload.parent_id,sort_order=payload.sort_order,description=current["description"],
        status=current["status"],updated_by=payload.updated_by))


@router.patch("/nodes/{node_id}/status")
def node_status(node_id:int,payload:StatusUpdate):
    current=node_detail(node_id)
    return update_node(node_id,ProductionToolNodeUpdate(name=current["name"],node_type_id=current["node_type_id"],
        parent_id=current["parent_id"],sort_order=current["sort_order"],description=current["description"],
        status=payload.status,updated_by=payload.updated_by))


@router.delete("/nodes/{node_id}")
def delete_node(node_id:int):
    conn=get_vcc_plastics_connection(); cur=conn.cursor(dictionary=True)
    try:
        get_node(cur,node_id)
        cur.execute("SELECT COUNT(*) total FROM production_tool_nodes WHERE parent_id=%s",(node_id,))
        if cur.fetchone()["total"]: raise HTTPException(409,"Cannot delete a node with child nodes.")
        cur.execute("SELECT COUNT(*) total FROM production_tools WHERE production_tool_node_id=%s",(node_id,))
        if cur.fetchone()["total"]: raise HTTPException(409,"Cannot delete a node containing tools.")
        cur.execute("DELETE FROM production_tool_nodes WHERE id=%s",(node_id,));conn.commit();return {"message":"Production tool node deleted successfully."}
    except HTTPException: conn.rollback(); raise
    except Exception as exc: conn.rollback(); _db_error(exc)
    finally: cur.close();conn.close()


# ------------------------- Production tools -------------------------

@router.get("/statistics")
def statistics():
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        cur.execute("""SELECT COUNT(*) total_tools,
          SUM(status='IN_USE') in_use,
          SUM(status='AVAILABLE') available,
          SUM(status='IN_MAINTENANCE') in_maintenance,
          SUM(status='OUT_OF_SERVICE') out_of_service,
          SUM(status='INACTIVE') inactive,
          SUM(calibration_required=1 AND calibration_due_date IS NOT NULL
              AND calibration_due_date <= DATE_ADD(CURDATE(), INTERVAL 30 DAY)) calibration_due_soon
          FROM production_tools""")
        row = cur.fetchone()
        return {key: int(value) if value is not None else 0 for key, value in row.items()}
    finally:cur.close();conn.close()


@router.get("/tools")
def list_tools(keyword:str|None=None,tool_type:str|None=None,production_tool_node_id:int|None=None,
               status:str|None=None,manufacturer:str|None=None,
               page:int=Query(default=1,ge=1),page_size:int=Query(default=50,ge=1,le=500)):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        conditions=[];params=[]
        if keyword:conditions.append("(m.tool_code LIKE %s OR m.tool_name LIKE %s)");v=f"%{keyword.strip()}%";params += [v,v]
        for clause,value in (("m.tool_type=%s",tool_type.upper() if tool_type else None),
                             ("m.production_tool_node_id=%s",production_tool_node_id),
                             ("m.status=%s",status.upper() if status else None),
                             ("m.manufacturer=%s",manufacturer)):
            if value is not None:conditions.append(clause);params.append(value)
        where=" WHERE "+" AND ".join(conditions) if conditions else ""
        cur.execute("SELECT COUNT(*) total FROM production_tools m"+where,tuple(params));total=cur.fetchone()["total"]
        sql="""SELECT m.*,n.code production_tool_node_code,n.name production_tool_node_name
          FROM production_tools m LEFT JOIN production_tool_nodes n ON n.id=m.production_tool_node_id"""+where+" ORDER BY m.tool_code LIMIT %s OFFSET %s"
        query_params=params+[page_size,(page-1)*page_size];cur.execute(sql,tuple(query_params))
        return {"items":cur.fetchall(),"total":total,"page":page,"page_size":page_size,"total_pages":(total+page_size-1)//page_size}
    finally:cur.close();conn.close()


@router.get("/tools/{tool_id}")
def tool_detail(tool_id:int):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        cur.execute("""SELECT m.*,n.code production_tool_node_code,n.name production_tool_node_name
            FROM production_tools m LEFT JOIN production_tool_nodes n ON n.id=m.production_tool_node_id WHERE m.id=%s""",(tool_id,))
        row=cur.fetchone()
        if not row: raise HTTPException(status_code=404, detail="Production tool not found.")
        row["custom_fields"] = _load_custom_fields(cur, tool_id)
        return row
    finally:cur.close();conn.close()


def _insert_tool(cur,payload:ToolCreate):
    cur.execute("""INSERT INTO production_tools (tool_code,tool_name,production_tool_node_id,tool_type,
      tool_category,manufacturer,model,serial_number,assigned_process,status,calibration_required,
      calibration_due_date,last_calibration_date,calibration_interval_days,measurement_range,
      measurement_resolution,description,image_url,created_by)
      VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
      (payload.tool_code,payload.tool_name,payload.production_tool_node_id,payload.tool_type,
       payload.tool_category,payload.manufacturer,payload.model,payload.serial_number,
       payload.assigned_process,payload.status,payload.calibration_required,payload.calibration_due_date,
       payload.last_calibration_date,payload.calibration_interval_days,payload.measurement_range,
       payload.measurement_resolution,payload.description,payload.image_url,payload.created_by))
    return cur.lastrowid


@router.post("/tools",status_code=201)
def create_tool(payload:ToolCreate):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        check_duplicate(cur,"production_tools","tool_code",payload.tool_code)
        if payload.production_tool_node_id:get_node(cur,payload.production_tool_node_id,True)
        tool_id=_insert_tool(cur,payload)
        _save_custom_fields(cur,tool_id,payload.custom_fields or {},payload.created_by)
        conn.commit()
        return {"message":"Production tool created successfully.","id":tool_id}
    except HTTPException:conn.rollback();raise
    except Exception as exc:conn.rollback();_db_error(exc)
    finally:cur.close();conn.close()


@router.put("/tools/{tool_id}")
def update_tool(tool_id:int,payload:ToolUpdate):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        get_tool(cur,tool_id)
        if payload.production_tool_node_id:get_node(cur,payload.production_tool_node_id,True)
        cur.execute("""UPDATE production_tools SET tool_name=%s,production_tool_node_id=%s,tool_type=%s,
          tool_category=%s,manufacturer=%s,model=%s,serial_number=%s,assigned_process=%s,status=%s,
          calibration_required=%s,calibration_due_date=%s,last_calibration_date=%s,
          calibration_interval_days=%s,measurement_range=%s,measurement_resolution=%s,description=%s,
          image_url=%s,updated_by=%s,version=version+1 WHERE id=%s AND version=%s""",
          (payload.tool_name,payload.production_tool_node_id,payload.tool_type,payload.tool_category,
           payload.manufacturer,payload.model,payload.serial_number,payload.assigned_process,payload.status,
           payload.calibration_required,payload.calibration_due_date,payload.last_calibration_date,
           payload.calibration_interval_days,payload.measurement_range,payload.measurement_resolution,
           payload.description,payload.image_url,payload.updated_by,tool_id,payload.version))
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="Production tool was changed by another user. Reload and try again.")
        _save_custom_fields(cur,tool_id,payload.custom_fields or {},payload.updated_by)
        conn.commit();return {"message":"Production tool updated successfully.","version":payload.version+1}
    except HTTPException:conn.rollback();raise
    except Exception as exc:conn.rollback();_db_error(exc)
    finally:cur.close();conn.close()


@router.patch("/tools/{tool_id}/status")
def update_tool_status(tool_id:int,payload:ToolStatusUpdate):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        get_tool(cur,tool_id)
        cur.execute("UPDATE production_tools SET status=%s,updated_by=%s,version=version+1 WHERE id=%s AND version=%s",
                    (payload.status,payload.updated_by,tool_id,payload.version))
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="Production tool was changed by another user. Reload and try again.")
        conn.commit()
        return {"message":"Production tool status updated successfully.","version":payload.version+1}
    except HTTPException:conn.rollback();raise
    except Exception as exc:conn.rollback();_db_error(exc)
    finally:cur.close();conn.close()


@router.delete("/tools/{tool_id}")
def delete_tool(tool_id:int):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        get_tool(cur,tool_id);cur.execute("DELETE FROM production_tools WHERE id=%s",(tool_id,));conn.commit();return {"message":"Production tool deleted successfully."}
    except HTTPException:conn.rollback();raise
    except Exception as exc:conn.rollback();_db_error(exc)
    finally:cur.close();conn.close()
