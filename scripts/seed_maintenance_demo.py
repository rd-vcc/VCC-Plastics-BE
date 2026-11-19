"""Demo data for Maintenance Management (6.1 - 6.7), woven into the demo production history.

Runs the three-week shop-floor simulation of ``seed_production_history_demo`` and lets the maintenance team work
alongside it, through the real services at their simulated moments, so machine status, downtime, mold status,
tool calibration, alarms and the maintenance timelines are written exactly as the screens write them:

- PM plans: weekly PM of the injection machines, robots and air compressor (done on Sundays, when the lines stop),
  monthly PM of the chiller, dryer, mold temperature controller, crusher and handling tools. The AUTO mold and
  calibration plans follow the molds' shot counters and the tools' calibration dates.
- Breakdowns: a heater alarm that stops a running Work Order raises a maintenance request (source ALARM) and a
  breakdown work order on the held downtime; a broken ejector pin is repaired on the machine; a power cut raises a
  critical alarm whose request is rejected. A mold gets its PM between two runs once it reaches its PM shot count.
- Alarms during production, parameter trend (last 48 h) and component status of the monitored equipment.
- The window ends now with open work in every state: IMM-B02 in monthly PM, IMM-A02 down waiting for a valve,
  MLD-AUTO-002 in repair (paused), a chiller inspection waiting for verification, an overdue calibration,
  requests in every status.

The user Work Order WO-261023-002 (IMM-B01, MLD-ELEC-001) is finished at its natural end, then IMM-B01 runs
MLD-ELEC-001 / MLD-PACK-002, IMM-D01 MLD-AUTO-001 / MLD-PACK-001. Like the production history it is refused once
the demo orders exist (restore the backup before seeding again).

    python -m scripts.seed_maintenance_demo
"""

import json
import math
import random
from datetime import datetime as real_datetime, timedelta

from fastapi import HTTPException

from app.core.database import get_vcc_plastics_connection
from app.modules.machine import service as machine
from app.modules.maintenance import service as ms
from app.modules.mold import service as mold_service
from app.modules.production.execution import service as execution
from scripts import seed_production_history_demo as base
from scripts.seed_production_history_demo import C, SIM, at, q, q1

rnd = random.Random(20261029)

SUP = "Ngô Sỹ Hùng (00842)"
CUONG = "Lưu Hùng Cường (00001)"
THAO = "Hoàng Minh Thảo (00034)"
TUAN = "Nguyễn Quang Tuấn (00863)"
QA = "Lý Minh Quang (00465)"
NAME = {"00842": SUP, "00001": CUONG, "00034": THAO, "00863": TUAN, "00465": QA}
LEADER = "00842"
PLANNING = "Kế hoạch bảo trì"

START, END = base.START, base.END
LINES = [(9, [6, 2]), (10, [5, 3]), (5, [4, 8])]

EQ, MOLD, TOOL, LOC, PLAN, PRE = {}, {}, {}, {}, {}, {}
STATS = {"errors": 0}

CHECKLIST = {
    "MACHINE": ["Kiểm tra mức dầu thủy lực và rò rỉ", "Bôi trơn khớp khuỷu / tie bar", "Kiểm tra điện trở gia nhiệt và cặp nhiệt",
                "Kiểm tra khóa liên động cửa an toàn và nút dừng khẩn", "Vệ sinh lọc dầu / lọc gió tủ điện"],
    "OVERHAUL": ["Tháo kiểm tra trục vít, xi lanh, van một chiều", "Đo độ mòn trục vít và xi lanh", "Thay dầu thủy lực và lọc dầu",
                 "Kiểm tra bơm, van tỷ lệ, áp suất hệ thủy lực", "Kiểm tra độ song song tấm khuôn", "Chạy thử và ghi nhận thông số"],
    "INSPECTION": ["Kiểm tra ngoại quan", "Đo các thông số chính", "Ghi nhận kết quả và đề xuất"],
    "ROBOT": ["Vệ sinh thân robot và ray trượt", "Bôi mỡ các khớp trục X / Y / Z", "Kiểm tra giác hút và đường khí", "Chạy thử chương trình gắp"],
    "AIR": ["Xả nước ngưng bình chứa", "Vệ sinh lọc gió đầu hút", "Kiểm tra mức dầu máy nén", "Kiểm tra áp suất làm việc"],
    "CHILLER": ["Vệ sinh lọc nước", "Vệ sinh dàn ngưng", "Kiểm tra dòng điện máy nén / bơm", "Kiểm tra nhiệt độ nước cấp / hồi"],
    "DRYER": ["Vệ sinh lọc gió", "Kiểm tra điện trở sấy", "Kiểm tra quạt thổi và cảm biến nhiệt", "Chạy thử, đo nhiệt độ sấy"],
    "MTC": ["Vệ sinh bồn chứa môi chất", "Kiểm tra bơm tuần hoàn", "Kiểm tra rò rỉ khớp nối nhanh", "Kiểm tra cảm biến nhiệt"],
    "CRUSHER": ["Kiểm tra độ sắc dao nghiền", "Siết lại bu lông dao", "Vệ sinh buồng nghiền và lưới sàng", "Kiểm tra công tắc an toàn nắp"],
    "TOOL": ["Vệ sinh và kiểm tra ngoại quan", "Kiểm tra độ mòn, độ chắc chắn", "Bôi trơn bộ phận chuyển động", "Chạy thử"],
    "REPAIR": ["Xác định nguyên nhân hư hỏng", "Sửa chữa / thay thế chi tiết", "Chạy thử sau sửa chữa"],
    "MOLD_PM": ["Vệ sinh mặt phân khuôn và rãnh thoát khí", "Kiểm tra kênh làm mát (lưu lượng / rò rỉ)", "Kiểm tra chốt đẩy và lò xo hồi",
                "Bôi trơn chốt dẫn hướng, bạc, slide", "Kiểm tra điện trở và cặp nhiệt hot runner", "Chạy thử và kiểm tra mẫu đầu"],
    "MOLD_REPAIR": ["Tháo khuôn, xác định vị trí hư hỏng", "Sửa chữa / thay chi tiết hư hỏng", "Lắp lại và kiểm tra độ khít", "Chạy thử và kiểm tra mẫu đầu"],
    "CALIBRATION": ["Vệ sinh và kiểm tra ngoại quan", "Hiệu chuẩn với chuẩn tham chiếu", "Ghi nhận giá trị đo", "Dán tem hiệu chuẩn"],
}


def part(code, name, qty, unit, cost):
    return {"part_code": code, "part_name": name, "qty": qty, "unit": unit, "unit_cost": cost}


GREASE = part("GR-EP2", "Mỡ bôi trơn EP2", 1, "hộp", 180000)
OIL_FILTER = part("FLT-OIL", "Lõi lọc dầu thủy lực", 1, "cái", 650000)

