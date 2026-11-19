"""Quality Management API (5.1 Quality Dashboard, 5.2 Inspection Management, 5.3 SPC Monitoring, 5.4 NG Management,
5.5 Measuring Equipment, 5.6 Calibration Management)."""

import time
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.database import get_vcc_plastics_connection
from app.core.employee_names import attach_employee_names
from app.modules.production.work_orders.router import _system_status
from app.modules.production.work_orders.service import load_shifts
from app.modules.quality import service as qs
from app.modules.quality import spc

router = APIRouter(prefix="/api/quality", tags=["Quality Management"])


def _read(action):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        return action(cur)
    finally:
        cur.close(); conn.close()


def _write(action):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        result = action(cur)
        conn.commit()
        return result if result is not None else {"message": "OK"}
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cur.close(); conn.close()


_last_sync = {"at": 0.0}


def _sync(force=False):
    """Automatic plans (IPQC rounds, calibration cycle) and alerts, at most every 20 s."""
    if not force and time.time() - _last_sync["at"] < 20:
        return
    _last_sync["at"] = time.time()

    def action(cur):
        qs.sync_cal_plans(cur)
        qs.sync_plans(cur)
        qs.sync_alerts(cur, _spc_states(cur))
    try:
        _write(action)
    except HTTPException:
        pass


def _spc_states(cur, days=30):
    """SPC status of every monitored characteristic of the products with an active standard."""
    cur.execute("""SELECT r.id AS revision_id, r.product_id, p.product_code FROM quality_standard_revisions r JOIN products p ON p.id=r.product_id
                   WHERE r.status='ACTIVE'""")
    out = []
    for rev in cur.fetchall():
        for c in qs.characteristics(cur, rev["revision_id"]):
            if not c["is_spc"] or (c.get("characteristic_type") or "VARIABLE") != "VARIABLE":
                continue
            a = spc.analyse(c, spc.load_measurements(cur, c["id"], {"product_id": rev["product_id"]}, limit_days=days), limit=25)
            out.append({"product_id": rev["product_id"], "product_code": rev["product_code"], "characteristic_id": c["id"],
                        "name": c["characteristic_name"], "status": a["status"], "is_critical": bool(c["is_critical"]), "analysis": a})
    return out


def _range(date_from, date_to):
    start = date_from or date.today()
    end = date_to or start
    if end < start:
        raise HTTPException(status_code=400, detail="The end date must be after the start date.")
    return start, end, datetime.combine(start, datetime.min.time()), datetime.combine(end, datetime.min.time()) + timedelta(days=1)


def _scope(filters, alias="ip"):
    """WHERE parts on inspection plans for the common filters."""
    where, params = [], []
    for key in ("work_order_id", "machine_id", "mold_id", "product_id", "shift_id"):
        if filters.get(key):
            where.append(f"{alias}.{key}=%s"); params.append(filters[key])
    if filters.get("inspection_type"):
        where.append(f"{alias}.inspection_type=%s"); params.append(filters["inspection_type"])
    return where, params


def _sample_stats(cur, start_dt, end_dt, filters):
    where, params = _scope(filters)
    cur.execute(f"""SELECT COUNT(*) n, SUM(r.result='NG') ng, SUM(r.is_reinspection=0) first_n, SUM(r.is_reinspection=0 AND r.result='OK') first_ok
                    FROM quality_inspection_records r JOIN quality_inspection_plans ip ON ip.id=r.plan_id
                    WHERE r.inspected_at >= %s AND r.inspected_at < %s {''.join(' AND ' + w for w in where)}""", (start_dt, end_dt, *params))
    r = cur.fetchone()
    n, ng = int(r["n"] or 0), int(r["ng"] or 0)
    return {"samples": n, "ng": ng, "ok": n - ng, "ng_rate": qs.pct(ng, n), "ok_rate": qs.pct(n - ng, n),
            "fpy": qs.pct(int(r["first_ok"] or 0), int(r["first_n"] or 0))}


def _production(cur, start_dt, end_dt, filters):
    where, params = [], []
    for key, col in (("work_order_id", "l.work_order_id"), ("machine_id", "l.machine_id"), ("mold_id", "l.mold_id"), ("shift_id", "l.shift_id")):
        if filters.get(key):
            where.append(f"{col}=%s"); params.append(filters[key])
    if filters.get("product_id"):
        where.append("d.product_id=%s"); params.append(filters["product_id"])
    cur.execute(f"""SELECT SUM(l.good_qty) g, SUM(l.reject_qty) r FROM production_execution_logs l JOIN production_plan_details d ON d.id=l.work_order_id
                    WHERE l.event_at >= %s AND l.event_at < %s {''.join(' AND ' + w for w in where)}""", (start_dt, end_dt, *params))
    r = cur.fetchone()
    good, rej = int(r["g"] or 0), int(r["r"] or 0)
    return {"good": good, "reject": rej, "produced": good + rej, "ppm": round(rej / (good + rej) * 1e6) if good + rej else None}


def _ng_scope(filters, alias="n"):
    where, params = [], []
    for key in ("work_order_id", "machine_id", "mold_id", "product_id"):
        if filters.get(key):
            where.append(f"{alias}.{key}=%s"); params.append(filters[key])
    return where, params


def _defects(cur, start_dt, end_dt, filters, limit=None):
    where, params = _ng_scope(filters)
    cur.execute(f"""SELECT n.defect_code_id, COALESCE(rc.reason_name, 'Unclassified') AS name, rc.reason_code AS code, SUM(n.ng_qty) qty, COUNT(*) events
                    FROM quality_ng_records n LEFT JOIN reason_codes rc ON rc.id=n.defect_code_id
                    WHERE n.detected_at >= %s AND n.detected_at < %s {''.join(' AND ' + w for w in where)}
                    GROUP BY n.defect_code_id, rc.reason_name, rc.reason_code ORDER BY qty DESC""", (start_dt, end_dt, *params))
    rows = cur.fetchall()
    total = sum(int(r["qty"] or 0) for r in rows)
    out = [{"defect_code_id": r["defect_code_id"], "code": r["code"], "name": r["name"], "qty": int(r["qty"] or 0), "events": int(r["events"]),
            "share": qs.pct(int(r["qty"] or 0), total, 1)} for r in rows]
    return (out[:limit] if limit else out), total


def _trend_days(start, end):
    if (end - start).days < 6:
        start = end - timedelta(days=6)
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _prev(start, end):
    span = (end - start).days + 1
    return start - timedelta(days=span), start - timedelta(days=1)


def _dt(d):
    return datetime.combine(d, datetime.min.time())


def _change(cur_v, prev_v):
    if cur_v is None or prev_v is None:
        return None
    return round(cur_v - prev_v, 2)


# =========================================================
# LOOKUPS
# =========================================================


