import os
import mysql.connector

VCC_PLASTICS_SCHEMA = os.getenv("VCC_PLASTICS_SCHEMA", "vcc_plastics")


def _connect(schema: str):
    return mysql.connector.connect(
        host=os.getenv("DB_HOST", "127.0.0.1"),
        port=int(os.getenv("DB_PORT", "3306")),
        user=os.getenv("DB_USER", "root"),
        password=os.getenv("DB_PASS", "1234"),
        database=schema,
        auth_plugin="mysql_native_password",
        use_pure=True,
        charset="utf8mb4",
    )


def get_connection():
    """Kết nối mặc định tới schema VCC Plastics."""
    return _connect(VCC_PLASTICS_SCHEMA)


def get_vcc_plastics_connection():
    """Alias rõ nghĩa cho kết nối schema VCC Plastics."""
    return _connect(VCC_PLASTICS_SCHEMA)
