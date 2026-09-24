"""One-off script: Material Management step 1 (local inventory mode, no WMS yet).

The plant has no WMS, so MES keeps lots and stock itself ("local inventory mode").
All stock access goes through app.modules.material.inventory.service so a WMS
adapter can replace it later without touching screens or calculations.

- material_locations: warehouses, silos, dryers, machines, IQC / Hold areas.
- material_lots: one row per lot; current quantity, location and status.
- material_lot_transactions: append-only history of every lot movement.
- material_stock_settings: safety stock per material.
- system_settings: default thresholds agreed on 23/09/2026.
- Migrates the temporary planning table material_inventory_balances into
  opening lots, then leaves that table unused.

Run with:

    python -m scripts.create_material_inventory_tables
"""

from datetime import date

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS material_locations (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        location_code VARCHAR(50) NOT NULL,
        location_name VARCHAR(200) NOT NULL,
        location_type ENUM('WAREHOUSE','SILO','DRYER','MACHINE','IQC_AREA','HOLD_AREA','OTHER') NOT NULL,
        equipment_id INT UNSIGNED NULL,
        factory_node_id BIGINT UNSIGNED NULL,
        capacity_kg DECIMAL(14,3) NULL,
        sort_order INT NOT NULL DEFAULT 0,
        status ENUM('ACTIVE','INACTIVE') NOT NULL DEFAULT 'ACTIVE',
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_material_locations_code (location_code),
        CONSTRAINT fk_material_locations_equipment FOREIGN KEY (equipment_id)
            REFERENCES equipment(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_material_locations_factory_node FOREIGN KEY (factory_node_id)
            REFERENCES factory_structure_nodes(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS material_lots (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        lot_no VARCHAR(50) NOT NULL,
        material_id BIGINT UNSIGNED NOT NULL,
        source ENUM('RECEIVING','REGRIND','OPENING','RETURN') NOT NULL DEFAULT 'RECEIVING',
        supplier VARCHAR(200) NULL,
        supplier_lot_no VARCHAR(100) NULL,
        source_work_order_id BIGINT UNSIGNED NULL,
        received_at DATETIME NOT NULL,
        manufacture_date DATE NULL,
        expiry_date DATE NULL,
        initial_qty DECIMAL(14,3) NOT NULL,
        current_qty DECIMAL(14,3) NOT NULL,
        reserved_qty DECIMAL(14,3) NOT NULL DEFAULT 0,
        location_id BIGINT UNSIGNED NOT NULL,
        status ENUM('WAITING_IQC','IN_STOCK','IN_USE','HOLD','CONSUMED','CLOSED') NOT NULL DEFAULT 'WAITING_IQC',
        iqc_result ENUM('PENDING','PASS','FAIL','NOT_REQUIRED') NOT NULL DEFAULT 'PENDING',
        iqc_by VARCHAR(100) NULL,
        iqc_at DATETIME NULL,
        hold_reason VARCHAR(500) NULL,
        remark VARCHAR(500) NULL,
        version INT UNSIGNED NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_material_lots_no (lot_no),
        KEY idx_material_lots_material (material_id, status),
        KEY idx_material_lots_location (location_id),
        KEY idx_material_lots_expiry (expiry_date),
        CONSTRAINT fk_material_lots_material FOREIGN KEY (material_id)
            REFERENCES materials(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_material_lots_location FOREIGN KEY (location_id)
            REFERENCES material_locations(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_material_lots_source_wo FOREIGN KEY (source_work_order_id)
            REFERENCES production_plan_details(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS material_lot_transactions (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        lot_id BIGINT UNSIGNED NOT NULL,
        txn_type ENUM('RECEIVING','OPENING','REGRIND_CREATE','IQC_PASS','IQC_FAIL','TRANSFER','HOLD','RELEASE',
            'ADJUSTMENT','RESERVATION','RESERVATION_RELEASE','ISSUE_TO_MACHINE','CONSUMPTION','RETURN','CLOSE') NOT NULL,
        qty_change DECIMAL(14,3) NOT NULL DEFAULT 0,
        qty_after DECIMAL(14,3) NOT NULL,
        from_location_id BIGINT UNSIGNED NULL,
        to_location_id BIGINT UNSIGNED NULL,
        status_after VARCHAR(30) NOT NULL,
        work_order_id BIGINT UNSIGNED NULL,
        machine_id INT UNSIGNED NULL,
        reference_no VARCHAR(100) NULL,
        actor VARCHAR(100) NOT NULL,
        acted_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        remark VARCHAR(500) NULL,
        PRIMARY KEY (id),
        KEY idx_material_lot_transactions_lot (lot_id, acted_at),
        KEY idx_material_lot_transactions_time (acted_at),
        KEY idx_material_lot_transactions_wo (work_order_id),
        CONSTRAINT fk_mlt_lot FOREIGN KEY (lot_id)
            REFERENCES material_lots(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS material_stock_settings (
        material_id BIGINT UNSIGNED NOT NULL,
        safety_stock_qty DECIMAL(14,3) NOT NULL DEFAULT 0,
        updated_by VARCHAR(100) NULL,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (material_id),
        CONSTRAINT fk_material_stock_settings_material FOREIGN KEY (material_id)
            REFERENCES materials(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
]

# code, name, type, equipment code (for machine locations), sort
LOCATIONS = [
    ("WH-RM-A", "Kho nguyên liệu A", "WAREHOUSE", None, 10),
    ("WH-RM-B", "Kho nguyên liệu B", "WAREHOUSE", None, 20),
    ("WH-ADD", "Kho phụ gia", "WAREHOUSE", None, 30),
    ("SILO-01", "Silo 1", "SILO", None, 40),
    ("SILO-02", "Silo 2", "SILO", None, 50),
    ("DRYER-01", "Máy sấy 1", "DRYER", None, 60),
    ("IQC", "Khu kiểm tra IQC", "IQC_AREA", None, 70),
    ("HOLD", "Khu Hold", "HOLD_AREA", None, 80),
]

SETTINGS = {
    "material.critical_coverage_pct": "50",
    "material.critical_hours": "24",
    "material.reservation_hours": "24",
    "material.usage_tolerance_pct": "3",
    "material.expiry_warning_days": "30",
}


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)
    try:
        for statement in STATEMENTS:
            cur.execute(statement)

        for code, name, kind, _equipment, sort in LOCATIONS:
            cur.execute(
                """INSERT IGNORE INTO material_locations (location_code, location_name, location_type, sort_order, created_by)
                   VALUES (%s,%s,%s,%s,'SYSTEM')""", (code, name, kind, sort))
        # one MACHINE location per active injection machine
        cur.execute(
            """SELECT e.id, e.equipment_code, e.equipment_name, e.factory_node_id FROM equipment e
               JOIN equipment_types t ON t.id = e.equipment_type_id
               WHERE t.category='MACHINE' AND e.asset_status='ACTIVE' ORDER BY e.equipment_code""")
        for index, machine in enumerate(cur.fetchall()):
            cur.execute(
                """INSERT IGNORE INTO material_locations
                   (location_code, location_name, location_type, equipment_id, factory_node_id, sort_order, created_by)
                   VALUES (%s,%s,'MACHINE',%s,%s,%s,'SYSTEM')""",
                (f"MC-{machine['equipment_code']}", f"Máy ép {machine['equipment_code']}", machine["id"],
                 machine["factory_node_id"], 100 + index))

        for key, value in SETTINGS.items():
            cur.execute("SELECT 1 FROM system_settings WHERE setting_key=%s", (key,))
            if not cur.fetchone():
                cur.execute("INSERT INTO system_settings (setting_key, setting_value) VALUES (%s,%s)", (key, value))

        # migrate the temporary planning stock table into opening lots (once)
        cur.execute("SHOW TABLES LIKE 'material_inventory_balances'")
        if cur.fetchone():
            cur.execute("SELECT id FROM material_locations WHERE location_code='WH-RM-A'")
            warehouse = cur.fetchone()["id"]
            cur.execute("SELECT * FROM material_inventory_balances")
            for row in cur.fetchall():
                cur.execute(
                    """INSERT INTO material_stock_settings (material_id, safety_stock_qty, updated_by) VALUES (%s,%s,%s)
                       ON DUPLICATE KEY UPDATE safety_stock_qty=safety_stock_qty""",
                    (row["material_id"], row["safety_stock_qty"], "MIGRATION"))
                lot_no = f"OPEN-{row['material_id']:05d}"
                cur.execute("SELECT id FROM material_lots WHERE lot_no=%s", (lot_no,))
                if cur.fetchone() or float(row["available_qty"]) <= 0:
                    continue
                cur.execute(
                    """INSERT INTO material_lots (lot_no, material_id, source, received_at, initial_qty, current_qty, location_id,
                       status, iqc_result, remark, created_by)
                       VALUES (%s,%s,'OPENING',NOW(),%s,%s,%s,'IN_STOCK','NOT_REQUIRED','Opening balance',%s)""",
                    (lot_no, row["material_id"], row["available_qty"], row["available_qty"], warehouse, "MIGRATION"))
                cur.execute(
                    """INSERT INTO material_lot_transactions (lot_id, txn_type, qty_change, qty_after, to_location_id,
                       status_after, actor, remark) VALUES (%s,'OPENING',%s,%s,%s,'IN_STOCK','MIGRATION','Opening balance')""",
                    (cur.lastrowid, row["available_qty"], row["available_qty"], warehouse))
        conn.commit()
        print(f"Material inventory tables ready ({date.today()}).")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
