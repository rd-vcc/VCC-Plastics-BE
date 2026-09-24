from datetime import date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query

from app.core.database import get_vcc_plastics_connection
from app.modules.production.planning.schemas import (
    MaterialStockUpdate,
    PlanAction,
    PlanCreate,
    SimulationRequest,
    WorkOrderAction,
    WorkOrderCreate,
    WorkOrderProgress,
    WorkOrderSchedule,
    WorkOrderUpdate,
)
from app.modules.material.inventory.service import available_by_material, safety_stocks
from app.modules.material.requirements.service import sync_work_order
from app.modules.material.usage.service import backflush
from app.modules.production.orders.service import sync_order_status
from app.modules.production.planning.service import (
    build_board,
    check_duplicate_wo_no,
    compute_end,
    ensure_plan_editable,
    get_plan,
    get_shift,
    get_work_order,
    load_backlog,
    load_context,
    load_plan_items,
    load_scheduled_between,
    shift_window,
    subtree_ids,
    write_audit,
)


router = APIRouter(prefix="/api/production-planning", tags=["Production Planning"])

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


def _compute_board(cur, plan, area_id=None):
    ctx = load_context(cur, plan, area_id)
    items = load_plan_items(cur, plan["id"])
    if ctx["scope"] is not None and area_id:
        machine_ids = {m["id"] for m in ctx["machines"]}
        items = [i for i in items if i["machine_id"] in machine_ids]
    others = load_scheduled_between(cur, ctx["window_start"] - timedelta(days=1), ctx["window_end"] + timedelta(days=1), plan["id"])
    return ctx, items, others, build_board(ctx, items, load_backlog(cur), others, datetime.now())


def _validation_detail(board):
    errors = [a for a in board["alerts"] if a["severity"] == "ERROR" and a["code"] != "BACKLOG_URGENT"]
    return {"message": "The plan has blocking issues. Resolve them before continuing.", "alerts": errors}


# =========================================================
# LOOKUPS
# =========================================================


@router.get("/lookups")
def lookups():
    def action(cur):
        cur.execute("SELECT id, product_code, product_name FROM products WHERE status='ACTIVE' ORDER BY product_code")
        products = cur.fetchall()
        cur.execute("SELECT id, mold_code, mold_name, cavity_count, status FROM molds WHERE is_active=1 ORDER BY mold_code")
        molds = cur.fetchall()
        cur.execute("SELECT mold_id, product_id, cavity_quantity FROM mold_products WHERE status='ACTIVE'")
        mold_products = cur.fetchall()
        cur.execute(
            """SELECT e.id, e.equipment_code, e.equipment_name, e.equipment_group_id, e.factory_node_id,
                      e.operational_status, g.group_name
               FROM equipment e JOIN equipment_types t ON t.id = e.equipment_type_id
               LEFT JOIN equipment_groups g ON g.id = e.equipment_group_id
               WHERE t.category='MACHINE' AND e.asset_status='ACTIVE' ORDER BY e.equipment_code"""
        )
        machines = cur.fetchall()
        cur.execute("SELECT id, material_code, material_name, unit FROM materials WHERE status='ACTIVE' ORDER BY material_code")
        materials = cur.fetchall()
        cur.execute("SELECT id, shift_code, shift_name, start_time, end_time, working_hours, is_overnight FROM shifts WHERE status='ACTIVE' ORDER BY start_time")
        shifts = [{**s, "start_time": str(s["start_time"]), "end_time": str(s["end_time"])} for s in cur.fetchall()]
        cur.execute(
            """SELECT n.id, n.code, n.name, n.parent_id, t.code AS node_type
               FROM factory_structure_nodes n LEFT JOIN factory_node_types t ON t.id = n.node_type_id
               WHERE n.status='ACTIVE' ORDER BY n.level_no, n.sort_order, n.name"""
        )
        nodes = cur.fetchall()
        cur.execute(
            """SELECT o.id, o.order_no, o.product_id, o.order_qty, o.priority, o.due_date, o.status,
                      c.short_name AS customer_short_name, c.customer_name,
                      COALESCE((SELECT SUM(d.planned_qty) FROM production_plan_details d
                                WHERE d.production_order_id=o.id AND d.status<>'CANCELLED'), 0) AS wo_planned_qty
               FROM production_orders o LEFT JOIN customers c ON c.id = o.customer_id
               WHERE o.status IN ('RELEASED','SCHEDULING','IN_PRODUCTION') ORDER BY o.order_no"""
        )
        orders = cur.fetchall()
        return {"products": products, "molds": molds, "mold_products": mold_products, "machines": machines,
                "materials": materials, "shifts": shifts, "factory_nodes": nodes, "production_orders": orders}
    return _read(action)


