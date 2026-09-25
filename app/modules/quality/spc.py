"""SPC engine (5.3 SPC Monitoring): control charts, capability and rule checks.

SPC never creates data: it reads the measurements recorded in Inspection Management.
- X̄-R chart: one subgroup = the samples of one inspection round (plan) of the characteristic.
- I-MR chart: one point = one measurement.
- Control limits are computed from the analysed window (phase I).
- Rules (Western Electric): R1 point beyond a control limit -> OUT_OF_CONTROL;
  R2 9 points on one side of CL, R3 6 points steadily up/down, R5 2 of 3 beyond 2 sigma,
  Cpk below target -> WARNING. Fewer than 5 points -> NO_DATA.
"""

import math
from collections import defaultdict
from statistics import mean, pstdev, stdev

# n: (A2, D3, D4, d2)
CONSTANTS = {
    2: (1.880, 0, 3.267, 1.128), 3: (1.023, 0, 2.574, 1.693), 4: (0.729, 0, 2.282, 2.059), 5: (0.577, 0, 2.114, 2.326),
    6: (0.483, 0, 2.004, 2.534), 7: (0.419, 0.076, 1.924, 2.704), 8: (0.373, 0.136, 1.864, 2.847), 9: (0.337, 0.184, 1.816, 2.970),
    10: (0.308, 0.223, 1.777, 3.078),
}
MIN_POINTS = 5


def _f(v):
    return float(v) if v is not None else None


def _r(v, d=4):
    return round(v, d) if v is not None and not math.isnan(v) else None


def _norm_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def load_measurements(cur, characteristic_id, filters=None, limit_days=None):
    """Measurements of one characteristic with their full traceability, oldest first."""
    f = filters or {}
    where, params = ["m.characteristic_id=%s", "m.measured_value IS NOT NULL"], [characteristic_id]
    for key, col in (("work_order_id", "ip.work_order_id"), ("machine_id", "ip.machine_id"), ("mold_id", "ip.mold_id"),
                     ("material_lot_id", "ip.material_lot_id"), ("shift_id", "ip.shift_id"), ("product_id", "ip.product_id")):
        if f.get(key):
            where.append(f"{col}=%s"); params.append(f[key])
    if f.get("date_from"):
        where.append("r.inspected_at >= %s"); params.append(f["date_from"])
    if f.get("date_to"):
        where.append("r.inspected_at < %s"); params.append(f["date_to"])
    if limit_days:
        where.append("r.inspected_at >= NOW() - INTERVAL %s DAY"); params.append(limit_days)
    cur.execute(
        f"""SELECT m.id, m.measured_value, m.result, r.id AS record_id, r.plan_id, r.sample_no, r.inspected_at, r.inspector, r.position, r.remark,
                   ip.plan_no, ip.work_order_id, d.wo_no, ip.machine_id, eq.equipment_code AS machine_code, ip.mold_id, mo.mold_code,
                   ip.material_lot_id, l.lot_no, ip.shift_id, me.equipment_code AS gauge_code, me.equipment_name AS gauge_name
            FROM quality_measurements m
            JOIN quality_inspection_records r ON r.id=m.record_id
            JOIN quality_inspection_plans ip ON ip.id=r.plan_id
            LEFT JOIN production_plan_details d ON d.id=ip.work_order_id
            LEFT JOIN equipment eq ON eq.id=ip.machine_id
            LEFT JOIN molds mo ON mo.id=ip.mold_id
            LEFT JOIN material_lots l ON l.id=ip.material_lot_id
            LEFT JOIN measuring_equipment me ON me.id=m.equipment_id
            WHERE {' AND '.join(where)} ORDER BY r.inspected_at, r.id""", tuple(params))
    return cur.fetchall()


def _points(rows, chart_type):
    """Chart points: [{value, range, n, time, plan_no, rows}] oldest first."""
    if chart_type == "IMR":
        pts, prev = [], None
        for r in rows:
            v = _f(r["measured_value"])
            pts.append({"value": v, "range": abs(v - prev) if prev is not None else None, "n": 1, "time": r["inspected_at"],
                        "label": r["plan_no"], "wo_no": r["wo_no"], "machine_code": r["machine_code"], "mold_code": r["mold_code"], "lot_no": r["lot_no"]})
            prev = v
        return pts
    groups = defaultdict(list)
    order = []
    for r in rows:
        if r["plan_id"] not in groups:
            order.append(r["plan_id"])
        groups[r["plan_id"]].append(r)
    pts = []
    for pid in order:
        g = groups[pid]
        vals = [_f(x["measured_value"]) for x in g]
        if len(vals) < 2:
            continue
        pts.append({"value": mean(vals), "range": max(vals) - min(vals), "n": len(vals), "time": g[-1]["inspected_at"], "label": g[0]["plan_no"],
                    "wo_no": g[0]["wo_no"], "machine_code": g[0]["machine_code"], "mold_code": g[0]["mold_code"], "lot_no": g[0]["lot_no"]})
    return pts


