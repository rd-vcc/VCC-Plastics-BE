"""Demo data to try the Quality Standard flow end to end (standard -> inspection plan -> measurements -> OK/NG -> NG record).

Additive and re-runnable: only the rows created by this script (remark DEMO_TAG, gauges ME-1xx) are rebuilt,
the inspections of the real Work Orders are not touched.

- Calibrated gauges for every equipment type used by the active standards (the BLOCK control lets them be used)
- 7 days of completed inspections on QS-PACK-001, QS-HOME-001 and QS-ELEC-002 measured through the service
  (OK/NG judged against LSL/USL, NG records created by the plan completion), then back-dated:
    * Nắp chai: one short shot (weight below LSL) 3 days ago -> NG record closed with its 4M analysis
    * Hộp thực phẩm: wall thickness drifting to the LSL yesterday -> NG record under investigation
    * Vỏ ổ cắm (lắp ráp): insulation resistance below 100 MΩ 2 days ago (critical) -> CRITICAL NG record, new
- Open plans for today, ready to be measured on the Inspection screen

Run with:

    python -m scripts.seed_quality_standard_usage_demo
"""

import random
from datetime import date, datetime, time, timedelta

from app.core.database import get_vcc_plastics_connection
from app.modules.quality import service as qs

random.seed(23)

DEMO_TAG = "Dữ liệu demo tiêu chuẩn chất lượng"
QC = [("00465", "Lý Minh Quang (00465)"), ("00863", "Nguyễn Quang Tuấn (00863)"), ("00842", "Ngô Sỹ Hùng (00842)")]

# code, name, type code, type name, model, serial, location
GAUGES = [
    ("ME-101", "Panme đo ngoài 0–25mm (QC)", "MIC", "Panme", "293-821-30", "MT-26Q101", "QC IPQC - Line 1"),
    ("ME-102", "Đồng hồ so 0–10mm (QC)", "DIAL", "Đồng hồ so", "2046S", "MT-26Q102", "QC IPQC - Line 1"),
    ("ME-103", "Cân điện tử 0,01g (QC)", "SCALE", "Cân điện tử", "GX-2002A", "AD-26Q103", "QC IPQC - Line 2"),
    ("ME-104", "Đồng hồ đo điện trở cách điện", "MULTI", "Đồng hồ vạn năng", "IR4056", "HI-26Q104", "QC FQC - Line 1"),
    ("ME-105", "Cờ lê lực 10–100 cN·m", "TORQUE", "Thiết bị đo lực xoắn", "RTD120CN", "TH-26Q105", "QC FQC - Line 1"),
]
CAL_GAUGE = "ME-002"  # existing caliper, calibrated until 04/2027

