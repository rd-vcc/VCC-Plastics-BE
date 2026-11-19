"""Demo production history: about three weeks of shop-floor activity, run through the real services.

Every action is executed at its simulated moment: Python ``datetime.now()`` / ``date.today()`` inside the
app modules and MySQL ``NOW()`` (``SET timestamp``) are frozen at that moment. So all side effects are written
exactly as the screens write them: material reservation / issue / back-flush / return, mold install / shots /
removal, machine status and downtime, inspection plans and samples, NG records, order status, audit trails.

Lines (machine -> molds it runs), from the start of the window or from the machine's last recorded event:
    IMM-D01 -> MLD-ELEC-001 / MLD-PACK-001, IMM-D02 -> MLD-MOTO-001 / MLD-HOME-001, IMM-B01 -> MLD-AUTO-001 / MLD-PACK-002
IMM-A01 (running a user Work Order), IMM-A02 (open downtime) and IMM-B02 (maintenance) are left untouched.

The window ends now: some Work Orders are running or on hold, tonight's / tomorrow's are queued or released,
future orders are still drafts. Re-running is refused once the demo orders exist (restore the backup instead).

    python -m scripts.seed_production_history_demo
"""

import heapq
import math
import random
import sys
import traceback
from datetime import date as _real_date, datetime as _real_datetime, timedelta

from fastapi import HTTPException

from app.core.database import get_vcc_plastics_connection
from app.modules.machine import service as machine_service
from app.modules.material.allocations import service as allocation_service
from app.modules.material.inventory.service import next_lot_no, write_txn
from app.modules.material.requirements.service import assign_order_materials, sync_work_order
from app.modules.production.execution import service as execution
from app.modules.production.orders.service import next_order_no, sync_order_status, write_history
from app.modules.production.planning.service import compute_end, write_audit
from app.modules.production.work_orders import service as wo_service
from app.modules.quality import service as quality_service
from app.modules.traceability import service as trace_service

# =========================================================
# FROZEN CLOCK
# =========================================================

SIM = {"now": _real_datetime.now().replace(microsecond=0)}


class _DateTimeMeta(type):
    def __instancecheck__(cls, obj):
        return isinstance(obj, _real_datetime)


class _DateMeta(type):
    def __instancecheck__(cls, obj):
        return isinstance(obj, _real_date)


class datetime(_real_datetime, metaclass=_DateTimeMeta):  # noqa: N801 - same name keeps the DB driver converters working
    @classmethod
    def now(cls, tz=None):
        return SIM["now"]

    @classmethod
    def today(cls):
        return SIM["now"]


class date(_real_date, metaclass=_DateMeta):  # noqa: N801
    @classmethod
    def today(cls):
        return SIM["now"].date()


def _freeze_app_modules():
    for name, module in list(sys.modules.items()):
        if not name.startswith("app.") or module is None:
            continue
        if getattr(module, "datetime", None) is _real_datetime:
            module.datetime = datetime
        if getattr(module, "date", None) is _real_date:
            module.date = date


# =========================================================
# SCENARIO
# =========================================================

random.seed(20261017)

MARK = "Demo lịch sử sản xuất"
PLANNER = "00842"
LEADER = "00842"
QC = "00465"
STORE = "00863"
OPERATORS = ["00863", "00001"]

END = SIM["now"]
START = (END - timedelta(days=21)).replace(hour=6, minute=0, second=0)
HORIZON = END + timedelta(days=4)

LINES = [(9, [4, 2]), (10, [5, 3]), (5, [6, 8])]

# mold -> ideal cycle (s), part weight (g), Work Order qty range (min, max, step)
MOLD_PARAMS = {
    4: (28, 12.5, (4000, 6000, 500)),
    2: (12, 2.1, (40000, 80000, 5000)),
    5: (55, 310.0, (500, 900, 50)),
    3: (32, 95.0, (1500, 2600, 100)),
    6: (40, 85.5, (1400, 2400, 100)),
    8: (11, 2.1, (30000, 50000, 5000)),
}

CUSTOMERS = [
    ("HONDA", "Honda Vietnam", "Honda"),
    ("TOYOTA", "Toyota Boshoku Hà Nội", "Toyota"),
    ("PANASONIC", "Panasonic Vietnam", "Panasonic"),
    ("LAVIE", "Công ty La Vie", "La Vie"),
    ("LOCKNLOCK", "Lock&Lock Vina", "Lock&Lock"),
]
PRODUCT_CUSTOMER = {1: "TOYOTA", 2: "TOYOTA", 3: "HONDA", 4: "PANASONIC", 5: "LAVIE", 6: "LOCKNLOCK"}

# reject reasons reported at the machine, per product
REJECT_POOL = {
    1: ["NG-001", "NG-003", "NG-007", "NG-010"],
    3: ["NG-002", "NG-004", "NG-003", "NG-009"],
    4: ["NG-001", "NG-002", "NG-007", "NG-008"],
    5: ["NG-001", "NG-002", "NG-004", "NG-005"],
    6: ["NG-004", "NG-003", "NG-010", "NG-006"],
}