def _rules(values, cl, ucl, lcl):
    """Violations per point index: [(index, rule)]."""
    out = []
    sigma = (ucl - cl) / 3 if ucl is not None and ucl > cl else None
    for i, v in enumerate(values):
        if (ucl is not None and v > ucl) or (lcl is not None and v < lcl):
            out.append((i, "R1"))
    side_run, last_side = 0, 0
    for i, v in enumerate(values):
        side = 1 if v > cl else -1 if v < cl else 0
        side_run = side_run + 1 if side and side == last_side else (1 if side else 0)
        last_side = side
        if side_run == 9:
            out.append((i, "R2"))
    up = down = 1
    for i in range(1, len(values)):
        up = up + 1 if values[i] > values[i - 1] else 1
        down = down + 1 if values[i] < values[i - 1] else 1
        if up == 6 or down == 6:
            out.append((i, "R3"))
    if sigma:
        for i in range(2, len(values)):
            window = values[i - 2:i + 1]
            hi = sum(1 for v in window if v > cl + 2 * sigma)
            lo = sum(1 for v in window if v < cl - 2 * sigma)
            if (hi >= 2 and values[i] > cl + 2 * sigma) or (lo >= 2 and values[i] < cl - 2 * sigma):
                out.append((i, "R5"))
    return out


def analyse(char, rows, limit=None):
    """Full SPC analysis of one characteristic from its measurement rows."""
    lsl, usl, nominal = _f(char.get("lsl")), _f(char.get("usl")), _f(char.get("nominal_value"))
    chart_type = char.get("chart_type") or "XBAR_R"
    target = _f(char.get("cpk_target")) or 1.33
    base = {"characteristic_id": char["id"], "name": char["characteristic_name"], "unit": char.get("unit"), "chart_type": chart_type,
            "characteristic_type": char.get("characteristic_type") or "VARIABLE", "lsl": lsl, "usl": usl, "nominal": nominal,
            "cpk_target": target, "is_critical": bool(char.get("is_critical")), "count": len(rows)}
    pts = _points(rows, chart_type)
    if limit:
        pts = pts[-limit:]
    if len(pts) < MIN_POINTS:
        return {**base, "status": "NO_DATA", "points": [{**p, "violation": None} for p in pts], "violations": [], "cp": None, "cpk": None,
                "mean": None, "std_dev": None, "cl": None, "ucl": None, "lcl": None, "r_cl": None, "r_ucl": None, "r_lcl": None,
                "ppm_expected": None, "ppm_observed": None, "histogram": [], "subgroup": None}
    values = [p["value"] for p in pts]
    ranges = [p["range"] for p in pts if p["range"] is not None]
    rbar = mean(ranges) if ranges else 0
    cl = mean(values)
    if chart_type == "IMR":
        ucl, lcl = cl + 2.66 * rbar, cl - 2.66 * rbar
        r_ucl, r_lcl, d2, n = 3.267 * rbar, 0.0, 1.128, 1
    else:
        n = max(2, min(10, round(mean(p["n"] for p in pts))))
        a2, d3, d4, d2 = CONSTANTS[n]
        ucl, lcl = cl + a2 * rbar, cl - a2 * rbar
        r_ucl, r_lcl = d4 * rbar, d3 * rbar
    raw = [_f(r["measured_value"]) for r in rows]
    if limit:
        keep = {p["label"] for p in pts} if chart_type != "IMR" else None
        raw = [_f(r["measured_value"]) for r in rows if keep is None or r["plan_no"] in keep][-(limit * max(n, 1)):]
    sigma_within = rbar / d2 if rbar and d2 else None
    overall = stdev(raw) if len(raw) > 1 else None
    mu = mean(raw)
    cp = cpk = None
    if sigma_within:
        if lsl is not None and usl is not None:
            cp = (usl - lsl) / (6 * sigma_within)
        sides = [x for x in ((usl - mu) / (3 * sigma_within) if usl is not None else None,
                             (mu - lsl) / (3 * sigma_within) if lsl is not None else None) if x is not None]
        cpk = min(sides) if sides else None
    ppm_expected = None
    if sigma_within:
        p_out = (1 - _norm_cdf((usl - mu) / sigma_within) if usl is not None else 0) + (_norm_cdf((lsl - mu) / sigma_within) if lsl is not None else 0)
        ppm_expected = p_out * 1e6
    outside = sum(1 for v in raw if (usl is not None and v > usl) or (lsl is not None and v < lsl))
    viol = _rules(values, cl, ucl, lcl)
    if chart_type != "IMR" or ranges:
        rvals = [p["range"] if p["range"] is not None else 0 for p in pts]
        viol += [(i, "RR") for i, v in enumerate(rvals) if v > r_ucl]
    by_idx = {}
    for i, rule in viol:
        by_idx.setdefault(i, rule if rule != "RR" else "RR")
    status = "NORMAL"
    if any(rule in ("R1", "RR") for _, rule in viol):
        status = "OUT_OF_CONTROL"
    elif viol or (cpk is not None and cpk < target):
        status = "WARNING"
    # histogram: 10 bins spanning the data and the specification limits
    lo = min([v for v in raw] + [x for x in (lsl,) if x is not None])
    hi = max([v for v in raw] + [x for x in (usl,) if x is not None])
    width = (hi - lo) / 10 if hi > lo else 1
    bins = [0] * 10
    for v in raw:
        bins[min(9, int((v - lo) / width))] += 1
    histogram = [{"from": _r(lo + i * width), "to": _r(lo + (i + 1) * width), "count": c} for i, c in enumerate(bins)]
    violations = [{"index": i, "point_no": i + 1, "rule": rule, "value": _r(values[i]) if rule != "RR" else _r(pts[i]["range"]),
                   "time": pts[i]["time"], "label": pts[i]["label"]} for i, rule in sorted(viol)]
    return {**base, "status": status, "subgroup": n,
            "points": [{"no": i + 1, "value": _r(p["value"]), "range": _r(p["range"]), "n": p["n"], "time": p["time"], "label": p["label"],
                        "wo_no": p["wo_no"], "machine_code": p["machine_code"], "mold_code": p["mold_code"], "lot_no": p["lot_no"],
                        "violation": by_idx.get(i)} for i, p in enumerate(pts)],
            "violations": violations, "cp": _r(cp, 3), "cpk": _r(cpk, 3), "mean": _r(mu), "std_dev": _r(overall), "sigma_within": _r(sigma_within),
            "cl": _r(cl), "ucl": _r(ucl), "lcl": _r(lcl), "r_cl": _r(rbar), "r_ucl": _r(r_ucl), "r_lcl": _r(r_lcl),
            "ppm_expected": round(ppm_expected) if ppm_expected is not None else None,
            "ppm_observed": round(outside / len(raw) * 1e6) if raw else None, "outside_spec": outside, "histogram": histogram}


