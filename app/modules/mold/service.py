"""Mold Management core (screens 3.1 - 3.7).

One mold has exactly one status and one location at a time:
- Mounted on a machine (molds.current_machine_id, open row in mold_installations) -> status IN_PRODUCTION,
  location = that machine. Production Execution installs the mold when a Work Order starts and removes it
  (reason END_OF_PLAN) when the Work Order ends; a mold of another Work Order starting on the same machine
  removes the mounted one (reason CHANGE_PRODUCT). Tooling staff can also install / remove manually.
- Not mounted -> AVAILABLE (standby), IN_MAINTENANCE, IN_REPAIR, LOCKED (quality hold), RETIRED
  (out of service) or SCRAPPED, located on a mold location node (storage / maintenance / repair / scrap area)
  or NOT_ASSIGNED.
Every change is written to mold_events (status timeline, location history, maintenance / repair history).

Derived values:
    life_used %      = current_shot / design_shot x 100
    daily avg shots  = shots of the last 30 days (mold_shot_daily) / days since the first shot day in that window
    est. end of life = today + remaining shots / daily avg
    next PM          = earliest of last PM date + PM interval (days) and the day the PM shot interval is reached
    health score     = 100 - life (>60 %) - repairs (180 days) - PM overdue / due - mold downtime (30 days)
                       - NG rate (30 days) - locked; see health()
"""

import math
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import HTTPException

STATUSES = ["IN_PRODUCTION", "AVAILABLE", "IN_MAINTENANCE", "IN_REPAIR", "LOCKED", "RETIRED", "SCRAPPED"]
OUT_OF_SERVICE = {"RETIRED", "SCRAPPED"}
LOCATION_TYPES = ["MACHINE", "STORAGE", "MAINTENANCE", "REPAIR", "SCRAP", "NOT_ASSIGNED"]
# removal reason: planned?
REMOVAL_REASONS = {"END_OF_PLAN": True, "CHANGE_PRODUCT": True, "PM": True, "NG_HIGH": False, "BREAKDOWN": False, "OTHER": False}
MAINTENANCE_TYPES = ["PM_SHOT", "PM_TIME", "CLEANING", "OVERHAUL", "INSPECTION", "REPAIR"]
LIFE_BUCKETS = [(0, 25), (25, 50), (50, 75), (75, 90), (90, None)]
REPAIR_LONG_DAYS = 3
MAINTENANCE_LONG_DAYS = 7
INACTIVE_DAYS = 30

MOLD_SELECT = """
    SELECT m.*, n.node_code AS location_code, n.node_name AS location_name, n.capacity AS location_capacity,
           t.location_category, t.type_name AS location_type_name,
           e.equipment_code AS machine_code, e.equipment_name AS machine_name, e.factory_node_id AS machine_area_id,
           fn.name AS machine_area
    FROM molds m
    LEFT JOIN mold_location_nodes n ON n.id = m.current_location_id
    LEFT JOIN mold_location_node_types t ON t.id = n.node_type_id
    LEFT JOIN equipment e ON e.id = m.current_machine_id
    LEFT JOIN factory_structure_nodes fn ON fn.id = e.factory_node_id
"""


def _now():
    return datetime.now().replace(microsecond=0)


