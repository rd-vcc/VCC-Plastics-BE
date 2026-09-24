"""Production Results (screen 1.8): what was produced, how good it was, and where it went wrong.

Two views of the same data:
- Period figures (KPIs, trend, shifts, reject reasons) come from production_execution_logs events
  inside the date range, so a Work Order running over midnight is split correctly between days.
- The result list, product summary and machine ranking are per Work Order (its totals so far),
  for every Work Order that produced or finished inside the range.
Quality summary: PPM = reject / total x 1,000,000. First-pass yield equals yield until rework is
recorded by the Quality module; rework is 0 until then.
"""

from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter

from app.core.database import get_vcc_plastics_connection
from app.modules.production.execution import service as ex
from app.modules.production.work_orders.router import _system_status
from app.modules.production.work_orders.service import WO_SELECT, decorate, load_shifts, shift_of


router = APIRouter(prefix="/api/production-results", tags=["Production Results"])


def _read(action):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        return action(cur)
    finally:
        cur.close(); conn.close()


def _pct(a, b, digits=2):
    return round(a / b * 100, digits) if b else None


@router.get("/lookups")
def lookups():
    def action(cur):
        cur.execute("""SELECT e.id, e.equipment_code, e.factory_node_id FROM equipment e JOIN equipment_types t ON t.id = e.equipment_type_id
                       WHERE t.category='MACHINE' AND e.asset_status='ACTIVE' ORDER BY e.equipment_code""")
        machines = cur.fetchall()
        cur.execute("SELECT id, product_code, product_name FROM products WHERE status='ACTIVE' ORDER BY product_code")
        products = cur.fetchall()
        cur.execute("SELECT id, customer_code, customer_name, short_name FROM customers ORDER BY customer_name")
        customers = cur.fetchall()
        shifts = [{k: s[k] for k in ("id", "shift_code", "shift_name", "start_min", "end_min")} for s in load_shifts(cur)]
        return {"machines": machines, "products": products, "customers": customers, "shifts": shifts}
    return _read(action)


def _period_logs(cur, start_dt, end_dt):
    cur.execute(
        """SELECT l.work_order_id, l.event_type, l.good_qty, l.reject_qty, l.cycle_time_sec, l.event_at, l.reason_text,
                  r.reason_code, r.reason_name
           FROM production_execution_logs l LEFT JOIN reason_codes r ON r.id = l.reason_code_id
           WHERE l.event_at >= %s AND l.event_at < %s ORDER BY l.event_at""", (start_dt, end_dt))
    return cur.fetchall()


def _totals(logs):
    good = sum(int(l["good_qty"]) for l in logs if l["event_type"] in {"OUTPUT", "REJECT", "QTY_ADJUST"})
    reject = sum(int(l["reject_qty"]) for l in logs if l["event_type"] in {"OUTPUT", "REJECT", "QTY_ADJUST"})
    cycles = [float(l["cycle_time_sec"]) for l in logs if l["event_type"] == "OUTPUT" and l["cycle_time_sec"]]
    return good, reject, (round(sum(cycles) / len(cycles), 1) if cycles else None)