@router.get("/next-wo-no")
def next_wo_no(plan_date: date | None = None):
    prefix = f"WO-{(plan_date or date.today()):%y%m%d}-"
    def action(cur):
        cur.execute("SELECT wo_no FROM production_plan_details WHERE wo_no LIKE %s ORDER BY wo_no DESC LIMIT 1", (f"{prefix}%",))
        row = cur.fetchone()
        seq = int(row["wo_no"][len(prefix):]) + 1 if row and row["wo_no"][len(prefix):].isdigit() else 1
        return {"wo_no": f"{prefix}{seq:03d}"}
    return _read(action)


# =========================================================
# BOARD
# =========================================================


@router.get("/board")
def board(
    plan_date: date, shift_id: int = Query(gt=0), factory_node_id: int | None = Query(default=None, gt=0),
    area_id: int | None = Query(default=None, gt=0),
):
    def action(cur):
        conditions = "plan_date=%s AND shift_id=%s AND status<>'CANCELLED' AND "
        conditions += "factory_node_id=%s" if factory_node_id else "factory_node_id IS NULL"
        params = (plan_date, shift_id, factory_node_id) if factory_node_id else (plan_date, shift_id)
        cur.execute(f"SELECT * FROM production_plans WHERE {conditions}", params)
        plan = cur.fetchone()

        day_start = datetime.combine(plan_date, datetime.min.time())
        day_items = load_scheduled_between(cur, day_start, day_start + timedelta(days=1))
        if factory_node_id or area_id:
            cur.execute("SELECT id, parent_id FROM factory_structure_nodes")
            scope = subtree_ids(cur.fetchall(), area_id or factory_node_id)
            cur.execute("SELECT id, factory_node_id FROM equipment")
            in_scope = {r["id"] for r in cur.fetchall() if r["factory_node_id"] in scope}
            day_items = [i for i in day_items if i["machine_id"] in in_scope]

        if not plan:
            shift = get_shift(cur, shift_id)
            start, end = shift_window(plan_date, shift)
            return {
                "plan": None,
                "shift": {**shift, "start_time": str(shift["start_time"]), "end_time": str(shift["end_time"])},
                "window": {"start": start, "end": end, "working_hours": float(shift["working_hours"] or 0)},
                "server_time": datetime.now(), "backlog": load_backlog(cur), "day_items": day_items,
            }
        *_, result = _compute_board(cur, plan, area_id)
        result["day_items"] = day_items
        return result
    return _read(action)


@router.post("/simulate")
def simulate(payload: SimulationRequest):
    def action(cur):
        plan = get_plan(cur, payload.plan_id)
        ctx, items, others, before = _compute_board(cur, plan, payload.area_id)
        backlog = load_backlog(cur)
        machines = {m["id"]: m for m in ctx["machines"]}
        by_id = {i["id"]: dict(i) for i in items}
        backlog_by_id = {b["id"]: dict(b) for b in backlog}
        for change in payload.changes:
            item = by_id.get(change.id) or backlog_by_id.get(change.id)
            if item is None:
                raise HTTPException(status_code=404, detail=f"Work order {change.id} is not part of this plan or backlog.")
            if change.planned_qty:
                item["planned_qty"] = change.planned_qty
            if change.machine_id:
                machine = machines.get(change.machine_id)
                if not machine:
                    raise HTTPException(status_code=400, detail="Machine is not available for this plan scope.")
                item.update(machine_id=machine["id"], machine_code=machine["equipment_code"],
                            machine_name=machine["equipment_name"], machine_group_id=machine["equipment_group_id"])
            if change.planned_start:
                item["planned_start"] = change.planned_start
            if item["planned_start"]:
                item["planned_end"] = compute_end(item["planned_start"], item)
            if change.id in backlog_by_id:
                if not item["machine_id"] or not item["planned_start"]:
                    raise HTTPException(status_code=400, detail="A backlog work order needs a machine and a start time to be simulated.")
                item.update(status="SCHEDULED", plan_id=plan["id"])
                by_id[change.id] = backlog_by_id.pop(change.id)
        after = build_board(ctx, list(by_id.values()), list(backlog_by_id.values()), others, datetime.now())

        def summary(board_data):
            return {"kpis": board_data["kpis"], "checks": board_data["checks"],
                    "capacity_totals": board_data["capacity_totals"], "material_summary": board_data["material_summary"]}
        return {"before": summary(before), "after": {**summary(after), "alerts": after["alerts"],
                "capacity": after["capacity"], "work_orders": after["work_orders"]}}
    return _read(action)


