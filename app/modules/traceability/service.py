"""Traceability (7.1 Product, 7.2 Material, 7.3 Lot Genealogy).

Chain used everywhere:
    Supplier -> Material lot (IQC, warehouse) -> issued / consumed by a Work Order (material_usages)
    -> Machine + Mold -> Inspections of the Work Order -> Product lots -> (shipment: not integrated yet)

- Product lot = one Work Order x production date x shift, built from the output of Production Execution.
  The night shift after midnight belongs to the production date it started on.
- Materials are issued per Work Order, so a product lot is linked to the material lots of its Work Order.
- Drying and shipment are not recorded by the MES yet: shown as "not integrated", never invented.
"""

import re
import time
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import HTTPException

from app.modules.production.work_orders.service import load_shifts, shift_of

NG_OPEN = ("NEW", "INVESTIGATING", "CA_IN_PROGRESS", "WAITING_VERIFICATION", "REOPENED")

WO_INFO = """SELECT d.id, d.wo_no, d.status, d.product_id, d.machine_id, d.mold_id, d.planned_qty, d.actual_qty, d.reject_qty, d.cavity, d.cycle_time_sec,
        d.operator_code, d.leader_code, d.started_at, d.completed_at, d.planned_start, d.planned_end, d.production_order_id,
        p.product_code, p.product_name, p.image_url AS product_image, eq.equipment_code AS machine_code, eq.equipment_name AS machine_name,
        mo.mold_code, mo.mold_name, mo.cavity_count, mo.image_url AS mold_image, o.order_no, c.customer_code, c.customer_name, c.short_name AS customer_short
    FROM production_plan_details d LEFT JOIN products p ON p.id=d.product_id LEFT JOIN equipment eq ON eq.id=d.machine_id
    LEFT JOIN molds mo ON mo.id=d.mold_id LEFT JOIN production_orders o ON o.id=d.production_order_id LEFT JOIN customers c ON c.id=o.customer_id"""

LOT_SELECT = """SELECT pl.*, d.wo_no, d.status AS wo_status, p.product_code, p.product_name, p.image_url AS product_image, eq.equipment_code AS machine_code,
        mo.mold_code, s.shift_name, s.shift_code, o.order_no, c.customer_name, c.short_name AS customer_short
    FROM product_lots pl JOIN production_plan_details d ON d.id=pl.work_order_id LEFT JOIN products p ON p.id=pl.product_id
    LEFT JOIN equipment eq ON eq.id=pl.machine_id LEFT JOIN molds mo ON mo.id=pl.mold_id LEFT JOIN shifts s ON s.id=pl.shift_id
    LEFT JOIN production_orders o ON o.id=d.production_order_id LEFT JOIN customers c ON c.id=o.customer_id"""

MAT_LOT_SELECT = """SELECT l.*, m.material_code, m.material_name, m.material_group, m.material_type, m.unit, m.image_url AS material_image, m.supplier AS material_supplier,
        loc.location_code, loc.location_name, loc.location_type
    FROM material_lots l JOIN materials m ON m.id=l.material_id LEFT JOIN material_locations loc ON loc.id=l.location_id"""


def _f(v):
    return float(v) if v is not None else 0.0


# =========================================================
# PRODUCT LOTS
# =========================================================


def production_date_of(moment, shift):
    """Night shift after midnight belongs to the day the shift started."""
    if shift and shift["start_min"] > shift["end_min"] and moment.hour * 60 + moment.minute < shift["end_min"]:
        return moment.date() - timedelta(days=1)
    return moment.date()


