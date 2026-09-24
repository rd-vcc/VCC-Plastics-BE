from datetime import date, datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import ValidationError

from app.core.database import get_vcc_plastics_connection
from app.modules.production.orders.schemas import (
    CopyOrder,
    ImportRequest,
    ImportRow,
    NoteCreate,
    OrderAction,
    OrderCreate,
    OrderUpdate,
    PriorityChange,
)
from app.modules.material.requirements.service import assign_order_materials, cancel_order_materials, sync_work_order
from app.modules.production.orders.record import build_record
from app.modules.production.orders.service import (
    FINAL_STATUSES,
    ORDER_SELECT,
    check_duplicate_order_no,
    decorate,
    get_order,
    next_order_no,
    summarize,
    write_history,
)


router = APIRouter(prefix="/api/production-orders", tags=["Production Orders"])

STALE = "This record was changed by another user. Reload and try again."


def _run_write(action):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        result = action(cur)
        conn.commit()
        return result
    except HTTPException:
        conn.rollback()
        raise
    except Exception as exc:
        conn.rollback()
        message = str(exc)
        if "Duplicate" in message:
            raise HTTPException(status_code=409, detail="Duplicate data.") from exc
        if "foreign key constraint fails" in message.lower():
            raise HTTPException(status_code=409, detail="Data is referenced or reference is invalid.") from exc
        raise HTTPException(status_code=400, detail=message) from exc
    finally:
        cur.close()
        conn.close()


def _read(action):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        return action(cur)
    finally:
        cur.close(); conn.close()


def _check_references(cur, payload):
    cur.execute("SELECT status FROM products WHERE id=%s", (payload.product_id,))
    product = cur.fetchone()
    if not product or product["status"] != "ACTIVE":
        raise HTTPException(status_code=400, detail="Product is missing or inactive.")
    if payload.customer_id:
        cur.execute("SELECT status FROM customers WHERE id=%s", (payload.customer_id,))
        customer = cur.fetchone()
        if not customer or customer["status"] != "ACTIVE":
            raise HTTPException(status_code=400, detail="Customer is missing or inactive.")


def _insert_order(cur, payload: OrderCreate):
    cur.execute(
        """INSERT INTO production_orders
           (order_no, customer_id, product_id, order_qty, priority, planned_start, planned_end, due_date,
            factory_node_id, source, source_ref, remark, status, created_by)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'DRAFT',%s)""",
        (payload.order_no, payload.customer_id, payload.product_id, payload.order_qty, payload.priority,
         payload.planned_start, payload.planned_end, payload.due_date, payload.factory_node_id, payload.source,
         payload.source_ref, payload.remark, payload.created_by),
    )
    order_id = cur.lastrowid
    write_history(cur, order_id, "CREATE", None, "DRAFT", payload.created_by)
    return order_id


# =========================================================
# LOOKUPS
# =========================================================


@router.get("/lookups")
def lookups():
    def action(cur):
        cur.execute("SELECT id, customer_code, customer_name, short_name FROM customers WHERE status='ACTIVE' ORDER BY customer_code")
        customers = cur.fetchall()
        cur.execute("SELECT id, product_code, product_name FROM products WHERE status='ACTIVE' ORDER BY product_code")
        products = cur.fetchall()
        cur.execute(
            """SELECT n.id, n.code, n.name, n.parent_id, t.code AS node_type
               FROM factory_structure_nodes n LEFT JOIN factory_node_types t ON t.id = n.node_type_id
               WHERE n.status='ACTIVE' ORDER BY n.level_no, n.sort_order, n.name""")
        nodes = cur.fetchall()
        return {"customers": customers, "products": products, "factory_nodes": nodes}
    return _read(action)


@router.get("/next-no")
def get_next_order_no(day: date | None = None):
    return _read(lambda cur: {"order_no": next_order_no(cur, day)})


# =========================================================
# LIST + SUMMARY
# =========================================================