@router.get("/lookups")
def lookups():
    def action(cur):
        t = qs.targets(cur)
        cur.execute("SELECT id, product_code, product_name FROM products WHERE status='ACTIVE' ORDER BY product_code")
        products = cur.fetchall()
        cur.execute("""SELECT d.id, d.wo_no, d.status, d.product_id, d.machine_id, d.mold_id, p.product_code, p.product_name, eq.equipment_code AS machine_code, mo.mold_code
                       FROM production_plan_details d LEFT JOIN products p ON p.id=d.product_id LEFT JOIN equipment eq ON eq.id=d.machine_id
                       LEFT JOIN molds mo ON mo.id=d.mold_id WHERE d.status NOT IN ('CANCELLED','UNSCHEDULED') ORDER BY d.wo_no DESC""")
        wos = cur.fetchall()
        cur.execute("SELECT e.id, e.equipment_code AS code, e.equipment_name AS name FROM equipment e ORDER BY e.equipment_code")
        machines = cur.fetchall()
        cur.execute("SELECT id, mold_code AS code, mold_name AS name FROM molds WHERE status<>'RETIRED' ORDER BY mold_code")
        molds = cur.fetchall()
        cur.execute("""SELECT l.id, l.lot_no, l.status, m.material_code, m.material_name FROM material_lots l LEFT JOIN materials m ON m.id=l.material_id
                       ORDER BY l.id DESC LIMIT 300""")
        lots = cur.fetchall()
        cur.execute("SELECT id, type_code, type_name FROM measuring_equipment_types WHERE is_active=1 ORDER BY type_name")
        types = cur.fetchall()
        gauges = [{"id": e["id"], "code": e["equipment_code"], "name": e["equipment_name"], "type_id": e["equipment_type_id"], "state": e["state"],
                   "usable": e["usable"], "next_calibration_date": e["next_calibration_date"]} for e in qs.load_equipment(cur)]
        locations = set(qs.USAGE_LOCATIONS)
        cur.execute("SELECT DISTINCT usage_location FROM measuring_equipment WHERE usage_location IS NOT NULL")
        locations |= {r["usage_location"] for r in cur.fetchall()}
        cur.execute("SELECT DISTINCT lab FROM quality_calibration_plans WHERE lab IS NOT NULL")
        labs = set(qs.LABS) | {r["lab"] for r in cur.fetchall()}
        return {"inspection_types": qs.INSPECTION_TYPES, "processes": qs.PROCESSES, "frequency_types": qs.FREQ_TYPES, "plan_statuses": qs.PLAN_STATUSES,
                "ng_sources": qs.NG_SOURCES, "ng_statuses": qs.NG_STATUSES, "priorities": qs.PRIORITIES, "dispositions": qs.DISPOSITIONS,
                "root_causes": qs.ROOT_CAUSES, "alert_statuses": qs.ALERT_STATUSES, "cal_statuses": qs.CAL_STATUSES, "cal_results": qs.CAL_RESULTS,
                "equipment_states": qs.EQUIPMENT_STATES, "locations": sorted(locations), "labs": sorted(labs), "equipment_types": types,
                "products": products, "work_orders": wos, "machines": machines, "molds": molds, "lots": lots, "gauges": gauges,
                "defects": qs.defect_codes(cur), "shifts": [{"id": s["id"], "name": s["shift_name"], "code": s["shift_code"]} for s in load_shifts(cur)],
                "inspectors": qs.inspectors(cur), "targets": t}
    return _read(action)


# =========================================================
# 5.1 QUALITY DASHBOARD
# =========================================================