# key, asset type, code, task, WO type, interval (days), first due, technician, est minutes, priority, checklist, spare parts, result
PLANS = [
    ("W-IMM-D01", "MACHINE", "IMM-D01", "Bảo dưỡng tuần máy ép: dầu thủy lực, bôi trơn, an toàn", "PM", 7, "S1", CUONG, 120, "MEDIUM", "MACHINE", [GREASE],
     "Đã bôi trơn, kiểm tra dầu và an toàn đạt"),
    ("W-IMM-D02", "MACHINE", "IMM-D02", "Bảo dưỡng tuần máy ép: dầu thủy lực, bôi trơn, an toàn", "PM", 7, "S1", THAO, 120, "MEDIUM", "MACHINE", [GREASE],
     "Đã bôi trơn, kiểm tra dầu và an toàn đạt"),
    ("W-IMM-B01", "MACHINE", "IMM-B01", "Bảo dưỡng tuần máy ép: dầu thủy lực, bôi trơn, an toàn", "PM", 7, "S1", TUAN, 120, "MEDIUM", "MACHINE", [GREASE],
     "Đã bôi trơn, kiểm tra dầu và an toàn đạt"),
    ("W-IMM-A01", "MACHINE", "IMM-A01", "Bảo dưỡng tuần máy ép: dầu thủy lực, bôi trơn, an toàn", "PM", 7, "S1", CUONG, 120, "MEDIUM", "MACHINE", [GREASE],
     "Đã bôi trơn, kiểm tra dầu và an toàn đạt"),
    ("W-IMM-A02", "MACHINE", "IMM-A02", "Bảo dưỡng tuần máy ép: dầu thủy lực, bôi trơn, an toàn", "PM", 7, "S1", THAO, 120, "MEDIUM", "MACHINE", [GREASE],
     "Đã bôi trơn, kiểm tra dầu và an toàn đạt"),
    ("W-IMM-B02", "MACHINE", "IMM-B02", "Bảo dưỡng tuần máy ép: dầu thủy lực, bôi trơn, an toàn", "PM", 7, "S1", TUAN, 120, "MEDIUM", "MACHINE", [GREASE],
     "Đã bôi trơn, kiểm tra dầu và an toàn đạt"),
    ("W-RB-A01", "EQUIPMENT", "RB-A01", "Bôi mỡ khớp robot, kiểm tra giác hút", "PM", 7, "S1", TUAN, 45, "LOW", "ROBOT", [],
     "Đã bôi mỡ các khớp, giác hút hoạt động tốt"),
    ("W-RB-B01", "EQUIPMENT", "RB-B01", "Bôi mỡ khớp robot, kiểm tra giác hút", "PM", 7, "S1", TUAN, 45, "LOW", "ROBOT", [],
     "Đã bôi mỡ các khớp, giác hút hoạt động tốt"),
    ("W-AC-01", "EQUIPMENT", "AC-01", "Xả nước ngưng, vệ sinh lọc gió máy nén khí", "PM", 7, "S1", THAO, 40, "MEDIUM", "AIR", [],
     "Đã xả nước ngưng, vệ sinh lọc gió, áp suất 7,5 bar"),
    ("M-IMM-B02", "MACHINE", "IMM-B02", "Bảo dưỡng tổng thể cụm phun & trục vít", "PM", 30, "END", CUONG, 480, "HIGH", "OVERHAUL",
     [part("HO-46", "Dầu thủy lực ISO VG46", 20, "lít", 95000), OIL_FILTER, part("CHK-RING", "Bộ vòng chặn trục vít", 1, "bộ", 2350000)], None),
    ("Q-IMM-A01", "MACHINE", "IMM-A01", "Kiểm tra độ song song tấm khuôn & tie bar", "INSPECTION", 90, "END+20", CUONG, 180, "MEDIUM", "INSPECTION", [], None),
    ("M-CHL-01", "EQUIPMENT", "CHL-01", "Vệ sinh lọc nước & dàn ngưng chiller", "PM", 30, "START+6", THAO, 120, "MEDIUM", "CHILLER",
     [part("FLT-W", "Lõi lọc nước 10 inch", 2, "cái", 420000)], "Đã vệ sinh, nhiệt độ nước cấp 12 °C"),
    ("M-DRY-A01", "EQUIPMENT", "DRY-A01", "Vệ sinh lọc gió & kiểm tra điện trở máy sấy", "PM", 30, "END+1", None, 60, "MEDIUM", "DRYER", [], None),
    ("M-MTC-B01", "EQUIPMENT", "MTC-B01", "Vệ sinh bồn & kiểm tra bơm máy điều nhiệt khuôn", "PM", 30, "START+9", TUAN, 60, "MEDIUM", "MTC", [],
     "Đã vệ sinh bồn, bơm chạy êm, không rò rỉ"),
    ("M-CRS-D01", "EQUIPMENT", "CRS-D01", "Kiểm tra & mài dao máy nghiền", "PM", 30, "START+13", CUONG, 90, "LOW", "CRUSHER",
     [part("BLT-M12", "Bu lông dao M12 cấp 10.9", 8, "cái", 25000)], "Đã mài dao, siết lại bu lông"),
    ("M-HDL-001", "TOOL", "TL-HDL-001", "Bảo dưỡng xe đẩy khuôn thủy lực", "PM", 30, "END", TUAN, 60, "MEDIUM", "TOOL", [], None),
    ("Q-HDL-002", "TOOL", "TL-HDL-002", "Kiểm tra an toàn xe đẩy thùng thành phẩm", "INSPECTION", 90, "START+3", TUAN, 45, "LOW", "INSPECTION", [],
     "Bánh xe, phanh và tay đẩy đạt"),
]
RESULT = {p[0]: p[12] for p in PLANS}
WEEKLY = ["W-IMM-D01", "W-IMM-D02", "W-IMM-B01", "W-IMM-A01", "W-IMM-A02", "W-IMM-B02", "W-RB-A01", "W-RB-B01", "W-AC-01"]

# mold code, shot counter at the start, shots at the last PM, days since the last PM
MOLD_SHOTS = [
    ("MLD-PACK-001", 1465000, 1250000, 48), ("MLD-HOME-001", 285000, 200000, 70), ("MLD-ELEC-001", 398000, 320000, 55),
    ("MLD-MOTO-001", 145000, 100000, 40), ("MLD-AUTO-001", 232000, 180000, 62), ("MLD-AUTO-002", 268000, 240000, 35),
    ("MLD-PACK-002", 590000, 400000, 66), ("MLD-HOME-OLD", 486000, None, None),
]
# tool code, days from the start of the window to the last calibration (negative = before)
TOOL_CALIBRATION = [("TL-MEA-001", -170), ("TL-MEA-002", None), ("TL-MEA-003", -355), ("TL-ASM-001", None)]

MINOR_ALARMS = {
    "MACHINE": [("E-HOP-01", "Mức nhựa trong phễu cấp liệu thấp", "LOW"), ("E-LUB-02", "Áp suất bôi trơn thấp", "MEDIUM"),
                ("E-ROB-12", "Áp suất chân không giác hút robot thấp", "MEDIUM"), ("E-CW-04", "Lưu lượng nước làm mát khuôn thấp", "MEDIUM"),
                ("E-SAFE-01", "Cửa an toàn mở trong chu kỳ", "HIGH"), ("E-COM-01", "Mất kết nối truyền thông PLC", "LOW"),
                ("E-HYD-01", "Nhiệt độ dầu thủy lực cao", "MEDIUM")],
    "UTILITY": [("E-CHL-02", "Nhiệt độ nước cấp cao hơn cài đặt", "MEDIUM"), ("E-AC-03", "Nhiệt độ dầu máy nén cao", "MEDIUM"),
                ("E-PWR-02", "Dòng điện động cơ vượt định mức", "HIGH")],
    "ROBOT": [("E-ROB-12", "Áp suất chân không giác hút thấp", "MEDIUM"), ("E-ROB-05", "Sai lệch vị trí trục Y", "LOW")],
    "AUXILIARY": [("E-DRY-01", "Nhiệt độ sấy thấp hơn cài đặt", "LOW"), ("E-MTC-02", "Mức môi chất trong bồn thấp", "MEDIUM")],
}


class Busy(Exception):
    pass


def d(spec):
    """Due date from a plan spec: S1 (first Sunday), END, END+n, START+n."""
    if spec == "S1":
        return SUNDAYS[0].date()
    anchor, _, days = spec.partition("+")
    return ((START if anchor == "START" else END) + timedelta(days=int(days or 0))).date()


SUNDAYS = []
day = START.replace(hour=0, minute=0)
while day < END:
    if day.weekday() == 6:
        SUNDAYS.append(day)
    day += timedelta(days=1)


def mins(lo, hi):
    return timedelta(minutes=rnd.uniform(lo, hi))


def guarded(fn, *args):
    """Run a maintenance side step inside a production event; a failure only drops the side step."""
    C.cur.execute("SAVEPOINT demo_maintenance")
    try:
        return fn(*args)
    except Exception as exc:  # noqa: BLE001 - the production event itself must go on
        C.cur.execute("ROLLBACK TO SAVEPOINT demo_maintenance")
        STATS["errors"] += 1
        print(f"[{SIM['now']:%m-%d %H:%M}] {fn.__name__} skipped: {getattr(exc, 'detail', exc)}")
        return None


