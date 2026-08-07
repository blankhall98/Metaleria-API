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
from app.web.perfil import router as perfil_web_router

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


def _home_resumen(
    db: Session,
    notas_revision_count: int,
    actividad_sucursal_id: int | None = None,
) -> dict:
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
        # Punto 7 (fase 2): el resumen usa el saldo EFECTIVO de cada nota
        # (fórmula canónica + ajustes de saldo + neteo por socio vinculado),
        # igual que la lista de notas. Antes solo restaba total − pagado y las
        # notas ya neteadas inflaban "por pagar" y aparecían como vencidas.
        from app.services import note_service

        hoy = date.today()
        inicio_dia = datetime(hoy.year, hoy.month, hoy.day)
        limite_alerta = hoy + timedelta(days=5)

        notas_aprobadas = db.query(Nota).filter(Nota.estado == NotaEstado.aprobada).all()
        balances = note_service.build_effective_note_balance_map(db, notas_aprobadas)

        # Punto 8 (fase 2): las notas de un par cliente↔proveedor vinculado
        # contribuyen al saldo de CLIENTES con su signo (las compras al par
        # restan de por cobrar), nunca al bucket de proveedores.
        par_keys: set[tuple[str, int]] = set()
        for nota in notas_aprobadas:
            if nota.proveedor_id:
                par_keys.add(("proveedor", nota.proveedor_id))
            if nota.cliente_id:
                par_keys.add(("cliente", nota.cliente_id))
        link_by_prov, link_by_cli = note_service._linked_partner_maps(db, par_keys)

        por_pagar = Decimal("0")
        por_cobrar = Decimal("0")
        vencidas = 0
        por_vencer = 0
        for nota in notas_aprobadas:
            balance = balances.get(nota.id) or {}
            pendiente = Decimal(str(balance.get("saldo_pendiente") or 0))
            if pendiente <= Decimal("0"):
                continue
            es_de_par = (
                (nota.proveedor_id and nota.proveedor_id in link_by_prov)
                or (nota.cliente_id and nota.cliente_id in link_by_cli)
            )
            if es_de_par:
                if nota.tipo_operacion == TipoOperacion.venta:
                    por_cobrar += pendiente
                elif nota.tipo_operacion == TipoOperacion.compra:
                    por_cobrar -= pendiente
            elif nota.tipo_operacion == TipoOperacion.compra:
                por_pagar += pendiente
            elif nota.tipo_operacion == TipoOperacion.venta:
                por_cobrar += pendiente
            if nota.fecha_caducidad_pago is not None:
                if nota.fecha_caducidad_pago < hoy:
                    vencidas += 1
                elif nota.fecha_caducidad_pago <= limite_alerta:
                    por_vencer += 1

        notas_hoy = db.query(Nota).filter(Nota.created_at >= inicio_dia).count()

        inventario_kg = db.query(func.coalesce(func.sum(Inventario.stock_actual), 0)).scalar()

        # Actividad reciente: el panel no solo dice cuánto, también qué pasó.
        # El filtro de sucursal se aplica aquí, no en el cliente: "las últimas
        # cinco de esa sucursal", no un recorte de las cinco globales.
        from app.models import Cliente, Proveedor, Sucursal

        recientes_query = db.query(Nota)
        if actividad_sucursal_id:
            recientes_query = recientes_query.filter(Nota.sucursal_id == actividad_sucursal_id)
        recientes = recientes_query.order_by(Nota.created_at.desc()).limit(5).all()
        sucursales_map = {s.id: s.nombre for s in db.query(Sucursal).all()}
        prov_ids = {n.proveedor_id for n in recientes if n.proveedor_id}
        cli_ids = {n.cliente_id for n in recientes if n.cliente_id}
        prov_map = (
            {p.id: p.nombre_completo for p in db.query(Proveedor).filter(Proveedor.id.in_(prov_ids)).all()}
            if prov_ids else {}
        )
        cli_map = (
            {c.id: c.nombre_completo for c in db.query(Cliente).filter(Cliente.id.in_(cli_ids)).all()}
            if cli_ids else {}
        )
        actividad = []
        for n in recientes:
            folio = note_service.format_folio(
                sucursal_id=n.sucursal_id,
                tipo_operacion=n.tipo_operacion,
                folio_seq=n.folio_seq,
            )
            if not folio:
                folio = "Pendiente" if n.estado in (NotaEstado.borrador, NotaEstado.en_revision) else "—"
            actividad.append({
                "id": n.id,
                "folio": folio,
                "partner": prov_map.get(n.proveedor_id) or cli_map.get(n.cliente_id) or "—",
                "tipo": n.tipo_operacion.value if n.tipo_operacion else "",
                "estado": n.estado.value if n.estado else "",
                "total": n.total_monto,
                "fecha": n.created_at,
                "sucursal": sucursales_map.get(n.sucursal_id, "—"),
            })

        return {
            "en_revision": notas_revision_count,
            "notas_hoy": notas_hoy,
            "por_pagar": por_pagar,
            "por_cobrar": por_cobrar,
            "vencidas": vencidas,
            "por_vencer": por_vencer,
            "inventario_kg": Decimal(str(inventario_kg or 0)),
            "actividad": actividad,
        }
    except Exception:  # noqa: BLE001 - the dashboard degrades, it does not fail
        return {}


