"""Seed demo/test data for the Production Tool Master module (danh muc dung cu san xuat).

Rebuilds the tool location tree (production_tool_node_types + production_tool_nodes)
to match a small factory layout:

    Xuong A (Workshop)
      Chuyen 1 (Line)
      Chuyen 2 (Line)
    Xuong B (Workshop)
      Chuyen 1 (Line)
    Khu vuc QA (Workshop)
    Khu bao tri (Workshop)

...then seeds one demo custom field ("Xuat xu" / country of origin) and a small,
realistic set of cutting/assembly/measuring/handling tools under each leaf
location, via the live HTTP API (so the same validation/insert path the
frontend uses is exercised).

This script is a full RESET + reseed of Production Tool Master demo data: it
deletes every existing tool and location node before rebuilding, since the
hierarchy shape itself may have changed. Do not run this against a database
that holds real production data.

Usage:
    python scripts/seed_production_tool_master_demo.py [base_url]

Default base_url is http://127.0.0.1:5005.
"""

import sys
from datetime import date, timedelta

import requests

BASE_URL = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5005").rstrip("/")
API = f"{BASE_URL}/api/production-tool-master"
ACTOR = "SEED_DEMO"
TODAY = date.today()


def _iso(days_from_today: int) -> str:
    return (TODAY + timedelta(days=days_from_today)).isoformat()


def reset_existing() -> None:
    response = requests.get(f"{API}/tools", params={"page": 1, "page_size": 500}, timeout=15)
    response.raise_for_status()
    tools = response.json().get("items", [])
    for tool in tools:
        requests.delete(f"{API}/tools/{tool['id']}", timeout=15)
    print(f"Deleted {len(tools)} existing tools.")

    response = requests.get(f"{API}/nodes", params={"tree": False}, timeout=15)
    response.raise_for_status()
    nodes = sorted(response.json(), key=lambda row: -row["level_no"])
    for node in nodes:
        requests.delete(f"{API}/nodes/{node['id']}", timeout=15)
    print(f"Deleted {len(nodes)} existing location nodes.")


def _node_types_by_code() -> dict[str, dict]:
    response = requests.get(f"{API}/node-types", timeout=15)
    response.raise_for_status()
    return {row["code"]: row for row in response.json()}


def _get_or_create_node_type(code: str, payload: dict, cache: dict[str, dict]) -> int:
    existing = cache.get(code)
    if existing:
        return existing["id"]
    response = requests.post(f"{API}/node-types", json={**payload, "code": code, "created_by": ACTOR}, timeout=15)
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Failed to create node-type {code}: {response.status_code} {response.text}")
    new_id = response.json()["id"]
    cache[code] = {"id": new_id}
    return new_id


def seed_node_types() -> dict[str, int]:
    cache = _node_types_by_code()
    workshop_id = _get_or_create_node_type(
        "WORKSHOP", {"name": "Xuong", "level_order": 1, "color": "#005BAB", "icon": "factory", "sort_order": 1},
        cache,
    )
    line_id = _get_or_create_node_type(
        "LINE",
        {"name": "Chuyen san xuat", "level_order": 2, "parent_type_id": workshop_id, "color": "#1976D2", "sort_order": 1},
        cache,
    )
    return {"WORKSHOP": workshop_id, "LINE": line_id}


