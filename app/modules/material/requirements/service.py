"""Material requirement (data standard 3.5) and order material assignment (4.6).

Requirement lines are derived from the product's BOM and kept in sync with the
Work Order: ``sync_work_order`` is called by Planning after every Work Order
change. Stock always comes from the inventory provider (``available_by_material``).
"""

from datetime import date, datetime, timedelta

from fastapi import HTTPException

from app.modules.master_data.products.bom_service import get_bom_items, requirement_from_items
from app.modules.material.inventory.service import available_by_material, load_settings, material_category, safety_stocks


OPEN_WO_STATUSES = ("UNSCHEDULED", "SCHEDULED", "RELEASED", "IN_PRODUCTION", "ON_HOLD")
LOCKED_LINE_STATUSES = {"RESERVED", "READY"}


def _active_bom(cur, product_id):
    cur.execute("SELECT id, bom_revision, base_qty FROM boms WHERE product_id=%s AND status='ACTIVE' LIMIT 1", (product_id,))
    return cur.fetchone()


def _bom_rows(cur, bom_id, qty):
    cur.execute("SELECT base_qty FROM boms WHERE id=%s", (bom_id,))
    bom = cur.fetchone()
    return requirement_from_items(get_bom_items(cur, bom_id), bom["base_qty"], qty) if bom else []


# =========================================================
# SYNC (Work Order -> requirement lines, Order -> assignment lines)
# =========================================================


def sync_work_order(cur, wo_id, actor="SYSTEM"):
    if not wo_id:
        return
    cur.execute("SELECT id, product_id, planned_qty, actual_qty, status, planned_start, due_date FROM production_plan_details WHERE id=%s", (wo_id,))
    wo = cur.fetchone()
    if not wo:
        return
    cur.execute("SELECT * FROM material_requirements WHERE work_order_id=%s", (wo_id,))
    existing = {r["material_id"]: r for r in cur.fetchall()}

    if wo["status"] not in OPEN_WO_STATUSES:
        final = "CANCELLED" if wo["status"] == "CANCELLED" else None
        for line in existing.values():
            if final:
                cur.execute("UPDATE material_requirements SET status='CANCELLED', open_qty=0, updated_by=%s WHERE id=%s", (actor, line["id"]))
            else:
                cur.execute("UPDATE material_requirements SET open_qty=0, updated_by=%s WHERE id=%s", (actor, line["id"]))
        return

    # a BOM revision already used by reserved lines stays; otherwise follow the ACTIVE BOM
    locked = next((l for l in existing.values() if l["status"] in LOCKED_LINE_STATUSES and l["bom_id"]), None)
    bom = {"id": locked["bom_id"], "bom_revision": locked["bom_revision"]} if locked else _active_bom(cur, wo["product_id"])
    planned = int(wo["planned_qty"])
    remaining = max(planned - int(wo["actual_qty"] or 0), 0)
    required_date = wo["planned_start"] or wo["due_date"]
    wanted = {}
    if bom:
        full = {r["material_id"]: r for r in _bom_rows(cur, bom["id"], planned)}
        open_rows = {r["material_id"]: r for r in _bom_rows(cur, bom["id"], remaining)} if remaining else {}
        for mid, row in full.items():
            wanted[mid] = (row["required_qty"], open_rows.get(mid, {}).get("required_qty", 0.0))
    for mid, (required, open_qty) in wanted.items():
        cur.execute(
            """INSERT INTO material_requirements (work_order_id, material_id, bom_id, bom_revision, required_qty, open_qty, required_date, status, updated_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,'PLANNED',%s)
               ON DUPLICATE KEY UPDATE bom_id=VALUES(bom_id), bom_revision=VALUES(bom_revision), required_qty=VALUES(required_qty),
                 open_qty=VALUES(open_qty), required_date=VALUES(required_date),
                 status=IF(status='CANCELLED','PLANNED',status), updated_by=VALUES(updated_by)""",
            (wo_id, mid, bom["id"], bom["bom_revision"], required, open_qty, required_date, actor))
    for mid, line in existing.items():
        if mid not in wanted and line["status"] != "CANCELLED":
            remark = "No active BOM for the product" if not bom else "Material removed from BOM"
            cur.execute("UPDATE material_requirements SET status='CANCELLED', open_qty=0, remark=%s, updated_by=%s WHERE id=%s", (remark, actor, line["id"]))
    refresh_statuses(cur)