# =========================================================
# SETUP
# =========================================================


def setup():
    EQ.update({r["equipment_code"]: r["id"] for r in q("SELECT id, equipment_code FROM equipment")})
    MOLD.update({r["mold_code"]: r["id"] for r in q("SELECT id, mold_code FROM molds")})
    TOOL.update({r["tool_code"]: r["id"] for r in q("SELECT id, tool_code FROM production_tools")})
    LOC.update({r["node_code"]: r["id"] for r in q("SELECT id, node_code FROM mold_location_nodes")})
    now = SIM["now"]

    # status history starts with the current state for equipment that has none yet
    for r in q("""SELECT e.id, e.operational_status FROM equipment e WHERE e.asset_status='ACTIVE'
                  AND NOT EXISTS (SELECT 1 FROM machine_status_events s WHERE s.equipment_id=e.id)"""):
        C.cur.execute("INSERT INTO machine_status_events (equipment_id, status, started_at, source, remark) VALUES (%s,%s,%s,'MES',%s)",
                      (r["id"], r["operational_status"], now, "Trạng thái ban đầu"))

    # accumulated shot counters (molds are not new) and their last PM
    for code, shot, pm_shot, pm_days in MOLD_SHOTS:
        mid = MOLD[code]
        if code == "MLD-ELEC-001":  # running WO-261023-002 since 23/10: its installations follow the counter
            shot = _elec_counter(mid, shot)
        else:
            C.cur.execute("UPDATE molds SET current_shot=%s WHERE id=%s", (shot, mid))
        mold_service.log_event(C.cur, mid, "SHOT_ADJUST", SUP, shot_before=0, shot_after=shot, reason="Nhập số shot tích lũy từ sổ theo dõi khuôn")
        if pm_shot:
            pm_at = (now - timedelta(days=pm_days)).replace(hour=14, minute=0)
            C.cur.execute("UPDATE molds SET last_pm_shot=%s, last_pm_at=%s WHERE id=%s", (pm_shot, pm_at, mid))
            mold_service.log_event(C.cur, mid, "MAINTENANCE_DONE", THAO, from_status="IN_MAINTENANCE", to_status="AVAILABLE", maintenance_type="PM_SHOT",
                                   technician=THAO, downtime_hours=3.5, reason="Bảo dưỡng định kỳ theo số shot", event_at=pm_at)

    for code, days in TOOL_CALIBRATION:
        t = q1("SELECT id, calibration_interval_days AS n FROM production_tools WHERE tool_code=%s", (code,))
        last = (START + timedelta(days=days)).date() if days is not None else None
        if code == "TL-MEA-002":
            last = (END - timedelta(days=185)).date()
        if code == "TL-ASM-001":
            last = (END - timedelta(days=340)).date()
        C.cur.execute("UPDATE production_tools SET last_calibration_date=%s, calibration_due_date=%s WHERE id=%s",
                      (last, last + timedelta(days=int(t["n"] or 365)), t["id"]))

    ms.sync_auto_plans(C.cur)
    C.cur.execute("UPDATE maintenance_plans SET checklist=%s, technician=%s WHERE source='AUTO_MOLD'", (json.dumps(CHECKLIST["MOLD_PM"], ensure_ascii=False), None))
    C.cur.execute("UPDATE maintenance_plans SET checklist=%s, technician=%s WHERE source='AUTO_CALIBRATION'", (json.dumps(CHECKLIST["CALIBRATION"], ensure_ascii=False), QA))
    for r in q("SELECT p.id, t.tool_code FROM maintenance_plans p JOIN production_tools t ON t.id=p.asset_id WHERE p.source='AUTO_CALIBRATION'"):
        PLAN[f"CAL-{r['tool_code']}"] = r["id"]
    for r in q("SELECT p.id, m.mold_code FROM maintenance_plans p JOIN molds m ON m.id=p.asset_id WHERE p.source='AUTO_MOLD'"):
        PLAN[f"MOLD-{r['mold_code']}"] = r["id"]

    for key, asset_type, code, task, wo_type, interval, due, tech, est, priority, checklist, parts, _result in PLANS:
        asset_id = TOOL[code] if asset_type == "TOOL" else EQ[code]
        PLAN[key] = ms.save_plan(C.cur, {"asset_type": asset_type, "asset_id": asset_id, "task_name": task, "wo_type": wo_type, "interval_value": interval,
                                         "next_due_date": d(due), "priority": priority, "technician": tech, "est_minutes": est,
                                         "checklist": CHECKLIST[checklist], "spare_parts": parts}, SUP)


def _elec_counter(mid, shot):
    """MLD-ELEC-001 already ran WO-261023-001 / -002 on IMM-B01: shift the recorded counters onto the accumulated one."""
    first = q1("SELECT id, shots_at_install FROM mold_installations WHERE mold_id=%s ORDER BY installed_at LIMIT 1", (mid,))
    done = q1("SELECT actual_qty + reject_qty AS pcs FROM production_plan_details WHERE wo_no='WO-261023-001'")
    used = math.ceil(int(done["pcs"] or 0) / 4) if done else 0
    if first:
        C.cur.execute("""UPDATE mold_installations SET shots_at_install=shots_at_install+%s,
                         shots_at_remove=IF(shots_at_remove IS NULL, NULL, shots_at_remove+%s+%s) WHERE mold_id=%s""", (shot, shot, used, mid))
        C.cur.execute("UPDATE mold_installations SET shots_at_install=%s WHERE mold_id=%s AND id<>%s", (shot + used, mid, first["id"]))
    C.cur.execute("UPDATE molds SET current_shot=%s WHERE id=%s", (shot + used, mid))
    return shot + used


# =========================================================
# HELPERS
# =========================================================


def set_checklist(wo_id, items):
    C.cur.execute("DELETE FROM maintenance_wo_checklist WHERE wo_id=%s", (wo_id,))
    for i, item in enumerate(items, 1):
        C.cur.execute("INSERT INTO maintenance_wo_checklist (wo_id, seq, item) VALUES (%s,%s,%s)", (wo_id, i, item))


def tick_checklist(wo_id, count=None):
    """Mark the open checklist items OK (all, or the first ``count``), spread over the work so far."""
    wo = ms.get_wo(C.cur, wo_id)
    tech = wo["technician"] or SUP
    items = q("SELECT id FROM maintenance_wo_checklist WHERE wo_id=%s AND result IS NULL ORDER BY seq", (wo_id,))
    items = items[:count] if count is not None else items
    start = wo["actual_start"] or SIM["now"]
    step = (SIM["now"] - start) / (len(items) + 1) if items else timedelta(0)
    for i, it in enumerate(items, 1):
        C.cur.execute("UPDATE maintenance_wo_checklist SET result='OK', done_by=%s, done_at=%s WHERE id=%s", (tech, start + step * i, it["id"]))


def start_wo(wo_id, reason=None, category="PLANNED", status="MAINTENANCE"):
    """Start a work order; a machine / equipment first gets its downtime with the maintenance reason code."""
    wo = ms.get_wo(C.cur, wo_id)
    tech = wo["technician"] or SUP
    if wo["asset_type"] in ("MACHINE", "EQUIPMENT"):
        if machine.running_work_order(C.cur, wo["asset_id"]):
            raise Busy()
        if not q1("SELECT id FROM downtime_events WHERE equipment_id=%s AND status='OPEN' LIMIT 1", (wo["asset_id"],)):
            PRE[wo_id] = q1("SELECT operational_status AS s FROM equipment WHERE id=%s", (wo["asset_id"],))["s"]
            text = f"{wo['wo_no']} · {wo['task_name']}"
            machine.open_downtime(C.cur, wo["asset_id"], tech, C.ids.get(reason), text, category=category, source="MES", set_status=False)
            machine.set_machine_status(C.cur, wo["asset_id"], status, "MES", None, text)
    ms.wo_action(C.cur, wo_id, "START", tech)