@router.get("/orders")
def list_orders(
    keyword: str | None = None, status: str | None = None, priority: str | None = None,
    customer_id: int | None = None, product_id: int | None = None, factory_node_id: int | None = None,
    source: str | None = None, date_from: date | None = None, date_to: date | None = None,
    due_from: date | None = None, due_to: date | None = None,
    limit: int = Query(default=2000, ge=1, le=5000),
):
    def action(cur):
        conditions, params = [], []
        if keyword:
            conditions.append("(o.order_no LIKE %s OR o.source_ref LIKE %s OR p.product_code LIKE %s OR p.product_name LIKE %s OR c.customer_name LIKE %s OR c.customer_code LIKE %s)")
            value = f"%{keyword.strip()}%"; params += [value] * 6
        for field, raw in (("o.status", status), ("o.priority", priority), ("o.source", source)):
            if raw:
                values = [v.strip().upper() for v in raw.split(",") if v.strip()]
                conditions.append(f"{field} IN ({','.join(['%s'] * len(values))})"); params += values
        for field, value in (("o.customer_id", customer_id), ("o.product_id", product_id), ("o.factory_node_id", factory_node_id)):
            if value is not None:
                conditions.append(f"{field}=%s"); params.append(value)
        if date_from:
            conditions.append("(o.planned_end IS NULL OR o.planned_end >= %s)"); params.append(date_from)
        if date_to:
            conditions.append("(o.planned_start IS NULL OR o.planned_start < DATE_ADD(%s, INTERVAL 1 DAY))"); params.append(date_to)
        if due_from:
            conditions.append("o.due_date >= %s"); params.append(due_from)
        if due_to:
            conditions.append("o.due_date < DATE_ADD(%s, INTERVAL 1 DAY)"); params.append(due_to)
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        cur.execute(f"{ORDER_SELECT}{where} ORDER BY o.planned_start IS NULL, o.planned_start DESC, o.order_no DESC LIMIT %s",
                    tuple(params + [limit]))
        now = datetime.now()
        rows = [decorate(r, now) for r in cur.fetchall()]
        return {"items": rows, "summary": summarize(rows, now), "server_time": now}
    return _read(action)


@router.get("/orders/{order_id}")
def order_detail(order_id: int):
    def action(cur):
        order = decorate(get_order(cur, order_id), datetime.now())
        cur.execute(
            """SELECT d.id, d.wo_no, d.status, d.planned_qty, d.actual_qty, d.reject_qty, d.planned_start, d.planned_end,
                      d.machine_id, e.equipment_code AS machine_code, mo.mold_code, pl.plan_code, pl.status AS plan_status
               FROM production_plan_details d
               LEFT JOIN equipment e ON e.id = d.machine_id
               LEFT JOIN molds mo ON mo.id = d.mold_id
               LEFT JOIN production_plans pl ON pl.id = d.plan_id
               WHERE d.production_order_id=%s ORDER BY d.planned_start IS NULL, d.planned_start, d.wo_no""",
            (order_id,),
        )
        work_orders = cur.fetchall()
        cur.execute(
            "SELECT * FROM production_order_status_history WHERE production_order_id=%s ORDER BY acted_at DESC, id DESC",
            (order_id,))
        return {**order, "work_orders": work_orders, "history": cur.fetchall()}
    return _read(action)


# =========================================================
# CREATE / UPDATE / COPY / IMPORT / DELETE
# =========================================================


@router.post("/orders", status_code=201)
def create_order(payload: OrderCreate):
    def action(cur):
        check_duplicate_order_no(cur, payload.order_no)
        _check_references(cur, payload)
        return {"message": "Production order created.", "id": _insert_order(cur, payload)}
    return _run_write(action)


