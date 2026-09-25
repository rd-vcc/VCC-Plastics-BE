"""Plant Dashboard API (screen 1.1): one call aggregating the modules.

Every figure comes from the module that owns it (no second calculation):
- Production KPIs, Work Orders, shift summary: Production Dashboard (1.2) for the business date / shift / area.
- Machine status, layout, downtime by reason, alarms: Machine Monitoring (2.1), real time.
- Material readiness: Material Inventory (available stock vs safety stock, shortages of planned Work Orders).
- Maintenance reminder: plans / work orders due today or overdue (Maintenance Management).
- Notifications: production alerts + open quality alerts + active machine alarms.
"""

import json
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.core.database import get_vcc_plastics_connection
from app.modules.machine import monitoring_router as mon
from app.modules.material.inventory.service import available_by_material, safety_stocks
from app.modules.production.dashboard import router as prod

router = APIRouter(prefix="/api/dashboard", tags=["Dashboard"])


def _json(v):
    return json.loads(json.dumps(v, default=str))


def _read(action):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        return action(cur)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cur.close(); conn.close()


def _material(cur):
    cur.execute("SELECT id, material_code, material_name, unit FROM materials WHERE status='ACTIVE'")
    materials = {m["id"]: m for m in cur.fetchall()}
    safety, stock = safety_stocks(cur), available_by_material(cur)
    cur.execute("""SELECT DISTINCT r.material_id FROM material_requirements r JOIN production_plan_details d ON d.id=r.work_order_id
                   WHERE d.status IN ('SCHEDULED','RELEASED','IN_PRODUCTION','ON_HOLD')""")
    needed = {r["material_id"] for r in cur.fetchall()}
    cur.execute("""SELECT DISTINCT r.material_id FROM material_requirements r JOIN production_plan_details d ON d.id=r.work_order_id
                   WHERE r.status='SHORTAGE' AND d.status IN ('SCHEDULED','RELEASED','IN_PRODUCTION','ON_HOLD')""")
    short = {r["material_id"] for r in cur.fetchall()}
    ready = low = out = 0
    low_list, out_list = [], []
    for mid, m in materials.items():
        if mid not in needed and mid not in safety and mid not in stock:
            continue  # neither stocked, planned nor safety-managed
        avail = float(stock.get(mid, {}).get("available", 0.0))
        level = float(safety.get(mid) or 0)
        if avail <= 0 or mid in short:
            out += 1
            out_list.append(m["material_code"])
        elif level and avail < level:
            low += 1
            low_list.append(m["material_code"])
        else:
            ready += 1
    return {"ready": ready, "running_low": low, "out_of_stock": out, "low_codes": low_list[:5], "out_codes": out_list[:5]}


def _maintenance(cur):
    today = date.today()
    due_today = overdue = 0
    items = []
    try:
        cur.execute("""SELECT w.id, w.wo_no AS ref, w.asset_type, w.task_name, COALESCE(DATE(w.planned_start), w.due_date) AS due FROM maintenance_work_orders w
                       WHERE w.status IN ('RELEASED','ASSIGNED','IN_PROGRESS','PAUSED','VERIFICATION') AND COALESCE(DATE(w.planned_start), w.due_date) <= %s""", (today,))
        wos = cur.fetchall()
        cur.execute("""SELECT p.id, p.plan_no AS ref, p.asset_type, p.task_name, p.next_due_date AS due FROM maintenance_plans p WHERE p.status='ACTIVE' AND p.next_due_date <= %s
                       AND NOT EXISTS (SELECT 1 FROM maintenance_work_orders w WHERE w.plan_id=p.id AND w.status IN ('RELEASED','ASSIGNED','IN_PROGRESS','PAUSED','VERIFICATION'))""", (today,))
        plans = cur.fetchall()
        for kind, rows in (("WO", wos), ("PLAN", plans)):
            for r in rows:
                if r["due"] and r["due"] < today:
                    overdue += 1
                else:
                    due_today += 1
                items.append({**r, "kind": kind, "overdue": bool(r["due"] and r["due"] < today)})
    except Exception:  # maintenance module not installed
        pass
    items.sort(key=lambda x: (not x["overdue"], x["due"] or today))
    return {"due_today": due_today, "overdue": overdue, "items": items[:6]}


