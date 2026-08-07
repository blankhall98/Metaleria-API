# app/web/perfil.py
import logging

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.security import hash_password, verify_password
from app.db.deps import get_db
from app.models import User
from app.services.auth import normalizar_password
from app.services.firebase_storage import resolve_image_content_type, upload_image
from app.web.template_utils import create_templates

templates = create_templates()
settings = get_settings()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web/perfil", tags=["web-perfil"])


def _require_user(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=403, detail="Necesitas iniciar sesión.")
    return user


def _load_perfil(db: Session, session_user: dict) -> User | None:
    return db.query(User).filter(User.id == session_user.get("id")).first()


def _render(request: Request, session_user: dict, perfil: User, *, error: str | None = None,
            ok: bool = False, status_code: int = 200):
    return templates.TemplateResponse(
        "perfil.html",
        {
            "request": request,
            "user": session_user,
            "perfil": perfil,
            "error": error,
            "ok": ok,
        },
        status_code=status_code,
    )


@router.get("")
async def perfil_get(request: Request, db: Session = Depends(get_db)):
    session_user = _require_user(request)
    perfil = _load_perfil(db, session_user)
    if not perfil:
        request.session.pop("user", None)
        return RedirectResponse(url="/web/login", status_code=303)
    return _render(request, session_user, perfil, ok=request.query_params.get("ok") == "1")


@router.post("")
async def perfil_post(
    request: Request,
    nombre_completo: str = Form(...),
    password_actual: str = Form(""),
    password_nueva: str = Form(""),
    password_confirmar: str = Form(""),
    quitar_foto: str = Form(""),
    foto: UploadFile | None = File(None),
    db: Session = Depends(get_db),
):
    session_user = _require_user(request)
    perfil = _load_perfil(db, session_user)
    if not perfil:
        request.session.pop("user", None)
        return RedirectResponse(url="/web/login", status_code=303)

    nombre_completo = (nombre_completo or "").strip()
    if not nombre_completo:
        return _render(request, session_user, perfil,
                       error="El nombre completo no puede quedar vacío.", status_code=400)

    password_actual = normalizar_password(password_actual)
    password_nueva = normalizar_password(password_nueva)
    password_confirmar = normalizar_password(password_confirmar)

    if password_nueva or password_confirmar or password_actual:
        if not password_actual:
            return _render(request, session_user, perfil,
                           error="Para cambiar la contraseña, escribe primero tu contraseña actual.",
                           status_code=400)
        if not verify_password(password_actual, perfil.password_hash):
            return _render(request, session_user, perfil,
                           error="La contraseña actual no coincide.", status_code=400)
        if not password_nueva:
            return _render(request, session_user, perfil,
                           error="Escribe la nueva contraseña.", status_code=400)
        if password_nueva != password_confirmar:
            return _render(request, session_user, perfil,
                           error="La nueva contraseña y su confirmación no coinciden.", status_code=400)

    nueva_foto_url: str | None = None
    if foto is not None and (foto.filename or "").strip():
        content = await foto.read()
        resolved_content_type = resolve_image_content_type(foto.filename, foto.content_type)
        if not resolved_content_type:
            return _render(request, session_user, perfil,
                           error="La foto no parece ser una imagen válida (JPG, PNG o WebP).",
                           status_code=400)
        if not content:
            return _render(request, session_user, perfil,
                           error="La foto llegó vacía. Intenta elegirla de nuevo.", status_code=400)
        max_bytes = settings.FIREBASE_MAX_MB * 1024 * 1024
        if len(content) > max_bytes:
            return _render(request, session_user, perfil,
                           error=f"La foto es demasiado pesada (máximo {settings.FIREBASE_MAX_MB}MB).",
                           status_code=400)
        try:
            nueva_foto_url = upload_image(
                content=content,
                filename=foto.filename or "perfil",
                content_type=resolved_content_type,
                folder=f"perfiles/user_{perfil.id}",
            )
        except Exception:
            logger.exception(
                "Profile photo upload failed",
                extra={"user_id": perfil.id, "upload_name": foto.filename},
            )
            return _render(request, session_user, perfil,
                           error="No se pudo subir la foto al almacenamiento. Intenta de nuevo.",
                           status_code=500)

    perfil.nombre_completo = nombre_completo
    if password_nueva:
        perfil.password_hash = hash_password(password_nueva)
    if nueva_foto_url:
        perfil.foto_url = nueva_foto_url
    elif quitar_foto == "1":
        perfil.foto_url = None
    db.commit()

    session_user = dict(session_user)
    session_user["foto_url"] = perfil.foto_url
    request.session["user"] = session_user

    return RedirectResponse(url="/web/perfil?ok=1", status_code=303)
