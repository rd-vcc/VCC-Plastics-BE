import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parents[1]
load_dotenv(BASE_DIR / ".env")

APP_NAME = os.getenv("APP_NAME", "VCC Plastics API")
APP_VERSION = os.getenv("APP_VERSION", "1.0.0")
VCC_GROUP_API = os.getenv("VCC_GROUP_API", "http://10.73.132.100:5000").rstrip("/")
CORS_ALLOW_ORIGINS = os.getenv("CORS_ALLOW_ORIGINS", "*")
