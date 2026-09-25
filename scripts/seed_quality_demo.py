"""Demo data for Quality Management (5.1 - 5.6). Re-runnable: quality transaction data is rebuilt from scratch.

- Measuring equipment types / equipment (ME-0001..) with calibration history, certificates and plans (6 months)
- SPC characteristics on the active Quality Standard of Connector Housing A
- 14 days of inspection plans (FAI / IPQC / FQC / OQC / IQC) with sample records and measurements
  (Dimension A drifts on IMM-001 today -> SPC out of control; Thickness D has a low Cpk -> warning)
- NG records with their processing history (closed / in progress / new, one customer complaint)
- NG records of the reject pieces already reported in Production Execution

Run with:

    python -m scripts.seed_quality_demo
"""

import os
import random
from datetime import date, datetime, time, timedelta

from app.core.database import get_vcc_plastics_connection
from app.modules.maintenance.service import technicians
from app.modules.quality import service as qs

random.seed(int(os.environ.get("QUALITY_SEED", "3")))

TYPES = [("CAL", "Caliper"), ("MIC", "Micrometer"), ("DIAL", "Dial Indicator"), ("HG", "Height Gauge"), ("CMM", "CMM"), ("VMM", "2D Measuring"),
         ("FG", "Force Gauge"), ("TH", "Temp. & Humidity"), ("DG", "Depth Gauge"), ("SCALE", "Precision Scale")]

# code, name, type, manufacturer, model, serial, location, cycle days, next due offset (days, None = never), state
EQUIPMENT = [
    ("ME-0001", "Thước cặp điện tử 0-150mm", "CAL", "Mitutoyo", "500-196-30", "231456789", "QC IPQC - Line 1", 180, 41, None),
    ("ME-0002", "Panme ngoài 0-25mm", "MIC", "Mitutoyo", "293-821-30", "231456790", "QC IPQC - Line 1", 180, 170, None),
    ("ME-0003", "Đồng hồ so 0-10mm", "DIAL", "Mitutoyo", "2046S", "231456791", "QC IPQC - Line 2", 180, 3, None),
    ("ME-0004", "Máy đo 2D", "VMM", "Mitutoyo", "Quick Scope QS-L", "231456792", "QC Lab", 365, 190, None),
    ("ME-0005", "Thước đo độ sâu", "DG", "Mitutoyo", "571-201-30", "231456793", "QC FQC - Packing", 180, -6, None),
    ("ME-0006", "Máy đo 3D (CMM)", "CMM", "Mitutoyo", "Crysta-Apex S", "231456794", "QC Lab", 365, 220, None),
    ("ME-0007", "Dụng cụ đo lực", "FG", "IMADA", "ZTS-500N", "231456795", "QC Lab", 365, 150, "UNDER_REPAIR"),
    ("ME-0008", "Nhiệt ẩm kế", "TH", "Testo", "608-H2", "231456796", "QC Lab", 365, 350, None),
    ("ME-0009", "Thước cặp điện tử 0-200mm", "CAL", "Mitutoyo", "500-197-30", "231456797", "QC IPQC - Line 2", 180, 5, None),
    ("ME-0010", "Panme ngoài 25-50mm", "MIC", "Mitutoyo", "293-831-30", "231456798", "QC FQC - Line 1", 180, 175, None),
    ("ME-0011", "Đồng hồ so chân gập", "DIAL", "Mitutoyo", "513-404-10E", "231456799", "QC FQC - Line 1", 180, -11, None),
    ("ME-0012", "Thước đo cao 0-300mm", "HG", "Mitutoyo", "192-630-10", "231456800", "QC Lab", 365, 7, None),
    ("ME-0013", "Cân điện tử 0.01g", "SCALE", "AND", "GX-2002A", "231456801", "QC IPQC - Line 1", 365, 355, None),
    ("ME-0014", "Thước cặp cơ 0-150mm", "CAL", "Insize", "1205-150S", "231456802", "Incoming Inspection", 180, 58, None),
    ("ME-0015", "Panme đo trong", "MIC", "Mitutoyo", "345-250-30", "231456803", "QC IPQC - Line 2", 180, 30, "OUT_OF_SERVICE"),
    ("ME-0016", "Thước cặp điện tử (dự phòng)", "CAL", "Mitutoyo", "500-196-30", "231456804", "QC Lab", 180, None, None),
    ("ME-0017", "Đồng hồ so cũ", "DIAL", "Peacock", "107", "231456805", "QC Lab", 180, -120, "SCRAPPED"),
    ("ME-0018", "Cân điện tử 0.1g", "SCALE", "AND", "EK-610i", "231456806", "QC FQC - Packing", 365, 18, "IN_CALIBRATION"),
]

