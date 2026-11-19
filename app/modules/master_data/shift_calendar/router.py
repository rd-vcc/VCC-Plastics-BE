from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from app.core.database import get_vcc_plastics_connection
from app.core.employee_names import attach_employee_names
from app.modules.master_data.shift_calendar.schemas import (
    CalendarDayCreate,
    CalendarDayStatusUpdate,
    CalendarDayUpdate,
    CopyShiftRequest,
    PatternAssignmentCreate,
    PatternAssignmentUpdate,
    ShiftCreate,
    ShiftStatusUpdate,
    ShiftUpdate,
)
from app.modules.master_data.shift_calendar.service import (
    check_assignment_period,
    check_calendar_conflict,
    check_pattern_exists,
    check_duplicate_shift_code,
    check_shift_overlap,
    compute_working_hours,
    format_shift_row,
    get_calendar_day,
    get_shift,
    resolve_area_pattern,
    shift_covers,
    validate_factory_node,
    write_audit,
)


router = APIRouter(prefix="/api/shift-calendar-master", tags=["Shift & Calendar Master"])


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
# STATISTICS
# =========================================================


@router.get("/statistics")
def statistics():
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT COUNT(*) total, SUM(status='ACTIVE') active FROM shifts")
        row = cur.fetchone()
        total_shifts = int(row["total"] or 0)
        active_shifts = int(row["active"] or 0)

        today = date.today()
        first_day = today.replace(day=1)
        if today.month == 12:
            next_month = today.replace(year=today.year + 1, month=1, day=1)
        else:
            next_month = today.replace(month=today.month + 1, day=1)
        days_in_month = (next_month - first_day).days

        cur.execute(
            """SELECT COUNT(DISTINCT calendar_date) total FROM calendar_days
               WHERE status='ACTIVE' AND day_type IN ('HOLIDAY','WEEKEND','FACTORY_SHUTDOWN','OTHER_HOLIDAY')
                 AND calendar_date >= %s AND calendar_date < %s""",
            (first_day, next_month),
        )
        non_working_days = cur.fetchone()["total"]
        cur.execute(
            """SELECT COUNT(DISTINCT calendar_date) total FROM calendar_days
               WHERE status='ACTIVE' AND day_type IN ('HOLIDAY','WEEKEND','OTHER_HOLIDAY')
                 AND calendar_date >= %s AND calendar_date < %s""",
            (first_day, next_month),
        )
        holidays_this_month = cur.fetchone()["total"]
        cur.execute(
            """SELECT COUNT(DISTINCT calendar_date) total FROM calendar_days
               WHERE status='ACTIVE' AND day_type IN ('SPECIAL_DAY','COMPANY_EVENT','OTHER_SPECIAL')
                 AND calendar_date >= %s AND calendar_date < %s""",
            (first_day, next_month),
        )
        special_days_this_month = cur.fetchone()["total"]

        return {
            "total_shifts": total_shifts,
            "active_shifts": active_shifts,
            "working_days_this_month": days_in_month - non_working_days,
            "holidays_this_month": holidays_this_month,
            "special_days_this_month": special_days_this_month,
        }
    finally:
        cur.close(); conn.close()


# =========================================================
# SHIFTS
# =========================================================


@router.get("/shifts")
def list_shifts(
    keyword: str | None = None, shift_type: str | None = None, status: str | None = None,
    applicable_area_id: int | None = None, effective_date: date | None = None,
    page: int = Query(default=1, ge=1), page_size: int = Query(default=50, ge=1, le=500),
):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        conditions, params = [], []
        if keyword:
            conditions.append("(s.shift_code LIKE %s OR s.shift_name LIKE %s)")
            value = f"%{keyword.strip()}%"; params += [value, value]
        if shift_type:
            conditions.append("s.shift_type=%s"); params.append(shift_type)
        if status:
            conditions.append("s.status=%s"); params.append(status.upper())
        if applicable_area_id is not None:
            conditions.append("s.applicable_area_id=%s"); params.append(applicable_area_id)
        if effective_date is not None:
            conditions.append("s.effective_date=%s"); params.append(effective_date)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        base = f"""
            FROM shifts s
            LEFT JOIN factory_structure_nodes n ON n.id = s.applicable_area_id
            {where}
        """
        cur.execute(f"SELECT COUNT(*) total {base}", tuple(params))
        total = cur.fetchone()["total"]
        sql = f"""
            SELECT s.*, n.code AS area_code, n.name AS area_name
            {base}
            ORDER BY s.shift_code
            LIMIT %s OFFSET %s
        """
        cur.execute(sql, tuple(params + [page_size, (page - 1) * page_size]))
        return {"items": [format_shift_row(r) for r in cur.fetchall()], "total": total, "page": page, "page_size": page_size}
    finally:
        cur.close(); conn.close()