def sync_all(cur, actor="SYSTEM"):
    cur.execute(f"SELECT id FROM production_plan_details WHERE status IN {OPEN_WO_STATUSES}")
    ids = [r["id"] for r in cur.fetchall()]
    cur.execute("SELECT DISTINCT work_order_id AS id FROM material_requirements WHERE status<>'CANCELLED'")
    ids += [r["id"] for r in cur.fetchall()]
    for wo_id in sorted(set(ids)):
        sync_work_order(cur, wo_id, actor)
    refresh_statuses(cur)
    return len(set(ids))


def assign_order_materials(cur, order_id, actor="SYSTEM", require_bom=True):
    """Create / refresh the order's material assignment (4.6) from its product's active BOM."""
    cur.execute("SELECT id, product_id, order_qty, bom_id, status FROM production_orders WHERE id=%s", (order_id,))
    order = cur.fetchone()
    if not order:
        return
    cur.execute("SELECT * FROM production_order_materials WHERE production_order_id=%s", (order_id,))
    existing = {r["material_id"]: r for r in cur.fetchall()}
    locked = any(r["status"] in LOCKED_LINE_STATUSES for r in existing.values())
    bom = {"id": order["bom_id"]} if (locked and order["bom_id"]) else _active_bom(cur, order["product_id"])
    if not bom:
        if require_bom:
            raise HTTPException(status_code=409, detail="The product has no active BOM. Activate a BOM in Product Master before releasing the order.")
        return
    cur.execute("SELECT bom_revision FROM boms WHERE id=%s", (bom["id"],))
    revision = cur.fetchone()["bom_revision"]
    rows = {r["material_id"]: r for r in _bom_rows(cur, bom["id"], int(order["order_qty"]))}
    for mid, row in rows.items():
        cur.execute(
            """INSERT INTO production_order_materials (production_order_id, material_id, bom_id, bom_revision, required_qty, unit, status, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,'ASSIGNED',%s)
               ON DUPLICATE KEY UPDATE bom_id=VALUES(bom_id), bom_revision=VALUES(bom_revision), required_qty=VALUES(required_qty),
                 unit=VALUES(unit), status=IF(status='CANCELLED','ASSIGNED',status)""",
            (order_id, mid, bom["id"], revision, row["required_qty"], row["unit"], actor))
    for mid, line in existing.items():
        if mid not in rows and line["status"] not in LOCKED_LINE_STATUSES:
            cur.execute("UPDATE production_order_materials SET status='CANCELLED', remark='Material removed from BOM' WHERE id=%s", (line["id"],))
    cur.execute("UPDATE production_orders SET bom_id=%s, bom_revision=%s WHERE id=%s", (bom["id"], revision, order_id))


def cancel_order_materials(cur, order_id):
    cur.execute("UPDATE production_order_materials SET status='CANCELLED' WHERE production_order_id=%s AND status<>'CANCELLED'", (order_id,))


# =========================================================
# COVERAGE (walk open lines in date order against available stock)
# =========================================================

LINE_SELECT = """
    SELECT r.*, d.wo_no, d.status AS wo_status, d.priority, d.planned_start, d.due_date, d.product_id,
           COALESCE((SELECT SUM(a.reserved_qty + a.issued_qty - a.returned_qty - a.consumed_qty) FROM material_allocations a
                     WHERE a.requirement_id = r.id AND a.status <> 'CANCELLED'), 0) AS allocated_qty,
           COALESCE((SELECT SUM(a.issued_qty - a.returned_qty - a.consumed_qty) FROM material_allocations a
                     WHERE a.requirement_id = r.id AND a.status <> 'CANCELLED'), 0) AS issued_qty,
           p.product_code, p.product_name, po.order_no,
           m.material_code, m.material_name, m.material_type, m.material_group, m.unit
    FROM material_requirements r
    JOIN production_plan_details d ON d.id = r.work_order_id
    JOIN products p ON p.id = d.product_id
    JOIN materials m ON m.id = r.material_id
    LEFT JOIN production_orders po ON po.id = d.production_order_id
"""