def sync_lots(cur, wo_id=None):
    """(Re)build product lots from the production output logs."""
    shifts = load_shifts(cur)
    where = "AND l.work_order_id=%s" if wo_id else ""
    cur.execute(f"""SELECT l.work_order_id, l.event_at, l.good_qty, l.reject_qty, l.machine_id, l.mold_id, l.operator_code, d.product_id, d.status,
                           d.machine_id AS wo_machine, d.mold_id AS wo_mold, d.operator_code AS wo_operator
                    FROM production_execution_logs l JOIN production_plan_details d ON d.id=l.work_order_id
                    WHERE l.event_type IN ('OUTPUT','REJECT','QTY_ADJUST') {where} ORDER BY l.event_at""", (wo_id,) if wo_id else ())
    groups = {}
    for r in cur.fetchall():
        s = shift_of(r["event_at"], shifts)
        key = (r["work_order_id"], production_date_of(r["event_at"], s), s["id"] if s else None)
        g = groups.setdefault(key, {"good": 0, "reject": 0, "first": r["event_at"], "last": r["event_at"], "row": r, "shift": s})
        g["good"] += int(r["good_qty"] or 0)
        g["reject"] += int(r["reject_qty"] or 0)
        g["last"] = r["event_at"]
    now = datetime.now()
    cur_shift = shift_of(now, shifts)
    today_key = (production_date_of(now, cur_shift), cur_shift["id"] if cur_shift else None)
    for (wid, pdate, sid), g in groups.items():
        r = g["row"]
        # shift code "A" or "SHIFT-A" -> "A"; no matching shift -> "X"
        letter = re.sub(r"[^A-Z0-9]", "", str(g["shift"]["shift_code"]).split("-")[-1].upper())[:3] if g["shift"] else ""
        letter = letter or "X"
        lot_no = f"PL-{pdate:%y%m%d}-{wid:04d}-{letter}"
        status = "OPEN" if r["status"] in ("IN_PRODUCTION", "ON_HOLD") and (pdate, sid) == today_key else "CLOSED"
        cur.execute("""INSERT INTO product_lots (lot_no, work_order_id, product_id, production_date, shift_id, machine_id, mold_id, operator_code, good_qty,
                       reject_qty, first_output_at, last_output_at, status) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON DUPLICATE KEY UPDATE good_qty=VALUES(good_qty), reject_qty=VALUES(reject_qty), first_output_at=VALUES(first_output_at),
                       last_output_at=VALUES(last_output_at), status=VALUES(status), machine_id=VALUES(machine_id), mold_id=VALUES(mold_id)""",
                    (lot_no, wid, r["product_id"], pdate, sid, r["machine_id"] or r["wo_machine"], r["mold_id"] or r["wo_mold"],
                     r["operator_code"] or r["wo_operator"], max(g["good"], 0), g["reject"], g["first"], g["last"], status))


def lot_quality(cur, lot):
    """Inspections and NG records of the lot's Work Order inside the lot window."""
    a = (lot["first_output_at"] or datetime.combine(lot["production_date"], datetime.min.time())) - timedelta(hours=1)
    b = (lot["last_output_at"] or a) + timedelta(hours=4)
    cur.execute("""SELECT ip.id, ip.plan_no, ip.inspection_type, ip.status, ip.result, ip.planned_at, ip.completed_at, ip.inspector, ip.sample_size,
                          COALESCE(agg.n,0) AS samples, COALESCE(agg.ng,0) AS ng
                   FROM quality_inspection_plans ip
                   LEFT JOIN (SELECT plan_id, COUNT(*) n, SUM(result='NG') ng FROM quality_inspection_records GROUP BY plan_id) agg ON agg.plan_id=ip.id
                   WHERE ip.work_order_id=%s AND ip.status<>'CANCELLED' AND ip.planned_at BETWEEN %s AND %s ORDER BY ip.planned_at""", (lot["work_order_id"], a, b))
    plans = cur.fetchall()
    cur.execute("""SELECT n.id, n.ng_no, n.source, n.status, n.priority, n.ng_qty, n.detected_at, rc.reason_name AS defect_name FROM quality_ng_records n
                   LEFT JOIN reason_codes rc ON rc.id=n.defect_code_id WHERE n.work_order_id=%s AND n.detected_at BETWEEN %s AND %s ORDER BY n.detected_at""",
                (lot["work_order_id"], a, b))
    ngs = cur.fetchall()
    samples = sum(int(p["samples"]) for p in plans)
    ng_samples = sum(int(p["ng"] or 0) for p in plans)
    open_ng = [n for n in ngs if n["status"] in NG_OPEN]
    done = [p for p in plans if p["status"] == "COMPLETED"]
    status = "HOLD" if open_ng else "PENDING" if not done else "FAIL" if any(p["result"] == "NG" for p in done) and ng_samples / max(samples, 1) > 0.2 else "PASS"
    return {"plans": plans, "ng": ngs, "samples": samples, "ng_samples": ng_samples, "status": status, "open_ng": len(open_ng)}


def wo_materials(cur, wo_id):
    cur.execute(f"""SELECT u.lot_id, u.material_id, SUM(u.qty) qty, MIN(u.used_at) first_used, MAX(u.used_at) last_used, COUNT(*) n,
                           l.lot_no, l.supplier, l.supplier_lot_no, l.iqc_result, l.status AS lot_status, m.material_code, m.material_name, m.material_group, m.unit
                    FROM material_usages u JOIN material_lots l ON l.id=u.lot_id JOIN materials m ON m.id=u.material_id
                    WHERE u.work_order_id=%s AND u.status='ACTIVE' GROUP BY u.lot_id, u.material_id, l.lot_no, l.supplier, l.supplier_lot_no, l.iqc_result,
                          l.status, m.material_code, m.material_name, m.material_group, m.unit ORDER BY qty DESC""", (wo_id,))
    return cur.fetchall()


def get_lot(cur, lot_id):
    cur.execute(LOT_SELECT + " WHERE pl.id=%s", (lot_id,))
    lot = cur.fetchone()
    if not lot:
        raise HTTPException(status_code=404, detail="Product lot not found.")
    return lot


