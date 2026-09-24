"""Mold Management API (screens 3.1 Overview, 3.2 List, 3.3 Detail, 3.4 Status, 3.5 Installation History,
3.6 Shot Counter, 3.7 Location). Master data (create / edit molds, locations, documents) stays in
System Configuration > Mold Master; this API reads it and records operational events."""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.database import get_vcc_plastics_connection
from app.modules.mold import service as ms
from app.modules.production.work_orders.router import _system_status

router = APIRouter(prefix="/api/mold-management", tags=["Mold Management"])


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


class InstallIn(BaseModel):
    machine_id: int = Field(gt=0)
    work_order_id: Optional[int] = Field(default=None, gt=0)
    minutes: Optional[float] = Field(default=None, ge=0, le=1440)
    remark: Optional[str] = Field(default=None, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)


class RemoveIn(BaseModel):
    reason: str = Field(min_length=1, max_length=30)
    minutes: Optional[float] = Field(default=None, ge=0, le=1440)
    remark: Optional[str] = Field(default=None, max_length=500)
    to_location_id: Optional[int] = Field(default=None, gt=0)
    actor: Optional[str] = Field(default=None, max_length=100)


class MoveIn(BaseModel):
    location_id: int = Field(gt=0)
    reason: Optional[str] = Field(default=None, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)


class StatusIn(BaseModel):
    action: str = Field(min_length=1, max_length=30)
    reason: Optional[str] = Field(default=None, max_length=1000)
    location_id: Optional[int] = Field(default=None, gt=0)
    maintenance_type: Optional[str] = Field(default=None, max_length=30)
    technician: Optional[str] = Field(default=None, max_length=100)
    downtime_hours: Optional[float] = Field(default=None, ge=0, le=10000)
    actor: Optional[str] = Field(default=None, max_length=100)


class ShotIn(BaseModel):
    value: int = Field(ge=0)
    reason: str = Field(min_length=1, max_length=500)
    actor: Optional[str] = Field(default=None, max_length=100)


def _pm_open(m):
    """PM still to be planned: not already in maintenance / repair."""
    return m["pm_days_left"] is not None and m["status"] not in ("IN_MAINTENANCE", "IN_REPAIR")


def _pct(a, b, d=1):
    return round(a / b * 100, d) if b else 0


def _timeline(cur, mold_id=None, limit=20, since=None):
    where, params = ["1=1"], []
    if mold_id:
        where.append("ev.mold_id=%s"); params.append(mold_id)
    if since:
        where.append("ev.event_at>=%s"); params.append(since)
    cur.execute(f"""SELECT ev.*, m.mold_code, m.mold_name, e.equipment_code AS machine_code, d.wo_no,
                           fl.node_name AS from_location, tl.node_name AS to_location
                    FROM mold_events ev JOIN molds m ON m.id=ev.mold_id
                    LEFT JOIN equipment e ON e.id=ev.machine_id LEFT JOIN production_plan_details d ON d.id=ev.work_order_id
                    LEFT JOIN mold_location_nodes fl ON fl.id=ev.from_location_id LEFT JOIN mold_location_nodes tl ON tl.id=ev.to_location_id
                    WHERE {' AND '.join(where)} ORDER BY ev.event_at DESC, ev.id DESC LIMIT %s""", (*params, limit))
    return cur.fetchall()


def _slim(m):
    keys = ["id", "mold_code", "mold_name", "mold_type", "mold_group", "cavity_count", "status", "status_since", "current_shot", "design_shot",
            "life_used_pct", "remaining_shot", "daily_avg_shot", "shots_today", "shots_30d", "eol_date", "eol_days", "pm_due_date", "pm_type",
            "pm_days_left", "pm_overdue", "next_pm_shot", "health", "alerts", "product_name", "part_no", "image_url", "revision",
            "current_machine_id", "machine_code", "machine_name", "machine_area", "current_location_id", "location_code", "location_name",
            "location_type", "location_label", "location_since", "since_hours", "idle_days", "lock_reason", "updated_at", "weight_kg",
            "manufacturer", "manufacture_date", "length_mm", "width_mm", "height_mm", "ng_rate_30d", "downtime_hours_30d"]
    out = {k: m.get(k) for k in keys}
    w = m.get("work_order")
    out["wo_id"], out["wo_no"], out["wo_status"] = (w["id"], w["wo_no"], w["status"]) if w else (None, None, None)
    return out


