-- Material Master schema
--
-- Mirrors the shape of mold_master_schema.sql (captured in
-- migrations/versions/0001_baseline.py) for the Material Master module:
-- a 2-level node-type/node hierarchy (material_node_types, material_nodes)
-- plus the material catalog itself (materials), which supports optimistic
-- locking via `version` exactly like `equipment.version`.
--
-- This file is documentation/reference only. It is not executed directly by
-- the application; the tables were created against the live dev database via
-- a one-off script (scripts/create_material_master_tables.py) using
-- app.core.database.get_vcc_plastics_connection(), per this project's
-- migrations/README.md convention (Alembic revisions are scaffolding/
-- reference only and are not run with `alembic upgrade` against the live dev
-- DB). The equivalent CREATE TABLE statements are also embedded, verbatim,
-- in migrations/versions/0002_material_master.py for future-baseline parity.

USE `vcc_plastics`;
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS material_node_types (
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
    UNIQUE KEY uk_material_node_types_code (code),
    KEY idx_material_node_types_parent (parent_type_id),
    KEY idx_material_node_types_active_sort (is_active, level_order, sort_order),
    CONSTRAINT fk_material_node_types_parent FOREIGN KEY (parent_type_id)
        REFERENCES material_node_types(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT chk_material_node_types_level CHECK (level_order > 0)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS material_nodes (
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
    UNIQUE KEY uk_material_nodes_code (code),
    KEY idx_material_nodes_parent (parent_id),
    KEY idx_material_nodes_type (node_type_id),
    KEY idx_material_nodes_tree (parent_id, node_type_id, sort_order),
    KEY idx_material_nodes_status (status),
    CONSTRAINT fk_material_nodes_type FOREIGN KEY (node_type_id)
        REFERENCES material_node_types(id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT fk_material_nodes_parent FOREIGN KEY (parent_id)
        REFERENCES material_nodes(id) ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS materials (
    id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT,
    material_code VARCHAR(50) NOT NULL,
    material_name VARCHAR(200) NOT NULL,
    material_node_id BIGINT UNSIGNED NULL,
    material_type ENUM('RAW_MATERIAL','COMPOUND','ADDITIVE','RECYCLED','PACKAGING') NOT NULL,
    base_resin VARCHAR(100) NULL,
    material_group VARCHAR(100) NULL,
    supplier VARCHAR(200) NULL,
    unit VARCHAR(20) NOT NULL DEFAULT 'KG',
    status ENUM('ACTIVE','INACTIVE') NOT NULL DEFAULT 'ACTIVE',
    density DECIMAL(6,3) NULL,
    mfi DECIMAL(8,2) NULL,
    moisture_content DECIMAL(5,2) NULL,
    melting_point_min DECIMAL(6,1) NULL,
    melting_point_max DECIMAL(6,1) NULL,
    image_url VARCHAR(1000) NULL,
    description TEXT NULL,
    version INT NOT NULL DEFAULT 1,
    created_by VARCHAR(100) NULL,
    updated_by VARCHAR(100) NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    PRIMARY KEY (id),
    UNIQUE KEY uk_materials_code (material_code),
    KEY idx_materials_name (material_name),
    KEY idx_materials_node (material_node_id),
    KEY idx_materials_type (material_type),
    KEY idx_materials_status (status),
    KEY idx_materials_supplier (supplier),
    CONSTRAINT fk_materials_node FOREIGN KEY (material_node_id)
        REFERENCES material_nodes(id) ON UPDATE RESTRICT ON DELETE RESTRICT
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