def finish_wo(wo_id, f):
    wo = ms.get_wo(C.cur, wo_id)
    tech = wo["technician"] or SUP
    tick_checklist(wo_id)
    for p in f.get("parts") or []:
        ms.add_part(C.cur, wo_id, p, tech)
    C.cur.execute("UPDATE maintenance_work_orders SET result=%s, root_cause=%s, calibration_result=%s, calibration_value=%s WHERE id=%s",
                  (f.get("result"), f.get("root_cause"), f.get("calibration"), f.get("calibration_value"), wo_id))
    if wo["asset_type"] == "MOLD":
        for comp in ms.MOLD_COMPONENTS:
            cond, act, note = f.get("components", {}).get(comp, ("GOOD", "CLEAN" if comp in ("VENT", "COOLING_CHANNEL") else "NONE", None))
            ms.set_component(C.cur, wo_id, comp, cond, act, note, tech)
    ms.wo_action(C.cur, wo_id, "SUBMIT", tech)
    if wo_id in PRE and PRE[wo_id] == "RUNNING":  # utilities / robots run on after their maintenance
        machine.set_machine_status(C.cur, wo["asset_id"], "RUNNING", "MES", None, "Vận hành lại sau bảo trì")
    if f.get("alarm"):
        alarm_do(f["alarm"], "clear", "PLC")


def alarm_do(alarm_id, action, actor, note=None):
    a = q1("SELECT status, version FROM machine_alarms WHERE id=%s", (alarm_id,))
    if not a:
        return
    allowed = {"acknowledge": {"ACTIVE", "SHELVED"}, "clear": set(machine.ALARM_OPEN), "close": {"ACTIVE", "ACKNOWLEDGED", "SHELVED", "CLEARED"}}[action]
    if a["status"] in allowed:
        machine.alarm_action(C.cur, alarm_id, action, actor, a["version"], note)


def new_request(asset_type, asset_id, short, by, *, rtype="CORRECTIVE", priority="MEDIUM", dept="Sản xuất", desc=None, source="MANUAL", **links):
    return ms.create_request(C.cur, {"asset_type": asset_type, "asset_id": asset_id, "request_type": rtype, "priority": priority, "short_description": short,
                                     "description": desc, "requested_by": by, "department": dept, "required_date": SIM["now"] + timedelta(hours=8), **links},
                             by, source=source)


def request_to_wo(rid, wo_type, task, tech, minutes, checklist="REPAIR", planned_start=None):
    ms.request_action(C.cur, rid, "REVIEW", SUP)
    start = planned_start or SIM["now"] + timedelta(minutes=10)
    wo_id = ms.wo_from_request(C.cur, rid, {"wo_type": wo_type, "task_name": task, "technician": tech, "planned_start": start,
                                            "planned_finish": start + timedelta(minutes=minutes), "est_minutes": int(minutes)}, SUP)
    set_checklist(wo_id, CHECKLIST[checklist])
    return wo_id


# =========================================================
# EVENTS
# =========================================================


def ev_plan_wo(key, start, finish=None, reason="MT-001", partial=None, stage=None):
    wo_id = ms.wo_from_plan(C.cur, PLAN[key], {"planned_start": start}, SUP)
    at(start, ev_wo_start, wo_id, reason, finish, partial, stage)


def ev_plan_wo_only(key, start):
    ms.wo_from_plan(C.cur, PLAN[key], {"planned_start": start}, SUP)


def ev_wo_start(wo_id, reason, finish=None, partial=None, stage=None, attempt=0):
    if ms.get_wo(C.cur, wo_id)["status"] not in ("RELEASED", "ASSIGNED"):
        return
    try:
        start_wo(wo_id, reason)
    except Busy:
        if attempt < 4 and SIM["now"].hour < 18:  # a Work Order still runs on the machine: try again later
            at(SIM["now"] + timedelta(hours=2), ev_wo_start, wo_id, reason, finish, partial, stage, attempt + 1)
        return
    if stage:
        ms.wo_action(C.cur, wo_id, "STAGE", ms.get_wo(C.cur, wo_id)["technician"], stage=stage)
    if partial:
        at(SIM["now"] + timedelta(minutes=partial[1]), ev_partial, wo_id, partial[0])
    if finish:
        if finish.get("stages"):
            minutes = finish["minutes"]
            for share, name in ((0.05, "DISASSEMBLY"), (0.25, "INSPECTION"), (0.45, "CLEANING_REPAIR"), (0.75, "ASSEMBLY"), (0.9, "TESTING")):
                at(SIM["now"] + timedelta(minutes=minutes * share), ev_stage, wo_id, name)
        at(SIM["now"] + timedelta(minutes=finish["minutes"]), ev_wo_finish, wo_id, finish)


def ev_partial(wo_id, count):
    if ms.get_wo(C.cur, wo_id)["status"] == "IN_PROGRESS":
        tick_checklist(wo_id, count)


def ev_stage(wo_id, stage):
    wo = ms.get_wo(C.cur, wo_id)
    if wo["status"] in ("IN_PROGRESS", "PAUSED"):
        ms.wo_action(C.cur, wo_id, "STAGE", wo["technician"], stage=stage)


def ev_wo_finish(wo_id, f):
    if ms.get_wo(C.cur, wo_id)["status"] not in ("IN_PROGRESS", "PAUSED"):
        return
    finish_wo(wo_id, f)
    if f.get("verify") is not False:
        at(SIM["now"] + timedelta(minutes=f.get("verify") or rnd.uniform(20, 60)), ev_wo_verify, wo_id, f)


def ev_wo_verify(wo_id, f):
    if ms.get_wo(C.cur, wo_id)["status"] != "VERIFICATION":
        return
    ms.wo_action(C.cur, wo_id, "VERIFY", SUP, "Đã kiểm tra, đạt yêu cầu")
    if f.get("alarm"):
        alarm_do(f["alarm"], "close", LEADER, "Đã xử lý theo lệnh bảo trì")
    if f.get("close", True):
        at(SIM["now"] + timedelta(hours=rnd.uniform(3, 20)), ev_wo_close, wo_id)


def ev_wo_close(wo_id):
    if ms.get_wo(C.cur, wo_id)["status"] == "COMPLETED":
        ms.wo_action(C.cur, wo_id, "CLOSE", SUP, "Đã đối chiếu chi phí và vật tư")


def ev_corrective(spec):
    """Request (optionally raised from an alarm) -> review -> work order -> start / finish."""
    asset_id = MOLD[spec["code"]] if spec["asset"] == "MOLD" else TOOL[spec["code"]] if spec["asset"] == "TOOL" else EQ[spec["code"]]
    alarm_id = None
    if spec.get("alarm"):
        code, msg, prio = spec["alarm"]
        alarm_id = machine.raise_alarm(C.cur, asset_id, msg, prio, "PLC", code)
        at(SIM["now"] + mins(2, 6), ev_alarm, alarm_id, "acknowledge", LEADER)
    rid = new_request(spec["asset"], asset_id, spec["short"], spec["by"], priority=spec.get("priority", "HIGH"), dept=spec.get("dept", "Sản xuất"),
                      desc=spec.get("desc"), source="ALARM" if alarm_id else "MANUAL", alarm_id=alarm_id)
    at(SIM["now"] + mins(15, 30), ev_corrective_wo, rid, spec, alarm_id)


def ev_corrective_wo(rid, spec, alarm_id):
    wo_id = request_to_wo(rid, spec.get("wo_type", "CORRECTIVE"), spec["task"], spec["tech"], spec["minutes"],
                          "MOLD_REPAIR" if spec["asset"] == "MOLD" else "REPAIR")
    f = {"minutes": spec["minutes"], "result": spec.get("result"), "root_cause": spec.get("root_cause"), "parts": spec.get("parts"),
         "alarm": alarm_id, "verify": spec.get("verify"), "close": spec.get("close", True), "components": spec.get("components", {})}
    if spec.get("open"):  # assigned, starts later
        return
    at(SIM["now"] + mins(10, 25), ev_wo_start, wo_id, spec.get("reason"), f, spec.get("partial"), spec.get("stage"))
    if spec.get("pause"):
        at(SIM["now"] + timedelta(minutes=spec["pause"][0]), ev_pause, wo_id, spec["pause"][1])


