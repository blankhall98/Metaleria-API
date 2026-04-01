# app/web/files.py
import logging

from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.core.config import get_settings
from app.services.firebase_storage import resolve_image_content_type, upload_image


router = APIRouter(prefix="/web/files", tags=["web-files"])
settings = get_settings()
logger = logging.getLogger(__name__)


def _require_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=403, detail="Necesitas iniciar sesion.")
    return user


@router.post("/upload")
async def upload_evidencia(request: Request, file: UploadFile = File(...)):
    user = _require_user(request)
    content = await file.read()
    resolved_content_type = resolve_image_content_type(file.filename, file.content_type)
    if not resolved_content_type:
        logger.warning(
            "Evidence upload rejected: invalid content type",
            extra={
                "user_id": user.get("id"),
                "upload_name": file.filename,
                "content_type": file.content_type,
            },
        )
        raise HTTPException(status_code=400, detail="El archivo no parece ser una imagen valida.")
    if not content:
        logger.warning(
            "Evidence upload rejected: empty file",
            extra={"user_id": user.get("id"), "upload_name": file.filename},
        )
        raise HTTPException(status_code=400, detail="La imagen llego vacia. Intenta elegirla de nuevo.")
    max_bytes = settings.FIREBASE_MAX_MB * 1024 * 1024
    if len(content) > max_bytes:
        logger.warning(
            "Evidence upload rejected: file too large",
            extra={
                "user_id": user.get("id"),
                "upload_name": file.filename,
                "content_type": resolved_content_type,
                "size_bytes": len(content),
                "max_bytes": max_bytes,
            },
        )
        raise HTTPException(status_code=400, detail=f"Imagen demasiado pesada (max {settings.FIREBASE_MAX_MB}MB).")

    try:
        url = upload_image(
            content=content,
            filename=file.filename or "evidencia",
            content_type=resolved_content_type,
            folder=f"evidencias/user_{user.get('id')}",
        )
    except Exception:
        logger.exception(
            "Evidence upload failed",
            extra={
                "user_id": user.get("id"),
                "upload_name": file.filename,
                "content_type": resolved_content_type,
                "size_bytes": len(content),
            },
        )
        raise HTTPException(status_code=500, detail="No se pudo subir la imagen al almacenamiento. Intenta de nuevo.")

    return {"url": url}