def resolve_product(cur, by, key):
    """Candidate product lots for a search key."""
    key = (key or "").strip()
    if not key:
        return []
    by = (by or "LOT").upper()
    if by in ("LOT", "BARCODE", "QR", "SERIAL"):
        cur.execute(LOT_SELECT + " WHERE pl.lot_no=%s", (key,))
        rows = cur.fetchall()
        if not rows:
            cur.execute(LOT_SELECT + " WHERE pl.lot_no LIKE %s ORDER BY pl.production_date DESC LIMIT 50", (f"%{key}%",))
            rows = cur.fetchall()
        return rows
    if by == "WO":
        cur.execute(LOT_SELECT + " WHERE d.wo_no LIKE %s ORDER BY pl.first_output_at", (f"%{key}%",))
        return cur.fetchall()
    if by == "ORDER":
        cur.execute(LOT_SELECT + " WHERE o.order_no LIKE %s ORDER BY pl.first_output_at", (f"%{key}%",))
        return cur.fetchall()
    raise HTTPException(status_code=400, detail="Unknown search type.")


def wo_info(cur, wo_id):
    cur.execute(WO_INFO + " WHERE d.id=%s", (wo_id,))
    return cur.fetchone()


def product_trace(cur, lot_id):
    lot = get_lot(cur, lot_id)
    wo = wo_info(cur, lot["work_order_id"])
    q = lot_quality(cur, lot)
    mats = wo_materials(cur, lot["work_order_id"])
    main = next((m for m in mats if (m["unit"] or "").upper() == "KG"), mats[0] if mats else None)
    cur.execute("SELECT id, lot_no, production_date, shift_id, good_qty FROM product_lots WHERE work_order_id=%s ORDER BY first_output_at", (lot["work_order_id"],))
    siblings = cur.fetchall()
    cur.execute("SELECT id, doc_type, title, file_url FROM mold_documents WHERE mold_id=%s ORDER BY id DESC LIMIT 10", (lot["mold_id"] or 0,))
    mold_docs = cur.fetchall()
    # mold installed on the machine when the lot was produced
    install = None
    if lot["mold_id"]:
        cur.execute("""SELECT installed_at, removed_at, installed_by FROM mold_installations WHERE mold_id=%s AND machine_id <=> %s AND installed_at <= %s
                       ORDER BY installed_at DESC LIMIT 1""", (lot["mold_id"], lot["machine_id"], lot["last_output_at"] or datetime.now()))
        install = cur.fetchone()
    passed = sum(int(p["samples"]) - int(p["ng"] or 0) for p in q["plans"])
    nodes = [
        {"key": "PRODUCT", "title": lot["lot_no"], "sub": f"{lot['good_qty']:,} pcs", "status": q["status"], "ref": {"type": "PRODUCT_LOT", "id": lot["id"]}},
        {"key": "INSPECTION", "title": f"{len(q['plans'])} inspections", "sub": f"Pass {passed} / Fail {q['ng_samples']}", "status": q["status"],
         "ref": {"type": "INSPECTION", "id": q["plans"][-1]["id"]} if q["plans"] else None},
        {"key": "PRODUCTION", "title": wo["wo_no"], "sub": f"{lot['production_date']}", "status": wo["status"], "ref": {"type": "WO", "id": wo["id"]}},
        {"key": "MACHINE", "title": lot["machine_code"] or "-", "sub": wo["machine_name"] or "", "ref": {"type": "MACHINE", "id": lot["machine_id"]} if lot["machine_id"] else None},
        {"key": "MOLD", "title": lot["mold_code"] or "-", "sub": f"Cavity {wo['cavity'] or wo['cavity_count'] or '-'}", "ref": {"type": "MOLD", "id": lot["mold_id"]} if lot["mold_id"] else None},
        {"key": "MATERIAL", "title": main["material_code"] if main else "-", "sub": f"Lot {main['lot_no']}" if main else "No material issue recorded",
         "extra": f"Supplier: {main['supplier'] or '-'}" if main else None, "ref": {"type": "MATERIAL_LOT", "id": main["lot_id"]} if main else None},
    ]
    # step details (backward order) + timeline (chronological)
    steps = [
        ("FINISHED_PRODUCT", "Product lot", lot["lot_no"], lot["last_output_at"], "SYSTEM"),
        ("PACKING", "Packing / shipment", None, None, None),
        ("INSPECTION", "Inspection result", f"Pass {passed} / Fail {q['ng_samples']} samples ({len(q['plans'])} plans)" if q["plans"] else None,
         q["plans"][-1]["completed_at"] if q["plans"] else None, q["plans"][-1]["inspector"] if q["plans"] else None),
        ("PRODUCTION", "Work order", wo["wo_no"], lot["first_output_at"], lot["operator_code"]),
        ("MACHINE", "Machine", f"{lot['machine_code'] or '-'} ({wo['machine_name'] or ''})", lot["first_output_at"], "AUTO"),
        ("MOLD", "Mold", f"{lot['mold_code'] or '-'} ({wo['mold_name'] or ''})", install["installed_at"] if install else None, install["installed_by"] if install else None),
        ("MATERIAL", "Material lot", ", ".join(f"{m['lot_no']} ({m['material_code']})" for m in mats) or None, min((m["first_used"] for m in mats), default=None), "AUTO"),
        ("SUPPLIER", "Supplier", ", ".join(sorted({m["supplier"] for m in mats if m["supplier"]})) or None, None, None),
    ]
    events = []
    for m in mats:
        cur.execute("""SELECT t.txn_type, t.qty_change, t.acted_at, t.actor, t.reference_no FROM material_lot_transactions t WHERE t.lot_id=%s
                       AND (t.work_order_id=%s OR t.txn_type IN ('RECEIVING','IQC_PASS','IQC_FAIL','OPENING')) ORDER BY t.acted_at""", (m["lot_id"], lot["work_order_id"]))
        for t in cur.fetchall():
            events.append({"at": t["acted_at"], "kind": f"MAT_{t['txn_type']}", "text": m["lot_no"], "qty": _f(t["qty_change"]), "unit": m["unit"], "actor": t["actor"]})
    if install:
        events.append({"at": install["installed_at"], "kind": "MOLD_INSTALLED", "text": lot["mold_code"], "actor": install["installed_by"]})
    if lot["first_output_at"]:
        events.append({"at": lot["first_output_at"], "kind": "PRODUCTION_STARTED", "text": wo["wo_no"], "actor": lot["operator_code"]})
    for p in q["plans"]:
        if p["completed_at"]:
            events.append({"at": p["completed_at"], "kind": "INSPECTION", "text": f"{p['inspection_type']} {p['plan_no']} · {p['result'] or ''}", "actor": p["inspector"],
                           "result": p["result"]})
    for n in q["ng"]:
        events.append({"at": n["detected_at"], "kind": "NG", "text": f"{n['ng_no']} · {n['defect_name'] or ''} · {n['ng_qty']} pcs", "result": "NG"})
    if lot["last_output_at"]:
        events.append({"at": lot["last_output_at"], "kind": "LOT_COMPLETED" if lot["status"] == "CLOSED" else "LAST_OUTPUT", "text": f"{lot['good_qty']:,} pcs",
                       "actor": lot["operator_code"]})
    events.sort(key=lambda e: e["at"] or datetime.min)
    docs = [{"kind": "INSPECTION_REPORT", "available": bool(q["plans"]), "ref": {"type": "INSPECTION", "id": q["plans"][-1]["id"]} if q["plans"] else None},
            {"kind": "PACKING_LIST", "available": False}, {"kind": "CERTIFICATE", "available": False}, {"kind": "COA", "available": False}]
    docs += [{"kind": "MOLD_DOC", "available": True, "title": d["title"], "url": d["file_url"]} for d in mold_docs]
    images = [x for x in ((lot["product_image"], "PRODUCT"), (wo["mold_image"], "MOLD")) if x[0]]
    return {"lot": {**lot, "quality_status": q["status"], "open_ng": q["open_ng"]}, "work_order": wo, "quality": q, "materials": mats, "nodes": nodes,
            "steps": [{"step": s, "detail": d, "value": v, "at": at, "actor": who} for s, d, v, at, who in steps], "timeline": events, "documents": docs,
            "siblings": siblings, "images": [{"url": u, "kind": k} for u, k in images], "shipment": None}


