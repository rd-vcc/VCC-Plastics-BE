"""Seed cac vai tro chuan theo vi tri cong viec tai nha may VCC Plastics.

Moi vai tro duoc gan bo quyen man hinh cu the (xem ``scripts.seed_permissions``).
Mot nguoi co the giu nhieu vai tro cung luc; quyen la hop cua cac vai tro.

- Vai tro chua co: tao moi va gan quyen mac dinh.
- Vai tro da co: GIU NGUYEN quyen admin da chinh tren man hinh Vai tro & phan quyen.
  Chay voi ``--reset`` de dua quyen cua cac vai tro chuan ve mac dinh.

Chay sau ``scripts.seed_permissions``:

    python -m scripts.seed_roles [--reset]
"""

import sys

from app.core.database import get_vcc_plastics_connection
from scripts.seed_permissions import SCREENS

V, E = "view", "edit"


def screens(*prefixes, actions=(V, E)):
    """Quyen cua cac man hinh theo prefix ma (vd 'material' = moi man hinh vat tu)."""
    out = set()
    for prefix, _, _, _, has_edit in SCREENS:
        if any(prefix == p or prefix.startswith(p + ".") for p in prefixes):
            out.add(f"{prefix}.view")
            if E in actions and has_edit:
                out.add(f"{prefix}.edit")
    return out


def view(*prefixes):
    return screens(*prefixes, actions=(V,))


# Man hinh van hanh (khong gom Quan tri & Cau hinh)
OPERATIONS = ("dashboard", "production", "machine", "equipment", "mold", "material", "quality",
              "maintenance", "traceability", "reports")

ROLES = [
    (
        "BOD_MANAGER", "Ban giám đốc, quản đốc",
        "Tổng quan, Báo cáo: theo dõi OEE, tiến độ, chất lượng.",
        view(*OPERATIONS) | screens("reports"),
    ),
    (
        "PLANNING", "Phòng kế hoạch",
        "Lệnh sản xuất, Kế hoạch: tạo lệnh, xếp ca, phát hành.",
        screens("production.planning", "production.order-list", "production.order-detail", "production.work-order")
        | view("dashboard", "production", "material.status", "material.requirement", "machine.monitoring",
               "mold.overview", "mold.list", "mold.status", "reports"),
    ),
    (
        "WAREHOUSE", "Kho",
        "Vật tư: nhập lô, giữ chỗ, cấp ra máy, nhận nhựa trả về.",
        screens("material")
        | view("dashboard", "production.work-order", "traceability.material"),
    ),
    (
        "OPERATOR", "Công nhân vận hành",
        "Thực thi sản xuất: bắt đầu, báo sản lượng, báo lỗi, kết thúc.",
        screens("production.execution")
        | view("dashboard", "production.work-order", "machine.monitoring"),
    ),
    (
        "SHIFT_LEADER", "Tổ trưởng, trưởng ca",
        "Theo dõi chuyền, báo dừng máy, nghiệm thu sau sửa chữa.",
        screens("production.execution", "machine.downtime", "maintenance.requests",
                "maintenance.machine-equipment", "maintenance.mold", "maintenance.production-tool")
        | view("dashboard", "production.dashboard", "production.work-order", "production.results",
               "machine", "equipment", "mold.status", "maintenance.overview", "maintenance.history"),
    ),
    (
        "QC", "QC",
        "Chất lượng, Truy xuất: kiểm tra, xử lý NG, hiệu chuẩn thiết bị đo.",
        screens("quality", "traceability")
        | view("dashboard", "production.work-order", "production.results", "material.lot-history",
               "mold.list", "mold.detail", "reports"),
    ),
    (
        "MAINTENANCE", "Bảo trì",
        "Bảo trì, Máy móc: sửa chữa, bảo dưỡng định kỳ máy và khuôn.",
        screens("maintenance", "machine", "equipment", "mold")
        | view("dashboard", "production.work-order", "reports"),
    ),
    (
        "SYS_ADMIN", "Quản trị viên",
        "Cấu hình, Quản trị: danh mục, người dùng, phân quyền.",
        screens("administration", "configuration") | view("dashboard"),
    ),
]


def seed_roles(reset=False):
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        cur.execute("SELECT permission_code, id FROM permissions WHERE is_active = 1")
        perm_ids = dict(cur.fetchall())
        for code, name, description, codes in ROLES:
            missing = codes - perm_ids.keys()
            if missing:
                raise RuntimeError(f"{code}: permission chua seed: {sorted(missing)} - chay scripts.seed_permissions truoc.")
            cur.execute("SELECT id FROM roles WHERE role_code = %s", (code,))
            row = cur.fetchone()
            if row:
                role_id = row[0]
                if not reset:
                    print(f"{code}: da co, giu nguyen quyen hien tai.")
                    continue
                cur.execute("UPDATE roles SET role_name = %s, description = %s, is_active = 1, updated_by = 'SYSTEM_SEED' WHERE id = %s",
                            (name, description, role_id))
                cur.execute("DELETE FROM role_permissions WHERE role_id = %s", (role_id,))
            else:
                cur.execute("INSERT INTO roles (role_code, role_name, description, is_active, created_by) VALUES (%s, %s, %s, 1, 'SYSTEM_SEED')",
                            (code, name, description))
                role_id = cur.lastrowid
            cur.executemany("INSERT INTO role_permissions (role_id, permission_id, created_by) VALUES (%s, %s, 'SYSTEM_SEED')",
                            [(role_id, perm_ids[c]) for c in sorted(codes)])
            edits = sum(c.endswith(".edit") for c in codes)
            print(f"{code}: {len(codes)} quyen ({len(codes) - edits} xem, {edits} thao tac).")
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    seed_roles(reset="--reset" in sys.argv)
