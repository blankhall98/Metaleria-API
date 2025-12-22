# app/services/firebase_storage.py
import os
import re
import uuid
from pathlib import Path

import firebase_admin
from firebase_admin import credentials, storage

from app.core.config import get_settings


_BUCKET = None


def _ensure_bucket():
    global _BUCKET
    if _BUCKET is not None:
        return _BUCKET

    settings = get_settings()
    cred_path = Path(settings.FIREBASE_CREDENTIALS_FILE)
    if not cred_path.exists():
        raise FileNotFoundError(f"No se encontro el archivo de credenciales: {cred_path}")

    if not firebase_admin._apps:
        cred = credentials.Certificate(str(cred_path))
        firebase_admin.initialize_app(
            cred,
            {"storageBucket": settings.FIREBASE_BUCKET},
        )

    _BUCKET = storage.bucket()
    return _BUCKET


def _safe_filename(filename: str, content_type: str | None) -> str:
    name, ext = os.path.splitext(filename or "")
    if not ext and content_type:
        if content_type == "image/jpeg":
            ext = ".jpg"
        elif content_type == "image/png":
            ext = ".png"
        elif content_type == "image/webp":
            ext = ".webp"
        else:
            ext = ""
    safe_base = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-") or "evidencia"
    return f"{safe_base}{ext.lower()}"


def upload_image(*, content: bytes, filename: str, content_type: str | None, folder: str) -> str:
    bucket = _ensure_bucket()
    safe_name = _safe_filename(filename, content_type)
    object_name = f"{folder}/{uuid.uuid4().hex}_{safe_name}"
    blob = bucket.blob(object_name)
    blob.cache_control = "public, max-age=31536000"
    blob.upload_from_string(content, content_type=content_type or "application/octet-stream")
    blob.make_public()
    return blob.public_url


def upload_file(*, content: bytes, filename: str, content_type: str | None, folder: str) -> str:
    bucket = _ensure_bucket()
    safe_name = _safe_filename(filename, content_type)
    object_name = f"{folder}/{uuid.uuid4().hex}_{safe_name}"
    blob = bucket.blob(object_name)
    blob.cache_control = "public, max-age=31536000"
    blob.upload_from_string(content, content_type=content_type or "application/octet-stream")
    blob.make_public()
    return blob.public_url
