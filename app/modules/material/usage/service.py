"""Material Usage (screen 4.4): record consumption of material at the machine and compare with the BOM.

Rules (spec 4.4 / 4.3):
- Material can only be consumed from an allocation that is at the machine (issued - returned - consumed).
- Standard usage of a Work Order = its BOM x (actual_qty + reject_qty): scrap parts used material too.
- Back-flush (PRODUCTION_RESULT) consumes the standard for the newly produced quantity, earliest issued first.
- Close-out (CLOSE_OUT) consumes whatever is still at the machine: left-over should be returned first
  on the Material Allocation screen, the rest is treated as loss (purge, start-up scrap...).
- Status of a line: PENDING while nothing is consumed yet, OVER when actual exceeds standard by more than material.usage_tolerance_pct,
  SAVING when below by more than the tolerance, otherwise WITHIN.
"""

from datetime import date

from fastapi import HTTPException

from app.modules.material.allocations.service import _after_change, _lock_lot, get_allocation
from app.modules.material.inventory.service import material_category, write_txn
from app.modules.material.requirements.service import _active_bom, _bom_rows

EPS = 1e-6
TOL = 1e-3

USAGE_SELECT = """
    SELECT u.*, d.wo_no, p.product_code, p.product_name, m.material_code, m.material_name, m.material_type,
           m.material_group, m.unit, l.lot_no, e.equipment_code AS machine_code, a.allocation_no
    FROM material_usages u
    JOIN production_plan_details d ON d.id = u.work_order_id
    JOIN products p ON p.id = d.product_id
    JOIN materials m ON m.id = u.material_id
    JOIN material_lots l ON l.id = u.lot_id
    JOIN material_allocations a ON a.id = u.allocation_id
    LEFT JOIN equipment e ON e.id = u.machine_id
"""


def decorate_usage(u):
    return {**u, "qty": float(u["qty"]), "category": material_category(u["material_type"], u["material_group"], u["material_code"])}


def next_usage_no(cur) -> str:
    stem = f"MU-{date.today():%y%m%d}-"
    cur.execute("SELECT usage_no FROM material_usages WHERE usage_no LIKE %s ORDER BY usage_no DESC LIMIT 1", (f"{stem}%",))
    row = cur.fetchone()
    tail = row["usage_no"][len(stem):] if row else ""
    return f"{stem}{(int(tail) + 1 if tail.isdigit() else 1):03d}"


def variance_status(standard, actual, tolerance_pct):
    if standard <= EPS:
        return "OVER" if actual > TOL else "WITHIN"
    pct = (actual - standard) / standard * 100
    return "OVER" if pct > tolerance_pct + EPS else "SAVING" if pct < -tolerance_pct - EPS else "WITHIN"


def work_order_bom(cur, wo):
    cur.execute("SELECT bom_id FROM material_requirements WHERE work_order_id=%s AND bom_id IS NOT NULL LIMIT 1", (wo["id"],))
    row = cur.fetchone()
    if row:
        return row["bom_id"]
    bom = _active_bom(cur, wo["product_id"])
    return bom["id"] if bom else None


def standard_by_material(cur, wo):
    """{material_id: standard qty} for what the Work Order has produced so far (good + reject)."""
    produced = int(wo["actual_qty"] or 0) + int(wo["reject_qty"] or 0)
    bom_id = work_order_bom(cur, wo)
    if not bom_id or produced <= 0:
        return {}
    return {r["material_id"]: r["required_qty"] for r in _bom_rows(cur, bom_id, produced)}


def _get_wo(cur, wo_id):
    cur.execute("SELECT id, wo_no, product_id, planned_qty, actual_qty, reject_qty, status, machine_id FROM production_plan_details WHERE id=%s", (wo_id,))
    wo = cur.fetchone()
    if not wo:
        raise HTTPException(status_code=404, detail="Work order not found.")
    return wo


def _in_use(a):
    return float(a["issued_qty"]) - float(a["returned_qty"]) - float(a["consumed_qty"])


