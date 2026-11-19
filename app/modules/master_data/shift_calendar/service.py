from decimal import Decimal

from fastapi import HTTPException


def get_shift(cur, shift_id: int):
    cur.execute("SELECT * FROM shifts WHERE id=%s", (shift_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Shift not found.")
    return row


def get_calendar_day(cur, day_id: int):
    cur.execute("SELECT * FROM calendar_days WHERE id=%s", (day_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Calendar day not found.")
    return row


def check_duplicate_shift_code(cur, value: str, exclude_id: int | None = None):
    sql = "SELECT id FROM shifts WHERE shift_code=%s"
    params = [value]
    if exclude_id is not None:
        sql += " AND id<>%s"
        params.append(exclude_id)
    cur.execute(sql, tuple(params))
    if cur.fetchone():
        raise HTTPException(status_code=409, detail="Shift code already exists.")


def validate_factory_node(cur, node_id: int):
    cur.execute(
        "SELECT id, code, name, status FROM factory_structure_nodes WHERE id = %s",
        (node_id,),
    )
    node = cur.fetchone()
    if not node:
        raise HTTPException(status_code=400, detail="Factory structure node not found.")
    if node["status"] != "ACTIVE":
        raise HTTPException(status_code=400, detail="Factory structure node is inactive.")
    return node


def format_time(value):
    """Normalize a TIME value to an 'HH:MM' string for JSON responses.

    mysql.connector hands back TIME columns as datetime.timedelta, which
    FastAPI's default encoder would otherwise serialize as raw seconds.
    """
    if value is None:
        return None
    minutes = _to_minutes(value)
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def format_shift_row(row: dict) -> dict:
    if row is None:
        return row
    row = dict(row)
    row["start_time"] = format_time(row.get("start_time"))
    row["end_time"] = format_time(row.get("end_time"))
    return row


def _to_minutes(t) -> int:
    # mysql.connector returns TIME columns read back from the DB as
    # datetime.timedelta (not datetime.time), while values coming straight
    # from a Pydantic-validated request body are datetime.time. Handle both.
    if hasattr(t, "seconds"):
        return (t.seconds // 60) % 1440
    return t.hour * 60 + t.minute


def compute_working_hours(start_time, end_time, break_minutes: int):
    start_min = _to_minutes(start_time)
    end_min = _to_minutes(end_time)
    is_overnight = end_min <= start_min
    total_minutes = (end_min + 1440 - start_min) if is_overnight else (end_min - start_min)
    if break_minutes > total_minutes:
        raise HTTPException(status_code=400, detail="Break Time cannot be greater than the total shift duration.")
    working_minutes = total_minutes - break_minutes
    working_hours = Decimal(working_minutes) / Decimal(60)
    return round(working_hours, 2), is_overnight


def _intervals_for(start_min: int, end_min: int):
    if end_min <= start_min:
        return [(start_min, 1440), (0, end_min)]
    return [(start_min, end_min)]


def _intervals_overlap(a_intervals, b_intervals) -> bool:
    for a1, a2 in a_intervals:
        for b1, b2 in b_intervals:
            if a1 < b2 and b1 < a2:
                return True
    return False


def node_ancestors(cur, node_id) -> list:
    """[node_id, parent_id, grand_parent_id, ...] up to the root of the factory structure."""
    if node_id is None:
        return []
    cur.execute("SELECT id, parent_id FROM factory_structure_nodes")
    parents = {row["id"]: row["parent_id"] for row in cur.fetchall()}
    chain, current = [], int(node_id)
    while current is not None and current not in chain:
        chain.append(current)
        current = parents.get(current)
    return chain


def _same_branch(cur, area_a, area_b) -> bool:
    """True when one area contains the other (or either one is company-wide)."""
    if area_a is None or area_b is None or area_a == area_b:
        return True
    return area_a in node_ancestors(cur, area_b) or area_b in node_ancestors(cur, area_a)


def _pattern_key(value) -> str:
    return (value or "").strip().lower()


def check_shift_overlap(cur, applicable_area_id, start_time, end_time, allow_overlap: bool,
                        exclude_id: int | None = None, shift_type: str | None = None):
    """Shifts of the same shift pattern (shift_type) must not overlap on the same branch of the
    factory structure. Different patterns are alternatives, an area runs only one of them."""
    if allow_overlap:
        return
    new_intervals = _intervals_for(_to_minutes(start_time), _to_minutes(end_time))
    sql = "SELECT * FROM shifts WHERE status='ACTIVE'"
    params = []
    if exclude_id is not None:
        sql += " AND id<>%s"
        params.append(exclude_id)
    cur.execute(sql, tuple(params))
    for candidate in cur.fetchall():
        if candidate["allow_overlap"]:
            continue
        if _pattern_key(candidate["shift_type"]) != _pattern_key(shift_type):
            continue
        if not _same_branch(cur, applicable_area_id, candidate["applicable_area_id"]):
            continue
        candidate_intervals = _intervals_for(_to_minutes(candidate["start_time"]), _to_minutes(candidate["end_time"]))
        if _intervals_overlap(new_intervals, candidate_intervals):
            raise HTTPException(
                status_code=409,
                detail=f"This shift overlaps with existing shift {candidate['shift_code']} of the same shift pattern in the same area.",
            )


def check_calendar_conflict(cur, calendar_date, applicable_area_id, day_type: str, exclude_id: int | None = None):
    if day_type == "OVERTIME":
        return
    sql = """SELECT id, day_type FROM calendar_days
             WHERE calendar_date=%s AND status='ACTIVE' AND day_type<>'OVERTIME'
               AND (applicable_area_id=%s OR applicable_area_id IS NULL OR %s IS NULL)"""
    params = [calendar_date, applicable_area_id, applicable_area_id]
    if exclude_id is not None:
        sql += " AND id<>%s"
        params.append(exclude_id)
    cur.execute(sql, tuple(params))
    conflict = cur.fetchone()
    if conflict:
        raise HTTPException(
            status_code=409,
            detail=f"This date/area already has a {conflict['day_type']} entry; only one non-Overtime calendar entry is allowed per date and area.",
        )


def write_audit(cur, entity_type: str, entity_id: int, action: str, from_status, to_status, actor: str | None, remark=None):
    cur.execute(
        """INSERT INTO shift_calendar_audit_log
           (entity_type, entity_id, action, from_status, to_status, actor, remark)
           VALUES (%s,%s,%s,%s,%s,%s,%s)""",
        (entity_type, entity_id, action, from_status, to_status, actor or "SYSTEM", remark),
    )


# =========================================================
# SHIFT PATTERN ASSIGNMENTS (area -> shift pattern)
# =========================================================


def resolve_area_pattern(cur, area_id, on_date):
    """Shift pattern valid for an area on a date: its own assignment, else the nearest parent's."""
    for level, node_id in enumerate(node_ancestors(cur, area_id)):
        cur.execute(
            """SELECT a.*, n.name AS area_name FROM shift_pattern_assignments a
               JOIN factory_structure_nodes n ON n.id = a.area_id
               WHERE a.area_id=%s AND a.effective_from<=%s AND (a.effective_to IS NULL OR a.effective_to>=%s)
               ORDER BY a.effective_from DESC LIMIT 1""",
            (node_id, on_date, on_date),
        )
        row = cur.fetchone()
        if row:
            row["inherited"] = level > 0
            return row
    return None


def check_assignment_period(cur, area_id, effective_from, effective_to, exclude_id=None):
    """One area cannot have two pattern assignments valid on the same day."""
    sql = """SELECT id, shift_type, effective_from, effective_to FROM shift_pattern_assignments
             WHERE area_id=%s AND (effective_to IS NULL OR effective_to>=%s)"""
    params = [area_id, effective_from]
    if effective_to is not None:
        sql += " AND effective_from<=%s"
        params.append(effective_to)
    if exclude_id is not None:
        sql += " AND id<>%s"
        params.append(exclude_id)
    cur.execute(sql, tuple(params))
    clash = cur.fetchone()
    if clash:
        raise HTTPException(
            status_code=409,
            detail=(f"This area already runs '{clash['shift_type']}' from {clash['effective_from']}"
                    f"{' to ' + str(clash['effective_to']) if clash['effective_to'] else ''}. "
                    "Close that assignment (end date) before adding a new one."),
        )


def check_pattern_exists(cur, shift_type):
    cur.execute("SELECT COUNT(*) AS total FROM shifts WHERE status='ACTIVE' AND LOWER(TRIM(shift_type))=%s",
                (_pattern_key(shift_type),))
    if not cur.fetchone()["total"]:
        raise HTTPException(status_code=400, detail="Shift pattern has no active shifts.")


def shift_covers(shift, minute_of_day: int) -> bool:
    start, end = _to_minutes(shift["start_time"]), _to_minutes(shift["end_time"])
    return any(a <= minute_of_day < b for a, b in _intervals_for(start, end))
