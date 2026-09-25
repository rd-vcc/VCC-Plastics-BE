"""Traceability API (7.1 Product Traceability, 7.2 Material Traceability, 7.3 Lot Genealogy)."""

import json
import time
from typing import Optional

from fastapi import APIRouter, HTTPException

from app.core.database import get_vcc_plastics_connection
from app.modules.production.work_orders.router import _system_status
from app.modules.traceability import service as ts

router = APIRouter(prefix="/api/traceability", tags=["Traceability"])


def _json(v):
    return json.loads(json.dumps(v, default=str))


def _run(action, write=False):
    conn = get_vcc_plastics_connection(); cur = conn.cursor(dictionary=True)
    try:
        result = action(cur)
        conn.commit()
        return result
    except HTTPException:
        conn.rollback(); raise
    except Exception as exc:
        conn.rollback(); raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cur.close(); conn.close()


@router.get("/lookups")
def lookups():
    def action(cur):
        ts.sync_lots(cur)
        cur.execute("SELECT lot_no FROM product_lots ORDER BY production_date DESC, lot_no DESC LIMIT 200")
        product_lots = [r["lot_no"] for r in cur.fetchall()]
        cur.execute("SELECT l.lot_no, m.material_code FROM material_lots l JOIN materials m ON m.id=l.material_id ORDER BY l.received_at DESC LIMIT 200")
        mats = cur.fetchall()
        cur.execute("SELECT DISTINCT d.wo_no FROM product_lots pl JOIN production_plan_details d ON d.id=pl.work_order_id ORDER BY d.wo_no DESC")
        wos = [r["wo_no"] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT o.order_no FROM product_lots pl JOIN production_plan_details d ON d.id=pl.work_order_id JOIN production_orders o ON o.id=d.production_order_id")
        orders = [r["order_no"] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT supplier FROM material_lots WHERE supplier IS NOT NULL")
        suppliers = [r["supplier"] for r in cur.fetchall()]
        cur.execute("SELECT DISTINCT supplier_lot_no FROM material_lots WHERE supplier_lot_no IS NOT NULL")
        supplier_lots = [r["supplier_lot_no"] for r in cur.fetchall()]
        cur.execute("SELECT equipment_code FROM equipment ORDER BY equipment_code")
        machines = [r["equipment_code"] for r in cur.fetchall()]
        cur.execute("SELECT mold_code FROM molds ORDER BY mold_code")
        molds = [r["mold_code"] for r in cur.fetchall()]
        return _json({"product_lots": product_lots, "material_lots": [m["lot_no"] for m in mats], "material_codes": sorted({m["material_code"] for m in mats}),
                      "work_orders": wos, "orders": orders, "suppliers": suppliers, "supplier_lots": supplier_lots, "machines": machines, "molds": molds})
    return _run(action)


@router.get("/summary/{trace_type}")
def summary(trace_type: str):
    t = trace_type.upper()
    if t not in ("PRODUCT", "MATERIAL", "GENEALOGY"):
        raise HTTPException(status_code=400, detail="Unknown trace type.")

    def action(cur):
        ts.sync_lots(cur)
        return _json({"kpis": ts.kpis(cur, t), "history": ts.history(cur, t), "system": _system_status(cur)})
    return _run(action)


@router.get("/product")
def product(by: str = "LOT", key: str = "", lot_id: Optional[int] = None, actor: Optional[str] = None):
    def action(cur):
        started = time.perf_counter()
        ts.sync_lots(cur)
        candidates = ts.resolve_product(cur, by, key) if not lot_id else []
        target = lot_id or (candidates[0]["id"] if candidates else None)
        if not target:
            ts.log_trace(cur, "PRODUCT", by, key, False, None, started, actor)
            return {"found": False, "candidates": []}
        data = ts.product_trace(cur, target)
        ts.log_trace(cur, "PRODUCT", by if not lot_id else "LOT", key or data["lot"]["lot_no"], True, data["lot"]["lot_no"], started, actor)
        return _json({"found": True, "candidates": [{"id": c["id"], "lot_no": c["lot_no"], "wo_no": c["wo_no"], "production_date": c["production_date"],
                                                      "shift_name": c["shift_name"], "good_qty": c["good_qty"]} for c in candidates], **data})
    return _run(action)


@router.get("/material")
def material(by: str = "LOT", key: str = "", lot_id: Optional[int] = None, actor: Optional[str] = None):
    def action(cur):
        started = time.perf_counter()
        ts.sync_lots(cur)
        candidates = ts.resolve_material(cur, by, key) if not lot_id else []
        target = lot_id or (candidates[0]["id"] if candidates else None)
        if not target:
            ts.log_trace(cur, "MATERIAL", by, key, False, None, started, actor)
            return {"found": False, "candidates": []}
        data = ts.material_trace(cur, target)
        ts.log_trace(cur, "MATERIAL", by if not lot_id else "LOT", key or data["lot"]["lot_no"], True, data["lot"]["lot_no"], started, actor)
        return _json({"found": True, "candidates": [{"id": c["id"], "lot_no": c["lot_no"], "material_code": c["material_code"], "supplier": c["supplier"],
                                                      "received_at": c["received_at"], "status": c["status"]} for c in candidates], **data})
    return _run(action)


@router.get("/genealogy")
def genealogy(by: str = "MATERIAL_LOT", key: str = "", direction: str = "BOTH", actor: Optional[str] = None):
    def action(cur):
        started = time.perf_counter()
        ts.sync_lots(cur)
        g = ts.genealogy(cur, by, key, direction)
        ts.log_trace(cur, "GENEALOGY", by, key, bool(g and g["nodes"]), g["ref"]["code"] if g else None, started, actor)
        if not g:
            return {"found": False}
        # details of the root for the side panel + usage / statistics / timeline
        detail = None
        ref = g["ref"]
        if ref["type"] == "MATERIAL_LOT":
            detail = ts.material_trace(cur, ref["id"])
        elif ref["type"] == "PRODUCT_LOT":
            p = ts.product_trace(cur, ref["id"])
            detail = {"lot": p["lot"], "usage": [{"work_order_id": p["work_order"]["id"], "wo_no": p["work_order"]["wo_no"], "machine_code": p["lot"]["machine_code"],
                                                  "mold_code": p["lot"]["mold_code"], "material": f"{m['lot_no']} ({m['material_code']})", "consumed": float(m["qty"]),
                                                  "unit": m["unit"], "used_at": m["first_used"], "operator": p["lot"]["operator_code"]} for m in p["materials"]],
                      "timeline": p["timeline"], "documents": p["documents"]}
        return _json({"found": True, **g, "detail": detail})
    return _run(action)


@router.get("/node")
def node(ref_type: str, ref_id: int):
    """Details of a genealogy node for the side panel."""
    def action(cur):
        t = ref_type.upper()
        if t == "MATERIAL_LOT":
            cur.execute(ts.MAT_LOT_SELECT + " WHERE l.id=%s", (ref_id,))
            return _json({"type": t, "data": cur.fetchone()})
        if t == "PRODUCT_LOT":
            lot = ts.get_lot(cur, ref_id)
            return _json({"type": t, "data": {**lot, "quality_status": ts.lot_quality(cur, lot)["status"]}})
        if t == "WO":
            return _json({"type": t, "data": ts.wo_info(cur, ref_id)})
        raise HTTPException(status_code=400, detail="Unknown node type.")
    return _run(action)