def _close_lot_if_empty(cur, lot_id, actor, reference_no):
    """A lot with nothing left in the warehouse and nothing at a machine is CONSUMED."""
    lot = _lock_lot(cur, lot_id)
    if float(lot["current_qty"]) > EPS or lot["status"] not in {"IN_USE", "IN_STOCK"}:
        return
    cur.execute("""SELECT COUNT(*) AS n FROM material_allocations WHERE lot_id=%s AND status IN ('RESERVED','IN_USE')
                   AND (reserved_qty > 0 OR issued_qty - returned_qty - consumed_qty > 0.0005)""", (lot_id,))
    if cur.fetchone()["n"]:
        return
    cur.execute("UPDATE material_lots SET status='CONSUMED', version=version+1 WHERE id=%s", (lot_id,))
    write_txn(cur, lot_id, "CLOSE", 0, 0, "CONSUMED", actor, reference_no=reference_no, remark="Lot fully consumed")


def consume(cur, allocation_id, qty, source, actor, remark=None):
    a = get_allocation(cur, allocation_id, for_update=True)
    in_use = _in_use(a)
    if qty <= EPS or qty > in_use + TOL:
        raise HTTPException(status_code=409, detail=f"Consume between 0 and the quantity at the machine ({round(in_use, 3):g} {a['unit']}).")
    qty = round(min(qty, in_use), 3)
    left = in_use - qty
    status = a["status"]
    if left <= TOL and float(a["reserved_qty"]) <= EPS:
        status = "COMPLETED"
    cur.execute(
        """UPDATE material_allocations SET consumed_qty=consumed_qty+%s, status=%s, completed_at=IF(%s='COMPLETED',NOW(),completed_at),
           updated_by=%s, version=version+1 WHERE id=%s""", (qty, status, status, actor, allocation_id))
    usage_no = next_usage_no(cur)
    cur.execute(
        """INSERT INTO material_usages (usage_no, work_order_id, allocation_id, material_id, lot_id, machine_id, qty, source, actor, remark)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
        (usage_no, a["work_order_id"], allocation_id, a["material_id"], a["lot_id"], a["machine_id"], qty, source, actor or "SYSTEM", remark))
    lot = _lock_lot(cur, a["lot_id"])
    write_txn(cur, a["lot_id"], "CONSUMPTION", 0, float(lot["current_qty"]), lot["status"], actor, work_order_id=a["work_order_id"],
              machine_id=a["machine_id"], reference_no=usage_no, remark=f"{qty:g} {a['unit']} consumed ({source}){f' · {remark}' if remark else ''}")
    _close_lot_if_empty(cur, a["lot_id"], actor, usage_no)
    return {"usage_no": usage_no, "qty": qty, "material_code": a["material_code"], "unit": a["unit"]}


def _consume_material(cur, wo_id, material_id, qty, source, actor, remark=None):
    """Consume qty of one material from the Work Order's allocations at the machine, earliest issued first."""
    cur.execute("""SELECT id, issued_qty, returned_qty, consumed_qty FROM material_allocations
                   WHERE work_order_id=%s AND material_id=%s AND status='IN_USE' ORDER BY issued_at, id""", (wo_id, material_id))
    done, left = [], qty
    for a in cur.fetchall():
        if left <= TOL:
            break
        take = min(_in_use(a), left)
        if take <= TOL:
            continue
        done.append(consume(cur, a["id"], take, source, actor, remark))
        left -= take
    return done, round(max(left, 0), 3)


def consumed_by_material(cur, wo_id):
    cur.execute("SELECT material_id, SUM(qty) AS qty FROM material_usages WHERE work_order_id=%s AND status='ACTIVE' GROUP BY material_id", (wo_id,))
    return {r["material_id"]: float(r["qty"]) for r in cur.fetchall()}


def backflush(cur, wo_id, actor):
    """Consume the BOM standard for what was produced and is not yet consumed."""
    wo = _get_wo(cur, wo_id)
    standard = standard_by_material(cur, wo)
    consumed = consumed_by_material(cur, wo_id)
    created, short = [], []
    for material_id, std in standard.items():
        need = round(std - consumed.get(material_id, 0.0), 3)
        if need <= TOL:
            continue
        done, left = _consume_material(cur, wo_id, material_id, need, "PRODUCTION_RESULT", actor, f"Back-flush {wo['wo_no']}")
        created += done
        if left > TOL:
            cur.execute("SELECT material_code, unit FROM materials WHERE id=%s", (material_id,))
            m = cur.fetchone()
            short.append({"material_code": m["material_code"], "unit": m["unit"], "missing_qty": left})
    if created:
        _after_change(cur, wo_id)
    return {"created": created, "not_at_machine": short}


def close_out(cur, wo_id, actor, remark=None):
    """Consume everything still at the machine for this Work Order (loss), after a final back-flush."""
    wo = _get_wo(cur, wo_id)
    if wo["status"] not in {"SCHEDULED", "RELEASED", "IN_PRODUCTION", "ON_HOLD", "COMPLETED"}:
        raise HTTPException(status_code=409, detail="Only running or completed work orders can be closed out.")
    result = backflush(cur, wo_id, actor)
    cur.execute("SELECT id, issued_qty, returned_qty, consumed_qty FROM material_allocations WHERE work_order_id=%s AND status='IN_USE'", (wo_id,))
    loss = []
    for a in cur.fetchall():
        left = round(_in_use(a), 3)
        if left > TOL:
            loss.append(consume(cur, a["id"], left, "CLOSE_OUT", actor, remark or "Close-out: left at machine"))
    _after_change(cur, wo_id)
    return {"backflush": result["created"], "loss": loss}


def manual(cur, allocation_id, qty, actor, remark):
    a = get_allocation(cur, allocation_id)
    result = consume(cur, allocation_id, qty, "MANUAL", actor, remark)
    _after_change(cur, a["work_order_id"])
    return result


def reverse(cur, usage_id, reason, actor):
    cur.execute("SELECT * FROM material_usages WHERE id=%s FOR UPDATE", (usage_id,))
    u = cur.fetchone()
    if not u:
        raise HTTPException(status_code=404, detail="Usage record not found.")
    if u["status"] != "ACTIVE":
        raise HTTPException(status_code=409, detail="This usage record is already reversed.")
    a = get_allocation(cur, u["allocation_id"], for_update=True)
    if a["status"] == "CANCELLED":
        raise HTTPException(status_code=409, detail="The allocation is cancelled.")
    qty = float(u["qty"])
    cur.execute("""UPDATE material_allocations SET consumed_qty=GREATEST(consumed_qty-%s,0), status='IN_USE', completed_at=NULL,
                   updated_by=%s, version=version+1 WHERE id=%s""", (qty, actor, a["id"]))
    cur.execute("UPDATE material_usages SET status='REVERSED', reversed_at=NOW(), reversed_by=%s, reverse_reason=%s WHERE id=%s",
                (actor, reason, usage_id))
    lot = _lock_lot(cur, u["lot_id"])
    lot_status = "IN_USE" if lot["status"] == "CONSUMED" else lot["status"]
    if lot_status != lot["status"]:
        cur.execute("UPDATE material_lots SET status=%s, version=version+1 WHERE id=%s", (lot_status, lot["id"]))
    write_txn(cur, lot["id"], "CONSUMPTION", 0, float(lot["current_qty"]), lot_status, actor, work_order_id=u["work_order_id"],
              machine_id=u["machine_id"], reference_no=u["usage_no"], remark=f"Reversed {qty:g} {a['unit']} · {reason}")
    _after_change(cur, u["work_order_id"])


# =========================================================
# ANALYSIS
# =========================================================


def work_order_lines(cur, wo_ids, tolerance_pct):
    """Standard vs actual per Work Order x material, for the given Work Orders."""
    if not wo_ids:
        return []
    marks = ",".join(["%s"] * len(wo_ids))
    cur.execute(
        f"""SELECT d.id, d.wo_no, d.product_id, d.planned_qty, d.actual_qty, d.reject_qty, d.status, d.machine_id,
                   p.product_code, p.product_name, e.equipment_code AS machine_code
            FROM production_plan_details d JOIN products p ON p.id = d.product_id LEFT JOIN equipment e ON e.id = d.machine_id
            WHERE d.id IN ({marks})""", tuple(wo_ids))
    wos = {w["id"]: w for w in cur.fetchall()}
    cur.execute(
        f"""SELECT work_order_id, material_id, SUM(qty) AS qty, MAX(used_at) AS last_used, COUNT(*) AS n
            FROM material_usages WHERE work_order_id IN ({marks}) AND status='ACTIVE' GROUP BY work_order_id, material_id""", tuple(wo_ids))
    actual = {(r["work_order_id"], r["material_id"]): r for r in cur.fetchall()}
    material_ids = {k[1] for k in actual}
    standards = {}
    for wo in wos.values():
        standards[wo["id"]] = standard_by_material(cur, wo)
        material_ids |= set(standards[wo["id"]])
    materials = {}
    if material_ids:
        cur.execute(f"SELECT id, material_code, material_name, material_type, material_group, unit FROM materials WHERE id IN ({','.join(['%s'] * len(material_ids))})",
                    tuple(material_ids))
        materials = {m["id"]: m for m in cur.fetchall()}
    rows = []
    for wo_id, wo in wos.items():
        mids = set(standards[wo_id]) | {k[1] for k in actual if k[0] == wo_id}
        for mid in mids:
            m = materials[mid]
            std = round(standards[wo_id].get(mid, 0.0), 3)
            act_row = actual.get((wo_id, mid))
            act = round(float(act_row["qty"]), 3) if act_row else 0.0
            diff = round(act - std, 3)
            rows.append({
                "key": f"{wo_id}-{mid}", "work_order_id": wo_id, "wo_no": wo["wo_no"], "wo_status": wo["status"],
                "product_code": wo["product_code"], "product_name": wo["product_name"], "machine_id": wo["machine_id"], "machine_code": wo["machine_code"],
                "produced_qty": int(wo["actual_qty"] or 0) + int(wo["reject_qty"] or 0),
                "material_id": mid, "material_code": m["material_code"], "material_name": m["material_name"], "unit": m["unit"],
                "category": material_category(m["material_type"], m["material_group"], m["material_code"]),
                "standard_qty": std, "actual_qty": act, "diff_qty": diff,
                "diff_pct": round(diff / std * 100, 2) if std > EPS else None,
                "usage_rate": round(act / std * 100, 2) if std > EPS else None,
                "status": variance_status(std, act, tolerance_pct) if act_row else "PENDING",
                "last_used": act_row["last_used"] if act_row else None,
            })
    rows.sort(key=lambda r: (r["wo_no"], r["material_code"]), reverse=False)
    return rows


def in_use_allocations(cur, wo_id):
    cur.execute(
        """SELECT a.id, a.allocation_no, a.material_id, a.issued_qty, a.returned_qty, a.consumed_qty, a.version, m.material_code, m.unit,
                  l.lot_no, e.equipment_code AS machine_code
           FROM material_allocations a JOIN materials m ON m.id = a.material_id JOIN material_lots l ON l.id = a.lot_id
           LEFT JOIN equipment e ON e.id = a.machine_id
           WHERE a.work_order_id=%s AND a.status='IN_USE' ORDER BY m.material_code, a.issued_at""", (wo_id,))
    out = []
    for a in cur.fetchall():
        left = round(_in_use(a), 3)
        if left > TOL:
            out.append({**a, "issued_qty": float(a["issued_qty"]), "returned_qty": float(a["returned_qty"]),
                        "consumed_qty": float(a["consumed_qty"]), "in_use_qty": left})
    return out