# hold reason code, min / max minutes, root cause, action taken (closing the downtime)
HOLDS = [
    ("DT-002", 40, 150, "Gãy chốt đẩy khuôn", "Thay chốt đẩy, kiểm tra lại khuôn"),
    ("DT-003", 10, 45, "Cảnh báo nhiệt độ vùng 3 xi lanh", "Reset alarm, kiểm tra cặp nhiệt"),
    ("DT-006", 15, 60, "Công nhân nghỉ đột xuất", "Điều người từ chuyền khác"),
    ("SU-003", 10, 30, "Sản phẩm có xu hướng bavia", "Chỉnh áp suất phun và lực kẹp"),
    ("DT-005", 20, 90, "Mất điện lưới khu vực", "Chờ có điện, khởi động lại máy"),
    ("SU-004", 30, 90, "Nhựa chưa đủ độ khô", "Sấy bổ sung nguyên liệu"),
    ("DT-004", 15, 40, "Chờ QC duyệt mẫu đầu", "QC duyệt mẫu đầu đạt"),
]

ROOT_CAUSES = {
    "NG-001": ("MACHINE", "Lực kẹp khuôn không đủ, mặt phân khuôn bám bẩn", "Vệ sinh mặt phân khuôn, tăng lực kẹp 5%"),
    "NG-002": ("METHOD", "Áp suất phun thấp, nhiệt độ nhựa chưa đạt", "Tăng áp suất phun 5 bar, kiểm tra nhiệt độ xi lanh"),
    "NG-003": ("METHOD", "Thời gian giữ áp ngắn", "Tăng thời gian giữ áp thêm 1,5 s"),
    "NG-004": ("METHOD", "Làm nguội chưa đủ, nhiệt độ hai nửa khuôn lệch", "Cân bằng nhiệt khuôn, tăng thời gian làm nguội 2 s"),
    "NG-005": ("MATERIAL", "Hạt nhựa chưa sấy đủ, độ ẩm cao", "Sấy nguyên liệu 80 °C trong 3 giờ trước khi dùng"),
    "NG-006": ("METHOD", "Áp suất giữ thấp, thành dày", "Tăng áp suất giữ, giảm tốc độ phun giai đoạn cuối"),
    "NG-007": ("METHOD", "Nhiệt độ dòng chảy tại điểm hợp dòng thấp", "Tăng nhiệt độ khuôn 5 °C"),
    "NG-008": ("MACHINE", "Rãnh thoát khí khuôn bị tắc", "Vệ sinh rãnh thoát khí"),
    "NG-009": ("MACHINE", "Nhiệt độ khuôn dao động do bộ điều nhiệt", "Hiệu chỉnh bộ điều nhiệt khuôn, theo dõi 3 lần kiểm tra"),
    "NG-010": ("MAN", "Thao tác lấy sản phẩm chưa đúng, khay chứa không lót", "Lót xốp khay chứa, đào tạo lại thao tác"),
}

# material -> received lot size
LOT_SIZE = {2: 1000, 3: 1000, 4: 1000, 5: 500, 6: 100, 7: 100, 8: 25, 9: 500, 10: 1000, 11: 2000}
PACKAGING = {10, 11}

# =========================================================
# EVENT LOOP
# =========================================================

QUEUE = []
SEQ = [0]
STATS = {"events": 0, "skipped": 0, "errors": 0}


def at(moment, fn, *args):
    SEQ[0] += 1
    heapq.heappush(QUEUE, (moment, SEQ[0], fn, args))


def jitter(minutes_from, minutes_to):
    return timedelta(minutes=random.uniform(minutes_from, minutes_to))


class Ctx:
    conn = None
    cur = None
    ids = {}            # reason code -> id
    plant_of = {}       # machine id -> plant node
    plant_code = {}
    shifts = {}
    mold_product = {}
    mold_cavity = {}
    resin_of = {}
    bom_of = {}
    customers = {}
    gauges = []
    lines = {}          # machine id -> its Work Orders in planned order


C = Ctx()


def q(sql, params=()):
    C.cur.execute(sql, params)
    return C.cur.fetchall()


def q1(sql, params=()):
    C.cur.execute(sql, params)
    return C.cur.fetchone()


def wo_row(w):
    return q1("SELECT id, status, version, actual_qty, reject_qty FROM production_plan_details WHERE id=%s", (w["id"],))


# =========================================================
# SETUP
# =========================================================


