from datetime import timedelta

from fastapi import APIRouter, HTTPException, Query

from app.core.database import get_vcc_plastics_connection
from app.modules.master_data.measuring_equipment.schemas import (
    CalibrationRecordCreate,
    EquipmentCreate,
    EquipmentStatusUpdate,
    EquipmentTypeCreate,
    EquipmentTypeUpdate,
    EquipmentUpdate,
)
from app.modules.master_data.measuring_equipment.service import (
    ensure_unique,
    get_record,
    validate_factory_node,
)


router = APIRouter(prefix="/api/measuring-equipment-master", tags=["Measuring Equipment Master"])


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
        message = str(exc)
        if "Duplicate" in message:
            raise HTTPException(status_code=409, detail="Duplicate data.") from exc
        if "foreign key constraint fails" in message.lower():
            raise HTTPException(status_code=409, detail="Data is referenced or reference is invalid.") from exc
        raise HTTPException(status_code=400, detail=message) from exc
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
                   (SELECT COUNT(*) FROM measuring_equipment e WHERE e.equipment_type_id = t.id) AS equipment_count
            FROM measuring_equipment_types t
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
        return get_record(cur, "measuring_equipment_types", type_id, "Equipment type")
    finally:
        cur.close()
        conn.close()


@router.post("/types", status_code=201)
def create_type(payload: EquipmentTypeCreate):
    def action(cur):
        ensure_unique(cur, "measuring_equipment_types", "type_code", payload.type_code)
        cur.execute(
            """INSERT INTO measuring_equipment_types
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
        get_record(cur, "measuring_equipment_types", type_id, "Equipment type")
        cur.execute(
            """UPDATE measuring_equipment_types
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
        get_record(cur, "measuring_equipment_types", type_id, "Equipment type")
        cur.execute("SELECT COUNT(*) total FROM measuring_equipment WHERE equipment_type_id=%s", (type_id,))
        if cur.fetchone()["total"]:
            raise HTTPException(status_code=409, detail="Equipment type is in use; set it inactive instead.")
        cur.execute("DELETE FROM measuring_equipment_types WHERE id=%s", (type_id,))
        return {"message": "Equipment type deleted successfully."}
    return _run_write(action)


# =========================================================
# EQUIPMENT
# =========================================================


@router.get("/statistics")
def statistics():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT COUNT(*) total,
                 SUM(status='IN_USE') in_use,
                 SUM(status='AVAILABLE') available,
                 SUM(status IN ('OUT_OF_SERVICE','INACTIVE')) out_of_use,
                 SUM(next_calibration_date IS NOT NULL AND next_calibration_date >= CURDATE()
                     AND next_calibration_date <= DATE_ADD(CURDATE(), INTERVAL 30 DAY)) calibration_due_soon,
                 SUM(next_calibration_date IS NOT NULL AND next_calibration_date < CURDATE()) calibration_overdue
               FROM measuring_equipment"""
        )
        row = cur.fetchone()
        return {key: int(value) if value is not None else 0 for key, value in row.items()}
    finally:
        cur.close()
        conn.close()


def _equipment_base_query():
    return """
        FROM measuring_equipment e
        JOIN measuring_equipment_types t ON t.id=e.equipment_type_id
        LEFT JOIN factory_structure_nodes n ON n.id=e.factory_node_id
        WHERE 1=1
    """


@router.get("/equipment")
def list_equipment(
    keyword: str | None = None,
    equipment_type_id: int | None = None,
    factory_node_id: int | None = None,
    status: str | None = None,
    calibration_status: str | None = None,
    manufacturer: str | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
    offset: int = Query(default=0, ge=0),
):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        base = _equipment_base_query()
        filters, params = "", []
        if keyword:
            filters += " AND (e.equipment_code LIKE %s OR e.equipment_name LIKE %s OR e.model LIKE %s OR e.serial_number LIKE %s)"
            term = f"%{keyword.strip()}%"
            params.extend([term, term, term, term])
        for column, value in (
            ("e.equipment_type_id", equipment_type_id),
            ("e.factory_node_id", factory_node_id),
        ):
            if value is not None:
                filters += f" AND {column}=%s"
                params.append(value)
        if status:
            filters += " AND e.status=%s"
            params.append(status.strip().upper())
        if manufacturer:
            filters += " AND e.manufacturer=%s"
            params.append(manufacturer)
        if calibration_status == "overdue":
            filters += " AND e.next_calibration_date IS NOT NULL AND e.next_calibration_date < CURDATE()"
        elif calibration_status == "due_soon":
            filters += " AND e.next_calibration_date IS NOT NULL AND e.next_calibration_date >= CURDATE() AND e.next_calibration_date <= DATE_ADD(CURDATE(), INTERVAL 30 DAY)"
        elif calibration_status == "valid":
            filters += " AND (e.next_calibration_date IS NULL OR e.next_calibration_date > DATE_ADD(CURDATE(), INTERVAL 30 DAY))"

        cur.execute("SELECT COUNT(*) total " + base + filters, tuple(params))
        total = cur.fetchone()["total"]
        sql = """SELECT e.*, t.type_code, t.type_name, t.category,
                        n.code factory_node_code, n.name factory_node_name,
                        (e.next_calibration_date IS NULL OR e.next_calibration_date >= CURDATE()) AS is_calibration_valid
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
                      n.code factory_node_code, n.name factory_node_name,
                      (e.next_calibration_date IS NULL OR e.next_calibration_date >= CURDATE()) AS is_calibration_valid
               FROM measuring_equipment e
               JOIN measuring_equipment_types t ON t.id=e.equipment_type_id
               LEFT JOIN factory_structure_nodes n ON n.id=e.factory_node_id
               WHERE e.id=%s""",
            (equipment_id,),
        )
        equipment = cur.fetchone()
        if not equipment:
            raise HTTPException(status_code=404, detail="Equipment not found.")
        cur.execute(
            """SELECT * FROM measuring_equipment_calibration_history
               WHERE equipment_id=%s ORDER BY calibration_date DESC, id DESC LIMIT 5""",
            (equipment_id,),
        )
        equipment["recent_calibrations"] = cur.fetchall()
        return equipment
    finally:
        cur.close()
        conn.close()


@router.post("/equipment", status_code=201)
def create_equipment(payload: EquipmentCreate):
    def action(cur):
        ensure_unique(cur, "measuring_equipment", "equipment_code", payload.equipment_code)
        equipment_type = get_record(cur, "measuring_equipment_types", payload.equipment_type_id, "Equipment type")
        if not equipment_type["is_active"]:
            raise HTTPException(status_code=400, detail="Equipment type is inactive.")
        validate_factory_node(cur, payload.factory_node_id)
        cur.execute(
            """INSERT INTO measuring_equipment
               (equipment_code,equipment_name,equipment_type_id,factory_node_id,manufacturer,
                model,serial_number,measurement_range,resolution,accuracy,calibration_cycle_days,
                status,description,image_url,created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (payload.equipment_code, payload.equipment_name, payload.equipment_type_id,
             payload.factory_node_id, payload.manufacturer, payload.model, payload.serial_number,
             payload.measurement_range, payload.resolution, payload.accuracy,
             payload.calibration_cycle_days, payload.status, payload.description, payload.image_url, payload.created_by),
        )
        return {"message": "Equipment created successfully.", "id": cur.lastrowid}
    return _run_write(action)


@router.put("/equipment/{equipment_id}")
def update_equipment(equipment_id: int, payload: EquipmentUpdate):
    def action(cur):
        get_record(cur, "measuring_equipment", equipment_id, "Equipment")
        ensure_unique(cur, "measuring_equipment", "equipment_code", payload.equipment_code, equipment_id)
        equipment_type = get_record(cur, "measuring_equipment_types", payload.equipment_type_id, "Equipment type")
        if not equipment_type["is_active"]:
            raise HTTPException(status_code=400, detail="Equipment type is inactive.")
        validate_factory_node(cur, payload.factory_node_id)
        cur.execute(
            """UPDATE measuring_equipment SET equipment_code=%s,equipment_name=%s,equipment_type_id=%s,
               factory_node_id=%s,manufacturer=%s,model=%s,serial_number=%s,measurement_range=%s,
               resolution=%s,accuracy=%s,calibration_cycle_days=%s,status=%s,description=%s,
               image_url=%s,updated_by=%s,version=version+1
               WHERE id=%s AND version=%s""",
            (payload.equipment_code, payload.equipment_name, payload.equipment_type_id,
             payload.factory_node_id, payload.manufacturer, payload.model, payload.serial_number,
             payload.measurement_range, payload.resolution, payload.accuracy,
             payload.calibration_cycle_days, payload.status, payload.description,
             payload.image_url, payload.updated_by, equipment_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="Equipment was changed by another user. Reload and try again.")
        return {"message": "Equipment updated successfully.", "version": payload.version + 1}
    return _run_write(action)


@router.patch("/equipment/{equipment_id}/status")
def update_equipment_status(equipment_id: int, payload: EquipmentStatusUpdate):
    def action(cur):
        get_record(cur, "measuring_equipment", equipment_id, "Equipment")
        cur.execute(
            """UPDATE measuring_equipment SET status=%s, updated_by=%s, version=version+1
               WHERE id=%s AND version=%s""",
            (payload.status, payload.updated_by, equipment_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="Equipment was changed by another user. Reload and try again.")
        return {"message": "Equipment status updated successfully.", "version": payload.version + 1}
    return _run_write(action)


@router.delete("/equipment/{equipment_id}")
def delete_equipment(equipment_id: int):
    def action(cur):
        get_record(cur, "measuring_equipment", equipment_id, "Equipment")
        cur.execute("SELECT COUNT(*) total FROM measuring_equipment_calibration_history WHERE equipment_id=%s", (equipment_id,))
        if cur.fetchone()["total"]:
            raise HTTPException(status_code=409, detail="Equipment has calibration data; set it Inactive instead.")
        cur.execute("DELETE FROM measuring_equipment WHERE id=%s", (equipment_id,))
        return {"message": "Equipment deleted successfully."}
    return _run_write(action)


# =========================================================
# CALIBRATION HISTORY
# =========================================================


@router.get("/equipment/{equipment_id}/calibration-history")
def list_calibration_history(equipment_id: int):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        get_record(cur, "measuring_equipment", equipment_id, "Equipment")
        cur.execute(
            """SELECT * FROM measuring_equipment_calibration_history
               WHERE equipment_id=%s ORDER BY calibration_date DESC, id DESC""",
            (equipment_id,),
        )
        return cur.fetchall()
    finally:
        cur.close()
        conn.close()


@router.post("/equipment/{equipment_id}/calibration-history", status_code=201)
def add_calibration_record(equipment_id: int, payload: CalibrationRecordCreate):
    def action(cur):
        equipment = get_record(cur, "measuring_equipment", equipment_id, "Equipment")
        next_due_date = payload.calibration_date + timedelta(days=equipment["calibration_cycle_days"])
        cur.execute(
            """INSERT INTO measuring_equipment_calibration_history
               (equipment_id,calibration_date,next_due_date,result,calibrated_by,certificate_no,
                remark,created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (equipment_id, payload.calibration_date, next_due_date, payload.result,
             payload.calibrated_by, payload.certificate_no, payload.remark, payload.created_by),
        )
        is_latest = equipment["last_calibration_date"] is None or payload.calibration_date >= equipment["last_calibration_date"]
        if is_latest:
            cur.execute(
                """UPDATE measuring_equipment
                   SET last_calibration_date=%s, next_calibration_date=%s, updated_by=%s, version=version+1
                   WHERE id=%s""",
                (payload.calibration_date, next_due_date, payload.created_by, equipment_id),
            )
        return {"message": "Calibration record added successfully.", "id": cur.lastrowid,
                "next_calibration_date": next_due_date.isoformat()}
    return _run_write(action)
