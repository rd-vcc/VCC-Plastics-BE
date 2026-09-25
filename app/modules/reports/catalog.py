"""Standard report catalog of the Report Center (8.1).

Each report: code, module, category, names (en / vi), description and run(cur, p) returning
    {"columns": [col], "rows": [dict], "summary": [(label_en, label_vi, value, type)], "chart": {...} | None}
where col = {"key", "en", "vi", "type"} and type in text / int / num / pct / date / datetime / money.
p = {"start": datetime, "end": datetime (exclusive), "date_from": date, "date_to": date}.
Figures reuse the owning module's logic where one exists (maintenance costs, mold life, SPC, OEE).
"""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta


def C(key, en, vi, t="text"):
    return {"key": key, "en": en, "vi": vi, "type": t}


def S(en, vi, value, t="int"):
    return {"en": en, "vi": vi, "value": value, "type": t}


def pct(a, b, d=2):
    return round(a / b * 100, d) if b else None


def f(v):
    return float(v) if v is not None else 0.0


# =========================================================
# PRODUCTION
# =========================================================


def _prod_logs(cur, p, group_sql):
    cur.execute(f"""SELECT {group_sql}, SUM(l.good_qty) good, SUM(l.reject_qty) reject FROM production_execution_logs l
                    JOIN production_plan_details d ON d.id=l.work_order_id LEFT JOIN products pr ON pr.id=d.product_id
                    LEFT JOIN equipment e ON e.id=l.machine_id LEFT JOIN shifts s ON s.id=l.shift_id
                    WHERE l.event_type IN ('OUTPUT','REJECT','QTY_ADJUST') AND l.event_at >= %s AND l.event_at < %s GROUP BY 1 ORDER BY 1""", (p["start"], p["end"]))
    return cur.fetchall()


def production_summary(cur, p):
    cur.execute("""SELECT d.id, d.wo_no, d.status, pr.product_code, pr.product_name, e.equipment_code, mo.mold_code, d.planned_qty,
                          SUM(l.good_qty) good, SUM(l.reject_qty) reject, MIN(l.event_at) first_at, MAX(l.event_at) last_at
                   FROM production_execution_logs l JOIN production_plan_details d ON d.id=l.work_order_id LEFT JOIN products pr ON pr.id=d.product_id
                   LEFT JOIN equipment e ON e.id=d.machine_id LEFT JOIN molds mo ON mo.id=d.mold_id
                   WHERE l.event_type IN ('OUTPUT','REJECT','QTY_ADJUST') AND l.event_at >= %s AND l.event_at < %s
                   GROUP BY d.id, d.wo_no, d.status, pr.product_code, pr.product_name, e.equipment_code, mo.mold_code, d.planned_qty ORDER BY d.wo_no""", (p["start"], p["end"]))
    rows = []
    for r in cur.fetchall():
        good, rej = int(r["good"] or 0), int(r["reject"] or 0)
        rows.append({"wo_no": r["wo_no"], "product": r["product_name"], "machine": r["equipment_code"], "mold": r["mold_code"], "planned": int(r["planned_qty"] or 0),
                     "good": good, "reject": rej, "total": good + rej, "yield": pct(good, good + rej), "progress": pct(good, int(r["planned_qty"] or 0), 1),
                     "first_at": r["first_at"], "last_at": r["last_at"], "status": r["status"]})
    g, rj = sum(r["good"] for r in rows), sum(r["reject"] for r in rows)
    return {"columns": [C("wo_no", "Work Order", "Work Order"), C("product", "Product", "Sản phẩm"), C("machine", "Machine", "Máy"), C("mold", "Mold", "Khuôn"),
                        C("planned", "Planned", "Kế hoạch", "int"), C("good", "Good", "Đạt", "int"), C("reject", "Reject", "Lỗi", "int"), C("yield", "Yield %", "Tỷ lệ đạt %", "pct"),
                        C("progress", "Progress %", "Tiến độ %", "pct"), C("first_at", "First output", "Bắt đầu", "datetime"), C("last_at", "Last output", "Gần nhất", "datetime"),
                        C("status", "Status", "Trạng thái")],
            "rows": rows, "summary": [S("Work orders", "Work Order", len(rows)), S("Good pieces", "Sản phẩm đạt", g), S("Reject pieces", "Sản phẩm lỗi", rj),
                                      S("Yield", "Tỷ lệ đạt", pct(g, g + rj), "pct")],
            "chart": {"type": "bar", "x": "wo_no", "series": ["good", "reject"]}}


def production_daily(cur, p):
    rows = [{"date": r["d"], "good": int(r["good"] or 0), "reject": int(r["reject"] or 0)} for r in _prod_logs(cur, p, "DATE(l.event_at) d")]
    for r in rows:
        r["total"] = r["good"] + r["reject"]
        r["yield"] = pct(r["good"], r["total"])
        r["ppm"] = round(r["reject"] / r["total"] * 1e6) if r["total"] else None
    g, rj = sum(r["good"] for r in rows), sum(r["reject"] for r in rows)
    return {"columns": [C("date", "Date", "Ngày", "date"), C("good", "Good", "Đạt", "int"), C("reject", "Reject", "Lỗi", "int"), C("total", "Total", "Tổng", "int"),
                        C("yield", "Yield %", "Tỷ lệ đạt %", "pct"), C("ppm", "PPM", "PPM", "int")],
            "rows": rows, "summary": [S("Days with output", "Số ngày có sản lượng", len(rows)), S("Good pieces", "Sản phẩm đạt", g), S("Reject pieces", "Sản phẩm lỗi", rj),
                                      S("PPM", "PPM", round(rj / (g + rj) * 1e6) if g + rj else None)],
            "chart": {"type": "line", "x": "date", "series": ["good", "reject"]}}


def production_shift(cur, p):
    rows = [{"shift": r["s"] or "-", "good": int(r["good"] or 0), "reject": int(r["reject"] or 0)} for r in _prod_logs(cur, p, "s.shift_name s")]
    for r in rows:
        r["total"] = r["good"] + r["reject"]
        r["yield"] = pct(r["good"], r["total"])
    return {"columns": [C("shift", "Shift", "Ca"), C("good", "Good", "Đạt", "int"), C("reject", "Reject", "Lỗi", "int"), C("total", "Total", "Tổng", "int"), C("yield", "Yield %", "Tỷ lệ đạt %", "pct")],
            "rows": rows, "summary": [S("Shifts", "Số ca", len(rows))], "chart": {"type": "bar", "x": "shift", "series": ["good", "reject"]}}


