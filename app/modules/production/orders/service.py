"""Production Order helpers: lookups, derived progress, status sync and summaries."""

from datetime import date, datetime, timedelta

from fastapi import HTTPException


FINAL_STATUSES = {"COMPLETED", "CLOSED", "CANCELLED"}
AUTO_SYNC_STATUSES = {"RELEASED", "SCHEDULING", "IN_PRODUCTION"}
STATUS_ORDER = ["DRAFT", "RELEASED", "SCHEDULING", "IN_PRODUCTION", "COMPLETED", "CLOSED", "CANCELLED"]
PRIORITY_ORDER = ["URGENT", "HIGH", "MEDIUM", "LOW"]
PROGRESS_STATES = ["ON_TRACK", "AT_RISK", "DELAY", "COMPLETED", "NOT_STARTED", "CANCELLED"]

ORDER_SELECT = """
    SELECT o.*, c.customer_code, c.customer_name, c.short_name AS customer_short_name,
           p.product_code, p.product_name, n.name AS factory_node_name,
           COALESCE(w.good_qty, 0) AS good_qty, COALESCE(w.reject_qty, 0) AS reject_qty,
           COALESCE(w.wo_count, 0) AS wo_count, COALESCE(w.wo_planned_qty, 0) AS wo_planned_qty,
           w.wo_start, w.wo_end, w.machine_ids
    FROM production_orders o
    JOIN products p ON p.id = o.product_id
    LEFT JOIN customers c ON c.id = o.customer_id
    LEFT JOIN factory_structure_nodes n ON n.id = o.factory_node_id
    LEFT JOIN (
        SELECT production_order_id, SUM(actual_qty) AS good_qty, SUM(reject_qty) AS reject_qty,
               COUNT(*) AS wo_count, SUM(planned_qty) AS wo_planned_qty,
               MIN(planned_start) AS wo_start, MAX(planned_end) AS wo_end,
               GROUP_CONCAT(DISTINCT machine_id) AS machine_ids
        FROM production_plan_details
        WHERE production_order_id IS NOT NULL AND status <> 'CANCELLED'
        GROUP BY production_order_id
    ) w ON w.production_order_id = o.id
"""


def get_order(cur, order_id: int):
    cur.execute(f"{ORDER_SELECT} WHERE o.id=%s", (order_id,))
    row = cur.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Production order not found.")
    return row


def check_duplicate_order_no(cur, value: str, exclude_id: int | None = None):
    sql = "SELECT id FROM production_orders WHERE order_no=%s"
    params = [value]
    if exclude_id is not None:
        sql += " AND id<>%s"
        params.append(exclude_id)
    cur.execute(sql, tuple(params))
    if cur.fetchone():
        raise HTTPException(status_code=409, detail="Production order number already exists.")


def next_order_no(cur, day: date | None = None) -> str:
    prefix = f"PO-{(day or date.today()):%y%m%d}-"
    cur.execute("SELECT order_no FROM production_orders WHERE order_no LIKE %s ORDER BY order_no DESC LIMIT 1", (f"{prefix}%",))
    row = cur.fetchone()
    tail = row["order_no"][len(prefix):] if row else ""
    return f"{prefix}{(int(tail) + 1 if tail.isdigit() else 1):03d}"