@router.get("/overview")
def overview(business_date: Optional[date] = None, shift_id: Optional[int] = None, area_id: Optional[int] = None):
    day = business_date or date.today()
    summary = prod.summary(day, shift_id, area_id)
    y_summary = prod.summary(day - timedelta(days=1), summary["window"]["shift_id"], area_id)
    machines = mon.overview("MACHINE", area_id)

    def action(cur):
        cur.execute("SELECT id, name FROM factory_structure_nodes WHERE parent_id IS NULL ORDER BY id LIMIT 1")
        plant = cur.fetchone()
        w_start, w_end = summary["window"]["start"], summary["window"]["end"]
        if isinstance(w_start, str):
            w_start, w_end = datetime.fromisoformat(w_start), datetime.fromisoformat(w_end)
        cur.execute("SELECT COUNT(*) n FROM machine_alarms WHERE raised_at >= %s AND raised_at < %s", (w_start, w_end))
        alarms_window = int(cur.fetchone()["n"])
        cur.execute("SELECT COUNT(*) n FROM machine_alarms WHERE raised_at >= %s AND raised_at < %s", (w_start - timedelta(days=1), w_end - timedelta(days=1)))
        alarms_prev = int(cur.fetchone()["n"])
        cur.execute("""SELECT COUNT(DISTINCT work_order_id) n FROM production_execution_logs WHERE event_at >= %s AND event_at < %s
                       AND event_type IN ('OUTPUT','REJECT','START')""", (w_start - timedelta(days=1), w_end - timedelta(days=1)))
        wo_prev = int(cur.fetchone()["n"])
        # downtime of the window (all machines of the area)
        ids = [i["id"] for i in machines["items"]]
        downtime = []
        if ids:
            marks = ",".join(["%s"] * len(ids))
            cur.execute(f"""SELECT COALESCE(r.reason_name, d.reason_text, 'Unspecified') AS reason,
                                   SUM(TIMESTAMPDIFF(SECOND, GREATEST(d.started_at, %s), LEAST(COALESCE(d.ended_at, NOW()), %s))) / 60 AS minutes
                            FROM downtime_events d LEFT JOIN reason_codes r ON r.id=d.reason_code_id
                            WHERE d.status<>'CANCELLED' AND d.equipment_id IN ({marks}) AND d.started_at < %s AND (d.ended_at IS NULL OR d.ended_at > %s)
                            GROUP BY COALESCE(r.reason_name, d.reason_text, 'Unspecified') ORDER BY minutes DESC""", (w_start, min(w_end, datetime.now()), *ids, w_end, w_start))
            downtime = [{"reason": r["reason"], "minutes": round(float(r["minutes"] or 0), 1)} for r in cur.fetchall() if float(r["minutes"] or 0) > 0]
        # production orders progress (top 5 open orders by due date)
        cur.execute("""SELECT o.id, o.order_no, o.order_qty, o.due_date, o.status, p.product_code, p.product_name,
                              COALESCE(SUM(d.actual_qty),0) AS actual, MIN(d.planned_start) AS start_plan, MAX(d.planned_end) AS end_plan
                       FROM production_orders o LEFT JOIN products p ON p.id=o.product_id LEFT JOIN production_plan_details d ON d.production_order_id=o.id AND d.status<>'CANCELLED'
                       WHERE o.status NOT IN ('CLOSED','CANCELLED','DRAFT')
                       GROUP BY o.id, o.order_no, o.order_qty, o.due_date, o.status, p.product_code, p.product_name ORDER BY o.due_date, o.order_no LIMIT 8""")
        orders = []
        now = datetime.now()
        for o in cur.fetchall():
            qty = int(o["order_qty"] or 0)
            actual = int(o["actual"] or 0)
            pct = round(actual / qty * 100, 1) if qty else None
            # expected progress by time between the planned start and end of its Work Orders
            s, e = o["start_plan"], o["end_plan"]
            expected = None
            if s and e and e > s:
                expected = max(0.0, min(100.0, (now - s).total_seconds() / (e - s).total_seconds() * 100))
            state = "NOT_STARTED" if not actual else "COMPLETED" if pct is not None and pct >= 100 and o["status"] == "COMPLETED" else None
            if state is None:
                if pct is not None and pct >= 100:
                    state = "AHEAD"
                elif expected is None:
                    state = "ON_TRACK"
                elif pct >= expected - 5:
                    state = "AHEAD" if pct > expected + 10 else "ON_TRACK"
                elif pct >= expected - 20:
                    state = "AT_RISK"
                else:
                    state = "DELAY"
            due = o["due_date"].date() if isinstance(o["due_date"], datetime) else o["due_date"]
            if due and due < day and (pct or 0) < 100:
                state = "DELAY"
            orders.append({"id": o["id"], "order_no": o["order_no"], "product": o["product_name"] or o["product_code"], "plan": qty, "actual": actual,
                           "progress": pct, "expected": round(expected, 1) if expected is not None else None, "due_date": o["due_date"], "state": state})
        # notifications: production alerts + open quality alerts + active machine alarms
        notes = []
        for a in summary["alerts"][:10]:
            notes.append({"source": "PRODUCTION", "severity": a["severity"], "code": a["code"], "ref": a.get("ref"), "ref_id": a.get("ref_id"), "type": a.get("type"),
                          "detail": a.get("detail"), "at": a.get("at")})
        try:
            cur.execute("""SELECT id, kind, severity, message, ref_type, ref_id, ref_code, raised_at FROM quality_alerts WHERE status IN ('NEW','ACKNOWLEDGED','IN_PROGRESS')
                           ORDER BY FIELD(severity,'CRITICAL','HIGH','MEDIUM','LOW'), raised_at DESC LIMIT 10""")
            for q in cur.fetchall():
                notes.append({"source": "QUALITY", "severity": "ERROR" if q["severity"] in ("CRITICAL", "HIGH") else "WARNING" if q["severity"] == "MEDIUM" else "INFO",
                              "code": q["kind"], "ref": q["ref_code"], "ref_id": q["ref_id"], "type": q["ref_type"], "detail": None, "at": q["raised_at"]})
        except Exception:
            pass
        for a in machines["alarms"][:10]:
            notes.append({"source": "MACHINE", "severity": "ERROR" if a["priority"] in ("CRITICAL", "HIGH") else "WARNING", "code": "ALARM", "ref": a["equipment_code"],
                          "ref_id": a["id"], "type": "ALARM", "detail": a["message"], "at": a["raised_at"]})
        rank = {"ERROR": 0, "WARNING": 1, "INFO": 2}
        notes.sort(key=lambda n: rank.get(n["severity"], 3))
        k, yk = summary["kpis"], y_summary["kpis"]
        mk = machines["kpis"]
        return _json({
            "plant": plant, "window": summary["window"], "shift": summary["shift"],
            "kpis": {"oee": k["oee"], "oee_prev": yk["oee"], "achievement": k["achievement"], "achievement_prev": yk["achievement"],
                     "good": k["good"], "good_prev": k["good_yesterday"], "reject": k["reject"], "reject_prev": k["reject_yesterday"],
                     "active_alarms": mk["active_alarms"], "alarms_window": alarms_window, "alarms_prev": alarms_prev,
                     "wo_in_progress": k["in_progress"], "wo_prev": wo_prev, "target": k["target"], "on_track": k["on_track"], "at_risk": k["at_risk"], "delay": k["delay"]},
            "machine_status": {"total": mk["total"], "running": mk.get("running", 0), "idle": mk.get("idle", 0), "down": mk.get("down", 0),
                               "maintenance": mk.get("maintenance", 0), "offline": mk.get("offline", 0)},
            "machines": machines["items"], "areas": machines["areas"], "downtime": downtime, "orders": orders[:5],
            "work_orders": [t for t in summary["timeline"] if t["status"] in ("IN_PRODUCTION", "ON_HOLD", "RELEASED")][:6],
            "material": _material(cur), "maintenance": _maintenance(cur), "notifications": notes[:20], "system": summary["system"],
        })
    return _read(action)
