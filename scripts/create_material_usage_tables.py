"""One-off script: Material Management step 4 (Material Usage, screen 4.4).

material_usages: one consumption record = material taken from ONE allocation (lot at the machine)
and used for a Work Order.
  source PRODUCTION_RESULT  back-flushed from a production progress report (BOM standard x produced qty)
         CLOSE_OUT          what was still at the machine when the Work Order was closed (loss / purge)
         MANUAL             entered by an operator for special cases (reason required)
         IOT                reserved for machine data
A record can be REVERSED (never deleted); the allocation's consumed_qty is kept equal to the
sum of ACTIVE records. Standard usage = active BOM of the Work Order x (actual_qty + reject_qty).

Run with:

    python -m scripts.create_material_usage_tables
"""

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS material_usages (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        usage_no VARCHAR(50) NOT NULL,
        work_order_id BIGINT UNSIGNED NOT NULL,
        allocation_id BIGINT UNSIGNED NOT NULL,
        material_id BIGINT UNSIGNED NOT NULL,
        lot_id BIGINT UNSIGNED NOT NULL,
        machine_id INT UNSIGNED NULL,
        qty DECIMAL(14,3) NOT NULL,
        source ENUM('PRODUCTION_RESULT','CLOSE_OUT','MANUAL','IOT') NOT NULL,
        status ENUM('ACTIVE','REVERSED') NOT NULL DEFAULT 'ACTIVE',
        used_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        actor VARCHAR(100) NULL,
        remark VARCHAR(500) NULL,
        reversed_at DATETIME NULL,
        reversed_by VARCHAR(100) NULL,
        reverse_reason VARCHAR(500) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_material_usages_no (usage_no),
        KEY idx_material_usages_wo (work_order_id, material_id),
        KEY idx_material_usages_time (used_at),
        KEY idx_material_usages_alloc (allocation_id),
        CONSTRAINT fk_mu_work_order FOREIGN KEY (work_order_id) REFERENCES production_plan_details(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_mu_allocation FOREIGN KEY (allocation_id) REFERENCES material_allocations(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_mu_material FOREIGN KEY (material_id) REFERENCES materials(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_mu_lot FOREIGN KEY (lot_id) REFERENCES material_lots(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_mu_machine FOREIGN KEY (machine_id) REFERENCES equipment(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        for statement in STATEMENTS:
            cur.execute(statement)
        conn.commit()
        print("Material usage table created (or already existed).")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