# product, revision, machine, mold, process, inspection type, rounds (hours) per day, sample size
LINES = {
    "PACK": dict(product=5, revision=3, machine=2, mold=2, process="INJECTION", itype="IPQC", hours=(8, 10, 14, 16), sample=5),
    "HOME": dict(product=6, revision=4, machine=1, mold=3, process="INJECTION", itype="IPQC", hours=(8, 10, 14, 16), sample=5),
    "ELEC": dict(product=4, revision=6, machine=None, mold=None, process="ASSEMBLY", itype="FQC", hours=(15,), sample=5),
}


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    today = date.today()
    try:
        # ------------------------------------------------ remove the previous run
        cur.execute("SELECT id FROM quality_inspection_plans WHERE remark=%s", (DEMO_TAG,))
        plans = [r["id"] for r in cur.fetchall()]
        if plans:
            pm = ",".join(["%s"] * len(plans))
            cur.execute(f"SELECT id FROM quality_ng_records WHERE inspection_plan_id IN ({pm})", tuple(plans))
            ngs = [r["id"] for r in cur.fetchall()]
            if ngs:
                nm = ",".join(["%s"] * len(ngs))
                cur.execute(f"DELETE FROM quality_ng_events WHERE ng_id IN ({nm})", tuple(ngs))
                cur.execute(f"DELETE FROM quality_alerts WHERE ref_type='NG' AND ref_id IN ({nm})", tuple(ngs))
                cur.execute(f"DELETE FROM quality_ng_records WHERE id IN ({nm})", tuple(ngs))
            cur.execute(f"DELETE m FROM quality_measurements m JOIN quality_inspection_records r ON r.id=m.record_id WHERE r.plan_id IN ({pm})", tuple(plans))
            cur.execute(f"DELETE FROM quality_inspection_records WHERE plan_id IN ({pm})", tuple(plans))
            cur.execute(f"DELETE FROM quality_events WHERE entity_type='PLAN' AND entity_id IN ({pm})", tuple(plans))
            cur.execute(f"DELETE FROM quality_inspection_plans WHERE id IN ({pm})", tuple(plans))
        codes = [g[0] for g in GAUGES]
        gm = ",".join(["%s"] * len(codes))
        cur.execute(f"SELECT id FROM measuring_equipment WHERE equipment_code IN ({gm})", tuple(codes))
        old = [r["id"] for r in cur.fetchall()]
        if old:
            om = ",".join(["%s"] * len(old))
            cur.execute(f"DELETE FROM measuring_equipment_calibration_history WHERE equipment_id IN ({om})", tuple(old))
            cur.execute(f"DELETE FROM quality_equipment_events WHERE equipment_id IN ({om})", tuple(old))
            cur.execute(f"DELETE FROM measuring_equipment WHERE id IN ({om})", tuple(old))

        # ------------------------------------------------ calibrated gauges
        cur.execute("SELECT id, type_code FROM measuring_equipment_types")
        type_ids = {r["type_code"]: r["id"] for r in cur.fetchall()}
        cur.execute("SELECT factory_node_id FROM measuring_equipment WHERE equipment_code=%s", (CAL_GAUGE,))
        node = cur.fetchone()["factory_node_id"]
        gauge_of = {}  # equipment type id -> gauge id
        for code, name, tcode, tname, model, serial, loc in GAUGES:
            if tcode not in type_ids:
                cur.execute("INSERT INTO measuring_equipment_types (type_code, type_name, category, created_by) VALUES (%s,%s,'PHYSICAL_TEST',%s)",
                            (tcode, tname, QC[2][0]))
                type_ids[tcode] = cur.lastrowid
            last = today - timedelta(days=random.randint(10, 40))
            cur.execute("""INSERT INTO measuring_equipment (equipment_code, equipment_name, equipment_type_id, factory_node_id, usage_location, owner_code,
                           manufacturer, model, serial_number, calibration_cycle_days, calibration_lab, last_calibration_date, next_calibration_date,
                           status, quality_state, description, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,180,'QC Lab',%s,%s,'AVAILABLE','NORMAL',%s,%s)""",
                        (code, name, type_ids[tcode], node, loc, QC[0][0], "Mitutoyo" if tcode in ("MIC", "DIAL") else "Hioki" if tcode == "MULTI" else "AND" if tcode == "SCALE" else "Tohnichi",
                         model, serial, last, last + timedelta(days=180), DEMO_TAG, QC[2][0]))
            gid = cur.lastrowid
            cur.execute("""INSERT INTO measuring_equipment_calibration_history (equipment_id, calibration_date, next_due_date, result, calibrated_by, lab,
                           certificate_no, created_by) VALUES (%s,%s,%s,'PASS',%s,'QC Lab',%s,%s)""",
                        (gid, last, last + timedelta(days=180), QC[0][1], f"CERT-{code}-{last:%y%m%d}", QC[2][0]))
            gauge_of[type_ids[tcode]] = gid
        cur.execute("SELECT id, equipment_type_id FROM measuring_equipment WHERE equipment_code=%s", (CAL_GAUGE,))
        cal = cur.fetchone()
        gauge_of[cal["equipment_type_id"]] = cal["id"]
        torque = gauge_of[type_ids["TORQUE"]]

        cur.execute("SELECT id, reason_code FROM reason_codes WHERE reason_code LIKE %s", ("NG-%",))
        defect = {r["reason_code"]: r["id"] for r in cur.fetchall()}
        chars = {k: qs.characteristics(cur, v["revision"]) for k, v in LINES.items()}

        # ------------------------------------------------ measured values
        def value(c, line, day_back, hour, k):
            name = c["characteristic_name"]
            lsl = float(c["lsl"]) if c["lsl"] is not None else None
            usl = float(c["usl"]) if c["usl"] is not None else None
            if name == "Điện trở cách điện":
                v = random.gauss(320, 45)
                if line == "ELEC" and day_back == 2 and k in (1, 3):
                    v = random.uniform(62, 88)  # moisture in the housing
                return round(v, 1)
            if name == "Cong vênh đáy":
                return round(abs(random.gauss(0.18, 0.06)), 3)
            if line == "HOME" and name == "Độ dày thành" and day_back == 1 and hour == 16 and k in (2, 4):
                return round(lsl - random.uniform(0.005, 0.02), 3)
            if line == "PACK" and name == "Khối lượng" and day_back == 3 and hour == 14 and k == 3:
                return round(lsl - 0.06, 3)  # short shot
            nom = float(c["nominal_value"])
            mean = nom
            if line == "HOME" and name == "Độ dày thành" and day_back == 1:
                mean = nom - 0.008 * (hour - 6)  # cavity wearing: the wall gets thinner during the day
            v = random.gauss(mean, (usl - lsl) / 9)
            return round(min(max(v, lsl + 0.002), usl - 0.002), 3)  # only the incidents above are out of tolerance

        def attr(c, line, day_back, hour, k):
            if line == "PACK" and c["characteristic_name"] == "Ren và vòng seal" and day_back == 3 and hour == 14 and k == 3:
                return "NG"  # the short shot also leaves the thread incomplete
            return "OK"

        def sample(line, day_back, hour, k):
            ms, bad_attr, bad_var = [], False, False
            for c in chars[line]:
                if c["characteristic_type"] == "ATTRIBUTE":
                    a = attr(c, line, day_back, hour, k)
                    bad_attr |= a == "NG"
                    ms.append({"characteristic_id": c["id"], "attr_result": a})
                    continue
                v = value(c, line, day_back, hour, k)
                lsl, usl = c["lsl"], c["usl"]
                bad_var |= (lsl is not None and v < float(lsl)) or (usl is not None and v > float(usl))
                ms.append({"characteristic_id": c["id"], "value": v,
                           "equipment_id": gauge_of.get(c["equipment_type_id"]) or torque})
            ng = bad_attr or bad_var
            code = None
            if ng:
                code = {"PACK": "NG-002", "HOME": "NG-009", "ELEC": "NG-009"}[line]
            return ms, (defect.get(code) if code else None)

        # ------------------------------------------------ inspection history (through the service, then back-dated)
        seq = {}

        def number(prefix, table, column, day):
            if (prefix, day) not in seq:
                cur.execute(f"SELECT {column} AS n FROM {table} WHERE {column} LIKE %s ORDER BY {column} DESC LIMIT 1", (f"{prefix}-{day:%y%m%d}-%",))
                row = cur.fetchone()
                seq[(prefix, day)] = int(row["n"][-4:]) if row else 0
            seq[(prefix, day)] += 1
            return f"{prefix}-{day:%y%m%d}-{seq[(prefix, day)]:04d}"

        ng_made = {}
        for day_back in range(7, 0, -1):
            day = today - timedelta(days=day_back)
            for line, cfg in LINES.items():
                for hour in cfg["hours"]:
                    moment = datetime.combine(day, time(hour, random.choice((5, 10, 20))))
                    code, insp = random.choice(QC)
                    p = qs.create_plan(cur, {"inspection_type": cfg["itype"], "product_id": cfg["product"], "standard_revision_id": cfg["revision"],
                                             "machine_id": cfg["machine"], "mold_id": cfg["mold"], "process": cfg["process"],
                                             "frequency_type": "TIME" if cfg["itype"] == "IPQC" else "LOT", "frequency_value": 120 if cfg["itype"] == "IPQC" else None,
                                             "sample_size": cfg["sample"], "inspector": insp, "remark": DEMO_TAG}, code, "AUTO_FREQUENCY")
                    pid = p["id"]
                    out = {}
                    for k in range(1, cfg["sample"] + 1):
                        ms, dcode = sample(line, day_back, hour, k)
                        out = qs.record_sample(cur, pid, {"measurements": ms, "inspector": code, "position": f"Cavity {(k - 1) % 4 + 1}",
                                                          "defect_code_id": dcode}, code)
                    end = moment + timedelta(minutes=4 + cfg["sample"] * 3)
                    cur.execute("UPDATE quality_inspection_plans SET plan_no=%s, planned_at=%s, due_at=%s, started_at=%s, completed_at=%s, created_at=%s, shift_id=%s WHERE id=%s",
                                (number("IP", "quality_inspection_plans", "plan_no", day), moment, moment + timedelta(hours=2 if cfg["itype"] == "IPQC" else 4),
                                 moment + timedelta(minutes=2), end, moment - timedelta(minutes=10), (qs.shift_of(moment, qs.load_shifts(cur)) or {}).get("id"), pid))
                    cur.execute("SELECT id FROM quality_inspection_records WHERE plan_id=%s ORDER BY sample_no", (pid,))
                    for i, r in enumerate(cur.fetchall()):
                        cur.execute("UPDATE quality_inspection_records SET inspected_at=%s WHERE id=%s", (moment + timedelta(minutes=4 + i * 3), r["id"]))
                    cur.execute("UPDATE quality_events SET event_at=%s WHERE entity_type='PLAN' AND entity_id=%s AND action='CREATE'", (moment - timedelta(minutes=10), pid))
                    cur.execute("UPDATE quality_events SET event_at=%s WHERE entity_type='PLAN' AND entity_id=%s AND action='START'", (moment + timedelta(minutes=4), pid))
                    cur.execute("UPDATE quality_events SET event_at=%s WHERE entity_type='PLAN' AND entity_id=%s AND action='COMPLETE'", (end, pid))
                    cur.execute("UPDATE measuring_equipment SET last_used_at=%s WHERE id IN (SELECT DISTINCT m.equipment_id FROM quality_measurements m JOIN quality_inspection_records r ON r.id=m.record_id WHERE r.plan_id=%s)",
                                (end, pid))
                    if out.get("ng"):
                        nid = out["ng"]["id"]
                        cur.execute("SELECT priority FROM quality_ng_records WHERE id=%s", (nid,))
                        prio = cur.fetchone()["priority"]
                        cur.execute("UPDATE quality_ng_records SET ng_no=%s, detected_at=%s, created_at=%s, due_date=%s WHERE id=%s",
                                    (number("NG", "quality_ng_records", "ng_no", day), end, end, end.date() + timedelta(days=qs.NG_DUE_DAYS[prio]), nid))
                        cur.execute("UPDATE quality_ng_events SET event_at=%s WHERE ng_id=%s", (end, nid))
                        ng_made[line] = (nid, end)

        # ------------------------------------------------ NG follow-up (through the service, then back-dated)
        def step(nid, action, data, actor, at):
            cur.execute("SELECT version FROM quality_ng_records WHERE id=%s", (nid,))
            qs.ng_action(cur, nid, action, {**data, "version": cur.fetchone()["version"]}, actor)
            cur.execute("UPDATE quality_ng_events SET event_at=%s WHERE ng_id=%s AND action=%s", (at, nid, action))

        if "PACK" in ng_made:  # short shot: closed with a 4M analysis
            nid, at = ng_made["PACK"]
            step(nid, "START_INVESTIGATION", {"owner": QC[1][1], "containment": "Cách ly 2 giờ sản xuất trước đó, cân 100% nắp chai"}, QC[1][0], at + timedelta(minutes=30))
            step(nid, "START_CA", {"root_cause_category": "MACHINE", "root_cause": "Nhiệt độ vòi phun dao động, cavity 3 thiếu nhựa",
                                   "corrective_action": "Thay can nhiệt vòi phun, kiểm tra lại nhiệt độ theo tiêu chuẩn thông số",
                                   "preventive_action": "Thêm điểm kiểm nhiệt độ vòi phun đầu ca"}, QC[1][0], at + timedelta(hours=3))
            step(nid, "SUBMIT_VERIFY", {"disposition": "SCRAP", "scrap_qty": 1}, QC[1][0], at + timedelta(hours=6))
            step(nid, "VERIFY", {"verification_result": "3 ca liên tiếp không tái diễn, khối lượng ổn định 2,08–2,12 g"}, QC[2][0], at + timedelta(days=1, hours=2))
            cur.execute("UPDATE quality_ng_records SET closed_at=%s WHERE id=%s", (at + timedelta(days=1, hours=2), nid))
        if "HOME" in ng_made:  # wall thickness: under investigation
            nid, at = ng_made["HOME"]
            step(nid, "START_INVESTIGATION", {"owner": QC[0][1], "containment": "Giữ lô ca chiều, kiểm 100% độ dày thành"}, QC[0][0], at + timedelta(minutes=40))
        # ELEC (insulation, critical) stays NEW for the user to process

        # ------------------------------------------------ open plans for today, to be measured by hand
        now = datetime.now().replace(microsecond=0)
        for line, itype, offset, sample_size in (("PACK", "IPQC", 0, 5), ("HOME", "IPQC", 15, 5), ("ELEC", "FQC", 30, 5), ("PACK", "OQC", 60, 5)):
            cfg = LINES[line]
            qs.create_plan(cur, {"inspection_type": itype, "product_id": cfg["product"], "standard_revision_id": cfg["revision"],
                                 "machine_id": cfg["machine"], "mold_id": cfg["mold"], "process": "PACKING" if itype == "OQC" else cfg["process"],
                                 "frequency_type": "TIME" if itype == "IPQC" else "LOT", "frequency_value": 120 if itype == "IPQC" else None,
                                 "sample_size": sample_size, "planned_at": now + timedelta(minutes=offset), "inspector": QC[2][1], "remark": DEMO_TAG},
                           QC[2][0], "MANUAL")

        conn.commit()
        cur.execute("SELECT COUNT(*) n FROM quality_inspection_plans WHERE remark=%s", (DEMO_TAG,)); n_plans = cur.fetchone()["n"]
        cur.execute("SELECT COUNT(*) n FROM quality_inspection_records r JOIN quality_inspection_plans p ON p.id=r.plan_id WHERE p.remark=%s", (DEMO_TAG,)); n_samples = cur.fetchone()["n"]
        print(f"Quality standard demo: {len(GAUGES)} gauges, {n_plans} plans, {n_samples} samples, NG records: "
              + ", ".join(f"{k}={v[0]}" for k, v in ng_made.items()))
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