@router.put("/orders/{order_id}")
def update_order(order_id: int, payload: OrderUpdate):
    def action(cur):
        order = get_order(cur, order_id)
        if order["status"] in FINAL_STATUSES:
            raise HTTPException(status_code=409, detail="Completed, closed or cancelled orders cannot be edited.")
        if order["status"] != "DRAFT":
            if payload.product_id != order["product_id"]:
                raise HTTPException(status_code=409, detail="The product can only be changed while the order is Draft.")
            if payload.order_qty < int(order["good_qty"] or 0):
                raise HTTPException(status_code=409, detail="Order quantity cannot be lower than the good pieces already produced.")
        check_duplicate_order_no(cur, payload.order_no, order_id)
        _check_references(cur, payload)
        cur.execute(
            """UPDATE production_orders SET order_no=%s, customer_id=%s, product_id=%s, order_qty=%s, priority=%s,
               planned_start=%s, planned_end=%s, due_date=%s, factory_node_id=%s, source=%s, source_ref=%s, remark=%s,
               updated_by=%s, version=version+1 WHERE id=%s AND version=%s""",
            (payload.order_no, payload.customer_id, payload.product_id, payload.order_qty, payload.priority,
             payload.planned_start, payload.planned_end, payload.due_date, payload.factory_node_id, payload.source,
             payload.source_ref, payload.remark, payload.updated_by, order_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail=STALE)
        write_history(cur, order_id, "UPDATE", order["status"], order["status"], payload.updated_by)
        if order["status"] != "DRAFT":
            assign_order_materials(cur, order_id, payload.updated_by or "SYSTEM", require_bom=False)
        return {"message": "Production order updated.", "version": payload.version + 1}
    return _run_write(action)


@router.post("/orders/{order_id}/copy", status_code=201)
def copy_order(order_id: int, payload: CopyOrder):
    def action(cur):
        source = get_order(cur, order_id)
        new_no = (payload.new_order_no or "").strip().upper() or next_order_no(cur)
        check_duplicate_order_no(cur, new_no)
        copy = OrderCreate(
            order_no=new_no, customer_id=source["customer_id"], product_id=source["product_id"],
            order_qty=source["order_qty"], priority=source["priority"], planned_start=source["planned_start"],
            planned_end=source["planned_end"], due_date=source["due_date"], factory_node_id=source["factory_node_id"],
            source=source["source"], source_ref=source["source_ref"], remark=source["remark"], created_by=payload.created_by,
        )
        new_id = _insert_order(cur, copy)
        write_history(cur, new_id, "COPY", None, "DRAFT", payload.created_by, f"Copied from {source['order_no']}")
        return {"message": "Production order copied.", "id": new_id, "order_no": new_no}
    return _run_write(action)


@router.post("/orders/import", status_code=201)
def import_orders(payload: ImportRequest):
    """All-or-nothing import. Returns per-row errors (1-based row numbers) when anything is invalid."""
    def action(cur):
        cur.execute("SELECT id, product_code FROM products WHERE status='ACTIVE'")
        products = {r["product_code"].upper(): r["id"] for r in cur.fetchall()}
        cur.execute("SELECT id, customer_code FROM customers WHERE status='ACTIVE'")
        customers = {r["customer_code"].upper(): r["id"] for r in cur.fetchall()}
        cur.execute("SELECT order_no FROM production_orders")
        existing = {r["order_no"] for r in cur.fetchall()}
        errors, orders, seen = [], [], set()
        for index, raw in enumerate(payload.rows, start=1):
            try:
                row = ImportRow(**{k: (v if v not in ("", None) else None) for k, v in raw.items() if k in ImportRow.model_fields}
                                | {"priority": raw.get("priority") or "MEDIUM", "source": raw.get("source") or "SALES_ORDER"})
            except ValidationError as exc:
                errors.append({"row": index, "message": "; ".join(e["msg"] for e in exc.errors())}); continue
            product_id = products.get(row.product_code.strip().upper())
            customer_id = customers.get(row.customer_code.strip().upper()) if row.customer_code else None
            order_no = (row.order_no or "").strip().upper()
            problems = []
            if not product_id:
                problems.append(f"unknown product {row.product_code}")
            if row.customer_code and not customer_id:
                problems.append(f"unknown customer {row.customer_code}")
            if order_no and (order_no in existing or order_no in seen):
                problems.append(f"duplicate order number {order_no}")
            if row.planned_start and row.planned_end and row.planned_end <= row.planned_start:
                problems.append("plan end must be after plan start")
            if problems:
                errors.append({"row": index, "message": "; ".join(problems)}); continue
            seen.add(order_no)
            orders.append((order_no, row, product_id, customer_id))
        if errors:
            raise HTTPException(status_code=422, detail={"message": "Import rejected. Fix the listed rows and try again.", "errors": errors})
        created = []
        for order_no, row, product_id, customer_id in orders:
            number = order_no or next_order_no(cur)
            order_id = _insert_order(cur, OrderCreate(
                order_no=number, customer_id=customer_id, product_id=product_id, order_qty=row.order_qty,
                priority=row.priority, planned_start=row.planned_start, planned_end=row.planned_end, due_date=row.due_date,
                factory_node_id=payload.factory_node_id, source=row.source, source_ref=row.source_ref, remark=row.remark,
                created_by=payload.created_by,
            ))
            created.append({"id": order_id, "order_no": number})
        return {"message": f"{len(created)} production orders imported.", "created": created}
    return _run_write(action)


@router.delete("/orders/{order_id}")
def delete_order(order_id: int):
    def action(cur):
        order = get_order(cur, order_id)
        if order["status"] != "DRAFT" or order["wo_count"]:
            raise HTTPException(status_code=409, detail="Only Draft orders without work orders can be deleted; cancel it instead.")
        cur.execute("DELETE FROM production_order_status_history WHERE production_order_id=%s", (order_id,))
        cur.execute("DELETE FROM production_orders WHERE id=%s", (order_id,))
        return {"message": "Production order deleted."}
    return _run_write(action)


# =========================================================
# LIFECYCLE
# =========================================================


def _transition(order_id: int, payload: OrderAction, allowed_from: set, to_status: str, action_name: str, extra_sql="", extra_params=(), check=None):
    def action(cur):
        order = get_order(cur, order_id)
        if order["status"] not in allowed_from:
            raise HTTPException(status_code=409, detail=f"Cannot {action_name.lower()} an order in status {order['status']}.")
        if check:
            check(cur, order)
        cur.execute(
            f"UPDATE production_orders SET status=%s{extra_sql}, updated_by=%s, version=version+1 WHERE id=%s AND version=%s",
            (to_status, *extra_params, payload.actor, order_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail=STALE)
        write_history(cur, order_id, action_name, order["status"], to_status, payload.actor, payload.remark)
        return {"message": f"Production order {to_status.lower().replace('_', ' ')}.", "version": payload.version + 1}
    return _run_write(action)


@router.post("/orders/{order_id}/release")
def release_order(order_id: int, payload: OrderAction):
    def check(cur, order):
        if not order["planned_start"] or not order["planned_end"]:
            raise HTTPException(status_code=409, detail="Set plan start and plan end before releasing the order.")
        assign_order_materials(cur, order["id"], payload.actor or "SYSTEM")
    return _transition(order_id, payload, {"DRAFT"}, "RELEASED", "RELEASE",
                       ", released_by=%s, released_at=NOW()", (payload.actor,), check)


@router.post("/orders/{order_id}/complete")
def complete_order(order_id: int, payload: OrderAction):
    def check(cur, _order):
        cur.execute(
            "SELECT COUNT(*) total FROM production_plan_details WHERE production_order_id=%s AND status IN ('UNSCHEDULED','SCHEDULED')",
            (order_id,))
        if cur.fetchone()["total"]:
            raise HTTPException(status_code=409, detail="Move or cancel the order's unreleased work orders before completing it.")
    return _transition(order_id, payload, {"IN_PRODUCTION"}, "COMPLETED", "COMPLETE", ", completed_at=NOW()", (), check)


@router.post("/orders/{order_id}/close")
def close_order(order_id: int, payload: OrderAction):
    return _transition(order_id, payload, {"COMPLETED"}, "CLOSED", "CLOSE", ", closed_by=%s, closed_at=NOW()", (payload.actor,))


@router.post("/orders/{order_id}/cancel")
def cancel_order(order_id: int, payload: OrderAction):
    def check(cur, _order):
        cur.execute(
            """SELECT d.id, d.status, pl.status AS plan_status FROM production_plan_details d
               LEFT JOIN production_plans pl ON pl.id = d.plan_id
               WHERE d.production_order_id=%s AND d.status<>'CANCELLED'""",
            (order_id,))
        work_orders = cur.fetchall()
        if any(w["status"] in {"RELEASED", "IN_PRODUCTION", "ON_HOLD", "COMPLETED", "CLOSED"} or (w["status"] == "SCHEDULED" and w["plan_status"] != "DRAFT") for w in work_orders):
            raise HTTPException(status_code=409, detail="The order has work orders in a confirmed/published plan or on the shop floor; it cannot be cancelled here.")
        for w in work_orders:
            cur.execute("UPDATE production_plan_details SET status='CANCELLED', updated_by=%s, version=version+1 WHERE id=%s",
                        (payload.actor, w["id"]))
            cur.execute(
                """INSERT INTO production_planning_audit_log (entity_type, entity_id, action, from_status, to_status, actor, remark)
                   VALUES ('WORK_ORDER', %s, 'CANCEL', %s, 'CANCELLED', %s, 'Production order cancelled')""",
                (w["id"], w["status"], payload.actor or "SYSTEM"))
            sync_work_order(cur, w["id"], payload.actor or "SYSTEM")
        cancel_order_materials(cur, order_id)
    return _transition(order_id, payload, {"DRAFT", "RELEASED", "SCHEDULING"}, "CANCELLED", "CANCEL",
                       ", cancelled_by=%s, cancelled_at=NOW(), cancel_reason=%s", (payload.actor, payload.remark), check)


@router.patch("/orders/{order_id}/priority")
def change_priority(order_id: int, payload: PriorityChange):
    def action(cur):
        order = get_order(cur, order_id)
        if order["status"] in FINAL_STATUSES:
            raise HTTPException(status_code=409, detail="Priority cannot be changed on completed, closed or cancelled orders.")
        cur.execute("UPDATE production_orders SET priority=%s, updated_by=%s, version=version+1 WHERE id=%s AND version=%s",
                    (payload.priority, payload.actor, order_id, payload.version))
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail=STALE)
        cur.execute(
            """UPDATE production_plan_details SET priority=%s, updated_by=%s, version=version+1
               WHERE production_order_id=%s AND status IN ('UNSCHEDULED','SCHEDULED')""",
            (payload.priority, payload.actor, order_id))
        write_history(cur, order_id, "PRIORITY", order["status"], order["status"], payload.actor,
                      f"{order['priority']} -> {payload.priority}{f' · {payload.remark}' if payload.remark else ''}")
        return {"message": "Priority updated.", "version": payload.version + 1}
    return _run_write(action)


# =========================================================
# ORDER DETAIL (electronic production record) + NOTES
# =========================================================


@router.get("/resolve")
def resolve_order(order_no: str | None = None, wo_no: str | None = None):
    def action(cur):
        if order_no:
            cur.execute("SELECT id, order_no FROM production_orders WHERE order_no=%s", (order_no.strip().upper(),))
        elif wo_no:
            cur.execute(
                """SELECT o.id, o.order_no FROM production_plan_details d JOIN production_orders o ON o.id = d.production_order_id
                   WHERE d.wo_no=%s""", (wo_no.strip().upper(),))
        else:
            raise HTTPException(status_code=400, detail="Provide order_no or wo_no.")
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Production order not found.")
        return row
    return _read(action)


@router.get("/orders/{order_id}/record")
def order_record(order_id: int):
    return _read(lambda cur: build_record(cur, order_id))


@router.post("/orders/{order_id}/notes", status_code=201)
def add_note(order_id: int, payload: NoteCreate):
    def action(cur):
        get_order(cur, order_id)
        cur.execute("INSERT INTO production_order_notes (production_order_id, note_text, created_by) VALUES (%s,%s,%s)",
                    (order_id, payload.note_text, payload.created_by))
        return {"message": "Note added.", "id": cur.lastrowid}
    return _run_write(action)