def load_context():
    for r in q("SELECT id, reason_code FROM reason_codes"):
        C.ids[r["reason_code"]] = r["id"]
    for r in q("""SELECT e.id, f.id AS plant_id, f.code FROM equipment e
                  JOIN factory_structure_nodes ws ON ws.id=e.factory_node_id
                  JOIN factory_structure_nodes dv ON dv.id=ws.parent_id
                  JOIN factory_structure_nodes f ON f.id=dv.parent_id"""):
        C.plant_of[r["id"]] = r["plant_id"]
        C.plant_code[r["id"]] = r["code"]
    for r in q("SELECT id, shift_code FROM shifts WHERE shift_code IN ('NGAY','DEM')"):
        C.shifts[r["shift_code"]] = r["id"]
    for r in q("SELECT mp.mold_id, mp.product_id, m.cavity_count FROM mold_products mp JOIN molds m ON m.id=mp.mold_id"):
        C.mold_product[r["mold_id"]] = r["product_id"]
        C.mold_cavity[r["mold_id"]] = int(r["cavity_count"])
    for r in q("""SELECT b.product_id, b.id, b.bom_revision,
                         (SELECT bi.material_id FROM bom_items bi WHERE bi.bom_id=b.id AND bi.component_type='RESIN' ORDER BY bi.line_no LIMIT 1) AS resin
                  FROM boms b WHERE b.status='ACTIVE'"""):
        C.bom_of[r["product_id"]] = (r["id"], r["bom_revision"])
        C.resin_of[r["product_id"]] = r["resin"]
    for code, name, short in CUSTOMERS:
        C.cur.execute("INSERT IGNORE INTO customers (customer_code, customer_name, short_name, country, created_by) VALUES (%s,%s,%s,'VN',%s)",
                      (code, name, short, PLANNER))
    for r in q("SELECT id, customer_code FROM customers"):
        C.customers[r["customer_code"]] = r["id"]
    C.gauges = q("SELECT id, equipment_type_id, status, quality_state, next_calibration_date FROM measuring_equipment")


def gauge_for(equipment_type_id):
    """A measuring equipment valid on the simulated day: same type when possible, else any valid caliper."""
    today = SIM["now"].date()
    usable = [g for g in C.gauges if quality_service.equipment_state(g, today) in quality_service.USABLE_STATES]
    same = [g["id"] for g in usable if g["equipment_type_id"] == equipment_type_id]
    return random.choice(same or [g["id"] for g in usable if g["equipment_type_id"] == 2] or [g["id"] for g in usable])


def line_start(machine_id):
    row = q1("SELECT MAX(started_at) AS t FROM machine_status_events WHERE equipment_id=%s", (machine_id,))
    begin = START
    if row and row["t"] and row["t"] + timedelta(hours=1) > begin:
        begin = (row["t"] + timedelta(days=1)).replace(hour=6, minute=0, second=0)
    return begin


def skip_sunday(t):
    if t.weekday() == 6 and t.hour >= 6:
        return (t + timedelta(days=1)).replace(hour=6, minute=0, second=0)
    if t.weekday() == 0 and t.hour < 6:
        return t.replace(hour=6, minute=0, second=0)
    return t


def build_schedule():
    orders = []
    for machine_id, molds in LINES:
        begin = line_start(machine_id)
        at(begin - timedelta(minutes=20), ev_machine_ready, machine_id)
        t, turn = begin, random.randrange(2)
        while t < HORIZON:
            mold_id = molds[turn % 2]
            turn += 1
            product_id = C.mold_product[mold_id]
            cycle, weight, (lo, hi, step) = MOLD_PARAMS[mold_id]
            cavity = C.mold_cavity[mold_id]
            wos = []
            for i in range(random.choice([1, 1, 2, 2, 3])):
                t = skip_sunday(t)
                qty = random.randrange(lo, hi + 1, step)
                setup = 40 if i == 0 else 10
                item = {"planned_qty": qty, "cavity": cavity, "cycle_time_sec": cycle, "setup_minutes": setup}
                end = compute_end(t, item)
                bad = random.random() < 0.12
                holds = []
                for _ in range(random.choices([0, 1, 2], [55, 35, 10])[0]):
                    code, lo_m, hi_m, cause, action = random.choice(HOLDS)
                    offset = random.uniform(5, 25) if code == "DT-004" else random.uniform(60, max((end - t).total_seconds() / 60 - 60, 90))
                    holds.append((offset, random.uniform(lo_m, hi_m), code, cause, action))
                wos.append({
                    "machine_id": machine_id, "mold_id": mold_id, "product_id": product_id, "cavity": cavity, "cycle": cycle,
                    "weight": weight, "qty": qty, "setup": setup, "planned_start": t, "planned_end": end,
                    "speed": random.uniform(1.02, 1.12) * (1.06 if bad else 1.0), "reject_rate": random.uniform(0.012, 0.03) * (2.4 if bad else 1.0),
                    "qc_ng": 0.05 if bad else 0.006, "drift": random.gauss(0, 0.12) * (3 if bad else 1), "holds": holds,
                    "operator": random.choice(OPERATORS), "id": None, "token": 0, "qc_seen": set(), "done": False, "started": None,
                    "defects": random.sample(REJECT_POOL.get(product_id, ["NG-001"]), random.choice([1, 2])),
                })
                C.lines.setdefault(machine_id, []).append(wos[-1])
                t = end + (end - t) * 0.08 + jitter(20, 60)
            if random.random() < 0.12:
                t += timedelta(hours=random.uniform(3, 8))
            orders.append({"product_id": product_id, "wos": wos, "id": None,
                           "priority": random.choices(["LOW", "MEDIUM", "HIGH", "URGENT"], [15, 50, 28, 7])[0]})
    for o in orders:
        first = o["wos"][0]["planned_start"]
        created = first - timedelta(days=random.uniform(3, 5))
        released = first - timedelta(hours=random.uniform(40, 60))
        at(created, ev_order_create, o)
        at(released, ev_order_release, o)
        for w in o["wos"]:
            w["order"] = o
            create_at = max(released + timedelta(minutes=30), w["planned_start"] - timedelta(hours=random.uniform(20, 30)))
            at(create_at, ev_wo_create, w)
            at(max(create_at + timedelta(minutes=10), w["planned_start"] - timedelta(hours=3)), ev_wo_release, w)
            at(w["planned_start"] - timedelta(minutes=45), ev_wo_issue, w)
            at(w["planned_start"] + timedelta(minutes=w["setup"] * random.uniform(0.8, 1.4)), ev_wo_start, w)
    day = START.replace(hour=8, minute=30)
    while day < END:
        at(day, ev_ng_review)
        at(day + timedelta(hours=7), ev_ng_review)
        day += timedelta(days=1)
    return orders


