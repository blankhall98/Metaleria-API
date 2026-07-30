import logging

from fastapi import FastAPI, Request, Depends, Form
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import Nota, NotaEstado

from app.api.router import api_router
from app.web.admin import router as admin_web_router
from app.web.worker import router as worker_web_router
from app.web.files import router as files_web_router

from app.core.config import get_settings
from app.db.deps import get_db
from app.services.auth import MotivoRechazo, autenticar, normalizar_username
from app.web.template_utils import create_templates

templates = create_templates()

logger = logging.getLogger(__name__)

# Un mensaje por motivo: el genérico obligaba a la clienta a adivinar si la
# cuenta estaba dada de baja, mal escrita o con otra contraseña.
_MENSAJES_RECHAZO = {
    MotivoRechazo.usuario_inactivo: (
        "Tu cuenta está dada de baja. Pídele a un administrador que la reactive."
    ),
    MotivoRechazo.usuario_ambiguo: (
        "Hay más de una cuenta con ese nombre. Escríbelo tal como te lo entregaron, "
        "respetando mayúsculas y minúsculas."
    ),
}
_MENSAJE_RECHAZO_GENERICO = "Usuario o contraseña inválidos."


def _mensaje_rechazo(motivo: "MotivoRechazo | None") -> str:
    return _MENSAJES_RECHAZO.get(motivo, _MENSAJE_RECHAZO_GENERICO)


def _get_session_user(request: Request) -> dict | None:
    return request.session.get("user")


def _home_resumen(db: Session, notas_revision_count: int) -> dict:
    """Operating figures for the dashboard.

    Read-only aggregates over approved notes; a failure here must never take
    the home page down, so the caller gets an empty dict instead.
    """
    from datetime import date, datetime, timedelta
    from decimal import Decimal
    from sqlalchemy import func

    from app.models import Inventario
    from app.models.pricing import TipoOperacion

    try:
        saldo = Nota.total_monto - Nota.monto_pagado
        aprobadas = Nota.estado == NotaEstado.aprobada

        def _sum_saldo(tipo: TipoOperacion) -> Decimal:
            value = (
                db.query(func.coalesce(func.sum(saldo), 0))
                .filter(aprobadas, Nota.tipo_operacion == tipo, saldo > 0)
                .scalar()
            )
            return Decimal(str(value or 0))

        hoy = date.today()
        inicio_dia = datetime(hoy.year, hoy.month, hoy.day)

        vencidas = (
            db.query(Nota)
            .filter(
                aprobadas,
                saldo > 0,
                Nota.fecha_caducidad_pago.isnot(None),
                Nota.fecha_caducidad_pago < hoy,
            )
            .count()
        )

        por_vencer = (
            db.query(Nota)
            .filter(
                aprobadas,
                saldo > 0,
                Nota.fecha_caducidad_pago.isnot(None),
                Nota.fecha_caducidad_pago >= hoy,
                Nota.fecha_caducidad_pago <= hoy + timedelta(days=5),
            )
            .count()
        )

        notas_hoy = db.query(Nota).filter(Nota.created_at >= inicio_dia).count()

        inventario_kg = db.query(func.coalesce(func.sum(Inventario.stock_actual), 0)).scalar()

        return {
            "en_revision": notas_revision_count,
            "notas_hoy": notas_hoy,
            "por_pagar": _sum_saldo(TipoOperacion.compra),
            "por_cobrar": _sum_saldo(TipoOperacion.venta),
            "vencidas": vencidas,
            "por_vencer": por_vencer,
            "inventario_kg": Decimal(str(inventario_kg or 0)),
        }
    except Exception:  # noqa: BLE001 - the dashboard degrades, it does not fail
        return {}


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.PROJECT_NAME,
        version="0.1.0",
        debug=settings.DEBUG,
        description="MVP de sistema de notas de pesaje, inventario y contabilidad para metalería.",
    )

    app.state.settings = settings

    # Middleware de sesión
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.SECRET_KEY,
        session_cookie="metalleria_session",
        same_site="lax",
        https_only=False,  # en prod lo subimos a True si hay HTTPS
    )

    # Static
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    # API
    app.include_router(api_router, prefix="/api")

    # Web Admin
    app.include_router(admin_web_router)
    # Web Worker
    app.include_router(worker_web_router)
    # Web Files (uploads)
    app.include_router(files_web_router)

    # Root JSON
    @app.get("/")
    async def root():
        return RedirectResponse(url="/web", status_code=307)

    # Home web
    @app.get("/web")
    async def web_home(request: Request):
        user = _get_session_user(request)
        notas_revision_count = 0
        resumen: dict = {}

        if user and user.get("rol") in ("admin", "super_admin", "visor"):
            db = SessionLocal()
            try:
                notas_revision_count = (
                    db.query(Nota).filter(Nota.estado == NotaEstado.en_revision).count()
                )
                resumen = _home_resumen(db, notas_revision_count)
            finally:
                db.close()

        return templates.TemplateResponse(
            "home.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": user,
                "notas_revision_count": notas_revision_count,
                "resumen": resumen,
            },
        )

    # Login GET
    @app.get("/web/login")
    async def web_login_get(request: Request):
        user = _get_session_user(request)
        if user:
            return RedirectResponse(url="/web", status_code=303)

        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": None,
                "error": None,
            },
        )

    # Login POST
    @app.post("/web/login")
    async def web_login_post(
        request: Request,
        username: str = Form(...),
        password: str = Form(...),
        db: Session = Depends(get_db),
    ):
        user_obj, motivo = autenticar(db=db, username=username, password=password)

        if not user_obj:
            # Sin esto, un acceso fallido es indistinguible de otro y no hay
            # forma de atender el reporte de "mi usuario y clave son correctos".
            # Va como warning a propósito: uvicorn no instala un handler para
            # este logger y el handler de último recurso descarta todo lo que
            # esté por debajo de warning.
            logger.warning(
                "Acceso rechazado para %r: %s",
                normalizar_username(username),
                motivo.value if motivo else "desconocido",
            )
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "env": settings.ENV,
                    "user": None,
                    "error": _mensaje_rechazo(motivo),
                },
                status_code=400,
            )

        # Guardar datos mínimos en sesión
        request.session["user"] = {
            "id": user_obj.id,
            "username": user_obj.username,
            "rol": user_obj.rol.value if hasattr(user_obj.rol, "value") else str(user_obj.rol),
            "sucursal_id": user_obj.sucursal_id,
        }

        return RedirectResponse(url="/web", status_code=303)

    # Logout
    @app.get("/web/logout")
    async def web_logout(request: Request):
        request.session.pop("user", None)
        return RedirectResponse(url="/web/login", status_code=303)

    # Health-check para infra
    @app.get("/healthz", tags=["health"])
    async def healthz():
        return {
            "status": "ok",
            "env": settings.ENV,
            "version": "0.1.0",
        }

    return app


# 👇 IMPORTANTE: que app sea de tipo FastAPI (no None)
app = create_app()


@app.middleware("http")
async def admin_notes_badge(request, call_next):
    """
    Middleware para exponer el número de notas en revisión en request.state
    para admins/super_admins (se usa en navbar).
    """
    request.state.notas_revision_count = 0
    user = None
    try:
        user = request.session.get("user")
    except Exception:
        user = None

    if user and user.get("rol") in ("admin", "super_admin"):
        db = SessionLocal()
        try:
            request.state.notas_revision_count = db.query(Nota).filter(Nota.estado == NotaEstado.en_revision).count()
        finally:
            db.close()

    response = await call_next(request)
    return response