@router.get("/lookups")
def lookups():
    def action(cur):
        cur.execute("""SELECT e.id, e.equipment_code, e.equipment_name, e.operational_status, fn.name AS area_name,
                              (SELECT mold_code FROM molds m WHERE m.current_machine_id=e.id LIMIT 1) AS mounted_mold
                       FROM equipment e JOIN equipment_types t ON t.id=e.equipment_type_id LEFT JOIN factory_structure_nodes fn ON fn.id=e.factory_node_id
                       WHERE t.category='MACHINE' AND e.asset_status='ACTIVE' ORDER BY e.equipment_code""")
        machines = cur.fetchall()
        cur.execute("""SELECT n.id, n.node_code, n.node_name, n.capacity, n.parent_id, t.location_category, t.type_name,
                              (SELECT COUNT(*) FROM molds m WHERE m.current_location_id=n.id AND m.current_machine_id IS NULL AND m.is_active=1) AS mold_count
                       FROM mold_location_nodes n JOIN mold_location_node_types t ON t.id=n.node_type_id WHERE n.status='ACTIVE'
                       ORDER BY t.location_category, n.node_code""")
        locations = cur.fetchall()
        cur.execute("SELECT DISTINCT mold_type FROM molds WHERE mold_type IS NOT NULL AND is_active=1 ORDER BY mold_type")
        types = [r["mold_type"] for r in cur.fetchall()]
        cur.execute("""SELECT DISTINCT p.id, p.product_code, p.product_name FROM mold_products mp JOIN products p ON p.id=mp.product_id
                       WHERE mp.status='ACTIVE' ORDER BY p.product_code""")
        products = cur.fetchall()
        cur.execute("""SELECT d.id, d.wo_no, d.status, d.mold_id, d.machine_id, p.product_name FROM production_plan_details d
                       LEFT JOIN products p ON p.id=d.product_id WHERE d.status IN ('SCHEDULED','RELEASED','IN_PRODUCTION','ON_HOLD') AND d.mold_id IS NOT NULL
                       ORDER BY d.wo_no""")
        wos = cur.fetchall()
        return {"machines": machines, "locations": locations, "mold_types": types, "products": products, "work_orders": wos,
                "statuses": ms.STATUSES, "removal_reasons": list(ms.REMOVAL_REASONS), "planned_reasons": [k for k, v in ms.REMOVAL_REASONS.items() if v],
                "maintenance_types": ms.MAINTENANCE_TYPES, "location_types": ms.LOCATION_TYPES}
    return _read(action)