def ev_pause(wo_id, note):
    wo = ms.get_wo(C.cur, wo_id)
    if wo["status"] == "IN_PROGRESS":
        ms.wo_action(C.cur, wo_id, "PAUSE", wo["technician"], note)


def ev_request(asset, code, short, by, final, note=None, kw=None):
    kw = kw or {}
    asset_id = MOLD[code] if asset == "MOLD" else TOOL[code] if asset == "TOOL" else EQ[code]
    rid = new_request(asset, asset_id, short, by, **kw)
    if final in ("IN_REVIEW", "APPROVED", "REJECTED"):
        ms.request_action(C.cur, rid, "REVIEW", SUP)
    if final in ("APPROVED", "REJECTED", "CANCELLED"):
        ms.request_action(C.cur, rid, {"APPROVED": "APPROVE", "REJECTED": "REJECT", "CANCELLED": "CANCEL"}[final], SUP, note)
    return rid


def at_req(when, asset, code, short, by, final, note=None, **kw):
    at(when, ev_request, asset, code, short, by, final, note, kw)


def ev_cancelled_wo(code, short, task):
    rid = ev_request("EQUIPMENT", code, short, CUONG, "NEW", kw={"priority": "LOW", "dept": "Bảo trì"})
    wo_id = request_to_wo(rid, "INSPECTION", task, CUONG, 60, "INSPECTION", SIM["now"] + timedelta(days=2))
    at(SIM["now"] + timedelta(hours=20), ev_cancel_wo, wo_id, "Gộp vào kế hoạch bảo dưỡng tháng của máy nghiền")


def ev_cancel_wo(wo_id, note):
    ms.wo_action(C.cur, wo_id, "CANCEL", SUP, note)


def ev_alarm(alarm_id, action, actor, note=None):
    alarm_do(alarm_id, action, actor, note)


def ev_alarm_clear(equipment_id, code):
    machine.auto_clear(C.cur, equipment_id, code, "PLC")


def ev_minor_alarm(equipment_id, category):
    if q1("SELECT operational_status AS s FROM equipment WHERE id=%s", (equipment_id,))["s"] != "RUNNING":
        return
    code, msg, prio = rnd.choice(MINOR_ALARMS[category])
    alarm_id = machine.raise_alarm(C.cur, equipment_id, msg, prio, "PLC", code)
    if rnd.random() < 0.7:
        at(SIM["now"] + mins(1, 8), ev_alarm, alarm_id, "acknowledge", LEADER)
    at(SIM["now"] + mins(4, 30), ev_alarm_clear, equipment_id, code)
    at(SIM["now"] + mins(60, 240), ev_alarm, alarm_id, "close", LEADER, "Đã kiểm tra, thiết bị hoạt động bình thường")


def ev_finish_user_wo(wo_no):
    """The user Work Order still running on IMM-B01 reaches its quantity and ends."""
    row = q1("SELECT id, status, version, planned_qty, actual_qty FROM production_plan_details WHERE wo_no=%s", (wo_no,))
    if not row or row["status"] != "IN_PRODUCTION":
        return
    reason = q1("SELECT reason_name FROM reason_codes WHERE reason_code='NG-001'")["reason_name"]
    good = max(int(row["planned_qty"]) - int(row["actual_qty"]), 0) + 2
    execution.output(C.cur, row["id"], good, 14, C.ids["NG-001"], reason, "00863", row["version"])
    execution.end(C.cur, row["id"], None, "00863", base.wo_row(row)["version"])


def ev_mold_repair_now():
    """MLD-AUTO-002: core insert cracked, repair waiting for the spare insert."""
    mid = MOLD["MLD-AUTO-002"]
    mold_service.move(C.cur, mid, LOC["RP-01"], THAO, "Chuyển khu sửa chữa khuôn")
    rid = new_request("MOLD", mid, "Insert lõi #2 bị nứt", THAO, priority="HIGH", dept="Khuôn mẫu",
                      desc="Phát hiện vết nứt 12 mm trên insert lõi #2 khi kiểm tra trước khi lắp máy.")
    wo_id = request_to_wo(rid, "CORRECTIVE", "Thay insert lõi #2 bị nứt", THAO, 960, "MOLD_REPAIR", SIM["now"] + timedelta(minutes=40))
    at(SIM["now"] + timedelta(minutes=50), ev_wo_start, wo_id, None, None, (1, 120), "DISASSEMBLY")
    at(SIM["now"] + timedelta(hours=17, minutes=30), ev_mold_repair_wait, wo_id)


def ev_mold_repair_wait(wo_id):
    wo = ms.get_wo(C.cur, wo_id)
    ms.wo_action(C.cur, wo_id, "STAGE", wo["technician"], stage="CLEANING_REPAIR")
    ms.set_component(C.cur, wo_id, "INSERT", "CRITICAL", "REPLACE", "Nứt 12 mm tại góc insert", wo["technician"])
    for comp in ("CORE", "CAVITY", "EJECTOR", "VENT"):
        ms.set_component(C.cur, wo_id, comp, "GOOD", "CLEAN" if comp == "VENT" else "NONE", None, wo["technician"])
    ms.add_part(C.cur, wo_id, part("CI-AUTO-002-2", "Insert lõi #2 (MLD-AUTO-002)", 1, "cái", 18500000), wo["technician"])
    ms.wo_action(C.cur, wo_id, "PAUSE", wo["technician"], "Chờ insert lõi thay thế từ nhà cung cấp (dự kiến 2 ngày)")


def ev_breakdown_now():
    """IMM-A02: hydraulic pressure lost, waiting for a relief valve."""
    eid = EQ["IMM-A02"]
    alarm_id = machine.raise_alarm(C.cur, eid, "Áp suất dầu thủy lực thấp – máy dừng khẩn", "CRITICAL", "PLC", "E-HYD-03", "HYDRAULIC")
    machine.open_downtime(C.cur, eid, "PLC", C.ids["DT-003"], "Áp suất dầu thủy lực thấp – máy dừng khẩn", source="PLC", alarm_id=alarm_id)
    at(SIM["now"] + mins(3, 6), ev_alarm, alarm_id, "acknowledge", LEADER)
    at(SIM["now"] + mins(15, 20), ev_breakdown_wo, alarm_id)


def ev_breakdown_wo(alarm_id):
    req = q1("SELECT id FROM maintenance_requests WHERE alarm_id=%s", (alarm_id,))
    wo_id = request_to_wo(req["id"], "BREAKDOWN", "Sửa hệ thủy lực: mất áp suất dầu", CUONG, 240)
    at(SIM["now"] + timedelta(minutes=20), ev_wo_start, wo_id, "MT-003", None, (1, 50))
    at(SIM["now"] + timedelta(minutes=100), ev_breakdown_wait, wo_id)


def ev_breakdown_wait(wo_id):
    wo = ms.get_wo(C.cur, wo_id)
    ms.add_part(C.cur, wo_id, part("VLV-DBDS10", "Van an toàn thủy lực DBDS10", 1, "cái", 6800000), wo["technician"])
    C.cur.execute("UPDATE maintenance_work_orders SET root_cause=%s WHERE id=%s", ("Van an toàn kẹt mở, dầu hồi về thùng", wo_id))
    ms.wo_action(C.cur, wo_id, "PAUSE", wo["technician"], "Chờ vật tư: van an toàn thủy lực DBDS10")


def ev_overdue_mold_pm():
    """Molds past their PM shot count and off the machine get their PM work order queued."""
    for m in mold_service.load_molds(C.cur):
        plan = PLAN.get(f"MOLD-{m['mold_code']}")
        if not plan or m["current_machine_id"] or m["status"] != "AVAILABLE" or not m["pm_interval_shot"]:
            continue
        if int(m["current_shot"] or 0) < int(m["last_pm_shot"] or 0) + int(m["pm_interval_shot"]):
            continue
        if not q1("SELECT id FROM maintenance_work_orders WHERE asset_type='MOLD' AND asset_id=%s AND status IN ('RELEASED','ASSIGNED','IN_PROGRESS','PAUSED','VERIFICATION')",
                  (m["id"],)):
            ms.wo_from_plan(C.cur, plan, {"planned_start": (END + timedelta(days=1)).replace(hour=8, minute=0)}, SUP)