# =========================================================
# ORDERS / PLANS / WORK ORDERS
# =========================================================


def ev_machine_ready(machine_id):
    row = q1("SELECT operational_status FROM equipment WHERE id=%s", (machine_id,))
    if row["operational_status"] != "IDLE":
        machine_service.set_machine_status(C.cur, machine_id, "IDLE", "MES", None, "Sẵn sàng sản xuất")


def ev_order_create(o):
    wos = o["wos"]
    qty = sum(w["qty"] for w in wos)
    bom_id, bom_rev = C.bom_of[o["product_id"]]
    order_no = next_order_no(C.cur)
    C.cur.execute(
        """INSERT INTO production_orders (order_no, customer_id, product_id, bom_id, bom_revision, order_qty, priority, planned_start, planned_end,
               due_date, factory_node_id, source, source_ref, status, remark, created_by)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'SALES_ORDER',%s,'DRAFT',%s,%s)""",
        (order_no, C.customers[PRODUCT_CUSTOMER[o["product_id"]]], o["product_id"], bom_id, bom_rev, qty, o["priority"],
         wos[0]["planned_start"], wos[-1]["planned_end"], wos[-1]["planned_end"] + timedelta(hours=random.uniform(12, 48)),
         C.plant_of[wos[0]["machine_id"]], f"SO-{SIM['now']:%y%m}-{random.randint(1000, 9999)}", MARK, PLANNER))
    o["id"] = C.cur.lastrowid
    o["order_no"] = order_no
    write_history(C.cur, o["id"], "CREATE", None, "DRAFT", PLANNER, None)


def ev_order_release(o):
    if not o["id"]:
        return
    assign_order_materials(C.cur, o["id"], PLANNER)
    C.cur.execute("UPDATE production_orders SET status='RELEASED', released_by=%s, released_at=NOW(), updated_by=%s, version=version+1 WHERE id=%s AND status='DRAFT'",
                  (PLANNER, PLANNER, o["id"]))
    write_history(C.cur, o["id"], "RELEASE", "DRAFT", "RELEASED", PLANNER, None)


def plan_for(w):
    start = w["planned_start"]
    code = "NGAY" if 6 <= start.hour < 18 else "DEM"
    plan_date = (start - timedelta(days=1)).date() if start.hour < 6 else start.date()
    plant = C.plant_code[w["machine_id"]]
    plan_code = f"PP-{plan_date:%Y%m%d}-{code}-{plant}"
    row = q1("SELECT id FROM production_plans WHERE plan_code=%s", (plan_code,))
    if row:
        return row["id"], C.shifts[code]
    C.cur.execute(
        """INSERT INTO production_plans (plan_code, plan_date, shift_id, factory_node_id, status, remark, confirmed_by, confirmed_at,
               published_by, published_at, created_by) VALUES (%s,%s,%s,%s,'PUBLISHED',%s,%s,NOW(),%s,NOW(),%s)""",
        (plan_code, plan_date, C.shifts[code], C.plant_of[w["machine_id"]], MARK, PLANNER, PLANNER, PLANNER))
    plan_id = C.cur.lastrowid
    write_audit(C.cur, "PLAN", plan_id, "CREATE", None, "DRAFT", PLANNER, plan_code)
    write_audit(C.cur, "PLAN", plan_id, "PUBLISH", "DRAFT", "PUBLISHED", PLANNER, None)
    return plan_id, C.shifts[code]


def next_wo_no():
    prefix = f"WO-{SIM['now']:%y%m%d}-"
    row = q1("SELECT wo_no FROM production_plan_details WHERE wo_no LIKE %s ORDER BY wo_no DESC LIMIT 1", (f"{prefix}%",))
    tail = row["wo_no"][len(prefix):] if row else ""
    return f"{prefix}{(int(tail) + 1 if tail.isdigit() else 1):03d}"