@router.get("/summary")
def summary(status: str | None = None, location_type: str | None = None, location_id: int | None = None, machine_id: int | None = None,
            mold_type: str | None = None, product_id: int | None = None, life_min: float | None = None, life_max: float | None = None,
            q: str | None = None, trend_days: int = 7):
    """Everything the overview-type screens need: KPIs over all molds, filtered list, distributions, alerts, trends."""
    def action(cur):
        molds = ms.load_molds(cur)
        today = date.today()
        now = datetime.now()
        n = len(molds)
        st = Counter(m["status"] for m in molds)
        loc = Counter(m["location_type"] for m in molds)
        lifes = [m["life_used_pct"] for m in molds if m["life_used_pct"] is not None]
        kpis = {"total": n, "running": st["IN_PRODUCTION"], "available": st["AVAILABLE"], "maintenance": st["IN_MAINTENANCE"], "repair": st["IN_REPAIR"],
                "locked": st["LOCKED"], "out_of_service": st["RETIRED"] + st["SCRAPPED"],
                "avg_life": round(sum(lifes) / len(lifes), 1) if lifes else None,
                "pm_due_7d": sum(1 for m in molds if _pm_open(m) and m["pm_days_left"] <= 7),
                "pm_overdue": sum(1 for m in molds if _pm_open(m) and m["pm_overdue"]),
                "total_shots": sum(int(m["current_shot"] or 0) for m in molds), "shots_today": sum(m["shots_today"] for m in molds),
                "life_90": sum(1 for m in molds if (m["life_used_pct"] or 0) >= 90),
                "eol_30d": sum(1 for m in molds if m["eol_days"] is not None and m["eol_days"] <= 30 and m["status"] not in ms.OUT_OF_SERVICE),
                "avg_health": round(sum(m["health"] for m in molds) / n) if n else None,
                **{f"loc_{k.lower()}": loc[k] for k in ms.LOCATION_TYPES}}

        # filters (list only; KPIs / charts cover every mold)
        def keep(m):
            if status and m["status"] != status.upper() and not (status.upper() == "OUT_OF_SERVICE" and m["status"] in ms.OUT_OF_SERVICE):
                return False
            if location_type and m["location_type"] != location_type.upper():
                return False
            if location_id and m["current_location_id"] != location_id:
                return False
            if machine_id and m["current_machine_id"] != machine_id:
                return False
            if mold_type and m["mold_type"] != mold_type:
                return False
            if product_id and not any(p["product_id"] == product_id for p in m["products"]):
                return False
            life = m["life_used_pct"] or 0
            if life_min is not None and life < life_min:
                return False
            if life_max is not None and life_max < 100 and life > life_max:
                return False
            if q:
                text = " ".join(str(m.get(k) or "") for k in ("mold_code", "mold_name", "product_name", "part_no", "machine_code", "location_name")).lower()
                if q.lower() not in text:
                    return False
            return True
        items = [_slim(m) for m in molds if keep(m)]

        buckets = Counter(ms.life_bucket(m["life_used_pct"] or 0) for m in molds if m["life_used_pct"] is not None)
        alert_counts, alert_molds = Counter(), defaultdict(list)
        for m in molds:
            for a in m["alerts"]:
                alert_counts[a["code"]] += 1
                alert_molds[a["code"]].append({"id": m["id"], "mold_code": m["mold_code"], "level": a["level"]})
        # status by location label (machines, storage nodes, areas)
        by_loc = defaultdict(Counter)
        for m in molds:
            by_loc[(m["location_type"], m["location_label"] or "—")][m["status"]] += 1
        status_by_location = [{"location_type": k[0], "label": k[1], "counts": dict(v), "total": sum(v.values())}
                              for k, v in sorted(by_loc.items(), key=lambda kv: (ms.LOCATION_TYPES.index(kv[0][0]), kv[0][1]))]
        pm_due = sorted([{"id": m["id"], "mold_code": m["mold_code"], "mold_name": m["mold_name"], "pm_due_date": m["pm_due_date"],
                          "pm_days_left": m["pm_days_left"], "pm_type": m["pm_type"], "pm_overdue": m["pm_overdue"]}
                         for m in molds if _pm_open(m) and m["pm_days_left"] <= 14], key=lambda x: x["pm_days_left"])
        top_shots = sorted([m for m in molds if m["shots_today"]], key=lambda m: -m["shots_today"])[:5]
        top_idle = sorted([m for m in molds if m["idle_days"] is not None and m["status"] == "AVAILABLE"], key=lambda m: -m["idle_days"])[:5]
        top_life = sorted([m for m in molds if m["life_used_pct"] is not None], key=lambda m: -m["life_used_pct"])[:5]
        eol = sorted([m for m in molds if m["eol_days"] is not None and m["eol_days"] <= 30 and m["status"] not in ms.OUT_OF_SERVICE], key=lambda m: m["eol_days"])

        # trends
        days = max(7, min(trend_days, 90))
        start = today - timedelta(days=days - 1)
        cur.execute("SELECT shot_date, SUM(shots) AS s FROM mold_shot_daily WHERE shot_date>=%s GROUP BY shot_date", (start,))
        daily = {r["shot_date"]: int(r["s"]) for r in cur.fetchall()}
        total_now = kpis["total_shots"]
        after = sum(v for d, v in daily.items())
        cum, shot_trend = total_now - after, []
        for i in range(days):
            d = start + timedelta(days=i)
            cum += daily.get(d, 0)
            shot_trend.append({"date": d.isoformat(), "shots": daily.get(d, 0), "total": cum})
        cur.execute("SELECT mold_id, installed_at, COALESCE(removed_at, NOW()) AS removed_at FROM mold_installations WHERE COALESCE(removed_at, NOW()) >= %s",
                    (datetime.combine(start, datetime.min.time()),))
        ins = cur.fetchall()
        active = sum(1 for m in molds if m["status"] not in ms.OUT_OF_SERVICE) or 1
        utilization = []
        for i in range(days):
            d0 = datetime.combine(start + timedelta(days=i), datetime.min.time())
            d1 = min(d0 + timedelta(days=1), now)
            hrs = sum(ms._overlap_hours(r["installed_at"], r["removed_at"], d0, d1) for r in ins)
            span = max((d1 - d0).total_seconds() / 3600, 0.01)
            utilization.append({"date": d0.date().isoformat(), "pct": round(hrs / (active * span) * 100, 1)})

        cur.execute("""SELECT n.id, n.node_code, n.node_name, n.capacity, t.location_category,
                              (SELECT COUNT(*) FROM molds m WHERE m.current_location_id=n.id AND m.current_machine_id IS NULL AND m.is_active=1) AS used
                       FROM mold_location_nodes n JOIN mold_location_node_types t ON t.id=n.node_type_id WHERE n.status='ACTIVE' ORDER BY t.location_category, n.node_code""")
        nodes = cur.fetchall()
        capacity = defaultdict(lambda: {"used": 0, "capacity": 0})
        for nd in nodes:
            if nd["capacity"]:
                capacity[nd["location_category"]]["used"] += int(nd["used"])
                capacity[nd["location_category"]]["capacity"] += int(nd["capacity"])
        cur.execute("""SELECT e.id, e.equipment_code, e.operational_status, fn.name AS area_name FROM equipment e JOIN equipment_types t ON t.id=e.equipment_type_id
                       LEFT JOIN factory_structure_nodes fn ON fn.id=e.factory_node_id WHERE t.category='MACHINE' AND e.asset_status='ACTIVE' ORDER BY e.equipment_code""")
        machines = cur.fetchall()
        mounted = {m["current_machine_id"]: m for m in molds if m["current_machine_id"]}
        for mc in machines:
            m = mounted.get(mc["id"])
            mc["mold_id"], mc["mold_code"], mc["mold_status"] = (m["id"], m["mold_code"], m["status"]) if m else (None, None, None)

        return {
            "kpis": kpis, "items": items,
            "status_counts": {s: st[s] for s in ms.STATUSES}, "type_counts": dict(Counter(m["mold_type"] or "—" for m in molds)),
            "life_buckets": {f"{lo}-{hi or 100}": buckets[f"{lo}-{hi or 100}"] for lo, hi in ms.LIFE_BUCKETS},
            "location_counts": {k: loc[k] for k in ms.LOCATION_TYPES}, "status_by_location": status_by_location,
            "alerts": [{"code": c, "count": alert_counts[c], "molds": alert_molds[c]} for c, _ in alert_counts.most_common()],
            "pm_due": pm_due, "top_shots": [_slim(m) for m in top_shots], "top_idle": [_slim(m) for m in top_idle],
            "top_life": [_slim(m) for m in top_life], "eol": [_slim(m) for m in eol],
            "shot_trend": shot_trend, "utilization": utilization,
            "capacity": [{"category": k, **v, "pct": _pct(v["used"], v["capacity"])} for k, v in capacity.items()],
            "nodes": nodes, "machines": machines, "timeline": _timeline(cur, limit=15), "system": _system_status(cur),
        }
    return _read(action)


