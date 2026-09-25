"""Image and document upload for master data (System Configuration): products, machines, molds, materials, tools...

The browser sends the file as base64 JSON (no multipart dependency). The file type is checked by its
magic bytes, not by the file name, and limited to MAX_BYTES. Files are stored under
storage/uploads/images/<category>/<uuid>.<ext> and served by the /uploads static mount (see main.py).
The returned url is relative ("/uploads/images/..."); the frontend prefixes the API base.
"""

import base64
import binascii
import re
import uuid
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter(prefix="/api/files", tags=["Files"])

UPLOAD_ROOT = Path(__file__).resolve().parents[3] / "storage" / "uploads"
IMAGE_ROOT = UPLOAD_ROOT / "images"
MAX_BYTES = 5 * 1024 * 1024
CATEGORIES = {"product", "machine", "equipment", "mold", "material", "production_tool", "measuring_equipment", "factory", "location", "user", "maintenance", "quality", "other"}
SIGNATURES = [
    (b"\x89PNG\r\n\x1a\n", "png"),
    (b"\xff\xd8\xff", "jpg"),
    (b"GIF87a", "gif"),
    (b"GIF89a", "gif"),
]


class ImageIn(BaseModel):
    category: str = Field(min_length=1, max_length=40)
    filename: str | None = Field(default=None, max_length=255)
    data: str = Field(min_length=10)  # base64, with or without a data: URL prefix


def _detect(raw: bytes):
    for sig, ext in SIGNATURES:
        if raw.startswith(sig):
            return ext
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "webp"
    return None


@router.post("/images", status_code=201)
def upload_image(payload: ImageIn):
    category = payload.category.strip().lower()
    if category not in CATEGORIES:
        raise HTTPException(status_code=400, detail="Unknown image category.")
    data = re.sub(r"^data:[^;]+;base64,", "", payload.data.strip())
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="The file is not valid base64 data.") from exc
    if not raw:
        raise HTTPException(status_code=400, detail="The file is empty.")
    if len(raw) > MAX_BYTES:
        raise HTTPException(status_code=413, detail="The image is larger than 5 MB.")
    ext = _detect(raw)
    if not ext:
        raise HTTPException(status_code=400, detail="Only PNG, JPG, WEBP or GIF images are allowed.")
    folder = IMAGE_ROOT / category
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    (folder / name).write_bytes(raw)
    return {"url": f"/uploads/images/{category}/{name}", "size": len(raw), "type": ext}


# ---------------------------------------------------------------------------
# Documents (mold drawings, BOM, layouts, 3D models...): storage/uploads/documents/<category>/<uuid>.<ext>
# ---------------------------------------------------------------------------

DOC_ROOT = UPLOAD_ROOT / "documents"
DOC_MAX_BYTES = 20 * 1024 * 1024
DOC_CATEGORIES = {"mold", "machine", "product", "maintenance", "quality", "measuring_equipment", "other"}
OFFICE_EXT = {"docx", "xlsx", "pptx"}


class DocumentIn(BaseModel):
    category: str = Field(min_length=1, max_length=40)
    filename: str = Field(min_length=1, max_length=255)
    data: str = Field(min_length=10)


def _detect_doc(raw: bytes, filename: str):
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if raw.startswith(b"%PDF"):
        return "pdf"
    if raw.startswith(b"PK") and ext in OFFICE_EXT:
        return ext
    if raw.startswith(b"AC10") and ext == "dwg":
        return "dwg"
    if raw[:12].startswith(b"ISO-10303-21") and ext in {"stp", "step"}:
        return ext
    return _detect(raw)


@router.post("/documents", status_code=201)
def upload_document(payload: DocumentIn):
    category = payload.category.strip().lower()
    if category not in DOC_CATEGORIES:
        raise HTTPException(status_code=400, detail="Unknown document category.")
    data = re.sub(r"^data:[^;]*;base64,", "", payload.data.strip())
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=400, detail="The file is not valid base64 data.") from exc
    if not raw:
        raise HTTPException(status_code=400, detail="The file is empty.")
    if len(raw) > DOC_MAX_BYTES:
        raise HTTPException(status_code=413, detail="The file is larger than 20 MB.")
    ext = _detect_doc(raw, payload.filename)
    if not ext:
        raise HTTPException(status_code=400, detail="Allowed files: PDF, Word / Excel / PowerPoint (docx, xlsx, pptx), DWG, STEP, PNG, JPG, WEBP, GIF.")
    folder = DOC_ROOT / category
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    (folder / name).write_bytes(raw)
    return {"url": f"/uploads/documents/{category}/{name}", "size": len(raw), "type": ext}
