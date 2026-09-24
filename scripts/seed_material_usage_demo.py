"""Demo data for Material Usage (screen 4.4), built through the real service flow.

For each demo Work Order: open the full requirement, auto-allocate (FEFO), issue everything to the
machine, report the production result (back-flush at BOM standard), then one of: return left-over,
close-out as loss, extra manual consumption, or reverse + lower manual weighing. Timestamps are moved
back a few days so the usage trend has points. Safe to run once on the demo database.

    python -m scripts.seed_material_usage_demo
"""

from datetime import datetime

from app.core.database import get_vcc_plastics_connection
from app.modules.material.allocations.service import auto_allocate, issue, return_material
from app.modules.material.requirements.service import sync_work_order
from app.modules.material.usage.service import backflush, close_out, consume, reverse

ACTOR = "00842"

# wo_no, days back, scenario, complete?
PLAN = [
    ("WO-260923-005", 5, "saving", True),
    ("WO-260923-004", 3, "return", True),
    ("WO-260923-003", 1, "extra", False),
    ("WO-260923-002", 0, "adjust", False),
    ("WO-260923-001", 0, "close_out", False),
]


def in_use_allocs(cur, wo_id):
    cur.execute("""SELECT id, version, material_id, issued_qty - returned_qty - consumed_qty AS left_qty FROM material_allocations
                   WHERE work_order_id=%s AND status='IN_USE' ORDER BY id""", (wo_id,))
    return cur.fetchall()


def run_one(cur, wo_no, days_back, scenario, complete):
    cur.execute("SELECT * FROM production_plan_details WHERE wo_no=%s", (wo_no,))
    wo = cur.fetchone()
    if not wo:
        print("skip", wo_no); return
    cur.execute("SELECT COUNT(*) AS n FROM material_usages WHERE work_order_id=%s AND source<>'PRODUCTION_RESULT'", (wo["id"],))
    if cur.fetchone()["n"]:
        print("already seeded", wo_no); return
    actual, reject = int(wo["actual_qty"]), int(wo["reject_qty"])
    if complete:
        actual = int(wo["planned_qty"]) - reject
    # 1. before production: whole requirement open -> allocate and issue everything
    cur.execute("UPDATE production_plan_details SET actual_qty=0, reject_qty=0 WHERE id=%s", (wo["id"],))
    sync_work_order(cur, wo["id"], ACTOR)
    auto_allocate(cur, wo["id"], ACTOR)
    cur.execute("SELECT id, version FROM material_allocations WHERE work_order_id=%s AND reserved_qty > 0", (wo["id"],))
    for a in cur.fetchall():
        issue(cur, a["id"], None, None, ACTOR, a["version"])
    # 2. production result -> back-flush
    cur.execute("UPDATE production_plan_details SET actual_qty=%s, reject_qty=%s, status=%s WHERE id=%s",
                (actual, reject, "COMPLETED" if complete else wo["status"], wo["id"]))
    sync_work_order(cur, wo["id"], ACTOR)
    backflush(cur, wo["id"], ACTOR)
    # 3. scenario
    allocs = in_use_allocs(cur, wo["id"])
    resin = [a for a in allocs if a["material_id"] == 23]
    if scenario == "extra" and resin:
        consume(cur, resin[0]["id"], min(0.9, float(resin[0]["left_qty"])), "MANUAL", ACTOR, "Mold change purge / đổi khuôn xả nhựa")
    if scenario == "adjust" and resin:
        consume(cur, resin[0]["id"], min(0.2, float(resin[0]["left_qty"])), "MANUAL", ACTOR, "Machine setting / chỉnh máy")
    if scenario == "saving":
        cur.execute("SELECT id, allocation_id, qty FROM material_usages WHERE work_order_id=%s AND material_id=23 AND status='ACTIVE' ORDER BY id LIMIT 1", (wo["id"],))
        u = cur.fetchone()
        if u:
            reverse(cur, u["id"], "Actual weighing lower than BOM / cân thực tế thấp hơn", ACTOR)
            consume(cur, u["allocation_id"], round(float(u["qty"]) * 0.95, 3), "MANUAL", ACTOR, "Actual weighing / cân thực tế")
    if scenario == "close_out":
        close_out(cur, wo["id"], ACTOR, "Start-up scrap and purge / phế đầu ca, xả nhựa")
    else:
        for a in in_use_allocs(cur, wo["id"]):
            if float(a["left_qty"]) > 1e-3:
                return_material(cur, a["id"], round(float(a["left_qty"]), 3), "Left-over after production / dư sau sản xuất", ACTOR, a["version"])
    # 4. move timestamps back
    if days_back:
        cur.execute("SELECT allocation_no FROM material_allocations WHERE work_order_id=%s", (wo["id"],))
        refs = [r["allocation_no"] for r in cur.fetchall()]
        cur.execute("SELECT usage_no FROM material_usages WHERE work_order_id=%s", (wo["id"],))
        refs += [r["usage_no"] for r in cur.fetchall()]
        cur.execute("""UPDATE material_allocations SET reserved_at=reserved_at-INTERVAL %s DAY, issued_at=issued_at-INTERVAL %s DAY,
                       completed_at=completed_at-INTERVAL %s DAY, expires_at=expires_at-INTERVAL %s DAY WHERE work_order_id=%s""",
                    (days_back, days_back, days_back, days_back, wo["id"]))
        cur.execute("UPDATE material_usages SET used_at=used_at-INTERVAL %s DAY, reversed_at=reversed_at-INTERVAL %s DAY WHERE work_order_id=%s",
                    (days_back, days_back, wo["id"]))
        if refs:
            cur.execute(f"UPDATE material_lot_transactions SET acted_at=acted_at-INTERVAL %s DAY WHERE reference_no IN ({','.join(['%s'] * len(refs))})",
                        (days_back, *refs))
    print("seeded", wo_no, scenario)


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        for wo_no, days_back, scenario, complete in PLAN:
            run_one(cur, wo_no, days_back, scenario, complete)
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
    print(datetime.now(), "done")


if __name__ == "__main__":
    main()