def get_mold(cur, mold_id, for_update=False):
    cur.execute(f"{MOLD_SELECT} WHERE m.id=%s" + (" FOR UPDATE" if for_update else ""), (mold_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Mold not found.")
    return row


def log_event(cur, mold_id, event_type, actor, **kw):
    cols = ["mold_id", "event_type", "actor", "event_at"]
    vals = [mold_id, event_type, actor or "SYSTEM", kw.pop("event_at", None) or _now()]
    for k, v in kw.items():
        cols.append(k)
        vals.append(v)
    cur.execute(f"INSERT INTO mold_events ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))})", tuple(vals))


def _set(cur, mold, actor, **fields):
    """Update the mold, stamping status_since / location_since when they change."""
    if "status" in fields and fields["status"] != mold["status"]:
        fields["status_since"] = _now()
    if ("current_location_id" in fields and fields["current_location_id"] != mold["current_location_id"]) or \
       ("current_machine_id" in fields and fields["current_machine_id"] != mold["current_machine_id"]):
        fields["location_since"] = _now()
    sets = ", ".join(f"{k}=%s" for k in fields)
    cur.execute(f"UPDATE molds SET {sets}, updated_by=%s WHERE id=%s", (*fields.values(), actor or "SYSTEM", mold["id"]))


def _location(cur, location_id):
    if not location_id:
        return None
    cur.execute("""SELECT n.*, t.location_category FROM mold_location_nodes n JOIN mold_location_node_types t ON t.id=n.node_type_id
                   WHERE n.id=%s AND n.status='ACTIVE'""", (location_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Location not found or inactive.")
    return row


def _running_wo(cur, mold_id):
    cur.execute("SELECT id, wo_no, machine_id FROM production_plan_details WHERE mold_id=%s AND status='IN_PRODUCTION' LIMIT 1", (mold_id,))
    return cur.fetchone()


# =========================================================
# INSTALL / REMOVE / MOVE
# =========================================================


def install(cur, mold_id, machine_id, actor, work_order_id=None, minutes=None, remark=None, source="MANUAL", installed_by=None):
    mold = get_mold(cur, mold_id, for_update=True)
    if mold["current_machine_id"]:
        if mold["current_machine_id"] == machine_id:
            return mold
        raise HTTPException(status_code=409, detail=f"Mold is mounted on {mold['machine_code']}. Remove it first.")
    if mold["status"] != "AVAILABLE":
        raise HTTPException(status_code=409, detail="Only an available (standby) mold can be installed.")
    cur.execute("""SELECT e.id, e.equipment_code, t.category FROM equipment e JOIN equipment_types t ON t.id=e.equipment_type_id WHERE e.id=%s""", (machine_id,))
    machine = cur.fetchone()
    if not machine or machine["category"] != "MACHINE":
        raise HTTPException(status_code=400, detail="Choose an injection machine.")
    cur.execute("SELECT id, mold_code FROM molds WHERE current_machine_id=%s AND id<>%s", (machine_id, mold_id))
    other = cur.fetchone()
    if other:
        if source == "MANUAL":
            raise HTTPException(status_code=409, detail=f"Machine {machine['equipment_code']} already has mold {other['mold_code']}. Remove it first.")
        remove(cur, other["id"], "CHANGE_PRODUCT", actor, source=source)
    now = _now()
    cur.execute("""INSERT INTO mold_installations (mold_id, machine_id, work_order_id, installed_at, installed_by, install_minutes, shots_at_install, remark, source)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (mold_id, machine_id, work_order_id, now, installed_by or actor, minutes, mold["current_shot"] or 0, remark, source))
    _set(cur, mold, actor, status="IN_PRODUCTION", current_machine_id=machine_id)
    log_event(cur, mold_id, "INSTALL", actor, from_status=mold["status"], to_status="IN_PRODUCTION", from_location_id=mold["current_location_id"],
              machine_id=machine_id, work_order_id=work_order_id, reason=remark)
    return get_mold(cur, mold_id)


def remove(cur, mold_id, reason, actor, minutes=None, remark=None, to_location_id=None, source="MANUAL"):
    reason = (reason or "OTHER").upper()
    if reason not in REMOVAL_REASONS:
        raise HTTPException(status_code=400, detail="Unknown removal reason.")
    mold = get_mold(cur, mold_id, for_update=True)
    if not mold["current_machine_id"]:
        raise HTTPException(status_code=409, detail="Mold is not mounted on a machine.")
    if source == "MANUAL":
        wo = _running_wo(cur, mold_id)
        if wo:
            raise HTTPException(status_code=409, detail=f"Work order {wo['wo_no']} is in production with this mold. Hold or end it first.")
    loc = _location(cur, to_location_id) if to_location_id else None
    now = _now()
    cur.execute("""UPDATE mold_installations SET removed_at=%s, removed_by=%s, remove_minutes=%s, shots_at_remove=%s, removal_reason=%s, planned=%s,
                   remark=COALESCE(%s, remark) WHERE mold_id=%s AND removed_at IS NULL""",
                (now, actor, minutes, mold["current_shot"] or 0, reason, REMOVAL_REASONS[reason], remark, mold_id))
    to_status = {"PM": "IN_MAINTENANCE", "BREAKDOWN": "IN_REPAIR"}.get(reason, "AVAILABLE")
    fields = {"status": to_status, "current_machine_id": None}
    if loc:
        fields["current_location_id"] = loc["id"]
    _set(cur, mold, actor, **fields)
    log_event(cur, mold_id, "REMOVE", actor, from_status=mold["status"], to_status=to_status, machine_id=mold["current_machine_id"],
              to_location_id=fields.get("current_location_id", mold["current_location_id"]), reason=f"{reason}{f': {remark}' if remark else ''}")
    if to_status == "IN_MAINTENANCE":
        log_event(cur, mold_id, "MAINTENANCE_START", actor, from_status=mold["status"], to_status=to_status, maintenance_type="PM_SHOT", reason=remark)
    elif to_status == "IN_REPAIR":
        log_event(cur, mold_id, "REPAIR_START", actor, from_status=mold["status"], to_status=to_status, maintenance_type="REPAIR", reason=remark or "Breakdown")


def move(cur, mold_id, location_id, actor, reason=None):
    mold = get_mold(cur, mold_id, for_update=True)
    if mold["current_machine_id"]:
        raise HTTPException(status_code=409, detail="Mold is mounted on a machine. Remove it first.")
    loc = _location(cur, location_id)
    if loc["id"] == mold["current_location_id"]:
        raise HTTPException(status_code=409, detail="Mold is already at this location.")
    if loc["capacity"]:
        cur.execute("SELECT COUNT(*) AS n FROM molds WHERE current_location_id=%s AND current_machine_id IS NULL AND is_active=1", (loc["id"],))
        if cur.fetchone()["n"] >= loc["capacity"]:
            raise HTTPException(status_code=409, detail=f"Location {loc['node_code']} is full ({loc['capacity']}).")
    _set(cur, mold, actor, current_location_id=loc["id"])
    log_event(cur, mold_id, "MOVE", actor, from_location_id=mold["current_location_id"], to_location_id=loc["id"], reason=reason)


# =========================================================
# STATUS ACTIONS
# =========================================================

ACTIONS = {
    # action: (allowed from, to status, event)
    "SEND_MAINTENANCE": ({"AVAILABLE", "LOCKED"}, "IN_MAINTENANCE", "MAINTENANCE_START"),
    "SEND_REPAIR": ({"AVAILABLE", "IN_MAINTENANCE", "LOCKED"}, "IN_REPAIR", "REPAIR_START"),
    "COMPLETE": ({"IN_MAINTENANCE", "IN_REPAIR"}, "AVAILABLE", None),
    "LOCK": ({"AVAILABLE", "IN_MAINTENANCE", "IN_REPAIR"}, "LOCKED", "LOCK"),
    "UNLOCK": ({"LOCKED"}, "AVAILABLE", "UNLOCK"),
    "RETIRE": ({"AVAILABLE", "LOCKED", "IN_REPAIR", "IN_MAINTENANCE"}, "RETIRED", "RETIRE"),
    "SCRAP": ({"AVAILABLE", "LOCKED", "IN_REPAIR", "RETIRED"}, "SCRAPPED", "SCRAP"),
    "REACTIVATE": ({"RETIRED"}, "AVAILABLE", "REACTIVATE"),
}


def change_status(cur, mold_id, action, actor, reason=None, location_id=None, maintenance_type=None, technician=None, downtime_hours=None, record_pm=True):
    action = (action or "").upper()
    if action not in ACTIONS:
        raise HTTPException(status_code=400, detail="Unknown action.")
    allowed, to_status, event = ACTIONS[action]
    mold = get_mold(cur, mold_id, for_update=True)
    if mold["current_machine_id"]:
        raise HTTPException(status_code=409, detail="Mold is mounted on a machine. Remove it first.")
    if mold["status"] not in allowed:
        raise HTTPException(status_code=409, detail=f"Action not allowed while the mold is {mold['status']}.")
    if action in {"LOCK", "SEND_REPAIR", "RETIRE", "SCRAP"} and not (reason or "").strip():
        raise HTTPException(status_code=400, detail="A reason is required.")
    loc = _location(cur, location_id) if location_id else None
    fields = {"status": to_status}
    if loc:
        fields["current_location_id"] = loc["id"]
    if action == "LOCK":
        fields["lock_reason"] = reason.strip()
    if action in {"UNLOCK", "COMPLETE", "REACTIVATE"}:
        fields["lock_reason"] = None
    if action == "COMPLETE":
        started = mold["status_since"] or _now()
        hours = downtime_hours if downtime_hours is not None else round((_now() - started).total_seconds() / 3600, 2)
        cur.execute("""SELECT maintenance_type FROM mold_events WHERE mold_id=%s AND event_type IN ('MAINTENANCE_START','REPAIR_START')
                       ORDER BY event_at DESC, id DESC LIMIT 1""", (mold_id,))
        start = cur.fetchone()
        repair = mold["status"] == "IN_REPAIR"
        if not repair and record_pm:   # an inspection does not restart the PM interval
            fields["last_pm_at"] = _now()
            fields["last_pm_shot"] = mold["current_shot"] or 0
        _set(cur, mold, actor, **fields)
        log_event(cur, mold_id, "REPAIR_DONE" if repair else "MAINTENANCE_DONE", actor, from_status=mold["status"], to_status=to_status,
                  maintenance_type=maintenance_type or (start and start["maintenance_type"]) or ("REPAIR" if repair else "PM_TIME"),
                  technician=technician or actor, downtime_hours=hours, reason=reason,
                  from_location_id=mold["current_location_id"] if loc else None, to_location_id=loc["id"] if loc else None)
        return
    _set(cur, mold, actor, **fields)
    log_event(cur, mold_id, event, actor, from_status=mold["status"], to_status=to_status, reason=reason,
              maintenance_type=maintenance_type or ("REPAIR" if action == "SEND_REPAIR" else "PM_TIME" if action == "SEND_MAINTENANCE" else None),
              technician=technician, from_location_id=mold["current_location_id"] if loc else None, to_location_id=loc["id"] if loc else None)


def adjust_shots(cur, mold_id, value, reason, actor):
    if not (reason or "").strip():
        raise HTTPException(status_code=400, detail="A reason is required.")
    if value < 0:
        raise HTTPException(status_code=400, detail="Shot count cannot be negative.")
    mold = get_mold(cur, mold_id, for_update=True)
    cur.execute("UPDATE molds SET current_shot=%s, updated_by=%s WHERE id=%s", (value, actor, mold_id))
    log_event(cur, mold_id, "SHOT_ADJUST", actor, shot_before=mold["current_shot"] or 0, shot_after=value, reason=reason.strip())


def add_shots(cur, mold_id, shots, source="MES"):
    """Output of Production Execution (and later PLC / IoT) adds shots to the mold and to today's counter."""
    if not mold_id or shots <= 0:
        return
    cur.execute("UPDATE molds SET current_shot=COALESCE(current_shot,0)+%s WHERE id=%s", (shots, mold_id))
    cur.execute("""INSERT INTO mold_shot_daily (mold_id, shot_date, shots, source) VALUES (%s, CURDATE(), %s, %s)
                   ON DUPLICATE KEY UPDATE shots=shots+VALUES(shots)""", (mold_id, shots, source))


# =========================================================
# PRODUCTION EXECUTION HOOKS
# =========================================================


def busy_elsewhere(cur, mold_id, machine_id):
    cur.execute("""SELECT e.equipment_code FROM molds m JOIN equipment e ON e.id=m.current_machine_id
                   WHERE m.id=%s AND m.current_machine_id<>%s""", (mold_id, machine_id))
    row = cur.fetchone()
    return row["equipment_code"] if row else None


def on_wo_start(cur, wo, operator, actor):
    if not wo.get("mold_id") or not wo.get("machine_id"):
        return
    cur.execute("SELECT current_machine_id FROM molds WHERE id=%s", (wo["mold_id"],))
    row = cur.fetchone()
    if row and row["current_machine_id"] == wo["machine_id"]:
        cur.execute("UPDATE mold_installations SET work_order_id=%s WHERE mold_id=%s AND removed_at IS NULL", (wo["id"], wo["mold_id"]))
        return
    install(cur, wo["mold_id"], wo["machine_id"], actor, work_order_id=wo["id"], source="MES", installed_by=operator)


def on_wo_end(cur, wo, actor):
    if not wo.get("mold_id"):
        return
    cur.execute("SELECT current_machine_id FROM molds WHERE id=%s", (wo["mold_id"],))
    row = cur.fetchone()
    if row and row["current_machine_id"] and row["current_machine_id"] == wo.get("machine_id"):
        remove(cur, wo["mold_id"], "END_OF_PLAN", actor, remark=wo.get("wo_no"), source="MES")


# =========================================================
# DERIVED VALUES
# =========================================================


def life_bucket(pct):
    for lo, hi in LIFE_BUCKETS:
        if hi is None or pct < hi:
            return f"{lo}-{hi or 100}"
    return "90-100"


def _overlap_hours(a0, a1, b0, b1):
    s, e = max(a0, b0), min(a1, b1)
    return max((e - s).total_seconds(), 0) / 3600


def enrich(cur, molds):
    """Add life, PM, health, product, work order and location fields to mold rows (batch queries)."""
    if not molds:
        return molds
    ids = [m["id"] for m in molds]
    marks = ",".join(["%s"] * len(ids))
    today = date.today()
    now = _now()
    d30 = now - timedelta(days=30)

    cur.execute(f"""SELECT mold_id, shot_date, shots FROM mold_shot_daily WHERE mold_id IN ({marks}) AND shot_date > %s""", (*ids, today - timedelta(days=30)))
    shots = defaultdict(list)
    for r in cur.fetchall():
        shots[r["mold_id"]].append(r)
    cur.execute(f"""SELECT mp.mold_id, mp.is_primary, p.id AS product_id, p.product_code, p.product_name, p.image_url AS product_image
                    FROM mold_products mp JOIN products p ON p.id=mp.product_id WHERE mp.mold_id IN ({marks}) AND mp.status='ACTIVE'
                    ORDER BY mp.is_primary DESC, mp.id""", tuple(ids))
    products = defaultdict(list)
    for r in cur.fetchall():
        products[r["mold_id"]].append(r)
    cur.execute(f"""SELECT d.id, d.wo_no, d.status, d.mold_id, d.machine_id, d.operator_code, d.started_at, p.product_name, p.product_code
                    FROM production_plan_details d LEFT JOIN products p ON p.id=d.product_id
                    WHERE d.mold_id IN ({marks}) AND d.status IN ('IN_PRODUCTION','ON_HOLD') ORDER BY d.status='IN_PRODUCTION' DESC""", tuple(ids))
    wos = {}
    for r in cur.fetchall():
        wos.setdefault(r["mold_id"], r)
    cur.execute(f"""SELECT mold_id, COUNT(*) AS n FROM mold_events WHERE mold_id IN ({marks}) AND event_type='REPAIR_START' AND event_at > %s GROUP BY mold_id""",
                (*ids, now - timedelta(days=180)))
    repairs = {r["mold_id"]: int(r["n"]) for r in cur.fetchall()}
    cur.execute(f"""SELECT mold_id, SUM(good_qty) AS good, SUM(reject_qty) AS ng FROM production_execution_logs
                    WHERE mold_id IN ({marks}) AND event_at > %s GROUP BY mold_id""", (*ids, d30))
    quality = {r["mold_id"]: (int(r["good"] or 0), int(r["ng"] or 0)) for r in cur.fetchall()}
    cur.execute(f"""SELECT mold_id, machine_id, installed_at, COALESCE(removed_at, NOW()) AS removed_at FROM mold_installations
                    WHERE mold_id IN ({marks}) AND COALESCE(removed_at, NOW()) > %s""", (*ids, d30))
    installs = defaultdict(list)
    for r in cur.fetchall():
        installs[r["mold_id"]].append(r)
    cur.execute("""SELECT equipment_id, started_at, COALESCE(ended_at, NOW()) AS ended_at FROM downtime_events
                   WHERE status<>'CANCELLED' AND COALESCE(ended_at, NOW()) > %s""", (d30,))
    downtime = defaultdict(list)
    for r in cur.fetchall():
        downtime[r["equipment_id"]].append(r)
    cur.execute(f"""SELECT mold_id, MAX(COALESCE(removed_at, NOW())) AS last_used FROM mold_installations WHERE mold_id IN ({marks}) GROUP BY mold_id""", tuple(ids))
    last_used = {r["mold_id"]: r["last_used"] for r in cur.fetchall()}

    for m in molds:
        cur_shot, design = int(m["current_shot"] or 0), int(m["design_shot"] or 0)
        m["life_used_pct"] = round(cur_shot / design * 100, 1) if design else None
        m["remaining_shot"] = max(design - cur_shot, 0) if design else None
        rows = shots.get(m["id"], [])
        total30 = sum(int(r["shots"]) for r in rows)
        first = min((r["shot_date"] for r in rows), default=None)
        days = max((today - first).days + 1, 1) if first else 30
        avg = total30 / days if total30 else 0
        m["daily_avg_shot"] = round(avg)
        m["shots_30d"] = total30
        m["shots_today"] = sum(int(r["shots"]) for r in rows if r["shot_date"] == today)
        m["eol_days"] = math.ceil(m["remaining_shot"] / avg) if avg and m["remaining_shot"] is not None else None
        m["eol_date"] = (today + timedelta(days=m["eol_days"])).isoformat() if m["eol_days"] is not None else None
        # PM due
        cands = []
        base = m["last_pm_at"] or m["created_at"]
        if m["pm_interval_days"] and base:
            cands.append(((base + timedelta(days=int(m["pm_interval_days"]))).date(), "PM_TIME"))
        m["next_pm_shot"] = None
        if m["pm_interval_shot"]:
            m["next_pm_shot"] = int(m["last_pm_shot"] or 0) + int(m["pm_interval_shot"])
            left = m["next_pm_shot"] - cur_shot
            if left <= 0:
                cands.append((today, "PM_SHOT"))
            elif avg:
                cands.append((today + timedelta(days=math.ceil(left / avg)), "PM_SHOT"))
        due = min(cands, default=None)
        active = m["status"] not in OUT_OF_SERVICE
        m["pm_due_date"] = due[0].isoformat() if due and active else None
        m["pm_type"] = due[1] if due and active else None
        m["pm_days_left"] = (due[0] - today).days if due and active else None
        m["pm_overdue"] = bool(due and active and (due[0] < today or (m["next_pm_shot"] is not None and cur_shot >= m["next_pm_shot"])))
        # product / work order
        prods = products.get(m["id"], [])
        m["products"] = prods
        w = wos.get(m["id"])
        m["work_order"] = w
        m["product_name"] = (w and w["product_name"]) or (prods[0]["product_name"] if prods else None)
        m["part_no"] = (w and w["product_code"]) or (prods[0]["product_code"] if prods else None)
        m["product_image"] = prods[0]["product_image"] if prods else None
        # location
        if m["current_machine_id"]:
            m["location_type"], m["location_label"] = "MACHINE", m["machine_code"]
        elif m["current_location_id"]:
            m["location_type"], m["location_label"] = (m["location_category"] or "STORAGE"), m["location_name"]
        else:
            m["location_type"], m["location_label"] = "NOT_ASSIGNED", None
        # quality / downtime (30 days)
        good, ng = quality.get(m["id"], (0, 0))
        m["good_30d"], m["ng_30d"] = good, ng
        m["ng_rate_30d"] = round(ng / (good + ng) * 100, 2) if good + ng else None
        dt = 0.0
        for ins in installs.get(m["id"], []):
            for d in downtime.get(ins["machine_id"], []):
                dt += _overlap_hours(max(ins["installed_at"], d30), ins["removed_at"], d["started_at"], d["ended_at"])
        m["downtime_hours_30d"] = round(dt, 2)
        m["repairs_180d"] = repairs.get(m["id"], 0)
        lu = last_used.get(m["id"])
        m["idle_days"] = None if m["current_machine_id"] else (today - (lu or m["created_at"]).date()).days if (lu or m["created_at"]) else None
        m["since_hours"] = round((now - m["location_since"]).total_seconds() / 3600, 1) if m.get("location_since") else None
        m["status_hours"] = round((now - m["status_since"]).total_seconds() / 3600, 1) if m.get("status_since") else None
        m["health"], m["health_breakdown"] = health(m)
        m["alerts"] = alerts(m)
    return molds


def health(m):
    """0-100. Penalties: life above 60 % (max 30), repairs in 180 days (8 each, max 24), PM overdue 15 / due in 7 days 5,
    mold downtime in 30 days (1.5 per hour, max 12), NG rate in 30 days (2 per %, max 14), locked 10."""
    b = {}
    life = m.get("life_used_pct") or 0
    b["life"] = round(min(max((life - 60) * 0.75, 0), 30), 1)
    b["repair"] = min(m.get("repairs_180d", 0) * 8, 24)
    in_work = m.get("status") in ("IN_MAINTENANCE", "IN_REPAIR")
    b["pm"] = 0 if in_work else 15 if m.get("pm_overdue") else 5 if m.get("pm_days_left") is not None and m["pm_days_left"] <= 7 else 0
    b["downtime"] = round(min((m.get("downtime_hours_30d") or 0) * 1.5, 12), 1)
    b["quality"] = round(min((m.get("ng_rate_30d") or 0) * 2, 14), 1)
    b["locked"] = 10 if m.get("status") == "LOCKED" else 0
    return max(0, round(100 - sum(b.values()))), b


def alerts(m):
    out = []
    life = m.get("life_used_pct")
    active = m["status"] not in OUT_OF_SERVICE
    if life is not None and life >= 100:
        out.append(("OVER_LIFE", "CRITICAL"))
    elif life is not None and life >= 90:
        out.append(("LIFE_90", "CRITICAL"))
    elif life is not None and life >= 75:
        out.append(("LIFE_75", "WARNING"))
    if m["status"] not in ("IN_MAINTENANCE", "IN_REPAIR"):   # PM is being done / mold is out for repair
        if m.get("pm_overdue"):
            out.append(("PM_OVERDUE", "CRITICAL"))
        elif m.get("pm_days_left") is not None and m["pm_days_left"] <= 7:
            out.append(("PM_DUE_7D", "WARNING"))
    hours = m.get("status_hours") or 0
    if m["status"] == "IN_REPAIR" and hours > REPAIR_LONG_DAYS * 24:
        out.append(("REPAIR_LONG", "CRITICAL"))
    if m["status"] == "IN_MAINTENANCE" and hours > MAINTENANCE_LONG_DAYS * 24:
        out.append(("MAINTENANCE_LONG", "WARNING"))
    if m["status"] == "LOCKED":
        out.append(("LOCKED", "WARNING"))
    if active and m.get("location_type") == "NOT_ASSIGNED":
        out.append(("NO_LOCATION", "WARNING"))
    wrong = {"IN_MAINTENANCE": "MAINTENANCE", "IN_REPAIR": "REPAIR", "SCRAPPED": "SCRAP"}.get(m["status"])
    if wrong and m.get("location_type") not in (wrong, "NOT_ASSIGNED"):
        out.append(("WRONG_LOCATION", "WARNING"))
    if m.get("eol_days") is not None and m["eol_days"] <= 30 and active:
        out.append(("EOL_30D", "WARNING"))
    if active and m.get("idle_days") is not None and m["idle_days"] > INACTIVE_DAYS and m["status"] == "AVAILABLE":
        out.append(("INACTIVE_30D", "WARNING"))
    return [{"code": c, "level": lvl} for c, lvl in out]


def load_molds(cur, active_only=True):
    cur.execute(f"{MOLD_SELECT}" + (" WHERE m.is_active=1" if active_only else "") + " ORDER BY m.mold_code")
    return enrich(cur, cur.fetchall())
