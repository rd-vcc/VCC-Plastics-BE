"""Machine Monitoring (2.1), Machine Detail (2.2) and Equipment Monitoring (2.3).

Machine-level OEE for a window (today by default):
    Availability = RUNNING time (machine_status_events) / elapsed time of the window
    Performance  = ideal time of the pieces produced (shots x ideal cycle of their Work Order) / RUNNING time
    Quality      = good / (good + reject) from the production log of the machine
Layout positions (layout_x / layout_y, % of the area layout image) are edited in System Configuration.
"""

import math
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.database import get_vcc_plastics_connection
from app.modules.machine import service as ms
from app.modules.production.execution import service as ex
from app.modules.production.work_orders.router import _system_status
from app.modules.production.work_orders.service import WO_SELECT, decorate, load_shifts

router = APIRouter(prefix="/api/machine-monitoring", tags=["Machine Monitoring"])
MACHINE_CATEGORIES = ("MACHINE",)


class LayoutItem(BaseModel):
    equipment_id: int = Field(gt=0)
    x: Optional[float] = Field(default=None, ge=0, le=100)
    y: Optional[float] = Field(default=None, ge=0, le=100)


class LayoutIn(BaseModel):
    items: list[LayoutItem] = Field(min_length=1, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)


def _read(action):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        return action(cur)
    finally:
        cur.close(); conn.close()


def _nodes(cur):
    cur.execute("SELECT id, parent_id, name, image_url FROM factory_structure_nodes")
    return {r["id"]: r for r in cur.fetchall()}


def layout_node(nodes, node_id):
    """Nearest ancestor-or-self that has a layout image, otherwise the node itself."""
    n, seen = node_id, set()
    while n and n in nodes and n not in seen:
        if nodes[n]["image_url"]:
            return n
        seen.add(n)
        n = nodes[n]["parent_id"]
    return node_id


def maintenance_due(cur, equipment_ids, days=0):
    """Maintenance plans due (overdue or within `days`) and open maintenance work orders planned by then, for machines / equipment."""
    if not equipment_ids:
        return []
    marks = ",".join(["%s"] * len(equipment_ids))
    limit = date.today() + timedelta(days=days)
    out = []
    try:
        cur.execute(f"""SELECT p.id, p.asset_id, p.plan_no AS ref, p.task_name AS title, p.next_due_date AS due, e.equipment_code FROM maintenance_plans p
                        JOIN equipment e ON e.id=p.asset_id WHERE p.status='ACTIVE' AND p.asset_type IN ('MACHINE','EQUIPMENT') AND p.asset_id IN ({marks})
                        AND p.next_due_date <= %s AND NOT EXISTS (SELECT 1 FROM maintenance_work_orders w WHERE w.plan_id=p.id
                        AND w.status IN ('RELEASED','ASSIGNED','IN_PROGRESS','PAUSED','VERIFICATION'))""", (*equipment_ids, limit))
        out += [{**r, "kind": "PLAN", "overdue": r["due"] < date.today()} for r in cur.fetchall()]
        cur.execute(f"""SELECT w.id, w.asset_id, w.wo_no AS ref, w.task_name AS title, COALESCE(DATE(w.planned_start), w.due_date) AS due, w.status, e.equipment_code
                        FROM maintenance_work_orders w JOIN equipment e ON e.id=w.asset_id WHERE w.asset_type IN ('MACHINE','EQUIPMENT') AND w.asset_id IN ({marks})
                        AND w.status IN ('RELEASED','ASSIGNED','IN_PROGRESS','PAUSED','VERIFICATION') AND COALESCE(DATE(w.planned_start), w.due_date, CURDATE()) <= %s""", (*equipment_ids, limit))
        out += [{**r, "kind": "WO", "overdue": bool(r["due"] and r["due"] < date.today())} for r in cur.fetchall()]
    except Exception:  # maintenance tables not created yet
        return []
    return sorted(out, key=lambda x: (x["due"] or date.today(), x["equipment_code"]))


def _pct(a, b, d=1):
    return round(a / b * 100, d) if b else None


