from fastapi import FastAPI, Request, Depends, Form
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
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
from app.services.auth import authenticate_user

templates = Jinja2Templates(directory="app/templates")


def _get_session_user(request: Request) -> dict | None:
    return request.session.get("user")


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
        if user and user.get("rol") in ("admin", "super_admin"):
            db = SessionLocal()
            try:
                notas_revision_count = db.query(Nota).filter(Nota.estado == NotaEstado.en_revision).count()
            finally:
                db.close()
        return templates.TemplateResponse(
            "home.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": user,
                "notas_revision_count": notas_revision_count,
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
        user_obj = authenticate_user(db=db, username=username, password=password)

        if not user_obj:
            return templates.TemplateResponse(
                "login.html",
                {
                    "request": request,
                    "env": settings.ENV,
                    "user": None,
                    "error": "Usuario o contraseña inválidos, o usuario inactivo.",
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