# =========================================================
# PLANS
# =========================================================


@router.get("/plans")
def list_plans(date_from: date | None = None, date_to: date | None = None, status: str | None = None):
    def action(cur):
        conditions, params = [], []
        if date_from:
            conditions.append("p.plan_date>=%s"); params.append(date_from)
        if date_to:
            conditions.append("p.plan_date<=%s"); params.append(date_to)
        if status:
            conditions.append("p.status=%s"); params.append(status.upper())
        where = " WHERE " + " AND ".join(conditions) if conditions else ""
        cur.execute(
            f"""SELECT p.*, s.shift_code, s.shift_name, n.name AS factory_node_name,
                       COUNT(d.id) AS work_order_count, COALESCE(SUM(d.planned_qty),0) AS planned_qty
                FROM production_plans p
                JOIN shifts s ON s.id = p.shift_id
                LEFT JOIN factory_structure_nodes n ON n.id = p.factory_node_id
                LEFT JOIN production_plan_details d ON d.plan_id = p.id AND d.status<>'CANCELLED'
                {where}
                GROUP BY p.id ORDER BY p.plan_date DESC, s.start_time LIMIT 500""",
            tuple(params),
        )
        return cur.fetchall()
    return _read(action)


@router.post("/plans", status_code=201)
def create_plan(payload: PlanCreate):
    def action(cur):
        shift = get_shift(cur, payload.shift_id)
        node_code = "ALL"
        if payload.factory_node_id:
            cur.execute("SELECT code FROM factory_structure_nodes WHERE id=%s", (payload.factory_node_id,))
            node = cur.fetchone()
            if not node:
                raise HTTPException(status_code=404, detail="Factory node not found.")
            node_code = node["code"]
        plan_code = f"PP-{payload.plan_date:%Y%m%d}-{shift['shift_code']}-{node_code}"
        cur.execute("SELECT id FROM production_plans WHERE plan_code=%s", (plan_code,))
        if cur.fetchone():
            raise HTTPException(status_code=409, detail="A plan already exists for this date, shift and plant.")
        cur.execute(
            """INSERT INTO production_plans (plan_code, plan_date, shift_id, factory_node_id, remark, created_by)
               VALUES (%s,%s,%s,%s,%s,%s)""",
            (plan_code, payload.plan_date, payload.shift_id, payload.factory_node_id, payload.remark, payload.created_by),
        )
        plan_id = cur.lastrowid
        write_audit(cur, "PLAN", plan_id, "CREATE", None, "DRAFT", payload.created_by)
        return {"message": "Production plan created.", "id": plan_id, "plan_code": plan_code}
    return _run_write(action)


