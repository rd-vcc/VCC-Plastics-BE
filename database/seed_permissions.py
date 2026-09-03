"""Seed permission co dinh cho VCC Plastics.

Permission la danh muc ky thuat cua phan mem, developer quan ly qua code/seed.
Admin MES chi gan permission co san vao Role, khong tu tao permission.
"""

from database.db import get_vcc_plastics_connection

PERMISSIONS = [
    # Dashboard
    ("dashboard.view", "Xem Dashboard", "DASHBOARD", None),

    # Production Management
    ("production.dashboard.view", "Xem Production Dashboard", "PRODUCTION", None),
    ("production.planning.view", "Xem Production Planning", "PRODUCTION", None),
    ("production.planning.create", "Tao ke hoach san xuat", "PRODUCTION", None),
    ("production.planning.edit", "Sua ke hoach san xuat", "PRODUCTION", None),
    ("production.order.view", "Xem Production Order", "PRODUCTION", None),
    ("production.order.create", "Tao Production Order", "PRODUCTION", None),
    ("production.order.edit", "Sua Production Order", "PRODUCTION", None),
    ("production.order.approve", "Phe duyet Production Order", "PRODUCTION", None),
    ("production.execution.view", "Xem Production Execution", "PRODUCTION", None),
    ("production.execution.start", "Bat dau Production Execution", "PRODUCTION", None),
    ("production.execution.pause", "Tam dung Production Execution", "PRODUCTION", None),
    ("production.execution.resume", "Tiep tuc Production Execution", "PRODUCTION", None),
    ("production.execution.finish", "Ket thuc Production Execution", "PRODUCTION", None),
    ("production.result.view", "Xem Production Result", "PRODUCTION", None),

    # Machine & Equipment
    ("machine.monitoring.view", "Xem Machine Monitoring", "MACHINE", None),
    ("machine.detail.view", "Xem Machine Detail", "MACHINE", None),
    ("machine.downtime.view", "Xem Downtime", "MACHINE", None),
    ("machine.downtime.manage", "Quan ly Downtime", "MACHINE", None),
    ("machine.alarm.view", "Xem Alarm History", "MACHINE", None),

    # Mold
    ("mold.view", "Xem Mold Management", "MOLD", None),
    ("mold.manage", "Quan ly nghiep vu Mold", "MOLD", None),

    # Material
    ("material.view", "Xem Material Management", "MATERIAL", None),
    ("material.manage", "Quan ly nghiep vu Material", "MATERIAL", None),

    # Quality
    ("quality.view", "Xem Quality Management", "QUALITY", None),
    ("quality.inspection.create", "Tao kiem tra chat luong", "QUALITY", None),
    ("quality.inspection.edit", "Sua ket qua kiem tra chat luong", "QUALITY", None),
    ("quality.inspection.approve", "Phe duyet ket qua chat luong", "QUALITY", None),

    # Maintenance
    ("maintenance.view", "Xem Maintenance Management", "MAINTENANCE", None),
    ("maintenance.manage", "Quan ly Maintenance", "MAINTENANCE", None),

    # Traceability
    ("traceability.view", "Xem Traceability", "TRACEABILITY", None),

    # Reports
    ("reports.view", "Xem Reports & Analytics", "REPORTS", None),

    # Administration
    ("administration.user.view", "Xem User Management", "ADMINISTRATION", None),
    ("administration.user.manage", "Quan ly User MES", "ADMINISTRATION", None),
    ("administration.role.view", "Xem Role & Permission", "ADMINISTRATION", None),
    ("administration.role.manage", "Quan ly Role & Permission", "ADMINISTRATION", None),
    ("administration.audit.view", "Xem Audit Log", "ADMINISTRATION", None),

    # System Configuration
    ("system.master.view", "Xem Master Data", "SYSTEM_CONFIGURATION", None),
    ("system.master.manage", "Quan ly Master Data", "SYSTEM_CONFIGURATION", None),
    ("system.iot.view", "Xem IoT Configuration", "SYSTEM_CONFIGURATION", None),
    ("system.iot.manage", "Quan ly IoT Configuration", "SYSTEM_CONFIGURATION", None),
    ("system.integration.manage", "Quan ly Integration Settings", "SYSTEM_CONFIGURATION", None),
    ("system.settings.manage", "Quan ly System Settings", "SYSTEM_CONFIGURATION", None),
]


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
        conn.commit()
        print(f"Seeded {len(PERMISSIONS)} permissions.")
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    seed_permissions()