# =========================================================
# MATERIAL LOTS
# =========================================================


def resolve_material(cur, by, key):
    key = (key or "").strip()
    if not key:
        return []
    by = (by or "LOT").upper()
    col = {"LOT": "l.lot_no", "BARCODE": "l.lot_no", "QR": "l.lot_no", "CODE": "m.material_code", "SUPPLIER_LOT": "l.supplier_lot_no", "SUPPLIER": "l.supplier"}.get(by)
    if not col:
        raise HTTPException(status_code=400, detail="Unknown search type.")
    cur.execute(MAT_LOT_SELECT + f" WHERE {col}=%s ORDER BY l.received_at DESC", (key,))
    rows = cur.fetchall()
    if not rows:
        cur.execute(MAT_LOT_SELECT + f" WHERE {col} LIKE %s ORDER BY l.received_at DESC LIMIT 50", (f"%{key}%",))
        rows = cur.fetchall()
    return rows


def material_trace(cur, lot_id):
    cur.execute(MAT_LOT_SELECT + " WHERE l.id=%s", (lot_id,))
    lot = cur.fetchone()
    if not lot:
        raise HTTPException(status_code=404, detail="Material lot not found.")
    cur.execute("""SELECT t.*, fl.location_code AS from_code, tl.location_code AS to_code, d.wo_no, eq.equipment_code AS machine_code
                   FROM material_lot_transactions t LEFT JOIN material_locations fl ON fl.id=t.from_location_id LEFT JOIN material_locations tl ON tl.id=t.to_location_id
                   LEFT JOIN production_plan_details d ON d.id=t.work_order_id LEFT JOIN equipment eq ON eq.id=t.machine_id
                   WHERE t.lot_id=%s ORDER BY t.acted_at, t.id""", (lot_id,))
    txns = cur.fetchall()
    cur.execute("""SELECT u.work_order_id, SUM(u.qty) qty, MIN(u.used_at) first_used, MAX(u.used_at) last_used FROM material_usages u
                   WHERE u.lot_id=%s AND u.status='ACTIVE' GROUP BY u.work_order_id""", (lot_id,))
    by_wo = {r["work_order_id"]: r for r in cur.fetchall()}
    usage = []
    product_lots = []
    for wid, u in by_wo.items():
        wo = wo_info(cur, wid)
        cur.execute(LOT_SELECT + " WHERE pl.work_order_id=%s ORDER BY pl.first_output_at", (wid,))
        lots = cur.fetchall()
        wo_good = sum(int(x["good_qty"]) for x in lots) or int(wo["actual_qty"] or 0)
        wo_rej = sum(int(x["reject_qty"]) for x in lots) or int(wo["reject_qty"] or 0)
        total = wo_good + wo_rej
        for x in lots:
            share = (int(x["good_qty"]) + int(x["reject_qty"])) / total if total else 0
            q = lot_quality(cur, x)
            usage.append({"work_order_id": wid, "wo_no": wo["wo_no"], "product_lot_id": x["id"], "product_lot": x["lot_no"], "machine_id": x["machine_id"],
                          "machine_code": x["machine_code"], "mold_id": x["mold_id"], "mold_code": x["mold_code"], "production_date": x["production_date"],
                          "shift_name": x["shift_name"], "consumed": round(_f(u["qty"]) * share, 3), "good_qty": x["good_qty"],
                          "scrap": round(_f(u["qty"]) * share * (int(x["reject_qty"]) / max(int(x["good_qty"]) + int(x["reject_qty"]), 1)), 3),
                          "operator": x["operator_code"], "quality_status": q["status"], "customer": x["customer_short"] or x["customer_name"]})
            product_lots.append({**x, "quality_status": q["status"]})
        if not lots:
            usage.append({"work_order_id": wid, "wo_no": wo["wo_no"], "product_lot_id": None, "product_lot": None, "machine_id": wo["machine_id"],
                          "machine_code": wo["machine_code"], "mold_id": wo["mold_id"], "mold_code": wo["mold_code"], "production_date": u["first_used"].date() if u["first_used"] else None,
                          "shift_name": None, "consumed": round(_f(u["qty"]), 3), "good_qty": 0, "scrap": 0, "operator": wo["operator_code"], "quality_status": "PENDING",
                          "customer": wo["customer_short"] or wo["customer_name"]})
    usage.sort(key=lambda x: (x["production_date"] or date.min, x["product_lot"] or ""))
    cur.execute("""SELECT ip.id, ip.plan_no, ip.status, ip.result, ip.completed_at, ip.inspector FROM quality_inspection_plans ip
                   WHERE ip.material_lot_id=%s AND ip.status<>'CANCELLED' ORDER BY ip.planned_at""", (lot_id,))
    iqc_plans = cur.fetchall()
    cur.execute("""SELECT n.id, n.ng_no, n.status, n.ng_qty, n.detected_at, rc.reason_name AS defect_name FROM quality_ng_records n
                   LEFT JOIN reason_codes rc ON rc.id=n.defect_code_id WHERE n.material_lot_id=%s ORDER BY n.detected_at""", (lot_id,))
    iqc_ng = cur.fetchall()
    received = sum(_f(t["qty_change"]) for t in txns if t["txn_type"] in ("RECEIVING", "OPENING", "REGRIND_CREATE"))
    consumed = sum(-_f(t["qty_change"]) for t in txns if t["txn_type"] == "CONSUMPTION") or sum(_f(u["qty"]) for u in by_wo.values())
    issued = sum(-_f(t["qty_change"]) for t in txns if t["txn_type"] == "ISSUE_TO_MACHINE")
    returned = sum(_f(t["qty_change"]) for t in txns if t["txn_type"] == "RETURN")
    in_production = max(issued - returned - consumed, 0) if issued else 0
    machines = sorted({u["machine_code"] for u in usage if u["machine_code"]})
    wos = sorted({u["wo_no"] for u in usage})
    q_all = [p["quality_status"] for p in product_lots]
    journey = [
        {"key": "SUPPLIER", "title": lot["supplier"] or lot["material_supplier"] or "-", "sub": f"Supplier lot: {lot['supplier_lot_no'] or '-'}", "status": "DONE"},
        {"key": "IQC", "title": lot["iqc_result"] or "-", "sub": str(lot["iqc_at"] or (iqc_plans[-1]["completed_at"] if iqc_plans else "") or ""), "status": lot["iqc_result"],
         "ref": {"type": "INSPECTION", "id": iqc_plans[-1]["id"]} if iqc_plans else None},
        {"key": "WAREHOUSE", "title": lot["location_code"] or "-", "sub": str(lot["received_at"] or ""), "status": lot["status"]},
        {"key": "DRYING", "title": "Not integrated", "sub": "", "status": "NA"},
        {"key": "MACHINE", "title": ", ".join(machines) or "-", "sub": "", "status": "DONE" if machines else "NA"},
        {"key": "PRODUCTION", "title": ", ".join(wos) or "-", "sub": f"{len(wos)} work orders", "status": "DONE" if wos else "NA"},
        {"key": "INSPECTION", "title": "HOLD" if "HOLD" in q_all else "PASS" if q_all and all(s == "PASS" for s in q_all) else "PENDING" if q_all else "-",
         "sub": f"{sum(int(p['good_qty']) for p in product_lots):,} pcs", "status": "HOLD" if "HOLD" in q_all else "DONE" if q_all else "NA"},
        {"key": "FINISHED", "title": f"{len(product_lots)} product lots", "sub": f"{sum(int(p['good_qty']) for p in product_lots):,} pcs",
         "status": "DONE" if product_lots else "NA", "ref": {"type": "PRODUCT_LOT", "id": product_lots[0]["id"]} if product_lots else None},
    ]
    timeline = [{"at": t["acted_at"], "kind": f"MAT_{t['txn_type']}", "text": " · ".join(filter(None, [t["wo_no"], t["machine_code"], t["to_code"] or t["from_code"], t["remark"]])),
                 "qty": _f(t["qty_change"]), "unit": lot["unit"], "actor": t["actor"]} for t in txns if t["txn_type"] != "RESERVATION"]
    for p in iqc_plans:
        if p["completed_at"]:
            timeline.append({"at": p["completed_at"], "kind": "IQC", "text": f"{p['plan_no']} · {p['result'] or ''}", "actor": p["inspector"], "result": p["result"]})
    for x in product_lots:
        timeline.append({"at": x["last_output_at"], "kind": "PRODUCT_LOT", "text": f"{x['lot_no']} · {x['good_qty']:,} pcs", "actor": x["operator_code"]})
    timeline.sort(key=lambda e: e["at"] or datetime.min)
    return {"lot": lot, "journey": journey, "usage": usage, "timeline": timeline, "transactions": txns, "iqc": iqc_plans, "ng": iqc_ng,
            "product_lots": product_lots, "customers": sorted({u["customer"] for u in usage if u["customer"]}),
            "statistics": {"received": round(received, 3), "consumed": round(consumed, 3), "in_production": round(in_production, 3), "current": _f(lot["current_qty"]),
                           "reserved": _f(lot["reserved_qty"]), "returned": round(returned, 3), "scrap": round(sum(u["scrap"] for u in usage), 3),
                           "finished_pcs": sum(int(p["good_qty"]) for p in product_lots), "unit": lot["unit"]},
            "documents": [{"kind": "COA", "available": False}, {"kind": "IQC_REPORT", "available": bool(iqc_plans), "ref": {"type": "INSPECTION", "id": iqc_plans[-1]["id"]} if iqc_plans else None},
                          {"kind": "DRYING_RECORD", "available": False}, {"kind": "MATERIAL_SPEC", "available": False}]}