def production_orders(cur, p):
    cur.execute("""SELECT o.order_no, o.status, o.order_qty, o.due_date, c.customer_name, pr.product_name, COALESCE(SUM(d.actual_qty),0) actual, COUNT(d.id) wos
                   FROM production_orders o LEFT JOIN customers c ON c.id=o.customer_id LEFT JOIN products pr ON pr.id=o.product_id
                   LEFT JOIN production_plan_details d ON d.production_order_id=o.id AND d.status<>'CANCELLED'
                   WHERE o.status NOT IN ('CANCELLED') AND (o.due_date >= %s OR o.status NOT IN ('CLOSED','COMPLETED'))
                   GROUP BY o.id, o.order_no, o.status, o.order_qty, o.due_date, c.customer_name, pr.product_name ORDER BY o.due_date""", (p["start"],))
    rows = []
    for r in cur.fetchall():
        qty = int(r["order_qty"] or 0)
        rows.append({"order_no": r["order_no"], "customer": r["customer_name"], "product": r["product_name"], "qty": qty, "actual": int(r["actual"] or 0),
                     "progress": pct(int(r["actual"] or 0), qty, 1), "wos": int(r["wos"]), "due": r["due_date"], "status": r["status"],
                     "late": "YES" if r["due_date"] and r["due_date"] < datetime.now() and int(r["actual"] or 0) < qty else ""})
    return {"columns": [C("order_no", "Production Order", "Lệnh sản xuất"), C("customer", "Customer", "Khách hàng"), C("product", "Product", "Sản phẩm"),
                        C("qty", "Order qty", "SL đặt", "int"), C("actual", "Produced", "Đã sản xuất", "int"), C("progress", "Progress %", "Tiến độ %", "pct"),
                        C("wos", "Work orders", "Số WO", "int"), C("due", "Due date", "Hạn giao", "datetime"), C("status", "Status", "Trạng thái"), C("late", "Late", "Trễ hạn")],
            "rows": rows, "summary": [S("Orders", "Lệnh", len(rows)), S("Late", "Trễ hạn", sum(1 for r in rows if r["late"]))],
            "chart": {"type": "bar", "x": "order_no", "series": ["qty", "actual"]}}


# =========================================================
# MACHINE & EQUIPMENT
# =========================================================


def machine_utilization(cur, p):
    end = min(p["end"], datetime.now())
    span = max((end - p["start"]).total_seconds(), 1)
    cur.execute("""SELECT e.id, e.equipment_code, e.equipment_name, s.status, SUM(TIMESTAMPDIFF(SECOND, GREATEST(s.started_at, %s), LEAST(COALESCE(s.ended_at, NOW()), %s))) sec
                   FROM machine_status_events s JOIN equipment e ON e.id=s.equipment_id
                   WHERE s.started_at < %s AND (s.ended_at IS NULL OR s.ended_at > %s) GROUP BY e.id, e.equipment_code, e.equipment_name, s.status""", (p["start"], end, end, p["start"]))
    by = defaultdict(lambda: defaultdict(float))
    names = {}
    for r in cur.fetchall():
        by[r["equipment_code"]][r["status"]] += max(f(r["sec"]), 0)
        names[r["equipment_code"]] = r["equipment_name"]
    rows = []
    for code, st in sorted(by.items()):
        tracked = sum(st.values())
        rows.append({"machine": code, "name": names[code], "running_h": round(st["RUNNING"] / 3600, 2), "idle_h": round(st["IDLE"] / 3600, 2),
                     "down_h": round(st["DOWN"] / 3600, 2), "maint_h": round(st["MAINTENANCE"] / 3600, 2), "offline_h": round(st["OFFLINE"] / 3600, 2),
                     "utilization": pct(st["RUNNING"], tracked, 1), "coverage": pct(tracked, span, 1)})
    return {"columns": [C("machine", "Machine", "Máy"), C("name", "Name", "Tên"), C("running_h", "Running (h)", "Chạy (h)", "num"), C("idle_h", "Idle (h)", "Chờ (h)", "num"),
                        C("down_h", "Down (h)", "Dừng (h)", "num"), C("maint_h", "Maintenance (h)", "Bảo trì (h)", "num"), C("offline_h", "Offline (h)", "Mất kết nối (h)", "num"),
                        C("utilization", "Utilization %", "Hiệu suất sử dụng %", "pct"), C("coverage", "Data coverage %", "Độ phủ dữ liệu %", "pct")],
            "rows": rows, "summary": [S("Machines", "Số máy", len(rows)), S("Avg. utilization", "Hiệu suất TB", round(sum(r["utilization"] or 0 for r in rows) / len(rows), 1) if rows else None, "pct")],
            "chart": {"type": "bar", "x": "machine", "series": ["running_h", "idle_h", "down_h", "maint_h"], "stacked": True}}


def machine_oee(cur, p):
    from app.modules.machine.monitoring_router import machine_oee as oee
    cur.execute("""SELECT e.id, e.equipment_code, e.equipment_name, e.operational_status FROM equipment e JOIN equipment_types t ON t.id=e.equipment_type_id
                   WHERE t.category='MACHINE' AND e.asset_status='ACTIVE' ORDER BY e.equipment_code""")
    rows = []
    for m in cur.fetchall():
        k = oee(cur, m["id"], p["start"], min(p["end"], datetime.now()), m["operational_status"])
        rows.append({"machine": m["equipment_code"], "name": m["equipment_name"], "availability": k["availability"], "performance": k["performance"], "quality": k["quality"],
                     "oee": k["oee"], "good": k["good"], "reject": k["reject"]})
    vals = [r["oee"] for r in rows if r["oee"] is not None]
    return {"columns": [C("machine", "Machine", "Máy"), C("name", "Name", "Tên"), C("availability", "Availability %", "Khả dụng %", "pct"), C("performance", "Performance %", "Hiệu năng %", "pct"),
                        C("quality", "Quality %", "Chất lượng %", "pct"), C("oee", "OEE %", "OEE %", "pct"), C("good", "Good", "Đạt", "int"), C("reject", "Reject", "Lỗi", "int")],
            "rows": rows, "summary": [S("Machines", "Số máy", len(rows)), S("Average OEE", "OEE trung bình", round(sum(vals) / len(vals), 1) if vals else None, "pct")],
            "chart": {"type": "bar", "x": "machine", "series": ["oee", "availability", "performance", "quality"]}}


def downtime_analysis(cur, p):
    cur.execute("""SELECT COALESCE(r.reason_name, d.reason_text, 'Unspecified') reason, COUNT(*) n,
                          SUM(TIMESTAMPDIFF(SECOND, GREATEST(d.started_at, %s), LEAST(COALESCE(d.ended_at, NOW()), %s)))/60 minutes
                   FROM downtime_events d LEFT JOIN reason_codes r ON r.id=d.reason_code_id
                   WHERE d.status<>'CANCELLED' AND d.started_at < %s AND (d.ended_at IS NULL OR d.ended_at > %s) GROUP BY 1 ORDER BY minutes DESC""", (p["start"], p["end"], p["end"], p["start"]))
    rows = [{"reason": r["reason"], "events": int(r["n"]), "minutes": round(f(r["minutes"]), 1)} for r in cur.fetchall()]
    total = sum(r["minutes"] for r in rows)
    cum = 0
    for r in rows:
        r["share"] = pct(r["minutes"], total, 1)
        cum += r["minutes"]
        r["cumulative"] = pct(cum, total, 1)
    return {"columns": [C("reason", "Reason", "Nguyên nhân"), C("events", "Events", "Số lần", "int"), C("minutes", "Minutes", "Phút", "num"), C("share", "Share %", "Tỷ trọng %", "pct"),
                        C("cumulative", "Cumulative %", "Lũy kế %", "pct")],
            "rows": rows, "summary": [S("Downtime events", "Số lần dừng", sum(r["events"] for r in rows)), S("Total minutes", "Tổng phút", round(total, 1), "num")],
            "chart": {"type": "bar", "x": "reason", "series": ["minutes"]}}