@router.get("/molds/{mold_id}")
def detail(mold_id: int):
    def action(cur):
        m = ms.enrich(cur, [ms.get_mold(cur, mold_id)])[0]
        today = date.today()
        d30 = datetime.now() - timedelta(days=30)
        cur.execute("""SELECT d.attribute_code, d.attribute_name, d.unit, d.data_type, v.value_text, v.value_number, v.value_boolean, v.value_date
                       FROM mold_attribute_values v JOIN mold_attribute_definitions d ON d.id=v.attribute_definition_id WHERE v.mold_id=%s ORDER BY d.sort_order""", (mold_id,))
        attrs = cur.fetchall()
        for a in attrs:
            a["value"] = a["value_text"] if a["value_text"] is not None else float(a["value_number"]) if a["value_number"] is not None else \
                a["value_boolean"] if a["value_boolean"] is not None else a["value_date"]
        cur.execute("""SELECT i.*, e.equipment_code AS machine_code, e.equipment_name AS machine_name, d.wo_no, d.operator_code,
                              (SELECT ROUND(AVG(l.cycle_time_sec),1) FROM production_execution_logs l WHERE l.mold_id=i.mold_id AND l.machine_id=i.machine_id
                                 AND l.cycle_time_sec IS NOT NULL AND l.event_at BETWEEN i.installed_at AND COALESCE(i.removed_at, NOW())) AS avg_cycle
                       FROM mold_installations i JOIN equipment e ON e.id=i.machine_id LEFT JOIN production_plan_details d ON d.id=i.work_order_id
                       WHERE i.mold_id=%s ORDER BY i.installed_at DESC LIMIT 20""", (mold_id,))
        installs = cur.fetchall()
        cur.execute("""SELECT * FROM mold_events WHERE mold_id=%s AND event_type IN ('MAINTENANCE_DONE','REPAIR_DONE','MAINTENANCE_START','REPAIR_START')
                       ORDER BY event_at DESC, id DESC LIMIT 20""", (mold_id,))
        maintenance = cur.fetchall()
        cur.execute("SELECT shot_date, shots FROM mold_shot_daily WHERE mold_id=%s AND shot_date>%s ORDER BY shot_date", (mold_id, today - timedelta(days=30)))
        daily = {r["shot_date"]: int(r["shots"]) for r in cur.fetchall()}
        cum = int(m["current_shot"] or 0) - sum(daily.values())
        shot_history = []
        for i in range(29, -1, -1):
            d = today - timedelta(days=i)
            cum += daily.get(d, 0)
            shot_history.append({"date": d.isoformat(), "shots": daily.get(d, 0), "total": cum})
        # downtime of the machines while this mold was mounted (30 days), by reason
        cur.execute("""SELECT i.machine_id, i.installed_at, COALESCE(i.removed_at, NOW()) AS e FROM mold_installations i
                       WHERE i.mold_id=%s AND COALESCE(i.removed_at, NOW()) > %s""", (mold_id, d30))
        windows = [{"machine_id": w["machine_id"], "s": max(w["installed_at"], d30), "e": w["e"]} for w in cur.fetchall()]
        by_reason = Counter()
        for w in windows:
            cur.execute("""SELECT COALESCE(r.reason_name, d.reason_text, 'UNSPECIFIED') AS reason, d.started_at, COALESCE(d.ended_at, NOW()) AS ended_at
                           FROM downtime_events d LEFT JOIN reason_codes r ON r.id=d.reason_code_id
                           WHERE d.equipment_id=%s AND d.status<>'CANCELLED' AND d.started_at < %s AND COALESCE(d.ended_at, NOW()) > %s""", (w["machine_id"], w["e"], w["s"]))
            for d in cur.fetchall():
                by_reason[d["reason"]] += ms._overlap_hours(w["s"], w["e"], d["started_at"], d["ended_at"])
        for e in maintenance:
            if e["event_type"] in ("MAINTENANCE_DONE", "REPAIR_DONE") and e["event_at"] > d30 and e["downtime_hours"]:
                by_reason["Scheduled PM" if e["event_type"] == "MAINTENANCE_DONE" else "Mold Repair"] += float(e["downtime_hours"])
        cur.execute("""SELECT COALESCE(SUM(good_qty),0) AS good, COALESCE(SUM(reject_qty),0) AS ng FROM production_execution_logs WHERE mold_id=%s AND event_at>%s""", (mold_id, d30))
        q = cur.fetchone()
        cur.execute("""SELECT COALESCE(r.reason_name, l.reason_text, 'UNSPECIFIED') AS reason, SUM(l.reject_qty) AS qty FROM production_execution_logs l
                       LEFT JOIN reason_codes r ON r.id=l.reason_code_id WHERE l.mold_id=%s AND l.event_at>%s AND l.reject_qty>0
                       GROUP BY COALESCE(r.reason_name, l.reason_text, 'UNSPECIFIED') ORDER BY qty DESC LIMIT 5""", (mold_id, d30))
        top_ng = [{"reason": r["reason"], "qty": int(r["qty"])} for r in cur.fetchall()]
        good, ng = int(q["good"]), int(q["ng"])
        cur.execute("SELECT * FROM mold_documents WHERE mold_id=%s ORDER BY doc_type, uploaded_at DESC", (mold_id,))
        docs = cur.fetchall()
        open_install = next((i for i in installs if i["removed_at"] is None), None)
        return {
            "mold": {**_slim(m), "description": m["description"], "remark": m["remark"], "mold_group": m["mold_group"], "pm_interval_shot": m["pm_interval_shot"],
                     "pm_interval_days": m["pm_interval_days"], "last_pm_at": m["last_pm_at"], "last_pm_shot": m["last_pm_shot"], "products": m["products"],
                     "health_breakdown": m["health_breakdown"], "good_30d": m["good_30d"], "ng_30d": m["ng_30d"], "repairs_180d": m["repairs_180d"],
                     "location_capacity": m["location_capacity"], "work_order": m["work_order"], "product_image": m["product_image"]},
            "attributes": attrs, "installations": installs, "current_installation": open_install, "maintenance": maintenance,
            "shot_history": shot_history, "downtime": [{"reason": k, "hours": round(v, 2)} for k, v in by_reason.most_common()],
            "quality": {"produced": good + ng, "good": good, "ng": ng, "ng_rate": _pct(ng, good + ng, 2), "good_rate": _pct(good, good + ng, 2), "top_ng": top_ng},
            "documents": docs, "events": _timeline(cur, mold_id, limit=50), "system": _system_status(cur),
        }
    return _read(action)


