"""Demo data for Mold Management (3.1 - 3.7). Re-runnable: rebuilds mold_installations / mold_events /
mold_shot_daily from scratch (source DEMO for synthetic history) and upserts demo molds MLD-005..MLD-009.

- Location areas: maintenance / repair / scrap nodes and rack capacities.
- MLD-005 (second Connector Housing A mold) takes over WO-260923-003 on IMM-003, so one mold is never
  mounted on two machines (MLD-001 runs WO-260923-001 on IMM-001).
- 30 days of install / remove history (planned and unplanned), maintenance / repair / lock events and daily shots.

Run with:

    python -m scripts.seed_mold_management_demo
"""

import math
from datetime import date, datetime, timedelta

from app.core.database import get_vcc_plastics_connection

D = lambda s: datetime.strptime(s, "%Y-%m-%d %H:%M")
TECHS = ["Nguyen Van A", "Tran Van B", "Le Van C", "Pham Van D"]


def node_type(cur, code, name, category, level):
    cur.execute("SELECT id FROM mold_location_node_types WHERE type_code=%s", (code,))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE mold_location_node_types SET location_category=%s WHERE id=%s", (category, row["id"]))
        return row["id"]
    cur.execute("""INSERT INTO mold_location_node_types (type_code, type_name, level_order, is_active, sort_order, location_category, created_by)
                   VALUES (%s,%s,%s,1,%s,%s,'DEMO')""", (code, name, level, level, category))
    return cur.lastrowid


def node(cur, code, name, type_id, capacity):
    cur.execute("SELECT id FROM mold_location_nodes WHERE node_code=%s", (code,))
    row = cur.fetchone()
    if row:
        cur.execute("UPDATE mold_location_nodes SET capacity=%s WHERE id=%s", (capacity, row["id"]))
        return row["id"]
    cur.execute("""INSERT INTO mold_location_nodes (node_code, node_name, node_type_id, parent_id, level_no, sort_order, status, capacity, created_by)
                   VALUES (%s,%s,%s,NULL,1,10,'ACTIVE',%s,'DEMO')""", (code, name, type_id, capacity))
    return cur.lastrowid