@router.get("/summary")
def summary(date_from: date | None = None, date_to: date | None = None, machine_id: int | None = None, product_id: int | None = None,
            customer_id: int | None = None, shift_id: int | None = None, search: str | None = None):
    start = date_from or date.today()
    end = date_to or start

    def action(cur):
        now = datetime.now()
        start_dt = datetime.combine(start, datetime.min.time())
        end_dt = datetime.combine(end, datetime.min.time()) + timedelta(days=1)
        shifts = load_shifts(cur)
        logs = _period_logs(cur, start_dt, end_dt)
        # previous period of the same length, for "vs previous" deltas
        span = end_dt - start_dt
        prev_logs = _period_logs(cur, start_dt - span, start_dt)

        # Work Orders in scope: produced inside the range or finished inside it
        wo_ids = {l["work_order_id"] for l in logs}
        cur.execute("""SELECT id FROM production_plan_details WHERE (completed_at >= %s AND completed_at < %s)
                          OR (status='CANCELLED' AND updated_at >= %s AND updated_at < %s)""", (start_dt, end_dt, start_dt, end_dt))
        wo_ids |= {r["id"] for r in cur.fetchall()}
        rows = []
        if wo_ids:
            cur.execute(f"{WO_SELECT} WHERE d.id IN ({','.join(['%s'] * len(wo_ids))}) ORDER BY d.completed_at IS NULL, d.completed_at DESC, d.wo_no",
                        tuple(wo_ids))
            rows = cur.fetchall()
        text = (search or "").strip().lower()
        items = []
        for row in rows:
            w = decorate(row, now, shifts)
            if (machine_id and w["machine_id"] != machine_id) or (product_id and w["product_id"] != product_id):
                continue
            if customer_id and w["customer_id"] != customer_id:
                continue
            if shift_id and w["shift_id"] != shift_id:
                continue
            if text and not any(text in str(w.get(k) or "").lower() for k in ("wo_no", "order_no", "product_name", "product_code", "machine_code", "mold_code", "operator_code")):
                continue
            wlogs = ex.logs_of(cur, w["id"])
            w["metrics"] = ex.compute_metrics(w, wlogs, now)
            w["result_state"] = "COMPLETED" if w["status"] in {"COMPLETED", "CLOSED"} else "CANCELLED" if w["status"] == "CANCELLED" else "IN_PROGRESS"
            items.append(w)
        keep = {w["id"] for w in items}
        logs = [l for l in logs if l["work_order_id"] in keep] if (machine_id or product_id or customer_id or shift_id or text) else logs

        good, reject, avg_cycle = _totals(logs)
        p_good, p_reject, p_cycle = _totals(prev_logs)
        total, p_total = good + reject, p_good + p_reject
        oees = [w["metrics"]["oee_pct"] for w in items if w["metrics"]["oee_pct"] is not None]
        kpis = {
            "total": total, "good": good, "reject": reject, "yield": _pct(good, total), "scrap": _pct(reject, total),
            "oee": round(sum(oees) / len(oees), 1) if oees else None, "avg_cycle": avg_cycle,
            "prev": {"total": p_total, "good": p_good, "reject": p_reject, "yield": _pct(p_good, p_total), "scrap": _pct(p_reject, p_total), "avg_cycle": p_cycle},
        }

        # trend: at least 7 days ending at date_to
        t_start = min(start, end - timedelta(days=6))
        trend_logs = logs if t_start == start else _period_logs(cur, datetime.combine(t_start, datetime.min.time()), end_dt)
        if (machine_id or product_id or customer_id or shift_id or text) and t_start != start:
            trend_logs = [l for l in trend_logs if l["work_order_id"] in keep]
        by_day = defaultdict(lambda: [0, 0])
        for l in trend_logs:
            if l["event_type"] in {"OUTPUT", "REJECT", "QTY_ADJUST"}:
                by_day[l["event_at"].date()][0] += int(l["good_qty"])
                by_day[l["event_at"].date()][1] += int(l["reject_qty"])
        trend = []
        for i in range((end - t_start).days + 1):
            d = t_start + timedelta(days=i)
            g, r = by_day[d]
            trend.append({"date": d.isoformat(), "good": g, "reject": r, "yield": _pct(g, g + r)})

        reasons = defaultdict(int)
        for l in logs:
            if int(l["reject_qty"]) > 0:
                name = l["reason_name"] or (l["reason_text"] or "").replace("Reject:", "").strip() or "UNSPECIFIED"
                reasons[name] += int(l["reject_qty"])
        reject_reasons = sorted(reasons.items(), key=lambda x: -x[1])

        products, machines = {}, {}
        for w in items:
            p = products.setdefault(w["product_id"], {"product_id": w["product_id"], "product_code": w["product_code"], "product_name": w["product_name"],
                                                      "product_image": w["product_image"], "planned": 0, "good": 0, "reject": 0, "work_orders": 0})
            p["planned"] += int(w["planned_qty"]); p["good"] += w["good_qty"]; p["reject"] += w["reject_qty"]; p["work_orders"] += 1
            if w["machine_id"]:
                m = machines.setdefault(w["machine_id"], {"machine_id": w["machine_id"], "machine_code": w["machine_code"], "machine_name": w["machine_name"],
                                                          "model": " ".join(x for x in (w["machine_manufacturer"], w["machine_model"]) if x),
                                                          "good": 0, "reject": 0, "oee": []})
                m["good"] += w["good_qty"]; m["reject"] += w["reject_qty"]
                if w["metrics"]["oee_pct"] is not None:
                    m["oee"].append(w["metrics"]["oee_pct"])
        for p in products.values():
            p["yield"] = _pct(p["good"], p["good"] + p["reject"])
            p["completion"] = _pct(p["good"], p["planned"], 1)
        for m in machines.values():
            m["yield"] = _pct(m["good"], m["good"] + m["reject"])
            m["oee"] = round(sum(m["oee"]) / len(m["oee"]), 1) if m["oee"] else None

        # shifts: output by the shift it was produced in; planned by the shift the Work Order was planned to start
        shift_rows = {s["id"]: {"shift_id": s["id"], "shift_code": s["shift_code"], "shift_name": s["shift_name"],
                                "start": f"{s['start_min'] // 60:02d}:{s['start_min'] % 60:02d}", "end": f"{s['end_min'] // 60:02d}:{s['end_min'] % 60:02d}",
                                "planned": 0, "good": 0, "reject": 0, "oee": []} for s in shifts}
        for l in logs:
            s = shift_of(l["event_at"], shifts)
            if s and l["event_type"] in {"OUTPUT", "REJECT", "QTY_ADJUST"}:
                shift_rows[s["id"]]["good"] += int(l["good_qty"]); shift_rows[s["id"]]["reject"] += int(l["reject_qty"])
        for w in items:
            s = shift_of(w["planned_start"], shifts) if w["planned_start"] and start_dt <= w["planned_start"] < end_dt else None
            if s:
                shift_rows[s["id"]]["planned"] += int(w["planned_qty"])
            s2 = shift_of(w["started_at"], shifts) if w["started_at"] else None
            if s2 and w["metrics"]["oee_pct"] is not None:
                shift_rows[s2["id"]]["oee"].append(w["metrics"]["oee_pct"])
        for s in shift_rows.values():
            s["yield"] = _pct(s["good"], s["good"] + s["reject"])
            s["oee"] = round(sum(s["oee"]) / len(s["oee"]), 1) if s["oee"] else None

        quality = {"ppm": round(reject / total * 1_000_000) if total else None, "fpy": _pct(good, total), "rework": 0, "scrap": reject}
        return {
            "items": items, "kpis": kpis, "trend": trend, "reject_reasons": reject_reasons,
            "products": sorted(products.values(), key=lambda p: -p["good"]),
            "machines": sorted(machines.values(), key=lambda m: -(m["oee"] or 0)),
            "shifts": list(shift_rows.values()), "quality": quality, "system": _system_status(cur),
        }
    return _read(action)
