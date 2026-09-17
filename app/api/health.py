"""Root and health-check endpoints, ported from ``main.py``'s originals."""

from fastapi import APIRouter

from app.core.config import APP_NAME, APP_VERSION

router = APIRouter()


@router.get("/")
def root():
    return {"message": APP_NAME, "version": APP_VERSION}


@router.get("/health")
def health():
    return {"status": "ok"}