def downtime_events(cur, p):
    cur.execute("""SELECT d.downtime_no, e.equipment_code, COALESCE(r.reason_name, d.reason_text) reason, d.category, d.started_at, d.ended_at, d.status, d.source, w.wo_no,
                          TIMESTAMPDIFF(SECOND, d.started_at, COALESCE(d.ended_at, NOW()))/60 minutes, d.root_cause, d.action_taken
                   FROM downtime_events d JOIN equipment e ON e.id=d.equipment_id LEFT JOIN reason_codes r ON r.id=d.reason_code_id
                   LEFT JOIN production_plan_details w ON w.id=d.work_order_id
                   WHERE d.started_at >= %s AND d.started_at < %s ORDER BY d.started_at DESC""", (p["start"], p["end"]))
    rows = [{**r, "minutes": round(f(r["minutes"]), 1)} for r in cur.fetchall()]
    return {"columns": [C("downtime_no", "Downtime No.", "Số sự kiện"), C("equipment_code", "Machine", "Máy"), C("reason", "Reason", "Nguyên nhân"), C("category", "Category", "Nhóm"),
                        C("started_at", "Start", "Bắt đầu", "datetime"), C("ended_at", "End", "Kết thúc", "datetime"), C("minutes", "Minutes", "Phút", "num"), C("wo_no", "Work Order", "Work Order"),
                        C("status", "Status", "Trạng thái"), C("source", "Source", "Nguồn"), C("root_cause", "Root cause", "Nguyên nhân gốc"), C("action_taken", "Action", "Hành động")],
            "rows": rows, "summary": [S("Events", "Số sự kiện", len(rows)), S("Total minutes", "Tổng phút", round(sum(r["minutes"] for r in rows), 1), "num")], "chart": None}


def alarm_history(cur, p):
    cur.execute("""SELECT a.alarm_no, e.equipment_code, a.alarm_code, a.message, a.priority, a.status, a.raised_at, a.acknowledged_at, a.cleared_at, a.acknowledged_by
                   FROM machine_alarms a JOIN equipment e ON e.id=a.equipment_id WHERE a.raised_at >= %s AND a.raised_at < %s ORDER BY a.raised_at DESC""", (p["start"], p["end"]))
    rows = cur.fetchall()
    for r in rows:
        r["response_min"] = round((r["acknowledged_at"] - r["raised_at"]).total_seconds() / 60, 1) if r["acknowledged_at"] else None
    by = Counter(r["priority"] for r in rows)
    return {"columns": [C("alarm_no", "Alarm No.", "Số cảnh báo"), C("equipment_code", "Machine", "Máy"), C("alarm_code", "Code", "Mã"), C("message", "Message", "Nội dung"),
                        C("priority", "Priority", "Mức độ"), C("status", "Status", "Trạng thái"), C("raised_at", "Raised", "Phát sinh", "datetime"),
                        C("acknowledged_at", "Acknowledged", "Tiếp nhận", "datetime"), C("response_min", "Response (min)", "Phản hồi (phút)", "num"), C("acknowledged_by", "By", "Người tiếp nhận")],
            "rows": rows, "summary": [S("Alarms", "Số cảnh báo", len(rows))] + [S(k, k, v) for k, v in by.most_common()], "chart": None}


# =========================================================
# MOLD
# =========================================================


def mold_performance(cur, p):
    from app.modules.mold.service import load_molds
    cur.execute("SELECT mold_id, SUM(shots) s FROM mold_shot_daily WHERE shot_date BETWEEN %s AND %s GROUP BY mold_id", (p["date_from"], p["date_to"]))
    shots = {r["mold_id"]: int(r["s"] or 0) for r in cur.fetchall()}
    rows = [{"mold": m["mold_code"], "name": m["mold_name"], "status": m["status"], "cavity": m["cavity_count"], "shots_period": shots.get(m["id"], 0),
             "current_shot": m["current_shot"], "design_shot": m["design_shot"], "life_used": m["life_used_pct"], "health": m["health"], "ng_rate_30d": m["ng_rate_30d"],
             "downtime_h_30d": m["downtime_hours_30d"], "pm_due": m["pm_due_date"], "eol": m["eol_date"]} for m in load_molds(cur)]
    return {"columns": [C("mold", "Mold", "Khuôn"), C("name", "Name", "Tên"), C("status", "Status", "Trạng thái"), C("cavity", "Cavity", "Cavity", "int"),
                        C("shots_period", "Shots (period)", "Shot trong kỳ", "int"), C("current_shot", "Current shot", "Shot hiện tại", "int"), C("design_shot", "Design life", "Tuổi thọ thiết kế", "int"),
                        C("life_used", "Life used %", "Đã dùng %", "pct"), C("health", "Health", "Sức khỏe", "int"), C("ng_rate_30d", "NG % (30d)", "NG % (30 ngày)", "pct"),
                        C("downtime_h_30d", "Downtime h (30d)", "Dừng h (30 ngày)", "num"), C("pm_due", "PM due", "Hạn PM", "date"), C("eol", "End of life", "Hết tuổi thọ", "date")],
            "rows": rows, "summary": [S("Molds", "Số khuôn", len(rows)), S("Shots in period", "Shot trong kỳ", sum(r["shots_period"] for r in rows))],
            "chart": {"type": "bar", "x": "mold", "series": ["shots_period"]}}


def mold_shot_counter(cur, p):
    cur.execute("""SELECT s.shot_date, m.mold_code, s.shots, s.source FROM mold_shot_daily s JOIN molds m ON m.id=s.mold_id
                   WHERE s.shot_date BETWEEN %s AND %s ORDER BY s.shot_date, m.mold_code""", (p["date_from"], p["date_to"]))
    rows = cur.fetchall()
    return {"columns": [C("shot_date", "Date", "Ngày", "date"), C("mold_code", "Mold", "Khuôn"), C("shots", "Shots", "Shot", "int"), C("source", "Source", "Nguồn")],
            "rows": rows, "summary": [S("Records", "Số dòng", len(rows)), S("Shots", "Tổng shot", sum(int(r["shots"] or 0) for r in rows))], "chart": None}


def mold_installations(cur, p):
    cur.execute("""SELECT m.mold_code, e.equipment_code, w.wo_no, i.installed_at, i.installed_by, i.removed_at, i.removed_by, i.removal_reason,
                          i.shots_at_install, i.shots_at_remove FROM mold_installations i JOIN molds m ON m.id=i.mold_id LEFT JOIN equipment e ON e.id=i.machine_id
                   LEFT JOIN production_plan_details w ON w.id=i.work_order_id WHERE i.installed_at < %s AND (i.removed_at IS NULL OR i.removed_at >= %s) ORDER BY i.installed_at DESC""",
                (p["end"], p["start"]))
    rows = cur.fetchall()
    for r in rows:
        r["shots"] = (int(r["shots_at_remove"]) - int(r["shots_at_install"] or 0)) if r["shots_at_remove"] is not None else None
        r["hours"] = round(((r["removed_at"] or datetime.now()) - r["installed_at"]).total_seconds() / 3600, 1)
    return {"columns": [C("mold_code", "Mold", "Khuôn"), C("equipment_code", "Machine", "Máy"), C("wo_no", "Work Order", "Work Order"), C("installed_at", "Installed", "Lắp", "datetime"),
                        C("installed_by", "By", "Người lắp"), C("removed_at", "Removed", "Tháo", "datetime"), C("removal_reason", "Reason", "Lý do"), C("hours", "Hours", "Giờ", "num"),
                        C("shots", "Shots", "Shot", "int")],
            "rows": rows, "summary": [S("Installations", "Số lần lắp", len(rows))], "chart": None}


# =========================================================
# MATERIAL
# =========================================================