def open_lines(cur):
    cur.execute(f"{LINE_SELECT} WHERE r.status<>'CANCELLED' AND d.status IN {OPEN_WO_STATUSES}")
    lines = []
    for line in cur.fetchall():
        need = max(float(line["open_qty"]) + float(line["adjustment_qty"]), 0.0)
        allocated = float(line["allocated_qty"])
        lines.append({**line, "required_qty": float(line["required_qty"]), "open_qty": float(line["open_qty"]),
                      "adjustment_qty": float(line["adjustment_qty"]), "need_qty": round(need, 4),
                      "allocated_qty": round(allocated, 4), "issued_qty": round(float(line["issued_qty"]), 4),
                      "unallocated_qty": round(max(need - allocated, 0.0), 4),
                      "category": material_category(line["material_type"], line["material_group"], line["material_code"])})
    lines.sort(key=lambda l: (l["required_date"] is None, l["required_date"] or datetime.max, l["wo_no"]))
    return lines


def walk_coverage(lines, stock):
    """Annotate each line with covered_qty / short_qty consuming ``stock`` (material -> available) in order.

    Quantities already allocated to the line are covered (reserved stock is not in ``stock``).
    """
    left = {mid: float(v) for mid, v in stock.items()}
    for line in lines:
        allocated = min(line.get("allocated_qty", 0.0), line["need_qty"])
        wanted = line["need_qty"] - allocated
        have = max(left.get(line["material_id"], 0.0), 0.0)
        from_stock = min(have, wanted)
        left[line["material_id"]] = have - from_stock
        line["covered_qty"] = round(allocated + from_stock, 4)
        line["short_qty"] = round(line["need_qty"] - allocated - from_stock, 4)
    return left


def refresh_statuses(cur):
    lines = open_lines(cur)
    stock = {mid: b["available"] for mid, b in available_by_material(cur, list({l["material_id"] for l in lines})).items()} if lines else {}
    walk_coverage(lines, stock)
    for line in lines:
        need = line["need_qty"]
        tol = 1e-3  # allocations are stored with 3 decimals
        if need > tol and line["issued_qty"] >= need - tol:
            status = "READY"
        elif need > tol and line["allocated_qty"] >= need - tol:
            status = "RESERVED"
        else:
            status = "SHORTAGE" if line["short_qty"] > tol else "PLANNED"
        if status != line["status"]:
            cur.execute("UPDATE material_requirements SET status=%s WHERE id=%s", (status, line["id"]))


# =========================================================
# SUMMARY (screen 4.2)
# =========================================================