def ev_wo_create(w):
    o = w["order"]
    if not o["id"]:
        return
    plan_id, shift_id = plan_for(w)
    wo_no = next_wo_no()
    C.cur.execute(
        """INSERT INTO production_plan_details (wo_no, plan_id, production_order_id, product_id, mold_id, machine_id, material_id, planned_qty,
               cavity, cycle_time_sec, setup_minutes, part_weight_g, planned_start, planned_end, due_date, priority, source, source_ref,
               status, shift_id, created_by)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'SALES_ORDER',%s,'SCHEDULED',%s,%s)""",
        (wo_no, plan_id, o["id"], w["product_id"], w["mold_id"], w["machine_id"], C.resin_of.get(w["product_id"]), w["qty"], w["cavity"],
         w["cycle"], w["setup"], w["weight"], w["planned_start"], w["planned_end"], w["planned_end"] + timedelta(hours=4), o["priority"],
         o["order_no"], shift_id, PLANNER))
    w["id"] = C.cur.lastrowid
    w["wo_no"] = wo_no
    write_audit(C.cur, "WORK_ORDER", w["id"], "CREATE", None, "SCHEDULED", PLANNER, f"{o['order_no']} · {wo_no}")
    sync_work_order(C.cur, w["id"], PLANNER)
    sync_order_status(C.cur, o["id"], PLANNER)


# =========================================================
# MATERIAL
# =========================================================


def free_stock(material_id):
    row = q1("""SELECT COALESCE(SUM(current_qty - reserved_qty), 0) AS free FROM material_lots WHERE material_id=%s AND status='IN_STOCK'
                AND iqc_result IN ('PASS','NOT_REQUIRED') AND (expiry_date IS NULL OR expiry_date >= CURDATE())""", (material_id,))
    return float(row["free"])


def receive(material_id, need):
    m = q1("SELECT material_code, supplier, unit FROM materials WHERE id=%s", (material_id,))
    size = LOT_SIZE.get(material_id, 500)
    count = math.ceil(need / size) + (0 if material_id in PACKAGING else 1)
    today = SIM["now"].date()
    for _ in range(count):
        lot_no = next_lot_no(C.cur, "L")
        packaging = material_id in PACKAGING
        C.cur.execute(
            """INSERT INTO material_lots (lot_no, material_id, source, supplier, supplier_lot_no, received_at, manufacture_date, expiry_date,
                   initial_qty, current_qty, location_id, status, iqc_result, iqc_by, iqc_at, remark, created_by)
               VALUES (%s,%s,'RECEIVING',%s,%s,NOW(),%s,%s,%s,%s,1,'IN_STOCK',%s,%s,%s,%s,%s)""",
            (lot_no, material_id, m["supplier"], f"S{random.randint(100000, 999999)}", today - timedelta(days=random.randint(10, 40)),
             None if packaging else today + timedelta(days=365), size, size, "NOT_REQUIRED" if packaging else "PASS",
             None if packaging else QC, None if packaging else SIM["now"], MARK, STORE))
        lot_id = C.cur.lastrowid
        write_txn(C.cur, lot_id, "RECEIVING", size, size, "IN_STOCK" if packaging else "WAITING_IQC", STORE, to_location_id=1,
                  reference_no=f"GRN-{today:%y%m%d}", remark=f"Nhập kho {m['material_code']}")
        if not packaging:
            write_txn(C.cur, lot_id, "IQC_PASS", 0, size, "IN_STOCK", QC, remark="IQC đạt")


def ev_wo_release(w):
    if not w["id"]:
        return
    row = wo_row(w)
    if row["status"] != "SCHEDULED":
        return
    sync_work_order(C.cur, w["id"], PLANNER)
    for line in q("SELECT id FROM material_requirements WHERE work_order_id=%s AND status<>'CANCELLED'", (w["id"],)):
        state = allocation_service.requirement_state(C.cur, line["id"])
        free = free_stock(state["material_id"])
        if state["remaining_qty"] > free:
            receive(state["material_id"], state["remaining_qty"] - free)
    allocation_service.auto_allocate(C.cur, w["id"], STORE)
    wo_service.release(C.cur, w["id"], PLANNER, wo_row(w)["version"], "Phát hành theo kế hoạch ca")


def ev_wo_issue(w):
    if not w["id"]:
        return
    for a in q("SELECT id, version FROM material_allocations WHERE work_order_id=%s AND status='RESERVED' AND reserved_qty > 0", (w["id"],)):
        allocation_service.issue(C.cur, a["id"], None, None, STORE, a["version"])


def ev_return_material(w):
    for a in q("""SELECT id, version, issued_qty - returned_qty - consumed_qty AS left_qty FROM material_allocations
                  WHERE work_order_id=%s AND status='IN_USE'""", (w["id"],)):
        qty = round(float(a["left_qty"]), 3)
        if qty >= 0.001:
            allocation_service.return_material(C.cur, a["id"], qty, "Trả vật tư dư về kho", STORE, a["version"])


# =========================================================
# EXECUTION
# =========================================================


def _startable(w):
    return w["id"] and not w["started"] and wo_row(w)["status"] == "RELEASED"


def ev_wo_start(w):
    """Start when the machine is free and every earlier Work Order of the line has started; otherwise wait in the queue."""
    if not _startable(w):
        return
    line = C.lines[w["machine_id"]]
    if any(_startable(x) for x in line[:line.index(w)]):
        STATS["skipped"] += 1
        return
    busy = q1("""SELECT COUNT(*) AS n FROM production_plan_details WHERE machine_id=%s AND id<>%s AND status IN ('IN_PRODUCTION','ON_HOLD')""",
              (w["machine_id"], w["id"]))["n"]
    if busy:
        STATS["skipped"] += 1
        return
    execution.start(C.cur, w["id"], w["operator"], LEADER, w["operator"], wo_row(w)["version"])
    w["started"] = SIM["now"]
    w["last"] = SIM["now"]
    w["finishing"] = False
    for offset, minutes, code, cause, action in w["holds"]:
        at(SIM["now"] + timedelta(minutes=offset), ev_hold, w, minutes, code, cause, action)
    at(SIM["now"] + jitter(50, 70), ev_output, w, w["token"])
    schedule_qc(w)


