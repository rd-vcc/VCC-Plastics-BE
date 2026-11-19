import json

from fastapi import APIRouter, HTTPException, Query

from app.core.database import get_vcc_plastics_connection
from app.core.employee_names import attach_employee_names
from app.modules.master_data.molds.schemas import (
    MoldAttributeDefinitionCreate, MoldAttributeDefinitionUpdate,
    MoldAttributeValuesReplace, MoldCreate, MoldNodeCreate, MoldNodeMove,
    MoldNodeTypeCreate, MoldNodeTypeUpdate, MoldNodeUpdate,
    MoldDocumentCreate, MoldProductsReplace, MoldStatusUpdate, MoldUpdate, StatusUpdate,
)
from app.modules.mold.service import log_event
from app.modules.master_data.molds.service import (
    calculate_level, check_duplicate, get_attribute, get_mold, get_node,
    get_node_type, is_node_descendant, replace_attribute_values,
    replace_products, serialize_value, update_descendant_levels,
)


router = APIRouter(prefix="/api/mold-master", tags=["Mold Master"])


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


def _mold_detail(cur, mold_id: int):
    cur.execute(
        """SELECT m.*, n.node_code AS location_code, n.node_name AS location_name,
                  t.type_code AS location_type_code, t.type_name AS location_type_name,
                  CASE WHEN m.design_shot > 0
                       THEN ROUND(m.current_shot * 100.0 / m.design_shot, 2) ELSE 0 END AS life_used_percent
           FROM molds m
           LEFT JOIN mold_location_nodes n ON n.id=m.current_location_id
           LEFT JOIN mold_location_node_types t ON t.id=n.node_type_id
           WHERE m.id=%s""",
        (mold_id,),
    )
    mold = cur.fetchone()
    if not mold:
        raise HTTPException(status_code=404, detail="Mold not found.")
    cur.execute(
        """SELECT d.*, v.id AS value_id, v.value_text, v.value_number,
                  v.value_boolean, v.value_date, v.value_datetime, v.value_json
           FROM mold_attribute_definitions d
           LEFT JOIN mold_attribute_values v
             ON v.attribute_definition_id=d.id AND v.mold_id=%s
           WHERE d.is_active=1 ORDER BY d.sort_order,d.id""",
        (mold_id,),
    )
    attributes = cur.fetchall()
    for item in attributes:
        for key in ("select_options", "validation_rules"):
            if isinstance(item.get(key), str):
                item[key] = json.loads(item[key])
        item["value"] = serialize_value(item) if item["value_id"] else None
        for key in ("value_text", "value_number", "value_boolean", "value_date", "value_datetime", "value_json"):
            item.pop(key, None)
    mold["attribute_values"] = attributes
    cur.execute(
        """SELECT mp.*, p.product_code, p.product_name, p.status AS product_status
           FROM mold_products mp JOIN products p ON p.id=mp.product_id
           WHERE mp.mold_id=%s ORDER BY mp.is_primary DESC,p.product_code""",
        (mold_id,),
    )
    mold["products"] = cur.fetchall()
    return mold


# ------------------------- Node types -------------------------

@router.get("/location-node-types")
def list_node_types(include_inactive: bool = True):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        sql = """SELECT t.*,
                 (SELECT COUNT(*) FROM mold_location_nodes n WHERE n.node_type_id=t.id) AS node_count
                 FROM mold_location_node_types t"""
        params = []
        if not include_inactive:
            sql += " WHERE t.is_active=1"
        sql += " ORDER BY t.level_order,t.sort_order,t.id"
        cur.execute(sql, tuple(params)); return cur.fetchall()
    finally:
        cur.close(); conn.close()


@router.get("/location-node-types/{type_id}")
def node_type_detail(type_id: int):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try: return get_node_type(cur, type_id)
    finally: cur.close(); conn.close()