def status_seconds(cur, equipment_id, start, end, current_status):
    cur.execute("""SELECT status, started_at, ended_at FROM machine_status_events
                   WHERE equipment_id=%s AND started_at < %s AND (ended_at IS NULL OR ended_at > %s) ORDER BY started_at""", (equipment_id, end, start))
    rows = cur.fetchall()
    out = defaultdict(float)
    if not rows:
        cur.execute("SELECT 1 FROM machine_status_events WHERE equipment_id=%s AND started_at >= %s LIMIT 1", (equipment_id, end))
        if cur.fetchall():   # tracking started after this window: unknown, not counted
            return out
        out[current_status or "OFFLINE"] += (end - start).total_seconds()
        return out
    # time before the first recorded status event is unknown and not counted
    for r in rows:
        s, e = max(r["started_at"], start), min(r["ended_at"] or end, end)
        out[r["status"]] += max((e - s).total_seconds(), 0)
    return out


def machine_oee(cur, equipment_id, start, end, current_status):
    now = datetime.now()
    end = min(end, now)
    if end <= start:
        return {"availability": None, "performance": None, "quality": None, "oee": None, "good": 0, "reject": 0, "shots": 0,
                "running_sec": 0, "elapsed_sec": 0, "status_seconds": {}}
    secs = status_seconds(cur, equipment_id, start, end, current_status)
    if not secs:   # window lies before status tracking started
        return {"availability": None, "performance": None, "quality": None, "oee": None, "good": 0, "reject": 0, "shots": 0,
                "running_sec": 0, "elapsed_sec": 0, "status_seconds": {}}
    elapsed = sum(secs.values())
    running = secs.get("RUNNING", 0.0)
    cur.execute("""SELECT l.good_qty, l.reject_qty, d.cavity, d.cycle_time_sec FROM production_execution_logs l
                   JOIN production_plan_details d ON d.id = l.work_order_id
                   WHERE l.machine_id=%s AND l.event_at >= %s AND l.event_at < %s AND l.event_type IN ('OUTPUT','REJECT','QTY_ADJUST')""", (equipment_id, start, end))
    good = reject = shots = 0
    ideal = 0.0
    for r in cur.fetchall():
        g, rj = int(r["good_qty"]), int(r["reject_qty"])
        good += g; reject += rj
        s = max((g + rj) / max(int(r["cavity"] or 1), 1), 0)
        shots += s
        ideal += s * float(r["cycle_time_sec"] or 0)
    a = running / elapsed if elapsed else None
    p = min(ideal / running, 1.0) if running and ideal else None
    q = good / (good + reject) if good + reject > 0 else None
    oee = a * p * q if None not in (a, p, q) else None
    return {"availability": round(a * 100, 1) if a is not None else None, "performance": round(p * 100, 1) if p is not None else None,
            "quality": round(q * 100, 2) if q is not None else None, "oee": round(oee * 100, 1) if oee is not None else None,
            "good": good, "reject": reject, "shots": math.ceil(shots), "running_sec": int(running), "elapsed_sec": int(elapsed),
            "status_seconds": {k: int(v) for k, v in secs.items()}}


def latest_parameters(cur, eq):
    limits = ms.parameter_limits(cur, eq)
    cur.execute("""SELECT r.parameter_code, r.parameter_name, r.value, r.unit, r.read_at, r.source FROM machine_parameter_readings r
                   JOIN (SELECT parameter_code, MAX(id) AS id FROM machine_parameter_readings WHERE equipment_id=%s AND read_at > NOW() - INTERVAL 1 DAY GROUP BY parameter_code) m
                   ON m.id = r.id""", (eq["id"],))
    latest = {r["parameter_code"]: r for r in cur.fetchall()}
    out = []
    for code in list(limits) + [c for c in latest if c not in limits]:
        lim = limits.get(code, {})
        r = latest.get(code)
        v = float(r["value"]) if r else None
        lo, hi = lim.get("min"), lim.get("max")
        state = None if v is None else "HIGH" if hi is not None and v > hi else "LOW" if lo is not None and v < lo else "OK"
        out.append({"code": code, "name": (r and r["parameter_name"]) or lim.get("name") or code, "unit": (r and r["unit"]) or lim.get("unit"),
                    "value": v, "target": lim.get("target"), "min": lo, "max": hi, "state": state, "standard": bool(lim.get("standard")),
                    "read_at": r["read_at"] if r else None, "source": r["source"] if r else None})
    return out