def produce(w):
    """Report what the machine made since the last report; returns True when the planned quantity is reached."""
    row = wo_row(w)
    if row["status"] != "IN_PRODUCTION":
        return False
    elapsed = (SIM["now"] - w["last"]).total_seconds()
    shots = int(elapsed / (w["cycle"] * w["speed"]))
    pcs = shots * w["cavity"]
    if pcs <= 0:
        return False
    reject = min(int(round(pcs * w["reject_rate"] * random.uniform(0.3, 1.7))), pcs)
    good = pcs - reject
    remaining = w["qty"] - int(row["actual_qty"])
    done = good >= remaining
    if done:
        good = remaining + random.randint(0, w["cavity"] - 1)
        reject = min(reject, max(int(round(good * w["reject_rate"])), 0))
    code = random.choice(w["defects"]) if reject else None
    reason = q1("SELECT reason_name FROM reason_codes WHERE reason_code=%s", (code,))["reason_name"] if code else None
    execution.output(C.cur, w["id"], good, reject, C.ids.get(code), reason, w["operator"], row["version"])
    w["last"] = SIM["now"]
    return done


def ev_output(w, token):
    if token != w["token"] or w["done"] or w.get("finishing"):
        return
    if produce(w):
        w["finishing"] = True
        at(SIM["now"] + jitter(5, 15), ev_end, w)
        return
    row = wo_row(w)
    left = max(w["qty"] - int(row["actual_qty"]), 0)
    per_sec = w["cavity"] * (1 - w["reject_rate"]) / (w["cycle"] * w["speed"])
    finish = SIM["now"] + timedelta(seconds=left / per_sec + 30)
    at(min(SIM["now"] + jitter(52, 68), finish), ev_output, w, w["token"])


def ev_hold(w, minutes, code, cause, action):
    if w["done"] or w.get("finishing") or wo_row(w)["status"] != "IN_PRODUCTION":
        return
    if produce(w):
        w["finishing"] = True
        at(SIM["now"] + jitter(5, 15), ev_end, w)
        return
    reason = q1("SELECT reason_name FROM reason_codes WHERE reason_code=%s", (code,))["reason_name"]
    execution.hold(C.cur, w["id"], C.ids.get(code), reason, w["operator"], wo_row(w)["version"])
    w["token"] += 1
    at(SIM["now"] + timedelta(minutes=minutes), ev_resume, w, cause, action)


def ev_resume(w, cause, action):
    row = wo_row(w)
    if row["status"] != "ON_HOLD":
        return
    for dt in q("SELECT id FROM downtime_events WHERE work_order_id=%s AND status='OPEN'", (w["id"],)):
        machine_service.close_downtime(C.cur, dt["id"], LEADER, root_cause=cause, action_taken=action)
    execution.resume(C.cur, w["id"], w["operator"], wo_row(w)["version"], action)
    quality_service.sync_plans(C.cur)  # IPQC rounds stop while held; the next one is planned again on resume
    schedule_qc(w)
    w["last"] = SIM["now"]
    at(SIM["now"] + jitter(40, 65), ev_output, w, w["token"])


def ev_end(w):
    row = wo_row(w)
    if row["status"] not in ("IN_PRODUCTION", "ON_HOLD"):
        return
    execution.end(C.cur, w["id"], None, w["operator"], row["version"])
    w["done"] = True
    w["token"] += 1
    schedule_qc(w)
    # the next queued Work Order of the line starts after the mold change / setup
    line = C.lines[w["machine_id"]]
    waiting = next((x for x in line[line.index(w) + 1:] if _startable(x)), None)
    if waiting and waiting["planned_start"] <= SIM["now"] + timedelta(minutes=waiting["setup"]):
        at(SIM["now"] + timedelta(minutes=waiting["setup"] * random.uniform(0.8, 1.4)), ev_wo_start, waiting)
    at(SIM["now"] + jitter(25, 60), ev_return_material, w)
    if random.random() < 0.88:
        at(SIM["now"] + timedelta(hours=random.uniform(16, 30)), ev_wo_close, w)


def ev_wo_close(w):
    row = wo_row(w)
    if row["status"] != "COMPLETED":
        return
    ev_return_material(w)
    wo_service.close(C.cur, w["id"], PLANNER, wo_row(w)["version"], "Đã đối chiếu sản lượng và vật tư")
    o = w["order"]
    open_wos = q1("SELECT COUNT(*) AS n FROM production_plan_details WHERE production_order_id=%s AND status NOT IN ('CLOSED','CANCELLED')", (o["id"],))["n"]
    status = q1("SELECT status FROM production_orders WHERE id=%s", (o["id"],))["status"]
    if not open_wos and status == "COMPLETED" and random.random() < 0.8:
        at(SIM["now"] + timedelta(hours=random.uniform(2, 20)), ev_order_close, o)


