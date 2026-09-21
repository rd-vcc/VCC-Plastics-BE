"""One-off DDL to create the Material Master dynamic-field tables.

Run once against the dev database:
    python scripts/create_material_field_tables.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.database import get_vcc_plastics_connection  # noqa: E402

DDL = [
    """
    CREATE TABLE IF NOT EXISTS material_field_definitions (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        field_code VARCHAR(50) NOT NULL UNIQUE,
        field_name VARCHAR(150) NOT NULL,
        data_type VARCHAR(20) NOT NULL DEFAULT 'TEXT',
        unit VARCHAR(50) NULL,
        is_required TINYINT(1) NOT NULL DEFAULT 0,
        is_active TINYINT(1) NOT NULL DEFAULT 1,
        sort_order INT NOT NULL DEFAULT 0,
        description VARCHAR(500) NULL,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
    """
    CREATE TABLE IF NOT EXISTS material_field_values (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        material_id BIGINT UNSIGNED NOT NULL,
        field_definition_id BIGINT UNSIGNED NOT NULL,
        value_text VARCHAR(1000) NULL,
        value_integer BIGINT NULL,
        value_decimal DECIMAL(18,4) NULL,
        value_boolean TINYINT(1) NULL,
        value_date DATE NULL,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        UNIQUE KEY uq_material_field (material_id, field_definition_id),
        CONSTRAINT fk_mfv_material FOREIGN KEY (material_id) REFERENCES materials(id) ON DELETE CASCADE,
        CONSTRAINT fk_mfv_field FOREIGN KEY (field_definition_id) REFERENCES material_field_definitions(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


def main() -> None:
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        for statement in DDL:
            cur.execute(statement)
        conn.commit()
        print("Material field tables ready.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