@router.get("/dashboard")
def dashboard(date_from: Optional[date] = None, date_to: Optional[date] = None, shift_id: Optional[int] = None, machine_id: Optional[int] = None,
              mold_id: Optional[int] = None, product_id: Optional[int] = None, work_order_id: Optional[int] = None, inspection_type: Optional[str] = None):
    _sync()
    start, end, start_dt, end_dt = _range(date_from, date_to)
    f = {"shift_id": shift_id, "machine_id": machine_id, "mold_id": mold_id, "product_id": product_id, "work_order_id": work_order_id,
         "inspection_type": inspection_type}

    def action(cur):
        t = qs.targets(cur)
        now = datetime.now()
        ps, pe = _prev(start, end)
        cur_s, prev_s = _sample_stats(cur, start_dt, end_dt, f), _sample_stats(cur, _dt(ps), _dt(pe) + timedelta(days=1), f)
        cur_p, prev_p = _production(cur, start_dt, end_dt, f), _production(cur, _dt(ps), _dt(pe) + timedelta(days=1), f)
        defects, defect_total = _defects(cur, start_dt, end_dt, f)
        prev_defects, prev_defect_total = _defects(cur, _dt(ps), _dt(pe) + timedelta(days=1), f)
        where, params = _ng_scope(f)
        wsql = "".join(" AND " + w for w in where)
        cur.execute(f"""SELECT COUNT(DISTINCT n.work_order_id) c FROM quality_ng_records n WHERE n.work_order_id IS NOT NULL
                        AND n.detected_at >= %s AND n.detected_at < %s{wsql}""", (start_dt, end_dt, *params))
        wo_ng = int(cur.fetchone()["c"] or 0)
        cur.execute(f"""SELECT COUNT(DISTINCT n.work_order_id) c FROM quality_ng_records n WHERE n.work_order_id IS NOT NULL
                        AND n.detected_at >= %s AND n.detected_at < %s{wsql}""", (_dt(ps), _dt(pe) + timedelta(days=1), *params))
        wo_ng_prev = int(cur.fetchone()["c"] or 0)
        # machines: running = with a Work Order in production / on hold; with an open quality alert
        cur.execute("SELECT DISTINCT machine_id FROM production_plan_details WHERE status IN ('IN_PRODUCTION','ON_HOLD') AND machine_id IS NOT NULL")
        running = {r["machine_id"] for r in cur.fetchall()}
        cur.execute("""SELECT DISTINCT a.ref_id AS machine_id FROM quality_alerts a WHERE a.ref_type='MACHINE' AND a.status IN ('NEW','ACKNOWLEDGED','IN_PROGRESS')
                       UNION SELECT DISTINCT d.machine_id FROM quality_alerts a JOIN production_plan_details d ON d.id=a.ref_id
                       WHERE a.ref_type='WO' AND a.status IN ('NEW','ACKNOWLEDGED','IN_PROGRESS') AND d.machine_id IS NOT NULL""")
        alert_machines = {r["machine_id"] for r in cur.fetchall()}
        # trend
        days = _trend_days(start, end)
        trend = []
        for d in days:
            s = _sample_stats(cur, _dt(d), _dt(d) + timedelta(days=1), f)
            p = _production(cur, _dt(d), _dt(d) + timedelta(days=1), f)
            trend.append({"date": d, "fpy": s["fpy"], "ppm": p["ppm"], "samples": s["samples"], "ng": s["ng"], "produced": p["produced"], "reject": p["reject"]})
        # quality by Work Order (inspection samples + production PPM)
        wwhere, wparams = _scope(f)
        cur.execute(f"""SELECT ip.work_order_id, d.wo_no, d.status, p.product_code, p.product_name, eq.equipment_code AS machine_code, mo.mold_code,
                               COUNT(*) n, SUM(r.result='NG') ng, SUM(r.is_reinspection=0) fn, SUM(r.is_reinspection=0 AND r.result='OK') fok
                        FROM quality_inspection_records r JOIN quality_inspection_plans ip ON ip.id=r.plan_id
                        JOIN production_plan_details d ON d.id=ip.work_order_id LEFT JOIN products p ON p.id=d.product_id
                        LEFT JOIN equipment eq ON eq.id=d.machine_id LEFT JOIN molds mo ON mo.id=d.mold_id
                        WHERE r.inspected_at >= %s AND r.inspected_at < %s {''.join(' AND ' + w for w in wwhere)}
                        GROUP BY ip.work_order_id, d.wo_no, d.status, p.product_code, p.product_name, eq.equipment_code, mo.mold_code""", (start_dt, end_dt, *wparams))
        wo_rows = []
        for w in cur.fetchall():
            n, ng = int(w["n"]), int(w["ng"] or 0)
            prod = _production(cur, start_dt, end_dt, {"work_order_id": w["work_order_id"]})
            rate = qs.pct(ng, n)
            top = _defects(cur, start_dt, end_dt, {"work_order_id": w["work_order_id"]}, 1)[0]
            status = "NORMAL" if rate is None or rate < t["wo_attention"] else "ATTENTION" if rate < t["wo_critical"] else "WARNING"
            if rate is not None and rate >= t["wo_critical"] * 2:
                status = "CRITICAL"
            wo_rows.append({**w, "samples": n, "ng": ng, "ok_rate": qs.pct(n - ng, n), "ng_rate": rate, "fpy": qs.pct(int(w["fok"] or 0), int(w["fn"] or 0)),
                            "ppm": prod["ppm"], "produced": prod["produced"], "top_defect": top[0]["name"] if top else None, "quality_status": status})
        wo_rows.sort(key=lambda x: (-(x["ng_rate"] or 0), x["wo_no"]))
        # SPC: worst monitored characteristic (optionally of the product filter)
        states = [s for s in _spc_states(cur) if not product_id or s["product_id"] == product_id]
        rank = {"OUT_OF_CONTROL": 0, "WARNING": 1, "NORMAL": 2, "NO_DATA": 3}
        states.sort(key=lambda s: (rank[s["status"]], not s["is_critical"]))
        spc_card = None
        if states:
            s = states[0]
            a = s["analysis"]
            spc_card = {"product_id": s["product_id"], "product_code": s["product_code"], "characteristic_id": s["characteristic_id"], "name": s["name"],
                        "unit": a["unit"], "status": a["status"], "cl": a["cl"], "ucl": a["ucl"], "lcl": a["lcl"], "usl": a["usl"], "lsl": a["lsl"],
                        "cpk": a["cpk"], "points": [{"time": p["time"], "value": p["value"], "violation": p["violation"]} for p in a["points"]]}
        spc_counts = Counter(s["status"] for s in states)
        # shifts
        shift_rows = []
        for sh in load_shifts(cur):
            if shift_id and sh["id"] != shift_id:
                continue
            s = _sample_stats(cur, start_dt, end_dt, {**f, "shift_id": sh["id"]})
            shift_rows.append({"shift_id": sh["id"], "name": sh["shift_name"], "samples": s["samples"], "ok_rate": s["ok_rate"], "ng_rate": s["ng_rate"]})
        # machines
        mwhere, mparams = _scope({k: v for k, v in f.items() if k != "machine_id"})
        cur.execute(f"""SELECT ip.machine_id, eq.equipment_code, COUNT(*) n, SUM(r.result='NG') ng FROM quality_inspection_records r
                        JOIN quality_inspection_plans ip ON ip.id=r.plan_id JOIN equipment eq ON eq.id=ip.machine_id
                        WHERE r.inspected_at >= %s AND r.inspected_at < %s {''.join(' AND ' + w for w in mwhere)}
                        GROUP BY ip.machine_id, eq.equipment_code""", (start_dt, end_dt, *mparams))
        machine_rows = []
        for m in cur.fetchall():
            n, ng = int(m["n"]), int(m["ng"] or 0)
            rate = qs.pct(ng, n)
            prod = _production(cur, start_dt, end_dt, {"machine_id": m["machine_id"]})
            cur.execute("""SELECT d.wo_no, mo.mold_code FROM production_plan_details d LEFT JOIN molds mo ON mo.id=d.mold_id
                           WHERE d.machine_id=%s AND d.status IN ('IN_PRODUCTION','ON_HOLD') LIMIT 1""", (m["machine_id"],))
            wo = cur.fetchone() or {}
            top = _defects(cur, start_dt, end_dt, {"machine_id": m["machine_id"]}, 1)[0]
            status = "NORMAL" if rate is None or rate < t["wo_attention"] else "ATTENTION" if rate < t["wo_critical"] else "WARNING"
            if m["machine_id"] in alert_machines and status == "NORMAL":
                status = "ATTENTION"
            machine_rows.append({"machine_id": m["machine_id"], "machine_code": m["equipment_code"], "samples": n, "ng_rate": rate, "ppm": prod["ppm"],
                                 "wo_no": wo.get("wo_no"), "mold_code": wo.get("mold_code"), "top_defect": top[0]["name"] if top else None, "quality_status": status})
        machine_rows.sort(key=lambda x: -(x["ng_rate"] or 0))
        alert_machines |= {m["machine_id"] for m in machine_rows if m["quality_status"] in ("WARNING", "CRITICAL")}
        alerts = qs.list_alerts(cur, 30)
        open_alerts = [a for a in alerts if a["status"] in qs.ALERT_OPEN]
        gauges = qs.load_equipment(cur)
        overdue_gauges = sum(1 for e in gauges if e["state"] == "OVERDUE")
        cur.execute("SELECT COUNT(*) c FROM quality_ng_records WHERE source='CUSTOMER' AND status IN %s" % str(qs.NG_OPEN))
        complaints = int(cur.fetchone()["c"])
        # overall status
        reasons = []
        critical = [a for a in open_alerts if a["severity"] == "CRITICAL"]
        if spc_counts.get("OUT_OF_CONTROL"):
            reasons.append("SPC_OOC")
        if complaints:
            reasons.append("CUSTOMER")
        if any(a["kind"] == "GAUGE_LOCKED" for a in open_alerts):
            reasons.append("GAUGE_LOCKED")
        if critical:
            reasons.append("CRITICAL_ALERT")
        overall = "CRITICAL" if reasons else "NORMAL"
        if overall == "NORMAL":
            if cur_s["fpy"] is not None and cur_s["fpy"] < t["fpy"]:
                reasons.append("FPY_LOW")
            if cur_p["ppm"] is not None and cur_p["ppm"] > t["ppm"] * 0.8:
                reasons.append("PPM_NEAR")
            if any(w["quality_status"] != "NORMAL" for w in wo_rows):
                reasons.append("WO_NG")
            if alert_machines:
                reasons.append("MACHINE")
            if spc_counts.get("WARNING"):
                reasons.append("SPC_WARNING")
            overall = "ATTENTION" if reasons else "NORMAL"
        # data confidence
        cur.execute("SELECT MAX(inspected_at) m FROM quality_inspection_records")
        last_insp = cur.fetchone()["m"]
        cur.execute("SELECT MAX(event_at) m FROM production_execution_logs WHERE event_type IN ('OUTPUT','REJECT')")
        last_prod = cur.fetchone()["m"]
        cur.execute("SELECT COUNT(*) c FROM quality_inspection_plans WHERE status IN ('NOT_STARTED','IN_PROGRESS') AND due_at < NOW()")
        pending_overdue = int(cur.fetchone()["c"])
        confidence = []
        if running and (not last_insp or last_insp < now - timedelta(hours=2)):
            confidence.append("MISSING_INSPECTION")
        if running and (not last_prod or last_prod < now - timedelta(hours=1)):
            confidence.append("PRODUCTION_DELAYED")
        if pending_overdue:
            confidence.append("PENDING_INSPECTION")
        return qs.spc_json({
            "range": {"from": start, "to": end, "prev_from": ps, "prev_to": pe}, "targets": t, "overall": overall, "reasons": reasons,
            "kpis": {"fpy": cur_s["fpy"], "fpy_prev": prev_s["fpy"], "fpy_change": _change(cur_s["fpy"], prev_s["fpy"]),
                     "ppm": cur_p["ppm"], "ppm_prev": prev_p["ppm"], "produced": cur_p["produced"], "reject": cur_p["reject"],
                     "ng_rate": cur_s["ng_rate"], "ng_rate_prev": prev_s["ng_rate"], "samples": cur_s["samples"], "ng_samples": cur_s["ng"],
                     "defects": defect_total, "defects_prev": prev_defect_total, "defect_types": len(defects), "defect_events": sum(d["events"] for d in defects),
                     "wo_ng": wo_ng, "wo_ng_prev": wo_ng_prev, "machines_alert": len(alert_machines & running) if running else len(alert_machines),
                     "machines_running": len(running), "overdue_gauges": overdue_gauges, "complaints": complaints, "open_alerts": len(open_alerts)},
            "trend": trend, "defects": defects, "top_defects": defects[:5], "work_orders": wo_rows, "spc": spc_card,
            "spc_counts": dict(spc_counts), "alerts": alerts[:12], "shifts": shift_rows, "machines": machine_rows[:10],
            "confidence": {"last_inspection": last_insp, "last_production": last_prod, "flags": confidence, "pending_overdue": pending_overdue},
            "system": _system_status(cur)})
    return _read(action)