def _transition_plan(plan_id: int, payload: PlanAction, allowed_from: str, to_status: str, audit_action: str, validate: bool):
    def action(cur):
        plan = get_plan(cur, plan_id)
        if plan["status"] != allowed_from:
            raise HTTPException(status_code=409, detail=f"Plan must be {allowed_from} to {audit_action.lower()}.")
        if validate:
            *_, board_data = _compute_board(cur, plan)
            if not board_data["work_orders"]:
                raise HTTPException(status_code=409, detail="The plan has no scheduled work orders.")
            if board_data["checks"]["error_count"]:
                raise HTTPException(status_code=409, detail=_validation_detail(board_data))
        extra = ""
        params = [to_status, payload.actor]
        if to_status == "CONFIRMED":
            extra = ", confirmed_by=%s, confirmed_at=NOW()"; params.append(payload.actor)
        elif to_status == "PUBLISHED":
            extra = ", published_by=%s, published_at=NOW()"; params.append(payload.actor)
        cur.execute(
            f"UPDATE production_plans SET status=%s, updated_by=%s{extra}, version=version+1 WHERE id=%s AND version=%s",
            tuple(params + [plan_id, payload.version]),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail=STALE)
        if to_status == "PUBLISHED":
            cur.execute(
                "SELECT id FROM production_plan_details WHERE plan_id=%s AND status='SCHEDULED'", (plan_id,))
            for row in cur.fetchall():
                write_audit(cur, "WORK_ORDER", row["id"], "RELEASE", "SCHEDULED", "RELEASED", payload.actor)
            cur.execute(
                "UPDATE production_plan_details SET status='RELEASED', released_at=NOW(), updated_by=%s, version=version+1 WHERE plan_id=%s AND status='SCHEDULED'",
                (payload.actor, plan_id),
            )
            cur.execute("SELECT DISTINCT production_order_id FROM production_plan_details WHERE plan_id=%s", (plan_id,))
            for row in cur.fetchall():
                sync_order_status(cur, row["production_order_id"])
        write_audit(cur, "PLAN", plan_id, audit_action, allowed_from, to_status, payload.actor, payload.remark)
        return {"message": f"Plan {to_status.lower()}.", "version": payload.version + 1}
    return _run_write(action)


@router.post("/plans/{plan_id}/confirm")
def confirm_plan(plan_id: int, payload: PlanAction):
    return _transition_plan(plan_id, payload, "DRAFT", "CONFIRMED", "CONFIRM", validate=True)


@router.post("/plans/{plan_id}/reopen")
def reopen_plan(plan_id: int, payload: PlanAction):
    return _transition_plan(plan_id, payload, "CONFIRMED", "DRAFT", "REOPEN", validate=False)


@router.post("/plans/{plan_id}/publish")
def publish_plan(plan_id: int, payload: PlanAction):
    return _transition_plan(plan_id, payload, "CONFIRMED", "PUBLISHED", "PUBLISH", validate=True)


@router.get("/plans/{plan_id}/audit-log")
def plan_audit_log(plan_id: int):
    def action(cur):
        get_plan(cur, plan_id)
        cur.execute(
            """SELECT a.*, d.wo_no FROM production_planning_audit_log a
               LEFT JOIN production_plan_details d ON a.entity_type='WORK_ORDER' AND d.id=a.entity_id
               WHERE (a.entity_type='PLAN' AND a.entity_id=%s)
                  OR (a.entity_type='WORK_ORDER' AND d.plan_id=%s)
               ORDER BY a.acted_at DESC, a.id DESC""",
            (plan_id, plan_id),
        )
        return cur.fetchall()
    return _read(action)


# =========================================================
# WORK ORDERS
# =========================================================


def _check_references(cur, payload):
    if payload.production_order_id:
        cur.execute("SELECT status, product_id FROM production_orders WHERE id=%s", (payload.production_order_id,))
        order = cur.fetchone()
        if not order or order["status"] not in {"RELEASED", "SCHEDULING", "IN_PRODUCTION"}:
            raise HTTPException(status_code=400, detail="Production order must be Released, Scheduling or In Production.")
        if order["product_id"] != payload.product_id:
            raise HTTPException(status_code=400, detail="Work order product must match the production order product.")
    cur.execute("SELECT status FROM products WHERE id=%s", (payload.product_id,))
    product = cur.fetchone()
    if not product or product["status"] != "ACTIVE":
        raise HTTPException(status_code=400, detail="Product is missing or inactive.")
    if payload.mold_id:
        cur.execute("SELECT is_active FROM molds WHERE id=%s", (payload.mold_id,))
        mold = cur.fetchone()
        if not mold or not mold["is_active"]:
            raise HTTPException(status_code=400, detail="Mold is missing or inactive.")
    if payload.material_id:
        cur.execute("SELECT status FROM materials WHERE id=%s", (payload.material_id,))
        material = cur.fetchone()
        if not material or material["status"] != "ACTIVE":
            raise HTTPException(status_code=400, detail="Material is missing or inactive.")