# =========================================================
# HOOKS ON THE PRODUCTION SIMULATION
# =========================================================

_orig_build, _orig_hold, _orig_resume, _orig_end = base.build_schedule, base.ev_hold, base.ev_resume, base.ev_end


def _open_downtime_of(w):
    return q1("SELECT id FROM downtime_events WHERE work_order_id=%s AND status='OPEN' ORDER BY id DESC LIMIT 1", (w["id"],))


def hold_maintenance(w, code):
    dt = _open_downtime_of(w)
    mid = w["machine_id"]
    if code == "DT-003":  # heater alarm: breakdown work order on the held downtime
        alarm_id = machine.raise_alarm(C.cur, mid, "Nhiệt độ vùng gia nhiệt 3 xi lanh lệch khỏi giá trị cài đặt", "HIGH", "PLC", "E-HEAT-03",
                                       "MELT_TEMPERATURE", round(rnd.uniform(256, 268), 1), 250)
        C.cur.execute("UPDATE downtime_events SET alarm_id=%s WHERE id=%s", (alarm_id, dt["id"]))
        alarm_do(alarm_id, "acknowledge", LEADER)
        rid = new_request("MACHINE", mid, "Máy dừng do cảnh báo nhiệt độ vùng 3 xi lanh", NAME.get(w["operator"], SUP), priority="HIGH", source="ALARM",
                          alarm_id=alarm_id, downtime_event_id=dt["id"], production_wo_id=w["id"])
        wo_id = request_to_wo(rid, "BREAKDOWN", "Sửa bộ gia nhiệt vùng 3 xi lanh", rnd.choice([CUONG, THAO]), 60)
        start_wo(wo_id)
        w["mwo"] = (wo_id, alarm_id)
    elif code == "DT-005":  # power cut: critical alarm raises a request by itself
        alarm_id = machine.raise_alarm(C.cur, mid, "Mất nguồn điện lưới – máy dừng", "CRITICAL", "PLC", "E-PWR-01")
        C.cur.execute("UPDATE downtime_events SET alarm_id=%s WHERE id=%s", (alarm_id, dt["id"]))
        w["power"] = alarm_id
    elif code == "DT-002":  # broken ejector pin: mold repair request, fixed on the machine
        rid = new_request("MOLD", w["mold_id"], "Gãy chốt đẩy khuôn – máy dừng", NAME.get(w["operator"], SUP), priority="HIGH", downtime_event_id=dt["id"],
                          production_wo_id=w["id"])
        wo_id = request_to_wo(rid, "CORRECTIVE", "Thay chốt đẩy bị gãy (sửa tại máy)", THAO, 60, "MOLD_REPAIR")
        w["moldwo"] = (wo_id, rid, dt["id"], SIM["now"])


def resume_maintenance(w, cause, action):
    if w.get("mwo"):
        wo_id, alarm_id = w.pop("mwo")
        finish_wo(wo_id, {"result": action, "root_cause": cause, "alarm": alarm_id,
                          "parts": [part("HB-60", "Điện trở gia nhiệt 60x40 220V 400W", 1, "cái", 450000), part("TC-J", "Cặp nhiệt loại J", 1, "cái", 380000)]})
        at(SIM["now"] + mins(15, 45), ev_wo_verify, wo_id, {"alarm": alarm_id})
    if w.get("power"):
        alarm_id = w.pop("power")
        alarm_do(alarm_id, "clear", "PLC")
        alarm_do(alarm_id, "close", LEADER, "Có điện trở lại, máy chạy bình thường")
        req = q1("SELECT id FROM maintenance_requests WHERE alarm_id=%s AND status='NEW'", (alarm_id,))
        if req:
            ms.request_action(C.cur, req["id"], "REVIEW", SUP)
            ms.request_action(C.cur, req["id"], "REJECT", SUP, "Mất điện lưới khu vực, thiết bị không hư hỏng – không cần lệnh bảo trì")
    if w.get("moldwo"):
        wo_id, rid, dt_id, t0 = w.pop("moldwo")
        repair_on_machine(wo_id, rid, dt_id, t0)


