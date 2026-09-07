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