def _events(cur, eq_id, limit=15):
    cur.execute("""SELECT 'STATUS' AS kind, started_at AS at, status AS title, previous_status AS detail, source AS actor, TIMESTAMPDIFF(SECOND, started_at, COALESCE(ended_at, NOW())) AS secs
                   FROM machine_status_events WHERE equipment_id=%s ORDER BY started_at DESC LIMIT %s""", (eq_id, limit))
    rows = cur.fetchall()
    cur.execute("""SELECT 'ALARM' AS kind, raised_at AS at, priority AS title, message AS detail, source AS actor, NULL AS secs
                   FROM machine_alarms WHERE equipment_id=%s ORDER BY raised_at DESC LIMIT %s""", (eq_id, limit))
    rows += cur.fetchall()
    cur.execute("""SELECT 'DOWNTIME' AS kind, d.started_at AS at, d.downtime_no AS title, COALESCE(r.reason_name, d.reason_text) AS detail, d.reporter AS actor,
                          TIMESTAMPDIFF(SECOND, d.started_at, COALESCE(d.ended_at, NOW())) AS secs
                   FROM downtime_events d LEFT JOIN reason_codes r ON r.id = d.reason_code_id WHERE d.equipment_id=%s AND d.status<>'CANCELLED' ORDER BY d.started_at DESC LIMIT %s""", (eq_id, limit))
    rows += cur.fetchall()
    cur.execute("""SELECT 'PRODUCTION' AS kind, l.event_at AS at, l.event_type AS title, CONCAT(w.wo_no, COALESCE(CONCAT(' · ', l.reason_text), '')) AS detail, l.actor, NULL AS secs
                   FROM production_execution_logs l JOIN production_plan_details w ON w.id = l.work_order_id
                   WHERE l.machine_id=%s AND l.event_type IN ('START','HOLD','RESUME','END') ORDER BY l.event_at DESC LIMIT %s""", (eq_id, limit))
    rows += cur.fetchall()
    rows.sort(key=lambda r: r["at"], reverse=True)
    return rows[:limit]


@router.get("/machines")
def machine_list(category: str | None = None):
    def action(cur):
        cur.execute("""SELECT e.id, e.equipment_code, e.equipment_name, e.operational_status, t.category, t.type_name, fn.name AS area_name
                       FROM equipment e JOIN equipment_types t ON t.id = e.equipment_type_id LEFT JOIN factory_structure_nodes fn ON fn.id = e.factory_node_id
                       WHERE e.asset_status='ACTIVE' ORDER BY (t.category='MACHINE') DESC, e.equipment_code""")
        rows = cur.fetchall()
        return [r for r in rows if not category or r["category"] == category.upper()]
    return _read(action)


