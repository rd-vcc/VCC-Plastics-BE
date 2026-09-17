import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[2]
load_dotenv(BASE_DIR / ".env")

APP_NAME = os.getenv("APP_NAME", "VCC Plastics API")
APP_VERSION = os.getenv("APP_VERSION", "1.0.0")
VCC_GROUP_API = os.getenv("VCC_GROUP_API", "http://10.73.132.100:5000").rstrip("/")
CORS_ALLOW_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "*")

DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
DB_PORT = int(os.getenv("DB_PORT", "3306"))
DB_USER = os.getenv("DB_USER", "root")
DB_PASS = os.getenv("DB_PASS", "1234")
VCC_PLASTICS_SCHEMA = os.getenv("VCC_PLASTICS_SCHEMA", "vcc_plastics")