def _get_or_create_field_definition(field_code: str, payload: dict) -> int:
    response = requests.get(f"{API}/field-definitions", timeout=15)
    response.raise_for_status()
    existing = {row["field_code"]: row for row in response.json()}
    if field_code in existing:
        return existing[field_code]["id"]
    response = requests.post(
        f"{API}/field-definitions", json={**payload, "field_code": field_code, "created_by": ACTOR}, timeout=15,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(f"Failed to create field-definition {field_code}: {response.status_code} {response.text}")
    return response.json()["id"]


def seed_field_definitions() -> None:
    _get_or_create_field_definition(
        "XUAT_XU",
        {"field_name": "Xuat xu", "data_type": "TEXT", "is_required": False, "sort_order": 1,
         "description": "Quoc gia san xuat cua dung cu."},
    )
    print("Custom field 'XUAT_XU' ready.")


# (code, name, parent_code or None, node_type key)
TREE = [
    ("WS_A", "Xuong A", None, "WORKSHOP"),
    ("WS_A_L1", "Chuyen 1", "WS_A", "LINE"),
    ("WS_A_L2", "Chuyen 2", "WS_A", "LINE"),
    ("WS_B", "Xuong B", None, "WORKSHOP"),
    ("WS_B_L1", "Chuyen 1", "WS_B", "LINE"),
    ("QA_AREA", "Khu vuc QA", None, "WORKSHOP"),
    ("MAINT_AREA", "Khu bao tri", None, "WORKSHOP"),
]


def seed_nodes(node_types: dict[str, int]) -> dict[str, int]:
    ids: dict[str, int] = {}
    for code, name, parent_code, type_key in TREE:
        payload = {
            "name": name,
            "node_type_id": node_types[type_key],
            "status": "ACTIVE",
            "sort_order": 1,
            "code": code,
            "created_by": ACTOR,
        }
        if parent_code:
            payload["parent_id"] = ids[parent_code]
        response = requests.post(f"{API}/nodes", json=payload, timeout=15)
        if response.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create node {code}: {response.status_code} {response.text}")
        ids[code] = response.json()["id"]
    return ids


# (code, name, tool_type, category, manufacturer, model, serial_number, node_code, status,
#  assigned_process, calibration_required, cal_due_offset_days, cal_interval_days,
#  measurement_range, measurement_resolution, description, custom_fields)
TOOLS = [
    ("CT-001", "Dao phay ngon D10", "CUTTING", "Dao phay", "Sandvik Coromant", "R390-11", "SN-CT-001",
     "WS_A_L1", "AVAILABLE", "Phay bien dang", False, None, None, None, None,
     "Dao phay ngon dung gia cong khuon mau.", {"XUAT_XU": "Thuy Dien"}),
    ("CT-002", "Mui khoan D6.5", "CUTTING", "Mui khoan", "Kennametal", "KC725M", "SN-CT-002",
     "WS_A_L1", "IN_USE", "Khoan lo bat vit", False, None, None, None, None,
     "Mui khoan carbide dung khoan lo lap rap.", None),
    ("CT-003", "Dao tien ngoai", "CUTTING", "Dao tien", "Mitsubishi Materials", "VNMG160404", "SN-CT-003",
     "WS_A_L2", "AVAILABLE", "Tien truc", False, None, None, None, None,
     "Dao tien hop kim dung gia cong truc kim loai.", None),
    ("CT-004", "Luoi cua dia", "CUTTING", "Luoi cua", "Bosch", "PCD-250", "SN-CT-004",
     "WS_B_L1", "IN_MAINTENANCE", "Cat phoi nhua", False, None, None, None, None,
     "Luoi cua dang bao tri, mai lai luoi cat.", None),
    ("CT-005", "Mui taro M6", "CUTTING", "Mui taro", "OSG", "EX-SUS", "SN-CT-005",
     "WS_B_L1", "OUT_OF_SERVICE", "Taro ren", False, None, None, None, None,
     "Mui taro bi gay, cho thanh ly.", None),
    ("AS-001", "Sung van vit khi nen", "ASSEMBLY", "Sung van vit", "Makita", "AN620H", "SN-AS-001",
     "WS_A_L1", "IN_USE", "Lap rap vo san pham", False, None, None, None, None,
     "Sung van vit khi nen dung lap rap hang loat.", None),
    ("AS-002", "Co le luc 1/2 inch", "ASSEMBLY", "Co le luc", "Stanley", "TW-50", "SN-AS-002",
     "WS_A_L2", "AVAILABLE", "Xiet bulong khung", False, None, None, None, None,
     "Co le luc dung xiet bulong dung luc quy dinh.", None),
    ("AS-003", "Bo luc giac", "ASSEMBLY", "Bo luc giac", "Snap-on", "HFSM6", "SN-AS-003",
     "WS_B_L1", "AVAILABLE", "Lap rap chi tiet nho", False, None, None, None, None,
     "Bo luc giac 6 canh dung lap rap chi tiet nho.", None),
    ("AS-004", "May ban rive cu", "ASSEMBLY", "Sung rive", "Local Brand", "RV-100", "SN-AS-004",
     "WS_B_L1", "INACTIVE", None, False, None, None, None, None,
     "May ban rive cu, da ngung su dung.", None),
    ("MS-001", "Thuoc cap dien tu 0-150mm", "MEASURING", "Thuoc cap", "Mitutoyo", "500-196-30", "SN-MS-001",
     "QA_AREA", "AVAILABLE", "Kiem tra kich thuoc", True, 12, 180, "0-150 mm", "0.01 mm",
     "Thuoc cap dien tu dung kiem tra kich thuoc san pham.", {"XUAT_XU": "Nhat Ban"}),
    ("MS-002", "Panme do ngoai 0-25mm", "MEASURING", "Panme", "Mitutoyo", "103-137", "SN-MS-002",
     "QA_AREA", "AVAILABLE", "Kiem tra do day", True, 20, 180, "0-25 mm", "0.001 mm",
     "Panme do ngoai dung kiem tra do day thanh san pham.", {"XUAT_XU": "Nhat Ban"}),
    ("MS-003", "Dong ho so", "MEASURING", "Dong ho so", "Mitutoyo", "2046S", "SN-MS-003",
     "QA_AREA", "AVAILABLE", "Kiem tra do phang", True, -10, 365, "0-10 mm", "0.01 mm",
     "Dong ho so dung kiem tra do phang be mat khuon.", None),
    ("MS-004", "May do do nham be mat", "MEASURING", "May do do nham", "Mitutoyo", "SJ-210", "SN-MS-004",
     "QA_AREA", "AVAILABLE", "Kiem tra be mat", True, 200, 365, "0-10 Ra", "0.01 Ra",
     "May do do nham be mat san pham sau gia cong.", None),
    ("HD-001", "Xe day vat lieu", "HANDLING", "Xe day", None, None, "SN-HD-001",
     "WS_A_L1", "AVAILABLE", "Van chuyen ban thanh pham", False, None, None, None, None,
     "Xe day tay dung van chuyen ban thanh pham noi bo.", None),
    ("HD-002", "Day cau 2 tan", "HANDLING", "Day cau", None, None, "SN-HD-002",
     "MAINT_AREA", "AVAILABLE", "Nang ha khuon", False, None, None, None, None,
     "Day cau dung nang ha khuon khi thay khuon.", None),
    ("HD-003", "Pallet truck", "HANDLING", "Xe nang tay", None, None, "SN-HD-003",
     "MAINT_AREA", "IN_MAINTENANCE", "Van chuyen pallet", False, None, None, None, None,
     "Pallet truck dang sua banh xe.", None),
]


def seed_tools(node_ids: dict[str, int]) -> None:
    created = 0
    for (code, name, tool_type, category, manufacturer, model, serial_number, node_code, status,
         assigned_process, calibration_required, cal_due_offset, cal_interval, meas_range, meas_resolution,
         description, custom_fields) in TOOLS:
        payload = {
            "tool_code": code,
            "tool_name": name,
            "tool_type": tool_type,
            "tool_category": category,
            "manufacturer": manufacturer,
            "model": model,
            "serial_number": serial_number,
            "production_tool_node_id": node_ids[node_code],
            "assigned_process": assigned_process,
            "status": status,
            "calibration_required": calibration_required,
            "calibration_due_date": _iso(cal_due_offset) if cal_due_offset is not None else None,
            "last_calibration_date": _iso(cal_due_offset - cal_interval) if cal_due_offset is not None else None,
            "calibration_interval_days": cal_interval,
            "measurement_range": meas_range,
            "measurement_resolution": meas_resolution,
            "description": description,
            "custom_fields": custom_fields or {},
            "created_by": ACTOR,
        }
        response = requests.post(f"{API}/tools", json=payload, timeout=15)
        if response.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create tool {code}: {response.status_code} {response.text}")
        created += 1
    print(f"Tools created: {created}.")


def main() -> None:
    reset_existing()
    node_types = seed_node_types()
    print(f"Node types ready: {node_types}")
    node_ids = seed_nodes(node_types)
    print(f"Location nodes created: {len(node_ids)}.")
    seed_field_definitions()
    seed_tools(node_ids)
    print("Production Tool Master demo data seeded successfully.")


if __name__ == "__main__":
    main()