# name, nominal, lsl, usl, unit, type code, chart, subgroup, spc, critical, char type, sigma, bias
CHARS = [
    ("Dimension A", 10.00, 9.70, 10.30, "mm", "CAL", "XBAR_R", 5, 1, 1, "VARIABLE", 0.045, 0.0),
    ("Dimension B", 25.00, 24.75, 25.25, "mm", "MIC", "XBAR_R", 5, 1, 0, "VARIABLE", 0.035, 0.01),
    ("Dimension C", 15.50, 15.30, 15.70, "mm", "CAL", "XBAR_R", 5, 1, 0, "VARIABLE", 0.03, -0.01),
    ("Thickness D", 2.00, 1.85, 2.15, "mm", "MIC", "XBAR_R", 5, 1, 1, "VARIABLE", 0.042, 0.02),
    ("Hole Diameter E", 3.20, 3.15, 3.25, "mm", "CAL", "XBAR_R", 5, 1, 0, "VARIABLE", 0.009, 0.0),
    ("Weight", 12.50, 12.00, 13.00, "g", "SCALE", "IMR", 1, 1, 0, "VARIABLE", 0.08, 0.0),
    ("Flatness", 0.05, 0.00, 0.10, "mm", "DIAL", "XBAR_R", 5, 1, 0, "VARIABLE", 0.012, -0.005),
    ("Appearance", None, None, None, None, None, "XBAR_R", 5, 0, 0, "ATTRIBUTE", 0, 0),
]
GAUGE_FOR = {"CAL": "ME-0001", "MIC": "ME-0002", "DIAL": "ME-0003", "SCALE": "ME-0013"}
DEFECT_MIX = ["DEF-001"] * 8 + ["DEF-002"] * 5 + ["DEF-003"] * 4 + ["DEF-004"] * 3 + ["DEF-005"] * 2 + ["DEF-006", "DEF-008", "DEF-012"]


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    today = date.today()
    now = datetime.now().replace(microsecond=0)
    people = technicians(cur) or ["QC Inspector"]
    inspectors = people[:6]
    try:
        # ------------------------------------------------ reset quality transaction data
        for t in ("quality_measurements", "quality_inspection_records", "quality_inspection_plans", "quality_ng_events", "quality_ng_records",
                  "quality_alerts", "quality_events", "quality_equipment_events", "quality_calibration_plans"):
            cur.execute(f"DELETE FROM {t}")
        cur.execute("SELECT id FROM measuring_equipment WHERE equipment_code LIKE 'ME-%'")
        old = [r["id"] for r in cur.fetchall()]
        if old:
            marks = ",".join(["%s"] * len(old))
            cur.execute(f"DELETE FROM measuring_equipment_calibration_history WHERE equipment_id IN ({marks})", tuple(old))
            cur.execute(f"DELETE FROM measuring_equipment WHERE id IN ({marks})", tuple(old))
        cur.execute("DELETE FROM measuring_equipment_calibration_history WHERE plan_id IS NOT NULL")

        # ------------------------------------------------ equipment types / equipment
        type_ids = {}
        cur.execute("SELECT id, type_code FROM measuring_equipment_types")
        for r in cur.fetchall():
            type_ids[r["type_code"]] = r["id"]
        for code, name in TYPES:
            if code not in type_ids:
                cur.execute("INSERT INTO measuring_equipment_types (type_code, type_name, category, created_by) VALUES (%s,%s,%s,'SEED')",
                            (code, name, "PHYSICAL_TEST" if code in ("FG", "TH", "SCALE") else "VISION_OPTICAL" if code in ("CMM", "VMM") else "DIMENSIONAL"))
                type_ids[code] = cur.lastrowid
        cur.execute("SELECT id FROM factory_structure_nodes ORDER BY id LIMIT 1")
        node = cur.fetchone()["id"]
        gauges = {}
        for code, name, tcode, mfr, model, serial, loc, cycle, due, state in EQUIPMENT:
            nxt = today + timedelta(days=due) if due is not None else None
            last = nxt - timedelta(days=cycle) if nxt else None
            status = "OUT_OF_SERVICE" if state == "OUT_OF_SERVICE" else "INACTIVE" if state == "SCRAPPED" else "AVAILABLE"
            qstate = state if state in ("UNDER_REPAIR", "SCRAPPED", "IN_CALIBRATION") else "NORMAL"
            lock = {"UNDER_REPAIR": "Load cell reading unstable", "OUT_OF_SERVICE": "Anvil worn, waiting for replacement", "SCRAPPED": "Obsolete, beyond repair"}.get(state)
            cur.execute("""INSERT INTO measuring_equipment (equipment_code, equipment_name, equipment_type_id, factory_node_id, usage_location, owner_code, manufacturer, model,
                           serial_number, measurement_range, resolution, accuracy, required_cpk, calibration_cycle_days, calibration_lab, last_calibration_date,
                           next_calibration_date, status, quality_state, lock_reason, created_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'SEED')""",
                        (code, name, type_ids[tcode], node, loc, random.choice(inspectors), mfr, model, serial, None, "0.01mm" if tcode in ("CAL", "HG") else "0.001mm" if tcode in ("MIC", "DIAL") else None,
                         None, 1.67 if tcode in ("CMM", "VMM") else 1.33, cycle, "External Lab" if tcode in ("CMM", "VMM", "FG") else "QC Lab", last, nxt, status, qstate, lock))
            gauges[code] = cur.lastrowid
            eid = cur.lastrowid
            # history: the last calibrations of the cycle (6+ months back) with certificates
            if nxt:
                d, n = last, 0
                while d >= today - timedelta(days=400) and n < 3:
                    late = random.random() < 0.15
                    cal_day = d + timedelta(days=random.randint(1, 5)) if late else d - timedelta(days=random.randint(0, 4))
                    cal_day = min(cal_day, today)
                    cert = f"CERT-{cal_day:%y%m%d}-{eid:03d}"
                    lab = "External Lab" if tcode in ("CMM", "VMM", "FG") else "QC Lab"
                    cur.execute("""INSERT INTO measuring_equipment_calibration_history (equipment_id, calibration_date, next_due_date, result, calibrated_by, lab,
                                   certificate_no, certificate_url, remark, created_by) VALUES (%s,%s,%s,%s,%s,%s,%s,NULL,NULL,'SEED')""",
                                (eid, cal_day, cal_day + timedelta(days=cycle), "ADJUSTED" if random.random() < 0.1 else "PASS", random.choice(inspectors), lab, cert))
                    hid = cur.lastrowid
                    planned = d
                    if planned >= today - timedelta(days=200):
                        no = f"CAL-{planned:%y%m%d}-{eid:04d}"
                        cur.execute("""INSERT INTO quality_calibration_plans (plan_no, equipment_id, planned_date, lab, owner, priority, status, source, sent_at, calibration_date,
                                       result, certificate_no, calibrated_by, history_id, created_by, created_at) VALUES (%s,%s,%s,%s,%s,'MEDIUM','COMPLETED','AUTO',%s,%s,'PASS',%s,%s,%s,'SYSTEM',%s)""",
                                    (no, eid, planned, lab, random.choice(inspectors), datetime.combine(cal_day, time(8)), cal_day, cert, random.choice(inspectors), hid,
                                     datetime.combine(planned - timedelta(days=30), time(7))))
                        cur.execute("UPDATE measuring_equipment_calibration_history SET plan_id=%s WHERE id=%s", (cur.lastrowid, hid))
                    d -= timedelta(days=cycle)
                    n += 1
            if state == "IN_CALIBRATION":
                no = f"CAL-{today:%y%m%d}-{eid:04d}"
                cur.execute("""INSERT INTO quality_calibration_plans (plan_no, equipment_id, planned_date, lab, owner, priority, status, source, sent_at, created_by)
                               VALUES (%s,%s,%s,'QC Lab',%s,'MEDIUM','IN_PROGRESS','AUTO',%s,'SYSTEM')""", (no, eid, nxt, random.choice(inspectors), now - timedelta(days=1)))
                qs.log(cur, "CAL", cur.lastrowid, "START", "SEED", "PLANNED", "IN_PROGRESS")
            for action, note in (("REPORT_BROKEN", lock) if state == "UNDER_REPAIR" else ("OUT_OF_SERVICE", lock) if state == "OUT_OF_SERVICE" else ("SCRAP", lock) if state == "SCRAPPED" else (None, None),):
                if action:
                    qs.equipment_event(cur, eid, action, "SEED", note)
        # the three pre-existing gauges get a usage location / owner
        cur.execute("UPDATE measuring_equipment SET usage_location='QC IPQC - Line 1', owner_code=%s WHERE equipment_code NOT LIKE 'ME-%%' AND usage_location IS NULL",
                    (inspectors[0],))

        # ------------------------------------------------ SPC characteristics on the active standard of product 1
        cur.execute("SELECT id FROM quality_standard_revisions WHERE product_id=1 AND status='ACTIVE' ORDER BY revision_no DESC LIMIT 1")
        rev = cur.fetchone()["id"]
        names = [c[0] for c in CHARS]
        cur.execute(f"DELETE FROM quality_standard_characteristics WHERE revision_id=%s AND characteristic_name IN ({','.join(['%s'] * len(names))})", (rev, *names))
        cur.execute("UPDATE quality_standard_characteristics SET is_spc=1, chart_type='XBAR_R', subgroup_size=5, sort_order=90 WHERE revision_id=%s", (rev,))
        chars = []
        for i, (name, nom, lsl, usl, unit, tcode, chart, sub, is_spc, crit, ctype, sigma, bias) in enumerate(CHARS):
            cur.execute("""INSERT INTO quality_standard_characteristics (revision_id, characteristic_name, nominal_value, lsl, usl, unit, inspection_method, equipment_type_id,
                           inspection_frequency, characteristic_type, is_spc, chart_type, subgroup_size, cpk_target, is_critical, sort_order, created_by)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1.33,%s,%s,'SEED')""",
                        (rev, name, nom, lsl, usl, unit, "Visual" if ctype == "ATTRIBUTE" else dict(TYPES).get(tcode), type_ids.get(tcode), "Every 30 min / 5 pcs",
                         ctype, is_spc, chart, sub, crit, i))
            chars.append({"id": cur.lastrowid, "name": name, "nom": nom, "lsl": lsl, "usl": usl, "tcode": tcode, "type": ctype, "sigma": sigma, "bias": bias})
        cur.execute("SELECT id FROM quality_standard_characteristics WHERE revision_id=%s AND characteristic_name NOT IN (%s)" % ("%s", ",".join(["%s"] * len(names))), (rev, *names))
        extra = [r["id"] for r in cur.fetchall()]  # e.g. Overall Length: measured with the caliper too
        cur.execute("SELECT id, reason_code FROM reason_codes WHERE reason_code LIKE 'DEF-%%'")
        defects = {r["reason_code"]: r["id"] for r in cur.fetchall()}

        # ------------------------------------------------ inspection history
        shifts = qs.load_shifts(cur)
        plan_seq = {}

        def plan_no(day):
            plan_seq[day] = plan_seq.get(day, 0) + 1
            return f"IP-{day:%y%m%d}-{plan_seq[day]:04d}"

        wo_windows = {  # machine -> (wo_id, start, end)
            1: [(9, datetime(2026, 9, 24, 6, 5), None)], 2: [(13, datetime(2026, 9, 23, 8, 0), datetime(2026, 9, 23, 9, 55)), (10, datetime(2026, 9, 24, 6, 35), None)],
            3: [(12, datetime(2026, 9, 23, 7, 0), datetime(2026, 9, 23, 10, 40)), (11, datetime(2026, 9, 24, 6, 50), datetime(2026, 9, 24, 8, 25))],
        }
        molds_of = {9: 1, 10: 2, 11: 7, 12: 3, 13: 2}
        default_mold = {1: 1, 2: 2, 3: 3}

        def wo_at(machine, moment):
            for wid, a, b in wo_windows.get(machine, []):
                if moment >= a and (b is None or moment <= b):
                    return wid
            return None

        ng_candidates = []

        def add_plan(itype, moment, machine=None, wo=None, sample=5, lot=None, status="COMPLETED", done=None, process="INJECTION", freq="TIME", source="AUTO_FREQUENCY",
                     drift=0.0, ng_prob=0.02, force_ng=None):
            day = moment.date()
            no = plan_no(day)
            shift = qs.shift_of(moment, shifts)
            mold = molds_of.get(wo) if wo else default_mold.get(machine)
            due = moment + (timedelta(minutes=30) if freq == "TIME" else timedelta(hours=4) if freq == "LOT" else timedelta(hours=1))
            done = sample if done is None else done
            insp = random.choice(inspectors)
            cur.execute("""INSERT INTO quality_inspection_plans (plan_no, inspection_type, work_order_id, product_id, machine_id, mold_id, material_lot_id, standard_revision_id,
                           process, frequency_type, frequency_value, sample_size, planned_at, due_at, shift_id, inspector, status, started_at, source, created_by, created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'SYSTEM',%s)""",
                        (no, itype, wo, None if itype == "IQC" else 1, machine, None if itype == "IQC" else mold, lot, None if itype == "IQC" else rev, process, freq,
                         30 if freq == "TIME" else None, sample, moment, due, shift["id"] if shift else None, insp, status if status != "COMPLETED" else "IN_PROGRESS",
                         moment if done else None, source, moment - timedelta(minutes=5)))
            pid = cur.lastrowid
            ng_count, ng_defects = 0, []
            for k in range(done):
                at = moment + timedelta(minutes=2 + k * 2)
                sample_ng = False
                defect = None
                ms = []
                if itype != "IQC":
                    for c in chars:
                        if c["type"] == "ATTRIBUTE":
                            bad = random.random() < ng_prob or (force_ng and k < force_ng)
                            ms.append((c["id"], None, "NG" if bad else "OK", "NG" if bad else "OK", None))
                            if bad:
                                sample_ng, defect = True, defects[random.choice(DEFECT_MIX)]
                            continue
                        shift_bias = drift if c["name"] == "Dimension A" else 0
                        v = round(random.gauss(c["nom"] + c["bias"] + shift_bias, c["sigma"]), 3)
                        bad = v < c["lsl"] or v > c["usl"]
                        ms.append((c["id"], v, None, "NG" if bad else "OK", gauges[GAUGE_FOR[c["tcode"]]]))
                        if bad:
                            sample_ng, defect = True, defect or defects["DEF-010"]
                    for x in extra:
                        v = round(random.gauss(50.0, 0.08), 3)
                        ms.append((x, v, None, "OK" if 49.5 <= v <= 50.5 else "NG", gauges["ME-0001"]))
                else:
                    sample_ng = random.random() < ng_prob or bool(force_ng and k < force_ng)
                    defect = defects[random.choice(["DEF-005", "DEF-008"])] if sample_ng else None
                cur.execute("""INSERT INTO quality_inspection_records (plan_id, sample_no, inspected_at, inspector, position, result, defect_code_id, remark)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,NULL)""", (pid, k + 1, at, insp, f"Cavity 1 - Point {k + 1}", "NG" if sample_ng else "OK", defect if sample_ng else None))
                rid = cur.lastrowid
                for cid, v, attr, res, g in ms:
                    cur.execute("INSERT INTO quality_measurements (record_id, characteristic_id, measured_value, attr_result, result, equipment_id) VALUES (%s,%s,%s,%s,%s,%s)",
                                (rid, cid, v, attr, res, g))
                if sample_ng:
                    ng_count += 1
                    ng_defects.append(defect)
            if status == "COMPLETED":
                end = moment + timedelta(minutes=2 + done * 2)
                cur.execute("UPDATE quality_inspection_plans SET status='COMPLETED', result=%s, completed_at=%s WHERE id=%s", ("NG" if ng_count else "OK", end, pid))
                if ng_count:
                    ng_candidates.append({"plan_id": pid, "type": itype, "wo": wo, "machine": machine, "mold": mold, "lot": lot, "at": end, "qty": ng_count,
                                          "inspected": done, "defect": max(set(ng_defects), key=ng_defects.count), "process": process})
            return pid

        for back in range(13, -1, -1):
            day = today - timedelta(days=back)
            for machine in (1, 2, 3):
                if machine == 3 and day == today:
                    continue  # WO-260923-003 on hold since yesterday
                for hour in (6, 8, 10, 12, 14, 16, 18, 20):
                    moment = datetime.combine(day, time(hour, random.choice((5, 15, 30))))
                    if moment > now - timedelta(minutes=25):
                        continue
                    wo = wo_at(machine, moment)
                    drift = 0.0
                    if machine == 1 and (day == today or (day == today - timedelta(days=1) and hour >= 12)):
                        step = (hour - 12) / 2 if day != today else 4 + (hour - 6) / 2
                        drift = 0.025 * step + (0.12 if day == today else 0)  # Dimension A drifting up on IMM-001
                    add_plan("IPQC", moment, machine, wo, drift=drift, ng_prob=0.03 if machine == 3 else 0.01,
                             source="AUTO_WO" if wo and hour == 6 else "AUTO_FREQUENCY")
        # first article / final / outgoing / incoming inspections
        add_plan("FAI", datetime(2026, 9, 23, 7, 5), 3, 12, sample=3, freq="ONCE", source="AUTO_WO")
        add_plan("FAI", datetime(2026, 9, 23, 8, 5), 2, 13, sample=3, freq="ONCE", source="AUTO_WO")
        add_plan("FAI", datetime(2026, 9, 24, 6, 10), 1, 9, sample=3, freq="ONCE", source="AUTO_WO")
        add_plan("FAI", datetime(2026, 9, 24, 6, 40), 2, 10, sample=3, freq="ONCE", source="AUTO_WO")
        add_plan("FAI", datetime(2026, 9, 24, 6, 55), 3, 11, sample=3, freq="ONCE", source="AUTO_WO", force_ng=1)
        add_plan("FQC", datetime(2026, 9, 23, 10, 50), 3, 12, sample=10, freq="LOT", process="INSPECTION", source="AUTO_WO")
        add_plan("FQC", datetime(2026, 9, 23, 10, 10), 2, 13, sample=10, freq="LOT", process="INSPECTION", source="AUTO_WO", force_ng=2)
        add_plan("OQC", datetime(2026, 9, 23, 14, 0), 2, 13, sample=13, freq="LOT", process="PACKING", source="MANUAL")
        for i, (lot, force) in enumerate(((4, None), (5, None), (6, None), (7, None), (8, 2))):
            add_plan("IQC", datetime.combine(today - timedelta(days=2 + i % 3), time(9 + i, 0)), None, None, sample=5, lot=lot, freq="LOT", process="INCOMING",
                     source="MANUAL", force_ng=force)
        # open plans: current rounds of the running Work Orders
        add_plan("IPQC", now - timedelta(minutes=50), 2, 10, status="IN_PROGRESS", done=3)            # overdue, 3/5
        add_plan("IPQC", now + timedelta(minutes=10), 1, 9, status="NOT_STARTED", done=0)
        add_plan("PATROL", now + timedelta(hours=1), 1, 9, sample=3, status="NOT_STARTED", done=0, source="MANUAL")
        add_plan("OQC", now + timedelta(hours=2), None, 12, sample=13, status="NOT_STARTED", done=0, freq="LOT", process="PACKING", source="CUSTOMER")
        cur.execute("UPDATE quality_inspection_plans SET machine_id=3, mold_id=3 WHERE work_order_id=12 AND machine_id IS NULL")

        # ------------------------------------------------ NG records
        seq = {}

        def ng_no(d):
            seq[d] = seq.get(d, 0) + 1
            return f"NG-{d:%y%m%d}-{seq[d]:04d}"

        def add_ng(src, at, qty, defect, status, priority, wo=None, machine=None, mold=None, lot=None, plan=None, inspected=None, process="INJECTION", desc=None):
            owner = random.choice(inspectors) if status != "NEW" else None
            due = at.date() + timedelta(days=qs.NG_DUE_DAYS[priority])
            fields = {"disposition": None, "root_cause_category": None, "root_cause": None, "corrective_action": None, "preventive_action": None,
                      "verification_result": None, "rework_qty": 0, "scrap_qty": 0, "containment": None}
            order = ["NEW", "INVESTIGATING", "CA_IN_PROGRESS", "WAITING_VERIFICATION", "CLOSED"]
            level = order.index(status)
            if level >= 1:
                fields["containment"] = "Suspect lot segregated and 100% sorted"
            if level >= 2:
                cat = random.choice(qs.ROOT_CAUSES[:4])
                fields.update(root_cause_category=cat, root_cause={"MACHINE": "Injection pressure drift after nozzle heater fluctuation",
                                                                    "MATERIAL": "Resin moisture above 0.02% (dryer dew point high)",
                                                                    "METHOD": "Holding time shortened during setup, not per standard",
                                                                    "MAN": "Operator skipped the first-shot check"}[cat],
                              corrective_action="Restore parameters to the process standard, re-train the setup team",
                              preventive_action="Add parameter interlock and first-shot checklist")
            if level >= 3:
                disp = random.choice(["REWORK", "SCRAP", "SORT"])
                fields.update(disposition=disp, rework_qty=qty if disp == "REWORK" else 0, scrap_qty=qty if disp == "SCRAP" else 0)
            if level >= 4:
                fields["verification_result"] = "3 consecutive shifts without recurrence, Cpk back above 1.33"
            no = ng_no(at.date())
            cur.execute("""INSERT INTO quality_ng_records (ng_no, detected_at, source, inspection_plan_id, work_order_id, product_id, machine_id, mold_id, material_lot_id,
                           defect_code_id, process, ng_qty, inspected_qty, description, priority, status, owner, detected_by, due_date, disposition, rework_qty, scrap_qty,
                           containment, root_cause_category, root_cause, corrective_action, preventive_action, verification_result, closed_at, created_by, created_at)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'SYSTEM',%s)""",
                        (no, at, src, plan, wo, None if src == "IQC" else 1, machine, mold, lot, defect, process, qty, inspected, desc, priority, status, owner,
                         random.choice(inspectors), due, fields["disposition"], fields["rework_qty"], fields["scrap_qty"], fields["containment"],
                         fields["root_cause_category"], fields["root_cause"], fields["corrective_action"], fields["preventive_action"],
                         fields["verification_result"], at + timedelta(days=2) if status == "CLOSED" else None, at))
            nid = cur.lastrowid
            t = at
            steps = [("CREATE", None, "NEW"), ("START_INVESTIGATION", "NEW", "INVESTIGATING"), ("START_CA", "INVESTIGATING", "CA_IN_PROGRESS"),
                     ("SUBMIT_VERIFY", "CA_IN_PROGRESS", "WAITING_VERIFICATION"), ("VERIFY", "WAITING_VERIFICATION", "CLOSED")]
            for action, a, b in steps[:level + 1]:
                cur.execute("INSERT INTO quality_ng_events (ng_id, action, from_status, to_status, actor, note, event_at) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                            (nid, action, a, b, owner or "SYSTEM", desc if action == "CREATE" else None, min(t, now)))
                t += timedelta(hours=random.randint(3, 12))
            return nid

        def pick_status(at):
            age = (now - at).days
            if age >= 6:
                return random.choice(["CLOSED"] * 5 + ["WAITING_VERIFICATION"])
            if age >= 2:
                return random.choice(["CLOSED", "CLOSED", "WAITING_VERIFICATION", "CA_IN_PROGRESS", "INVESTIGATING"])
            return random.choice(["NEW", "NEW", "INVESTIGATING", "CA_IN_PROGRESS"])

        for c in sorted(ng_candidates, key=lambda x: x["at"]):
            ratio = c["qty"] / c["inspected"]
            prio = "HIGH" if c["type"] in ("FAI", "FQC", "OQC") or ratio >= 0.4 else "MEDIUM"
            add_ng(c["type"], c["at"], c["qty"], c["defect"], pick_status(c["at"]), prio, c["wo"], c["machine"], c["mold"], c["lot"], c["plan_id"], c["inspected"],
                   c["process"], f"{c['type']}: {c['qty']}/{c['inspected']} NG samples")
        # rejects already reported at the machines (Production Execution) -> one record per WO + defect + day
        cur.execute("""SELECT l.work_order_id, DATE(l.event_at) d, l.reason_text, SUM(l.reject_qty) q, MAX(l.event_at) at, d2.machine_id, d2.mold_id
                       FROM production_execution_logs l JOIN production_plan_details d2 ON d2.id=l.work_order_id WHERE l.reject_qty>0
                       GROUP BY l.work_order_id, DATE(l.event_at), l.reason_text, d2.machine_id, d2.mold_id ORDER BY at""")
        for r in cur.fetchall():
            defect = qs.defect_from_text(cur, r["reason_text"])
            add_ng("PRODUCTION", r["at"], int(r["q"]), defect, "CLOSED" if r["d"] < today - timedelta(days=1) else random.choice(["NEW", "INVESTIGATING", "CLOSED"]),
                   "MEDIUM", r["work_order_id"], r["machine_id"], r["mold_id"], None, None, None, "INJECTION", r["reason_text"])
        # a customer complaint (open) and a trimming / assembly issue
        add_ng("CUSTOMER", now - timedelta(hours=20), 36, defects["DEF-001"], "INVESTIGATING", "CRITICAL", 13, 2, 2, None, None, None, "PACKING",
               "Molex complaint: flash on the latch of Connector Housing A, lot of 23/09")
        add_ng("PATROL", now - timedelta(days=4, hours=3), 12, defects["DEF-003"], "CLOSED", "LOW", None, 2, 2, None, None, None, "TRIMMING", "Burr after trimming station")
        add_ng("AUDIT", now - timedelta(days=9), 8, defects["DEF-012"], "CLOSED", "LOW", None, None, None, None, None, None, "ASSEMBLY", "Scratch found at layered audit")
        # repeat links: same defect on the same product after a closed record
        cur.execute("SELECT id, defect_code_id, product_id, mold_id, detected_at FROM quality_ng_records ORDER BY detected_at")
        rows = cur.fetchall()
        for r in rows:
            cur.execute("""SELECT id FROM quality_ng_records WHERE defect_code_id=%s AND product_id <=> %s AND mold_id <=> %s AND status='CLOSED' AND closed_at < %s
                           AND closed_at >= %s - INTERVAL 30 DAY ORDER BY closed_at DESC LIMIT 1""", (r["defect_code_id"], r["product_id"], r["mold_id"], r["detected_at"], r["detected_at"]))
            rep = cur.fetchone()
            if rep and r["defect_code_id"]:
                cur.execute("UPDATE quality_ng_records SET repeat_of_id=%s WHERE id=%s", (rep["id"], r["id"]))
        conn.commit()
        cur.execute("SELECT COUNT(*) c FROM quality_inspection_plans"); p = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) c FROM quality_inspection_records"); s = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) c FROM quality_ng_records"); n = cur.fetchone()["c"]
        cur.execute("SELECT COUNT(*) c FROM quality_ng_records WHERE repeat_of_id IS NOT NULL"); rp = cur.fetchone()["c"]
        print(f"Quality demo: {len(EQUIPMENT)} gauges, {p} inspection plans, {s} samples, {n} NG records ({rp} repeats).")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