@router.get("/installations")
def installations(date_from: date | None = None, date_to: date | None = None, mold_id: int | None = None, machine_id: int | None = None,
                  product_id: int | None = None, action_type: str | None = None, work_order: str | None = None, q: str | None = None):
    """3.5: every install and every removal is one row; KPIs compare with the previous period of the same length."""
    def action(cur):
        d_to = date_to or date.today()
        d_from = date_from or (d_to - timedelta(days=29))
        if d_from > d_to:
            raise HTTPException(status_code=400, detail="Date from must be before date to.")
        span = (d_to - d_from).days + 1
        p_from, p_to = d_from - timedelta(days=span), d_from - timedelta(days=1)

        def fetch(a, b):
            t0, t1 = datetime.combine(a, datetime.min.time()), datetime.combine(b + timedelta(days=1), datetime.min.time())
            cur.execute("""SELECT i.*, m.mold_code, m.mold_name, m.mold_type, m.cavity_count, m.image_url, e.equipment_code AS machine_code,
                                  e.equipment_name AS machine_name, fn.name AS area_name, d.wo_no, d.product_id, p.product_name
                           FROM mold_installations i JOIN molds m ON m.id=i.mold_id JOIN equipment e ON e.id=i.machine_id
                           LEFT JOIN factory_structure_nodes fn ON fn.id=e.factory_node_id
                           LEFT JOIN production_plan_details d ON d.id=i.work_order_id LEFT JOIN products p ON p.id=d.product_id
                           WHERE (i.installed_at >= %s AND i.installed_at < %s) OR (i.removed_at >= %s AND i.removed_at < %s)
                           ORDER BY i.installed_at DESC""", (t0, t1, t0, t1))
            rows = []
            for r in cur.fetchall():
                if mold_id and r["mold_id"] != mold_id or machine_id and r["machine_id"] != machine_id or product_id and r["product_id"] != product_id:
                    continue
                if work_order and work_order.lower() not in (r["wo_no"] or "").lower():
                    continue
                if q and q.lower() not in f"{r['mold_code']} {r['mold_name']} {r['machine_code']} {r['product_name'] or ''}".lower():
                    continue
                if t0 <= r["installed_at"] < t1:
                    rows.append({**r, "action": "INSTALL", "at": r["installed_at"]})
                if r["removed_at"] and t0 <= r["removed_at"] < t1:
                    rows.append({**r, "action": "REMOVE", "at": r["removed_at"]})
            if action_type:
                rows = [x for x in rows if x["action"] == action_type.upper()]
            rows.sort(key=lambda x: x["at"], reverse=True)
            return rows

        rows, prev = fetch(d_from, d_to), fetch(p_from, p_to)

        def k(rs):
            ins = [x for x in rs if x["action"] == "INSTALL"]
            rem = [x for x in rs if x["action"] == "REMOVE"]
            im = [float(x["install_minutes"]) for x in ins if x["install_minutes"] is not None]
            rmm = [float(x["remove_minutes"]) for x in rem if x["remove_minutes"] is not None]
            return {"total": len(rs), "installs": len(ins), "removals": len(rem), "avg_install_min": round(sum(im) / len(im), 1) if im else None,
                    "avg_remove_min": round(sum(rmm) / len(rmm), 1) if rmm else None, "machines": len({x["machine_id"] for x in rs}),
                    "unplanned": sum(1 for x in rem if x["planned"] == 0)}
        by_machine = Counter(x["machine_code"] for x in rows if x["action"] == "INSTALL")
        trend = []
        for i in range(span):
            d = d_from + timedelta(days=i)
            trend.append({"date": d.isoformat(), "installs": sum(1 for x in rows if x["action"] == "INSTALL" and x["at"].date() == d),
                          "removals": sum(1 for x in rows if x["action"] == "REMOVE" and x["at"].date() == d)})
        reasons = Counter(x["removal_reason"] or "OTHER" for x in rows if x["action"] == "REMOVE")
        unplanned = [x for x in rows if x["action"] == "REMOVE" and x["planned"] == 0]
        for u in unplanned:
            cur.execute("""SELECT MIN(installed_at) AS nxt FROM mold_installations WHERE machine_id=%s AND installed_at > %s""", (u["machine_id"], u["removed_at"]))
            nxt = cur.fetchone()["nxt"]
            u["machine_down_hours"] = round(((nxt or datetime.now()) - u["removed_at"]).total_seconds() / 3600, 1)
        last = next((x for x in rows if x["action"] == "INSTALL"), None)
        cur.execute("""SELECT i.machine_id, e.equipment_code, i.work_order_id, m.mold_code FROM mold_installations i JOIN equipment e ON e.id=i.machine_id
                       JOIN molds m ON m.id=i.mold_id WHERE i.removed_at IS NULL""")
        running = cur.fetchall()
        cur.execute("SELECT COUNT(*) AS n FROM production_plan_details WHERE status IN ('IN_PRODUCTION','ON_HOLD')")
        active_wos = cur.fetchone()["n"]
        for x in rows:
            x.pop("at", None)
        return {"period": {"from": d_from.isoformat(), "to": d_to.isoformat()}, "kpis": k(rows), "kpis_prev": k(prev), "rows": rows,
                "by_machine": by_machine.most_common(), "trend": trend, "reasons": reasons.most_common(), "unplanned": unplanned, "last": last,
                "running": {"molds": len(running), "machines": len({r["machine_id"] for r in running}), "active_work_orders": active_wos,
                            "by_machine": [{"machine": r["equipment_code"], "mold": r["mold_code"]} for r in running]},
                "system": _system_status(cur)}
    return _read(action)