def cpk_trend(char, rows):
    """Daily Cpk (overall sigma of the day), days with at least 5 values."""
    lsl, usl = _f(char.get("lsl")), _f(char.get("usl"))
    days = defaultdict(list)
    for r in rows:
        days[r["inspected_at"].date()].append(_f(r["measured_value"]))
    out = []
    for d in sorted(days):
        vals = days[d]
        if len(vals) < 5:
            continue
        mu, sd = mean(vals), pstdev(vals)
        if not sd:
            continue
        sides = [x for x in ((usl - mu) / (3 * sd) if usl is not None else None, (mu - lsl) / (3 * sd) if lsl is not None else None) if x is not None]
        out.append({"date": d, "cpk": round(min(sides), 3) if sides else None, "count": len(vals)})
    return out


def attribute_summary(char, cur, filters=None):
    """Attribute characteristic: OK / NG counts; any NG in the window is a warning."""
    f = filters or {}
    where, params = ["m.characteristic_id=%s"], [char["id"]]
    for key, col in (("work_order_id", "ip.work_order_id"), ("machine_id", "ip.machine_id"), ("mold_id", "ip.mold_id"), ("shift_id", "ip.shift_id")):
        if f.get(key):
            where.append(f"{col}=%s"); params.append(f[key])
    if f.get("date_from"):
        where.append("r.inspected_at >= %s"); params.append(f["date_from"])
    if f.get("date_to"):
        where.append("r.inspected_at < %s"); params.append(f["date_to"])
    cur.execute(f"""SELECT COUNT(*) n, SUM(m.result='NG') ng FROM quality_measurements m JOIN quality_inspection_records r ON r.id=m.record_id
                    JOIN quality_inspection_plans ip ON ip.id=r.plan_id WHERE {' AND '.join(where)}""", tuple(params))
    row = cur.fetchone()
    n, ng = int(row["n"] or 0), int(row["ng"] or 0)
    return {"characteristic_id": char["id"], "name": char["characteristic_name"], "unit": char.get("unit"), "chart_type": "ATTRIBUTE",
            "characteristic_type": "ATTRIBUTE", "is_critical": bool(char.get("is_critical")), "count": n, "ng": ng,
            "status": "NO_DATA" if n == 0 else "WARNING" if ng else "NORMAL", "cp": None, "cpk": None, "points": [], "violations": []}
