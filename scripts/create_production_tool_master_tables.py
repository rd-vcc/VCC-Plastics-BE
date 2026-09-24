"""One-off script: create the Production Tool Master tables in the live dev database.

Mirrors scripts/create_material_master_tables.py + create_material_field_tables.py
(raw mysql.connector via app.core.database.get_vcc_plastics_connection()).

Run with:

    python -m scripts.create_production_tool_master_tables
"""

from app.core.database import get_vcc_plastics_connection

STATEMENTS = [
    """
    CREATE TABLE IF NOT EXISTS production_tool_node_types (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        code VARCHAR(50) NOT NULL,
        name VARCHAR(150) NOT NULL,
        parent_type_id BIGINT UNSIGNED NULL,
        level_order INT UNSIGNED NOT NULL DEFAULT 1,
        color VARCHAR(20) NULL,
        icon VARCHAR(100) NULL,
        sort_order INT NOT NULL DEFAULT 0,
        is_active TINYINT(1) NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_production_tool_node_types_code (code),
        KEY idx_production_tool_node_types_parent (parent_type_id),
        KEY idx_production_tool_node_types_active_sort (is_active, level_order, sort_order),
        CONSTRAINT fk_production_tool_node_types_parent FOREIGN KEY (parent_type_id)
            REFERENCES production_tool_node_types(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT chk_production_tool_node_types_level CHECK (level_order > 0)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS production_tool_nodes (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        code VARCHAR(50) NOT NULL,
        name VARCHAR(200) NOT NULL,
        node_type_id BIGINT UNSIGNED NOT NULL,
        parent_id BIGINT UNSIGNED NULL,
        level_no INT NOT NULL DEFAULT 0,
        sort_order INT NOT NULL DEFAULT 0,
        description VARCHAR(500) NULL,
        status ENUM('ACTIVE','INACTIVE') NOT NULL DEFAULT 'ACTIVE',
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_production_tool_nodes_code (code),
        KEY idx_production_tool_nodes_parent (parent_id),
        KEY idx_production_tool_nodes_type (node_type_id),
        KEY idx_production_tool_nodes_tree (parent_id, node_type_id, sort_order),
        KEY idx_production_tool_nodes_status (status),
        CONSTRAINT fk_production_tool_nodes_type FOREIGN KEY (node_type_id)
            REFERENCES production_tool_node_types(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
        CONSTRAINT fk_production_tool_nodes_parent FOREIGN KEY (parent_id)
            REFERENCES production_tool_nodes(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS production_tools (
        id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
        tool_code VARCHAR(50) NOT NULL,
        tool_name VARCHAR(200) NOT NULL,
        production_tool_node_id BIGINT UNSIGNED NULL,
        tool_type ENUM('CUTTING','ASSEMBLY','MEASURING','HANDLING') NOT NULL,
        tool_category VARCHAR(100) NULL,
        manufacturer VARCHAR(150) NULL,
        model VARCHAR(100) NULL,
        serial_number VARCHAR(100) NULL,
        assigned_process VARCHAR(200) NULL,
        status ENUM('IN_USE','AVAILABLE','IN_MAINTENANCE','OUT_OF_SERVICE','INACTIVE') NOT NULL DEFAULT 'AVAILABLE',
        calibration_required TINYINT(1) NOT NULL DEFAULT 0,
        calibration_due_date DATE NULL,
        last_calibration_date DATE NULL,
        calibration_interval_days INT NULL,
        measurement_range VARCHAR(100) NULL,
        measurement_resolution VARCHAR(100) NULL,
        description TEXT NULL,
        version INT NOT NULL DEFAULT 1,
        created_by VARCHAR(100) NULL,
        updated_by VARCHAR(100) NULL,
        created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
        PRIMARY KEY (id),
        UNIQUE KEY uk_production_tools_code (tool_code),
        KEY idx_production_tools_name (tool_name),
        KEY idx_production_tools_node (production_tool_node_id),
        KEY idx_production_tools_type (tool_type),
        KEY idx_production_tools_status (status),
        KEY idx_production_tools_manufacturer (manufacturer),
        CONSTRAINT fk_production_tools_node FOREIGN KEY (production_tool_node_id)
            REFERENCES production_tool_nodes(id) ON UPDATE RESTRICT ON DELETE RESTRICT
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """,
    """
    CREATE TABLE IF NOT EXISTS production_tool_field_definitions (
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
    CREATE TABLE IF NOT EXISTS production_tool_field_values (
        id BIGINT UNSIGNED AUTO_INCREMENT PRIMARY KEY,
        tool_id BIGINT UNSIGNED NOT NULL,
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
        UNIQUE KEY uq_production_tool_field (tool_id, field_definition_id),
        CONSTRAINT fk_ptfv_tool FOREIGN KEY (tool_id) REFERENCES production_tools(id) ON DELETE CASCADE,
        CONSTRAINT fk_ptfv_field FOREIGN KEY (field_definition_id) REFERENCES production_tool_field_definitions(id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
    """,
]


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        for statement in STATEMENTS:
            cur.execute(statement)
        conn.commit()
        print("Production Tool Master tables created (or already existed).")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
