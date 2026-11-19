"""Seed permission co dinh cho VCC Plastics - phan quyen chi tiet theo tung man hinh.

Permission la danh muc ky thuat cua phan mem, developer quan ly qua code/seed.
Admin MES chi gan permission co san vao Role, khong tu tao permission.

Moi man hinh co 2 quyen:
    <screen>.view  - thay menu va mo duoc man hinh
    <screen>.edit  - thao tac tren man hinh (tao, sua, xac nhan, chuyen trang thai...)
Man hinh chi de xem (dashboard, ket qua san xuat) chi co quyen .view.

Ma quyen PHAI trung voi ``PAGE_PERMISSIONS`` trong FE (src/auth/pagePermissions.js).
Cot ``description`` luu khoa menu (navigation.<key>) de FE hien ten man hinh da dich.

Lan chay dau tien se chuyen cac role dang dung bo quyen cu theo phan he
(``<module>.view`` / ``<module>.create``) sang quyen man hinh tuong ung roi tat bo quyen cu.

Run with:

    python -m scripts.seed_permissions
"""

from app.core.database import get_vcc_plastics_connection

# (code prefix, module_code, navigation key, Vietnamese screen name, has edit)
SCREENS = [
    ("dashboard", "DASHBOARD", "dashboard", "Tổng quan", False),

    ("production.dashboard", "PRODUCTION", "production-dashboard", "Tổng quan sản xuất", False),
    ("production.planning", "PRODUCTION", "production-planning", "Kế hoạch sản xuất", True),
    ("production.order-list", "PRODUCTION", "production-order-list", "Danh sách lệnh sản xuất", True),
    ("production.order-detail", "PRODUCTION", "production-order-detail", "Chi tiết lệnh sản xuất", True),
    ("production.work-order", "PRODUCTION", "work-order-management", "Quản lý lệnh công việc", True),
    ("production.execution", "PRODUCTION", "production-execution", "Thực thi sản xuất", True),
    ("production.results", "PRODUCTION", "production-results", "Kết quả sản xuất", False),

    ("machine.monitoring", "MACHINE_EQUIPMENT", "machine-monitoring", "Giám sát máy", True),
    ("machine.detail", "MACHINE_EQUIPMENT", "machine-detail", "Chi tiết máy", True),
    ("equipment.monitoring", "MACHINE_EQUIPMENT", "equipment-monitoring", "Giám sát thiết bị", True),
    ("machine.downtime", "MACHINE_EQUIPMENT", "downtime-management", "Quản lý dừng máy", True),
    ("machine.alarm-history", "MACHINE_EQUIPMENT", "alarm-history", "Lịch sử cảnh báo", True),

    ("mold.overview", "MOLD", "mold-overview", "Tổng quan khuôn", True),
    ("mold.list", "MOLD", "mold-list", "Danh sách khuôn", True),
    ("mold.detail", "MOLD", "mold-detail", "Chi tiết khuôn", True),
    ("mold.status", "MOLD", "mold-status", "Trạng thái khuôn", True),
    ("mold.installation-history", "MOLD", "mold-installation-history", "Lịch sử lắp khuôn", True),
    ("mold.shot-counter", "MOLD", "mold-shot-counter", "Bộ đếm shot khuôn", True),
    ("mold.location", "MOLD", "mold-location", "Vị trí khuôn", True),

    ("material.status", "MATERIAL", "material-status", "Trạng thái vật tư", True),
    ("material.requirement", "MATERIAL", "material-requirement", "Nhu cầu vật tư", True),
    ("material.allocation", "MATERIAL", "material-allocation", "Phân bổ vật tư", True),
    ("material.usage", "MATERIAL", "material-usage", "Sử dụng vật tư", True),
    ("material.lot-history", "MATERIAL", "material-lot-history", "Lịch sử lô vật tư", True),

    ("quality.dashboard", "QUALITY", "quality-dashboard", "Tổng quan chất lượng", True),
    ("quality.inspection", "QUALITY", "inspection-management", "Quản lý kiểm tra", True),
    ("quality.spc", "QUALITY", "spc-monitoring", "Giám sát SPC", True),
    ("quality.ng", "QUALITY", "ng-management", "Quản lý NG", True),
    ("quality.measuring-equipment", "QUALITY", "measuring-equipment", "Thiết bị đo lường", True),
    ("quality.calibration", "QUALITY", "calibration-management", "Quản lý hiệu chuẩn", True),

    ("maintenance.overview", "MAINTENANCE", "maintenance-overview", "Tổng quan bảo trì", True),
    ("maintenance.requests", "MAINTENANCE", "maintenance-requests", "Yêu cầu bảo trì", True),
    ("maintenance.planning", "MAINTENANCE", "maintenance-planning", "Kế hoạch bảo trì", True),
    ("maintenance.machine-equipment", "MAINTENANCE", "machine-equipment-maintenance", "Bảo trì máy & thiết bị", True),
    ("maintenance.mold", "MAINTENANCE", "mold-maintenance", "Bảo trì khuôn", True),
    ("maintenance.production-tool", "MAINTENANCE", "production-tool-maintenance", "Bảo trì công cụ sản xuất", True),
    ("maintenance.history", "MAINTENANCE", "maintenance-history", "Lịch sử bảo trì", True),

    ("traceability.product", "TRACEABILITY", "product-traceability", "Truy xuất sản phẩm", True),
    ("traceability.material", "TRACEABILITY", "material-traceability", "Truy xuất vật tư", True),
    ("traceability.lot-genealogy", "TRACEABILITY", "lot-genealogy", "Phả hệ lô", True),

    ("reports.report-center", "REPORTS", "report-center", "Trung tâm báo cáo", True),

    ("administration.user", "ADMINISTRATION", "user-management", "Quản lý người dùng", True),
    ("administration.role", "ADMINISTRATION", "role-permission", "Vai trò & phân quyền", True),
    ("administration.audit-log", "ADMINISTRATION", "audit-log", "Nhật ký hệ thống", True),

    ("configuration.factory-structure", "SYSTEM_CONFIGURATION", "factory-structure-master", "Cơ cấu nhà máy", True),
    ("configuration.product", "SYSTEM_CONFIGURATION", "product-master", "Danh mục sản phẩm", True),
    ("configuration.machine-equipment", "SYSTEM_CONFIGURATION", "machine-equipment-master", "Danh mục máy & thiết bị", True),
    ("configuration.mold", "SYSTEM_CONFIGURATION", "mold-master", "Danh mục khuôn", True),
    ("configuration.material", "SYSTEM_CONFIGURATION", "material-master", "Danh mục vật tư", True),
    ("configuration.production-tool", "SYSTEM_CONFIGURATION", "production-tool-master", "Danh mục công cụ sản xuất", True),
    ("configuration.measuring-equipment", "SYSTEM_CONFIGURATION", "measuring-equipment-master", "Danh mục thiết bị đo", True),
    ("configuration.quality-standard", "SYSTEM_CONFIGURATION", "quality-standard-master", "Tiêu chuẩn chất lượng", True),
    ("configuration.process-parameter-standard", "SYSTEM_CONFIGURATION", "process-parameter-standard", "Tiêu chuẩn thông số công đoạn", True),
    ("configuration.shift-calendar", "SYSTEM_CONFIGURATION", "shift-calendar-master", "Ca làm việc & lịch", True),
    ("configuration.reason-code", "SYSTEM_CONFIGURATION", "reason-code-master", "Danh mục mã nguyên nhân", True),
    ("configuration.iot-data-dictionary", "SYSTEM_CONFIGURATION", "iot-data-dictionary", "Từ điển dữ liệu IoT", True),
    ("configuration.device-mapping", "SYSTEM_CONFIGURATION", "device-mapping", "Ánh xạ thiết bị", True),
    ("configuration.data-collection-status", "SYSTEM_CONFIGURATION", "data-collection-status", "Trạng thái thu thập dữ liệu", True),
    ("configuration.integration-settings", "SYSTEM_CONFIGURATION", "integration-settings", "Cài đặt tích hợp", True),
    ("configuration.system-settings", "SYSTEM_CONFIGURATION", "system-settings", "Cài đặt hệ thống", True),
]