def write_history(cur, order_id: int, action: str, from_status, to_status, actor, remark=None):
    cur.execute(
        """INSERT INTO production_order_status_history
           (production_order_id, action, from_status, to_status, actor, remark)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (order_id, action, from_status, to_status, actor or "SYSTEM", remark),
    )


def sync_order_status(cur, order_id, actor="SYSTEM"):
    """Move an in-flight order along its lifecycle from the state of its Work Orders.

    RELEASED -> SCHEDULING when a Work Order is scheduled or released to the shop floor,
    -> IN_PRODUCTION only once a Work Order has actually started (or reported output),
    -> COMPLETED when good pieces reach the order quantity. Draft / final orders are never touched here.
    """
    if not order_id:
        return
    cur.execute("SELECT id, status, order_qty FROM production_orders WHERE id=%s FOR UPDATE", (order_id,))
    order = cur.fetchone()
    if not order or order["status"] not in AUTO_SYNC_STATUSES:
        return
    cur.execute(
        """SELECT status, actual_qty FROM production_plan_details
           WHERE production_order_id=%s AND status<>'CANCELLED'""",
        (order_id,),
    )
    work_orders = cur.fetchall()
    good = sum(int(w["actual_qty"] or 0) for w in work_orders)
    statuses = {w["status"] for w in work_orders}
    if good >= int(order["order_qty"]):
        target = "COMPLETED"
    elif statuses & {"IN_PRODUCTION", "ON_HOLD", "COMPLETED", "CLOSED"} or good > 0:
        target = "IN_PRODUCTION"
    elif statuses & {"SCHEDULED", "RELEASED"}:
        target = "SCHEDULING"
    else:
        target = "RELEASED"
    if target == order["status"]:
        return
    extra = ", completed_at=NOW()" if target == "COMPLETED" else ""
    cur.execute(f"UPDATE production_orders SET status=%s{extra}, version=version+1 WHERE id=%s", (target, order_id))
    write_history(cur, order_id, "AUTO_SYNC", order["status"], target, actor, "Updated from work order status")


def progress_state(order, now: datetime) -> str:
    status = order["status"]
    if status == "CANCELLED":
        return "CANCELLED"
    qty, good = int(order["order_qty"]), int(order["good_qty"] or 0)
    if status in {"COMPLETED", "CLOSED"} or good >= qty:
        return "COMPLETED"
    start, end, due = order["planned_start"], order["planned_end"], order["due_date"]
    if status == "DRAFT" or not start or not end or now < start:
        state = "NOT_STARTED"
    elif now >= end:
        return "DELAY"
    else:
        expected = qty * (now - start).total_seconds() / max((end - start).total_seconds(), 1)
        ratio = good / expected if expected >= 1 else 1
        state = "ON_TRACK" if ratio >= 0.9 else "AT_RISK" if ratio >= 0.75 else "DELAY"
    if state in {"NOT_STARTED", "ON_TRACK"} and due and end and end > due:
        return "AT_RISK"
    if state == "NOT_STARTED" and due and now > due:
        return "DELAY"
    return state


def decorate(order, now: datetime):
    qty = int(order["order_qty"])
    good = int(order["good_qty"] or 0)
    machine_ids = [int(x) for x in str(order.get("machine_ids") or "").split(",") if x]
    return {
        **order,
        "good_qty": good,
        "reject_qty": int(order["reject_qty"] or 0),
        "wo_planned_qty": int(order["wo_planned_qty"] or 0),
        "unplanned_qty": max(qty - int(order["wo_planned_qty"] or 0), 0),
        "progress_pct": round(min(good / qty * 100, 100), 1) if qty else 0.0,
        "progress_state": progress_state(order, now),
        "machine_ids": machine_ids,
    }


def summarize(rows, now: datetime):
    def pct(value, total):
        return round(value / total * 100, 1) if total else 0.0

    total = len(rows)
    states = {s: 0 for s in PROGRESS_STATES}
    for row in rows:
        states[row["progress_state"]] += 1
    by_status = [{"status": s, "count": sum(r["status"] == s for r in rows)} for s in STATUS_ORDER]
    by_priority = [{"priority": p, "count": sum(r["priority"] == p for r in rows)} for p in PRIORITY_ORDER]

    products = {}
    for row in rows:
        entry = products.setdefault(row["product_id"], {"product_code": row["product_code"], "product_name": row["product_name"], "order_qty": 0, "count": 0})
        entry["order_qty"] += int(row["order_qty"]); entry["count"] += 1
    top_products = sorted(products.values(), key=lambda e: -e["order_qty"])[:5]

    customers = {}
    for row in rows:
        key = row["customer_id"] or 0
        entry = customers.setdefault(key, {"customer_id": row["customer_id"], "customer_name": row["customer_short_name"] or row["customer_name"] or "—", "count": 0, "order_qty": 0})
        entry["count"] += 1; entry["order_qty"] += int(row["order_qty"])
    by_customer = sorted(customers.values(), key=lambda e: -e["count"])

    today = now.date()
    days = [today - timedelta(days=i) for i in range(13, -1, -1)]
    created = {d: 0 for d in days}
    completed = {d: 0 for d in days}
    for row in rows:
        if row["created_at"] and row["created_at"].date() in created:
            created[row["created_at"].date()] += 1
        if row["completed_at"] and row["completed_at"].date() in completed:
            completed[row["completed_at"].date()] += 1

    return {
        "kpis": {
            "total": total,
            "on_track": states["ON_TRACK"], "on_track_pct": pct(states["ON_TRACK"], total),
            "at_risk": states["AT_RISK"], "at_risk_pct": pct(states["AT_RISK"], total),
            "delay": states["DELAY"], "delay_pct": pct(states["DELAY"], total),
            "completed": states["COMPLETED"], "completed_pct": pct(states["COMPLETED"], total),
            "not_started": states["NOT_STARTED"], "cancelled": states["CANCELLED"],
            "good_qty": sum(r["good_qty"] for r in rows), "reject_qty": sum(r["reject_qty"] for r in rows),
            "order_qty": sum(int(r["order_qty"]) for r in rows),
            "created_today": created[today], "completed_today": completed[today],
        },
        "progress_states": states,
        "by_status": by_status,
        "by_priority": by_priority,
        "top_products": top_products,
        "by_customer": by_customer,
        "trend": [{"date": d.isoformat(), "created": created[d], "completed": completed[d]} for d in days],
    }