def upsert_mold(cur, code, **f):
    cur.execute("SELECT id FROM molds WHERE mold_code=%s", (code,))
    row = cur.fetchone()
    if row:
        sets = ", ".join(f"{k}=%s" for k in f)
        cur.execute(f"UPDATE molds SET {sets} WHERE id=%s", (*f.values(), row["id"]))
        return row["id"]
    cols = ["mold_code", *f.keys(), "is_active", "created_by"]
    cur.execute(f"INSERT INTO molds ({','.join(cols)}) VALUES ({','.join(['%s'] * len(cols))})", (code, *f.values(), 1, "DEMO"))
    return cur.lastrowid


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        # ---- locations
        node_type(cur, "WAREHOUSE", "Warehouse", "STORAGE", 1)
        node_type(cur, "RACK", "Rack / Shelf", "STORAGE", 2)
        t_mt = node_type(cur, "MAINT_AREA", "Maintenance Area", "MAINTENANCE", 1)
        t_rp = node_type(cur, "REPAIR_AREA", "Repair Area", "REPAIR", 1)
        t_sc = node_type(cur, "SCRAP_AREA", "Scrap Area", "SCRAP", 1)
        cur.execute("UPDATE mold_location_nodes SET capacity=12 WHERE node_code IN ('RACK-A1','RACK-A2')")
        cur.execute("SELECT id, node_code FROM mold_location_nodes")
        loc = {r["node_code"]: r["id"] for r in cur.fetchall()}
        loc["MT-01"] = node(cur, "MT-01", "Mold Maintenance Area", t_mt, 6)
        loc["RP-01"] = node(cur, "RP-01", "Mold Repair Area", t_rp, 4)
        loc["SC-01"] = node(cur, "SC-01", "Scrap Area", t_sc, 10)

        # ---- molds
        m1 = upsert_mold(cur, "MLD-001", revision="02", pm_interval_shot=50000, pm_interval_days=30, last_pm_at=D("2026-09-05 14:00"), last_pm_shot=90000,
                         current_location_id=loc["RACK-A1"])
        m2 = upsert_mold(cur, "MLD-002", revision="01", pm_interval_shot=30000, pm_interval_days=45, last_pm_at=D("2026-08-20 10:00"), last_pm_shot=20000,
                         current_location_id=loc["RACK-A1"])
        m3 = upsert_mold(cur, "MLD-003", revision="03", pm_interval_shot=60000, pm_interval_days=60, last_pm_at=D("2026-07-28 09:00"), last_pm_shot=550000,
                         status="IN_MAINTENANCE", current_location_id=loc["MT-01"], current_machine_id=None,
                         status_since=D("2026-09-23 11:00"), location_since=D("2026-09-23 11:10"))
        m4 = upsert_mold(cur, "MLD-004", revision="05", pm_interval_shot=40000, pm_interval_days=30, last_pm_at=D("2026-08-25 08:00"), last_pm_shot=960000,
                         status="IN_REPAIR", current_location_id=loc["RP-01"], current_machine_id=None,
                         status_since=D("2026-09-19 15:30"), location_since=D("2026-09-19 16:00"))
        m5 = upsert_mold(cur, "MLD-005", mold_name="Connector Housing A Mold #2", mold_type="2 Plate", mold_group="Connector", cavity_count=4, status="IN_PRODUCTION",
                         current_location_id=loc["RACK-A2"], design_shot=500000, current_shot=210500, weight_kg=318, length_mm=550, width_mm=450, height_mm=480,
                         manufacturer="VCC Toolmaking", manufacture_date=date(2025, 3, 15), revision="01", pm_interval_shot=50000, pm_interval_days=30,
                         last_pm_at=D("2026-09-10 09:00"), last_pm_shot=180000, current_machine_id=3, status_since=D("2026-09-24 06:30"), location_since=D("2026-09-24 06:30"))
        m6 = upsert_mold(cur, "MLD-006", mold_name="Connector Cap Mold", mold_type="3 Plate", mold_group="Connector", cavity_count=2, status="AVAILABLE",
                         current_location_id=loc["RACK-A1"], design_shot=400000, current_shot=352000, weight_kg=260, length_mm=500, width_mm=420, height_mm=450,
                         manufacturer="Fu Chun Shin", manufacture_date=date(2022, 6, 1), revision="02", pm_interval_shot=40000, pm_interval_days=60,
                         last_pm_at=D("2026-08-12 10:00"), last_pm_shot=340000, current_machine_id=None, status_since=D("2026-08-12 16:00"), location_since=D("2026-08-12 16:00"))
        m7 = upsert_mold(cur, "MLD-007", mold_name="Terminal Block Mold", mold_type="Hot Runner", mold_group="Terminal", cavity_count=8, status="LOCKED",
                         current_location_id=loc["RACK-A2"], design_shot=600000, current_shot=240000, weight_kg=480, length_mm=650, width_mm=520, height_mm=560,
                         manufacturer="VCC Toolmaking", manufacture_date=date(2024, 1, 20), revision="03", pm_interval_shot=60000, pm_interval_days=45,
                         last_pm_at=D("2026-09-01 09:00"), last_pm_shot=200000, lock_reason="Flash defect - quality hold QH-0922", current_machine_id=None,
                         status_since=D("2026-09-22 14:20"), location_since=D("2026-09-22 15:00"))
        m8 = upsert_mold(cur, "MLD-008", mold_name="Old Bezel Mold", mold_type="2 Plate", mold_group="Cover", cavity_count=2, status="RETIRED",
                         current_location_id=loc["SC-01"], design_shot=500000, current_shot=505300, weight_kg=300, manufacturer="VCC Toolmaking",
                         manufacture_date=date(2018, 5, 10), revision="04", current_machine_id=None, status_since=D("2026-07-01 10:00"), location_since=D("2026-07-01 11:00"))
        m9 = upsert_mold(cur, "MLD-009", mold_name="Clip Mold", mold_type="Stack Mold", mold_group="Clip", cavity_count=16, status="AVAILABLE",
                         current_location_id=None, design_shot=300000, current_shot=12000, weight_kg=150, manufacturer="Fu Chun Shin",
                         manufacture_date=date(2026, 7, 1), revision="01", pm_interval_shot=30000, pm_interval_days=90, last_pm_at=D("2026-08-30 09:00"),
                         last_pm_shot=0, current_machine_id=None, status_since=D("2026-09-18 10:00"), location_since=D("2026-09-18 10:00"))
        cur.execute("UPDATE molds SET current_machine_id=1, status='IN_PRODUCTION', status_since=%s, location_since=%s WHERE id=%s", (D("2026-09-24 05:45"), D("2026-09-24 05:45"), m1))
        cur.execute("UPDATE molds SET current_machine_id=2, status='IN_PRODUCTION', status_since=%s, location_since=%s WHERE id=%s", (D("2026-09-24 06:15"), D("2026-09-24 06:15"), m2))
        for mid in (m5,):
            cur.execute("SELECT id FROM mold_products WHERE mold_id=%s AND product_id=1", (mid,))
            if not cur.fetchone():
                cur.execute("INSERT INTO mold_products (mold_id, product_id, cavity_quantity, is_primary, status, created_by) VALUES (%s,1,4,1,'ACTIVE','DEMO')", (mid,))
        # WO-260923-003 runs on MLD-005 (IMM-003); its logs follow
        cur.execute("SELECT id FROM production_plan_details WHERE wo_no='WO-260923-003'")
        wo3 = cur.fetchone()
        if wo3:
            cur.execute("UPDATE production_plan_details SET mold_id=%s WHERE id=%s", (m5, wo3["id"]))
            cur.execute("UPDATE production_execution_logs SET mold_id=%s WHERE work_order_id=%s", (m5, wo3["id"]))
        cur.execute("SELECT id, wo_no FROM production_plan_details WHERE wo_no LIKE 'WO-260923-00%%'")
        wo = {r["wo_no"]: r["id"] for r in cur.fetchall()}

        # ---- rebuild history
        for t in ("mold_installations", "mold_events", "mold_shot_daily"):
            cur.execute(f"DELETE FROM {t}")
        cur.execute("SELECT id, cavity_count FROM molds")
        cav = {r["id"]: int(r["cavity_count"] or 1) for r in cur.fetchall()}
        installs = [
            # mold, machine, wo, installed, install_min, removed, remove_min, reason, remark
            (m1, 1, None, "2026-08-26 06:10", 20, "2026-08-29 17:40", 16, "END_OF_PLAN", None),
            (m2, 2, None, "2026-08-27 06:30", 24, "2026-08-30 12:10", 18, "NG_HIGH", "Short shot / flash rate 4.8 %"),
            (m3, 3, None, "2026-08-26 07:00", 26, "2026-09-02 18:00", 17, "CHANGE_PRODUCT", None),
            (m4, 4, None, "2026-08-28 06:00", 30, "2026-09-04 09:30", 22, "END_OF_PLAN", None),
            (m6, 1, None, "2026-08-30 06:20", 19, "2026-09-01 16:50", 14, "END_OF_PLAN", None),
            (m1, 2, None, "2026-09-01 06:00", 17, "2026-09-05 11:30", 15, "PM", "PM at 90k shots"),
            (m2, 1, None, "2026-09-02 06:40", 21, "2026-09-06 18:10", 16, "END_OF_PLAN", None),
            (m3, 4, None, "2026-09-05 06:30", 25, "2026-09-11 17:20", 18, "END_OF_PLAN", None),
            (m5, 3, None, "2026-09-03 07:00", 22, "2026-09-09 08:40", 19, "PM", "PM at 180k shots"),
            (m4, 2, None, "2026-09-08 06:15", 28, "2026-09-10 14:05", 21, "BREAKDOWN", "Ejector pin broken"),
            (m1, 1, None, "2026-09-08 06:10", 18, "2026-09-15 17:30", 15, "CHANGE_PRODUCT", None),
            (m5, 3, None, "2026-09-11 06:20", 20, "2026-09-16 18:00", 17, "END_OF_PLAN", None),
            (m2, 2, None, "2026-09-12 06:30", 23, "2026-09-18 12:40", 16, "NG_HIGH", "Sink mark on rib"),
            (m3, 4, None, "2026-09-13 07:10", 27, "2026-09-19 09:15", 18, "END_OF_PLAN", None),
            (m4, 1, None, "2026-09-17 06:00", 31, "2026-09-19 15:10", 24, "BREAKDOWN", "Core insert cracked"),
            (m1, 3, None, "2026-09-18 06:30", 19, "2026-09-22 17:00", 15, "END_OF_PLAN", None),
            (m9, 3, None, "2026-09-16 19:30", 35, "2026-09-18 05:50", 20, "OTHER", "Trial run finished"),
            (m3, 3, wo.get("WO-260923-004"), "2026-09-23 06:40", 20, "2026-09-23 10:55", 15, "PM", "PM at 612k shots"),
            (m2, 2, wo.get("WO-260923-005"), "2026-09-23 07:35", 22, "2026-09-23 10:10", 16, "END_OF_PLAN", "WO-260923-005"),
            (m1, 1, wo.get("WO-260923-001"), "2026-09-24 05:45", 18, None, None, None, None),
            (m2, 2, wo.get("WO-260923-002"), "2026-09-24 06:15", 20, None, None, None, None),
            (m5, 3, wo.get("WO-260923-003"), "2026-09-24 06:30", 16, None, None, None, None),
        ]
        planned = {"END_OF_PLAN": 1, "CHANGE_PRODUCT": 1, "PM": 1, "NG_HIGH": 0, "BREAKDOWN": 0, "OTHER": 0}
        ops = ["Tran Van B", "Le Van C", "Pham Van D", "Hoang Van E"]
        daily = {}
        today = date.today()
        for n, (mid, mc, wid, a, amin, b, bmin, reason, remark) in enumerate(installs):
            ia, rb = D(a), D(b) if b else None
            by = ops[n % len(ops)]
            cur.execute("""INSERT INTO mold_installations (mold_id, machine_id, work_order_id, installed_at, installed_by, install_minutes, removed_at, removed_by,
                           remove_minutes, removal_reason, planned, remark, source) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'DEMO')""",
                        (mid, mc, wid, ia, by, amin, rb, ops[(n + 1) % len(ops)] if rb else None, bmin, reason, planned.get(reason), remark))
            cur.execute("INSERT INTO mold_events (mold_id, event_type, from_status, to_status, machine_id, work_order_id, actor, event_at) VALUES (%s,'INSTALL','AVAILABLE','IN_PRODUCTION',%s,%s,%s,%s)",
                        (mid, mc, wid, by, ia))
            if rb:
                to = {"PM": "IN_MAINTENANCE", "BREAKDOWN": "IN_REPAIR"}.get(reason, "AVAILABLE")
                cur.execute("""INSERT INTO mold_events (mold_id, event_type, from_status, to_status, machine_id, work_order_id, reason, actor, event_at)
                               VALUES (%s,'REMOVE','IN_PRODUCTION',%s,%s,%s,%s,%s,%s)""", (mid, to, mc, wid, f"{reason}{f': {remark}' if remark else ''}", ops[(n + 1) % 4], rb))
            # synthetic shots for history (today and yesterday come from real production logs below)
            if not wid:
                end = rb or datetime.now()
                t = ia
                while t < end:
                    nxt = min(datetime.combine(t.date() + timedelta(days=1), datetime.min.time()), end)
                    hours = (nxt - t).total_seconds() / 3600 * 0.78
                    daily[(mid, t.date())] = daily.get((mid, t.date()), 0) + int(hours * 3600 / 26)
                    t = nxt
        cur.execute("""SELECT mold_id, DATE(event_at) AS d, SUM(good_qty + reject_qty) AS pcs FROM production_execution_logs
                       WHERE mold_id IS NOT NULL AND event_type IN ('OUTPUT','REJECT') GROUP BY mold_id, DATE(event_at)""")
        for r in cur.fetchall():
            daily[(r["mold_id"], r["d"])] = daily.get((r["mold_id"], r["d"]), 0) + math.ceil(int(r["pcs"] or 0) / cav.get(r["mold_id"], 1))
        for (mid, d), s in daily.items():
            if s > 0 and d >= today - timedelta(days=45):
                cur.execute("INSERT INTO mold_shot_daily (mold_id, shot_date, shots, source) VALUES (%s,%s,%s,%s)", (mid, d, s, "MES" if d >= today - timedelta(days=1) else "DEMO"))

        # ---- maintenance / repair / lock / move events
        ev = [
            (m1, "MAINTENANCE_START", "AVAILABLE", "IN_MAINTENANCE", "PM_SHOT", None, None, "PM at 90k shots", "2026-09-05 11:40"),
            (m1, "MAINTENANCE_DONE", "IN_MAINTENANCE", "AVAILABLE", "PM_SHOT", TECHS[0], 2.5, "Check cooling, clean mold, replace O-rings", "2026-09-05 14:00"),
            (m1, "REPAIR_START", "AVAILABLE", "IN_REPAIR", "REPAIR", None, None, "Gate wear", "2026-07-20 08:00"),
            (m1, "REPAIR_DONE", "IN_REPAIR", "AVAILABLE", "REPAIR", TECHS[1], 6.0, "Gate insert replacement", "2026-07-20 16:00"),
            (m2, "MAINTENANCE_DONE", "IN_MAINTENANCE", "AVAILABLE", "PM_TIME", TECHS[2], 2.0, "Check ejection, lubrication", "2026-08-20 10:00"),
            (m3, "MAINTENANCE_DONE", "IN_MAINTENANCE", "AVAILABLE", "PM_SHOT", TECHS[0], 3.0, "General inspection, polish cavity", "2026-07-28 09:00"),
            (m3, "MAINTENANCE_START", "AVAILABLE", "IN_MAINTENANCE", "PM_SHOT", TECHS[3], None, "PM at 612k shots", "2026-09-23 11:00"),
            (m4, "REPAIR_START", "AVAILABLE", "IN_REPAIR", "REPAIR", None, None, "Ejector pin broken", "2026-09-10 14:20"),
            (m4, "REPAIR_DONE", "IN_REPAIR", "AVAILABLE", "REPAIR", TECHS[1], 18.0, "Ejector pins replaced", "2026-09-11 10:00"),
            (m4, "REPAIR_START", "AVAILABLE", "IN_REPAIR", "REPAIR", None, None, "Core insert cracked", "2026-09-19 15:30"),
            (m5, "MAINTENANCE_START", "AVAILABLE", "IN_MAINTENANCE", "PM_SHOT", None, None, "PM at 180k shots", "2026-09-09 08:50"),
            (m5, "MAINTENANCE_DONE", "IN_MAINTENANCE", "AVAILABLE", "PM_SHOT", TECHS[2], 2.2, "Clean vents, check cooling", "2026-09-10 09:00"),
            (m6, "MAINTENANCE_DONE", "IN_MAINTENANCE", "AVAILABLE", "PM_TIME", TECHS[3], 1.8, "Clean and rust protection", "2026-08-12 10:00"),
            (m7, "LOCK", "AVAILABLE", "LOCKED", None, None, None, "Flash defect - quality hold QH-0922", "2026-09-22 14:20"),
            (m8, "RETIRE", "AVAILABLE", "RETIRED", None, None, None, "Over design life, product discontinued", "2026-07-01 10:00"),
            (m9, "MAINTENANCE_DONE", "IN_MAINTENANCE", "AVAILABLE", "INSPECTION", TECHS[0], 1.0, "Incoming inspection", "2026-08-30 09:00"),
        ]
        for mid, et, fs, ts, mt, tech, dh, reason, at in ev:
            cur.execute("""INSERT INTO mold_events (mold_id, event_type, from_status, to_status, maintenance_type, technician, downtime_hours, reason, actor, event_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (mid, et, fs, ts, mt, tech, dh, reason, tech or "Tooling", D(at)))
        moves = [(m3, loc["RACK-A2"], loc["MT-01"], "2026-09-23 11:10"), (m4, loc["RACK-A1"], loc["RP-01"], "2026-09-19 16:00"),
                 (m7, loc["RACK-A1"], loc["RACK-A2"], "2026-09-22 15:00"), (m8, loc["RACK-A2"], loc["SC-01"], "2026-07-01 11:00"),
                 (m6, loc["MT-01"], loc["RACK-A1"], "2026-08-12 16:00")]
        for mid, a, b, at in moves:
            cur.execute("INSERT INTO mold_events (mold_id, event_type, from_location_id, to_location_id, actor, event_at) VALUES (%s,'MOVE',%s,%s,'Tooling',%s)", (mid, a, b, D(at)))
        conn.commit()
        print("Mold management demo data ready.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