def material_stock(cur, p):
    from app.modules.material.inventory.service import available_by_material, safety_stocks
    cur.execute("SELECT id, material_code, material_name, material_group, unit FROM materials WHERE status='ACTIVE' ORDER BY material_code")
    mats = cur.fetchall()
    stock, safety = available_by_material(cur), safety_stocks(cur)
    rows = []
    for m in mats:
        b = stock.get(m["id"], {})
        avail, level = f(b.get("available")), f(safety.get(m["id"]))
        if not b and not level:
            continue
        rows.append({"code": m["material_code"], "name": m["material_name"], "group": m["material_group"], "unit": m["unit"], "available": round(avail, 3),
                     "reserved": round(f(b.get("reserved")), 3), "in_use": round(f(b.get("in_use")), 3), "hold": round(f(b.get("hold")), 3), "waiting_iqc": round(f(b.get("waiting_iqc")), 3),
                     "safety": round(level, 3), "coverage": pct(avail, level, 1), "state": "OUT" if avail <= 0 else "LOW" if level and avail < level else "OK"})
    return {"columns": [C("code", "Material", "Mã NL"), C("name", "Name", "Tên"), C("group", "Group", "Nhóm"), C("unit", "Unit", "ĐVT"), C("available", "Available", "Khả dụng", "num"),
                        C("reserved", "Reserved", "Đã giữ", "num"), C("in_use", "At machine", "Tại máy", "num"), C("hold", "Hold", "Tạm giữ", "num"), C("waiting_iqc", "Waiting IQC", "Chờ IQC", "num"),
                        C("safety", "Safety stock", "Tồn an toàn", "num"), C("coverage", "Coverage %", "Độ phủ %", "pct"), C("state", "State", "Tình trạng")],
            "rows": rows, "summary": [S("Materials", "Số NL", len(rows)), S("Low", "Sắp hết", sum(1 for r in rows if r["state"] == "LOW")), S("Out", "Hết", sum(1 for r in rows if r["state"] == "OUT"))],
            "chart": None}


def material_consumption(cur, p):
    cur.execute("""SELECT m.material_code, m.material_name, m.unit, COUNT(DISTINCT u.work_order_id) wos, COUNT(DISTINCT u.lot_id) lots, SUM(u.qty) qty
                   FROM material_usages u JOIN materials m ON m.id=u.material_id WHERE u.status='ACTIVE' AND u.used_at >= %s AND u.used_at < %s
                   GROUP BY m.material_code, m.material_name, m.unit ORDER BY qty DESC""", (p["start"], p["end"]))
    rows = [{**r, "qty": round(f(r["qty"]), 3)} for r in cur.fetchall()]
    return {"columns": [C("material_code", "Material", "Mã NL"), C("material_name", "Name", "Tên"), C("unit", "Unit", "ĐVT"), C("wos", "Work orders", "Số WO", "int"),
                        C("lots", "Lots", "Số lô", "int"), C("qty", "Consumed", "Tiêu thụ", "num")],
            "rows": rows, "summary": [S("Materials", "Số NL", len(rows))], "chart": {"type": "bar", "x": "material_code", "series": ["qty"]}}


def material_transactions(cur, p):
    cur.execute("""SELECT t.acted_at, l.lot_no, m.material_code, t.txn_type, t.qty_change, t.qty_after, fl.location_code from_loc, tl.location_code to_loc, w.wo_no, t.actor, t.remark
                   FROM material_lot_transactions t JOIN material_lots l ON l.id=t.lot_id JOIN materials m ON m.id=l.material_id
                   LEFT JOIN material_locations fl ON fl.id=t.from_location_id LEFT JOIN material_locations tl ON tl.id=t.to_location_id
                   LEFT JOIN production_plan_details w ON w.id=t.work_order_id WHERE t.acted_at >= %s AND t.acted_at < %s ORDER BY t.acted_at DESC""", (p["start"], p["end"]))
    rows = [{**r, "qty_change": f(r["qty_change"]), "qty_after": f(r["qty_after"])} for r in cur.fetchall()]
    return {"columns": [C("acted_at", "Time", "Thời gian", "datetime"), C("lot_no", "Lot", "Lô"), C("material_code", "Material", "Mã NL"), C("txn_type", "Transaction", "Giao dịch"),
                        C("qty_change", "Qty", "SL", "num"), C("qty_after", "Balance", "Tồn sau", "num"), C("from_loc", "From", "Từ"), C("to_loc", "To", "Đến"), C("wo_no", "Work Order", "Work Order"),
                        C("actor", "User", "Người thực hiện"), C("remark", "Remark", "Ghi chú")],
            "rows": rows, "summary": [S("Transactions", "Số giao dịch", len(rows))], "chart": None}


def material_shortage(cur, p):
    cur.execute("""SELECT d.wo_no, d.status wo_status, d.planned_start, m.material_code, m.material_name, m.unit, r.required_qty, r.open_qty, r.status
                   FROM material_requirements r JOIN production_plan_details d ON d.id=r.work_order_id JOIN materials m ON m.id=r.material_id
                   WHERE d.status IN ('SCHEDULED','RELEASED','IN_PRODUCTION','ON_HOLD') AND r.status IN ('SHORTAGE','PARTIAL','PLANNED') ORDER BY r.status='SHORTAGE' DESC, d.planned_start""")
    rows = [{**r, "required_qty": f(r["required_qty"]), "open_qty": f(r["open_qty"])} for r in cur.fetchall()]
    return {"columns": [C("wo_no", "Work Order", "Work Order"), C("wo_status", "WO status", "Trạng thái WO"), C("planned_start", "Planned start", "Bắt đầu KH", "datetime"),
                        C("material_code", "Material", "Mã NL"), C("material_name", "Name", "Tên"), C("unit", "Unit", "ĐVT"), C("required_qty", "Required", "Cần", "num"),
                        C("open_qty", "Open", "Còn thiếu", "num"), C("status", "Status", "Trạng thái")],
            "rows": rows, "summary": [S("Lines", "Số dòng", len(rows)), S("Shortage", "Thiếu", sum(1 for r in rows if r["status"] == "SHORTAGE"))], "chart": None}


# =========================================================
# QUALITY
# =========================================================


def quality_inspection(cur, p):
    cur.execute("""SELECT DATE(r.inspected_at) d, ip.inspection_type t, COUNT(*) n, SUM(r.result='NG') ng, SUM(r.is_reinspection=0) fn, SUM(r.is_reinspection=0 AND r.result='OK') fok
                   FROM quality_inspection_records r JOIN quality_inspection_plans ip ON ip.id=r.plan_id
                   WHERE r.inspected_at >= %s AND r.inspected_at < %s GROUP BY d, t ORDER BY d, t""", (p["start"], p["end"]))
    rows = []
    for r in cur.fetchall():
        n, ng = int(r["n"]), int(r["ng"] or 0)
        rows.append({"date": r["d"], "type": r["t"], "samples": n, "ok": n - ng, "ng": ng, "ng_rate": pct(ng, n), "fpy": pct(int(r["fok"] or 0), int(r["fn"] or 0))})
    n, ng = sum(r["samples"] for r in rows), sum(r["ng"] for r in rows)
    return {"columns": [C("date", "Date", "Ngày", "date"), C("type", "Inspection type", "Loại kiểm tra"), C("samples", "Samples", "Số mẫu", "int"), C("ok", "OK", "OK", "int"),
                        C("ng", "NG", "NG", "int"), C("ng_rate", "NG %", "Tỷ lệ NG %", "pct"), C("fpy", "FPY %", "FPY %", "pct")],
            "rows": rows, "summary": [S("Samples", "Số mẫu", n), S("NG samples", "Mẫu NG", ng), S("NG rate", "Tỷ lệ NG", pct(ng, n), "pct")],
            "chart": {"type": "line", "x": "date", "series": ["samples", "ng"]}}