def _worker_home_resumen(db: Session, user_id: int | None) -> dict:
    """El panel del trabajador: sus pendientes y sus últimas notas.

    Solo lecturas; si algo falla el home degrada a la bienvenida simple.
    """
    try:
        from app.models import User

        usuario = db.query(User).filter(User.id == user_id).first() if user_id else None
        nombre_pila = ""
        if usuario and usuario.nombre_completo:
            nombre_pila = usuario.nombre_completo.strip().split(" ")[0]

        propias = db.query(Nota).filter(Nota.trabajador_id == user_id)
        borradores = propias.filter(Nota.estado == NotaEstado.borrador).count()
        en_revision = propias.filter(Nota.estado == NotaEstado.en_revision).count()
        aprobadas = propias.filter(Nota.estado == NotaEstado.aprobada).count()
        recientes = propias.order_by(Nota.created_at.desc()).limit(5).all()

        return {
            "nombre_pila": nombre_pila,
            "borradores": borradores,
            "en_revision": en_revision,
            "aprobadas": aprobadas,
            "recientes": [
                {
                    "id": n.id,
                    "estado": n.estado.value if n.estado else "",
                    "tipo": n.tipo_operacion.value if n.tipo_operacion else "",
                    "total": n.total_monto,
                    "fecha": n.created_at,
                }
                for n in recientes
            ],
        }
    except Exception:  # noqa: BLE001 - el home degrada, no falla
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
    # Web Perfil (cuenta propia)
    app.include_router(perfil_web_router)

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
        worker_resumen: dict = {}
        nombre_pila = ""
        sucursales_activas: list = []
        actividad_sucursal_id: int | None = None

        if user and user.get("rol") in ("admin", "super_admin", "visor"):
            try:
                actividad_sucursal_id = int(request.query_params.get("actividad_sucursal") or 0) or None
            except ValueError:
                actividad_sucursal_id = None
            db = SessionLocal()
            try:
                notas_revision_count = (
                    db.query(Nota).filter(Nota.estado == NotaEstado.en_revision).count()
                )
                resumen = _home_resumen(db, notas_revision_count, actividad_sucursal_id)
                from app.models import Sucursal, SucursalStatus

                sucursales_activas = (
                    db.query(Sucursal)
                    .filter(Sucursal.estado == SucursalStatus.activa)
                    .order_by(Sucursal.nombre)
                    .all()
                )
            finally:
                db.close()
        elif user and user.get("rol") == "trabajador":
            db = SessionLocal()
            try:
                worker_resumen = _worker_home_resumen(db, user.get("id"))
                nombre_pila = worker_resumen.pop("nombre_pila", "") or ""
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
                "worker_resumen": worker_resumen,
                "nombre_pila": nombre_pila,
                "sucursales_activas": sucursales_activas,
                "actividad_sucursal_id": actividad_sucursal_id,
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
            "foto_url": user_obj.foto_url,
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