def _week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def build_summary(cur, date_from: date, date_to: date, category=None, material_id=None, work_order_id=None,
                  priority=None, include_unscheduled=True):
    settings = load_settings(cur)
    now = datetime.now()
    lines = open_lines(cur)
    material_ids = list({l["material_id"] for l in lines})
    buckets = available_by_material(cur, material_ids) if material_ids else {}
    stock = {mid: b["available"] for mid, b in buckets.items()}
    walk_coverage(lines, stock)
    safety = safety_stocks(cur)
    start_dt = datetime.combine(date_from, datetime.min.time())
    end_dt = datetime.combine(date_to, datetime.min.time()) + timedelta(days=1)

    def in_period(line):
        if line["required_date"] is None:
            return include_unscheduled
        return start_dt <= line["required_date"] < end_dt

    def matches(line):
        return ((not category or line["category"] == category) and (not material_id or line["material_id"] == material_id)
                and (not work_order_id or line["work_order_id"] == work_order_id))

    # stock left for the period = available minus needs dated before it
    before = {}
    for line in lines:
        if line["required_date"] is not None and line["required_date"] < start_dt:
            before[line["material_id"]] = before.get(line["material_id"], 0.0) + line["unallocated_qty"]
    period_lines = [l for l in lines if in_period(l) and matches(l)]

    rows = {}
    for line in period_lines:
        row = rows.setdefault(line["material_id"], {
            "material_id": line["material_id"], "material_code": line["material_code"], "material_name": line["material_name"],
            "category": line["category"], "unit": line["unit"], "planned_qty": 0.0, "adjustment_qty": 0.0, "total_qty": 0.0, "allocated_qty": 0.0,
            "earliest_date": line["required_date"], "work_orders": set()})
        row["planned_qty"] += line["open_qty"]
        row["adjustment_qty"] += line["adjustment_qty"]
        row["total_qty"] += line["need_qty"]
        row["allocated_qty"] += min(line["allocated_qty"], line["need_qty"])
        row["work_orders"].add(line["wo_no"])
        if line["required_date"] and (row["earliest_date"] is None or line["required_date"] < row["earliest_date"]):
            row["earliest_date"] = line["required_date"]

    critical_pct = settings["material.critical_coverage_pct"]
    critical_hours = settings["material.critical_hours"]
    result_rows = []
    for mid, row in rows.items():
        available = max(stock.get(mid, 0.0) - before.get(mid, 0.0), 0.0)
        total = row["total_qty"]
        allocated = row["allocated_qty"]
        diff = available - (total - allocated)
        coverage = (available + allocated) / total * 100 if total > 0 else 100.0
        soon = row["earliest_date"] is not None and row["earliest_date"] - now <= timedelta(hours=critical_hours)
        if diff < -1e-6:
            state = "CRITICAL" if coverage < critical_pct or soon else "SHORT"
        elif diff > max(total, safety.get(mid, 0.0)):
            state = "EXCESS"
        else:
            state = "ENOUGH"
        if state == "CRITICAL":
            prio = "HIGH"
        elif state == "SHORT":
            prio = "HIGH" if row["earliest_date"] is not None and row["earliest_date"] - now <= timedelta(hours=72) else "MEDIUM"
        else:
            prio = "LOW"
        bucket = buckets.get(mid, {})
        result_rows.append({**row, "work_orders": sorted(row["work_orders"]), "planned_qty": round(row["planned_qty"], 3),
                            "adjustment_qty": round(row["adjustment_qty"], 3), "total_qty": round(total, 3), "allocated_qty": round(allocated, 3),
                            "available_qty": round(available, 3), "difference_qty": round(diff, 3), "coverage_pct": round(coverage, 1),
                            "waiting_iqc_qty": bucket.get("waiting_iqc", 0.0), "hold_qty": bucket.get("hold", 0.0),
                            "safety_stock_qty": safety.get(mid, 0.0), "status": state, "priority": prio})
    if priority:
        result_rows = [r for r in result_rows if r["priority"] == priority.upper()]
    order = {"CRITICAL": 0, "SHORT": 1, "ENOUGH": 2, "EXCESS": 3}
    result_rows.sort(key=lambda r: (order[r["status"]], r["difference_qty"]))
    keep = {r["material_id"] for r in result_rows}
    kg = lambda r: str(r["unit"] or "").upper() == "KG"  # noqa: E731

    # weekly trend (5 weeks from the period start): per material walk, KG materials only
    weeks = [_week_start(date_from) + timedelta(weeks=i) for i in range(5)]
    trend = []
    left = {mid: max(stock.get(mid, 0.0) - before.get(mid, 0.0), 0.0) for mid in keep}
    for week in weeks:
        w_start = datetime.combine(week, datetime.min.time())
        w_end = w_start + timedelta(days=7)
        need_week, short_week = 0.0, 0.0
        for line in lines:
            if line["material_id"] not in keep or not matches(line) or str(line["unit"] or "").upper() != "KG":
                continue
            if line["required_date"] is None or not (w_start <= line["required_date"] < w_end):
                continue
            have = left.get(line["material_id"], 0.0)
            take = min(have, line["unallocated_qty"])
            left[line["material_id"]] = have - take
            need_week += line["need_qty"]
            short_week += line["unallocated_qty"] - take
        excess = sum(v for mid, v in left.items() if any(r["material_id"] == mid and kg(r) for r in result_rows))
        trend.append({"week_start": week.isoformat(), "week_no": week.isocalendar()[1], "requirement": round(need_week, 3),
                      "shortage": round(short_week, 3), "excess": round(excess, 3)})

    distribution = {}
    for r in result_rows:
        if kg(r):
            distribution[r["category"]] = round(distribution.get(r["category"], 0.0) + r["total_qty"], 3)

    # work orders at risk: lines in the period that the date-ordered walk could not cover
    risk = {}
    for line in period_lines:
        if line["material_id"] not in keep or line["short_qty"] <= 1e-6:
            continue
        entry = risk.setdefault(line["work_order_id"], {"work_order_id": line["work_order_id"], "wo_no": line["wo_no"], "product_name": line["product_name"],
                                                         "order_no": line["order_no"], "planned_start": line["planned_start"], "required_date": line["required_date"],
                                                         "materials": [], "worst_coverage": 100.0})
        coverage = line["covered_qty"] / line["need_qty"] * 100 if line["need_qty"] else 100.0
        entry["materials"].append(line["material_code"])
        entry["worst_coverage"] = min(entry["worst_coverage"], round(coverage, 1))
    for entry in risk.values():
        when = entry["required_date"]
        hours = (when - now).total_seconds() / 3600 if when else None
        entry["risk"] = "HIGH" if (hours is not None and hours <= critical_hours) or entry["worst_coverage"] < critical_pct else "MEDIUM" if hours is not None and hours <= 72 else "LOW"
    risk_rows = sorted(risk.values(), key=lambda e: ({"HIGH": 0, "MEDIUM": 1, "LOW": 2}[e["risk"]], e["required_date"] or datetime.max))

    short_rows = [r for r in result_rows if r["status"] in {"SHORT", "CRITICAL"}]
    purchase = []
    for r in short_rows:
        suggested = max(-r["difference_qty"] + r["safety_stock_qty"] - r["waiting_iqc_qty"], 0.0)
        if suggested > 0:
            purchase.append({"material_id": r["material_id"], "material_code": r["material_code"], "material_name": r["material_name"],
                             "unit": r["unit"], "shortage_qty": round(-r["difference_qty"], 3), "safety_stock_qty": r["safety_stock_qty"],
                             "waiting_iqc_qty": r["waiting_iqc_qty"], "suggested_qty": round(suggested, 3), "needed_by": r["earliest_date"]})
    release_candidates = [r["material_code"] for r in short_rows if (r["waiting_iqc_qty"] + r["hold_qty"]) > 0]

    return {
        "period": {"date_from": date_from, "date_to": date_to, "include_unscheduled": include_unscheduled},
        "settings": settings,
        "kpis": {
            "total_requirement": round(sum(r["total_qty"] for r in result_rows if kg(r)), 3),
            "available": round(sum(r["available_qty"] for r in result_rows if kg(r)), 3),
            "shortage": round(sum(-r["difference_qty"] for r in short_rows if kg(r)), 3),
            "short_count": sum(1 for r in result_rows if r["status"] == "SHORT"),
            "critical_count": sum(1 for r in result_rows if r["status"] == "CRITICAL"),
            "purchase_count": len(purchase), "material_count": len(result_rows), "line_count": len(period_lines),
        },
        "rows": result_rows,
        "trend": trend,
        "distribution": distribution,
        "critical": sorted(short_rows, key=lambda r: r["difference_qty"])[:10],
        "work_order_risk": risk_rows,
        "purchase_suggestions": purchase,
        "actions": {"purchase": len(purchase), "adjust_plan": len(risk_rows), "release_stock": len(release_candidates),
                    "release_materials": release_candidates},
        "server_time": now,
    }