def ng_report(cur, p):
    cur.execute("""SELECT n.ng_no, n.detected_at, n.source, w.wo_no, pr.product_name, rc.reason_name defect, n.process, e.equipment_code machine, mo.mold_code mold, n.ng_qty,
                          n.priority, n.status, n.owner, n.disposition, n.root_cause_category, n.root_cause, n.corrective_action, n.closed_at
                   FROM quality_ng_records n LEFT JOIN production_plan_details w ON w.id=n.work_order_id LEFT JOIN products pr ON pr.id=n.product_id
                   LEFT JOIN reason_codes rc ON rc.id=n.defect_code_id LEFT JOIN equipment e ON e.id=n.machine_id LEFT JOIN molds mo ON mo.id=n.mold_id
                   WHERE n.detected_at >= %s AND n.detected_at < %s ORDER BY n.detected_at DESC""", (p["start"], p["end"]))
    rows = cur.fetchall()
    return {"columns": [C("ng_no", "NG No.", "Số NG"), C("detected_at", "Detected", "Phát hiện", "datetime"), C("source", "Source", "Nguồn"), C("wo_no", "Work Order", "Work Order"),
                        C("product_name", "Product", "Sản phẩm"), C("defect", "Defect", "Lỗi"), C("process", "Process", "Công đoạn"), C("machine", "Machine", "Máy"), C("mold", "Mold", "Khuôn"),
                        C("ng_qty", "Qty", "SL", "int"), C("priority", "Priority", "Ưu tiên"), C("status", "Status", "Trạng thái"), C("owner", "Owner", "Phụ trách"),
                        C("disposition", "Disposition", "Xử lý"), C("root_cause_category", "4M", "4M"), C("root_cause", "Root cause", "Nguyên nhân gốc"),
                        C("corrective_action", "Corrective action", "Hành động khắc phục"), C("closed_at", "Closed", "Đóng", "datetime")],
            "rows": rows, "summary": [S("NG records", "Hồ sơ NG", len(rows)), S("NG pieces", "SL NG", sum(int(r["ng_qty"] or 0) for r in rows)),
                                      S("Open", "Đang mở", sum(1 for r in rows if r["status"] != "CLOSED"))], "chart": None}


def ng_pareto(cur, p):
    cur.execute("""SELECT COALESCE(rc.reason_name, 'Unclassified') defect, COUNT(*) records, SUM(n.ng_qty) qty FROM quality_ng_records n LEFT JOIN reason_codes rc ON rc.id=n.defect_code_id
                   WHERE n.detected_at >= %s AND n.detected_at < %s GROUP BY 1 ORDER BY qty DESC""", (p["start"], p["end"]))
    rows = [{"defect": r["defect"], "records": int(r["records"]), "qty": int(r["qty"] or 0)} for r in cur.fetchall()]
    total, cum = sum(r["qty"] for r in rows), 0
    for r in rows:
        r["share"] = pct(r["qty"], total, 1)
        cum += r["qty"]
        r["cumulative"] = pct(cum, total, 1)
    return {"columns": [C("defect", "Defect", "Lỗi"), C("records", "Records", "Số hồ sơ", "int"), C("qty", "NG pieces", "SL NG", "int"), C("share", "Share %", "Tỷ trọng %", "pct"),
                        C("cumulative", "Cumulative %", "Lũy kế %", "pct")],
            "rows": rows, "summary": [S("Defect types", "Số loại lỗi", len(rows)), S("NG pieces", "SL NG", total)], "chart": {"type": "bar", "x": "defect", "series": ["qty"]}}


def spc_capability(cur, p):
    from app.modules.quality import service as qs, spc
    cur.execute("""SELECT r.id, r.product_id, pr.product_code, pr.product_name FROM quality_standard_revisions r JOIN products pr ON pr.id=r.product_id WHERE r.status='ACTIVE'""")
    rows = []
    for rev in cur.fetchall():
        for c in qs.characteristics(cur, rev["id"]):
            if (c.get("characteristic_type") or "VARIABLE") != "VARIABLE":
                continue
            a = spc.analyse(c, spc.load_measurements(cur, c["id"], {"product_id": rev["product_id"], "date_from": p["start"], "date_to": p["end"]}))
            rows.append({"product": rev["product_name"], "characteristic": c["characteristic_name"], "unit": c["unit"], "lsl": f(c["lsl"]) if c["lsl"] is not None else None,
                         "usl": f(c["usl"]) if c["usl"] is not None else None, "count": a["count"], "mean": a["mean"], "std_dev": a["std_dev"], "cp": a["cp"], "cpk": a["cpk"],
                         "target": f(c["cpk_target"]) if c["cpk_target"] is not None else None, "status": a["status"]})
    return {"columns": [C("product", "Product", "Sản phẩm"), C("characteristic", "Characteristic", "Đặc tính"), C("unit", "Unit", "ĐVT"), C("lsl", "LSL", "LSL", "num"), C("usl", "USL", "USL", "num"),
                        C("count", "Values", "Số giá trị", "int"), C("mean", "Mean", "Trung bình", "num"), C("std_dev", "Std. dev.", "Độ lệch chuẩn", "num"), C("cp", "Cp", "Cp", "num"),
                        C("cpk", "Cpk", "Cpk", "num"), C("target", "Cpk target", "Cpk mục tiêu", "num"), C("status", "SPC status", "Trạng thái SPC")],
            "rows": rows, "summary": [S("Characteristics", "Số đặc tính", len(rows)), S("Out of control", "Mất kiểm soát", sum(1 for r in rows if r["status"] == "OUT_OF_CONTROL"))],
            "chart": {"type": "bar", "x": "characteristic", "series": ["cpk"]}}


def calibration_status(cur, p):
    from app.modules.quality import service as qs
    rows = [{"code": e["equipment_code"], "name": e["equipment_name"], "type": e["type_name"], "location": e["location"], "last": e["last_calibration_date"],
             "next": e["next_calibration_date"], "days_left": e["days_left"], "state": e["state"], "cycle": e["calibration_cycle_days"], "lab": e["calibration_lab"]} for e in qs.load_equipment(cur)]
    comp = qs.cal_compliance(cur, p["date_from"], p["date_to"])
    return {"columns": [C("code", "Equipment", "Mã thiết bị"), C("name", "Name", "Tên"), C("type", "Type", "Loại"), C("location", "Location", "Vị trí"), C("last", "Last calibration", "HC gần nhất", "date"),
                        C("next", "Next calibration", "HC kế tiếp", "date"), C("days_left", "Days left", "Còn (ngày)", "int"), C("state", "State", "Trạng thái"), C("cycle", "Cycle (days)", "Chu kỳ (ngày)", "int"),
                        C("lab", "Lab", "Đơn vị HC")],
            "rows": rows, "summary": [S("Equipment", "Thiết bị", len(rows)), S("Overdue", "Quá hạn", sum(1 for r in rows if r["state"] == "OVERDUE")),
                                      S("Compliance (period)", "Tuân thủ (kỳ)", comp["rate"], "pct")], "chart": None}


# =========================================================
# MAINTENANCE
# =========================================================


