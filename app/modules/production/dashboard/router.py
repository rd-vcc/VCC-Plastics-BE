"""Production Dashboard (screen 1.2): real-time overview of one shift.

The window is the selected shift on the business date (default: the shift running now; without
shifts, the whole day). Figures inside the window:
- Target = planned quantity of Work Orders prorated by how much of their planned run falls in the window,
  plus carry-over: what late Work Orders still had to make when the window started.
- Good / reject pieces = production log events inside the window; achievement = good / target.
- Production by hour = cumulative plan (prorated) vs cumulative actual per hour.
- OEE = average of the Work Orders that ran in the window (Production Execution metrics).
Alerts are derived from live data: machines stopped, delayed / on-hold Work Orders, material
shortages, molds near end of life and yield below target.
"""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter

from app.core.database import get_vcc_plastics_connection
from app.modules.production.execution import service as ex
from app.modules.production.planning.service import progress_state
from app.modules.production.work_orders.router import _system_status
from app.modules.production.work_orders.service import WO_SELECT, decorate, load_shifts, shift_of


router = APIRouter(prefix="/api/production-dashboard", tags=["Production Dashboard"])
YIELD_TARGET = 97.0
MOLD_LIFE_WARN = 90.0


def _read(action):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        return action(cur)
    finally:
        cur.close(); conn.close()


def _pct(a, b, digits=1):
    return round(a / b * 100, digits) if b else None


def _window(day, shift):
    if not shift:
        start = datetime.combine(day, datetime.min.time())
        return start, start + timedelta(days=1)
    start = datetime.combine(day, datetime.min.time()) + timedelta(minutes=shift["start_min"])
    end = datetime.combine(day, datetime.min.time()) + timedelta(minutes=shift["end_min"])
    if end <= start:
        end += timedelta(days=1)
    return start, end


def _overlap(a1, a2, b1, b2):
    return max((min(a2, b2) - max(a1, b1)).total_seconds(), 0)


@router.get("/lookups")
def lookups():
    def action(cur):
        cur.execute("""SELECT DISTINCT fn.id, fn.code, fn.name, fn.image_url FROM equipment e JOIN equipment_types t ON t.id = e.equipment_type_id
                       JOIN factory_structure_nodes fn ON fn.id = e.factory_node_id WHERE t.category='MACHINE' AND e.asset_status='ACTIVE' ORDER BY fn.name""")
        areas = cur.fetchall()
        shifts = [{k: s[k] for k in ("id", "shift_code", "shift_name", "start_min", "end_min")} for s in load_shifts(cur)]
        current = shift_of(datetime.now(), load_shifts(cur))
        return {"areas": areas, "shifts": shifts, "current_shift_id": current["id"] if current else None}
    return _read(action)


def _logs(cur, start, end):
    cur.execute("""SELECT work_order_id, event_type, good_qty, reject_qty, event_at FROM production_execution_logs
                   WHERE event_at >= %s AND event_at < %s AND event_type IN ('OUTPUT','REJECT','QTY_ADJUST')""", (start, end))
    return cur.fetchall()