# =========================================================
# GENEALOGY
# =========================================================

COLUMNS = ["SUPPLIER", "MATERIAL", "MACHINE", "PRODUCTION", "INSPECTION", "PRODUCT", "SHIPMENT"]


class Graph:
    def __init__(self):
        self.nodes, self.edges = {}, set()

    def node(self, nid, col, title, **kw):
        if nid not in self.nodes:
            self.nodes[nid] = {"id": nid, "col": col, "title": title, **kw}
        return nid

    def edge(self, a, b, kind="PROCESS"):
        if a and b:
            self.edges.add((a, b, kind))


def _add_wo(cur, g, wid, direction, with_materials=True, with_products=True):
    wo = wo_info(cur, wid)
    if not wo:
        return None
    wn = g.node(f"WO:{wid}", "PRODUCTION", wo["wo_no"], ref={"type": "WO", "id": wid}, sub=str((wo["started_at"] or wo["planned_start"] or "") and (wo["started_at"] or wo["planned_start"]).strftime("%d/%m/%Y %H:%M")),
                qty=f"{int(wo['actual_qty'] or 0):,} pcs", status=wo["status"])
    mn = None
    if wo["machine_id"]:
        mn = g.node(f"MC:{wo['machine_id']}", "MACHINE", wo["machine_code"], sub=wo["machine_name"], ref={"type": "MACHINE", "id": wo["machine_id"]})
        g.edge(mn, wn)
    if wo["mold_id"]:
        g.nodes[wn]["mold"] = wo["mold_code"]
        g.nodes[wn]["mold_id"] = wo["mold_id"]
    if with_materials and direction in ("BACKWARD", "BOTH"):
        for m in wo_materials(cur, wid):
            ln = g.node(f"ML:{m['lot_id']}", "MATERIAL", m["lot_no"], sub=f"{m['material_code']}", ref={"type": "MATERIAL_LOT", "id": m["lot_id"]}, status=m["lot_status"])
            g.nodes[ln].setdefault("used", {})[wid] = _f(m["qty"])
            g.nodes[ln]["unit"] = m["unit"]
            if m["supplier"]:
                sn = g.node(f"SP:{m['supplier']}", "SUPPLIER", m["supplier"], sub=f"Supplier lot: {m['supplier_lot_no'] or '-'}")
                g.edge(sn, ln, "MATERIAL")
            g.edge(ln, mn or wn, "MATERIAL")
    if with_products and direction in ("FORWARD", "BOTH"):
        cur.execute(LOT_SELECT + " WHERE pl.work_order_id=%s ORDER BY pl.first_output_at", (wid,))
        for x in cur.fetchall():
            q = lot_quality(cur, x)
            last = wn
            if q["plans"]:
                p = q["plans"][-1]
                inode = g.node(f"IN:{x['id']}", "INSPECTION", f"{len(q['plans'])} × {', '.join(sorted({pp['inspection_type'] for pp in q['plans']}))}",
                               sub=p["plan_no"], status=q["status"], ref={"type": "INSPECTION", "id": p["id"]})
                g.edge(wn, inode)
                last = inode
            pn = g.node(f"PL:{x['id']}", "PRODUCT", x["lot_no"], sub=x["product_name"], qty=f"{int(x['good_qty']):,} pcs", status=q["status"], ref={"type": "PRODUCT_LOT", "id": x["id"]})
            g.edge(last, pn)
            shn = g.node(f"SH:{x['id']}", "SHIPMENT", "Not integrated", sub=f"Customer: {x['customer_short'] or x['customer_name'] or '-'}", status="NA")
            g.edge(pn, shn, "INFO")
    return wn