def ev_order_close(o):
    row = q1("SELECT status FROM production_orders WHERE id=%s", (o["id"],))
    if row["status"] != "COMPLETED":
        return
    C.cur.execute("UPDATE production_orders SET status='CLOSED', closed_by=%s, closed_at=NOW(), updated_by=%s, version=version+1 WHERE id=%s",
                  (PLANNER, PLANNER, o["id"]))
    write_history(C.cur, o["id"], "CLOSE", "COMPLETED", "CLOSED", PLANNER, "Đã giao đủ hàng")


# =========================================================
# QUALITY
# =========================================================


def schedule_qc(w):
    for p in q("""SELECT id, inspection_type, planned_at FROM quality_inspection_plans
                  WHERE work_order_id=%s AND status IN ('NOT_STARTED','IN_PROGRESS')""", (w["id"],)):
        if p["id"] in w["qc_seen"]:
            continue
        w["qc_seen"].add(p["id"])
        base = max(p["planned_at"], SIM["now"])
        if p["inspection_type"] == "FAI":
            delay = jitter(8, 25)
        elif p["inspection_type"] == "FQC":
            delay = jitter(20, 90)
        else:
            delay = jitter(30, 55) if random.random() < 0.08 else jitter(0, 18)
        at(base + delay, ev_inspect, w, p["id"])


def sample_data(w, plan, chars):
    make_ng = random.random() < (w["qc_ng"] * (2.5 if plan["inspection_type"] == "FAI" else 1))
    bad_char = random.choice(chars)["id"] if make_ng and chars else None
    measurements = []
    for c in chars:
        if (c["characteristic_type"] or "VARIABLE") == "ATTRIBUTE":
            measurements.append({"characteristic_id": c["id"], "attr_result": "NG" if c["id"] == bad_char else "OK"})
            continue
        lsl = float(c["lsl"]) if c["lsl"] is not None else None
        usl = float(c["usl"]) if c["usl"] is not None else None
        nominal = float(c["nominal_value"]) if c["nominal_value"] is not None else ((lsl + usl) / 2 if lsl is not None and usl is not None else 0.0)
        if lsl is not None and usl is not None:
            sigma = (usl - lsl) / 9
            value = random.gauss(nominal + w["drift"] * sigma * 3, sigma)
            value = min(max(value, lsl + sigma * 0.3), usl - sigma * 0.3)
            if c["id"] == bad_char:
                value = usl + sigma * random.uniform(0.2, 1.2) if random.random() < 0.5 else lsl - sigma * random.uniform(0.2, 1.2)
        elif usl is not None:
            value = min(abs(random.gauss(usl * 0.3, usl * 0.15)), usl * 0.95)
            if c["id"] == bad_char:
                value = usl * random.uniform(1.05, 1.4)
        else:
            value = (lsl or 0) * random.uniform(1.3, 3.0)
            if c["id"] == bad_char:
                value = (lsl or 1) * random.uniform(0.6, 0.95)
        gauge = gauge_for(c["equipment_type_id"])
        measurements.append({"characteristic_id": c["id"], "value": round(value, 3), "equipment_id": gauge})
    data = {"measurements": measurements, "inspector": QC}
    if not chars:
        make_ng = random.random() < w["qc_ng"] * 1.5
        data["result"] = "NG" if make_ng else "OK"
    if make_ng:
        if bad_char and any(c["id"] == bad_char and (c["characteristic_type"] or "VARIABLE") == "VARIABLE" for c in chars):
            data["defect_code_id"] = C.ids["NG-009"]
        else:
            data["defect_code_id"] = C.ids[random.choice(w["defects"])]
    return data


def ev_inspect(w, plan_id):
    plan = q1("SELECT id, inspection_type, status, sample_size, standard_revision_id FROM quality_inspection_plans WHERE id=%s", (plan_id,))
    if not plan or plan["status"] not in ("NOT_STARTED", "IN_PROGRESS"):
        return
    chars = quality_service.characteristics(C.cur, plan["standard_revision_id"])
    done = q1("SELECT COUNT(*) AS n FROM quality_inspection_records WHERE plan_id=%s", (plan_id,))["n"]
    for _ in range(int(plan["sample_size"]) - int(done)):
        quality_service.record_sample(C.cur, plan_id, sample_data(w, plan, chars), QC)
    schedule_qc(w)