@router.get("/events")
def events(mold_id: int | None = None, limit: int = 50):
    return _read(lambda cur: _timeline(cur, mold_id, limit=max(1, min(limit, 500))))


@router.post("/molds/{mold_id}/install")
def install(mold_id: int, p: InstallIn):
    return _write(lambda cur: (ms.install(cur, mold_id, p.machine_id, p.actor, p.work_order_id, p.minutes, p.remark), {"message": "Mold installed."})[1])


@router.post("/molds/{mold_id}/remove")
def remove(mold_id: int, p: RemoveIn):
    return _write(lambda cur: (ms.remove(cur, mold_id, p.reason, p.actor, p.minutes, p.remark, p.to_location_id), {"message": "Mold removed."})[1])


@router.post("/molds/{mold_id}/move")
def move(mold_id: int, p: MoveIn):
    return _write(lambda cur: (ms.move(cur, mold_id, p.location_id, p.actor, p.reason), {"message": "Mold moved."})[1])


@router.post("/molds/{mold_id}/status")
def status(mold_id: int, p: StatusIn):
    return _write(lambda cur: (ms.change_status(cur, mold_id, p.action, p.actor, p.reason, p.location_id, p.maintenance_type, p.technician, p.downtime_hours),
                               {"message": "Mold status updated."})[1])


@router.post("/molds/{mold_id}/shots")
def shots(mold_id: int, p: ShotIn):
    return _write(lambda cur: (ms.adjust_shots(cur, mold_id, p.value, p.reason, p.actor), {"message": "Shot counter adjusted."})[1])