def repair_on_machine(wo_id, rid, dt_id, t0):
    """The mold stays mounted: the repair is recorded on the work order without moving the mold."""
    now = SIM["now"]
    started = t0 + (now - t0) * 0.15
    minutes = round((now - started).total_seconds() / 60, 1)
    C.cur.execute("""UPDATE maintenance_work_orders SET status='COMPLETED', stage='TESTING', actual_start=%s, actual_finish=%s, downtime_event_id=%s,
                     downtime_minutes=%s, result=%s, root_cause=%s, verified_by=%s, verified_at=%s, completed_at=%s, version=version+1 WHERE id=%s""",
                  (started, now, dt_id, round((now - t0).total_seconds() / 60, 1), "Thay 2 chốt đẩy D6, chạy thử mẫu đầu đạt",
                   "Chốt đẩy mỏi sau thời gian dài sử dụng", SUP, now, now, wo_id))
    tick_checklist(wo_id)
    C.cur.execute("UPDATE maintenance_wo_components SET condition_code='GOOD' WHERE wo_id=%s", (wo_id,))
    C.cur.execute("UPDATE maintenance_wo_components SET condition_code='ATTENTION', action_code='REPLACE', note='Gãy 2 chốt đẩy D6' WHERE wo_id=%s AND component='EJECTOR'", (wo_id,))
    C.cur.execute("INSERT INTO maintenance_wo_parts (wo_id, part_code, part_name, qty, unit, unit_cost, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                  (wo_id, "EJP-6", "Chốt đẩy D6 x 150", 2, "cái", 185000, THAO))
    C.cur.execute("INSERT INTO maintenance_wo_labor (wo_id, technician, labor_type, hours, rate, created_by) VALUES (%s,%s,'REPAIR',%s,%s,'SYSTEM')",
                  (wo_id, THAO, round(minutes / 60, 2), ms.labor_rate(C.cur)))
    ms.log(C.cur, "WO", wo_id, "START", THAO, "ASSIGNED", "IN_PROGRESS", "Sửa tại máy", at=started)
    ms.log(C.cur, "WO", wo_id, "SUBMIT", THAO, "IN_PROGRESS", "VERIFICATION", at=now)
    ms.log(C.cur, "WO", wo_id, "VERIFY", SUP, "VERIFICATION", "COMPLETED", "Đã kiểm tra, đạt yêu cầu", at=now)
    C.cur.execute("UPDATE maintenance_requests SET status='COMPLETED', version=version+1 WHERE id=%s AND status='APPROVED'", (rid,))
    ms.log(C.cur, "REQ", rid, "COMPLETE", SUP, "APPROVED", "COMPLETED", at=now)
    at(now + timedelta(hours=rnd.uniform(6, 20)), ev_wo_close, wo_id)


def maybe_mold_pm(w):
    """Mold PM between two runs once the mold reaches its PM shot count (and is not needed again soon)."""
    m = mold_service.get_mold(C.cur, w["mold_id"])
    if m["current_machine_id"] or m["status"] != "AVAILABLE" or not m["pm_interval_shot"]:
        return
    interval = int(m["pm_interval_shot"])
    if int(m["current_shot"] or 0) < int(m["last_pm_shot"] or 0) + interval * 0.97:
        return
    plan = PLAN.get(f"MOLD-{m['mold_code']}")
    if not plan or q1("SELECT id FROM maintenance_work_orders WHERE asset_type='MOLD' AND asset_id=%s AND status IN ('RELEASED','ASSIGNED','IN_PROGRESS','PAUSED','VERIFICATION')",
                      (m["id"],)):
        return
    nxt = min((x["planned_start"] for x in C.lines[w["machine_id"]] if x["mold_id"] == m["id"] and not x["started"] and x is not w), default=None)
    gap = (nxt - SIM["now"]).total_seconds() / 3600 if nxt else 99
    if gap < 5:
        return
    hours = min(rnd.uniform(3, 4.5), gap - 1.5)
    start = SIM["now"] + mins(20, 40)
    wo_id = ms.wo_from_plan(C.cur, plan, {"technician": rnd.choice([THAO, CUONG]), "planned_start": start}, SUP)
    monitor = rnd.choice(["HOT_RUNNER", "SLIDE", "CAVITY"])
    at(start, ev_wo_start, wo_id, None, {
        "minutes": hours * 60, "stages": True, "verify": 15, "result": f"Bảo dưỡng định kỳ tại {int(m['current_shot']):,} shot, mẫu đầu đạt".replace(",", "."),
        "parts": [part("OR-M", "Bộ gioăng kênh làm mát", 1, "bộ", 280000), part("GR-HT", "Mỡ chịu nhiệt cho khuôn", 1, "tuýp", 165000)],
        "components": {monitor: ("MONITOR", "CLEAN", "Theo dõi ở lần bảo dưỡng tới"), "EJECTOR": ("GOOD", "ADJUST", None)}})


def build_schedule():
    base.LINES[:] = LINES
    setup()
    orders = _orig_build()
    schedule()
    return orders


def ev_hold(w, minutes, code, cause, action):
    _orig_hold(w, minutes, code, cause, action)
    if base.wo_row(w)["status"] == "ON_HOLD" and code in ("DT-002", "DT-003", "DT-005"):
        guarded(hold_maintenance, w, code)


def ev_resume(w, cause, action):
    if base.wo_row(w)["status"] == "ON_HOLD":
        guarded(resume_maintenance, w, cause, action)
    _orig_resume(w, cause, action)


def ev_end(w):
    _orig_end(w)
    if w["done"]:
        guarded(maybe_mold_pm, w)


base.build_schedule, base.ev_hold, base.ev_resume, base.ev_end = build_schedule, ev_hold, ev_resume, ev_end


# =========================================================
# SCHEDULE
# =========================================================


def schedule():
    S = lambda days, hh, mm=0: (START + timedelta(days=days)).replace(hour=hh, minute=mm)
    E = lambda hours: END - timedelta(hours=hours)

    # the user Work Order on IMM-B01 ends after its planned quantity
    row = q1("SELECT started_at, planned_qty, cavity, cycle_time_sec FROM production_plan_details WHERE wo_no='WO-261023-002' AND status='IN_PRODUCTION'")
    if row:
        run = int(row["planned_qty"]) / max(int(row["cavity"] or 1), 1) * float(row["cycle_time_sec"] or 28) * 1.05
        at(row["started_at"] + timedelta(seconds=run), ev_finish_user_wo, "WO-261023-002")

    # weekly PM on Sundays (A01 skipped last Sunday -> overdue, A02 one day late once)
    for n, sunday in enumerate(SUNDAYS):
        for i, key in enumerate(WEEKLY):
            if key == "W-IMM-A01" and n == len(SUNDAYS) - 1:
                continue
            start = sunday.replace(hour=7, minute=30) + timedelta(minutes=35 * i)
            if key == "W-IMM-A02" and n == 1:
                start += timedelta(days=1, hours=2)
            est = next(p[8] for p in PLANS if p[0] == key)
            at(sunday - timedelta(hours=10), ev_plan_wo, key, start,
               {"minutes": est * rnd.uniform(0.8, 1.2), "result": RESULT[key], "parts": [] if n else [OIL_FILTER] if key.startswith("W-IMM") else []})

    # monthly / quarterly PM and calibration
    for key, when, minutes in (("Q-HDL-002", S(3, 10), 40), ("M-CHL-01", S(6, 8), 130), ("M-MTC-B01", S(9, 13, 30), 70), ("M-CRS-D01", S(13, 9), 95)):
        at(when - timedelta(hours=20), ev_plan_wo, key, when, {"minutes": minutes, "result": RESULT[key]})
    for key, when in (("CAL-TL-MEA-003", S(9, 9)), ("CAL-TL-MEA-001", S(12, 14))):
        at(when - timedelta(days=2), ev_plan_wo, key, when, {"minutes": rnd.uniform(50, 70), "calibration": "PASS",
                                                              "calibration_value": "Sai số lớn nhất 0,01 mm", "result": "Sai số trong giới hạn cho phép"}, None)

    # corrective history
    at(S(4, 13, 20), ev_corrective, {
        "asset": "MACHINE", "code": "IMM-A02", "short": "Rò rỉ dầu ống thủy lực cụm kẹp khuôn", "by": TUAN, "task": "Thay ống thủy lực cụm kẹp khuôn",
        "tech": THAO, "minutes": 135, "reason": "MT-003", "result": "Thay ống thủy lực 1/2\", siết lại khớp nối, chạy thử không rò rỉ",
        "root_cause": "Ống thủy lực mòn tại vị trí uốn", "parts": [part("HS-12", "Ống thủy lực 1/2 inch 2 lớp bố thép", 1, "cái", 850000)]})
    at(S(5, 9, 40), ev_corrective, {
        "asset": "TOOL", "code": "TL-CUT-001", "short": "Lưỡi dao cắt runner bị cùn", "by": TUAN, "task": "Mài lại lưỡi dao cắt runner",
        "tech": CUONG, "minutes": 40, "priority": "LOW", "result": "Đã mài lại lưỡi, cắt thử đạt", "root_cause": "Mòn sau thời gian sử dụng"})
    at_req(S(7, 10, 15), "MACHINE", "IMM-A01", "Màn hình HMI tối, khó đọc", TUAN, "REJECTED",
       "Không phải lỗi thiết bị – đã chỉnh lại độ sáng màn hình", priority="LOW")
    at(S(12, 10, 10), ev_corrective, {
        "asset": "EQUIPMENT", "code": "RB-B01", "short": "Robot mất vị trí gốc trục Y", "by": TUAN, "alarm": ("E-ROB-21", "Robot mất vị trí gốc trục Y", "HIGH"),
        "wo_type": "BREAKDOWN", "task": "Sửa robot: hiệu chỉnh gốc trục Y, kiểm tra encoder", "tech": CUONG, "minutes": 90, "reason": "MT-005",
        "result": "Siết lại khớp nối encoder, hiệu chỉnh gốc, chạy thử 50 chu kỳ đạt", "root_cause": "Khớp nối encoder trục Y bị lỏng",
        "parts": [part("ENC-CP", "Khớp nối encoder", 1, "cái", 1450000)]})
    at(S(15, 21, 30), ev_corrective, {
        "asset": "EQUIPMENT", "code": "CHL-01", "short": "Lưu lượng nước lạnh thấp", "by": "Bộ phận Utility", "dept": "Utility",
        "alarm": ("E-CW-04", "Lưu lượng nước lạnh thấp", "HIGH"), "task": "Thay phớt cơ khí bơm tuần hoàn chiller", "tech": THAO, "minutes": 150,
        "reason": "MT-003", "result": "Thay phớt cơ khí, lưu lượng 82 L/phút", "root_cause": "Phớt cơ khí bơm bị mòn gây rò rỉ",
        "parts": [part("PMP-S", "Phớt cơ khí bơm tuần hoàn", 1, "cái", 1650000)], "verify": 600})
    at(S(18, 9, 30), ev_cancelled_wo, "CRS-D01", "Máy nghiền kêu to khi chạy", "Kiểm tra tiếng ồn máy nghiền")

    # alarms during production (machines) and on the utilities / auxiliaries
    for row in q("""SELECT e.id, t.category FROM equipment e JOIN equipment_types t ON t.id=e.equipment_type_id WHERE e.asset_status='ACTIVE'"""):
        if row["category"] not in MINOR_ALARMS:
            continue
        per_day = 1.4 if row["category"] == "MACHINE" else 0.35
        t = START
        while t < END:
            t += timedelta(hours=rnd.expovariate(per_day / 24))
            if t < END:
                at(t, ev_minor_alarm, row["id"], row["category"])

    # open work now
    at(E(76), ev_mold_repair_now)
    at(E(144), ev_plan_wo_only, "CAL-TL-MEA-002", E(140) + timedelta(days=2))
    at_req(E(20), "EQUIPMENT", "MTC-B01", "Rò rỉ nước tại khớp nối nhanh", TUAN, "IN_REVIEW")
    at(E(7), ev_corrective, {
        "asset": "TOOL", "code": "TL-CUT-002", "short": "Lưỡi kìm cắt bavia bị mẻ", "by": TUAN, "task": "Thay lưỡi kìm cắt bavia", "tech": CUONG,
        "minutes": 60, "priority": "MEDIUM", "open": True})
    at_req(E(6), "MOLD", "MLD-PACK-001", "Bavia tại đường phân khuôn", QA, "IN_REVIEW", source="QUALITY", dept="Chất lượng", priority="HIGH",
       desc="IPQC ghi nhận bavia 0,3 mm tại đường phân khuôn ở 3 lần kiểm tra liên tiếp.")
    at(E(5.3), ev_plan_wo, "M-IMM-B02", E(5), None, "MT-001", (2, 150))
    at_req(E(5), "EQUIPMENT", "RB-A01", "Đệm giác hút mòn – thay ở lần PM tới", TUAN, "NEW", rtype="PREVENTIVE", priority="LOW")
    at(E(4.4), ev_plan_wo_only, "M-DRY-A01", (END + timedelta(days=1)).replace(hour=9, minute=0))
    at(E(4.3), ev_plan_wo_inspection)
    at(E(3), ev_breakdown_now)
    at(E(26), ev_plan_wo, "M-HDL-001", E(2), None, None, (2, 70), "INSPECTION")
    at_req(E(1.5), "MACHINE", "IMM-A01", "Tiếng kêu bất thường cụm kẹp khuôn khi đóng tốc độ cao", TUAN, "NEW",
       desc="Có tiếng gõ khi khuôn đóng ở tốc độ cao, cần kiểm tra.")
    at_req(E(1.2), "MACHINE", "IMM-A01", "Máy A01 kêu khi đóng khuôn", CUONG, "CANCELLED", "Trùng yêu cầu đã tạo trước đó")
    at(E(0.5), ev_overdue_mold_pm)


def ev_plan_wo_inspection():
    """CHL-01: outlet temperature higher than usual, inspected and waiting for verification."""
    wo_id = ms.create_wo(C.cur, {"asset_type": "EQUIPMENT", "asset_id": EQ["CHL-01"], "wo_type": "INSPECTION", "priority": "MEDIUM",
                                 "task_name": "Kiểm tra nhiệt độ nước ra cao hơn bình thường", "technician": THAO, "requested_by": "Bộ phận Utility",
                                 "planned_start": SIM["now"] + timedelta(minutes=20), "planned_finish": SIM["now"] + timedelta(hours=2), "est_minutes": 90}, SUP)
    set_checklist(wo_id, CHECKLIST["INSPECTION"])
    at(SIM["now"] + timedelta(minutes=20), ev_wo_start, wo_id, "MT-001",
       {"minutes": 110, "result": "Vệ sinh lá tản nhiệt dàn ngưng, nhiệt độ nước ra về 12 °C", "verify": False})


# =========================================================
# AFTER THE SIMULATION: trend, components, plan sync
# =========================================================


def finish_up():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        now = END
        ms.sync_auto_plans(cur)
        cur.execute("UPDATE maintenance_work_orders SET requested_by=%s WHERE requested_by='Maintenance Planning'", (PLANNING,))
        cur.execute("""SELECT e.id, e.equipment_code, t.category FROM equipment e JOIN equipment_types t ON t.id=e.equipment_type_id
                       WHERE e.asset_status='ACTIVE' AND t.category IN ('MACHINE','UTILITY','ROBOT')""")
        equipment = cur.fetchall()
        since = (now - timedelta(hours=48)).replace(second=0, microsecond=0)
        since -= timedelta(minutes=since.minute % 5)
        readings = 0
        for eq in equipment:
            limits = machine.parameter_limits(cur, machine.get_equipment(cur, equipment_id=eq["id"]))
            cur.execute("""SELECT status, started_at, ended_at FROM machine_status_events
                           WHERE equipment_id=%s AND (ended_at IS NULL OR ended_at > %s) ORDER BY started_at""", (eq["id"], since))
            running = [(r["started_at"], r["ended_at"] or now) for r in cur.fetchall() if r["status"] == "RUNNING"]
            cur.execute("SELECT raised_at FROM machine_alarms WHERE equipment_id=%s AND alarm_code='E-HEAT-03' AND raised_at > %s", (eq["id"], since))
            heat = [r["raised_at"] for r in cur.fetchall()]
            rows, t = [], since
            while t <= now:
                if any(a <= t < b for a, b in running):
                    phase = math.sin(t.timestamp() / 5400 + eq["id"])
                    for code, lim in limits.items():
                        lo, hi, target = lim["min"], lim["max"], lim["target"]
                        if target is None:
                            target = ((lo or 0) + (hi or 0)) / 2 if lo is not None and hi is not None else (hi or lo or 1)
                        span = (hi - lo) / 2 if lo is not None and hi is not None else abs(target) * 0.1 or 1
                        value = target + span * (0.12 * phase + rnd.gauss(0, 0.15))
                        if code == "MELT_TEMPERATURE" and hi is not None and any(abs((t - h).total_seconds()) < 900 for h in heat):
                            value = hi + span * rnd.uniform(0.1, 0.4)
                        rows.append((eq["id"], code, lim["name"], round(value, 2), lim["unit"], t))
                t += timedelta(minutes=5)
            for i in range(0, len(rows), 2000):
                cur.executemany("""INSERT INTO machine_parameter_readings (equipment_id, parameter_code, parameter_name, value, unit, read_at, source)
                                   VALUES (%s,%s,%s,%s,%s,%s,'SIMULATOR')""", rows[i:i + 2000])
            readings += len(rows)
            special = {("IMM-A02", "HYDRAULIC"): ("ERROR", "Áp suất dầu thủy lực thấp"), ("CHL-01", "PUMP"): ("WARNING", "Rung động bơm tăng nhẹ"),
                       ("IMM-D02", "LUBRICATION"): ("WARNING", "Mức dầu bôi trơn dưới 30 %")}
            for comp in machine.COMPONENTS[eq["category"]]:
                status, message = special.get((eq["equipment_code"], comp), ("OK", None))
                cur.execute("""INSERT INTO machine_component_status (equipment_id, component_code, status, value, message, source) VALUES (%s,%s,%s,NULL,%s,'SIMULATOR')
                               ON DUPLICATE KEY UPDATE status=VALUES(status), message=VALUES(message), source=VALUES(source), updated_at=NOW()""",
                            (eq["id"], comp, status, message))
        conn.commit()
        print(f"  machine_parameter_readings: {readings}")
        for table in ("maintenance_plans", "maintenance_requests", "maintenance_work_orders", "maintenance_wo_parts", "maintenance_wo_labor",
                      "maintenance_events", "machine_alarms", "downtime_events", "mold_events", "mold_shot_daily"):
            cur.execute(f"SELECT COUNT(*) AS n FROM {table}")
            print(f"  {table}: {cur.fetchone()['n']}")
        cur.execute("SELECT status, COUNT(*) AS n FROM maintenance_work_orders GROUP BY status")
        print("  work orders:", {r["status"]: r["n"] for r in cur.fetchall()})
        cur.execute("SELECT status, COUNT(*) AS n FROM maintenance_requests GROUP BY status")
        print("  requests:", {r["status"]: r["n"] for r in cur.fetchall()})
        print(f"  maintenance side steps skipped: {STATS['errors']}")
    finally:
        cur.close()
        conn.close()


def main():
    if real_datetime.now() - END > timedelta(minutes=30):
        raise SystemExit("Run this script directly; the window ends when it is imported.")
    base.main()
    finish_up()


if __name__ == "__main__":
    main()