# =========================================================
# 5.2 INSPECTION MANAGEMENT
# =========================================================


class PlanIn(BaseModel):
    inspection_type: Optional[str] = Field(default=None, max_length=20)
    work_order_id: Optional[int] = None
    product_id: Optional[int] = None
    machine_id: Optional[int] = None
    mold_id: Optional[int] = None
    material_lot_id: Optional[int] = None
    process: Optional[str] = Field(default=None, max_length=30)
    frequency_type: Optional[str] = Field(default=None, max_length=10)
    frequency_value: Optional[int] = Field(default=None, ge=1, le=100000)
    sample_size: Optional[int] = Field(default=None, ge=1, le=200)
    planned_at: Optional[datetime] = None
    due_at: Optional[datetime] = None
    inspector: Optional[str] = Field(default=None, max_length=100)
    remark: Optional[str] = Field(default=None, max_length=1000)
    version: Optional[int] = None
    actor: Optional[str] = Field(default=None, max_length=100)


class MeasurementIn(BaseModel):
    characteristic_id: int
    value: Optional[float] = None
    attr_result: Optional[str] = Field(default=None, max_length=2)
    equipment_id: Optional[int] = None


class SampleIn(BaseModel):
    measurements: list[MeasurementIn] = []
    equipment_id: Optional[int] = None
    result: Optional[str] = Field(default=None, max_length=2)
    defect_code_id: Optional[int] = None
    inspector: Optional[str] = Field(default=None, max_length=100)
    position: Optional[str] = Field(default=None, max_length=100)
    remark: Optional[str] = Field(default=None, max_length=500)
    is_reinspection: bool = False
    actor: Optional[str] = Field(default=None, max_length=100)


class ActionIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=1000)
    version: Optional[int] = None
    actor: Optional[str] = Field(default=None, max_length=100)
    data: dict[str, Any] = {}


@router.get("/inspections")
def inspections(date_from: Optional[date] = None, date_to: Optional[date] = None, work_order_id: Optional[int] = None, product_id: Optional[int] = None,
                inspection_type: Optional[str] = None, process: Optional[str] = None, machine_id: Optional[int] = None, mold_id: Optional[int] = None,
                status: Optional[str] = None, search: Optional[str] = None):
    _sync()
    start, end, start_dt, end_dt = _range(date_from, date_to)
    f = {"work_order_id": work_order_id, "product_id": product_id, "inspection_type": inspection_type, "machine_id": machine_id, "mold_id": mold_id}

    def action(cur):
        now = datetime.now()
        where, params = _scope(f)
        if process:
            where.append("ip.process=%s"); params.append(process)
        # plans of the period + every plan still open (so that nothing overdue is hidden)
        base = f"""(ip.planned_at >= %s AND ip.planned_at < %s OR ip.status IN ('NOT_STARTED','IN_PROGRESS') AND ip.planned_at < %s)"""
        cur.execute(f"{qs.PLAN_SELECT} WHERE {base}{''.join(' AND ' + w for w in where)} ORDER BY ip.planned_at DESC",
                    (start_dt, end_dt, end_dt, *params))
        plans = [qs.enrich_plan(p, now) for p in cur.fetchall()]
        if search:
            s = search.lower()
            plans = [p for p in plans if s in " ".join(str(p.get(k) or "") for k in ("plan_no", "wo_no", "product_code", "product_name", "lot_no", "machine_code")).lower()]
        counts = Counter(p["display_status"] for p in plans)
        listed = [p for p in plans if not status or p["display_status"] == status]
        stats = _sample_stats(cur, start_dt, end_dt, f)
        yd = start - timedelta(days=1)
        ystats = _sample_stats(cur, _dt(yd), _dt(yd) + timedelta(days=1), f)
        cur.execute(f"{qs.PLAN_SELECT} WHERE ip.planned_at >= %s AND ip.planned_at < %s{''.join(' AND ' + w for w in where)}", (_dt(yd), _dt(yd) + timedelta(days=1), *params))
        yplans = [qs.enrich_plan(p, now) for p in cur.fetchall()]
        ycounts = Counter(p["status"] for p in yplans)
        # NG by process / by type (samples of the period)
        cur.execute(f"""SELECT ip.process, ip.inspection_type, COUNT(*) n, SUM(r.result='NG') ng FROM quality_inspection_records r
                        JOIN quality_inspection_plans ip ON ip.id=r.plan_id WHERE r.inspected_at >= %s AND r.inspected_at < %s
                        {''.join(' AND ' + w for w in where)} GROUP BY ip.process, ip.inspection_type""", (start_dt, end_dt, *params))
        by_process, by_type = defaultdict(lambda: [0, 0]), defaultdict(lambda: [0, 0])
        for r in cur.fetchall():
            for bucket, key in ((by_process, r["process"]), (by_type, r["inspection_type"])):
                bucket[key][0] += int(r["n"]); bucket[key][1] += int(r["ng"] or 0)
        # top defects found at inspection (samples) with the previous-period trend
        def top(s_dt, e_dt):
            cur.execute(f"""SELECT rc.id, rc.reason_code, rc.reason_name, COUNT(*) c FROM quality_inspection_records r
                            JOIN quality_inspection_plans ip ON ip.id=r.plan_id JOIN reason_codes rc ON rc.id=r.defect_code_id
                            WHERE r.result='NG' AND r.inspected_at >= %s AND r.inspected_at < %s {''.join(' AND ' + w for w in where)}
                            GROUP BY rc.id, rc.reason_code, rc.reason_name ORDER BY c DESC""", (s_dt, e_dt, *params))
            return cur.fetchall()
        ps, pe = _prev(start, end)
        prev_top = {r["id"]: int(r["c"]) for r in top(_dt(ps), _dt(pe) + timedelta(days=1))}
        cur_top = top(start_dt, end_dt)
        total_def = sum(int(r["c"]) for r in cur_top)
        top5 = [{"id": r["id"], "code": r["reason_code"], "name": r["reason_name"], "qty": int(r["c"]), "share": qs.pct(int(r["c"]), total_def, 1),
                 "prev": prev_top.get(r["id"], 0)} for r in cur_top[:5]]
        recent = sorted([p for p in plans if p["status"] == "COMPLETED"], key=lambda p: p["completed_at"] or p["planned_at"], reverse=True)[:12]
        return qs.spc_json({
            "kpis": {"total": len(plans), "completed": counts.get("COMPLETED", 0), "in_progress": counts.get("IN_PROGRESS", 0),
                     "not_started": counts.get("NOT_STARTED", 0), "overdue": counts.get("OVERDUE", 0), "cancelled": counts.get("CANCELLED", 0),
                     "ok_rate": stats["ok_rate"], "ng_rate": stats["ng_rate"], "samples": stats["samples"], "ok": stats["ok"], "ng": stats["ng"],
                     "y_total": len(yplans), "y_completed": ycounts.get("COMPLETED", 0), "y_ok_rate": ystats["ok_rate"], "y_ng_rate": ystats["ng_rate"]},
            "counts": dict(counts), "plans": listed,
            "by_process": [{"process": k, "samples": v[0], "ng": v[1], "ng_rate": qs.pct(v[1], v[0])} for k, v in by_process.items()],
            "by_type": [{"type": k, "samples": v[0], "ok": v[0] - v[1], "ng": v[1], "ok_rate": qs.pct(v[0] - v[1], v[0]), "ng_rate": qs.pct(v[1], v[0])}
                        for k, v in sorted(by_type.items())],
            "top_defects": top5, "recent": recent, "system": _system_status(cur)})
    return _read(action)