def maintenance_history(cur, p):
    from app.modules.maintenance import service as ms
    wos = ms.load_wos(cur, "w.created_at >= %s AND w.created_at < %s", (p["start"], p["end"]))
    rows = [{"wo_no": w["wo_no"], "asset_type": w["asset_type"], "asset": w["asset_code"], "type": w["wo_type"], "priority": w["priority"], "status": w["status"], "task": w["task_name"],
             "technician": w["technician"], "start": w["actual_start"], "finish": w["actual_finish"], "work_min": w["work_minutes"], "downtime_min": w["downtime_actual_minutes"],
             "parts": w["parts_cost"], "labor": w["labor_cost"], "total": w["total_cost"]} for w in wos]
    return {"columns": [C("wo_no", "WO No.", "Số WO"), C("asset_type", "Asset type", "Loại tài sản"), C("asset", "Asset", "Tài sản"), C("type", "Type", "Loại"), C("priority", "Priority", "Ưu tiên"),
                        C("status", "Status", "Trạng thái"), C("task", "Task", "Công việc"), C("technician", "Technician", "Kỹ thuật viên"), C("start", "Start", "Bắt đầu", "datetime"),
                        C("finish", "Finish", "Kết thúc", "datetime"), C("work_min", "Work (min)", "Thời gian (phút)", "num"), C("downtime_min", "Downtime (min)", "Dừng (phút)", "num"),
                        C("parts", "Parts cost", "Chi phí phụ tùng", "money"), C("labor", "Labor cost", "Chi phí nhân công", "money"), C("total", "Total cost", "Tổng chi phí", "money")],
            "rows": rows, "summary": [S("Work orders", "Work Order", len(rows)), S("Total cost", "Tổng chi phí", round(sum(r["total"] for r in rows)), "money"),
                                      S("MTTR (h)", "MTTR (giờ)", ms.mttr(wos), "num"), S("PM compliance", "Tuân thủ PM", ms.pm_compliance(wos, p["date_from"], p["date_to"]), "pct")], "chart": None}


def maintenance_cost(cur, p):
    from app.modules.maintenance import service as ms
    wos = ms.load_wos(cur, "w.status IN ('COMPLETED','CLOSED','VERIFICATION') AND COALESCE(w.completed_at, w.actual_finish) >= %s AND COALESCE(w.completed_at, w.actual_finish) < %s",
                      (p["start"], p["end"]))
    by = defaultdict(lambda: {"wos": 0, "parts": 0.0, "labor": 0.0, "other": 0.0, "total": 0.0})
    for w in wos:
        b = by[(w["asset_type"], w["asset_code"])]
        b["wos"] += 1
        b["parts"] += w["parts_cost"]
        b["labor"] += w["labor_cost"]
        b["other"] += float(w["outsourcing_cost"] or 0) + float(w["tool_cost"] or 0) + float(w["other_cost"] or 0)
        b["total"] += w["total_cost"]
    rows = sorted([{"asset_type": k[0], "asset": k[1], **{x: round(y, 2) if isinstance(y, float) else y for x, y in v.items()}} for k, v in by.items()], key=lambda r: -r["total"])
    return {"columns": [C("asset_type", "Asset type", "Loại tài sản"), C("asset", "Asset", "Tài sản"), C("wos", "Work orders", "Số WO", "int"), C("parts", "Parts", "Phụ tùng", "money"),
                        C("labor", "Labor", "Nhân công", "money"), C("other", "Other", "Khác", "money"), C("total", "Total", "Tổng", "money")],
            "rows": rows, "summary": [S("Total cost", "Tổng chi phí", round(sum(r["total"] for r in rows)), "money")], "chart": {"type": "bar", "x": "asset", "series": ["parts", "labor", "other"], "stacked": True}}


def maintenance_plans(cur, p):
    cur.execute("""SELECT plan_no, asset_type, task_name, wo_type, plan_type, interval_value, frequency_unit, next_due_date, last_done_at, priority, technician, status, source
                   FROM maintenance_plans ORDER BY status='ACTIVE' DESC, next_due_date""")
    rows = cur.fetchall()
    today = date.today()
    for r in rows:
        r["overdue"] = "YES" if r["status"] == "ACTIVE" and r["next_due_date"] and r["next_due_date"] < today else ""
    return {"columns": [C("plan_no", "Plan No.", "Số kế hoạch"), C("asset_type", "Asset type", "Loại tài sản"), C("task_name", "Task", "Công việc"), C("wo_type", "Type", "Loại"),
                        C("plan_type", "Plan type", "Chu kỳ"), C("interval_value", "Interval", "Khoảng", "int"), C("frequency_unit", "Unit", "Đơn vị"), C("next_due_date", "Next due", "Hạn kế tiếp", "date"),
                        C("last_done_at", "Last done", "Lần gần nhất", "datetime"), C("priority", "Priority", "Ưu tiên"), C("technician", "Technician", "Kỹ thuật viên"), C("status", "Status", "Trạng thái"),
                        C("overdue", "Overdue", "Quá hạn")],
            "rows": rows, "summary": [S("Plans", "Kế hoạch", len(rows)), S("Overdue", "Quá hạn", sum(1 for r in rows if r["overdue"]))], "chart": None}


# =========================================================
# TRACEABILITY / ADMINISTRATION / SYSTEM
# =========================================================


def product_lots(cur, p):
    from app.modules.traceability import service as ts
    ts.sync_lots(cur)
    cur.execute(ts.LOT_SELECT + " WHERE pl.production_date BETWEEN %s AND %s ORDER BY pl.production_date, pl.lot_no", (p["date_from"], p["date_to"]))
    rows = []
    for x in cur.fetchall():
        mats = ts.wo_materials(cur, x["work_order_id"])
        rows.append({"lot_no": x["lot_no"], "date": x["production_date"], "shift": x["shift_name"], "wo_no": x["wo_no"], "product": x["product_name"], "machine": x["machine_code"],
                     "mold": x["mold_code"], "good": x["good_qty"], "reject": x["reject_qty"], "materials": ", ".join(m["lot_no"] for m in mats), "customer": x["customer_name"],
                     "quality": ts.lot_quality(cur, x)["status"]})
    return {"columns": [C("lot_no", "Product lot", "Lô thành phẩm"), C("date", "Production date", "Ngày SX", "date"), C("shift", "Shift", "Ca"), C("wo_no", "Work Order", "Work Order"),
                        C("product", "Product", "Sản phẩm"), C("machine", "Machine", "Máy"), C("mold", "Mold", "Khuôn"), C("good", "Good", "Đạt", "int"), C("reject", "Reject", "Lỗi", "int"),
                        C("materials", "Material lots", "Lô nguyên liệu"), C("customer", "Customer", "Khách hàng"), C("quality", "Quality", "Chất lượng")],
            "rows": rows, "summary": [S("Product lots", "Lô thành phẩm", len(rows)), S("Good pieces", "Sản phẩm đạt", sum(int(r["good"]) for r in rows))], "chart": None}


def material_lot_usage(cur, p):
    cur.execute("""SELECT l.lot_no, m.material_code, l.supplier, l.supplier_lot_no, w.wo_no, e.equipment_code, SUM(u.qty) qty, MIN(u.used_at) first_used, m.unit
                   FROM material_usages u JOIN material_lots l ON l.id=u.lot_id JOIN materials m ON m.id=u.material_id JOIN production_plan_details w ON w.id=u.work_order_id
                   LEFT JOIN equipment e ON e.id=u.machine_id WHERE u.status='ACTIVE' AND u.used_at >= %s AND u.used_at < %s
                   GROUP BY l.lot_no, m.material_code, l.supplier, l.supplier_lot_no, w.wo_no, e.equipment_code, m.unit ORDER BY l.lot_no, w.wo_no""", (p["start"], p["end"]))
    rows = [{**r, "qty": round(f(r["qty"]), 3)} for r in cur.fetchall()]
    return {"columns": [C("lot_no", "Material lot", "Lô nguyên liệu"), C("material_code", "Material", "Mã NL"), C("supplier", "Supplier", "Nhà cung cấp"), C("supplier_lot_no", "Supplier lot", "Lô NCC"),
                        C("wo_no", "Work Order", "Work Order"), C("equipment_code", "Machine", "Máy"), C("qty", "Qty", "SL", "num"), C("unit", "Unit", "ĐVT"),
                        C("first_used", "First used", "Dùng lần đầu", "datetime")],
            "rows": rows, "summary": [S("Links", "Số liên kết", len(rows))], "chart": None}