@router.post("/location-node-types", status_code=201)
def create_node_type(payload: MoldNodeTypeCreate):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        check_duplicate(cur, "mold_location_node_types", "type_code", payload.type_code)
        cur.execute("""INSERT INTO mold_location_node_types
            (type_code,type_name,level_order,icon,color,description,is_active,sort_order,location_category,created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (payload.type_code,payload.type_name,payload.level_order,payload.icon,payload.color,
             payload.description,payload.is_active,payload.sort_order,payload.location_category,payload.created_by))
        conn.commit(); return {"message":"Mold location node type created successfully.","id":cur.lastrowid}
    except HTTPException: conn.rollback(); raise
    except Exception as exc: conn.rollback(); _db_error(exc)
    finally: cur.close(); conn.close()


@router.put("/location-node-types/{type_id}")
def update_node_type(type_id: int, payload: MoldNodeTypeUpdate):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_node_type(cur,type_id)
        if not payload.is_active:
            cur.execute("SELECT COUNT(*) total FROM mold_location_nodes WHERE node_type_id=%s AND status='ACTIVE'",(type_id,))
            if cur.fetchone()["total"]: raise HTTPException(409,"Deactivate nodes of this type first.")
        cur.execute("""UPDATE mold_location_node_types SET type_name=%s,level_order=%s,icon=%s,color=%s,
            description=%s,is_active=%s,sort_order=%s,location_category=%s,updated_by=%s WHERE id=%s""",
            (payload.type_name,payload.level_order,payload.icon,payload.color,payload.description,
             payload.is_active,payload.sort_order,payload.location_category,payload.updated_by,type_id))
        conn.commit(); return {"message":"Mold location node type updated successfully."}
    except HTTPException: conn.rollback(); raise
    except Exception as exc: conn.rollback(); _db_error(exc)
    finally: cur.close(); conn.close()


@router.delete("/location-node-types/{type_id}")
def delete_node_type(type_id: int):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_node_type(cur,type_id); cur.execute("SELECT COUNT(*) total FROM mold_location_nodes WHERE node_type_id=%s",(type_id,))
        if cur.fetchone()["total"]: raise HTTPException(409,"Cannot delete a node type that is in use.")
        cur.execute("DELETE FROM mold_location_node_types WHERE id=%s",(type_id,)); conn.commit()
        return {"message":"Mold location node type deleted successfully."}
    except HTTPException: conn.rollback(); raise
    except Exception as exc: conn.rollback(); _db_error(exc)
    finally: cur.close(); conn.close()


# ------------------------- Location tree -------------------------

@router.get("/location-nodes")
def list_nodes(keyword: str | None=None, node_type_id: int | None=None,
               status: str | None=None, parent_id: int | None=None, tree: bool=True):
    conn=get_vcc_plastics_connection(); cur=conn.cursor(dictionary=True)
    try:
        conditions=[]; params=[]
        if keyword:
            conditions.append("(n.node_code LIKE %s OR n.node_name LIKE %s)"); value=f"%{keyword.strip()}%"; params += [value,value]
        if node_type_id: conditions.append("n.node_type_id=%s"); params.append(node_type_id)
        if status: conditions.append("n.status=%s"); params.append(status.upper())
        if parent_id is not None: conditions.append("n.parent_id=%s"); params.append(parent_id)
        sql="""SELECT n.*,t.type_code,t.type_name,
               (SELECT COUNT(*) FROM mold_location_nodes c WHERE c.parent_id=n.id) child_count,
               (SELECT COUNT(*) FROM molds m WHERE m.current_location_id=n.id) mold_count
               FROM mold_location_nodes n JOIN mold_location_node_types t ON t.id=n.node_type_id"""
        if conditions: sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY n.level_no,n.sort_order,n.node_name,n.id"
        cur.execute(sql,tuple(params)); rows=attach_employee_names(cur,cur.fetchall(),["created_by","updated_by"])
        return _tree(rows) if tree and not keyword and parent_id is None else rows
    finally: cur.close(); conn.close()


@router.get("/location-nodes/{node_id}")
def node_detail(node_id: int):
    conn=get_vcc_plastics_connection(); cur=conn.cursor(dictionary=True)
    try:
        get_node(cur,node_id); cur.execute("""SELECT n.*,t.type_code,t.type_name,p.node_code parent_code,p.node_name parent_name,
            (SELECT COUNT(*) FROM molds m WHERE m.current_location_id=n.id) mold_count
            FROM mold_location_nodes n JOIN mold_location_node_types t ON t.id=n.node_type_id
            LEFT JOIN mold_location_nodes p ON p.id=n.parent_id WHERE n.id=%s""",(node_id,)); return cur.fetchone()
    finally: cur.close(); conn.close()


@router.post("/location-nodes", status_code=201)
def create_node(payload: MoldNodeCreate):
    conn=get_vcc_plastics_connection(); cur=conn.cursor(dictionary=True)
    try:
        check_duplicate(cur,"mold_location_nodes","node_code",payload.node_code); get_node_type(cur,payload.node_type_id,True)
        level=calculate_level(cur,payload.parent_id)
        cur.execute("""INSERT INTO mold_location_nodes
            (node_code,node_name,node_type_id,parent_id,level_no,sort_order,description,status,capacity,created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (payload.node_code,payload.node_name,payload.node_type_id,payload.parent_id,level,
             payload.sort_order,payload.description,payload.status,payload.capacity,payload.created_by))
        conn.commit(); return {"message":"Mold location node created successfully.","id":cur.lastrowid}
    except HTTPException: conn.rollback(); raise
    except Exception as exc: conn.rollback(); _db_error(exc)
    finally: cur.close(); conn.close()


@router.put("/location-nodes/{node_id}")
def update_node(node_id: int,payload:MoldNodeUpdate):
    conn=get_vcc_plastics_connection(); cur=conn.cursor(dictionary=True)
    try:
        get_node(cur,node_id); get_node_type(cur,payload.node_type_id,True)
        if payload.parent_id==node_id or (payload.parent_id and is_node_descendant(cur,node_id,payload.parent_id)):
            raise HTTPException(400,"Cannot move a node into itself or its descendant.")
        level=calculate_level(cur,payload.parent_id)
        cur.execute("""UPDATE mold_location_nodes SET node_name=%s,node_type_id=%s,parent_id=%s,level_no=%s,
            sort_order=%s,description=%s,status=%s,capacity=%s,updated_by=%s WHERE id=%s""",
            (payload.node_name,payload.node_type_id,payload.parent_id,level,payload.sort_order,
             payload.description,payload.status,payload.capacity,payload.updated_by,node_id))
        update_descendant_levels(cur,node_id,level); conn.commit(); return {"message":"Mold location node updated successfully."}
    except HTTPException: conn.rollback(); raise
    except Exception as exc: conn.rollback(); _db_error(exc)
    finally: cur.close(); conn.close()


@router.post("/location-nodes/{node_id}/move")
def move_node(node_id:int,payload:MoldNodeMove):
    current=node_detail(node_id)
    return update_node(node_id,MoldNodeUpdate(node_name=current["node_name"],node_type_id=current["node_type_id"],
        parent_id=payload.parent_id,sort_order=payload.sort_order,description=current["description"],
        status=current["status"],capacity=current.get("capacity"),updated_by=payload.updated_by))


@router.patch("/location-nodes/{node_id}/status")
def node_status(node_id:int,payload:StatusUpdate):
    current=node_detail(node_id)
    return update_node(node_id,MoldNodeUpdate(node_name=current["node_name"],node_type_id=current["node_type_id"],
        parent_id=current["parent_id"],sort_order=current["sort_order"],description=current["description"],
        status=payload.status,capacity=current.get("capacity"),updated_by=payload.updated_by))


@router.delete("/location-nodes/{node_id}")
def delete_node(node_id:int):
    conn=get_vcc_plastics_connection(); cur=conn.cursor(dictionary=True)
    try:
        get_node(cur,node_id)
        cur.execute("SELECT COUNT(*) total FROM mold_location_nodes WHERE parent_id=%s",(node_id,))
        if cur.fetchone()["total"]: raise HTTPException(409,"Cannot delete a node with child nodes.")
        cur.execute("SELECT COUNT(*) total FROM molds WHERE current_location_id=%s",(node_id,))
        if cur.fetchone()["total"]: raise HTTPException(409,"Cannot delete a location containing molds.")
        cur.execute("DELETE FROM mold_location_nodes WHERE id=%s",(node_id,));conn.commit();return {"message":"Mold location node deleted successfully."}
    except HTTPException: conn.rollback(); raise
    except Exception as exc: conn.rollback(); _db_error(exc)
    finally: cur.close();conn.close()


# ------------------------- Dynamic attributes -------------------------

@router.get("/attributes")
def list_attributes(include_inactive:bool=True):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        sql="SELECT * FROM mold_attribute_definitions" + ("" if include_inactive else " WHERE is_active=1") + " ORDER BY sort_order,id"
        cur.execute(sql); rows=cur.fetchall()
        for row in rows:
            for key in ("select_options","validation_rules"):
                if isinstance(row.get(key),str): row[key]=json.loads(row[key])
        return rows
    finally:cur.close();conn.close()


@router.get("/attributes/{attribute_id}")
def attribute_detail(attribute_id:int):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        row=get_attribute(cur,attribute_id)
        for key in ("select_options","validation_rules"):
            if isinstance(row.get(key),str):row[key]=json.loads(row[key])
        return row
    finally:cur.close();conn.close()


@router.post("/attributes",status_code=201)
def create_attribute(payload:MoldAttributeDefinitionCreate):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        check_duplicate(cur,"mold_attribute_definitions","attribute_code",payload.attribute_code)
        cur.execute("""INSERT INTO mold_attribute_definitions
          (attribute_code,attribute_name,data_type,unit,default_value,select_options,is_required,is_searchable,
           is_filterable,show_in_list,sort_order,validation_rules,description,is_active,created_by)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
          (payload.attribute_code,payload.attribute_name,payload.data_type,payload.unit,payload.default_value,
           json.dumps(payload.select_options,ensure_ascii=False) if payload.select_options is not None else None,
           payload.is_required,payload.is_searchable,payload.is_filterable,payload.show_in_list,payload.sort_order,
           json.dumps(payload.validation_rules,ensure_ascii=False) if payload.validation_rules is not None else None,
           payload.description,payload.is_active,payload.created_by))
        conn.commit();return {"message":"Mold attribute created successfully.","id":cur.lastrowid}
    except HTTPException:conn.rollback();raise
    except Exception as exc:conn.rollback();_db_error(exc)
    finally:cur.close();conn.close()


@router.put("/attributes/{attribute_id}")
def update_attribute(attribute_id:int,payload:MoldAttributeDefinitionUpdate):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        get_attribute(cur,attribute_id)
        cur.execute("""UPDATE mold_attribute_definitions SET attribute_name=%s,unit=%s,default_value=%s,
          select_options=%s,is_required=%s,is_searchable=%s,is_filterable=%s,show_in_list=%s,sort_order=%s,
          validation_rules=%s,description=%s,is_active=%s,updated_by=%s WHERE id=%s""",
          (payload.attribute_name,payload.unit,payload.default_value,
           json.dumps(payload.select_options,ensure_ascii=False) if payload.select_options is not None else None,
           payload.is_required,payload.is_searchable,payload.is_filterable,payload.show_in_list,payload.sort_order,
           json.dumps(payload.validation_rules,ensure_ascii=False) if payload.validation_rules is not None else None,
           payload.description,payload.is_active,payload.updated_by,attribute_id))
        conn.commit();return {"message":"Mold attribute updated successfully."}
    except HTTPException:conn.rollback();raise
    except Exception as exc:conn.rollback();_db_error(exc)
    finally:cur.close();conn.close()


@router.delete("/attributes/{attribute_id}")
def delete_attribute(attribute_id:int):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        get_attribute(cur,attribute_id);cur.execute("SELECT COUNT(*) total FROM mold_attribute_values WHERE attribute_definition_id=%s",(attribute_id,))
        if cur.fetchone()["total"]:raise HTTPException(409,"Cannot delete an attribute that has values. Deactivate it instead.")
        cur.execute("DELETE FROM mold_attribute_definitions WHERE id=%s",(attribute_id,));conn.commit();return {"message":"Mold attribute deleted successfully."}
    except HTTPException:conn.rollback();raise
    except Exception as exc:conn.rollback();_db_error(exc)
    finally:cur.close();conn.close()


# ------------------------- Molds and relations -------------------------

@router.get("/statistics")
def statistics():
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        cur.execute("""SELECT COUNT(*) total_molds,
          SUM(status='IN_PRODUCTION') in_production,SUM(status='AVAILABLE') available,
          SUM(status='IN_MAINTENANCE') in_maintenance,SUM(status='IN_REPAIR') in_repair,
          ROUND(AVG(CASE WHEN design_shot>0 THEN current_shot*100.0/design_shot ELSE 0 END),2) average_life_used
          FROM molds WHERE is_active=1""");return cur.fetchone()
    finally:cur.close();conn.close()


@router.get("/products/lookup")
def product_lookup(keyword:str|None=None,limit:int=Query(default=50,ge=1,le=200)):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        sql="SELECT id,product_code,product_name,status FROM products";params=[]
        if keyword:sql+=" WHERE product_code LIKE %s OR product_name LIKE %s";v=f"%{keyword.strip()}%";params=[v,v]
        sql+=" ORDER BY product_code LIMIT %s";params.append(limit);cur.execute(sql,tuple(params));return cur.fetchall()
    finally:cur.close();conn.close()


@router.get("/molds")
def list_molds(keyword:str|None=None,status:str|None=None,mold_type:str|None=None,mold_group:str|None=None,
               location_id:int|None=None,product_id:int|None=None,is_active:bool|None=None,
               page:int=Query(default=1,ge=1),page_size:int=Query(default=50,ge=1,le=500)):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        conditions=[];params=[]
        if keyword:conditions.append("(m.mold_code LIKE %s OR m.mold_name LIKE %s)");v=f"%{keyword.strip()}%";params += [v,v]
        for clause,value in (("m.status=%s",status.upper() if status else None),("m.mold_type=%s",mold_type),
                             ("m.mold_group=%s",mold_group),("m.current_location_id=%s",location_id),
                             ("m.is_active=%s",is_active)):
            if value is not None:conditions.append(clause);params.append(value)
        if product_id:conditions.append("EXISTS (SELECT 1 FROM mold_products x WHERE x.mold_id=m.id AND x.product_id=%s)");params.append(product_id)
        where=" WHERE "+" AND ".join(conditions) if conditions else ""
        cur.execute("SELECT COUNT(*) total FROM molds m"+where,tuple(params));total=cur.fetchone()["total"]
        sql="""SELECT m.*,n.node_code location_code,n.node_name location_name,
          CASE WHEN m.design_shot>0 THEN ROUND(m.current_shot*100.0/m.design_shot,2) ELSE 0 END life_used_percent,
          (SELECT GROUP_CONCAT(p.product_name ORDER BY mp.is_primary DESC,p.product_code SEPARATOR ', ')
           FROM mold_products mp JOIN products p ON p.id=mp.product_id WHERE mp.mold_id=m.id) product_names
          FROM molds m LEFT JOIN mold_location_nodes n ON n.id=m.current_location_id"""+where+" ORDER BY m.mold_code LIMIT %s OFFSET %s"
        query_params=params+[page_size,(page-1)*page_size];cur.execute(sql,tuple(query_params))
        return {"items":cur.fetchall(),"total":total,"page":page,"page_size":page_size,"total_pages":(total+page_size-1)//page_size}
    finally:cur.close();conn.close()


@router.get("/molds/{mold_id}")
def mold_detail(mold_id:int):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:return attach_employee_names(cur,_mold_detail(cur,mold_id),["created_by","updated_by"])[0]
    finally:cur.close();conn.close()


def _write_mold(cur,payload,mold_id:int|None=None):
    values=(payload.mold_name,payload.mold_type,payload.mold_group,payload.cavity_count,payload.status,
      payload.current_location_id,payload.design_shot,payload.current_shot,payload.weight_kg,payload.length_mm,
      payload.width_mm,payload.height_mm,payload.manufacturer,payload.manufacture_date,payload.image_url,
      payload.description,payload.remark,payload.is_active,payload.revision,payload.pm_interval_shot or None,payload.pm_interval_days or None)
    if mold_id is None:
        cur.execute("""INSERT INTO molds (mold_code,mold_name,mold_type,mold_group,cavity_count,status,current_location_id,
          design_shot,current_shot,weight_kg,length_mm,width_mm,height_mm,manufacturer,manufacture_date,image_url,
          description,remark,is_active,revision,pm_interval_shot,pm_interval_days,status_since,location_since,created_by)
          VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),NOW(),%s)""",
          (payload.mold_code,)+values+(payload.created_by,));return cur.lastrowid
    # status and current_shot are live data owned by Mold Management (install / remove, shot counting,
    # maintenance) and logged in mold_events, so the master data edit leaves them untouched.
    edit_values=values[:4]+values[5:7]+values[8:]
    cur.execute("""UPDATE molds SET mold_name=%s,mold_type=%s,mold_group=%s,cavity_count=%s,current_location_id=%s,
      design_shot=%s,weight_kg=%s,length_mm=%s,width_mm=%s,height_mm=%s,manufacturer=%s,
      manufacture_date=%s,image_url=%s,description=%s,remark=%s,is_active=%s,revision=%s,pm_interval_shot=%s,pm_interval_days=%s,
      updated_by=%s WHERE id=%s""",
      edit_values+(payload.updated_by,mold_id));return mold_id


def _guard_status(before,status):
    """The mounted state belongs to Mold Management (install / remove); admins cannot fake it here."""
    if before and before.get("current_machine_id") and status!="IN_PRODUCTION":
        raise HTTPException(409,"The mold is mounted on a machine. Remove it in Mold Management first.")
    if status=="IN_PRODUCTION" and not (before and before.get("current_machine_id")):
        raise HTTPException(409,"In Production is set by installing the mold on a machine in Mold Management.")


def _log_master_changes(cur,before,mold_id,actor):
    cur.execute("SELECT status,current_location_id,current_shot FROM molds WHERE id=%s",(mold_id,))
    after=cur.fetchone()
    if before["status"]!=after["status"]:
        cur.execute("UPDATE molds SET status_since=NOW() WHERE id=%s",(mold_id,))
        log_event(cur,mold_id,"STATUS",actor,from_status=before["status"],to_status=after["status"],reason="Mold Master")
    if before["current_location_id"]!=after["current_location_id"]:
        cur.execute("UPDATE molds SET location_since=NOW() WHERE id=%s",(mold_id,))
        log_event(cur,mold_id,"MOVE",actor,from_location_id=before["current_location_id"],to_location_id=after["current_location_id"],reason="Mold Master")
    if int(before["current_shot"] or 0)!=int(after["current_shot"] or 0):
        log_event(cur,mold_id,"SHOT_ADJUST",actor,shot_before=before["current_shot"] or 0,shot_after=after["current_shot"] or 0,reason="Mold Master")


@router.post("/molds",status_code=201)
def create_mold(payload:MoldCreate):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        check_duplicate(cur,"molds","mold_code",payload.mold_code)
        if payload.current_location_id:get_node(cur,payload.current_location_id,True)
        _guard_status(None,payload.status)
        mold_id=_write_mold(cur,payload);replace_attribute_values(cur,mold_id,payload.attribute_values,payload.created_by)
        replace_products(cur,mold_id,payload.products,payload.created_by);conn.commit()
        return {"message":"Mold created successfully.","id":mold_id}
    except HTTPException:conn.rollback();raise
    except Exception as exc:conn.rollback();_db_error(exc)
    finally:cur.close();conn.close()


@router.put("/molds/{mold_id}")
def update_mold(mold_id:int,payload:MoldUpdate):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        get_mold(cur,mold_id)
        cur.execute("SELECT status,current_location_id,current_shot,current_machine_id FROM molds WHERE id=%s FOR UPDATE",(mold_id,));before=cur.fetchone()
        if payload.current_location_id:get_node(cur,payload.current_location_id,True)
        _write_mold(cur,payload,mold_id);_log_master_changes(cur,before,mold_id,payload.updated_by)
        replace_attribute_values(cur,mold_id,payload.attribute_values,payload.updated_by)
        replace_products(cur,mold_id,payload.products,payload.updated_by);conn.commit();return {"message":"Mold updated successfully."}
    except HTTPException:conn.rollback();raise
    except Exception as exc:conn.rollback();_db_error(exc)
    finally:cur.close();conn.close()


@router.patch("/molds/{mold_id}/status")
def update_mold_status(mold_id:int,payload:MoldStatusUpdate):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        get_mold(cur,mold_id)
        cur.execute("SELECT status,current_location_id,current_shot,current_machine_id FROM molds WHERE id=%s FOR UPDATE",(mold_id,));before=cur.fetchone()
        if payload.status!=before["status"]:_guard_status(before,payload.status)
        cur.execute("UPDATE molds SET status=%s,updated_by=%s WHERE id=%s",(payload.status,payload.updated_by,mold_id))
        _log_master_changes(cur,before,mold_id,payload.updated_by);conn.commit()
        return {"message":"Mold status updated successfully."}
    except HTTPException:conn.rollback();raise
    except Exception as exc:conn.rollback();_db_error(exc)
    finally:cur.close();conn.close()


@router.delete("/molds/{mold_id}")
def delete_mold(mold_id:int):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        get_mold(cur,mold_id);cur.execute("DELETE FROM molds WHERE id=%s",(mold_id,));conn.commit();return {"message":"Mold deleted successfully."}
    except HTTPException:conn.rollback();raise
    except Exception as exc:conn.rollback();_db_error(exc)
    finally:cur.close();conn.close()


@router.get("/molds/{mold_id}/attribute-values")
def mold_attribute_values(mold_id:int):
    return mold_detail(mold_id)["attribute_values"]


@router.put("/molds/{mold_id}/attribute-values")
def put_mold_attribute_values(mold_id:int,payload:MoldAttributeValuesReplace):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:get_mold(cur,mold_id);replace_attribute_values(cur,mold_id,payload.values,payload.updated_by);conn.commit();return {"message":"Mold attribute values updated successfully."}
    except HTTPException:conn.rollback();raise
    except Exception as exc:conn.rollback();_db_error(exc)
    finally:cur.close();conn.close()


@router.get("/molds/{mold_id}/products")
def mold_products(mold_id:int):
    return mold_detail(mold_id)["products"]


@router.put("/molds/{mold_id}/products")
def put_mold_products(mold_id:int,payload:MoldProductsReplace):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:get_mold(cur,mold_id);replace_products(cur,mold_id,payload.products,payload.updated_by);conn.commit();return {"message":"Mold products updated successfully."}
    except HTTPException:conn.rollback();raise
    except Exception as exc:conn.rollback();_db_error(exc)
    finally:cur.close();conn.close()



# ------------------------- Documents (files uploaded via /api/files/documents) -------------------------

@router.get("/molds/{mold_id}/documents")
def list_documents(mold_id:int):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        get_mold(cur,mold_id);cur.execute("SELECT * FROM mold_documents WHERE mold_id=%s ORDER BY doc_type,uploaded_at DESC",(mold_id,));return cur.fetchall()
    finally:cur.close();conn.close()


@router.post("/molds/{mold_id}/documents",status_code=201)
def add_document(mold_id:int,payload:MoldDocumentCreate):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        get_mold(cur,mold_id)
        if not payload.file_url.startswith("/uploads/"):raise HTTPException(400,"Upload the file first.")
        cur.execute("""INSERT INTO mold_documents (mold_id,doc_type,title,file_url,file_name,file_size,uploaded_by) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (mold_id,payload.doc_type,payload.title.strip(),payload.file_url,payload.file_name,payload.file_size,payload.uploaded_by))
        conn.commit();return {"message":"Document added.","id":cur.lastrowid}
    except HTTPException:conn.rollback();raise
    except Exception as exc:conn.rollback();_db_error(exc)
    finally:cur.close();conn.close()


@router.delete("/molds/{mold_id}/documents/{doc_id}")
def delete_document(mold_id:int,doc_id:int):
    conn=get_vcc_plastics_connection();cur=conn.cursor(dictionary=True)
    try:
        cur.execute("DELETE FROM mold_documents WHERE id=%s AND mold_id=%s",(doc_id,mold_id))
        if not cur.rowcount:raise HTTPException(404,"Document not found.")
        conn.commit();return {"message":"Document deleted."}
    except HTTPException:conn.rollback();raise
    except Exception as exc:conn.rollback();_db_error(exc)
    finally:cur.close();conn.close()