@router.get("/inspections/{plan_id}")
def inspection_detail(plan_id: int):
    def action(cur):
        p = qs.load_plan(cur, plan_id)
        chars = qs.characteristics(cur, p["standard_revision_id"])
        cur.execute("SELECT id, ng_no, status, priority FROM quality_ng_records WHERE inspection_plan_id=%s", (plan_id,))
        ng = cur.fetchall()
        records = attach_employee_names(cur, qs.plan_records(cur, plan_id), ["inspector"])  # shown as "Full name - code"
        return qs.spc_json({"plan": p, "characteristics": chars, "records": records, "events": qs.events(cur, "PLAN", plan_id),
                            "ng": ng, "control": qs.targets(cur)["equipment_control"]})
    return _read(action)


@router.post("/inspections")
def create_inspection(payload: PlanIn):
    return _write(lambda cur: qs.create_plan(cur, payload.model_dump(), payload.actor or "SYSTEM"))


@router.put("/inspections/{plan_id}")
def update_inspection(plan_id: int, payload: PlanIn):
    return _write(lambda cur: qs.update_plan(cur, plan_id, payload.model_dump(), payload.actor or "SYSTEM"))


@router.post("/inspections/{plan_id}/samples")
def add_sample(plan_id: int, payload: SampleIn):
    data = payload.model_dump()
    result = _write(lambda cur: qs.record_sample(cur, plan_id, data, payload.actor or "SYSTEM"))
    _last_sync["at"] = 0
    return result


@router.post("/inspections/{plan_id}/{action}")
def inspection_action(plan_id: int, action: str, payload: ActionIn):
    return _write(lambda cur: qs.plan_action(cur, plan_id, action.upper(), {**payload.data, "note": payload.note, "version": payload.version},
                                            payload.actor or "SYSTEM"))


# =========================================================
# 5.3 SPC MONITORING
# =========================================================


def _spc_filters(product_id, work_order_id, machine_id, mold_id, material_lot_id, shift_id, date_from, date_to):
    f = {"product_id": product_id, "work_order_id": work_order_id, "machine_id": machine_id, "mold_id": mold_id, "material_lot_id": material_lot_id,
         "shift_id": shift_id}
    if date_from:
        f["date_from"] = _dt(date_from)
    if date_to:
        f["date_to"] = _dt(date_to) + timedelta(days=1)
    return f


@router.get("/spc")
def spc_overview(product_id: Optional[int] = None, work_order_id: Optional[int] = None, machine_id: Optional[int] = None, mold_id: Optional[int] = None,
                 material_lot_id: Optional[int] = None, shift_id: Optional[int] = None, date_from: Optional[date] = None, date_to: Optional[date] = None,
                 revision_id: Optional[int] = None, limit: int = 30):
    if not product_id and not work_order_id:
        raise HTTPException(status_code=400, detail="Select a product or a work order.")

    def action(cur):
        wo = None
        pid = product_id
        if work_order_id:
            cur.execute("""SELECT d.*, eq.equipment_code AS machine_code, mo.mold_code, mo.cavity_count, s.shift_name FROM production_plan_details d
                           LEFT JOIN equipment eq ON eq.id=d.machine_id LEFT JOIN molds mo ON mo.id=d.mold_id LEFT JOIN shifts s ON s.id=d.shift_id
                           WHERE d.id=%s""", (work_order_id,))
            wo = cur.fetchone()
            if not wo:
                raise HTTPException(status_code=404, detail="Work order not found.")
            pid = wo["product_id"]
        cur.execute("""SELECT p.id, p.product_code, p.product_name, p.image_url, f.family_name FROM products p
                       LEFT JOIN product_families f ON f.id=p.product_family_id WHERE p.id=%s""", (pid,))
        product = cur.fetchone()
        if not product:
            raise HTTPException(status_code=404, detail="Product not found.")
        cur.execute("""SELECT r.id, r.revision_no, r.standard_name, r.status, r.customer, r.process FROM quality_standard_revisions r
                       WHERE r.product_id=%s AND r.status IN ('ACTIVE','OBSOLETE','APPROVED') ORDER BY r.status='ACTIVE' DESC, r.revision_no DESC""", (pid,))
        revisions = cur.fetchall()
        rev = next((r for r in revisions if r["id"] == revision_id), None) or (revisions[0] if revisions else None)
        f = _spc_filters(pid, work_order_id, machine_id, mold_id, material_lot_id, shift_id, date_from, date_to)
        chars = []
        for c in qs.characteristics(cur, rev["id"]) if rev else []:
            if (c.get("characteristic_type") or "VARIABLE") == "ATTRIBUTE":
                chars.append(spc.attribute_summary(c, cur, f))
                continue
            a = spc.analyse(c, spc.load_measurements(cur, c["id"], f), limit=limit)
            chars.append({k: a[k] for k in ("characteristic_id", "name", "unit", "chart_type", "characteristic_type", "status", "cp", "cpk",
                                            "count", "is_critical", "cpk_target")} | {"is_spc": bool(c["is_spc"])})
        monitored = [c for c in chars if c["status"] != "NO_DATA"]
        cps = [c["cp"] for c in chars if c.get("cp") is not None]
        cpks = [c["cpk"] for c in chars if c.get("cpk") is not None]
        material = None
        if pid:
            cur.execute("""SELECT m.material_code, m.material_name FROM boms b JOIN bom_items bi ON bi.bom_id=b.id JOIN materials m ON m.id=bi.material_id
                           WHERE b.product_id=%s ORDER BY bi.id LIMIT 1""", (pid,))
            material = cur.fetchone()
        if wo:
            attach_employee_names(cur, [wo], ["operator_code"])
        return qs.spc_json({"product": product, "revision": rev, "revisions": revisions, "work_order": wo, "material": material, "characteristics": chars,
                            "summary": {"total": len(chars), "monitored": len(monitored), "normal": sum(1 for c in chars if c["status"] == "NORMAL"),
                                        "warning": sum(1 for c in chars if c["status"] == "WARNING"),
                                        "out_of_control": sum(1 for c in chars if c["status"] == "OUT_OF_CONTROL"),
                                        "no_data": sum(1 for c in chars if c["status"] == "NO_DATA"),
                                        "cp_avg": round(sum(cps) / len(cps), 3) if cps else None, "cpk_avg": round(sum(cpks) / len(cpks), 3) if cpks else None},
                            "updated_at": datetime.now()})
    try:
        return _read(action)
    except KeyError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/spc/{characteristic_id}")
def spc_detail(characteristic_id: int, product_id: Optional[int] = None, work_order_id: Optional[int] = None, machine_id: Optional[int] = None,
               mold_id: Optional[int] = None, material_lot_id: Optional[int] = None, shift_id: Optional[int] = None, date_from: Optional[date] = None,
               date_to: Optional[date] = None, limit: int = 30, chart_type: Optional[str] = None):
    def action(cur):
        cur.execute("SELECT * FROM quality_standard_characteristics WHERE id=%s", (characteristic_id,))
        c = cur.fetchone()
        if not c:
            raise HTTPException(status_code=404, detail="Characteristic not found.")
        if chart_type in ("XBAR_R", "IMR"):
            c = {**c, "chart_type": chart_type}
        f = _spc_filters(product_id, work_order_id, machine_id, mold_id, material_lot_id, shift_id, date_from, date_to)
        rows = spc.load_measurements(cur, characteristic_id, f)
        a = spc.analyse(c, rows, limit=limit)
        labels = {p["label"] for p in a["points"]}
        shown = [r for r in rows if c["chart_type"] == "IMR" or r["plan_no"] in labels]
        if c["chart_type"] == "IMR":
            shown = shown[-limit:]
        history = [{"no": i + 1, "time": r["inspected_at"], "value": float(r["measured_value"]), "inspector": r["inspector"], "gauge": r["gauge_code"],
                    "gauge_name": r["gauge_name"], "position": r["position"] or f"Sample {r['sample_no']}", "result": r["result"], "remark": r["remark"],
                    "plan_no": r["plan_no"], "wo_no": r["wo_no"], "machine_code": r["machine_code"], "mold_code": r["mold_code"], "lot_no": r["lot_no"]}
                   for i, r in enumerate(shown)]
        attach_employee_names(cur, history, ["inspector"])  # shown as "Full name - code"
        return qs.spc_json({**a, "history": history, "cpk_trend": spc.cpk_trend(c, rows)})
    return _read(action)