@router.get("/machines/{equipment_id}")
def machine_detail(equipment_id: int):
    def action(cur):
        now = datetime.now()
        eq = ms.get_equipment(cur, equipment_id=equipment_id)
        cur.execute("""SELECT d.spec_name, d.unit, v.value_text, v.value_integer, v.value_decimal FROM equipment_spec_values v
                       JOIN equipment_spec_definitions d ON d.id = v.spec_definition_id WHERE v.equipment_id=%s ORDER BY d.sort_order, d.id""", (equipment_id,))
        specs = [{"name": s["spec_name"], "unit": s["unit"], "value": s["value_text"] if s["value_text"] is not None else
                  (float(s["value_decimal"]) if s["value_decimal"] is not None else s["value_integer"])} for s in cur.fetchall()]
        cur.execute("SELECT group_name FROM equipment_groups WHERE id=%s", (eq["equipment_group_id"],)) if eq["equipment_group_id"] else None
        group = (cur.fetchone() or {}).get("group_name") if eq["equipment_group_id"] else None
        cur.execute("SELECT started_at FROM machine_status_events WHERE equipment_id=%s AND ended_at IS NULL ORDER BY id DESC LIMIT 1", (equipment_id,))
        since = (cur.fetchone() or {}).get("started_at")
        # current production (running or on hold)
        cur.execute(f"{WO_SELECT} WHERE d.machine_id=%s AND d.status IN ('IN_PRODUCTION','ON_HOLD') ORDER BY FIELD(d.status,'IN_PRODUCTION','ON_HOLD'), d.started_at DESC LIMIT 1", (equipment_id,))
        row = cur.fetchone()
        current = None
        if row:
            shifts = load_shifts(cur)
            current = decorate(row, now, shifts)
            current["metrics"] = ex.compute_metrics(current, ex.logs_of(cur, current["id"]), now)
            cur.execute("""SELECT m.material_code, m.material_name, m.unit, r.status FROM material_requirements r JOIN materials m ON m.id = r.material_id
                           WHERE r.work_order_id=%s AND r.status <> 'CANCELLED' ORDER BY m.material_code""", (current["id"],))
            current["materials"] = cur.fetchall()
        today0 = datetime.combine(date.today(), datetime.min.time())
        k_today = machine_oee(cur, equipment_id, today0, now, eq["operational_status"])
        k_yday = machine_oee(cur, equipment_id, today0 - timedelta(days=1), today0, eq["operational_status"])
        params = latest_parameters(cur, eq)
        cycle = next((p for p in params if p["code"] == "CYCLE_TIME"), None)
        cur.execute("SELECT component_code, status, value, message, source, updated_at FROM machine_component_status WHERE equipment_id=%s", (equipment_id,))
        components = {c["component_code"]: c for c in cur.fetchall()}
        category = eq["category"] if eq["category"] in ms.COMPONENTS else "MACHINE"
        comp_list = [components.get(code, {"component_code": code, "status": None}) for code in ms.COMPONENTS[category]]
        cur.execute(f"""SELECT id, alarm_no, message, priority, status, raised_at, version FROM machine_alarms WHERE equipment_id=%s AND status IN {ms.ALARM_OPEN}
                        ORDER BY FIELD(priority,'CRITICAL','HIGH','MEDIUM','LOW'), raised_at DESC""", (equipment_id,))
        alarms = cur.fetchall()
        cur.execute("SELECT id, downtime_no, started_at, reason_text, version, source FROM downtime_events WHERE equipment_id=%s AND status='OPEN' LIMIT 1", (equipment_id,))
        open_downtime = cur.fetchone()
        breakdown_codes = ["INJECTION_TIME", "HOLDING_TIME", "COOLING_TIME", "MOLD_OPEN_TIME", "EJECT_TIME"]
        pv = {p["code"]: p["value"] for p in params}
        breakdown = [[c, pv[c]] for c in breakdown_codes if pv.get(c) is not None]
        if cycle and cycle["value"] and breakdown:
            other = round(cycle["value"] - sum(v for _, v in breakdown), 2)
            if other > 0:
                breakdown.append(["OTHER", other])
        # hourly OEE for the last 8 hours
        hours = [now.replace(minute=0, second=0, microsecond=0) - timedelta(hours=7 - i) for i in range(8)]
        oee_trend = [{"hour": h.strftime("%H:%M"), **{k: v for k, v in machine_oee(cur, equipment_id, h, h + timedelta(hours=1), eq["operational_status"]).items()
                                                     if k in ("oee", "availability", "performance", "quality")}} for h in hours]
        return {
            "machine": {**eq, "group_name": group, "specs": specs, "status_since": since,
                        "layout_x": float(eq["layout_x"]) if eq["layout_x"] is not None else None, "layout_y": float(eq["layout_y"]) if eq["layout_y"] is not None else None},
            "current": current, "kpi": k_today, "kpi_yesterday": k_yday, "cycle": cycle, "shot_count": current["current_shot"] if current else None,
            "parameters": params, "components": comp_list, "alarms": alarms, "open_downtime": open_downtime,
            "breakdown": breakdown, "oee_trend": oee_trend, "events": _events(cur, equipment_id), "system": _system_status(cur),
            "maintenance_due": maintenance_due(cur, [equipment_id], days=14),
        }
    return _read(action)