def ev_ng_review():
    now = SIM["now"]
    for n in q("""SELECT n.id, n.status, n.detected_at, n.source, n.ng_qty, rc.reason_code FROM quality_ng_records n
                  LEFT JOIN reason_codes rc ON rc.id=n.defect_code_id
                  WHERE n.status IN ('NEW','INVESTIGATING','CA_IN_PROGRESS','WAITING_VERIFICATION')"""):
        age = (now - n["detected_at"]).total_seconds() / 86400
        category, cause, action = ROOT_CAUSES.get(n["reason_code"], ROOT_CAUSES["NG-009"])
        if n["status"] == "NEW" and age > 0.2 and random.random() < 0.85:
            quality_service.ng_action(C.cur, n["id"], "START_INVESTIGATION",
                                      {"owner": random.choice([QC, LEADER]), "containment": "Cách ly hàng nghi ngờ, kiểm tra 100% lô đang chạy"}, QC)
        elif n["status"] == "INVESTIGATING" and age > 1 and random.random() < 0.7:
            quality_service.ng_action(C.cur, n["id"], "START_CA", {"root_cause_category": category, "root_cause": cause,
                                      "corrective_action": action, "preventive_action": "Bổ sung vào checklist kiểm tra đầu ca"}, QC)
        elif n["status"] == "CA_IN_PROGRESS" and age > 2 and random.random() < 0.7:
            production = n["source"] == "PRODUCTION"
            quality_service.ng_action(C.cur, n["id"], "SUBMIT_VERIFY", {"disposition": "SCRAP" if production else "SORT",
                                      "scrap_qty": int(n["ng_qty"]) if production else 0}, QC)
        elif n["status"] == "WAITING_VERIFICATION" and age > 3 and random.random() < 0.75:
            quality_service.ng_action(C.cur, n["id"], "VERIFY",
                                      {"verification_result": "Theo dõi 3 lần kiểm tra liên tiếp đều đạt"}, QC)


# =========================================================
# MAIN
# =========================================================


def finish(orders):
    """End state: open orders without a Work Order yet get one in the planning backlog."""
    for o in orders:
        if not o["id"]:
            continue
        row = q1("""SELECT po.status, (SELECT COUNT(*) FROM production_plan_details d WHERE d.production_order_id=po.id) AS n
                    FROM production_orders po WHERE po.id=%s""", (o["id"],))
        if row["status"] != "RELEASED" or row["n"]:
            continue
        w = o["wos"][0]
        qty = sum(x["qty"] for x in o["wos"])
        wo_no = next_wo_no()
        C.cur.execute(
            """INSERT INTO production_plan_details (wo_no, production_order_id, product_id, mold_id, material_id, planned_qty, cavity, cycle_time_sec,
                   setup_minutes, part_weight_g, due_date, priority, source, source_ref, backlog_reason, status, created_by)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'SALES_ORDER',%s,'Chờ xếp máy','UNSCHEDULED',%s)""",
            (wo_no, o["id"], w["product_id"], w["mold_id"], C.resin_of.get(w["product_id"]), qty, w["cavity"], w["cycle"], 40, w["weight"],
             o["wos"][-1]["planned_end"] + timedelta(hours=12), o["priority"], o["order_no"], PLANNER))
        wo_id = C.cur.lastrowid
        write_audit(C.cur, "WORK_ORDER", wo_id, "CREATE", None, "UNSCHEDULED", PLANNER, o["order_no"])
        sync_work_order(C.cur, wo_id, PLANNER)
    quality_service.sync_plans(C.cur)
    trace_service.sync_lots(C.cur)


def main():
    _freeze_app_modules()
    C.conn = get_vcc_plastics_connection()
    C.cur = C.conn.cursor(dictionary=True)
    try:
        if q1("SELECT id FROM production_orders WHERE remark=%s LIMIT 1", (MARK,)):
            print("Demo production history already exists; restore the backup before seeding again.")
            return
        SIM["now"] = START - timedelta(days=6)
        C.cur.execute("SET timestamp=%s", (SIM["now"].timestamp(),))
        load_context()
        orders = build_schedule()
        C.conn.commit()
        shown = 0
        while QUEUE:
            moment, _seq, fn, args = heapq.heappop(QUEUE)
            if moment > END:
                break
            SIM["now"] = max(moment.replace(microsecond=0), SIM["now"])
            C.cur.execute("SET timestamp=%s", (SIM["now"].timestamp(),))
            try:
                fn(*args)
                C.conn.commit()
                STATS["events"] += 1
                if STATS["events"] % 2000 == 0:
                    print(f"... {SIM['now']:%Y-%m-%d %H:%M}  {STATS['events']} events")
            except Exception as exc:  # keep simulating; a refused action is skipped like a user would
                C.conn.rollback()
                STATS["errors"] += 1
                if shown < 15:
                    shown += 1
                    print(f"[{SIM['now']:%m-%d %H:%M}] {fn.__name__} failed: {getattr(exc, 'detail', exc)}")
                    if not isinstance(exc, HTTPException):
                        traceback.print_exc()
        SIM["now"] = END
        C.cur.execute("SET timestamp=%s", (END.timestamp(),))
        finish(orders)
        C.conn.commit()
        C.cur.execute("SET timestamp=DEFAULT")
        print(f"Done: {STATS['events']} events, {STATS['skipped']} starts queued behind the machine, {STATS['errors']} refused actions.")
        for table in ("production_orders", "production_plans", "production_plan_details", "production_execution_logs", "downtime_events",
                      "material_lots", "material_allocations", "material_usages", "quality_inspection_plans", "quality_inspection_records",
                      "quality_ng_records", "product_lots", "mold_installations"):
            print(f"  {table}: {q1(f'SELECT COUNT(*) AS n FROM {table}')['n']}")
    finally:
        C.cur.close()
        C.conn.close()


if __name__ == "__main__":
    main()
