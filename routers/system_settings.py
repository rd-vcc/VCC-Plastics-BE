from fastapi import APIRouter, HTTPException

from database.db import get_vcc_plastics_connection
from schemas.system_settings import DefaultLanguageUpdate


router = APIRouter(
    prefix="/api/system-settings",
    tags=["System Settings"],
)

DEFAULT_LANGUAGE_KEY = "default_language"
FALLBACK_LANGUAGE = "vi"
SUPPORTED_LANGUAGES = ("vi", "en", "ja")


def _language_response(default_language: str) -> dict:
    return {
        "default_language": default_language,
        "supported_languages": list(SUPPORTED_LANGUAGES),
    }


@router.get("/language")
def get_default_language():
    """Return the global language used when a client has no temporary choice."""
    conn = get_vcc_plastics_connection()
    cur = conn.cursor(dictionary=True)

    try:
        cur.execute(
            """
            SELECT setting_value
            FROM system_settings
            WHERE setting_key = %s
            LIMIT 1
            """,
            (DEFAULT_LANGUAGE_KEY,),
        )
        setting = cur.fetchone()
        language = str(setting["setting_value"]).strip().lower() if setting else ""

        if language not in SUPPORTED_LANGUAGES:
            language = FALLBACK_LANGUAGE

        return _language_response(language)
    finally:
        cur.close()
        conn.close()


@router.put("/language")
def update_default_language(payload: DefaultLanguageUpdate):
    """Update the global default language; no per-user preference is stored."""
    language = payload.default_language
    conn = get_vcc_plastics_connection()
    cur = conn.cursor()

    try:
        cur.execute(
            """
            INSERT INTO system_settings (setting_key, setting_value)
            VALUES (%s, %s)
            ON DUPLICATE KEY UPDATE setting_value = VALUES(setting_value)
            """,
            (DEFAULT_LANGUAGE_KEY, language),
        )
        conn.commit()
        return {
            "message": "Default language updated",
            **_language_response(language),
        }
    except Exception as exc:
        conn.rollback()
        raise HTTPException(
            status_code=500,
            detail="Không thể cập nhật ngôn ngữ mặc định của hệ thống.",
        ) from exc
    finally:
        cur.close()
        conn.close()

