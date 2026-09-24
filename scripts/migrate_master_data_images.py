"""One-off migration: image fields for master data (System Configuration), from the mockup review.

Objects shown with a picture in the mockups and where the image is stored:
    products.image_url                    product photo / 3D render (1.6, 1.7, 1.8, 2.2, 5.3, 7.1, 10.2)
    equipment.image_url      (existing)   injection machine photo (1.6, 1.7, 2.2, 6.4, 6.7, 10.3, 10.14)
    molds.image_url          (existing)   mold photo / 3D render (1.6, 1.7, 2.1, 3.1, 3.3, 3.5, 3.6, 10.4)
    materials.image_url      (existing)   material photo (7.2, 10.5)
    production_tools.image_url            production tool photo (6.6, 10.6)
    measuring_equipment.image_url         measuring equipment photo (10.7)
    factory_structure_nodes.image_url     factory / area layout image (1.1, 1.2, 2.1)
Files themselves are uploaded through POST /api/files/images (storage/uploads/images/...).

Run with:

    python -m scripts.migrate_master_data_images
"""

from app.core.database import get_vcc_plastics_connection

TABLES = ["products", "production_tools", "measuring_equipment", "factory_structure_nodes"]


def main():
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()
    try:
        for table in TABLES:
            cur.execute(f"SHOW COLUMNS FROM {table}")
            if "image_url" not in {row[0] for row in cur.fetchall()}:
                cur.execute(f"ALTER TABLE {table} ADD COLUMN image_url VARCHAR(1000) NULL")
                print("added image_url to", table)
        conn.commit()
    finally:
        cur.close()
        conn.close()


if __name__ == "__main__":
    main()