# (permission_code, permission_name, module_code, description)
PERMISSIONS = [
    perm
    for prefix, module, nav_key, name, has_edit in SCREENS
    for perm in (
        [(f"{prefix}.view", f"Xem - {name}", module, nav_key)]
        + ([(f"{prefix}.edit", f"Thao tác - {name}", module, nav_key)] if has_edit else [])
    )
]
PERMISSION_CODES = {code for code, _, _, _ in PERMISSIONS}

# Bo quyen cu theo phan he -> module_code cua man hinh tuong ung.
LEGACY_MODULES = {
    "dashboard": "DASHBOARD", "production": "PRODUCTION", "machine_equipment": "MACHINE_EQUIPMENT",
    "mold": "MOLD", "material": "MATERIAL", "quality": "QUALITY", "maintenance": "MAINTENANCE",
    "traceability": "TRACEABILITY", "reports": "REPORTS", "administration": "ADMINISTRATION",
    "system_configuration": "SYSTEM_CONFIGURATION",
}


def _migrate_legacy_role_permissions(cur):
    """Role dang co <module>.view -> moi quyen .view cua module; <module>.create -> them moi quyen .edit."""
    cur.execute(
        """
        SELECT rp.role_id, p.permission_code
        FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id
        """
    )
    grants = set()
    for role_id, code in cur.fetchall():
        module_key, _, action = code.partition(".")
        module = LEGACY_MODULES.get(module_key)
        if not module or action not in ("view", "create"):
            continue
        wanted = ("view",) if action == "view" else ("view", "edit")
        for new_code, _, new_module, _ in PERMISSIONS:
            if new_module == module and new_code.rsplit(".", 1)[1] in wanted:
                grants.add((role_id, new_code))
    for role_id, new_code in sorted(grants):
        cur.execute(
            """
            INSERT IGNORE INTO role_permissions (role_id, permission_id, created_by)
            SELECT %s, id, 'SYSTEM_SEED' FROM permissions WHERE permission_code = %s
            """,
            (role_id, new_code),
        )
    return len(grants)


