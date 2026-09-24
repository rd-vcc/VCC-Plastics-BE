"""Seed demo customers and Production Orders, and link the planning demo Work Orders.

Run after scripts.seed_production_planning_demo. Re-running is safe: existing
customers / orders are skipped.

Run with:

    python -m scripts.seed_production_order_demo
"""

from datetime import datetime, timedelta

from app.core.database import get_vcc_plastics_connection
from app.modules.production.orders.service import sync_order_status, write_history

ACTOR = "SEED"

CUSTOMERS = [
    ("MOLEX", "Molex Vietnam", "Molex"),
    ("SUMITOMO", "Sumitomo Electric", "Sumitomo"),
    ("DENSO", "Denso Manufacturing", "Denso"),
    ("PANASONIC", "Panasonic Vietnam", "Panasonic"),
    ("TOYOTA", "Toyota Boshoku", "Toyota"),
]

# (seq, customer idx, qty, priority, start offset days, duration h, status, source)
ORDERS = [
    (1, 0, 12000, "HIGH", -1, 48, "RELEASED", "SALES_ORDER"),
    (2, 1, 8000, "HIGH", -1, 60, "RELEASED", "SALES_ORDER"),
    (3, 2, 5000, "MEDIUM", 0, 30, "RELEASED", "SALES_ORDER"),
    (4, 3, 6000, "MEDIUM", 0, 32, "DRAFT", "FORECAST"),
    (5, 4, 4000, "HIGH", 1, 20, "DRAFT", "SALES_ORDER"),
    (6, 0, 3000, "LOW", -4, 24, "COMPLETED", "SALES_ORDER"),
    (7, 1, 2500, "LOW", -5, 24, "CLOSED", "MRP"),
    (8, 2, 3500, "MEDIUM", 2, 24, "DRAFT", "PLANNING"),
]


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        for code, name, short in CUSTOMERS:
            cur.execute("INSERT IGNORE INTO customers (customer_code, customer_name, short_name, created_by) VALUES (%s,%s,%s,%s)",
                        (code, name, short, ACTOR))
        cur.execute("SELECT id FROM customers WHERE customer_code IN (%s) ORDER BY FIELD(customer_code, %s)" % (
            ",".join(["%s"] * len(CUSTOMERS)), ",".join(["%s"] * len(CUSTOMERS))),
            tuple(c[0] for c in CUSTOMERS) * 2)
        customer_ids = [r["id"] for r in cur.fetchall()]
        cur.execute("SELECT id FROM products WHERE status='ACTIVE' ORDER BY id LIMIT 1")
        product = cur.fetchone()
        cur.execute("SELECT n.id FROM factory_structure_nodes n JOIN factory_node_types t ON t.id=n.node_type_id WHERE t.code='FACTORY' LIMIT 1")
        plant = cur.fetchone()
        if not product:
            print("No active product; nothing seeded.")
            return

        today = datetime.now().replace(hour=6, minute=0, second=0, microsecond=0)
        prefix = f"PO-{today:%y%m%d}-"
        order_ids = {}
        for seq, c_idx, qty, priority, offset, hours, status, source in ORDERS:
            order_no = f"{prefix}{seq:03d}"
            cur.execute("SELECT id FROM production_orders WHERE order_no=%s", (order_no,))
            row = cur.fetchone()
            if row:
                order_ids[seq] = row["id"]
                continue
            start = today + timedelta(days=offset)
            end = start + timedelta(hours=hours)
            cur.execute(
                """INSERT INTO production_orders
                   (order_no, customer_id, product_id, order_qty, priority, planned_start, planned_end, due_date,
                    factory_node_id, source, source_ref, status, released_by, released_at, completed_at, closed_by, closed_at,
                    created_by, created_at)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (order_no, customer_ids[c_idx], product["id"], qty, priority, start, end, end + timedelta(hours=12),
                 plant["id"] if plant else None, source, f"SO-{seq:05d}" if source == "SALES_ORDER" else None, status,
                 ACTOR if status != "DRAFT" else None, start - timedelta(days=1) if status != "DRAFT" else None,
                 end if status in {"COMPLETED", "CLOSED"} else None,
                 ACTOR if status == "CLOSED" else None, end + timedelta(hours=4) if status == "CLOSED" else None,
                 ACTOR, start - timedelta(days=2)),
            )
            order_id = order_ids[seq] = cur.lastrowid
            write_history(cur, order_id, "CREATE", None, "DRAFT", ACTOR, "Demo data")
            cur.execute("UPDATE production_order_status_history SET acted_at=%s WHERE production_order_id=%s",
                        (start - timedelta(days=2), order_id))

        # Link planning demo Work Orders: WO-001/003 -> PO-001, WO-002/005 -> PO-002, WO-004 -> PO-003, backlog -> PO-001/002
        links = {1: 1, 3: 1, 2: 2, 5: 2, 4: 3, 8: 1, 9: 2, 10: 3}
        wo_prefix = f"WO-{today:%y%m%d}-"
        for wo_seq, order_seq in links.items():
            cur.execute(
                "UPDATE production_plan_details SET production_order_id=%s WHERE wo_no=%s AND production_order_id IS NULL",
                (order_ids[order_seq], f"{wo_prefix}{wo_seq:03d}"))
        cur.execute("UPDATE production_plan_details SET reject_qty=ROUND(actual_qty*0.015) WHERE created_by='SEED' AND reject_qty=0")
        for seq in (1, 2, 3):
            sync_order_status(cur, order_ids[seq], ACTOR)
        conn.commit()
        print(f"Seeded {len(CUSTOMERS)} customers and {len(ORDERS)} production orders.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