@router.get("/shifts/{shift_id}")
def shift_detail(shift_id: int):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """SELECT s.*, n.code AS area_code, n.name AS area_name
               FROM shifts s LEFT JOIN factory_structure_nodes n ON n.id = s.applicable_area_id
               WHERE s.id=%s""",
            (shift_id,),
        )
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Shift not found.")
        return format_shift_row(row)
    finally:
        cur.close(); conn.close()


@router.get("/shifts/{shift_id}/audit-log")
def shift_audit_log(shift_id: int):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_shift(cur, shift_id)
        cur.execute(
            "SELECT * FROM shift_calendar_audit_log WHERE entity_type='SHIFT' AND entity_id=%s ORDER BY acted_at DESC, id DESC",
            (shift_id,),
        )
        return attach_employee_names(cur, cur.fetchall(), ["actor"])
    finally:
        cur.close(); conn.close()


@router.post("/shifts", status_code=201)
def create_shift(payload: ShiftCreate):
    def action(cur):
        check_duplicate_shift_code(cur, payload.shift_code)
        if payload.applicable_area_id:
            validate_factory_node(cur, payload.applicable_area_id)
        working_hours, is_overnight = compute_working_hours(payload.start_time, payload.end_time, payload.break_minutes)
        check_shift_overlap(cur, payload.applicable_area_id, payload.start_time, payload.end_time, payload.allow_overlap,
                            shift_type=payload.shift_type)
        cur.execute(
            """INSERT INTO shifts
               (shift_code, shift_name, shift_type, start_time, end_time, break_minutes, working_hours,
                is_overnight, applicable_area_id, effective_date, overtime_allowed, allow_overlap,
                color_code, description, status, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (payload.shift_code, payload.shift_name, payload.shift_type, payload.start_time, payload.end_time,
             payload.break_minutes, working_hours, is_overnight, payload.applicable_area_id, payload.effective_date,
             payload.overtime_allowed, payload.allow_overlap, payload.color_code, payload.description,
             payload.status, payload.created_by),
        )
        shift_id = cur.lastrowid
        write_audit(cur, "SHIFT", shift_id, "CREATE", None, payload.status, payload.created_by)
        return {"message": "Shift created successfully.", "id": shift_id, "working_hours": float(working_hours)}
    return _run_write(action)


@router.put("/shifts/{shift_id}")
def update_shift(shift_id: int, payload: ShiftUpdate):
    def action(cur):
        current = get_shift(cur, shift_id)
        check_duplicate_shift_code(cur, payload.shift_code, shift_id)
        if payload.applicable_area_id:
            validate_factory_node(cur, payload.applicable_area_id)
        working_hours, is_overnight = compute_working_hours(payload.start_time, payload.end_time, payload.break_minutes)
        check_shift_overlap(cur, payload.applicable_area_id, payload.start_time, payload.end_time, payload.allow_overlap,
                            exclude_id=shift_id, shift_type=payload.shift_type)
        cur.execute(
            """UPDATE shifts SET shift_code=%s, shift_name=%s, shift_type=%s, start_time=%s, end_time=%s,
               break_minutes=%s, working_hours=%s, is_overnight=%s, applicable_area_id=%s, effective_date=%s,
               overtime_allowed=%s, allow_overlap=%s, color_code=%s, description=%s, status=%s,
               updated_by=%s, version=version+1
               WHERE id=%s AND version=%s""",
            (payload.shift_code, payload.shift_name, payload.shift_type, payload.start_time, payload.end_time,
             payload.break_minutes, working_hours, is_overnight, payload.applicable_area_id, payload.effective_date,
             payload.overtime_allowed, payload.allow_overlap, payload.color_code, payload.description,
             payload.status, payload.updated_by, shift_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="This shift was changed by another user. Reload and try again.")
        write_audit(cur, "SHIFT", shift_id, "UPDATE", current["status"], payload.status, payload.updated_by)
        return {"message": "Shift updated successfully.", "version": payload.version + 1, "working_hours": float(working_hours)}
    return _run_write(action)


@router.patch("/shifts/{shift_id}/status")
def update_shift_status(shift_id: int, payload: ShiftStatusUpdate):
    def action(cur):
        current = get_shift(cur, shift_id)
        cur.execute(
            "UPDATE shifts SET status=%s, updated_by=%s, version=version+1 WHERE id=%s AND version=%s",
            (payload.status, payload.updated_by, shift_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="This shift was changed by another user. Reload and try again.")
        write_audit(cur, "SHIFT", shift_id, "ACTIVATE" if payload.status == "ACTIVE" else "DEACTIVATE",
                    current["status"], payload.status, payload.updated_by)
        return {"message": "Shift status updated successfully.", "version": payload.version + 1}
    return _run_write(action)


@router.post("/shifts/{shift_id}/copy", status_code=201)
def copy_shift(shift_id: int, payload: CopyShiftRequest):
    def action(cur):
        source = get_shift(cur, shift_id)
        check_duplicate_shift_code(cur, payload.new_shift_code)
        cur.execute(
            """INSERT INTO shifts
               (shift_code, shift_name, shift_type, start_time, end_time, break_minutes, working_hours,
                is_overnight, applicable_area_id, effective_date, overtime_allowed, allow_overlap,
                color_code, description, status, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'ACTIVE',%s)""",
            (payload.new_shift_code, f"{source['shift_name']} (Copy)", source["shift_type"], source["start_time"],
             source["end_time"], source["break_minutes"], source["working_hours"], source["is_overnight"],
             source["applicable_area_id"], source["effective_date"], source["overtime_allowed"],
             source["allow_overlap"], source["color_code"], source["description"], payload.created_by),
        )
        new_id = cur.lastrowid
        write_audit(cur, "SHIFT", new_id, "CREATE", None, "ACTIVE", payload.created_by, remark=f"Copied from shift {shift_id}")
        return {"message": "Shift copied successfully.", "id": new_id}
    return _run_write(action)


@router.delete("/shifts/{shift_id}")
def delete_shift(shift_id: int):
    def action(cur):
        get_shift(cur, shift_id)
        cur.execute("SELECT COUNT(*) total FROM shift_calendar_audit_log WHERE entity_type='SHIFT' AND entity_id=%s", (shift_id,))
        if cur.fetchone()["total"] > 1:
            raise HTTPException(status_code=409, detail="This shift has activity; set it Inactive instead of deleting.")
        cur.execute("DELETE FROM shifts WHERE id=%s", (shift_id,))
        return {"message": "Shift deleted successfully."}
    return _run_write(action)


# =========================================================
# CALENDAR DAYS
# =========================================================


@router.get("/calendar-days")
def list_calendar_days(
    year: int | None = None, month: int | None = None, day_type: str | None = None,
    applicable_area_id: int | None = None,
):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        conditions, params = [], []
        if year and month:
            first_day = date(year, month, 1)
            next_month = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
            conditions.append("d.calendar_date >= %s AND d.calendar_date < %s")
            params += [first_day, next_month]
        if day_type:
            types = [t.strip().upper() for t in day_type.split(",") if t.strip()]
            conditions.append(f"d.day_type IN ({','.join(['%s'] * len(types))})")
            params += types
        if applicable_area_id is not None:
            conditions.append("d.applicable_area_id=%s"); params.append(applicable_area_id)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        cur.execute(
            f"""SELECT d.*, n.code AS area_code, n.name AS area_name
                FROM calendar_days d LEFT JOIN factory_structure_nodes n ON n.id = d.applicable_area_id
                {where}
                ORDER BY d.calendar_date""",
            tuple(params),
        )
        return cur.fetchall()
    finally:
        cur.close(); conn.close()


@router.get("/calendar-days/{day_id}")
def calendar_day_detail(day_id: int):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        return get_calendar_day(cur, day_id)
    finally:
        cur.close(); conn.close()


@router.get("/calendar-days/{day_id}/audit-log")
def calendar_day_audit_log(day_id: int):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        get_calendar_day(cur, day_id)
        cur.execute(
            "SELECT * FROM shift_calendar_audit_log WHERE entity_type='CALENDAR_DAY' AND entity_id=%s ORDER BY acted_at DESC, id DESC",
            (day_id,),
        )
        return attach_employee_names(cur, cur.fetchall(), ["actor"])
    finally:
        cur.close(); conn.close()


@router.post("/calendar-days", status_code=201)
def create_calendar_day(payload: CalendarDayCreate):
    def action(cur):
        if payload.applicable_area_id:
            validate_factory_node(cur, payload.applicable_area_id)
        check_calendar_conflict(cur, payload.calendar_date, payload.applicable_area_id, payload.day_type)
        cur.execute(
            """INSERT INTO calendar_days
               (calendar_date, day_type, custom_type_name, name, applicable_area_id, description, status, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
            (payload.calendar_date, payload.day_type, payload.custom_type_name, payload.name, payload.applicable_area_id,
             payload.description, payload.status, payload.created_by),
        )
        day_id = cur.lastrowid
        write_audit(cur, "CALENDAR_DAY", day_id, "CREATE", None, payload.status, payload.created_by)
        return {"message": "Calendar day created successfully.", "id": day_id}
    return _run_write(action)


@router.put("/calendar-days/{day_id}")
def update_calendar_day(day_id: int, payload: CalendarDayUpdate):
    def action(cur):
        current = get_calendar_day(cur, day_id)
        if payload.applicable_area_id:
            validate_factory_node(cur, payload.applicable_area_id)
        check_calendar_conflict(cur, payload.calendar_date, payload.applicable_area_id, payload.day_type, exclude_id=day_id)
        cur.execute(
            """UPDATE calendar_days SET calendar_date=%s, day_type=%s, custom_type_name=%s, name=%s, applicable_area_id=%s,
               description=%s, status=%s, updated_by=%s, version=version+1
               WHERE id=%s AND version=%s""",
            (payload.calendar_date, payload.day_type, payload.custom_type_name, payload.name, payload.applicable_area_id,
             payload.description, payload.status, payload.updated_by, day_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="This calendar day was changed by another user. Reload and try again.")
        write_audit(cur, "CALENDAR_DAY", day_id, "UPDATE", current["status"], payload.status, payload.updated_by)
        return {"message": "Calendar day updated successfully.", "version": payload.version + 1}
    return _run_write(action)


@router.patch("/calendar-days/{day_id}/status")
def update_calendar_day_status(day_id: int, payload: CalendarDayStatusUpdate):
    def action(cur):
        current = get_calendar_day(cur, day_id)
        cur.execute(
            "UPDATE calendar_days SET status=%s, updated_by=%s, version=version+1 WHERE id=%s AND version=%s",
            (payload.status, payload.updated_by, day_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail="This calendar day was changed by another user. Reload and try again.")
        write_audit(cur, "CALENDAR_DAY", day_id, "ACTIVATE" if payload.status == "ACTIVE" else "DEACTIVATE",
                    current["status"], payload.status, payload.updated_by)
        return {"message": "Calendar day status updated successfully.", "version": payload.version + 1}
    return _run_write(action)


@router.delete("/calendar-days/{day_id}")
def delete_calendar_day(day_id: int):
    def action(cur):
        get_calendar_day(cur, day_id)
        cur.execute("SELECT COUNT(*) total FROM shift_calendar_audit_log WHERE entity_type='CALENDAR_DAY' AND entity_id=%s", (day_id,))
        if cur.fetchone()["total"] > 1:
            raise HTTPException(status_code=409, detail="This calendar day has activity; set it Inactive instead of deleting.")
        cur.execute("DELETE FROM calendar_days WHERE id=%s", (day_id,))
        return {"message": "Calendar day deleted successfully."}
    return _run_write(action)


# =========================================================
# SHIFT PATTERNS: which shift pattern (shift_type) each area runs
# =========================================================


@router.get("/shift-patterns")
def list_shift_patterns():
    """Shift patterns = distinct shift_type of the active shifts, with their shifts."""
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM shifts WHERE status='ACTIVE' AND shift_type IS NOT NULL AND TRIM(shift_type)<>'' ORDER BY shift_type, start_time")
        patterns = {}
        for row in cur.fetchall():
            row = format_shift_row(row)
            key = row["shift_type"].strip()
            patterns.setdefault(key, {"shift_type": key, "shifts": []})["shifts"].append(
                {k: row[k] for k in ("id", "shift_code", "shift_name", "start_time", "end_time", "working_hours", "is_overnight", "color_code")})
        return list(patterns.values())
    finally:
        cur.close(); conn.close()


@router.get("/pattern-assignments")
def list_pattern_assignments(area_id: int | None = None):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        sql = """SELECT a.*, n.code AS area_code, n.name AS area_name FROM shift_pattern_assignments a
                 JOIN factory_structure_nodes n ON n.id = a.area_id"""
        params = []
        if area_id is not None:
            sql += " WHERE a.area_id=%s"; params.append(area_id)
        cur.execute(sql + " ORDER BY n.level_no, n.sort_order, a.effective_from DESC", tuple(params))
        return cur.fetchall()
    finally:
        cur.close(); conn.close()


@router.post("/pattern-assignments", status_code=201)
def create_pattern_assignment(payload: PatternAssignmentCreate):
    def action(cur):
        validate_factory_node(cur, payload.area_id)
        check_pattern_exists(cur, payload.shift_type)
        check_assignment_period(cur, payload.area_id, payload.effective_from, payload.effective_to)
        cur.execute(
            """INSERT INTO shift_pattern_assignments (area_id, shift_type, effective_from, effective_to, remark, created_by)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (payload.area_id, payload.shift_type, payload.effective_from, payload.effective_to, payload.remark, payload.created_by),
        )
        return {"message": "Shift pattern assigned.", "id": cur.lastrowid}
    return _run_write(action)


@router.put("/pattern-assignments/{assignment_id}")
def update_pattern_assignment(assignment_id: int, payload: PatternAssignmentUpdate):
    def action(cur):
        cur.execute("SELECT id FROM shift_pattern_assignments WHERE id=%s", (assignment_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Shift pattern assignment not found.")
        validate_factory_node(cur, payload.area_id)
        check_pattern_exists(cur, payload.shift_type)
        check_assignment_period(cur, payload.area_id, payload.effective_from, payload.effective_to, exclude_id=assignment_id)
        cur.execute(
            """UPDATE shift_pattern_assignments SET area_id=%s, shift_type=%s, effective_from=%s, effective_to=%s,
               remark=%s, updated_by=%s WHERE id=%s""",
            (payload.area_id, payload.shift_type, payload.effective_from, payload.effective_to, payload.remark,
             payload.updated_by, assignment_id),
        )
        return {"message": "Shift pattern assignment updated."}
    return _run_write(action)


@router.delete("/pattern-assignments/{assignment_id}")
def delete_pattern_assignment(assignment_id: int):
    def action(cur):
        cur.execute("DELETE FROM shift_pattern_assignments WHERE id=%s", (assignment_id,))
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail="Shift pattern assignment not found.")
        return {"message": "Shift pattern assignment deleted."}
    return _run_write(action)


@router.get("/area-patterns")
def area_patterns(on_date: date | None = None):
    """Effective shift pattern of every active area on a date (own or inherited from a parent)."""
    on_date = on_date or date.today()
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT id FROM factory_structure_nodes WHERE status='ACTIVE'")
        result = []
        for node in cur.fetchall():
            pattern = resolve_area_pattern(cur, node["id"], on_date)
            result.append({
                "area_id": node["id"],
                "shift_type": pattern["shift_type"] if pattern else None,
                "source_area_id": pattern["area_id"] if pattern else None,
                "source_area_name": pattern["area_name"] if pattern else None,
                "inherited": pattern["inherited"] if pattern else False,
            })
        return result
    finally:
        cur.close(); conn.close()


@router.get("/current-shift")
def current_shift(area_id: int, at: datetime | None = None):
    """Shift an area is running at a moment: area pattern (own/inherited) + the shift covering that time.
    Used by operation screens and OEE so operators never pick the shift by hand."""
    at = at or datetime.now()
    minute = at.hour * 60 + at.minute
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        for work_date in (at.date(), at.date() - timedelta(days=1)):  # early-morning part of an overnight shift
            pattern = resolve_area_pattern(cur, area_id, work_date)
            if not pattern:
                continue
            cur.execute(
                """SELECT * FROM shifts WHERE status='ACTIVE' AND LOWER(TRIM(shift_type))=%s AND effective_date<=%s
                   ORDER BY start_time""",
                (pattern["shift_type"].strip().lower(), work_date),
            )
            for shift in cur.fetchall():
                starts_today = _minutes(shift["start_time"]) <= minute
                if work_date == at.date() and shift_covers(shift, minute) and (starts_today or not shift["is_overnight"]):
                    return {"work_date": work_date, "pattern": pattern, "shift": format_shift_row(shift)}
                if work_date != at.date() and shift["is_overnight"] and minute < _minutes(shift["end_time"]):
                    return {"work_date": work_date, "pattern": pattern, "shift": format_shift_row(shift)}
        return {"work_date": at.date(), "pattern": resolve_area_pattern(cur, area_id, at.date()), "shift": None}
    finally:
        cur.close(); conn.close()


def _minutes(t) -> int:
    if hasattr(t, "seconds"):
        return (t.seconds // 60) % 1440
    return t.hour * 60 + t.minute