def seed_permissions():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        for code, name, module, description in PERMISSIONS:
            cur.execute(
                """
                INSERT INTO permissions
                    (permission_code, permission_name, module_code, description, is_active, created_by)
                VALUES (%s, %s, %s, %s, 1, 'SYSTEM_SEED')
                ON DUPLICATE KEY UPDATE
                    permission_name = VALUES(permission_name),
                    module_code = VALUES(module_code),
                    description = VALUES(description),
                    is_active = 1,
                    updated_by = 'SYSTEM_SEED'
                """,
                (code, name, module, description),
            )

        migrated = _migrate_legacy_role_permissions(cur)

        # Quyen khong con trong danh muc: go khoi role va tat (giu ban ghi de tra cuu lich su).
        placeholders = ",".join(["%s"] * len(PERMISSION_CODES))
        codes = tuple(sorted(PERMISSION_CODES))
        cur.execute(
            f"DELETE rp FROM role_permissions rp JOIN permissions p ON p.id = rp.permission_id "
            f"WHERE p.permission_code NOT IN ({placeholders})",
            codes,
        )
        removed_links = cur.rowcount
        cur.execute(
            f"UPDATE permissions SET is_active = 0, updated_by = 'SYSTEM_SEED' "
            f"WHERE is_active = 1 AND permission_code NOT IN ({placeholders})",
            codes,
        )
        retired = cur.rowcount

        conn.commit()
        print(f"Seeded {len(PERMISSIONS)} permissions ({len(SCREENS)} screens).")
        print(f"Migrated {migrated} role grants from legacy module permissions; "
              f"removed {removed_links} legacy role links; retired {retired} legacy permissions.")
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    seed_permissions()
