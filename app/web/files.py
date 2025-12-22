# app/web/files.py
from fastapi import APIRouter, File, HTTPException, Request, UploadFile

from app.core.config import get_settings
from app.services.firebase_storage import upload_image


router = APIRouter(prefix="/web/files", tags=["web-files"])
settings = get_settings()


def _require_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=403, detail="Necesitas iniciar sesion.")
    return user


@router.post("/upload")
async def upload_evidencia(request: Request, file: UploadFile = File(...)):
    user = _require_user(request)
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Solo se permiten imagenes.")

    content = await file.read()
    max_bytes = settings.FIREBASE_MAX_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise HTTPException(status_code=400, detail=f"Imagen demasiado pesada (max {settings.FIREBASE_MAX_MB}MB).")

    try:
        url = upload_image(
            content=content,
            filename=file.filename or "evidencia",
            content_type=file.content_type,
            folder=f"evidencias/user_{user.get('id')}",
        )
    except Exception:
        raise HTTPException(status_code=500, detail="No se pudo subir la imagen.")

    return {"url": url}