@router.get("/machines/{equipment_id}/trend")
def parameter_trend(equipment_id: int, parameter: str, hours: float = 2):
    def action(cur):
        cur.execute("""SELECT read_at, value FROM machine_parameter_readings WHERE equipment_id=%s AND parameter_code=%s AND read_at >= NOW() - INTERVAL %s MINUTE
                       ORDER BY read_at""", (equipment_id, ms.param_code(parameter), int(hours * 60)))
        rows = cur.fetchall()
        step = max(len(rows) // 240, 1)  # at most ~240 points
        return [{"t": r["read_at"], "v": float(r["value"])} for r in rows[::step]]
    return _read(action)


@router.get("/overview")
def overview(category: str = "MACHINE", area_id: int | None = None, group_id: int | None = None, status: str | None = None, type_code: str | None = None):
    """Plant view for 2.1 (category MACHINE) or 2.3 (category AUX = everything that is not an injection machine)."""
    def action(cur):
        now = datetime.now()
        today0 = datetime.combine(date.today(), datetime.min.time())
        cur.execute("""SELECT e.id, e.equipment_code, e.equipment_name, e.operational_status, e.model, e.manufacturer, e.image_url, e.layout_x, e.layout_y,
                              e.equipment_group_id, g.group_name, e.factory_node_id, fn.name AS area_name, fn.image_url AS area_image, t.category, t.type_code, t.type_name
                       FROM equipment e JOIN equipment_types t ON t.id = e.equipment_type_id LEFT JOIN equipment_groups g ON g.id = e.equipment_group_id
                       LEFT JOIN factory_structure_nodes fn ON fn.id = e.factory_node_id WHERE e.asset_status='ACTIVE' ORDER BY fn.name, e.equipment_code""")
        rows = cur.fetchall()
        aux = category.upper() != "MACHINE"
        types = sorted({(r["type_code"], r["type_name"]) for r in rows if (r["category"] != "MACHINE") == aux})
        rows = [r for r in rows if (r["category"] != "MACHINE") == aux and (not type_code or r["type_code"] == type_code)
                and (not group_id or r["equipment_group_id"] == group_id)]
        nodes = _nodes(cur)
        for r in rows:
            ln = layout_node(nodes, r["factory_node_id"])
            r["layout_node_id"] = ln
            r["layout_area"] = nodes[ln]["name"] if ln in nodes else r["area_name"]
            r["layout_image"] = nodes[ln]["image_url"] if ln in nodes else r["area_image"]
        rows = [r for r in rows if not area_id or r["layout_node_id"] == area_id or r["factory_node_id"] == area_id]
        cur.execute(f"""SELECT equipment_id, priority, COUNT(*) AS n FROM machine_alarms WHERE status IN ('ACTIVE','ACKNOWLEDGED') GROUP BY equipment_id, priority""")
        alarm_rows = cur.fetchall()
        alarms = defaultdict(Counter)
        for a in alarm_rows:
            alarms[a["equipment_id"]][a["priority"]] += int(a["n"])
        cur.execute(f"""{WO_SELECT} WHERE d.status IN ('IN_PRODUCTION','ON_HOLD')""")
        wos = {}
        for w in cur.fetchall():
            if w["machine_id"] and (w["machine_id"] not in wos or w["status"] == "IN_PRODUCTION"):
                wos[w["machine_id"]] = w
        items = []
        for r in rows:
            k = machine_oee(cur, r["id"], today0, now, r["operational_status"])
            w = wos.get(r["id"])
            items.append({**r, "layout_x": float(r["layout_x"]) if r["layout_x"] is not None else None, "layout_y": float(r["layout_y"]) if r["layout_y"] is not None else None,
                          "oee": k["oee"], "availability": k["availability"], "performance": k["performance"], "quality": k["quality"],
                          "running_sec": k["running_sec"], "elapsed_sec": k["elapsed_sec"], "good": k["good"], "reject": k["reject"],
                          "alarms": sum(alarms[r["id"]].values()), "alarm_top": next((p for p in ms.PRIORITIES if alarms[r["id"]][p]), None),
                          "wo_id": w["id"] if w else None, "wo_no": w["wo_no"] if w else None, "product_name": w["product_name"] if w else None,
                          "mold_code": w["mold_code"] if w else None, "wo_status": w["status"] if w else None,
                          "progress_pct": round(min(int(w["actual_qty"] or 0) / max(int(w["planned_qty"]), 1) * 100, 100), 1) if w else None})
        shown = [i for i in items if not status or i["operational_status"] == status.upper()]
        counts = Counter(i["operational_status"] or "OFFLINE" for i in items)
        oees = [i["oee"] for i in items if i["oee"] is not None]
        avails = [i["availability"] for i in items if i["availability"] is not None]
        ids = [i["id"] for i in items]
        downtime, alarms_open = [], []
        if ids:
            marks = ",".join(["%s"] * len(ids))
            cur.execute(f"""SELECT COALESCE(r.reason_name, d.reason_text, 'UNSPECIFIED') AS reason,
                                   SUM(TIMESTAMPDIFF(SECOND, GREATEST(d.started_at, %s), COALESCE(d.ended_at, NOW()))) / 60 AS minutes
                            FROM downtime_events d LEFT JOIN reason_codes r ON r.id = d.reason_code_id
                            WHERE d.status<>'CANCELLED' AND d.equipment_id IN ({marks}) AND (d.ended_at IS NULL OR d.ended_at > %s)
                            GROUP BY COALESCE(r.reason_name, d.reason_text, 'UNSPECIFIED') ORDER BY minutes DESC""", (today0, *ids, today0))
            downtime = [[r["reason"], round(float(r["minutes"] or 0), 1)] for r in cur.fetchall()]
            cur.execute(f"""SELECT a.id, a.alarm_no, a.message, a.priority, a.status, a.raised_at, e.equipment_code FROM machine_alarms a JOIN equipment e ON e.id = a.equipment_id
                            WHERE a.status IN ('ACTIVE','ACKNOWLEDGED') AND a.equipment_id IN ({marks})
                            ORDER BY FIELD(a.priority,'CRITICAL','HIGH','MEDIUM','LOW'), a.raised_at DESC LIMIT 10""", tuple(ids))
            alarms_open = cur.fetchall()
        by_area = defaultdict(list)
        for i in items:
            by_area[(i["layout_node_id"], i["layout_area"] or "—", i["layout_image"])].append(i)
        areas = [{"id": k[0], "name": k[1], "image_url": k[2], "count": len(v),
                  "utilization": round(sum(x["running_sec"] for x in v) / max(sum(x["elapsed_sec"] for x in v), 1) * 100, 1),
                  "oee": round(sum(x["oee"] for x in v if x["oee"] is not None) / max(len([x for x in v if x["oee"] is not None]), 1), 1) if any(x["oee"] is not None for x in v) else None}
                 for k, v in by_area.items()]
        # runtime trend (2.3): running hours per day for the last 7 days
        runtime = []
        for d in range(6, -1, -1):
            d0 = today0 - timedelta(days=d)
            run = sum(status_seconds(cur, i["id"], d0, min(d0 + timedelta(days=1), now), i["operational_status"]).get("RUNNING", 0) for i in items)
            runtime.append({"date": d0.date().isoformat(), "hours": round(run / 3600, 1)})
        # OEE trend: average machine OEE per day for the last 7 days
        oee_trend = []
        for d in range(6, -1, -1):
            d0 = today0 - timedelta(days=d)
            vals = [machine_oee(cur, i["id"], d0, min(d0 + timedelta(days=1), now), i["operational_status"])["oee"] for i in items] if not aux else []
            vals = [v for v in vals if v is not None]
            oee_trend.append({"date": d0.date().isoformat(), "oee": round(sum(vals) / len(vals), 1) if vals else None})
        # 2.3: running hours per hour today, lifetime runtime and last data time per equipment
        runtime_hourly = []
        for h in range(0, now.hour + 1):
            h0 = today0 + timedelta(hours=h)
            run = sum(status_seconds(cur, i["id"], h0, min(h0 + timedelta(hours=1), now), i["operational_status"]).get("RUNNING", 0) for i in items)
            runtime_hourly.append({"hour": f"{h:02d}", "hours": round(run / 3600, 2)})
        if ids:
            cur.execute(f"""SELECT equipment_id, SUM(TIMESTAMPDIFF(SECOND, started_at, COALESCE(ended_at, NOW()))) / 3600 AS h FROM machine_status_events
                            WHERE status='RUNNING' AND equipment_id IN ({marks}) GROUP BY equipment_id""", tuple(ids))
            life = {r["equipment_id"]: float(r["h"] or 0) for r in cur.fetchall()}
            cur.execute(f"""SELECT equipment_id, MAX(read_at) AS t FROM machine_parameter_readings WHERE equipment_id IN ({marks}) AND read_at >= %s GROUP BY equipment_id""",
                        (*ids, now - timedelta(days=1)))
            last = {r["equipment_id"]: r["t"] for r in cur.fetchall()}
            for i in items:
                i["runtime_hours"] = round(life.get(i["id"], 0), 1)
                i["last_update"] = last.get(i["id"])
        cur.execute("SELECT id, group_name FROM equipment_groups WHERE is_active=1 ORDER BY sort_order, group_name")
        groups = cur.fetchall()
        return {
            "kpis": {"total": len(items), **{s.lower(): counts[s] for s in ms.STATUSES}, "oee": round(sum(oees) / len(oees), 1) if oees else None,
                     "availability": round(sum(avails) / len(avails), 1) if avails else None, "active_alarms": sum(i["alarms"] for i in items)},
            "items": shown, "areas": areas, "downtime_by_reason": downtime, "alarms": alarms_open, "runtime": runtime, "runtime_hourly": runtime_hourly, "oee_trend": oee_trend, "groups": groups, "types": [{"code": c, "name": n} for c, n in types],
            "maintenance_due": maintenance_due(cur, ids), "system": _system_status(cur),
        }
    return _read(action)


@router.get("/layout")
def layout(node_id: int):
    """Layout editor (System Configuration > Factory Structure): equipment drawn on the node's layout image."""
    def action(cur):
        nodes = _nodes(cur)
        if node_id not in nodes:
            raise HTTPException(status_code=404, detail="Factory node not found.")
        cur.execute("""SELECT e.id, e.equipment_code, e.equipment_name, e.operational_status, e.image_url, e.layout_x, e.layout_y, e.factory_node_id,
                              t.category, t.type_name FROM equipment e JOIN equipment_types t ON t.id = e.equipment_type_id
                       WHERE e.asset_status='ACTIVE' ORDER BY t.category, e.equipment_code""")
        items = []
        for r in cur.fetchall():
            n, seen, ok = r["factory_node_id"], set(), False
            while n and n in nodes and n not in seen:   # walk up to node_id; stop at a nearer node that has its own image
                if n == node_id:
                    ok = True
                    break
                if nodes[n]["image_url"]:
                    break
                seen.add(n)
                n = nodes[n]["parent_id"]
            if ok:
                items.append({**r, "layout_x": float(r["layout_x"]) if r["layout_x"] is not None else None,
                              "layout_y": float(r["layout_y"]) if r["layout_y"] is not None else None})
        return {"node": nodes[node_id], "items": items}
    return _read(action)


@router.put("/layout")
def save_layout(p: LayoutIn):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        for it in p.items:
            cur.execute("UPDATE equipment SET layout_x=%s, layout_y=%s, updated_by=%s WHERE id=%s", (it.x, it.y, p.actor, it.equipment_id))
        conn.commit()
        return {"message": "Layout saved.", "count": len(p.items)}
    except Exception as exc:
        conn.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cur.close()
        conn.close()