@router.get("/summary")
def summary(business_date: date | None = None, shift_id: int | None = None, area_id: int | None = None):
    day = business_date or date.today()

    def action(cur):
        now = datetime.now()
        shifts = load_shifts(cur)
        shift = next((s for s in shifts if s["id"] == shift_id), None) if shift_id else (shift_of(now, shifts) if day == date.today() else None)
        w_start, w_end = _window(day, shift)
        span = (w_end - w_start).total_seconds()

        # machines (area overview)
        cur.execute("""SELECT e.id, e.equipment_code, e.equipment_name, e.operational_status, e.factory_node_id, fn.name AS area_name, fn.image_url AS area_image,
                              e.model, e.manufacturer, e.image_url
                       FROM equipment e JOIN equipment_types t ON t.id = e.equipment_type_id LEFT JOIN factory_structure_nodes fn ON fn.id = e.factory_node_id
                       WHERE t.category='MACHINE' AND e.asset_status='ACTIVE' ORDER BY fn.name, e.equipment_code""")
        machines = [m for m in cur.fetchall() if not area_id or m["factory_node_id"] == area_id]
        machine_ids = {m["id"] for m in machines}

        # Work Orders touching the window
        cur.execute(
            f"""{WO_SELECT}
                WHERE d.status IN ('RELEASED','IN_PRODUCTION','ON_HOLD')
                   OR (d.status IN ('SCHEDULED','COMPLETED','CLOSED') AND d.planned_start < %s AND COALESCE(d.completed_at, d.planned_end) > %s)
                   OR (d.completed_at >= %s AND d.completed_at < %s)
                ORDER BY d.planned_start""", (w_end, w_start, w_start, w_end))
        rows = cur.fetchall()
        wos = []
        for row in rows:
            if area_id and row["machine_id"] not in machine_ids:
                continue
            w = decorate(row, now, shifts)
            w["metrics"] = ex.compute_metrics(w, ex.logs_of(cur, w["id"]), now) if w["started_at"] else ex.compute_metrics(w, [], now)
            w["progress_state"] = "ON_HOLD" if w["status"] == "ON_HOLD" else progress_state(row, now)
            wos.append(w)
        wo_ids = {w["id"] for w in wos}

        logs = [l for l in _logs(cur, w_start, w_end) if not area_id or l["work_order_id"] in wo_ids]
        y_logs = [l for l in _logs(cur, w_start - timedelta(days=1), w_end - timedelta(days=1)) if not area_id or l["work_order_id"] in wo_ids]
        good = sum(int(l["good_qty"]) for l in logs)
        reject = sum(int(l["reject_qty"]) for l in logs)
        y_good = sum(int(l["good_qty"]) for l in y_logs)
        y_reject = sum(int(l["reject_qty"]) for l in y_logs)

        # target: planned quantity prorated over the window
        target, plan_by_hour = 0.0, defaultdict(float)
        hours = [w_start + timedelta(hours=i) for i in range(int(span // 3600) + (1 if span % 3600 else 0))]
        for w in wos:
            s, e = w["planned_start"], w["planned_end"]
            if not s or not e or e <= s:
                continue
            dur = (e - s).total_seconds()
            target += int(w["planned_qty"]) * _overlap(s, e, w_start, w_end) / dur
            for h in hours:
                plan_by_hour[h] += int(w["planned_qty"]) * _overlap(s, e, h, h + timedelta(hours=1)) / dur
        # carry-over: Work Orders that should have finished before the window but still run in it
        carry = 0
        for w in wos:
            late = w["planned_end"] and w["planned_end"] <= w_start
            ran_here = w["status"] in {"IN_PRODUCTION", "ON_HOLD", "RELEASED"} or (w["completed_at"] and w["completed_at"] >= w_start)
            if not late or not ran_here:
                continue
            cur.execute("""SELECT COALESCE(SUM(good_qty),0) AS g FROM production_execution_logs WHERE work_order_id=%s AND event_at < %s
                           AND event_type IN ('OUTPUT','REJECT','QTY_ADJUST')""", (w["id"], w_start))
            left = max(int(w["planned_qty"]) - int(cur.fetchone()["g"] or 0), 0)
            carry += left
            for h in hours:
                plan_by_hour[h] += left * _overlap(h, h + timedelta(hours=1), w_start, w_end) / span
        target = round(target + carry)
        actual_by_hour = defaultdict(int)
        for l in logs:
            actual_by_hour[l["event_at"].replace(minute=0, second=0, microsecond=0)] += int(l["good_qty"])
        cum_p = cum_a = 0
        by_hour = []
        for h in hours:
            cum_p += plan_by_hour[h]
            cum_a += actual_by_hour[h]
            by_hour.append({"hour": h.strftime("%H:%M"), "plan": round(cum_p), "actual": cum_a if h <= now else None})

        active = [w for w in wos if w["status"] in {"IN_PRODUCTION", "ON_HOLD", "RELEASED"}]
        states = Counter(w["progress_state"] for w in active)
        oees = [w["metrics"]["oee_pct"] for w in wos if w["metrics"]["oee_pct"] is not None and w["started_at"]]
        kpis = {
            "oee": round(sum(oees) / len(oees), 1) if oees else None,
            "achievement": _pct(good, target), "good": good, "reject": reject, "good_yesterday": y_good, "reject_yesterday": y_reject,
            "target": target, "carry_over": carry, "remaining": max(target - good, 0),
            "in_progress": len([w for w in active if w["status"] == "IN_PRODUCTION"]),
            "on_track": states["ON_TRACK"] + states["AHEAD"] + states["COMPLETED"], "at_risk": states["AT_RISK"] + states["NOT_STARTED"],
            "delay": states["DELAY"] + states["ON_HOLD"],
        }

        # machine tiles + top machines by OEE
        by_machine = defaultdict(list)
        for w in wos:
            if w["machine_id"]:
                by_machine[w["machine_id"]].append(w)
        tiles, top = [], []
        for m in machines:
            ms = by_machine.get(m["id"], [])
            current = next((w for w in ms if w["status"] == "IN_PRODUCTION"), None) or next((w for w in ms if w["status"] == "ON_HOLD"), None)
            ran = [w["metrics"] for w in ms if w["started_at"] and w["metrics"]["oee_pct"] is not None]
            avg = (lambda key: round(sum(x[key] for x in ran if x[key] is not None) / len(ran), 1) if ran else None)
            status = m["operational_status"] or "OFFLINE"
            tiles.append({**m, "status": status, "wo_id": current["id"] if current else None, "wo_no": current["wo_no"] if current else None,
                          "product_name": current["product_name"] if current else None, "progress_pct": current["progress_pct"] if current else None,
                          "oee": avg("oee_pct")})
            if ran:
                top.append({"machine_id": m["id"], "machine_code": m["equipment_code"], "status": status, "oee": avg("oee_pct"),
                            "availability": avg("availability_pct"), "performance": avg("performance_pct"), "quality": avg("quality_pct")})
        top.sort(key=lambda x: -(x["oee"] or 0))

        timeline = [{"id": w["id"], "wo_no": w["wo_no"], "product_name": w["product_name"], "machine_code": w["machine_code"], "order_no": w["order_no"],
                     "planned_start": w["planned_start"], "planned_end": w["planned_end"], "started_at": w["started_at"], "completed_at": w["completed_at"],
                     "held_at": w["held_at"] if w["status"] == "ON_HOLD" else None,
                     "progress_pct": w["progress_pct"], "status": w["status"], "progress_state": w["progress_state"]}
                    for w in wos if w["planned_start"] and w["planned_end"]]

        # alerts
        alerts = []
        for m in machines:
            if m["operational_status"] in {"DOWN", "MAINTENANCE", "OFFLINE"}:
                alerts.append({"severity": "ERROR" if m["operational_status"] == "DOWN" else "WARNING", "type": "MACHINE", "code": f"MACHINE_{m['operational_status']}",
                               "ref": m["equipment_code"], "ref_id": m["id"], "at": None})
        for w in wos:
            if w["status"] == "ON_HOLD":
                alerts.append({"severity": "WARNING", "type": "WORK_ORDER", "code": "WO_ON_HOLD", "ref": w["wo_no"], "ref_id": w["id"], "detail": w["hold_reason"], "at": w["held_at"]})
            elif w["delayed"] and w["status"] not in {"COMPLETED", "CLOSED"}:
                alerts.append({"severity": "WARNING", "type": "WORK_ORDER", "code": "WO_DELAYED", "ref": w["wo_no"], "ref_id": w["id"], "minutes": w["delay_minutes"], "at": None})
            if w["mold_usage_pct"] and w["mold_usage_pct"] >= MOLD_LIFE_WARN and w["status"] in {"IN_PRODUCTION", "RELEASED"}:
                alerts.append({"severity": "INFO", "type": "MOLD", "code": "MOLD_LIFE", "ref": w["mold_code"], "ref_id": w["mold_id"], "value": w["mold_usage_pct"], "at": None})
            y = w["metrics"]["yield_pct"]
            if y is not None and y < YIELD_TARGET and w["status"] in {"IN_PRODUCTION", "ON_HOLD"}:
                alerts.append({"severity": "WARNING", "type": "QUALITY", "code": "LOW_YIELD", "ref": w["wo_no"], "ref_id": w["id"], "value": y, "at": None})
        cur.execute("""SELECT m.material_code, COUNT(*) AS n, MIN(r.required_date) AS need FROM material_requirements r JOIN materials m ON m.id = r.material_id
                       JOIN production_plan_details d ON d.id = r.work_order_id
                       WHERE r.status='SHORTAGE' AND d.status IN ('SCHEDULED','RELEASED','IN_PRODUCTION','ON_HOLD') GROUP BY m.material_code""")
        for r in cur.fetchall():
            alerts.append({"severity": "ERROR", "type": "MATERIAL", "code": "MATERIAL_SHORTAGE", "ref": r["material_code"], "count": int(r["n"]), "at": r["need"]})
        rank = {"ERROR": 0, "WARNING": 1, "INFO": 2}
        alerts.sort(key=lambda a: rank[a["severity"]])

        shift_summary = {"shift_name": shift["shift_name"] if shift else None, "start": w_start, "end": w_end, "planned": target, "actual": good + reject,
                         "good": good, "reject": reject, "reject_rate": _pct(reject, good + reject, 2), "remaining": max(target - good, 0), "achievement": _pct(good, target)}
        return {
            "window": {"start": w_start, "end": w_end, "shift_id": shift["id"] if shift else None}, "kpis": kpis, "machines": tiles, "timeline": timeline,
            "by_hour": by_hour, "top_machines": top[:5], "wo_states": dict(states), "alerts": alerts, "shift": shift_summary,
            "system": _system_status(cur),
        }
    return _read(action)
