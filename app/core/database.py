import mysql.connector

from app.core.config import DB_HOST, DB_PASS, DB_PORT, DB_USER, VCC_PLASTICS_SCHEMA

try:
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
except ImportError:  # pragma: no cover - sqlalchemy is a required dependency, guarded for safety
    create_engine = None
    sessionmaker = None


def _connect(schema: str):
    return mysql.connector.connect(
        host=DB_HOST,
        port=DB_PORT,
        user=DB_USER,
        password=DB_PASS,
        database=schema,
        auth_plugin="mysql_native_password",
        use_pure=True,
        charset="utf8mb4",
    )


def get_connection():
    """Ket noi mac dinh toi schema VCC Plastics."""
    return _connect(VCC_PLASTICS_SCHEMA)


def get_vcc_plastics_connection():
    """Alias ro nghia cho ket noi schema VCC Plastics."""
    return _connect(VCC_PLASTICS_SCHEMA)


# --------------------------------------------------------------------------
# SQLAlchemy engine/sessionmaker, reserved for Alembic and future incremental
# ORM adoption (see app/models). Nothing in the existing, ported endpoints
# uses this - they all continue to use the raw mysql.connector helpers above,
# exactly like the original flat-layout code did.
# --------------------------------------------------------------------------
SQLALCHEMY_DATABASE_URL = (
    f"mysql+mysqlconnector://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{VCC_PLASTICS_SCHEMA}"
)

engine = create_engine(SQLALCHEMY_DATABASE_URL, pool_pre_ping=True) if create_engine else None
SessionLocal = (
    sessionmaker(autocommit=False, autoflush=False, bind=engine) if sessionmaker and engine else None
)
