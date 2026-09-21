"""Seed demo/test data for the Material Master module (danh muc vat tu).

Rebuilds the material category tree (material_node_types + material_nodes)
to match the "VCC Plant 1" hierarchy:

    VCC Plant 1
      Raw Materials
        Base Resins        -> PP, ABS, PC, PA, PE
        Compounds          -> PP/ABS/PC/PA Compounds
      Additives
        Color Masterbatch
        Additives (general)
        Others
      Recycled Materials
      Packaging Materials

...then seeds a small, real set of plastics-industry materials under each
leaf category, via the live HTTP API (so the same validation/insert path the
frontend uses is exercised).

This script is a full RESET + reseed of Material Master demo data: it
deletes every existing material and category node before rebuilding, since
the hierarchy shape itself changed. Do not run this against a database that
holds real production Material Master data.

Usage:
    python scripts/seed_material_master_demo.py [base_url]

Default base_url is http://127.0.0.1:5005.
"""

import sys

import requests

BASE_URL = (sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:5005").rstrip("/")
API = f"{BASE_URL}/api/material-master"
ACTOR = "SEED_DEMO"


def reset_existing() -> None:
    response = requests.get(f"{API}/materials", params={"page": 1, "page_size": 500}, timeout=15)
    response.raise_for_status()
    materials = response.json().get("items", [])
    for material in materials:
        requests.delete(f"{API}/materials/{material['id']}", timeout=15)
    print(f"Deleted {len(materials)} existing materials.")

    response = requests.get(f"{API}/nodes", params={"tree": False}, timeout=15)
    response.raise_for_status()
    nodes = sorted(response.json(), key=lambda row: -row["level_no"])
    for node in nodes:
        requests.delete(f"{API}/nodes/{node['id']}", timeout=15)
    print(f"Deleted {len(nodes)} existing category nodes.")


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
    group_id = _get_or_create_node_type(
        "GROUP", {"name": "Nhom vat tu", "level_order": 1, "color": "#005BAB", "icon": "category", "sort_order": 1},
        cache,
    )
    category_id = _get_or_create_node_type(
        "CATEGORY",
        {"name": "Danh muc vat tu", "level_order": 2, "parent_type_id": group_id, "color": "#1976D2", "sort_order": 1},
        cache,
    )
    return {"GROUP": group_id, "CATEGORY": category_id}


# (code, name, parent_code or None, node_type key)
TREE = [
    ("PLANT1", "VCC Plant 1", None, "GROUP"),
    ("RAW_MATERIALS", "Raw Materials", "PLANT1", "GROUP"),
    ("BASE_RESINS", "Base Resins", "RAW_MATERIALS", "CATEGORY"),
    ("PP", "PP (Polypropylene)", "BASE_RESINS", "CATEGORY"),
    ("ABS", "ABS", "BASE_RESINS", "CATEGORY"),
    ("PC", "PC (Polycarbonate)", "BASE_RESINS", "CATEGORY"),
    ("PA", "PA (Polyamide)", "BASE_RESINS", "CATEGORY"),
    ("PE", "PE (Polyethylene)", "BASE_RESINS", "CATEGORY"),
    ("COMPOUNDS", "Compounds", "RAW_MATERIALS", "CATEGORY"),
    ("PP_COMPOUNDS", "PP Compounds", "COMPOUNDS", "CATEGORY"),
    ("ABS_COMPOUNDS", "ABS Compounds", "COMPOUNDS", "CATEGORY"),
    ("PC_COMPOUNDS", "PC Compounds", "COMPOUNDS", "CATEGORY"),
    ("PA_COMPOUNDS", "PA Compounds", "COMPOUNDS", "CATEGORY"),
    ("ADDITIVES_GROUP", "Additives", "PLANT1", "GROUP"),
    ("COLOR_MASTERBATCH", "Color Masterbatch", "ADDITIVES_GROUP", "CATEGORY"),
    ("ADDITIVES_GENERAL", "Additives", "ADDITIVES_GROUP", "CATEGORY"),
    ("OTHERS_ADDITIVES", "Others", "ADDITIVES_GROUP", "CATEGORY"),
    ("RECYCLED_MATERIALS", "Recycled Materials", "PLANT1", "GROUP"),
    ("PACKAGING_MATERIALS", "Packaging Materials", "PLANT1", "GROUP"),
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


MATERIALS = [
    # code, name, material_type, node_code, base_resin, material_group, supplier, unit,
    # density, mfi, moisture, melt_min, melt_max, description
    ("PP-SCG-P510J", "PP Homopolymer SCG P510J", "RAW_MATERIAL", "PP", "PP", "Homopolymer",
     "SCG Chemicals", "KG", "0.9000", "8.0000", "0.0500", "160.00", "165.00",
     "Hat nhua PP nguyen sinh dung ep phun san pham gia dung."),
    ("PP-FORMOSA-1102H", "PP Copolymer Formosa 1102H", "RAW_MATERIAL", "PP", "PP", "Copolymer",
     "Formosa Plastics", "KG", "0.9050", "2.0000", "0.0500", "160.00", "168.00",
     "PP copolymer chiu va dap, dung cho linh kien ky thuat."),
    ("ABS-CHIMEI-PA757", "ABS Chi Mei PA-757", "RAW_MATERIAL", "ABS", "ABS", None,
     "Chi Mei Corporation", "KG", "1.0400", "32.0000", "0.2000", "220.00", "240.00",
     "ABS chuan, dung ep phun vo thiet bi dien tu."),
    ("ABS-LG-XR401", "ABS LG Chem XR-401", "RAW_MATERIAL", "ABS", "ABS", None,
     "LG Chem", "KG", "1.0500", "40.0000", "0.2000", "220.00", "240.00",
     "ABS chay tot, dung ep phun linh kien noi that."),
    ("PC-LOTTE-L1225L", "PC Lotte Chemical Panlite L-1225L", "RAW_MATERIAL", "PC", "PC", None,
     "Lotte Chemical", "KG", "1.2000", "10.0000", "0.0200", "280.00", "300.00",
     "PC trong suot, do ben va dap cao."),
    ("PC-SABIC-LEXAN121R", "PC Sabic Lexan 121R", "RAW_MATERIAL", "PC", "PC", None,
     "Sabic", "KG", "1.2000", "18.5000", "0.0200", "280.00", "300.00",
     "PC ep phun da dung, do chay trung binh-cao."),
    ("PA6-BASF-B3S", "PA6 BASF Ultramid B3S", "RAW_MATERIAL", "PA", "PA6", None,
     "BASF", "KG", "1.1300", None, "0.1000", "215.00", "225.00",
     "PA6 nguyen sinh, dung linh kien ky thuat chiu ma sat."),
    ("PA66-BASF-A3K", "PA66 BASF Ultramid A3K", "RAW_MATERIAL", "PA", "PA66", None,
     "BASF", "KG", "1.1400", None, "0.1000", "255.00", "262.00",
     "PA66 nguyen sinh, do ben co va nhiet cao."),
    ("PE-FORMOSA-9002", "HDPE Formosa 9002", "RAW_MATERIAL", "PE", "PE", "HDPE",
     "Formosa Plastics", "KG", "0.9520", "0.3000", "0.0300", "128.00", "132.00",
     "HDPE ep thoi, dung san xuat can, thung nhua."),
    ("PE-LOTTE-MB9500", "LDPE Lotte Chemical MB9500", "RAW_MATERIAL", "PE", "PE", "LDPE",
     "Lotte Chemical", "KG", "0.9230", "2.0000", "0.0400", "105.00", "112.00",
     "LDPE dung san xuat mang mong, bao bi mem."),
    ("PPC-BOREALIS-BJ368MO", "PP Compound Borealis BJ368MO", "COMPOUND", "PP_COMPOUNDS", "PP", "Talc filled",
     "Borealis", "KG", "1.0500", "22.0000", None, "160.00", "168.00",
     "PP compound don talc, dung linh kien noi that o to."),
    ("ABSC-COVESTRO-BAYBLENDT65", "ABS/PC Blend Covestro Bayblend T65", "COMPOUND", "ABS_COMPOUNDS", "ABS/PC", "Blend",
     "Covestro", "KG", "1.1500", "17.0000", "0.1000", "230.00", "250.00",
     "Hop kim ABS/PC, do ben va dap va chiu nhiet cao."),
    ("PCC-LOTTE-1300G10", "PC Compound Lotte GF10", "COMPOUND", "PC_COMPOUNDS", "PC", "Glass fiber 10%",
     "Lotte Chemical", "KG", "1.3000", "9.0000", "0.0200", "280.00", "300.00",
     "PC gia cuong soi thuy tinh 10%, tang do cung."),
    ("PA66C-DUPONT-ZYTEL70G30", "PA66 Compound DuPont Zytel 70G30", "COMPOUND", "PA_COMPOUNDS", "PA66", "Glass fiber 30%",
     "DuPont", "KG", "1.3700", None, "0.0800", "255.00", "262.00",
     "PA66 gia cuong soi thuy tinh 30%, dung linh kien chiu luc."),
    ("MB-WHITE-TIO2-50", "Masterbatch trang TiO2 50%", "COMPOUND", "COLOR_MASTERBATCH", None, "Masterbatch mau",
     "Clariant", "KG", "1.8000", None, None, None, None,
     "Masterbatch trang ham luong TiO2 50%."),
    ("MB-BLACK-CB40", "Masterbatch den Carbon Black 40%", "COMPOUND", "COLOR_MASTERBATCH", None, "Masterbatch mau",
     "Cabot Corporation", "KG", "1.2000", None, None, None, None,
     "Masterbatch den ham luong carbon black 40%."),
    ("MB-COLOR-RED01", "Masterbatch mau do RAL3020", "COMPOUND", "COLOR_MASTERBATCH", None, "Masterbatch mau",
     "Clariant", "KG", "1.1000", None, None, None, None,
     "Masterbatch mau do theo bang mau RAL3020."),
    ("ADD-UV326", "Chat chong tia UV UV-326", "ADDITIVE", "ADDITIVES_GENERAL", None, "Phu gia on dinh",
     "Songwon Industrial", "KG", None, None, None, None, None,
     "Phu gia chong lao hoa do tia UV cho san pham ngoai troi."),
    ("ADD-AS100", "Chat chong tinh dien AS-100", "ADDITIVE", "ADDITIVES_GENERAL", None, "Phu gia chuc nang",
     "Croda", "KG", None, None, None, None, None,
     "Phu gia chong tinh dien be mat san pham nhua."),
    ("ADD-AO1010", "Chat chong oxy hoa Irganox 1010", "ADDITIVE", "OTHERS_ADDITIVES", None, "Phu gia on dinh",
     "BASF", "KG", None, None, None, None, None,
     "Chat chong oxy hoa chinh, bao ve polymer khoi nhiet do gia cong."),
    ("RPP-PELLET-01", "Hat nhua PP tai sinh", "RECYCLED", "RECYCLED_MATERIALS", "PP", "Tai sinh noi bo",
     "Noi bo - Xuong tai che", "KG", "0.9000", "10.0000", "0.1000", "158.00", "164.00",
     "Hat PP tai sinh tu phe lieu noi bo, da qua loc va say."),
    ("RPE-PELLET-01", "Hat nhua PE tai sinh", "RECYCLED", "RECYCLED_MATERIALS", "PE", "Tai sinh noi bo",
     "Noi bo - Xuong tai che", "KG", "0.9400", "1.5000", "0.1000", "120.00", "130.00",
     "Hat PE tai sinh tu phe lieu noi bo."),
    ("PKG-PEBAG-50KG", "Bao PE dung hat nhua 50kg", "PACKAGING", "PACKAGING_MATERIALS", None, "Bao bi",
     "Bao Bi An Phat", "CAI", None, None, None, None, None,
     "Bao PE dung dong goi hat nhua, tai trong 50kg."),
    ("PKG-CARTON-5L", "Thung carton 5 lop", "PACKAGING", "PACKAGING_MATERIALS", None, "Bao bi",
     "Bao Bi An Phat", "CAI", None, None, None, None, None,
     "Thung carton 5 lop dung dong goi thanh pham."),
]


def seed_materials(node_ids: dict[str, int]) -> None:
    created = 0
    for (code, name, mtype, node_code, base_resin, group, supplier, unit,
         density, mfi, moisture, melt_min, melt_max, description) in MATERIALS:
        payload = {
            "material_code": code,
            "material_name": name,
            "material_type": mtype,
            "material_node_id": node_ids[node_code],
            "base_resin": base_resin,
            "material_group": group,
            "supplier": supplier,
            "unit": unit,
            "status": "ACTIVE",
            "density": density,
            "mfi": mfi,
            "moisture_content": moisture,
            "melting_point_min": melt_min,
            "melting_point_max": melt_max,
            "description": description,
            "created_by": ACTOR,
        }
        response = requests.post(f"{API}/materials", json=payload, timeout=15)
        if response.status_code not in (200, 201):
            raise RuntimeError(f"Failed to create material {code}: {response.status_code} {response.text}")
        created += 1
    print(f"Materials created: {created}.")


def main() -> None:
    reset_existing()
    node_types = seed_node_types()
    print(f"Node types ready: {node_types}")
    node_ids = seed_nodes(node_types)
    print(f"Category nodes created: {len(node_ids)}.")
    seed_materials(node_ids)
    print("Material Master demo data (VCC Plant 1 hierarchy) seeded successfully.")


if __name__ == "__main__":
    main()