def genealogy(cur, by, key, direction="BOTH", days=30):
    direction = (direction or "BOTH").upper()
    by = (by or "MATERIAL_LOT").upper()
    g = Graph()
    root = None
    if by == "MATERIAL_LOT":
        rows = resolve_material(cur, "LOT", key)
        if not rows:
            return None
        lot = rows[0]
        root = g.node(f"ML:{lot['id']}", "MATERIAL", lot["lot_no"], sub=lot["material_code"], ref={"type": "MATERIAL_LOT", "id": lot["id"]}, status=lot["status"])
        g.nodes[root]["unit"] = lot["unit"]
        if direction in ("BACKWARD", "BOTH") and lot["supplier"]:
            g.edge(g.node(f"SP:{lot['supplier']}", "SUPPLIER", lot["supplier"], sub=f"Supplier lot: {lot['supplier_lot_no'] or '-'}"), root, "MATERIAL")
        if direction in ("FORWARD", "BOTH"):
            cur.execute("SELECT work_order_id, SUM(qty) q FROM material_usages WHERE lot_id=%s AND status='ACTIVE' GROUP BY work_order_id", (lot["id"],))
            for u in cur.fetchall():
                wn = _add_wo(cur, g, u["work_order_id"], "FORWARD")
                g.nodes[root].setdefault("used", {})[u["work_order_id"]] = _f(u["q"])
                mc = next((a for a, b, k in g.edges if b == wn and a.startswith("MC:")), None)
                g.edge(root, mc or wn, "MATERIAL")
        ref = {"type": "MATERIAL_LOT", "id": lot["id"], "code": lot["lot_no"]}
    elif by == "PRODUCT_LOT":
        rows = resolve_product(cur, "LOT", key)
        if not rows:
            return None
        x = rows[0]
        _add_wo(cur, g, x["work_order_id"], "BOTH")
        root = f"PL:{x['id']}"
        # only this product lot on the forward side; the direction trims the other side
        drop = [n for n in g.nodes if n.startswith(("PL:", "IN:", "SH:")) and n.split(":")[1] != str(x["id"])]
        if direction == "BACKWARD":
            drop += [n for n in g.nodes if n.startswith("SH:")]
        if direction == "FORWARD":
            drop += [n for n in g.nodes if n.startswith(("ML:", "SP:"))]
        for nid in set(drop):
            g.nodes.pop(nid, None)
        g.edges = {e for e in g.edges if e[0] in g.nodes and e[1] in g.nodes}
        ref = {"type": "PRODUCT_LOT", "id": x["id"], "code": x["lot_no"]}
    elif by == "WO":
        cur.execute("SELECT id, wo_no FROM production_plan_details WHERE wo_no LIKE %s ORDER BY wo_no LIMIT 1", (f"%{key.strip()}%",))
        w = cur.fetchone()
        if not w:
            return None
        root = _add_wo(cur, g, w["id"], direction)
        ref = {"type": "WO", "id": w["id"], "code": w["wo_no"]}
    elif by in ("MACHINE", "MOLD"):
        table, col, code = ("equipment", "machine_id", "equipment_code") if by == "MACHINE" else ("molds", "mold_id", "mold_code")
        cur.execute(f"SELECT id, {code} AS code FROM {table} WHERE {code} LIKE %s LIMIT 1", (f"%{key.strip()}%",))
        obj = cur.fetchone()
        if not obj:
            return None
        cur.execute(f"""SELECT id FROM production_plan_details WHERE {col}=%s AND (started_at >= NOW() - INTERVAL %s DAY OR status IN ('IN_PRODUCTION','ON_HOLD'))
                        ORDER BY started_at""", (obj["id"], days))
        for r in cur.fetchall():
            _add_wo(cur, g, r["id"], direction)
        root = f"MC:{obj['id']}" if by == "MACHINE" else None
        ref = {"type": by, "id": obj["id"], "code": obj["code"]}
        if by == "MOLD":
            for n in g.nodes.values():
                if n.get("mold_id") == obj["id"]:
                    n["highlight"] = True
    else:
        raise HTTPException(status_code=400, detail="Unknown search type.")
    # layout: rows per column in insertion order
    rows_used = defaultdict(int)
    nodes = []
    for n in g.nodes.values():
        n["row"] = rows_used[n["col"]]
        rows_used[n["col"]] += 1
        nodes.append(n)
    for n in nodes:
        if n["col"] == "MATERIAL" and n.get("used"):
            n["qty"] = f"{sum(n['used'].values()):,.2f} {n.get('unit') or ''}".strip()
    return {"root": root, "ref": ref, "direction": direction, "columns": COLUMNS, "nodes": nodes,
            "edges": [{"from": a, "to": b, "kind": k} for a, b, k in sorted(g.edges)], "rows": max(rows_used.values() or [1])}