# =========================================================
# 5.4 NG MANAGEMENT
# =========================================================


class NgIn(BaseModel):
    source: Optional[str] = Field(default=None, max_length=20)
    work_order_id: Optional[int] = None
    product_id: Optional[int] = None
    machine_id: Optional[int] = None
    mold_id: Optional[int] = None
    material_lot_id: Optional[int] = None
    defect_code_id: Optional[int] = None
    process: Optional[str] = Field(default=None, max_length=30)
    ng_qty: Optional[int] = Field(default=None, ge=0)
    inspected_qty: Optional[int] = Field(default=None, ge=0)
    description: Optional[str] = Field(default=None, max_length=1000)
    priority: Optional[str] = Field(default=None, max_length=10)
    owner: Optional[str] = Field(default=None, max_length=100)
    due_date: Optional[date] = None
    detected_at: Optional[datetime] = None
    disposition: Optional[str] = Field(default=None, max_length=20)
    rework_qty: Optional[int] = Field(default=None, ge=0)
    scrap_qty: Optional[int] = Field(default=None, ge=0)
    containment: Optional[str] = Field(default=None, max_length=1000)
    root_cause_category: Optional[str] = Field(default=None, max_length=20)
    root_cause: Optional[str] = Field(default=None, max_length=2000)
    corrective_action: Optional[str] = Field(default=None, max_length=2000)
    preventive_action: Optional[str] = Field(default=None, max_length=2000)
    verification_result: Optional[str] = Field(default=None, max_length=1000)
    hold_wo: bool = False
    note: Optional[str] = Field(default=None, max_length=1000)
    version: Optional[int] = None
    actor: Optional[str] = Field(default=None, max_length=100)


NG_KINDS = ("WO_NG", "MACHINE_NG", "MOLD_REPEAT", "NG_CRITICAL", "NG_OVERDUE", "NG_CUSTOMER", "SPC_OOC", "PPM_HIGH")


@router.get("/ng")
def ng_page(date_from: Optional[date] = None, date_to: Optional[date] = None, work_order_id: Optional[int] = None, product_id: Optional[int] = None,
            source: Optional[str] = None, defect_code_id: Optional[int] = None, process: Optional[str] = None, machine_id: Optional[int] = None,
            mold_id: Optional[int] = None, owner: Optional[str] = None, status: Optional[str] = None, priority: Optional[str] = None,
            search: Optional[str] = None):
    _sync()
    start, end, start_dt, end_dt = _range(date_from, date_to)
    f = {"work_order_id": work_order_id, "product_id": product_id, "machine_id": machine_id, "mold_id": mold_id}

    def action(cur):
        today = date.today()
        where, params = _ng_scope(f)
        for key, col in ((source, "n.source"), (defect_code_id, "n.defect_code_id"), (process, "n.process"), (owner, "n.owner"), (priority, "n.priority")):
            if key:
                where.append(f"{col}=%s"); params.append(key)
        wsql = "".join(" AND " + w for w in where)
        cur.execute(f"{qs.NG_SELECT} WHERE n.detected_at >= %s AND n.detected_at < %s{wsql} ORDER BY n.detected_at DESC", (start_dt, end_dt, *params))
        rows = [qs.enrich_ng(n, today) for n in cur.fetchall()]
        if search:
            s = search.lower()
            rows = [n for n in rows if s in " ".join(str(n.get(k) or "") for k in ("ng_no", "wo_no", "product_code", "product_name", "defect_name", "machine_code",
                                                                                  "mold_code", "description")).lower()]
        yd = today - timedelta(days=1)
        cur.execute(f"SELECT status, COUNT(*) c FROM quality_ng_records n WHERE n.detected_at >= %s AND n.detected_at < %s{wsql} GROUP BY status",
                    (_dt(yd), _dt(today), *params))
        yc = {r["status"]: int(r["c"]) for r in cur.fetchall()}
        st = Counter(n["status"] for n in rows)
        stats, ystats = _sample_stats(cur, start_dt, end_dt, f), _sample_stats(cur, _dt(yd), _dt(today), f)
        repeat = sum(1 for n in rows if n["is_repeat"])
        listed = [n for n in rows if not status or n["status"] == status]
        listed.sort(key=lambda n: (n["status"] == "CLOSED", qs.PRIORITY_ORDER[n["priority"]], -n["detected_at"].timestamp()))
        by_source = Counter()
        by_process = Counter()
        defect_qty = Counter()
        for n in rows:
            by_source[n["source"] if n["source"] in ("IQC", "IPQC", "FQC", "OQC") else "OTHER"] += n["ng_qty"]
            by_process[n["process"]] += n["ng_qty"]
            defect_qty[(n["defect_code_id"], n["defect_name"] or "Unclassified")] += n["ng_qty"]
        ps, pe = _prev(start, end)
        prev_def = {d["defect_code_id"]: d["qty"] for d in _defects(cur, _dt(ps), _dt(pe) + timedelta(days=1), f)[0]}
        total_qty = sum(defect_qty.values())
        top = [{"defect_code_id": k[0], "name": k[1], "qty": v, "share": qs.pct(v, total_qty, 1), "prev": prev_def.get(k[0], 0)}
               for k, v in defect_qty.most_common(5)]
        days = _trend_days(start, end)
        trend = []
        for d in days:
            cur.execute(f"SELECT COALESCE(SUM(n.ng_qty),0) q, COUNT(*) c FROM quality_ng_records n WHERE n.detected_at >= %s AND n.detected_at < %s{wsql}",
                        (_dt(d), _dt(d) + timedelta(days=1), *params))
            r = cur.fetchone()
            trend.append({"date": d, "qty": int(r["q"]), "records": int(r["c"])})
        by_machine = defaultdict(int)
        for n in rows:
            if n["machine_id"]:
                by_machine[(n["machine_id"], n["machine_code"])] += n["ng_qty"]
        machines = []
        for (mid, code), q in by_machine.items():
            prod = _production(cur, start_dt, end_dt, {"machine_id": mid})
            machines.append({"machine_id": mid, "machine_code": code, "ng_qty": q, "produced": prod["produced"],
                             "ng_rate": qs.pct(q, prod["produced"]) if prod["produced"] else None})
        machines.sort(key=lambda m: -(m["ng_rate"] or 0))
        repeats = Counter()
        for n in rows:
            if n["is_repeat"]:
                repeats[(n["defect_code_id"], n["defect_name"] or "Unclassified")] += 1
        repeat_top = []
        for (did, name), c in repeats.most_common(5):
            total_def = sum(1 for n in rows if n["defect_code_id"] == did)
            series = [sum(1 for n in rows if n["defect_code_id"] == did and n["detected_at"].date() == d) for d in days]
            repeat_top.append({"defect_code_id": did, "name": name, "repeats": c, "rate": qs.pct(c, total_def, 1), "series": series})
        stage = {"NEW": 0, "INVESTIGATING": 0, "CA_IN_PROGRESS": 0, "WAITING_VERIFICATION": 0, "CLOSED": 0}
        for n in rows:
            stage["INVESTIGATING" if n["status"] == "REOPENED" else n["status"]] += 1
        return qs.spc_json({
            "kpis": {"total": len(rows), "in_progress": st["INVESTIGATING"] + st["CA_IN_PROGRESS"] + st["WAITING_VERIFICATION"],
                     "pending": st["NEW"] + st["REOPENED"], "closed": st["CLOSED"], "ng_rate": stats["ng_rate"], "ng_rate_prev": ystats["ng_rate"],
                     "repeat_rate": qs.pct(repeat, len(rows), 1), "repeat": repeat, "ng_qty": total_qty, "overdue": sum(1 for n in rows if n["overdue"]),
                     "y_total": sum(yc.values()), "y_pending": yc.get("NEW", 0) + yc.get("REOPENED", 0), "y_closed": yc.get("CLOSED", 0),
                     "y_in_progress": yc.get("INVESTIGATING", 0) + yc.get("CA_IN_PROGRESS", 0) + yc.get("WAITING_VERIFICATION", 0)},
            "records": listed, "by_source": [[k, v] for k, v in by_source.most_common()], "by_process": [[k, v] for k, v in by_process.most_common()],
            "top_defects": top, "trend": trend, "machines": machines[:8], "stages": stage, "repeat_top": repeat_top,
            "alerts": qs.list_alerts(cur, 10, NG_KINDS), "system": _system_status(cur)})
    return _read(action)


