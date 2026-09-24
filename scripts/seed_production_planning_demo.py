"""Seed demo data for the Production Planning screen (today's first active shift).

Creates one Draft plan, a handful of scheduled Work Orders on the existing
injection machines, a small backlog, and on-hand stock for the materials used.
Re-running is safe: it skips Work Orders / plans that already exist.

Run with:

    python -m scripts.seed_production_planning_demo
"""

from datetime import date, datetime, timedelta

from app.core.database import get_vcc_plastics_connection
from app.modules.production.planning.service import compute_end, shift_window

ACTOR = "SEED"


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT * FROM shifts WHERE status='ACTIVE' ORDER BY start_time LIMIT 1")
        shift = cur.fetchone()
        cur.execute(
            """SELECT n.id, n.code FROM factory_structure_nodes n JOIN factory_node_types t ON t.id=n.node_type_id
               WHERE t.code='FACTORY' ORDER BY n.id LIMIT 1""")
        plant = cur.fetchone()
        cur.execute(
            """SELECT e.id, e.equipment_code, e.operational_status FROM equipment e
               JOIN equipment_types t ON t.id=e.equipment_type_id
               WHERE t.category='MACHINE' AND e.asset_status='ACTIVE' ORDER BY e.equipment_code""")
        machines = [m for m in cur.fetchall() if m["operational_status"] not in {"MAINTENANCE", "DOWN", "OFFLINE"}]
        cur.execute("SELECT id FROM products WHERE status='ACTIVE' ORDER BY id LIMIT 1")
        product = cur.fetchone()
        cur.execute("SELECT id, mold_code FROM molds WHERE is_active=1 ORDER BY mold_code")
        molds = cur.fetchall()
        cur.execute("SELECT id, material_code FROM materials WHERE status='ACTIVE' ORDER BY id LIMIT 4")
        materials = cur.fetchall()
        if not (shift and product and machines and molds and materials):
            print("Missing master data (shift / product / machine / mold / material); nothing seeded.")
            return

        today = date.today()
        win_start, _win_end = shift_window(today, shift)
        plan_code = f"PP-{today:%Y%m%d}-{shift['shift_code']}-{plant['code'] if plant else 'ALL'}"
        cur.execute("SELECT id FROM production_plans WHERE plan_code=%s", (plan_code,))
        row = cur.fetchone()
        if row:
            plan_id = row["id"]
        else:
            cur.execute(
                """INSERT INTO production_plans (plan_code, plan_date, shift_id, factory_node_id, remark, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s)""",
                (plan_code, today, shift["id"], plant["id"] if plant else None, "Demo plan", ACTOR))
            plan_id = cur.lastrowid

        for material, available, safety in zip(materials, (800, 120, 60, 0), (200, 100, 50, 50)):
            cur.execute(
                """INSERT IGNORE INTO material_inventory_balances (material_id, available_qty, safety_stock_qty, updated_by)
                   VALUES (%s,%s,%s,%s)""", (material["id"], available, safety, ACTOR))

        mold = lambda index: molds[index % len(molds)]["id"]  # noqa: E731
        machine = lambda index: machines[index % len(machines)]["id"]  # noqa: E731
        prefix = f"WO-{today:%y%m%d}-"
        # (seq, machine idx, start offset h, qty, cavity, cycle s, weight g, material idx, mold idx, priority, due offset h)
        scheduled = [
            (1, 0, 0.0, 3000, 4, 24, 12.5, 0, 0, "HIGH", 6),
            (2, 1, 0.5, 1400, 2, 30, 18.0, 1, 1, "MEDIUM", 7),
            (3, 0, 5.5, 1000, 4, 26, 12.5, 0, 0, "MEDIUM", 8),
            (4, 2, 1.0, 500, 1, 35, 40.0, 2, 2, "URGENT", 7),
            (5, 1, 6.8, 300, 2, 30, 9.0, 3, 1, "LOW", 12),
        ]
        now = datetime.now()
        for seq, m_idx, offset, qty, cavity, cycle, weight, mat_idx, mold_idx, priority, due in scheduled:
            wo_no = f"{prefix}{seq:03d}"
            cur.execute("SELECT id FROM production_plan_details WHERE wo_no=%s", (wo_no,))
            if cur.fetchone():
                continue
            item = {"planned_qty": qty, "cavity": cavity, "cycle_time_sec": cycle, "setup_minutes": 20}
            start = win_start + timedelta(hours=offset)
            end = compute_end(start, item)
            fraction = min(max((now - start).total_seconds() / max((end - start).total_seconds(), 1), 0), 1)
            actual = int(qty * fraction * (0.95 if seq != 2 else 0.6))
            cur.execute(
                """INSERT INTO production_plan_details
                   (wo_no, plan_id, product_id, mold_id, machine_id, material_id, planned_qty, actual_qty, cavity,
                    cycle_time_sec, setup_minutes, part_weight_g, planned_start, planned_end, due_date, priority,
                    source, status, created_by)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,20,%s,%s,%s,%s,%s,'SALES_ORDER','SCHEDULED',%s)""",
                (wo_no, plan_id, product["id"], mold(mold_idx), machine(m_idx), materials[mat_idx]["id"], qty, actual,
                 cavity, cycle, weight, start, end, win_start + timedelta(hours=due), priority, ACTOR))

        backlog = [
            (8, 2000, "HIGH", "Customer urgent", 6),
            (9, 1600, "MEDIUM", "Plan change", 30),
            (10, 1200, "LOW", "Forecast", 72),
        ]
        for seq, qty, priority, reason, due in backlog:
            wo_no = f"{prefix}{seq:03d}"
            cur.execute("SELECT id FROM production_plan_details WHERE wo_no=%s", (wo_no,))
            if cur.fetchone():
                continue
            cur.execute(
                """INSERT INTO production_plan_details
                   (wo_no, product_id, mold_id, material_id, planned_qty, cavity, cycle_time_sec, setup_minutes,
                    part_weight_g, due_date, priority, source, backlog_reason, status, created_by)
                   VALUES (%s,%s,%s,%s,%s,4,24,15,12.5,%s,%s,'SALES_ORDER',%s,'UNSCHEDULED',%s)""",
                (wo_no, product["id"], mold(0), materials[0]["id"], qty, win_start + timedelta(hours=due),
                 priority, reason, ACTOR))
        conn.commit()
        print(f"Production Planning demo seeded into plan {plan_code}.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