def _editable_work_order(cur, wo_id: int):
    wo = get_work_order(cur, wo_id)
    if wo["status"] == "UNSCHEDULED":
        return wo, None
    if wo["status"] == "SCHEDULED":
        plan = get_plan(cur, wo["plan_id"])
        ensure_plan_editable(plan)
        return wo, plan
    raise HTTPException(status_code=409, detail="Released, completed or cancelled work orders cannot be changed here.")


@router.get("/work-orders/{wo_id}")
def work_order_detail(wo_id: int):
    return _read(lambda cur: get_work_order(cur, wo_id))


@router.post("/work-orders", status_code=201)
def create_work_order(payload: WorkOrderCreate):
    def action(cur):
        check_duplicate_wo_no(cur, payload.wo_no)
        if not payload.production_order_id:
            raise HTTPException(status_code=400, detail="Every work order must be created from a production order.")
        _check_references(cur, payload)
        cur.execute(
            """INSERT INTO production_plan_details
               (wo_no, production_order_id, product_id, mold_id, material_id, planned_qty, cavity, cycle_time_sec,
                setup_minutes, part_weight_g, due_date, priority, source, source_ref, backlog_reason, remark, status, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'UNSCHEDULED',%s)""",
            (payload.wo_no, payload.production_order_id, payload.product_id, payload.mold_id, payload.material_id, payload.planned_qty, payload.cavity,
             payload.cycle_time_sec, payload.setup_minutes, payload.part_weight_g, payload.due_date, payload.priority,
             payload.source, payload.source_ref, payload.backlog_reason, payload.remark, payload.created_by),
        )
        wo_id = cur.lastrowid
        write_audit(cur, "WORK_ORDER", wo_id, "CREATE", None, "UNSCHEDULED", payload.created_by)
        sync_order_status(cur, payload.production_order_id)
        sync_work_order(cur, wo_id, payload.created_by or "SYSTEM")
        return {"message": "Work order created.", "id": wo_id}
    return _run_write(action)


