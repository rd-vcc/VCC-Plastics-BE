"""baseline: capture the already-live schema as the Alembic starting point

This embeds the exact raw SQL that previously lived in
``database/schema.sql``, ``database/product_master_schema.sql`` and
``database/mold_master_schema.sql`` (before that directory was removed once
its content was safely captured here - see migrations/README.md).

Revision ID: 0001
Revises:
Create Date: 2026-09-17

IMPORTANT: do not run `alembic upgrade head` against the existing dev
database - it already has these tables and real data. See
migrations/README.md: review this file, then `alembic stamp head`.
"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ---------------------------------------------------------------------------
# Verbatim contents of database/schema.sql
# ---------------------------------------------------------------------------
SCHEMA_SQL = """
    CREATE DATABASE IF NOT EXISTS vcc_plastics
CHARACTER SET utf8mb4
COLLATE utf8mb4_unicode_ci;

USE vcc_plastics;

CREATE TABLE IF NOT EXISTS mes_users (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    external_user_id BIGINT UNSIGNED NULL,
    employee_code VARCHAR(50) NOT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    last_login_at DATETIME NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(50) NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    updated_by VARCHAR(50) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_mes_users_employee_code (employee_code),
    KEY idx_mes_users_external_user_id (external_user_id),
    KEY idx_mes_users_is_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS roles (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    role_code VARCHAR(100) NOT NULL,
    role_name VARCHAR(255) NOT NULL,
    description VARCHAR(500) NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(50) NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    updated_by VARCHAR(50) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_roles_role_code (role_code),
    KEY idx_roles_is_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS permissions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    permission_code VARCHAR(150) NOT NULL,
    permission_name VARCHAR(255) NOT NULL,
    module_code VARCHAR(100) NULL,
    description VARCHAR(500) NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(50) NULL,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    updated_by VARCHAR(50) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_permissions_permission_code (permission_code),
    KEY idx_permissions_module_code (module_code),
    KEY idx_permissions_is_active (is_active)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS user_roles (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    user_id BIGINT UNSIGNED NOT NULL,
    role_id BIGINT UNSIGNED NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(50) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_user_roles_user_role (user_id, role_id),
    KEY idx_user_roles_user_id (user_id),
    KEY idx_user_roles_role_id (role_id),
    CONSTRAINT fk_user_roles_user FOREIGN KEY (user_id) REFERENCES mes_users(id) ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT fk_user_roles_role FOREIGN KEY (role_id) REFERENCES roles(id) ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS role_permissions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    role_id BIGINT UNSIGNED NOT NULL,
    permission_id BIGINT UNSIGNED NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by VARCHAR(50) NULL,
    PRIMARY KEY (id),
    UNIQUE KEY uq_role_permissions_role_permission (role_id, permission_id),
    KEY idx_role_permissions_role_id (role_id),
    KEY idx_role_permissions_permission_id (permission_id),
    CONSTRAINT fk_role_permissions_role FOREIGN KEY (role_id) REFERENCES roles(id) ON UPDATE CASCADE ON DELETE CASCADE,
    CONSTRAINT fk_role_permissions_permission FOREIGN KEY (permission_id) REFERENCES permissions(id) ON UPDATE CASCADE ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
"""


# ---------------------------------------------------------------------------
# Verbatim contents of database/product_master_schema.sql
# ---------------------------------------------------------------------------
PRODUCT_MASTER_SQL = """
USE `vcc_plastics`;

SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS product_families (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    family_code VARCHAR(100) NOT NULL,
    family_name VARCHAR(255) NOT NULL,
    parent_id BIGINT UNSIGNED NULL,
    sort_order INT NOT NULL DEFAULT 0,
    description TEXT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    created_by VARCHAR(100) NULL,
    updated_by VARCHAR(100) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_product_families_code (family_code),
    KEY idx_product_families_parent_sort (parent_id, sort_order, id),
    KEY idx_product_families_status (status),
    CONSTRAINT fk_product_families_parent FOREIGN KEY (parent_id)
        REFERENCES product_families (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT chk_product_families_status CHECK (status IN ('ACTIVE', 'INACTIVE'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS products (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    product_code VARCHAR(100) NOT NULL,
    product_name VARCHAR(255) NOT NULL,
    product_family_id BIGINT UNSIGNED NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    created_by VARCHAR(100) NULL,
    updated_by VARCHAR(100) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_products_code (product_code),
    KEY idx_products_name (product_name),
    KEY idx_products_family_status (product_family_id, status),
    KEY idx_products_created_at (created_at),
    CONSTRAINT fk_products_family FOREIGN KEY (product_family_id)
        REFERENCES product_families (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT chk_products_status CHECK (status IN ('ACTIVE', 'INACTIVE'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS product_field_definitions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    field_code VARCHAR(100) NOT NULL,
    field_name VARCHAR(255) NOT NULL,
    data_type VARCHAR(30) NOT NULL,
    field_group VARCHAR(100) NULL DEFAULT 'GENERAL',
    description TEXT NULL,
    placeholder VARCHAR(255) NULL,
    default_value TEXT NULL,
    unit_label VARCHAR(50) NULL,
    validation_rules JSON NULL,
    is_required TINYINT(1) NOT NULL DEFAULT 0,
    is_unique TINYINT(1) NOT NULL DEFAULT 0,
    is_filterable TINYINT(1) NOT NULL DEFAULT 0,
    is_list_visible TINYINT(1) NOT NULL DEFAULT 0,
    applies_to_all_families TINYINT(1) NOT NULL DEFAULT 1,
    is_system TINYINT(1) NOT NULL DEFAULT 0,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0,
    created_by VARCHAR(100) NULL,
    updated_by VARCHAR(100) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_product_field_definitions_code (field_code),
    KEY idx_product_fields_active_sort (is_active, sort_order, id),
    KEY idx_product_fields_group (field_group, sort_order),
    CONSTRAINT chk_product_fields_data_type CHECK (data_type IN (
        'TEXT','LONG_TEXT','INTEGER','DECIMAL','DATE','DATETIME','BOOLEAN',
        'SELECT','MULTI_SELECT','FILE','IMAGE'
    ))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS product_field_options (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    field_definition_id BIGINT UNSIGNED NOT NULL,
    option_value VARCHAR(255) NOT NULL,
    option_label VARCHAR(255) NOT NULL,
    color VARCHAR(30) NULL,
    sort_order INT NOT NULL DEFAULT 0,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_product_field_option_value (field_definition_id, option_value),
    KEY idx_product_field_options_sort (field_definition_id, is_active, sort_order, id),
    CONSTRAINT fk_product_field_options_definition FOREIGN KEY (field_definition_id)
        REFERENCES product_field_definitions (id) ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS product_family_fields (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    product_family_id BIGINT UNSIGNED NOT NULL,
    field_definition_id BIGINT UNSIGNED NOT NULL,
    include_descendants TINYINT(1) NOT NULL DEFAULT 1,
    is_required_override TINYINT(1) NULL,
    sort_order_override INT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_product_family_field (product_family_id, field_definition_id),
    KEY idx_product_family_fields_field (field_definition_id),
    CONSTRAINT fk_product_family_fields_family FOREIGN KEY (product_family_id)
        REFERENCES product_families (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_product_family_fields_definition FOREIGN KEY (field_definition_id)
        REFERENCES product_field_definitions (id) ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS product_field_values (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    product_id BIGINT UNSIGNED NOT NULL,
    field_definition_id BIGINT UNSIGNED NOT NULL,
    value_text LONGTEXT NULL,
    value_integer BIGINT NULL,
    value_decimal DECIMAL(20,6) NULL,
    value_date DATE NULL,
    value_datetime DATETIME NULL,
    value_boolean TINYINT(1) NULL,
    value_json JSON NULL,
    created_by VARCHAR(100) NULL,
    updated_by VARCHAR(100) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_product_field_value (product_id, field_definition_id),
    KEY idx_product_field_values_field (field_definition_id),
    KEY idx_product_field_values_integer (field_definition_id, value_integer),
    KEY idx_product_field_values_decimal (field_definition_id, value_decimal),
    KEY idx_product_field_values_date (field_definition_id, value_date),
    KEY idx_product_field_values_datetime (field_definition_id, value_datetime),
    KEY idx_product_field_values_boolean (field_definition_id, value_boolean),
    CONSTRAINT fk_product_field_values_product FOREIGN KEY (product_id)
        REFERENCES products (id) ON UPDATE RESTRICT ON DELETE CASCADE,
    CONSTRAINT fk_product_field_values_definition FOREIGN KEY (field_definition_id)
        REFERENCES product_field_definitions (id) ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS product_versions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    product_id BIGINT UNSIGNED NOT NULL,
    revision_code VARCHAR(50) NOT NULL,
    effective_from DATE NULL,
    effective_to DATE NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    description TEXT NULL,
    created_by VARCHAR(100) NULL,
    updated_by VARCHAR(100) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uq_product_revision (product_id, revision_code),
    KEY idx_product_versions_status (product_id, status, effective_from),
    CONSTRAINT fk_product_versions_product FOREIGN KEY (product_id)
        REFERENCES products (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT chk_product_versions_status CHECK (status IN ('ACTIVE', 'INACTIVE')),
    CONSTRAINT chk_product_versions_dates CHECK (
        effective_to IS NULL OR effective_from IS NULL OR effective_to >= effective_from
    )
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO product_field_definitions (
    field_code, field_name, data_type, field_group, placeholder, unit_label,
    is_required, is_filterable, is_list_visible,
    applies_to_all_families, is_system, is_active, sort_order
) VALUES
('customer','Customer','TEXT','GENERAL','Enter customer name',NULL,0,1,1,1,0,1,10),
('drawing_no','Drawing No.','TEXT','SPECIFICATION','Enter drawing number',NULL,0,1,0,1,0,1,20),
('revision','Revision','TEXT','SPECIFICATION','Example: A, B, C',NULL,0,1,1,1,0,1,30),
('material','Material','TEXT','MATERIAL','Example: PBT-GF30',NULL,0,1,1,1,0,1,40),
('material_grade','Material Grade','TEXT','MATERIAL','Enter commercial material grade',NULL,0,1,0,1,0,1,50),
('color','Color','TEXT','MATERIAL','Enter product color',NULL,0,1,0,1,0,1,60),
('unit_weight','Unit Weight','DECIMAL','SPECIFICATION','Enter unit weight','g',0,0,0,1,0,1,70),
('unit','Unit','SELECT','GENERAL','Select unit',NULL,1,1,0,1,0,1,80),
('reference_cycle_time','Reference Cycle Time','DECIMAL','SPECIFICATION','Enter reference cycle time','second',0,0,0,1,0,1,90),
('product_image','Product Image','IMAGE','GENERAL',NULL,NULL,0,0,0,1,0,1,100),
('description','Description','LONG_TEXT','GENERAL','Enter product description',NULL,0,0,0,1,0,1,110)
ON DUPLICATE KEY UPDATE field_name=VALUES(field_name);

INSERT INTO product_field_options (field_definition_id, option_value, option_label, sort_order)
SELECT id, 'PCS', 'PCS', 10 FROM product_field_definitions WHERE field_code='unit'
ON DUPLICATE KEY UPDATE option_label=VALUES(option_label);

INSERT INTO product_field_options (field_definition_id, option_value, option_label, sort_order)
SELECT id, 'SET', 'SET', 20 FROM product_field_definitions WHERE field_code='unit'
ON DUPLICATE KEY UPDATE option_label=VALUES(option_label);
"""


# ---------------------------------------------------------------------------
# Verbatim contents of database/mold_master_schema.sql
# ---------------------------------------------------------------------------
MOLD_MASTER_SQL = """
CREATE DATABASE IF NOT EXISTS `vcc_plastics`
    CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE `vcc_plastics`;
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS mold_location_node_types (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    type_code VARCHAR(50) NOT NULL,
    type_name VARCHAR(150) NOT NULL,
    level_order INT UNSIGNED NOT NULL DEFAULT 1,
    icon VARCHAR(100) NULL,
    color VARCHAR(20) NULL,
    description VARCHAR(500) NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0,
    created_by VARCHAR(100) NULL,
    updated_by VARCHAR(100) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_mold_location_node_types_code (type_code),
    KEY idx_mold_location_node_types_active_sort (is_active, level_order, sort_order),
    CONSTRAINT chk_mold_location_node_types_level CHECK (level_order > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS mold_location_nodes (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    node_code VARCHAR(50) NOT NULL,
    node_name VARCHAR(200) NOT NULL,
    node_type_id BIGINT UNSIGNED NOT NULL,
    parent_id BIGINT UNSIGNED NULL,
    level_no INT UNSIGNED NOT NULL DEFAULT 1,
    sort_order INT NOT NULL DEFAULT 0,
    description VARCHAR(500) NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    created_by VARCHAR(100) NULL,
    updated_by VARCHAR(100) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_mold_location_nodes_code (node_code),
    KEY idx_mold_location_nodes_parent (parent_id),
    KEY idx_mold_location_nodes_type (node_type_id),
    KEY idx_mold_location_nodes_tree (parent_id, node_type_id, sort_order),
    KEY idx_mold_location_nodes_status (status),
    CONSTRAINT fk_mold_location_nodes_type FOREIGN KEY (node_type_id)
        REFERENCES mold_location_node_types(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_mold_location_nodes_parent FOREIGN KEY (parent_id)
        REFERENCES mold_location_nodes(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT chk_mold_location_nodes_level CHECK (level_no > 0),
    CONSTRAINT chk_mold_location_nodes_status CHECK (status IN ('ACTIVE','INACTIVE'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS molds (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    mold_code VARCHAR(50) NOT NULL,
    mold_name VARCHAR(255) NOT NULL,
    mold_type VARCHAR(100) NULL,
    mold_group VARCHAR(100) NULL,
    cavity_count INT UNSIGNED NOT NULL DEFAULT 1,
    status VARCHAR(30) NOT NULL DEFAULT 'AVAILABLE',
    current_location_id BIGINT UNSIGNED NULL,
    design_shot BIGINT UNSIGNED NOT NULL DEFAULT 0,
    current_shot BIGINT UNSIGNED NOT NULL DEFAULT 0,
    weight_kg DECIMAL(12,3) NULL,
    length_mm DECIMAL(12,2) NULL,
    width_mm DECIMAL(12,2) NULL,
    height_mm DECIMAL(12,2) NULL,
    manufacturer VARCHAR(255) NULL,
    manufacture_date DATE NULL,
    image_url VARCHAR(1000) NULL,
    description TEXT NULL,
    remark TEXT NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_by VARCHAR(100) NULL,
    updated_by VARCHAR(100) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_molds_code (mold_code),
    KEY idx_molds_name (mold_name),
    KEY idx_molds_type (mold_type),
    KEY idx_molds_group (mold_group),
    KEY idx_molds_status (status),
    KEY idx_molds_location (current_location_id),
    KEY idx_molds_active_status (is_active, status),
    CONSTRAINT fk_molds_current_location FOREIGN KEY (current_location_id)
        REFERENCES mold_location_nodes(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT chk_molds_cavity CHECK (cavity_count > 0),
    CONSTRAINT chk_molds_dimensions CHECK (
        (weight_kg IS NULL OR weight_kg >= 0)
        AND (length_mm IS NULL OR length_mm >= 0)
        AND (width_mm IS NULL OR width_mm >= 0)
        AND (height_mm IS NULL OR height_mm >= 0)
    ),
    CONSTRAINT chk_molds_status CHECK (status IN (
        'AVAILABLE','IN_PRODUCTION','IN_MAINTENANCE','IN_REPAIR','RETIRED','SCRAPPED'
    ))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS mold_attribute_definitions (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    attribute_code VARCHAR(100) NOT NULL,
    attribute_name VARCHAR(255) NOT NULL,
    data_type VARCHAR(30) NOT NULL,
    unit VARCHAR(50) NULL,
    default_value TEXT NULL,
    select_options JSON NULL,
    is_required TINYINT(1) NOT NULL DEFAULT 0,
    is_searchable TINYINT(1) NOT NULL DEFAULT 0,
    is_filterable TINYINT(1) NOT NULL DEFAULT 0,
    show_in_list TINYINT(1) NOT NULL DEFAULT 0,
    sort_order INT NOT NULL DEFAULT 0,
    validation_rules JSON NULL,
    description VARCHAR(500) NULL,
    is_active TINYINT(1) NOT NULL DEFAULT 1,
    created_by VARCHAR(100) NULL,
    updated_by VARCHAR(100) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_mold_attribute_definitions_code (attribute_code),
    KEY idx_mold_attribute_definitions_display (is_active, show_in_list, sort_order),
    CONSTRAINT chk_mold_attribute_definitions_type CHECK (data_type IN (
        'TEXT','LONG_TEXT','INTEGER','DECIMAL','BOOLEAN','DATE','DATETIME','SELECT','MULTI_SELECT'
    ))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS mold_attribute_values (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    mold_id BIGINT UNSIGNED NOT NULL,
    attribute_definition_id BIGINT UNSIGNED NOT NULL,
    value_text LONGTEXT NULL,
    value_number DECIMAL(24,6) NULL,
    value_boolean TINYINT(1) NULL,
    value_date DATE NULL,
    value_datetime DATETIME NULL,
    value_json JSON NULL,
    created_by VARCHAR(100) NULL,
    updated_by VARCHAR(100) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_mold_attribute_values (mold_id, attribute_definition_id),
    KEY idx_mold_attribute_values_definition (attribute_definition_id),
    KEY idx_mold_attribute_values_number (attribute_definition_id, value_number),
    KEY idx_mold_attribute_values_date (attribute_definition_id, value_date),
    CONSTRAINT fk_mold_attribute_values_mold FOREIGN KEY (mold_id)
        REFERENCES molds(id) ON UPDATE RESTRICT ON DELETE CASCADE,
    CONSTRAINT fk_mold_attribute_values_definition FOREIGN KEY (attribute_definition_id)
        REFERENCES mold_attribute_definitions(id) ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS mold_products (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    mold_id BIGINT UNSIGNED NOT NULL,
    product_id BIGINT UNSIGNED NOT NULL,
    cavity_quantity INT UNSIGNED NULL,
    is_primary TINYINT(1) NOT NULL DEFAULT 0,
    valid_from DATE NULL,
    valid_to DATE NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVE',
    remark VARCHAR(500) NULL,
    created_by VARCHAR(100) NULL,
    updated_by VARCHAR(100) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_mold_products_mold_product (mold_id, product_id),
    KEY idx_mold_products_product (product_id),
    KEY idx_mold_products_status (status),
    KEY idx_mold_products_primary (mold_id, is_primary),
    CONSTRAINT fk_mold_products_mold FOREIGN KEY (mold_id)
        REFERENCES molds(id) ON UPDATE RESTRICT ON DELETE CASCADE,
    CONSTRAINT fk_mold_products_product FOREIGN KEY (product_id)
        REFERENCES products(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT chk_mold_products_dates CHECK (
        valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from
    ),
    CONSTRAINT chk_mold_products_status CHECK (status IN ('ACTIVE','INACTIVE'))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT INTO mold_location_node_types
    (type_code,type_name,level_order,icon,color,sort_order)
VALUES
    ('COMPANY','Công ty',1,'business','#005BAB',1),
    ('FACTORY','Nhà máy',2,'factory','#2563EB',2),
    ('AREA','Khu vực',3,'domain','#0891B2',3),
    ('WORKSHOP','Xưởng',4,'warehouse','#059669',4),
    ('LINE','Dây chuyền',5,'account_tree','#65A30D',5),
    ('TOOL_ROOM','Phòng khuôn',4,'construction','#7C3AED',6),
    ('RACK','Giá để khuôn',5,'shelves','#D97706',7),
    ('POSITION','Vị trí đặt khuôn',6,'location_on','#DC2626',8)
ON DUPLICATE KEY UPDATE
    type_name=VALUES(type_name), level_order=VALUES(level_order),
    icon=VALUES(icon), color=VALUES(color), sort_order=VALUES(sort_order);
"""


def _split_statements(sql: str) -> list[str]:
    """Split a .sql file's text into individual statements on top-level semicolons.

    Good enough for these three files (no stored procedures / DELIMITER
    tricks), and lets each statement be sent as its own ``op.execute()``
    call, which is friendlier to DB-API drivers that don't support multiple
    statements per execute.
    """
    statements = []
    for raw_statement in sql.split(";"):
        statement = raw_statement.strip()
        if statement:
            statements.append(statement)
    return statements


def upgrade() -> None:
    for sql_text in (SCHEMA_SQL, PRODUCT_MASTER_SQL, MOLD_MASTER_SQL):
        for statement in _split_statements(sql_text):
            op.execute(statement)


def downgrade() -> None:
    # This is the baseline revision - downgrading would mean dropping the
    # live schema, which we never want an automated tool to do. Intentionally
    # a no-op; drop tables manually and deliberately if that's ever needed.
    pass