@router.get("/ng/{ng_id}")
def ng_detail(ng_id: int):
    def action(cur):
        n = qs.load_ng(cur, ng_id)
        cur.execute(f"{qs.NG_SELECT} WHERE n.repeat_of_id=%s OR n.id=%s ORDER BY n.detected_at DESC", (ng_id, n["repeat_of_id"] or 0))
        related = [r for r in cur.fetchall() if r["id"] != ng_id]
        return qs.spc_json({"ng": n, "events": qs.ng_timeline(cur, ng_id), "related": related})
    return _read(action)


@router.post("/ng")
def create_ng(payload: NgIn):
    return _write(lambda cur: qs.create_ng(cur, payload.model_dump(), payload.actor or "SYSTEM"))


@router.put("/ng/{ng_id}")
def update_ng(ng_id: int, payload: NgIn):
    return _write(lambda cur: qs.update_ng(cur, ng_id, payload.model_dump(exclude_unset=True), payload.actor or "SYSTEM"))


@router.post("/ng/{ng_id}/{action}")
def ng_action(ng_id: int, action: str, payload: NgIn):
    return _write(lambda cur: qs.ng_action(cur, ng_id, action.upper(), payload.model_dump(exclude_unset=True), payload.actor or "SYSTEM"))


# =========================================================
# ALERTS
# =========================================================


@router.get("/alerts")
def alerts(limit: int = 100):
    _sync()
    return _read(lambda cur: qs.spc_json(qs.list_alerts(cur, limit)))


@router.post("/alerts/{alert_id}/{action}")
def alert_action(alert_id: int, action: str, payload: ActionIn):
    return _write(lambda cur: qs.alert_action(cur, alert_id, action.upper(), {**payload.data, "note": payload.note}, payload.actor or "SYSTEM"))


# =========================================================
# 5.5 MEASURING EQUIPMENT
# =========================================================