@router.put("/work-orders/{wo_id}")
def update_work_order(wo_id: int, payload: WorkOrderUpdate):
    def action(cur):
        wo, _plan = _editable_work_order(cur, wo_id)
        check_duplicate_wo_no(cur, payload.wo_no, wo_id)
        _check_references(cur, payload)
        planned_end = compute_end(wo["planned_start"], payload.model_dump()) if wo["planned_start"] else None
        cur.execute(
            """UPDATE production_plan_details SET wo_no=%s, production_order_id=%s, product_id=%s, mold_id=%s, material_id=%s, planned_qty=%s,
               cavity=%s, cycle_time_sec=%s, setup_minutes=%s, part_weight_g=%s, due_date=%s, priority=%s, source=%s,
               source_ref=%s, backlog_reason=%s, remark=%s, planned_end=%s, updated_by=%s, version=version+1
               WHERE id=%s AND version=%s""",
            (payload.wo_no, payload.production_order_id, payload.product_id, payload.mold_id, payload.material_id,
             payload.planned_qty, payload.cavity, payload.cycle_time_sec, payload.setup_minutes, payload.part_weight_g,
             payload.due_date, payload.priority, payload.source, payload.source_ref, payload.backlog_reason, payload.remark,
             planned_end, payload.updated_by,
             wo_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail=STALE)
        write_audit(cur, "WORK_ORDER", wo_id, "UPDATE", wo["status"], wo["status"], payload.updated_by)
        for order_id in {wo["production_order_id"], payload.production_order_id}:
            sync_order_status(cur, order_id)
        sync_work_order(cur, wo_id, payload.updated_by or "SYSTEM")
        return {"message": "Work order updated.", "version": payload.version + 1}
    return _run_write(action)


@router.post("/work-orders/{wo_id}/schedule")
def schedule_work_order(wo_id: int, payload: WorkOrderSchedule):
    def action(cur):
        wo, _current_plan = _editable_work_order(cur, wo_id)
        plan = get_plan(cur, payload.plan_id)
        ensure_plan_editable(plan)
        cur.execute(
            """SELECT e.id FROM equipment e JOIN equipment_types t ON t.id = e.equipment_type_id
               WHERE e.id=%s AND t.category='MACHINE' AND e.asset_status='ACTIVE'""",
            (payload.machine_id,),
        )
        if not cur.fetchone():
            raise HTTPException(status_code=400, detail="Machine is missing, inactive or not a production machine.")
        planned_end = compute_end(payload.planned_start, wo)
        cur.execute(
            """UPDATE production_plan_details SET plan_id=%s, machine_id=%s, planned_start=%s, planned_end=%s,
               status='SCHEDULED', updated_by=%s, version=version+1 WHERE id=%s AND version=%s""",
            (plan["id"], payload.machine_id, payload.planned_start, planned_end, payload.actor, wo_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail=STALE)
        write_audit(cur, "WORK_ORDER", wo_id, "SCHEDULE" if wo["status"] == "UNSCHEDULED" else "RESCHEDULE",
                    wo["status"], "SCHEDULED", payload.actor, f"{plan['plan_code']} @ {payload.planned_start:%Y-%m-%d %H:%M}")
        sync_order_status(cur, wo["production_order_id"])
        sync_work_order(cur, wo_id, payload.actor or "SYSTEM")
        return {"message": "Work order scheduled.", "version": payload.version + 1, "planned_end": planned_end}
    return _run_write(action)


@router.post("/work-orders/{wo_id}/unschedule")
def unschedule_work_order(wo_id: int, payload: WorkOrderAction):
    def action(cur):
        wo, _plan = _editable_work_order(cur, wo_id)
        if wo["status"] != "SCHEDULED":
            raise HTTPException(status_code=409, detail="Only scheduled work orders can be moved back to the backlog.")
        cur.execute(
            """UPDATE production_plan_details SET plan_id=NULL, machine_id=NULL, planned_start=NULL, planned_end=NULL,
               status='UNSCHEDULED', backlog_reason=COALESCE(%s, backlog_reason), updated_by=%s, version=version+1
               WHERE id=%s AND version=%s""",
            (payload.remark, payload.actor, wo_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail=STALE)
        write_audit(cur, "WORK_ORDER", wo_id, "UNSCHEDULE", "SCHEDULED", "UNSCHEDULED", payload.actor, payload.remark)
        sync_order_status(cur, wo["production_order_id"])
        sync_work_order(cur, wo_id, payload.actor or "SYSTEM")
        return {"message": "Work order moved to backlog.", "version": payload.version + 1}
    return _run_write(action)


@router.post("/work-orders/{wo_id}/cancel")
def cancel_work_order(wo_id: int, payload: WorkOrderAction):
    def action(cur):
        wo, _plan = _editable_work_order(cur, wo_id)
        cur.execute(
            "UPDATE production_plan_details SET status='CANCELLED', updated_by=%s, version=version+1 WHERE id=%s AND version=%s",
            (payload.actor, wo_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail=STALE)
        write_audit(cur, "WORK_ORDER", wo_id, "CANCEL", wo["status"], "CANCELLED", payload.actor, payload.remark)
        sync_order_status(cur, wo["production_order_id"])
        sync_work_order(cur, wo_id, payload.actor or "SYSTEM")
        return {"message": "Work order cancelled.", "version": payload.version + 1}
    return _run_write(action)


@router.patch("/work-orders/{wo_id}/progress")
def update_work_order_progress(wo_id: int, payload: WorkOrderProgress):
    def action(cur):
        wo = get_work_order(cur, wo_id)
        if wo["status"] not in {"SCHEDULED", "RELEASED", "IN_PRODUCTION", "ON_HOLD", "COMPLETED"}:
            raise HTTPException(status_code=409, detail="Progress can only be recorded for scheduled, released, running, on-hold or completed work orders.")
        status = wo["status"]
        if status in {"RELEASED", "IN_PRODUCTION"} and payload.actual_qty >= wo["planned_qty"]:
            status = "COMPLETED"
        elif status == "RELEASED" and payload.actual_qty > 0:
            status = "IN_PRODUCTION"
        elif status == "COMPLETED" and payload.actual_qty < wo["planned_qty"]:
            status = "IN_PRODUCTION"
        reject_qty = wo["reject_qty"] if payload.reject_qty is None else payload.reject_qty
        cur.execute(
            """UPDATE production_plan_details SET actual_qty=%s, reject_qty=%s, status=%s, updated_by=%s, version=version+1,
                      started_at=IF(%s IN ('IN_PRODUCTION','COMPLETED'), COALESCE(started_at, NOW()), started_at),
                      completed_at=IF(%s='COMPLETED', COALESCE(completed_at, NOW()), NULL)
               WHERE id=%s AND version=%s""",
            (payload.actual_qty, reject_qty, status, payload.actor, status, status, wo_id, payload.version),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=409, detail=STALE)
        write_audit(cur, "WORK_ORDER", wo_id, "PROGRESS", wo["status"], status, payload.actor,
                    f"actual_qty {wo['actual_qty']} -> {payload.actual_qty}, reject_qty {wo['reject_qty']} -> {reject_qty}")
        sync_order_status(cur, wo["production_order_id"])
        sync_work_order(cur, wo_id, payload.actor or "SYSTEM")
        # production result -> material consumption at the BOM standard (Material Usage back-flush)
        consumed = backflush(cur, wo_id, payload.actor or "SYSTEM")
        return {"message": "Progress updated.", "version": payload.version + 1, "material_consumed": consumed["created"],
                "material_not_at_machine": consumed["not_at_machine"]}
    return _run_write(action)


@router.delete("/work-orders/{wo_id}")
def delete_work_order(wo_id: int):
    def action(cur):
        wo = get_work_order(cur, wo_id)
        if wo["status"] != "UNSCHEDULED":
            raise HTTPException(status_code=409, detail="Only unscheduled work orders can be deleted; cancel it instead.")
        cur.execute(
            "SELECT COUNT(*) total FROM production_planning_audit_log WHERE entity_type='WORK_ORDER' AND entity_id=%s",
            (wo_id,))
        if cur.fetchone()["total"] > 2:
            raise HTTPException(status_code=409, detail="This work order has planning history; cancel it instead of deleting.")
        cur.execute("DELETE FROM production_planning_audit_log WHERE entity_type='WORK_ORDER' AND entity_id=%s", (wo_id,))
        cur.execute("DELETE FROM production_plan_details WHERE id=%s", (wo_id,))
        return {"message": "Work order deleted."}
    return _run_write(action)


# =========================================================
# MATERIAL STOCK (read from material lots; safety stock kept here for the planning dialog)
# =========================================================


@router.get("/material-stock")
def list_material_stock():
    def action(cur):
        cur.execute("SELECT id, material_code, material_name, unit FROM materials WHERE status='ACTIVE' ORDER BY material_code")
        rows = cur.fetchall()
        stock, safety = available_by_material(cur), safety_stocks(cur)
        return [{"material_id": r["id"], "material_code": r["material_code"], "material_name": r["material_name"], "unit": r["unit"],
                 "available_qty": stock[r["id"]]["available"] if r["id"] in stock else None,
                 "safety_stock_qty": safety.get(r["id"], 0.0)} for r in rows]
    return _read(action)


@router.put("/material-stock/{material_id}")
def update_material_stock(material_id: int, payload: MaterialStockUpdate):
    def action(cur):
        cur.execute("SELECT id FROM materials WHERE id=%s", (material_id,))
        if not cur.fetchone():
            raise HTTPException(status_code=404, detail="Material not found.")
        cur.execute(
            """INSERT INTO material_stock_settings (material_id, safety_stock_qty, updated_by) VALUES (%s,%s,%s)
               ON DUPLICATE KEY UPDATE safety_stock_qty=VALUES(safety_stock_qty), updated_by=VALUES(updated_by)""",
            (material_id, payload.safety_stock_qty, payload.updated_by),
        )
        return {"message": "Safety stock updated."}
    return _run_write(action)