# =========================================================
# LOGS / KPIs
# =========================================================


def log_trace(cur, trace_type, by, key, found, ref, started, actor):
    cur.execute("INSERT INTO trace_logs (trace_type, search_by, keyword, found, result_ref, duration_ms, actor) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (trace_type, by, (key or "")[:100], 1 if found else 0, ref, int((time.perf_counter() - started) * 1000), actor))


def kpis(cur, trace_type):
    def stats(day):
        cur.execute("""SELECT COUNT(*) n, SUM(found) ok, COUNT(DISTINCT CASE WHEN found=1 THEN result_ref END) objs, AVG(duration_ms) ms
                       FROM trace_logs WHERE trace_type=%s AND DATE(created_at)=%s""", (trace_type, day))
        r = cur.fetchone()
        n = int(r["n"] or 0)
        return {"total": n, "success": round(int(r["ok"] or 0) / n * 100, 1) if n else None, "objects": int(r["objs"] or 0),
                "avg_sec": round(float(r["ms"]) / 1000, 2) if r["ms"] is not None else None}
    today, yday = stats(date.today()), stats(date.today() - timedelta(days=1))
    cur.execute("SELECT COUNT(*) n FROM product_lots")
    lots = int(cur.fetchone()["n"])
    # recall readiness: product lots whose chain is complete (material issue + inspection recorded)
    cur.execute("""SELECT COUNT(*) n FROM product_lots pl WHERE EXISTS (SELECT 1 FROM material_usages u WHERE u.work_order_id=pl.work_order_id AND u.status='ACTIVE')
                   AND EXISTS (SELECT 1 FROM quality_inspection_plans ip WHERE ip.work_order_id=pl.work_order_id AND ip.status='COMPLETED')""")
    ready = int(cur.fetchone()["n"])
    cur.execute("SELECT COUNT(*) n FROM material_lots"); mlots = int(cur.fetchone()["n"])
    cur.execute("SELECT COUNT(DISTINCT lot_id) n FROM material_usages WHERE status='ACTIVE'"); used_lots = int(cur.fetchone()["n"])
    cur.execute("SELECT COUNT(DISTINCT work_order_id) n FROM product_lots"); wos = int(cur.fetchone()["n"])
    cur.execute("SELECT COUNT(DISTINCT machine_id) m, COUNT(DISTINCT mold_id) o FROM product_lots"); r = cur.fetchone()
    cur.execute("SELECT COUNT(*) n FROM product_lots pl WHERE EXISTS (SELECT 1 FROM quality_ng_records n WHERE n.work_order_id=pl.work_order_id AND n.status IN %s)" % str(NG_OPEN))
    hold = int(cur.fetchone()["n"])
    return {"today": today, "yesterday": yday, "product_lots": lots, "recall_ready": round(ready / lots * 100, 1) if lots else None, "ready_lots": ready,
            "material_lots": mlots, "used_material_lots": used_lots, "work_orders": wos, "machines": int(r["m"] or 0), "molds": int(r["o"] or 0), "lots_with_open_ng": hold}


def history(cur, trace_type, limit=30):
    cur.execute("SELECT * FROM trace_logs WHERE trace_type=%s ORDER BY created_at DESC LIMIT %s", (trace_type, limit))
    return cur.fetchall()