def users_roles(cur, p):
    cur.execute("""SELECT u.employee_code, u.is_active, u.last_login_at, u.created_at, GROUP_CONCAT(r.role_name ORDER BY r.role_name SEPARATOR ', ') roles
                   FROM mes_users u LEFT JOIN user_roles ur ON ur.user_id=u.id LEFT JOIN roles r ON r.id=ur.role_id GROUP BY u.id, u.employee_code, u.is_active, u.last_login_at, u.created_at
                   ORDER BY u.employee_code""")
    rows = [{**r, "is_active": "YES" if r["is_active"] else "NO"} for r in cur.fetchall()]
    return {"columns": [C("employee_code", "Employee", "Mã nhân viên"), C("roles", "Roles", "Vai trò"), C("is_active", "Active", "Hoạt động"), C("last_login_at", "Last login", "Đăng nhập gần nhất", "datetime"),
                        C("created_at", "Created", "Ngày tạo", "datetime")],
            "rows": rows, "summary": [S("Users", "Người dùng", len(rows)), S("Active", "Đang hoạt động", sum(1 for r in rows if r["is_active"] == "YES"))], "chart": None}


def audit_log(cur, p):
    cur.execute("""SELECT event_at, user_name, user_code, role_name, module, action, data_type, record_id, ip_address, result, severity, message
                   FROM audit_logs WHERE event_at >= %s AND event_at < %s ORDER BY event_at DESC LIMIT 20000""", (p["start"], p["end"]))
    rows = cur.fetchall()
    by = Counter(r["severity"] for r in rows)
    return {"columns": [C("event_at", "Time", "Thời gian", "datetime"), C("user_name", "User", "Người dùng"), C("role_name", "Role", "Vai trò"), C("module", "Module", "Phân hệ"),
                        C("action", "Action", "Hành động"), C("data_type", "Data type", "Loại dữ liệu"), C("record_id", "Record ID", "Mã bản ghi"), C("ip_address", "IP address", "Địa chỉ IP"),
                        C("result", "Result", "Kết quả"), C("severity", "Severity", "Mức độ"), C("message", "Message", "Thông báo")],
            "rows": rows, "summary": [S("Activities", "Hoạt động", len(rows)), S("Failed", "Thất bại", sum(1 for r in rows if r["result"] == "FAILED")),
                                      S("High severity", "Mức cao", by.get("HIGH", 0) + by.get("CRITICAL", 0))], "chart": None}


def master_data_summary(cur, p):
    items = [("Products", "Sản phẩm", "products"), ("Materials", "Nguyên vật liệu", "materials"), ("Machines & equipment", "Máy & thiết bị", "equipment"), ("Molds", "Khuôn", "molds"),
             ("Production tools", "Dụng cụ sản xuất", "production_tools"), ("Measuring equipment", "Thiết bị đo", "measuring_equipment"), ("Customers", "Khách hàng", "customers"),
             ("Quality standards", "Tiêu chuẩn chất lượng", "quality_standards"), ("Reason codes", "Mã lý do", "reason_codes"), ("Shifts", "Ca làm việc", "shifts"),
             ("Factory structure nodes", "Cấu trúc nhà máy", "factory_structure_nodes")]
    rows = []
    for en, vi, t in items:
        cur.execute(f"SELECT COUNT(*) n, MAX(updated_at) u FROM {t}")
        r = cur.fetchone()
        rows.append({"master": en, "master_vi": vi, "records": int(r["n"]), "updated": r["u"]})
    return {"columns": [C("master", "Master data", "Danh mục"), C("master_vi", "Name (VI)", "Tên"), C("records", "Records", "Số bản ghi", "int"), C("updated", "Last updated", "Cập nhật gần nhất", "datetime")],
            "rows": rows, "summary": [S("Master tables", "Số danh mục", len(rows))], "chart": {"type": "bar", "x": "master", "series": ["records"]}}


def system_settings(cur, p):
    cur.execute("SHOW COLUMNS FROM system_settings")
    cols = {r["Field"] for r in cur.fetchall()}
    cur.execute(f"SELECT setting_key, setting_value{', description' if 'description' in cols else ''}{', updated_at' if 'updated_at' in cols else ''} FROM system_settings ORDER BY setting_key")
    rows = cur.fetchall()
    return {"columns": [C("setting_key", "Setting", "Thiết lập"), C("setting_value", "Value", "Giá trị"), C("description", "Description", "Mô tả"), C("updated_at", "Updated", "Cập nhật", "datetime")],
            "rows": rows, "summary": [S("Settings", "Số thiết lập", len(rows))], "chart": None}


MODULES = ["PRODUCTION", "MACHINE", "MOLD", "MATERIAL", "QUALITY", "MAINTENANCE", "TRACEABILITY", "ADMINISTRATION", "SYSTEM"]