def _months(n=6):
    today = date.today()
    out, y, m = [], today.year, today.month
    for _ in range(n):
        out.append((y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def _month_range(y, m):
    start = date(y, m, 1)
    end = date(y + (m == 12), (m % 12) + 1, 1) - timedelta(days=1)
    return start, end


@router.get("/equipment")
def equipment_page(type_id: Optional[int] = None, location: Optional[str] = None, state: Optional[str] = None, manufacturer: Optional[str] = None,
                   owner: Optional[str] = None, search: Optional[str] = None):
    _sync()

    def action(cur):
        t = qs.targets(cur)
        rows = qs.load_equipment(cur)
        rows = [e for e in rows if (not type_id or e["equipment_type_id"] == type_id) and (not location or e["location"] == location)
                and (not manufacturer or e["manufacturer"] == manufacturer) and (not owner or e["owner_code"] == owner)]
        if search:
            s = search.lower()
            rows = [e for e in rows if s in " ".join(str(e.get(k) or "") for k in ("equipment_code", "equipment_name", "serial_number", "model", "type_name")).lower()]
        c = Counter(e["state"] for e in rows)
        listed = [e for e in rows if not state or e["state"] == state]
        due = sorted([e for e in rows if e["state"] in ("DUE_SOON", "OVERDUE")], key=lambda e: e["days_left"])
        need = [e for e in rows if e["state"] != "SCRAPPED" and e["status"] != "INACTIVE"]
        valid = [e for e in need if e["next_calibration_date"] and e["next_calibration_date"] >= date.today() and e["state"] not in ("OUT_OF_SERVICE",)]
        monthly = []
        for y, m in _months():
            s, e_ = _month_range(y, m)
            cur.execute("SELECT COUNT(*) c FROM measuring_equipment_calibration_history WHERE calibration_date BETWEEN %s AND %s", (s, e_))
            done = int(cur.fetchone()["c"])
            cur.execute("""SELECT COUNT(*) c FROM quality_calibration_plans WHERE status<>'CANCELLED' AND planned_date BETWEEN %s AND %s
                           AND planned_date < CURDATE() AND (status<>'COMPLETED' OR calibration_date > planned_date)""", (s, e_))
            monthly.append({"month": f"{m:02d}/{y}", "calibrated": done, "overdue": int(cur.fetchone()["c"])})
        return qs.spc_json({
            "kpis": {"total": len(rows), "in_service": c["IN_SERVICE"] + c["DUE_SOON"], "due_soon": c["DUE_SOON"], "overdue": c["OVERDUE"],
                     "under_repair": c["UNDER_REPAIR"], "out_of_service": c["OUT_OF_SERVICE"] + c["SCRAPPED"], "not_calibrated": c["NOT_CALIBRATED"],
                     "in_calibration": c["IN_CALIBRATION"]},
            "counts": dict(c), "equipment": listed, "due": due[:20],
            "by_type": [[k, v] for k, v in Counter(e["type_name"] for e in rows).most_common()],
            "by_location": [[k or "-", v] for k, v in Counter(e["location"] for e in rows).most_common()],
            "monthly": monthly, "compliance": {"valid": len(valid), "total": len(need), "rate": qs.pct(len(valid), len(need), 1), "target": t["cal_compliance"]},
            "due_days": t["cal_due_days"], "manufacturers": sorted({e["manufacturer"] for e in qs.load_equipment(cur) if e["manufacturer"]}),
            "system": _system_status(cur)})
    return _read(action)


@router.get("/equipment/{equipment_id}")
def equipment_detail(equipment_id: int):
    def action(cur):
        e = attach_employee_names(cur, [qs.load_equipment(cur, equipment_id)], ["owner_code"])[0]
        cur.execute("SELECT * FROM measuring_equipment_calibration_history WHERE equipment_id=%s ORDER BY calibration_date DESC, id DESC", (equipment_id,))
        history = cur.fetchall()
        cur.execute("SELECT * FROM quality_equipment_events WHERE equipment_id=%s ORDER BY event_at DESC, id DESC LIMIT 100", (equipment_id,))
        evs = cur.fetchall()
        cur.execute(qs.CAL_SELECT + " WHERE cp.equipment_id=%s ORDER BY cp.planned_date DESC", (equipment_id,))
        plans = [qs.enrich_cal(p, date.today(), 7) for p in cur.fetchall()]
        cur.execute("""SELECT r.inspected_at, ip.plan_no, ip.inspection_type, d.wo_no, c.characteristic_name, m.measured_value, m.result
                       FROM quality_measurements m JOIN quality_inspection_records r ON r.id=m.record_id JOIN quality_inspection_plans ip ON ip.id=r.plan_id
                       LEFT JOIN production_plan_details d ON d.id=ip.work_order_id JOIN quality_standard_characteristics c ON c.id=m.characteristic_id
                       WHERE m.equipment_id=%s ORDER BY r.inspected_at DESC LIMIT 30""", (equipment_id,))
        usage = cur.fetchall()
        return qs.spc_json({"equipment": e, "history": history, "events": evs, "plans": plans, "usage": usage})
    return _read(action)


class EquipmentActionIn(BaseModel):
    note: Optional[str] = Field(default=None, max_length=1000)
    usage_location: Optional[str] = Field(default=None, max_length=100)
    owner: Optional[str] = Field(default=None, max_length=100)
    planned_date: Optional[date] = None
    priority: Optional[str] = Field(default=None, max_length=10)
    version: Optional[int] = None
    actor: Optional[str] = Field(default=None, max_length=100)


@router.post("/equipment/{equipment_id}/{action}")
def equipment_action(equipment_id: int, action: str, payload: EquipmentActionIn):
    result = _write(lambda cur: qs.equipment_action(cur, equipment_id, action.upper(), payload.model_dump(), payload.actor or "SYSTEM"))
    _last_sync["at"] = 0
    return result


# =========================================================
# 5.6 CALIBRATION MANAGEMENT
# =========================================================


class CalIn(BaseModel):
    equipment_id: Optional[int] = None
    planned_date: Optional[date] = None
    lab: Optional[str] = Field(default=None, max_length=150)
    owner: Optional[str] = Field(default=None, max_length=100)
    priority: Optional[str] = Field(default=None, max_length=10)
    remark: Optional[str] = Field(default=None, max_length=1000)
    result: Optional[str] = Field(default=None, max_length=10)
    calibration_date: Optional[date] = None
    certificate_no: Optional[str] = Field(default=None, max_length=100)
    certificate_url: Optional[str] = Field(default=None, max_length=1000)
    calibrated_by: Optional[str] = Field(default=None, max_length=150)
    note: Optional[str] = Field(default=None, max_length=1000)
    version: Optional[int] = None
    actor: Optional[str] = Field(default=None, max_length=100)


@router.get("/calibration")
def calibration_page(date_from: Optional[date] = None, date_to: Optional[date] = None, status: Optional[str] = None, type_id: Optional[int] = None,
                     location: Optional[str] = None, lab: Optional[str] = None, owner: Optional[str] = None, search: Optional[str] = None):
    _sync()
    today = date.today()
    start = date_from or today.replace(day=1)
    end = date_to or (_month_range(today.year, today.month)[1])

    def action(cur):
        t = qs.targets(cur)
        cur.execute(qs.CAL_SELECT + """ WHERE (cp.planned_date BETWEEN %s AND %s OR cp.status IN ('PLANNED','IN_PROGRESS'))
                                         ORDER BY cp.planned_date""", (start, end))
        rows = [qs.enrich_cal(p, today, t["cal_due_days"]) for p in cur.fetchall()]
        rows = [p for p in rows if (not type_id or p["equipment_type_id"] == type_id) and (not location or p["location"] == location)
                and (not lab or p["lab"] == lab) and (not owner or p["owner"] == owner)]
        if search:
            s = search.lower()
            rows = [p for p in rows if s in " ".join(str(p.get(k) or "") for k in ("plan_no", "equipment_code", "equipment_name", "model", "serial_number")).lower()]
        c = Counter(p["display_status"] for p in rows)
        due_soon = [p for p in rows if p["due_soon"]]
        listed = [p for p in rows if not status or (p["display_status"] == status if status != "DUE_SOON" else p["due_soon"])]
        listed.sort(key=lambda p: (p["display_status"] != "OVERDUE", p["status"] in ("COMPLETED", "CANCELLED"), p["planned_date"]))
        comp = qs.cal_compliance(cur, start, end)
        overdue_eq = sorted([e for e in qs.load_equipment(cur) if e["state"] == "OVERDUE"], key=lambda e: e["days_left"])
        monthly = []
        for y, m in _months():
            s, e_ = _month_range(y, m)
            cur.execute("""SELECT COUNT(*) total, SUM(status='COMPLETED') done,
                                  SUM(planned_date < CURDATE() AND (status<>'COMPLETED' OR calibration_date > planned_date)) late
                           FROM quality_calibration_plans WHERE status<>'CANCELLED' AND planned_date BETWEEN %s AND %s""", (s, e_))
            r = cur.fetchone()
            monthly.append({"month": f"{m:02d}/{y}", "planned": int(r["total"] or 0), "completed": int(r["done"] or 0), "overdue": int(r["late"] or 0)})
        cur.execute("""SELECT h.*, me.equipment_code, me.equipment_name FROM measuring_equipment_calibration_history h
                       JOIN measuring_equipment me ON me.id=h.equipment_id ORDER BY h.calibration_date DESC, h.id DESC LIMIT 15""")
        recent = cur.fetchall()
        return qs.spc_json({
            "range": {"from": start, "to": end},
            "kpis": {"total": len(rows), "completed": c["COMPLETED"], "in_progress": c["IN_PROGRESS"], "overdue": c["OVERDUE"], "due_soon": len(due_soon),
                     "planned": c["PLANNED"], "compliance": comp["rate"], "compliance_due": comp["due"], "compliance_on_time": comp["on_time"]},
            "plans": listed, "donut": {"COMPLETED": c["COMPLETED"], "IN_PROGRESS": c["IN_PROGRESS"], "DUE_SOON": len(due_soon), "OVERDUE": c["OVERDUE"],
                                       "PLANNED": c["PLANNED"] - len(due_soon)},
            "overdue_equipment": overdue_eq, "monthly": monthly, "recent": recent, "target": t["cal_compliance"], "due_days": t["cal_due_days"],
            "system": _system_status(cur)})
    return _read(action)


@router.get("/calibration/{plan_id}")
def calibration_detail(plan_id: int):
    def action(cur):
        p = qs.load_cal(cur, plan_id)
        cur.execute("SELECT * FROM measuring_equipment_calibration_history WHERE equipment_id=%s ORDER BY calibration_date DESC, id DESC", (p["equipment_id"],))
        history = cur.fetchall()
        return qs.spc_json({"plan": p, "events": qs.events(cur, "CAL", plan_id), "history": history,
                            "equipment": qs.load_equipment(cur, p["equipment_id"])})
    return _read(action)


@router.post("/calibration")
def create_calibration(payload: CalIn):
    if not payload.equipment_id:
        raise HTTPException(status_code=400, detail="Select the measuring equipment.")
    result = _write(lambda cur: qs.create_cal_plan(cur, payload.model_dump(), payload.actor or "SYSTEM"))
    _last_sync["at"] = 0
    return result


@router.put("/calibration/{plan_id}")
def update_calibration(plan_id: int, payload: CalIn):
    return _write(lambda cur: qs.update_cal_plan(cur, plan_id, payload.model_dump(), payload.actor or "SYSTEM"))


@router.post("/calibration/{plan_id}/{action}")
def calibration_action(plan_id: int, action: str, payload: CalIn):
    result = _write(lambda cur: qs.cal_action(cur, plan_id, action.upper(), payload.model_dump(), payload.actor or "SYSTEM"))
    _last_sync["at"] = 0
    return result
