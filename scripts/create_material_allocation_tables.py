"""One-off script: Material Management step 3 (Material Allocation, screen 4.3).

material_allocations: one reservation of ONE lot for one Work Order requirement line.
  reserved_qty  = still reserved in the warehouse (counted in material_lots.reserved_qty)
  issued_qty    = handed out to the machine (deducted from the lot's current_qty)
  returned_qty  = brought back from the machine to the lot
  consumed_qty  = recorded as used (step 4, Material Usage)
  in use at the machine = issued_qty - returned_qty - consumed_qty
Status: RESERVED -> IN_USE -> COMPLETED, or CANCELLED. Reservations expire after
system setting material.reservation_hours and are released automatically.
Every movement is also written to material_lot_transactions with reference_no = allocation_no.

Run with:

    python -m scripts.create_material_allocation_tables
"""

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS material_allocations (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        allocation_no VARCHAR(50) NOT NULL,
        work_order_id BIGINT UNSIGNED NOT NULL,
        requirement_id BIGINT UNSIGNED NOT NULL,
        material_id BIGINT UNSIGNED NOT NULL,
        lot_id BIGINT UNSIGNED NOT NULL,
        location_id BIGINT UNSIGNED NOT NULL,
        machine_id INT UNSIGNED NULL,
        reserved_qty DECIMAL(14,3) NOT NULL DEFAULT 0,
        issued_qty DECIMAL(14,3) NOT NULL DEFAULT 0,
        returned_qty DECIMAL(14,3) NOT NULL DEFAULT 0,
        consumed_qty DECIMAL(14,3) NOT NULL DEFAULT 0,
        initial_qty DECIMAL(14,3) NOT NULL,
        status ENUM('RESERVED','IN_USE','COMPLETED','CANCELLED') NOT NULL DEFAULT 'RESERVED',
        reserved_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        expires_at DATETIME NULL,
        issued_at DATETIME NULL,
        completed_at DATETIME NULL,
        cancelled_at DATETIME NULL,
        cancel_reason VARCHAR(500) NULL,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_material_allocations_no (allocation_no),
        KEY idx_material_allocations_wo (work_order_id),
        KEY idx_material_allocations_lot (lot_id, status),
        KEY idx_material_allocations_status (status, expires_at),
        CONSTRAINT fk_ma_work_order FOREIGN KEY (work_order_id) REFERENCES production_plan_details(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_ma_requirement FOREIGN KEY (requirement_id) REFERENCES material_requirements(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_ma_material FOREIGN KEY (material_id) REFERENCES materials(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_ma_lot FOREIGN KEY (lot_id) REFERENCES material_lots(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_ma_location FOREIGN KEY (location_id) REFERENCES material_locations(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_ma_machine FOREIGN KEY (machine_id) REFERENCES equipment(id) ON UPDATE RESTRICT ON DELETE RESTRICT
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
        print("Material allocation table created (or already existed).")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