# code, module, category, en, vi, description (en), run
REPORTS = [
    ("PRODUCTION_SUMMARY", "PRODUCTION", "SUMMARY", "Production Summary Report", "Báo cáo tổng hợp sản xuất", "Good / reject / yield per work order in the period", production_summary),
    ("PRODUCTION_DAILY", "PRODUCTION", "TREND", "Daily Production Report", "Báo cáo sản lượng theo ngày", "Daily good, reject, yield and PPM", production_daily),
    ("PRODUCTION_SHIFT", "PRODUCTION", "EFFICIENCY", "Production by Shift Report", "Báo cáo sản lượng theo ca", "Output and yield compared by shift", production_shift),
    ("PRODUCTION_ORDERS", "PRODUCTION", "PROGRESS", "Production Order Progress Report", "Báo cáo tiến độ lệnh sản xuất", "Order quantity vs produced, late orders", production_orders),
    ("MACHINE_UTILIZATION", "MACHINE", "UTILIZATION", "Machine Utilization Report", "Báo cáo hiệu suất sử dụng máy", "Running / idle / down / maintenance hours per machine", machine_utilization),
    ("MACHINE_OEE", "MACHINE", "EFFICIENCY", "OEE by Machine Report", "Báo cáo OEE theo máy", "Availability, performance, quality and OEE per machine", machine_oee),
    ("DOWNTIME_ANALYSIS", "MACHINE", "DOWNTIME", "Downtime Analysis Report", "Báo cáo phân tích dừng máy", "Downtime minutes by reason (Pareto)", downtime_analysis),
    ("DOWNTIME_EVENTS", "MACHINE", "DOWNTIME", "Downtime Event List", "Danh sách sự kiện dừng máy", "Every downtime event with reason, duration and action", downtime_events),
    ("ALARM_HISTORY", "MACHINE", "ALARM", "Alarm History Report", "Báo cáo lịch sử cảnh báo", "Machine alarms with priority and response time", alarm_history),
    ("MOLD_PERFORMANCE", "MOLD", "PERFORMANCE", "Mold Performance Report", "Báo cáo hiệu suất khuôn", "Shots, life used, health, NG and downtime per mold", mold_performance),
    ("MOLD_SHOTS", "MOLD", "SHOT_COUNTER", "Mold Shot Counter Report", "Báo cáo bộ đếm shot khuôn", "Daily shots per mold", mold_shot_counter),
    ("MOLD_INSTALLATIONS", "MOLD", "HISTORY", "Mold Installation History Report", "Báo cáo lịch sử lắp / tháo khuôn", "Install / remove events with machine, work order and shots", mold_installations),
    ("MATERIAL_STOCK", "MATERIAL", "INVENTORY", "Material Stock Status Report", "Báo cáo tồn kho vật tư", "Available, reserved, hold vs safety stock", material_stock),
    ("MATERIAL_CONSUMPTION", "MATERIAL", "CONSUMPTION", "Material Consumption Report", "Báo cáo tiêu thụ vật tư", "Consumption per material in the period", material_consumption),
    ("MATERIAL_TRANSACTIONS", "MATERIAL", "INVENTORY", "Material Lot Transaction Report", "Báo cáo giao dịch lô vật tư", "Receiving, issue, return, consumption per lot", material_transactions),
    ("MATERIAL_SHORTAGE", "MATERIAL", "PLANNING", "Material Shortage Report", "Báo cáo thiếu vật tư", "Open requirements of planned work orders", material_shortage),
    ("QUALITY_INSPECTION", "QUALITY", "QUALITY", "Quality Inspection Summary", "Báo cáo tổng hợp kiểm tra chất lượng", "Samples, NG rate and FPY per day and inspection type", quality_inspection),
    ("NG_REPORT", "QUALITY", "NG", "NG Report", "Báo cáo NG", "NG records with root cause and corrective action", ng_report),
    ("NG_PARETO", "QUALITY", "NG", "NG Pareto Report", "Báo cáo Pareto lỗi", "NG pieces by defect with cumulative share", ng_pareto),
    ("SPC_CAPABILITY", "QUALITY", "SPC", "SPC Capability Report", "Báo cáo năng lực quá trình (SPC)", "Cp / Cpk and SPC status per characteristic", spc_capability),
    ("CALIBRATION_STATUS", "QUALITY", "CALIBRATION", "Calibration Status Report", "Báo cáo tình trạng hiệu chuẩn", "Measuring equipment calibration dates and compliance", calibration_status),
    ("MAINTENANCE_HISTORY", "MAINTENANCE", "HISTORY", "Maintenance History Report", "Báo cáo lịch sử bảo trì", "Maintenance work orders, time, downtime and cost", maintenance_history),
    ("MAINTENANCE_COST", "MAINTENANCE", "COST", "Maintenance Cost Report", "Báo cáo chi phí bảo trì", "Parts, labor and other cost per asset", maintenance_cost),
    ("MAINTENANCE_PLANS", "MAINTENANCE", "PLANNING", "Maintenance Plan Report", "Báo cáo kế hoạch bảo trì", "Preventive plans with next due date", maintenance_plans),
    ("PRODUCT_LOTS", "TRACEABILITY", "TRACEABILITY", "Product Lot Register", "Sổ lô thành phẩm", "Product lots with work order, machine, mold and material lots", product_lots),
    ("MATERIAL_LOT_USAGE", "TRACEABILITY", "TRACEABILITY", "Material Lot Usage Report", "Báo cáo sử dụng lô nguyên liệu", "Material lot -> work order links (forward trace)", material_lot_usage),
    ("USERS_ROLES", "ADMINISTRATION", "SECURITY", "Users & Roles Report", "Báo cáo người dùng & vai trò", "MES users with roles and last login", users_roles),
    ("AUDIT_LOG", "ADMINISTRATION", "SECURITY", "Audit Log Report", "Báo cáo nhật ký hệ thống", "User and system activities with result and severity", audit_log),
    ("MASTER_DATA", "SYSTEM", "MASTER_DATA", "Master Data Summary", "Tổng hợp dữ liệu danh mục", "Record count of every master table", master_data_summary),
    ("SYSTEM_SETTINGS", "SYSTEM", "SETTINGS", "System Settings Report", "Báo cáo thiết lập hệ thống", "Current values of the system settings", system_settings),
]

BY_CODE = {r[0]: r for r in REPORTS}

DESCRIPTION_VI = {
    "PRODUCTION_SUMMARY": "Sản lượng đạt / lỗi / tỷ lệ đạt theo từng Work Order trong kỳ",
    "PRODUCTION_DAILY": "Sản lượng đạt, lỗi, tỷ lệ đạt và PPM theo ngày",
    "PRODUCTION_SHIFT": "So sánh sản lượng và tỷ lệ đạt giữa các ca",
    "PRODUCTION_ORDERS": "Số lượng đặt so với đã sản xuất, lệnh trễ hạn",
    "MACHINE_UTILIZATION": "Số giờ chạy / chờ / dừng / bảo trì theo máy",
    "MACHINE_OEE": "Khả dụng, hiệu năng, chất lượng và OEE theo máy",
    "DOWNTIME_ANALYSIS": "Số phút dừng máy theo nguyên nhân (Pareto)",
    "DOWNTIME_EVENTS": "Từng sự kiện dừng máy với nguyên nhân, thời lượng và hành động",
    "ALARM_HISTORY": "Cảnh báo máy theo mức độ và thời gian phản hồi",
    "MOLD_PERFORMANCE": "Shot, tuổi thọ đã dùng, sức khỏe, NG và dừng máy theo khuôn",
    "MOLD_SHOTS": "Số shot theo ngày của từng khuôn",
    "MOLD_INSTALLATIONS": "Lịch sử lắp / tháo khuôn với máy, Work Order và số shot",
    "MATERIAL_STOCK": "Tồn khả dụng, đã giữ, tạm giữ so với tồn an toàn",
    "MATERIAL_CONSUMPTION": "Lượng tiêu thụ theo nguyên vật liệu trong kỳ",
    "MATERIAL_TRANSACTIONS": "Nhập, cấp, trả, tiêu thụ theo từng lô",
    "MATERIAL_SHORTAGE": "Nhu cầu vật tư còn thiếu của các Work Order đã lên kế hoạch",
    "QUALITY_INSPECTION": "Số mẫu, tỷ lệ NG và FPY theo ngày và loại kiểm tra",
    "NG_REPORT": "Hồ sơ NG với nguyên nhân gốc và hành động khắc phục",
    "NG_PARETO": "Số sản phẩm NG theo lỗi và tỷ trọng lũy kế",
    "SPC_CAPABILITY": "Cp / Cpk và trạng thái SPC theo đặc tính",
    "CALIBRATION_STATUS": "Ngày hiệu chuẩn và tỷ lệ tuân thủ của thiết bị đo",
    "MAINTENANCE_HISTORY": "Work Order bảo trì, thời gian, dừng máy và chi phí",
    "MAINTENANCE_COST": "Chi phí phụ tùng, nhân công và khác theo tài sản",
    "MAINTENANCE_PLANS": "Kế hoạch bảo trì phòng ngừa và hạn kế tiếp",
    "PRODUCT_LOTS": "Lô thành phẩm với Work Order, máy, khuôn và lô nguyên liệu",
    "MATERIAL_LOT_USAGE": "Liên kết lô nguyên liệu → Work Order (truy xuất xuôi)",
    "USERS_ROLES": "Người dùng MES với vai trò và lần đăng nhập gần nhất",
    "AUDIT_LOG": "Hoạt động của người dùng và hệ thống với kết quả và mức độ",
    "MASTER_DATA": "Số bản ghi của từng danh mục",
    "SYSTEM_SETTINGS": "Giá trị hiện tại của các thiết lập hệ thống",
}
