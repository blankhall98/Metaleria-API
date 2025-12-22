# app/web/admin.py
import io
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import or_
from decimal import Decimal, InvalidOperation
from datetime import datetime, date, timedelta
from typing import Iterable, List

from app.core.config import get_settings
from app.core.security import hash_password
from app.db.deps import get_db
from app.models import (
    User,
    UserRole,
    UserStatus,
    Sucursal,
    SucursalStatus,
    Material,
    TablaPrecio,
    TipoOperacion,
    TipoCliente,
    Proveedor,
    ProveedorPlaca,
    Cliente,
    ClientePlaca,
    Nota,
    NotaEstado,
    NotaMaterial,
    Subpesaje,
    NotaPago,
    Inventario,
    MovimientoContable,
    Material,
    InventarioMovimiento,
)

from app.services.pricing_service import create_price_version
from app.services import note_service, invoice_service, contabilidad_report_service
from app.services.evidence_service import build_evidence_groups
from app.services.firebase_storage import upload_image

templates = Jinja2Templates(directory="app/templates")
settings = get_settings()

router = APIRouter(prefix="/web/admin", tags=["web-admin"])

_TRANSFER_RELATED_NOTE_RE = re.compile(r"Nota (?:entrada|salida) #(\d+)")
_FOLIO_QUERY_RE = re.compile(r"^\s*(\d+)[-_]([CV])[_-](\d+)\s*$", re.IGNORECASE)

def _signed_inventario_qty(mov: InventarioMovimiento) -> Decimal:
    qty = Decimal(str(mov.cantidad_kg or 0))
    if mov.tipo == "venta":
        return -abs(qty)
    if mov.tipo == "compra":
        return abs(qty)
    return qty

async def _upload_logo_file(upload: UploadFile | None, folder: str) -> str | None:
    if not upload or not upload.filename:
        return None
    if not upload.content_type or not upload.content_type.startswith("image/"):
        raise ValueError("El logo debe ser una imagen.")
    content = await upload.read()
    max_bytes = settings.FIREBASE_MAX_MB * 1024 * 1024
    if len(content) > max_bytes:
        raise ValueError(f"El logo supera el limite de {settings.FIREBASE_MAX_MB} MB.")
    try:
        return upload_image(
            content=content,
            filename=upload.filename,
            content_type=upload.content_type,
            folder=folder,
        )
    except Exception:
        raise ValueError("No se pudo subir el logo. Intenta nuevamente.")


def _parse_placas(raw: str | None) -> list[str]:
    if not raw:
        return []
    parts = []
    for line in raw.replace(",", "\n").splitlines():
        val = line.strip().upper()
        if val:
            parts.append(val)
    # dedupe preserving order
    seen = set()
    unique = []
    for p in parts:
        if p not in seen:
            unique.append(p)
            seen.add(p)
    return unique


def _set_proveedor_placas(db: Session, proveedor: Proveedor, placas_list: list[str]):
    proveedor.placas_rel.clear()
    proveedor.placas = placas_list[0] if placas_list else None
    for pl in placas_list:
        proveedor.placas_rel.append(ProveedorPlaca(placa=pl))
    db.add(proveedor)


def _set_cliente_placas(db: Session, cliente: Cliente, placas_list: list[str]):
    cliente.placas_rel.clear()
    cliente.placas = placas_list[0] if placas_list else None
    for pl in placas_list:
        cliente.placas_rel.append(ClientePlaca(placa=pl))
    db.add(cliente)


def _get_or_create_branch_cliente(db: Session, sucursal: Sucursal) -> Cliente:
    nombre = f"Sucursal {sucursal.nombre}"
    cliente = db.query(Cliente).filter(Cliente.nombre_completo == nombre).first()
    if cliente:
        return cliente
    cliente = Cliente(nombre_completo=nombre, activo=True)
    db.add(cliente)
    db.flush()
    return cliente


def _get_or_create_branch_proveedor(db: Session, sucursal: Sucursal) -> Proveedor:
    nombre = f"Sucursal {sucursal.nombre}"
    proveedor = db.query(Proveedor).filter(Proveedor.nombre_completo == nombre).first()
    if proveedor:
        return proveedor
    proveedor = Proveedor(nombre_completo=nombre, activo=True)
    db.add(proveedor)
    db.flush()
    return proveedor


def _is_transfer_note(
    db: Session,
    nota: Nota,
    proveedor: Proveedor | None,
    cliente: Cliente | None,
) -> bool:
    if nota.comentarios_admin and "Transferencia entre sucursales" in nota.comentarios_admin:
        return True
    partner_name = ""
    if nota.tipo_operacion == TipoOperacion.compra:
        partner_name = proveedor.nombre_completo if proveedor else ""
    else:
        partner_name = cliente.nombre_completo if cliente else ""
    if partner_name.startswith("Sucursal "):
        suc_name = partner_name.replace("Sucursal ", "", 1).strip()
        if suc_name and db.query(Sucursal).filter(Sucursal.nombre == suc_name).first():
            return True
    return False


def _extract_transfer_related_id(nota: Nota) -> int | None:
    if not nota.comentarios_admin:
        return None
    match = _TRANSFER_RELATED_NOTE_RE.search(nota.comentarios_admin)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _parse_folio_query(
    folio_raw: str,
) -> tuple[int, TipoOperacion, int] | None:
    if not folio_raw:
        return None
    match = _FOLIO_QUERY_RE.match(folio_raw.strip())
    if not match:
        return None
    sucursal_id = int(match.group(1))
    letter = match.group(2).upper()
    seq = int(match.group(3))
    tipo_op = TipoOperacion.compra if letter == "C" else TipoOperacion.venta
    return sucursal_id, tipo_op, seq


def _build_folio_map(notas: Iterable[Nota]) -> dict[int, str]:
    folio_map: dict[int, str] = {}
    for nota in notas:
        if not nota:
            continue
        folio = note_service.format_folio(
            sucursal_id=nota.sucursal_id,
            tipo_operacion=nota.tipo_operacion,
            folio_seq=nota.folio_seq,
        )
        folio_map[nota.id] = folio or "-"
    return folio_map


def _get_allowed_sucursal_ids(
    db: Session,
    current_user: dict,
) -> list[int] | None:
    if current_user.get("rol") != UserRole.admin.value:
        return None
    user = db.get(User, current_user.get("id"))
    if not user:
        raise HTTPException(status_code=403, detail="Usuario no encontrado.")
    ids = [s.id for s in user.sucursales_admin]
    if not ids and user.sucursal_id:
        ids = [user.sucursal_id]
    if not ids:
        raise HTTPException(status_code=403, detail="No tienes sucursales asignadas.")
    return sorted(set(ids))


def _filter_sucursales_for_admin(
    sucursales: list[Sucursal],
    allowed_ids: list[int] | None,
) -> list[Sucursal]:
    if allowed_ids is None:
        return sucursales
    return [s for s in sucursales if s.id in allowed_ids]


def _ensure_nota_access(
    nota: Nota,
    allowed_ids: list[int] | None,
) -> None:
    if allowed_ids is None:
        return
    if nota.sucursal_id not in allowed_ids:
        raise HTTPException(status_code=403, detail="No tienes acceso a esta sucursal.")


def _sync_admin_primary_sucursal(admin: User) -> None:
    if admin.rol != UserRole.admin:
        return
    ids = [s.id for s in admin.sucursales_admin]
    admin.sucursal_id = sorted(ids)[0] if ids else None


def _placas_conflict(db: Session, placas_list: list[str], modelo, owner_field: str, owner_id: int | None = None) -> str | None:
    if not placas_list:
        return None
    existing = db.query(modelo).filter(modelo.placa.in_(placas_list)).all()
    for ex in existing:
        if owner_id is None or getattr(ex, owner_field) != owner_id:
            return f"La placa {ex.placa} ya está asignada."
    return None


def require_superadmin(request: Request) -> dict:
    user = request.session.get("user")
    if not user or user.get("rol") != "super_admin":
        raise HTTPException(status_code=403, detail="Solo super admins pueden acceder a esta sección.")
    return user


def require_admin_or_superadmin(request: Request) -> dict:
    user = request.session.get("user")
    if not user or user.get("rol") not in ("super_admin", "admin"):
        raise HTTPException(status_code=403, detail="Solo admins pueden acceder a esta sección.")
    return user


# ---------- SUCURSALES ----------


@router.get("/sucursales")
async def sucursales_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    return templates.TemplateResponse(
        "admin/sucursales_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "sucursales": sucursales,
        },
    )


@router.get("/sucursales/nueva")
async def sucursal_new_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    admins = db.query(User).filter(User.rol == UserRole.admin).order_by(User.nombre_completo).all()
    return templates.TemplateResponse(
        "admin/sucursal_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "error": None,
            "sucursal": None,
            "admins": admins,
            "selected_admin_ids": [],
            "trabajadores": [],
        },
    )


@router.post("/sucursales/nueva")
async def sucursal_new_post(
    request: Request,
    nombre: str = Form(...),
    direccion: str = Form(""),
    logo_file: UploadFile | None = File(None),
    admin_ids: List[str] = Form([]),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    nombre = nombre.strip()
    direccion = direccion.strip()
    admins = db.query(User).filter(User.rol == UserRole.admin).order_by(User.nombre_completo).all()

    if not nombre:
        return templates.TemplateResponse(
            "admin/sucursal_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "error": "El nombre de la sucursal es obligatorio.",
                "sucursal": None,
                "admins": admins,
                "selected_admin_ids": [int(aid) for aid in admin_ids if aid],
                "trabajadores": [],
            },
            status_code=400,
        )

    existing = db.query(Sucursal).filter(Sucursal.nombre == nombre).first()
    if existing:
        return templates.TemplateResponse(
            "admin/sucursal_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "error": "Ya existe una sucursal con ese nombre.",
                "sucursal": None,
                "admins": admins,
                "selected_admin_ids": [int(aid) for aid in admin_ids if aid],
                "trabajadores": [],
            },
            status_code=400,
        )

    sucursal = Sucursal(
        nombre=nombre,
        direccion=direccion or None,
        estado=SucursalStatus.activa,
        logo_url=None,
    )
    db.add(sucursal)
    db.flush()

    try:
        saved_logo = await _upload_logo_file(
            logo_file,
            folder=f"logos/sucursales/{sucursal.id}",
        )
    except ValueError as exc:
        db.rollback()
        return templates.TemplateResponse(
            "admin/sucursal_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "error": str(exc),
                "sucursal": None,
                "admins": admins,
                "selected_admin_ids": [int(aid) for aid in admin_ids if aid],
                "trabajadores": [],
            },
            status_code=400,
        )

    if saved_logo:
        sucursal.logo_url = saved_logo

    selected_ids = {int(aid) for aid in admin_ids if aid}
    if selected_ids:
        for admin in admins:
            if admin.id in selected_ids:
                if sucursal not in admin.sucursales_admin:
                    admin.sucursales_admin.append(sucursal)
                _sync_admin_primary_sucursal(admin)
                db.add(admin)
    db.commit()
    db.refresh(sucursal)

    return RedirectResponse(url="/web/admin/sucursales", status_code=303)


@router.get("/sucursales/{sucursal_id}/editar")
async def sucursal_edit_get(
    sucursal_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    sucursal = db.query(Sucursal).get(sucursal_id)
    if not sucursal:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")
    admins = db.query(User).filter(User.rol == UserRole.admin).order_by(User.nombre_completo).all()
    selected_admin_ids = [
        adm.id for adm in admins if any(s.id == sucursal.id for s in adm.sucursales_admin)
    ]
    trabajadores = (
        db.query(User)
        .filter(User.rol == UserRole.trabajador, User.sucursal_id == sucursal.id)
        .order_by(User.nombre_completo)
        .all()
    )
    return templates.TemplateResponse(
        "admin/sucursal_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "error": None,
            "sucursal": sucursal,
            "admins": admins,
            "selected_admin_ids": selected_admin_ids,
            "trabajadores": trabajadores,
        },
    )


@router.post("/sucursales/{sucursal_id}/editar")
async def sucursal_edit_post(
    sucursal_id: int,
    request: Request,
    nombre: str = Form(...),
    direccion: str = Form(""),
    logo_file: UploadFile | None = File(None),
    admin_ids: List[str] = Form([]),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    sucursal = db.query(Sucursal).get(sucursal_id)
    if not sucursal:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")
    admins = db.query(User).filter(User.rol == UserRole.admin).order_by(User.nombre_completo).all()
    trabajadores = (
        db.query(User)
        .filter(User.rol == UserRole.trabajador, User.sucursal_id == sucursal.id)
        .order_by(User.nombre_completo)
        .all()
    )
    nombre = nombre.strip()
    direccion = direccion.strip()
    selected_admin_ids = [int(aid) for aid in admin_ids if aid]

    def render_error(msg: str):
        return templates.TemplateResponse(
            "admin/sucursal_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "error": msg,
                "sucursal": sucursal,
                "admins": admins,
                "selected_admin_ids": selected_admin_ids,
                "trabajadores": trabajadores,
            },
            status_code=400,
        )

    if not nombre:
        return render_error("El nombre de la sucursal es obligatorio.")

    existing = (
        db.query(Sucursal)
        .filter(Sucursal.nombre == nombre, Sucursal.id != sucursal.id)
        .first()
    )
    if existing:
        return render_error("Ya existe otra sucursal con ese nombre.")

    sucursal.nombre = nombre
    sucursal.direccion = direccion or None
    try:
        new_logo = await _upload_logo_file(
            logo_file,
            folder=f"logos/sucursales/{sucursal.id}",
        )
    except ValueError as exc:
        return render_error(str(exc))
    if new_logo:
        sucursal.logo_url = new_logo
    db.add(sucursal)

    selected_ids_set = set(selected_admin_ids)
    for adm in admins:
        if adm.id in selected_ids_set:
            if sucursal not in adm.sucursales_admin:
                adm.sucursales_admin.append(sucursal)
        else:
            if sucursal in adm.sucursales_admin:
                adm.sucursales_admin.remove(sucursal)
        _sync_admin_primary_sucursal(adm)
        db.add(adm)

    db.commit()
    return RedirectResponse(url="/web/admin/sucursales", status_code=303)


# ---------- USUARIOS ----------


@router.get("/users")
async def users_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    updated = request.query_params.get("updated") == "1"
    sucursal_id = request.query_params.get("sucursal_id")
    try:
        sucursal_id_int = int(sucursal_id) if sucursal_id else None
    except ValueError:
        sucursal_id_int = None

    usuarios = (
        db.query(User)
        .order_by(User.id.desc())
    )
    if sucursal_id_int:
        usuarios = usuarios.filter(User.sucursal_id == sucursal_id_int)
    usuarios = usuarios.all()
    sucursales = {s.id: s for s in db.query(Sucursal).all()}

    return templates.TemplateResponse(
        "admin/users_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "usuarios": usuarios,
            "sucursales_map": sucursales,
            "sucursal_id": sucursal_id_int,
            "updated": updated,
        },
    )


@router.get("/users/nuevo")
async def user_new_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    return templates.TemplateResponse(
        "admin/user_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "sucursales": sucursales,
            "error": None,
        },
    )


@router.post("/users/nuevo")
async def user_new_post(
    request: Request,
    username: str = Form(...),
    nombre_completo: str = Form(...),
    password: str = Form(...),
    rol: str = Form(...),
    sucursal_id: int | None = Form(None),
    admin_sucursal_ids: List[str] = Form([]),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    username = username.strip()
    nombre_completo = nombre_completo.strip()

    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()

    if not username or not nombre_completo or not password:
        return templates.TemplateResponse(
            "admin/user_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "sucursales": sucursales,
                "error": "Username, nombre y contraseña son obligatorios.",
            },
            status_code=400,
        )

    # Validar rol
    try:
        user_role = UserRole(rol)
    except ValueError:
        return templates.TemplateResponse(
            "admin/user_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "sucursales": sucursales,
                "error": "Rol inválido.",
            },
            status_code=400,
        )

    # Validar sucursal para trabajador/admin
    selected_admin_suc_ids = [int(sid) for sid in admin_sucursal_ids if sid]
    if user_role == UserRole.trabajador and not sucursal_id:
        return templates.TemplateResponse(
            "admin/user_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "sucursales": sucursales,
                "error": "Los trabajadores deben tener una sucursal asignada.",
            },
            status_code=400,
        )
    if user_role == UserRole.admin:
        if not selected_admin_suc_ids and sucursal_id:
            selected_admin_suc_ids = [sucursal_id]
        if not selected_admin_suc_ids:
            return templates.TemplateResponse(
                "admin/user_form.html",
                {
                    "request": request,
                    "env": settings.ENV,
                    "user": current_user,
                    "sucursales": sucursales,
                    "error": "Los admins deben tener al menos una sucursal asignada.",
                },
                status_code=400,
            )
        found = (
            db.query(Sucursal)
            .filter(Sucursal.id.in_(selected_admin_suc_ids))
            .all()
        )
        if len(found) != len(set(selected_admin_suc_ids)):
            return templates.TemplateResponse(
                "admin/user_form.html",
                {
                    "request": request,
                    "env": settings.ENV,
                    "user": current_user,
                    "sucursales": sucursales,
                    "error": "Una de las sucursales seleccionadas no existe.",
                },
                status_code=400,
            )

    # Unicidad de username
    existing = db.query(User).filter(User.username == username).first()
    if existing:
        return templates.TemplateResponse(
            "admin/user_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "sucursales": sucursales,
                "error": "Ya existe un usuario con ese username.",
            },
            status_code=400,
        )

    user = User(
        username=username,
        nombre_completo=nombre_completo,
        password_hash=hash_password(password),
        rol=user_role,
        estado=UserStatus.activo,
        sucursal_id=(
            sucursal_id
            if user_role == UserRole.trabajador
            else (selected_admin_suc_ids[0] if user_role == UserRole.admin else None)
        ),
        super_admin_original=False,
    )

    db.add(user)
    db.commit()
    if user_role == UserRole.admin and selected_admin_suc_ids:
        user.sucursales_admin = (
            db.query(Sucursal)
            .filter(Sucursal.id.in_(selected_admin_suc_ids))
            .all()
        )
        _sync_admin_primary_sucursal(user)
        db.add(user)
        db.commit()

    return RedirectResponse(url="/web/admin/users", status_code=303)


@router.get("/users/{user_id}/editar")
async def user_edit_get(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    admin_sucursal_ids = []
    if user.rol == UserRole.admin:
        admin_sucursal_ids = [s.id for s in user.sucursales_admin]
        if not admin_sucursal_ids and user.sucursal_id:
            admin_sucursal_ids = [user.sucursal_id]
    return templates.TemplateResponse(
        "admin/user_edit.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "edit_user": user,
            "sucursales": sucursales,
            "admin_sucursal_ids": admin_sucursal_ids,
            "error": None,
        },
    )


@router.post("/users/{user_id}/editar")
async def user_edit_post(
    user_id: int,
    request: Request,
    username: str = Form(...),
    nombre_completo: str = Form(...),
    password: str = Form(""),
    rol: str = Form(...),
    estado: str = Form(...),
    sucursal_id: str | None = Form(None),
    admin_sucursal_ids: List[str] = Form([]),
    super_admin_original: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    username = username.strip()
    nombre_completo = nombre_completo.strip()
    password = (password or "").strip()

    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    selected_admin_suc_ids = [int(sid) for sid in admin_sucursal_ids if sid]

    def render_error(msg: str):
        return templates.TemplateResponse(
            "admin/user_edit.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "edit_user": user,
                "sucursales": sucursales,
                "error": msg,
            },
            status_code=400,
        )

    if not username or not nombre_completo:
        return render_error("Usuario y nombre son obligatorios.")

    try:
        user_role = UserRole(rol)
    except ValueError:
        return render_error("Rol invalido.")

    try:
        user_status = UserStatus(estado)
    except ValueError:
        return render_error("Estado invalido.")

    suc_id: int | None = None
    if sucursal_id:
        try:
            suc_id = int(sucursal_id)
        except ValueError:
            return render_error("Sucursal invalida.")
        if not db.get(Sucursal, suc_id):
            return render_error("Sucursal no encontrada.")

    if user_role == UserRole.trabajador and not suc_id:
        return render_error("Los trabajadores deben tener una sucursal asignada.")
    if user_role == UserRole.admin:
        if not selected_admin_suc_ids and suc_id:
            selected_admin_suc_ids = [suc_id]
        if not selected_admin_suc_ids:
            return render_error("Los admins deben tener al menos una sucursal asignada.")
        found = (
            db.query(Sucursal)
            .filter(Sucursal.id.in_(selected_admin_suc_ids))
            .all()
        )
        if len(found) != len(set(selected_admin_suc_ids)):
            return render_error("Una de las sucursales seleccionadas no existe.")

    existing = db.query(User).filter(User.username == username, User.id != user.id).first()
    if existing:
        return render_error("Ya existe un usuario con ese username.")

    user.username = username
    user.nombre_completo = nombre_completo
    user.rol = user_role
    user.estado = user_status
    if user_role == UserRole.admin:
        user.sucursales_admin = found
        _sync_admin_primary_sucursal(user)
    else:
        user.sucursales_admin = []
        user.sucursal_id = suc_id
    user.super_admin_original = bool(super_admin_original)
    if password:
        user.password_hash = hash_password(password)
    db.add(user)
    db.commit()

    return RedirectResponse(url="/web/admin/users?updated=1", status_code=303)


# ---------- MATERIALES ----------


@router.get("/materiales")
async def materiales_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    materiales = db.query(Material).order_by(Material.nombre).all()
    return templates.TemplateResponse(
        "admin/materiales_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "materiales": materiales,
        },
    )


@router.get("/materiales/nuevo")
async def material_new_get(
    request: Request,
    current_user: dict = Depends(require_superadmin),
):
    return templates.TemplateResponse(
        "admin/material_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "material": None,
            "error": None,
        },
    )


@router.post("/materiales/nuevo")
async def material_new_post(
    request: Request,
    nombre: str = Form(...),
    descripcion: str = Form(""),
    unidad_medida: str = Form("kg"),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    nombre = nombre.strip()
    descripcion = descripcion.strip()
    unidad_medida = unidad_medida.strip() or "kg"

    if not nombre:
        return templates.TemplateResponse(
            "admin/material_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "material": None,
                "error": "El nombre del material es obligatorio.",
            },
            status_code=400,
        )

    existing = db.query(Material).filter(Material.nombre == nombre).first()
    if existing:
        return templates.TemplateResponse(
            "admin/material_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "material": None,
                "error": "Ya existe un material con ese nombre.",
            },
            status_code=400,
        )

    material = Material(
        nombre=nombre,
        descripcion=descripcion or None,
        unidad_medida=unidad_medida,
        activo=True,
    )
    db.add(material)
    db.commit()

    return RedirectResponse(url="/web/admin/materiales", status_code=303)


@router.get("/materiales/{material_id}/editar")
async def material_edit_get(
    material_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    material = db.query(Material).get(material_id)
    if not material:
        raise HTTPException(status_code=404, detail="Material no encontrado.")

    return templates.TemplateResponse(
        "admin/material_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "material": material,
            "error": None,
        },
    )


@router.post("/materiales/{material_id}/editar")
async def material_edit_post(
    material_id: int,
    request: Request,
    nombre: str = Form(...),
    descripcion: str = Form(""),
    unidad_medida: str = Form("kg"),
    activo: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    material = db.query(Material).get(material_id)
    if not material:
        raise HTTPException(status_code=404, detail="Material no encontrado.")

    nombre = nombre.strip()
    descripcion = descripcion.strip()
    unidad_medida = unidad_medida.strip() or "kg"

    if not nombre:
        return templates.TemplateResponse(
            "admin/material_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "material": material,
                "error": "El nombre del material es obligatorio.",
            },
            status_code=400,
        )

    # validar unicidad de nombre
    existing = (
        db.query(Material)
        .filter(Material.nombre == nombre, Material.id != material.id)
        .first()
    )
    if existing:
        return templates.TemplateResponse(
            "admin/material_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "material": material,
                "error": "Ya existe otro material con ese nombre.",
            },
            status_code=400,
        )

    material.nombre = nombre
    material.descripcion = descripcion or None
    material.unidad_medida = unidad_medida
    material.activo = bool(activo)  # checkbox: "on" o None

    db.add(material)
    db.commit()

    return RedirectResponse(url="/web/admin/materiales", status_code=303)


# ---------- PRECIOS POR MATERIAL ----------


@router.get("/materiales/{material_id}/precios")
async def material_precios_list(
    material_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    material = db.query(Material).get(material_id)
    if not material:
        raise HTTPException(status_code=404, detail="Material no encontrado.")

    precios = (
        db.query(TablaPrecio)
        .filter(TablaPrecio.material_id == material_id)
        .order_by(
            TablaPrecio.tipo_operacion,
            TablaPrecio.tipo_cliente,
            TablaPrecio.version.desc(),
        )
        .all()
    )

    return templates.TemplateResponse(
        "admin/precios_material.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "material": material,
            "precios": precios,
        },
    )


@router.get("/materiales/{material_id}/precios/nuevo")
async def material_precio_new_get(
    material_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    material = db.query(Material).get(material_id)
    if not material:
        raise HTTPException(status_code=404, detail="Material no encontrado.")

    return templates.TemplateResponse(
        "admin/precio_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "material": material,
            "error": None,
            "tipos_operacion": list(TipoOperacion),
            "tipos_cliente": list(TipoCliente),
        },
    )


@router.post("/materiales/{material_id}/precios/nuevo")
async def material_precio_new_post(
    material_id: int,
    request: Request,
    tipo_operacion: str = Form(...),
    tipo_cliente: str = Form(...),
    precio_por_unidad: str = Form(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    material = db.query(Material).get(material_id)
    if not material:
        raise HTTPException(status_code=404, detail="Material no encontrado.")

    # Parsear enums
    try:
        tipo_op = TipoOperacion(tipo_operacion)
        tipo_cli = TipoCliente(tipo_cliente)
    except ValueError:
        return templates.TemplateResponse(
            "admin/precio_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "material": material,
                "error": "Tipo de operación o tipo de cliente inválido.",
                "tipos_operacion": list(TipoOperacion),
                "tipos_cliente": list(TipoCliente),
            },
            status_code=400,
        )

    # Parsear precio
    try:
        precio_dec = Decimal(precio_por_unidad)
        if precio_dec <= 0:
            raise InvalidOperation()
    except (InvalidOperation, ValueError):
        return templates.TemplateResponse(
            "admin/precio_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "material": material,
                "error": "El precio debe ser un número mayor que 0.",
                "tipos_operacion": list(TipoOperacion),
                "tipos_cliente": list(TipoCliente),
            },
            status_code=400,
        )

    create_price_version(
        db,
        material_id=material_id,
        tipo_operacion=tipo_op,
        tipo_cliente=tipo_cli,
        precio=precio_dec,
        user_id=current_user.get("id"),
        source="web",
    )

    return RedirectResponse(
        url=f"/web/admin/materiales/{material_id}/precios",
        status_code=303,
    )

# ---------- PROVEEDORES ----------


@router.get("/proveedores")
async def proveedores_list(
    request: Request,
    q: str | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    query = db.query(Proveedor)

    if q:
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Proveedor.nombre_completo.ilike(term),
                Proveedor.telefono.ilike(term),
                Proveedor.correo_electronico.ilike(term),
                Proveedor.placas.ilike(term),
            )
        )

    proveedores = query.order_by(Proveedor.nombre_completo).all()

    return templates.TemplateResponse(
        "admin/proveedores_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "proveedores": proveedores,
            "q": q or "",
        },
    )


@router.get("/proveedores/nuevo")
async def proveedor_new_get(
    request: Request,
    current_user: dict = Depends(require_admin_or_superadmin),
):
    return templates.TemplateResponse(
        "admin/proveedor_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "proveedor": None,
            "error": None,
            "placas_text": "",
        },
    )


@router.post("/proveedores/nuevo")
async def proveedor_new_post(
    request: Request,
    nombre_completo: str = Form(...),
    telefono: str = Form(""),
    correo_electronico: str = Form(""),
    placas: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nombre_completo = nombre_completo.strip()
    telefono = telefono.strip()
    correo_electronico = correo_electronico.strip()
    placas_list = _parse_placas(placas)

    if not nombre_completo:
        return templates.TemplateResponse(
            "admin/proveedor_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "proveedor": None,
                "error": "El nombre del proveedor es obligatorio.",
                "placas_text": placas,
            },
            status_code=400,
        )

    conflict = _placas_conflict(db, placas_list, ProveedorPlaca, "proveedor_id", None)
    if conflict:
        return templates.TemplateResponse(
            "admin/proveedor_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "proveedor": None,
                "error": conflict,
                "placas_text": placas,
            },
            status_code=400,
        )

    proveedor = Proveedor(
        nombre_completo=nombre_completo,
        telefono=telefono or None,
        correo_electronico=correo_electronico or None,
        placas=placas_list[0] if placas_list else None,
        activo=True,
    )
    db.add(proveedor)
    db.commit()
    db.refresh(proveedor)
    _set_proveedor_placas(db, proveedor, placas_list)
    db.commit()

    return RedirectResponse(url="/web/admin/proveedores", status_code=303)


@router.get("/proveedores/{proveedor_id}/editar")
async def proveedor_edit_get(
    proveedor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    proveedor = db.query(Proveedor).get(proveedor_id)
    if not proveedor:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado.")

    return templates.TemplateResponse(
        "admin/proveedor_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "proveedor": proveedor,
            "error": None,
            "placas_text": "\n".join([pl.placa for pl in proveedor.placas_rel]) if proveedor.placas_rel else (proveedor.placas or ""),
        },
    )


@router.post("/proveedores/{proveedor_id}/editar")
async def proveedor_edit_post(
    proveedor_id: int,
    request: Request,
    nombre_completo: str = Form(...),
    telefono: str = Form(""),
    correo_electronico: str = Form(""),
    placas: str = Form(""),
    activo: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    proveedor = db.query(Proveedor).get(proveedor_id)
    if not proveedor:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado.")

    nombre_completo = nombre_completo.strip()
    telefono = telefono.strip()
    correo_electronico = correo_electronico.strip()
    placas_list = _parse_placas(placas)

    if not nombre_completo:
        return templates.TemplateResponse(
            "admin/proveedor_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "proveedor": proveedor,
                "error": "El nombre del proveedor es obligatorio.",
                "placas_text": placas,
            },
            status_code=400,
        )

    conflict = _placas_conflict(db, placas_list, ProveedorPlaca, "proveedor_id", proveedor.id)
    if conflict:
        return templates.TemplateResponse(
            "admin/proveedor_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "proveedor": proveedor,
                "error": conflict,
                "placas_text": placas,
            },
            status_code=400,
        )

    proveedor.nombre_completo = nombre_completo
    proveedor.telefono = telefono or None
    proveedor.correo_electronico = correo_electronico or None
    proveedor.placas = placas_list[0] if placas_list else None
    proveedor.activo = bool(activo)

    _set_proveedor_placas(db, proveedor, placas_list)
    db.commit()

    return RedirectResponse(url="/web/admin/proveedores", status_code=303)

# ---------- CLIENTES ----------


@router.get("/clientes")
async def clientes_list(
    request: Request,
    q: str | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    query = db.query(Cliente)

    if q:
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Cliente.nombre_completo.ilike(term),
                Cliente.telefono.ilike(term),
                Cliente.correo_electronico.ilike(term),
                Cliente.placas.ilike(term),
            )
        )

    clientes = query.order_by(Cliente.nombre_completo).all()

    return templates.TemplateResponse(
        "admin/clientes_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "clientes": clientes,
            "q": q or "",
        },
    )


@router.get("/clientes/nuevo")
async def cliente_new_get(
    request: Request,
    current_user: dict = Depends(require_admin_or_superadmin),
):
    return templates.TemplateResponse(
        "admin/cliente_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "cliente": None,
            "error": None,
            "placas_text": "",
        },
    )


@router.post("/clientes/nuevo")
async def cliente_new_post(
    request: Request,
    nombre_completo: str = Form(...),
    telefono: str = Form(""),
    correo_electronico: str = Form(""),
    placas: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nombre_completo = nombre_completo.strip()
    telefono = telefono.strip()
    correo_electronico = correo_electronico.strip()
    placas_list = _parse_placas(placas)

    if not nombre_completo:
        return templates.TemplateResponse(
            "admin/cliente_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "cliente": None,
                "error": "El nombre del cliente es obligatorio.",
                "placas_text": placas,
            },
            status_code=400,
        )

    conflict = _placas_conflict(db, placas_list, ClientePlaca, "cliente_id", None)
    if conflict:
        return templates.TemplateResponse(
            "admin/cliente_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "cliente": None,
                "error": conflict,
                "placas_text": placas,
            },
            status_code=400,
        )

    cliente = Cliente(
        nombre_completo=nombre_completo,
        telefono=telefono or None,
        correo_electronico=correo_electronico or None,
        placas=placas_list[0] if placas_list else None,
        activo=True,
    )
    db.add(cliente)
    db.commit()
    db.refresh(cliente)
    _set_cliente_placas(db, cliente, placas_list)
    db.commit()

    return RedirectResponse(url="/web/admin/clientes", status_code=303)


@router.get("/clientes/{cliente_id}/editar")
async def cliente_edit_get(
    cliente_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    cliente = db.query(Cliente).get(cliente_id)
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")

    return templates.TemplateResponse(
        "admin/cliente_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "cliente": cliente,
            "error": None,
            "placas_text": "\n".join([pl.placa for pl in cliente.placas_rel]) if cliente.placas_rel else (cliente.placas or ""),
        },
    )


@router.post("/clientes/{cliente_id}/editar")
async def cliente_edit_post(
    cliente_id: int,
    request: Request,
    nombre_completo: str = Form(...),
    telefono: str = Form(""),
    correo_electronico: str = Form(""),
    placas: str = Form(""),
    activo: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    cliente = db.query(Cliente).get(cliente_id)
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")

    nombre_completo = nombre_completo.strip()
    telefono = telefono.strip()
    correo_electronico = correo_electronico.strip()
    placas_list = _parse_placas(placas)

    if not nombre_completo:
        return templates.TemplateResponse(
            "admin/cliente_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "cliente": cliente,
                "error": "El nombre del cliente es obligatorio.",
                "placas_text": placas,
            },
            status_code=400,
        )

    conflict = _placas_conflict(db, placas_list, ClientePlaca, "cliente_id", cliente.id)
    if conflict:
        return templates.TemplateResponse(
            "admin/cliente_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "cliente": cliente,
                "error": conflict,
                "placas_text": placas,
            },
            status_code=400,
        )

    cliente.nombre_completo = nombre_completo
    cliente.telefono = telefono or None
    cliente.correo_electronico = correo_electronico or None
    cliente.placas = placas_list[0] if placas_list else None
    cliente.activo = bool(activo)

    _set_cliente_placas(db, cliente, placas_list)
    db.commit()

    return RedirectResponse(url="/web/admin/clientes", status_code=303)


# ---------- NOTAS ----------


@router.get("/notas")
async def notas_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    notas_revision = (
        db.query(Nota)
        .filter(
            Nota.estado == NotaEstado.en_revision,
            *([Nota.sucursal_id.in_(allowed_suc_ids)] if allowed_suc_ids else []),
        )
        .order_by(Nota.id.desc())
        .all()
    )
    notas_recientes = (
        db.query(Nota)
        .filter(*([Nota.sucursal_id.in_(allowed_suc_ids)] if allowed_suc_ids else []))
        .order_by(Nota.id.desc())
        .limit(10)
        .all()
    )
    hoy = date.today()
    alerta_dias = max(1, int(getattr(settings, "NOTA_VENCIMIENTO_ALERTA_DIAS", 5)))
    limite_alerta = hoy + timedelta(days=alerta_dias)
    notas_con_vencimiento = (
        db.query(Nota)
        .filter(
            Nota.estado == NotaEstado.aprobada,
            Nota.fecha_caducidad_pago.isnot(None),
            *([Nota.sucursal_id.in_(allowed_suc_ids)] if allowed_suc_ids else []),
        )
        .order_by(Nota.fecha_caducidad_pago.asc())
        .all()
    )

    def saldo_pendiente(nota: Nota) -> Decimal:
        total = Decimal(str(nota.total_monto or 0))
        pagado = Decimal(str(nota.monto_pagado or 0))
        saldo = total - pagado
        if saldo < Decimal("0"):
            saldo = Decimal("0")
        return saldo

    notas_vencidas = []
    notas_por_vencer = []
    for nota in notas_con_vencimiento:
        saldo = saldo_pendiente(nota)
        if saldo <= Decimal("0"):
            continue
        if nota.fecha_caducidad_pago < hoy:
            notas_vencidas.append(
                {
                    "nota": nota,
                    "saldo_pendiente": saldo,
                    "dias": (hoy - nota.fecha_caducidad_pago).days,
                }
            )
        elif nota.fecha_caducidad_pago <= limite_alerta:
            notas_por_vencer.append(
                {
                    "nota": nota,
                    "saldo_pendiente": saldo,
                    "dias": (nota.fecha_caducidad_pago - hoy).days,
                }
            )
    folio_query = (request.query_params.get("folio") or "").strip()
    folio_error = None
    folio_result = None
    if folio_query:
        parsed = _parse_folio_query(folio_query)
        if not parsed:
            folio_error = "Formato de folio inv\u00e1lido. Usa 01_C_1."
        else:
            sucursal_id, tipo_op, seq = parsed
            folio_result = (
                db.query(Nota)
                .filter(
                    Nota.sucursal_id == sucursal_id,
                    Nota.tipo_operacion == tipo_op,
                    Nota.folio_seq == seq,
                )
                .first()
            )
            if folio_result and allowed_suc_ids and folio_result.sucursal_id not in allowed_suc_ids:
                folio_result = None
                folio_error = "No tienes acceso a esa sucursal."
            if not folio_result and not folio_error:
                folio_error = "No se encontr\u00f3 una nota con ese folio."
    suc_query = db.query(Sucursal)
    if allowed_suc_ids:
        suc_query = suc_query.filter(Sucursal.id.in_(allowed_suc_ids))
    sucursales = {s.id: s for s in suc_query.all()}
    proveedores = {p.id: p for p in db.query(Proveedor).all()}
    clientes = {c.id: c for c in db.query(Cliente).all()}
    notas_folio = []
    notas_folio.extend(notas_revision)
    notas_folio.extend(notas_recientes)
    notas_folio.extend([item["nota"] for item in notas_vencidas])
    notas_folio.extend([item["nota"] for item in notas_por_vencer])
    if folio_result:
        notas_folio.append(folio_result)
    folio_map = _build_folio_map(notas_folio)

    return templates.TemplateResponse(
        "admin/notes_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "notas_revision": notas_revision,
            "notas_recientes": notas_recientes,
            "notas_vencidas": notas_vencidas,
            "notas_por_vencer": notas_por_vencer,
            "alerta_dias": alerta_dias,
            "sucursales": sucursales,
            "proveedores": proveedores,
            "clientes": clientes,
            "folio_query": folio_query,
            "folio_error": folio_error,
            "folio_result": folio_result,
            "folio_map": folio_map,
        },
    )


@router.get("/transferencias")
async def transferencias_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.nombre).all()
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    origin_locked = (
        current_user.get("rol") == UserRole.admin.value
        and allowed_suc_ids
        and len(allowed_suc_ids) == 1
    )
    origin_id = allowed_suc_ids[0] if origin_locked else None
    origin_sucursal = db.get(Sucursal, origin_id) if origin_id else None
    ok = request.query_params.get("ok") == "1"
    nota_salida_id = request.query_params.get("salida")
    nota_entrada_id = request.query_params.get("entrada")
    nota_salida = None
    nota_entrada = None
    nota_salida_sucursal = None
    nota_entrada_sucursal = None
    missing_transfer_note = False
    if nota_salida_id:
        try:
            nota_salida = db.get(Nota, int(nota_salida_id))
        except ValueError:
            nota_salida = None
        if nota_salida:
            _ensure_nota_access(nota_salida, allowed_suc_ids)
        if nota_salida and nota_salida.sucursal_id:
            nota_salida_sucursal = db.get(Sucursal, nota_salida.sucursal_id)
        elif nota_salida_id:
            missing_transfer_note = True
    if nota_entrada_id:
        try:
            nota_entrada = db.get(Nota, int(nota_entrada_id))
        except ValueError:
            nota_entrada = None
        if nota_entrada:
            _ensure_nota_access(nota_entrada, allowed_suc_ids)
        if nota_entrada and nota_entrada.sucursal_id:
            nota_entrada_sucursal = db.get(Sucursal, nota_entrada.sucursal_id)
        elif nota_entrada_id:
            missing_transfer_note = True
    return templates.TemplateResponse(
        "admin/transferencias.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "materiales": materiales,
            "sucursales": sucursales,
            "tipos_cliente": list(TipoCliente),
            "origin_locked": origin_locked,
            "origin_sucursal": origin_sucursal,
            "form_origen": origin_id,
            "form_destino": None,
            "form_rows": [],
            "form_comentario": "",
            "ok": ok,
            "nota_salida_id": nota_salida_id,
            "nota_entrada_id": nota_entrada_id,
            "nota_salida": nota_salida,
            "nota_entrada": nota_entrada,
            "nota_salida_sucursal": nota_salida_sucursal,
            "nota_entrada_sucursal": nota_entrada_sucursal,
            "missing_transfer_note": missing_transfer_note,
            "error": None,
        },
    )


@router.post("/transferencias")
async def transferencias_post(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.nombre).all()
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    origin_locked = (
        current_user.get("rol") == UserRole.admin.value
        and allowed_suc_ids
        and len(allowed_suc_ids) == 1
    )
    origin_id = allowed_suc_ids[0] if origin_locked else None
    origin_sucursal = db.get(Sucursal, origin_id) if origin_id else None

    form = await request.form()
    form_origen = origin_id or form.get("origen_sucursal_id")
    form_destino = form.get("destino_sucursal_id")
    comentario = (form.get("comentario") or "").strip()

    def render_error(msg: str, rows: list[dict]):
        return templates.TemplateResponse(
            "admin/transferencias.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "materiales": materiales,
                "sucursales": sucursales,
                "tipos_cliente": list(TipoCliente),
                "origin_locked": origin_locked,
                "origin_sucursal": origin_sucursal,
                "form_origen": form_origen,
                "form_destino": form_destino,
                "form_rows": rows,
                "form_comentario": comentario,
                "ok": False,
                "nota_salida_id": None,
                "nota_entrada_id": None,
                "error": msg,
            },
            status_code=400,
        )

    try:
        origen_id_int = int(form_origen) if form_origen else None
        destino_id_int = int(form_destino) if form_destino else None
    except ValueError:
        return render_error("Sucursal invalida.", [])
    if not origen_id_int or not destino_id_int:
        return render_error("Debes seleccionar sucursal de origen y destino.", [])
    if origen_id_int == destino_id_int:
        return render_error("La sucursal de origen y destino deben ser diferentes.", [])
    if allowed_suc_ids:
        if origen_id_int not in allowed_suc_ids:
            return render_error("Sucursal de origen no autorizada.", [])
        if destino_id_int not in allowed_suc_ids:
            return render_error("Sucursal de destino no autorizada.", [])

    origen = db.get(Sucursal, origen_id_int)
    destino = db.get(Sucursal, destino_id_int)
    if not origen or not destino:
        return render_error("Sucursal no encontrada.", [])

    material_ids = form.getlist("material_id")
    kg_netos = form.getlist("kg_neto")
    tipos_cli = form.getlist("tipo_cliente")
    precios_unit = form.getlist("precio_unitario")
    rows: list[dict] = []
    materiales_payload: list[dict] = []
    for idx in range(max(len(material_ids), len(kg_netos), len(tipos_cli), len(precios_unit))):
        mat_raw = material_ids[idx] if idx < len(material_ids) else ""
        kg_raw = kg_netos[idx] if idx < len(kg_netos) else ""
        tipo_raw = tipos_cli[idx] if idx < len(tipos_cli) else "regular"
        precio_raw = precios_unit[idx] if idx < len(precios_unit) else ""
        rows.append(
            {
                "material_id": mat_raw,
                "kg_neto": kg_raw,
                "tipo_cliente": tipo_raw or "regular",
                "precio_unitario": precio_raw,
            }
        )
        if not mat_raw and not kg_raw and not precio_raw:
            continue
        try:
            mat_id = int(mat_raw)
        except (TypeError, ValueError):
            return render_error("Material invalido.", rows)
        if not db.get(Material, mat_id):
            return render_error("Material no encontrado.", rows)
        try:
            kg_val = Decimal(str(kg_raw))
        except (InvalidOperation, TypeError):
            return render_error("Cantidad invalida.", rows)
        if kg_val <= 0:
            return render_error("La cantidad debe ser mayor a 0.", rows)
        try:
            precio_val = Decimal(str(precio_raw))
        except (InvalidOperation, TypeError):
            return render_error("Precio unitario invalido.", rows)
        if precio_val <= 0:
            return render_error("El precio unitario debe ser mayor a 0.", rows)
        try:
            tipo_cli = TipoCliente(tipo_raw or "regular")
        except ValueError:
            return render_error("Tipo de precio invalido.", rows)
        materiales_payload.append(
            {
                "material_id": mat_id,
                "kg_bruto": kg_val,
                "kg_descuento": Decimal("0"),
                "tipo_cliente": tipo_cli.value,
                "precio_unitario": precio_val,
            }
        )

    if not materiales_payload:
        return render_error("Debes agregar al menos un material.", rows)

    try:
        cliente = _get_or_create_branch_cliente(db, destino)
        proveedor = _get_or_create_branch_proveedor(db, origen)
        nota_salida, nota_entrada = note_service.create_transfer_notes(
            db,
            origen_sucursal_id=origen.id,
            destino_sucursal_id=destino.id,
            cliente_id=cliente.id,
            proveedor_id=proveedor.id,
            materiales_payload=materiales_payload,
            admin_id=current_user.get("id"),
            comentario=comentario or None,
            origen_nombre=origen.nombre,
            destino_nombre=destino.nombre,
        )
    except ValueError as exc:
        db.rollback()
        return render_error(str(exc), rows)

    return RedirectResponse(
        url=f"/web/admin/transferencias?ok=1&salida={nota_salida.id}&entrada={nota_entrada.id}",
        status_code=303,
    )


@router.get("/notas/precio")
async def nota_precio(
    material_id: int,
    tipo_operacion: str,
    tipo_cliente: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    try:
        tipo_op = TipoOperacion(tipo_operacion)
    except ValueError:
        return JSONResponse({"error": "tipo_operacion_invalido"}, status_code=400)
    try:
        tipo_cli = TipoCliente(tipo_cliente)
    except ValueError:
        return JSONResponse({"error": "tipo_cliente_invalido"}, status_code=400)

    precio = (
        db.query(TablaPrecio)
        .filter(
            TablaPrecio.material_id == material_id,
            TablaPrecio.tipo_operacion == tipo_op,
            TablaPrecio.tipo_cliente == tipo_cli,
            TablaPrecio.activo.is_(True),
        )
        .order_by(TablaPrecio.version.desc())
        .first()
    )
    if not precio:
        return JSONResponse({"precio_unitario": None})

    return JSONResponse(
        {
            "precio_unitario": float(precio.precio_por_unidad),
            "version_id": precio.id,
        }
    )


def _render_nota_detail(
    request: Request,
    db: Session,
    current_user: dict,
    nota: Nota,
    error: str | None = None,
    form_state: dict | None = None,
    pago_updated: bool = False,
    precios_updated: bool = False,
    edit_updated: bool = False,
):
    sucursal = db.get(Sucursal, nota.sucursal_id) if nota.sucursal_id else None
    proveedor = db.get(Proveedor, nota.proveedor_id) if nota.proveedor_id else None
    cliente = db.get(Cliente, nota.cliente_id) if nota.cliente_id else None
    trabajador = db.get(User, nota.trabajador_id) if nota.trabajador_id else None
    inv_movs = db.query(InventarioMovimiento).filter(InventarioMovimiento.nota_id == nota.id).all()
    pagos = (
        db.query(NotaPago)
        .filter(NotaPago.nota_id == nota.id)
        .order_by(NotaPago.created_at.desc())
        .all()
    )
    price_map: dict[str, dict[str, float]] = {}
    material_ids = [m.material_id for m in nota.materiales if m.material_id]
    if material_ids:
        precios = (
            db.query(TablaPrecio)
            .filter(
                TablaPrecio.material_id.in_(material_ids),
                TablaPrecio.tipo_operacion == nota.tipo_operacion,
                TablaPrecio.activo.is_(True),
            )
            .order_by(TablaPrecio.version.desc())
            .all()
        )
        for p in precios:
            mat_key = str(p.material_id)
            tipo_cli = p.tipo_cliente.value
            if mat_key not in price_map:
                price_map[mat_key] = {}
            if tipo_cli not in price_map[mat_key]:
                price_map[mat_key][tipo_cli] = float(p.precio_por_unidad)
    price_map_json = json.dumps(price_map, ensure_ascii=True)
    price_map_by_material: dict[int, str] = {}
    for mat_id in material_ids:
        mat_key = str(mat_id)
        price_map_by_material[mat_id] = json.dumps(
            price_map.get(mat_key, {}),
            ensure_ascii=True,
        )
    saldo_pendiente = Decimal(str(nota.total_monto or 0)) - Decimal(str(nota.monto_pagado or 0))
    if saldo_pendiente < Decimal("0"):
        saldo_pendiente = Decimal("0")
    folio = note_service.format_folio(
        sucursal_id=nota.sucursal_id,
        tipo_operacion=nota.tipo_operacion,
        folio_seq=nota.folio_seq,
    )
    is_transfer = _is_transfer_note(db, nota, proveedor, cliente)
    transfer_related = None
    transfer_related_sucursal = None
    if is_transfer:
        related_id = _extract_transfer_related_id(nota)
        if related_id:
            transfer_related = db.get(Nota, related_id)
            if transfer_related and transfer_related.sucursal_id:
                transfer_related_sucursal = db.get(Sucursal, transfer_related.sucursal_id)
    base_form_state = {
        "form_metodo": None,
        "form_cuenta": None,
        "form_fecha": None,
        "form_comentarios": None,
        "form_pagado": None,
        "form_pago_monto": None,
        "form_pago_metodo": None,
        "form_pago_cuenta": None,
        "form_pago_comentario": None,
    }
    context = {
        "request": request,
        "env": settings.ENV,
        "user": current_user,
        "nota": nota,
        "sucursal": sucursal,
        "proveedor": proveedor,
        "cliente": cliente,
        "trabajador": trabajador,
        "tipos_cliente": list(TipoCliente),
        "inv_movs": inv_movs,
        "pagos": pagos,
        "price_map_json": price_map_json,
        "price_map_by_material": price_map_by_material,
        "saldo_pendiente": saldo_pendiente,
        "folio": folio,
        "is_transfer": is_transfer,
        "transfer_related": transfer_related,
        "transfer_related_sucursal": transfer_related_sucursal,
        "pago_updated": pago_updated,
        "precios_updated": precios_updated,
        "edit_updated": edit_updated,
        "error": error,
    }
    context.update(base_form_state)
    if form_state:
        context.update(form_state)
    return templates.TemplateResponse(
        "admin/note_detail.html",
        context,
        status_code=400 if error else 200,
    )


def _render_nota_edit(
    request: Request,
    db: Session,
    current_user: dict,
    nota: Nota,
    *,
    error: str | None = None,
    comentario_edicion: str | None = None,
    saved: bool = False,
):
    sucursal = db.get(Sucursal, nota.sucursal_id) if nota.sucursal_id else None
    proveedor = db.get(Proveedor, nota.proveedor_id) if nota.proveedor_id else None
    cliente = db.get(Cliente, nota.cliente_id) if nota.cliente_id else None
    trabajador = db.get(User, nota.trabajador_id) if nota.trabajador_id else None
    saldo_pendiente = Decimal(str(nota.total_monto or 0)) - Decimal(str(nota.monto_pagado or 0))
    if saldo_pendiente < Decimal("0"):
        saldo_pendiente = Decimal("0")
    folio = note_service.format_folio(
        sucursal_id=nota.sucursal_id,
        tipo_operacion=nota.tipo_operacion,
        folio_seq=nota.folio_seq,
    )
    is_transfer = _is_transfer_note(db, nota, proveedor, cliente)
    transfer_related = None
    transfer_related_sucursal = None
    if is_transfer:
        related_id = _extract_transfer_related_id(nota)
        if related_id:
            transfer_related = db.get(Nota, related_id)
            if transfer_related and transfer_related.sucursal_id:
                transfer_related_sucursal = db.get(Sucursal, transfer_related.sucursal_id)

    return templates.TemplateResponse(
        "admin/note_edit.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "nota": nota,
            "sucursal": sucursal,
            "proveedor": proveedor,
            "cliente": cliente,
            "trabajador": trabajador,
            "tipos_cliente": list(TipoCliente),
            "saldo_pendiente": saldo_pendiente,
            "folio": folio,
            "is_transfer": is_transfer,
            "transfer_related": transfer_related,
            "transfer_related_sucursal": transfer_related_sucursal,
            "comentario_edicion": comentario_edicion or "",
            "saved": saved,
            "error": error,
        },
        status_code=400 if error else 200,
    )


@router.get("/notas/{nota_id}")
async def notas_detail(
    nota_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_nota_access(nota, allowed_suc_ids)

    pago_updated = request.query_params.get("pago") == "1"
    precios_updated = request.query_params.get("precios") == "1"
    edit_updated = request.query_params.get("edit") == "1"
    return _render_nota_detail(
        request,
        db,
        current_user,
        nota,
        pago_updated=pago_updated,
        precios_updated=precios_updated,
        edit_updated=edit_updated,
    )


@router.get("/notas/{nota_id}/evidencias")
async def notas_evidencias(
    nota_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_nota_access(nota, allowed_suc_ids)

    sucursal = db.get(Sucursal, nota.sucursal_id) if nota.sucursal_id else None
    proveedor = db.get(Proveedor, nota.proveedor_id) if nota.proveedor_id else None
    cliente = db.get(Cliente, nota.cliente_id) if nota.cliente_id else None
    trabajador = db.get(User, nota.trabajador_id) if nota.trabajador_id else None
    if nota.tipo_operacion.value == "compra":
        partner_label = "Proveedor"
        partner_name = proveedor.nombre_completo if proveedor else "-"
    else:
        partner_label = "Cliente"
        partner_name = cliente.nombre_completo if cliente else "-"

    evidence_groups = build_evidence_groups(nota)
    total_sub = sum(len(g["subpesajes"]) for g in evidence_groups)
    missing = sum(
        1
        for g in evidence_groups
        for sp in g["subpesajes"]
        if not sp.get("foto_url")
    )

    return templates.TemplateResponse(
        "note_evidencias.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "nota": nota,
            "sucursal": sucursal,
            "partner_label": partner_label,
            "partner_name": partner_name,
            "trabajador_name": trabajador.nombre_completo if trabajador else None,
            "evidence_groups": evidence_groups,
            "total_subpesajes": total_sub,
            "missing_subpesajes": missing,
            "can_upload": True,
            "upload_action_base": f"/web/admin/notas/{nota.id}/subpesajes",
            "back_url": f"/web/admin/notas/{nota.id}",
            "max_mb": settings.FIREBASE_MAX_MB,
            "capture_mode": None,
            "updated": request.query_params.get("updated"),
            "error": request.query_params.get("error"),
        },
    )


@router.get("/notas/{nota_id}/factura")
async def notas_factura(
    nota_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_nota_access(nota, allowed_suc_ids)
    if nota.estado != NotaEstado.aprobada:
        raise HTTPException(status_code=400, detail="La nota debe estar aprobada.")
    if nota.factura_url:
        return RedirectResponse(url=nota.factura_url, status_code=302)

    pdf_bytes, filename = invoice_service.build_invoice_pdf(db, nota)
    if current_user.get("rol") == UserRole.super_admin.value:
        try:
            factura_url = invoice_service.upload_invoice_pdf(pdf_bytes, filename, nota.id)
            if factura_url:
                nota.factura_url = factura_url
                nota.factura_generada_at = datetime.utcnow()
                db.add(nota)
                db.commit()
                return RedirectResponse(url=factura_url, status_code=302)
        except Exception:
            db.rollback()

    headers = {"Content-Disposition": f"attachment; filename={filename}"}
    return StreamingResponse(io.BytesIO(pdf_bytes), media_type="application/pdf", headers=headers)


@router.get("/notas/{nota_id}/editar")
async def notas_edit_get(
    nota_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    if nota.estado == NotaEstado.cancelada:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="No puedes editar una nota cancelada.",
        )
    saved = request.query_params.get("saved") == "1"
    return _render_nota_edit(
        request,
        db,
        current_user,
        nota,
        saved=saved,
    )


@router.post("/notas/{nota_id}/editar")
async def notas_edit_post(
    nota_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    if nota.estado == NotaEstado.cancelada:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="No puedes editar una nota cancelada.",
        )

    def parse_decimal(raw: str | None, field: str, default: Decimal | None = None) -> Decimal:
        if raw is None or str(raw).strip() == "":
            if default is not None:
                return default
            raise ValueError(f"{field} es obligatorio.")
        try:
            return Decimal(str(raw))
        except (InvalidOperation, TypeError):
            raise ValueError(f"{field} es invalido.")

    form = await request.form()
    comentario_edicion = (form.get("comentario_edicion") or "").strip() or None

    try:
        tipo_cliente_map: dict[int, TipoCliente] = {}
        kg_override_map: dict[int, tuple[Decimal, Decimal]] = {}
        subpesaje_map: dict[int, tuple[Decimal, Decimal]] = {}

        for nm in nota.materiales:
            tipo_raw = (form.get(f"tipo_cliente_{nm.id}") or "").strip()
            if tipo_raw:
                try:
                    tipo_cliente_map[nm.id] = TipoCliente(tipo_raw)
                except ValueError:
                    raise ValueError("Tipo de precio invalido.")

            if nm.subpesajes:
                for sp in nm.subpesajes:
                    peso_raw = form.get(f"sp_peso_{sp.id}")
                    desc_raw = form.get(f"sp_desc_{sp.id}")
                    peso = parse_decimal(peso_raw, "Peso bruto")
                    desc = parse_decimal(desc_raw, "Descuento", default=Decimal("0"))
                    if peso <= 0:
                        raise ValueError("El peso bruto debe ser mayor a 0.")
                    if desc < 0:
                        raise ValueError("El descuento no puede ser negativo.")
                    if desc > peso:
                        raise ValueError("El descuento no puede ser mayor al peso bruto.")
                    subpesaje_map[sp.id] = (peso, desc)
            else:
                kg_bruto = parse_decimal(form.get(f"kg_bruto_{nm.id}"), "Kg bruto")
                kg_desc = parse_decimal(
                    form.get(f"kg_desc_{nm.id}"),
                    "Kg descuento",
                    default=Decimal("0"),
                )
                if kg_bruto <= 0:
                    raise ValueError("El kg bruto debe ser mayor a 0.")
                if kg_desc < 0:
                    raise ValueError("El kg descuento no puede ser negativo.")
                if kg_desc > kg_bruto:
                    raise ValueError("El kg descuento no puede ser mayor al kg bruto.")
                kg_override_map[nm.id] = (kg_bruto, kg_desc)

        note_service.edit_note_by_superadmin(
            db,
            nota,
            tipo_cliente_map=tipo_cliente_map,
            kg_override_map=kg_override_map,
            subpesaje_map=subpesaje_map,
            admin_id=current_user.get("id"),
            comentario=comentario_edicion,
        )
    except ValueError as exc:
        db.rollback()
        return _render_nota_edit(
            request,
            db,
            current_user,
            nota,
            error=str(exc),
            comentario_edicion=comentario_edicion,
        )

    return RedirectResponse(url=f"/web/admin/notas/{nota_id}?edit=1", status_code=303)


@router.post("/notas/{nota_id}/subpesajes/{subpesaje_id}/evidencia")
async def notas_subpesaje_upload(
    nota_id: int,
    subpesaje_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_nota_access(nota, allowed_suc_ids)

    subpesaje = (
        db.query(Subpesaje)
        .join(NotaMaterial, NotaMaterial.id == Subpesaje.nota_material_id)
        .filter(Subpesaje.id == subpesaje_id, NotaMaterial.nota_id == nota_id)
        .first()
    )
    if not subpesaje:
        raise HTTPException(status_code=404, detail="Subpesaje no encontrado.")

    if not file.content_type or not file.content_type.startswith("image/"):
        return RedirectResponse(
            url=f"/web/admin/notas/{nota_id}/evidencias?error=tipo",
            status_code=303,
        )

    content = await file.read()
    max_bytes = settings.FIREBASE_MAX_MB * 1024 * 1024
    if len(content) > max_bytes:
        return RedirectResponse(
            url=f"/web/admin/notas/{nota_id}/evidencias?error=peso",
            status_code=303,
        )

    try:
        url = upload_image(
            content=content,
            filename=file.filename or "evidencia",
            content_type=file.content_type,
            folder=f"evidencias/nota_{nota_id}/sub_{subpesaje_id}",
        )
    except Exception:
        return RedirectResponse(
            url=f"/web/admin/notas/{nota_id}/evidencias?error=upload",
            status_code=303,
        )

    subpesaje.foto_url = url
    db.add(subpesaje)
    db.commit()

    return RedirectResponse(
        url=f"/web/admin/notas/{nota_id}/evidencias?updated=1",
        status_code=303,
    )


@router.post("/notas/{nota_id}/aprobar")
async def notas_aprobar(
    nota_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_nota_access(nota, allowed_suc_ids)
    if nota.estado not in (NotaEstado.en_revision, NotaEstado.borrador):
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="Solo puedes aprobar notas en revisión o borrador.",
        )

    form = await request.form()
    comentarios_admin = (form.get("comentarios_admin") or "").strip()
    fecha_caducidad_pago_raw = (form.get("fecha_caducidad_pago") or "").strip()
    metodo_pago = (form.get("metodo_pago") or "").strip().lower()
    cuenta_financiera = (form.get("cuenta_financiera") or "").strip()
    monto_pagado_raw = (form.get("monto_pagado") or "").strip()
    form_state = {
        "form_metodo": metodo_pago,
        "form_cuenta": cuenta_financiera,
        "form_fecha": fecha_caducidad_pago_raw,
        "form_comentarios": comentarios_admin,
        "form_pagado": monto_pagado_raw,
    }

    fecha_caducidad_pago = None
    if fecha_caducidad_pago_raw:
        try:
            fecha_caducidad_pago = datetime.strptime(fecha_caducidad_pago_raw, "%Y-%m-%d").date()
        except ValueError:
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error="La fecha de caducidad de pago es inválida.",
                form_state=form_state,
            )

    tipo_cliente_map: dict[int, TipoCliente] = {}
    for key, value in form.items():
        if key.startswith("tipo_cliente_"):
            nm_key = key.rsplit("_", 1)[-1]
            try:
                nm_id = int(nm_key)
            except ValueError:
                continue
            if value:
                try:
                    tipo_cliente_map[nm_id] = TipoCliente(value)
                except ValueError:
                    return _render_nota_detail(
                        request,
                        db,
                        current_user,
                        nota,
                        error="Tipo de cliente inválido para un material.",
                        form_state=form_state,
                    )

    monto_pagado = None
    if monto_pagado_raw:
        try:
            monto_pagado = Decimal(str(monto_pagado_raw))
        except (InvalidOperation, TypeError):
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error="El pago inicial es invA­lido.",
                form_state=form_state,
            )

    try:
        note_service.approve_note(
            db,
            nota,
            tipo_cliente_map=tipo_cliente_map or None,
            admin_id=current_user.get("id"),
            comentarios_admin=comentarios_admin,
            fecha_caducidad_pago=fecha_caducidad_pago,
            metodo_pago=metodo_pago,
            cuenta_financiera=cuenta_financiera or None,
            monto_pagado=monto_pagado,
        )
    except ValueError as e:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error=str(e),
            form_state=form_state,
        )

    if current_user.get("rol") == UserRole.super_admin.value:
        try:
            pdf_bytes, filename = invoice_service.build_invoice_pdf(db, nota)
            factura_url = invoice_service.upload_invoice_pdf(pdf_bytes, filename, nota.id)
            if factura_url:
                nota.factura_url = factura_url
                nota.factura_generada_at = datetime.utcnow()
                db.add(nota)
                db.commit()
        except Exception:
            db.rollback()

    return RedirectResponse(url="/web/admin/notas?approved=1", status_code=303)


@router.post("/notas/{nota_id}/precios")
async def notas_actualizar_precios(
    nota_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_nota_access(nota, allowed_suc_ids)
    if nota.estado == NotaEstado.aprobada:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="No puedes actualizar precios en una nota aprobada.",
        )

    form = await request.form()
    comentarios_admin = (form.get("comentarios_admin") or "").strip()
    fecha_caducidad_pago_raw = (form.get("fecha_caducidad_pago") or "").strip()
    metodo_pago = (form.get("metodo_pago") or "").strip().lower()
    cuenta_financiera = (form.get("cuenta_financiera") or "").strip()
    monto_pagado_raw = (form.get("monto_pagado") or "").strip()
    form_state = {
        "form_metodo": metodo_pago,
        "form_cuenta": cuenta_financiera,
        "form_fecha": fecha_caducidad_pago_raw,
        "form_comentarios": comentarios_admin,
        "form_pagado": monto_pagado_raw,
    }

    tipo_cliente_map: dict[int, TipoCliente] = {}
    for key, value in form.items():
        if key.startswith("tipo_cliente_"):
            nm_key = key.rsplit("_", 1)[-1]
            try:
                nm_id = int(nm_key)
            except ValueError:
                continue
            if not value:
                continue
            try:
                tipo_cliente_map[nm_id] = TipoCliente(value)
            except ValueError:
                return _render_nota_detail(
                    request,
                    db,
                    current_user,
                    nota,
                    error="Tipo de cliente invA­lido para un material.",
                    form_state=form_state,
                )
    if not tipo_cliente_map:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="No hay cambios de precio para actualizar.",
            form_state=form_state,
        )

    note_service.set_tipo_cliente_and_prices(db, nota, tipo_cliente_map)
    return RedirectResponse(url=f"/web/admin/notas/{nota_id}?precios=1", status_code=303)


@router.post("/notas/{nota_id}/pago")
async def notas_actualizar_pago(
    nota_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_nota_access(nota, allowed_suc_ids)
    if nota.estado != NotaEstado.aprobada:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="Solo puedes registrar pagos en notas aprobadas.",
        )

    form = await request.form()
    monto_pagado_raw = (form.get("monto_pagado") or "").strip()
    metodo_pago = (form.get("pago_metodo") or "").strip().lower()
    cuenta_financiera = (form.get("pago_cuenta") or "").strip()
    comentario = (form.get("pago_comentario") or "").strip()
    form_state = {
        "form_pago_monto": monto_pagado_raw,
        "form_pago_metodo": metodo_pago,
        "form_pago_cuenta": cuenta_financiera,
        "form_pago_comentario": comentario,
    }
    if not monto_pagado_raw:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="Debes indicar el monto pagado.",
            form_state=form_state,
        )
    try:
        monto_pagado = Decimal(str(monto_pagado_raw))
    except (InvalidOperation, TypeError):
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="El monto pagado es invA­lido.",
            form_state=form_state,
        )

    try:
        note_service.add_payment(
            db,
            nota,
            monto_pagado=monto_pagado,
            usuario_id=current_user.get("id"),
            metodo_pago=metodo_pago or None,
            cuenta_financiera=cuenta_financiera or None,
            comentario=comentario or None,
        )
    except ValueError as e:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error=str(e),
            form_state=form_state,
        )

    return RedirectResponse(url=f"/web/admin/notas/{nota_id}?pago=1", status_code=303)


@router.post("/notas/{nota_id}/cancelar")
async def notas_cancelar(
    nota_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_nota_access(nota, allowed_suc_ids)
    if nota.estado == NotaEstado.aprobada:
        return _render_nota_detail(
            request, db, current_user, nota, error="No puedes rechazar una nota aprobada."
        )
    form = await request.form()
    comentarios_admin = (form.get("comentarios_admin") or "").strip()
    note_service.update_state(
        db,
        nota,
        new_state=NotaEstado.cancelada,
        admin_id=current_user.get("id"),
        comentarios_admin=comentarios_admin,
    )
    return RedirectResponse(url="/web/admin/notas?cancelled=1", status_code=303)


@router.post("/notas/{nota_id}/devolver")
async def notas_devolver(
    nota_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_nota_access(nota, allowed_suc_ids)
    if nota.estado == NotaEstado.aprobada:
        return _render_nota_detail(
            request, db, current_user, nota, error="No puedes devolver una nota aprobada."
        )
    note_service.update_state(
        db,
        nota,
        new_state=NotaEstado.borrador,
        admin_id=current_user.get("id"),
    )
    return RedirectResponse(url="/web/admin/notas?returned=1", status_code=303)


@router.post("/notas/{nota_id}/eliminar")
async def notas_eliminar(
    nota_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    if nota.estado == NotaEstado.aprobada:
        return _render_nota_detail(
            request, db, current_user, nota, error="No puedes eliminar una nota aprobada."
        )
    db.delete(nota)
    db.commit()
    return RedirectResponse(url="/web/admin/notas?deleted=1", status_code=303)


@router.get("/inventario/ajuste")
async def inventario_ajuste_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.nombre).all()
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    suc_ids = [s.id for s in sucursales]
    inv_rows = db.query(Inventario).filter(Inventario.sucursal_id.in_(suc_ids)).all() if suc_ids else []
    inv_map: dict[int, dict[int, float]] = {}
    for inv in inv_rows:
        inv_map.setdefault(inv.sucursal_id, {})[inv.material_id] = float(inv.stock_actual or 0)
    return templates.TemplateResponse(
        "admin/inventario_ajuste.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "materiales": materiales,
            "sucursales": sucursales,
            "inv_map": inv_map,
            "error": None,
        },
    )


@router.post("/inventario/ajuste")
async def inventario_ajuste_post(
    request: Request,
    sucursal_id: str = Form(...),
    material_id: str = Form(...),
    cantidad_kg: str = Form(""),
    nuevo_stock: str = Form(""),
    comentario: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.nombre).all()
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)

    suc_ids = [s.id for s in sucursales]
    inv_rows = db.query(Inventario).filter(Inventario.sucursal_id.in_(suc_ids)).all() if suc_ids else []
    inv_map: dict[int, dict[int, float]] = {}
    for inv in inv_rows:
        inv_map.setdefault(inv.sucursal_id, {})[inv.material_id] = float(inv.stock_actual or 0)

    def render_error(msg: str):
        return templates.TemplateResponse(
            "admin/inventario_ajuste.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "materiales": materiales,
                "sucursales": sucursales,
                "inv_map": inv_map,
                "error": msg,
            },
            status_code=400,
        )

    if allowed_suc_ids:
        if not sucursal_id:
            if len(allowed_suc_ids) == 1:
                sucursal_id = str(allowed_suc_ids[0])
            else:
                return render_error("Selecciona una sucursal valida.")

    try:
        suc_id = int(sucursal_id)
        mat_id = int(material_id)
    except ValueError:
        return render_error("Sucursal o material inválido.")

    if allowed_suc_ids and suc_id not in allowed_suc_ids:
        return render_error("Sucursal no autorizada.")

    suc = db.get(Sucursal, suc_id)
    if not suc:
        return render_error("Sucursal no encontrada.")
    mat = db.get(Material, mat_id)
    if not mat:
        return render_error("Material no encontrado.")

    # decidir delta: si se envía nuevo stock, usarlo como objetivo; si no, usar delta directo
    nuevo_stock_raw = (nuevo_stock or "").strip()
    inv_actual = db.query(Inventario).filter(
        Inventario.sucursal_id == suc_id, Inventario.material_id == mat_id
    ).first()
    stock_actual = Decimal(str(inv_actual.stock_actual or 0)) if inv_actual else Decimal("0")
    delta: Decimal
    if nuevo_stock_raw:
        try:
            nuevo_stock = Decimal(str(nuevo_stock_raw))
        except (InvalidOperation, TypeError):
            return render_error("El nuevo stock es inválido.")
        if nuevo_stock < 0:
            nuevo_stock = Decimal("0")
        delta = nuevo_stock - stock_actual
    else:
        try:
            delta = Decimal(str(cantidad_kg))
        except (InvalidOperation, TypeError):
            return render_error("Cantidad inválida.")

    comentario = (comentario or "").strip() or "Ajuste manual"

    note_service.ajustar_stock(
        db,
        sucursal_id=suc.id,
        material_id=mat.id,
        cantidad_kg=delta,
        comentario=comentario,
        usuario_id=current_user.get("id"),
    )
    return RedirectResponse(url="/web/admin/inventario?ajuste=1", status_code=303)


@router.get("/inventario")
async def inventario_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)

    sel = request.query_params.get("sucursal_id")
    sucursal_id = None
    if sel:
        try:
            sucursal_id = int(sel)
        except ValueError:
            sucursal_id = None
    if allowed_suc_ids is not None:
        if sucursal_id and sucursal_id not in allowed_suc_ids:
            sucursal_id = None
        if sucursal_id is None and len(allowed_suc_ids) == 1:
            sucursal_id = allowed_suc_ids[0]

    query = db.query(Inventario)
    if allowed_suc_ids is not None:
        if sucursal_id:
            query = query.filter(Inventario.sucursal_id == sucursal_id)
        else:
            query = query.filter(Inventario.sucursal_id.in_(allowed_suc_ids))
    elif sucursal_id:
        query = query.filter(Inventario.sucursal_id == sucursal_id)
    inventarios = query.order_by(Inventario.sucursal_id, Inventario.material_id).all()
    return templates.TemplateResponse(
        "admin/inventario_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "inventarios": inventarios,
            "sucursales": sucursales,
            "sucursal_id": sucursal_id,
        },
    )


@router.get("/contabilidad")
async def contabilidad_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    params = request.query_params
    sucursal_id = None
    if params.get("sucursal_id"):
        try:
            sucursal_id = int(params.get("sucursal_id"))
        except ValueError:
            sucursal_id = None
    if allowed_suc_ids is not None:
        if sucursal_id and sucursal_id not in allowed_suc_ids:
            sucursal_id = None
        if sucursal_id is None and len(allowed_suc_ids) == 1:
            sucursal_id = allowed_suc_ids[0]

    date_from = params.get("from")
    date_to = params.get("to")
    export_query = request.url.query
    fmt = params.get("format") or "csv"
    query = db.query(MovimientoContable)
    if allowed_suc_ids is not None:
        if sucursal_id:
            query = query.filter(MovimientoContable.sucursal_id == sucursal_id)
        else:
            query = query.filter(MovimientoContable.sucursal_id.in_(allowed_suc_ids))
    elif sucursal_id:
        query = query.filter(MovimientoContable.sucursal_id == sucursal_id)
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
            query = query.filter(MovimientoContable.created_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d")
            query = query.filter(MovimientoContable.created_at <= dt_to)
        except ValueError:
            pass
    movimientos = query.order_by(MovimientoContable.created_at.desc()).limit(200).all()
    total_filtrado = sum([float(m.monto or 0) for m in movimientos])
    return templates.TemplateResponse(
        "admin/contabilidad_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "movimientos": movimientos,
            "sucursales": sucursales,
            "sucursal_id": sucursal_id,
            "date_from": date_from or "",
            "date_to": date_to or "",
            "total_filtrado": total_filtrado,
            "export_query": export_query,
        },
    )

@router.get("/contabilidad/export")
async def contabilidad_export(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    params = request.query_params
    sucursal_id = None
    if params.get("sucursal_id"):
        try:
            sucursal_id = int(params.get("sucursal_id"))
        except ValueError:
            sucursal_id = None
    if allowed_suc_ids is not None:
        if sucursal_id and sucursal_id not in allowed_suc_ids:
            sucursal_id = None
        if sucursal_id is None and len(allowed_suc_ids) == 1:
            sucursal_id = allowed_suc_ids[0]

    date_from = params.get("from")
    date_to = params.get("to")
    fmt = params.get("format") or "csv"
    query = db.query(MovimientoContable)
    if allowed_suc_ids is not None:
        if sucursal_id:
            query = query.filter(MovimientoContable.sucursal_id == sucursal_id)
        else:
            query = query.filter(MovimientoContable.sucursal_id.in_(allowed_suc_ids))
    elif sucursal_id:
        query = query.filter(MovimientoContable.sucursal_id == sucursal_id)
    if date_from:
        try:
            dt_from = datetime.strptime(date_from, "%Y-%m-%d")
            query = query.filter(MovimientoContable.created_at >= dt_from)
        except ValueError:
            pass
    if date_to:
        try:
            dt_to = datetime.strptime(date_to, "%Y-%m-%d")
            query = query.filter(MovimientoContable.created_at <= dt_to)
        except ValueError:
            pass
    movimientos = query.order_by(MovimientoContable.created_at.desc()).limit(1000).all()

    if fmt == "csv":
        import csv
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "id", "tipo", "monto", "nota_id", "sucursal",
            "usuario_id", "metodo_pago", "cuenta_financiera", "comentario", "created_at",
        ])
        for m in movimientos:
            writer.writerow([
                m.id,
                m.tipo,
                float(m.monto or 0),
                m.nota_id or "",
                m.sucursal.nombre if m.sucursal else m.sucursal_id or "",
                m.usuario_id or "",
                m.metodo_pago or "",
                m.cuenta_financiera or "",
                (m.comentario or "").replace("\n", " "),
                m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else "",
            ])
        output.seek(0)
        headers = {"Content-Disposition": "attachment; filename=movimientos_contables.csv"}
        return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers=headers)

    headers_xml = ["id", "tipo", "monto", "nota_id", "sucursal", "usuario_id", "metodo_pago", "cuenta_financiera", "comentario", "created_at"]

    if fmt in ("xlsx", "xls", "excel"):
        import io
        rows = []
        rows.append("<Row>" + "".join([f"<Cell><Data ss:Type='String'>{h}</Data></Cell>" for h in headers_xml]) + "</Row>")
        for m in movimientos:
            vals = [
                m.id,
                m.tipo,
                float(m.monto or 0),
                m.nota_id or "",
                m.sucursal.nombre if m.sucursal else m.sucursal_id or "",
                m.usuario_id or "",
                m.metodo_pago or "",
                m.cuenta_financiera or "",
                (m.comentario or "").replace("\\n", " "),
                m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else "",
            ]
            rows.append("<Row>" + "".join([f"<Cell><Data ss:Type='String'>{v}</Data></Cell>" for v in vals]) + "</Row>")
        workbook = f"""<?xml version="1.0"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:x="urn:schemas-microsoft-com:office:excel"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
 <Worksheet ss:Name="Movimientos">
  <Table>
   {''.join(rows)}
  </Table>
 </Worksheet>
</Workbook>"""
        content = workbook.encode("utf-8")
        headers = {"Content-Disposition": "attachment; filename=movimientos_contables.xls"}
        return StreamingResponse(io.BytesIO(content), media_type="application/vnd.ms-excel", headers=headers)

    # PDF fallback (simple text-based)
    import io

    def _escape_pdf(txt: str) -> str:
        return txt.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    header_line = " | ".join(headers_xml)
    suc_label = f"Sucursal: {sucursal_id or 'Todas'}"
    range_label = f"Rango: {date_from or '---'} a {date_to or '---'}"
    text_lines = ["Movimientos contables", suc_label, range_label, "", header_line]
    for m in movimientos:
        vals = [
            str(m.id),
            m.tipo,
            f"{float(m.monto or 0):.2f}",
            str(m.nota_id or ""),
            m.sucursal.nombre if m.sucursal else str(m.sucursal_id or ""),
            str(m.usuario_id or ""),
            m.metodo_pago or "",
            m.cuenta_financiera or "",
            (m.comentario or "").replace("\n", " "),
            m.created_at.strftime("%Y-%m-%d %H:%M") if m.created_at else "",
        ]
        text_lines.append(" | ".join(vals))

    stream_lines = [f"({_escape_pdf(line)}) Tj T*" for line in text_lines]
    stream_content = "BT /F1 10 Tf 12 TL 50 780 Td\n" + "\n".join(stream_lines) + "\nET"
    stream_bytes = stream_content.encode("latin-1", errors="ignore")
    len_stream = len(stream_bytes)

    objects = []
    def obj(num: int, body: str) -> None:
        objects.append((num, body.encode("latin-1")))

    obj(1, "<< /Type /Catalog /Pages 2 0 R >>")
    obj(2, "<< /Type /Pages /Count 1 /Kids [3 0 R] >>")
    obj(3, "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>")
    obj(4, f"<< /Length {len_stream} >>\nstream\n".encode() + stream_bytes + b"\nendstream")
    obj(5, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n")
    offsets = [0]
    for num, body in objects:
        offsets.append(buffer.tell())
        buffer.write(f"{num} 0 obj\n".encode())
        buffer.write(body)
        buffer.write(b"\nendobj\n")
    xref_pos = buffer.tell()
    buffer.write(f"xref\n0 {len(offsets)}\n".encode())
    buffer.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buffer.write(f"{off:010} 00000 n \n".encode())
    buffer.write(b"trailer\n")
    buffer.write(f"<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode())

    headers = {"Content-Disposition": "attachment; filename=movimientos_contables.pdf"}
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/pdf", headers=headers)


@router.get("/contabilidad/reporte")
async def contabilidad_reporte(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    params = request.query_params
    sucursal_id = None
    if params.get("sucursal_id"):
        try:
            sucursal_id = int(params.get("sucursal_id"))
        except ValueError:
            sucursal_id = None
    if allowed_suc_ids is not None:
        if sucursal_id and sucursal_id not in allowed_suc_ids:
            sucursal_id = None
        if sucursal_id is None and len(allowed_suc_ids) == 1:
            sucursal_id = allowed_suc_ids[0]

    date_from = None
    date_to = None
    if params.get("from"):
        try:
            date_from = datetime.strptime(params.get("from"), "%Y-%m-%d").date()
        except ValueError:
            date_from = None
    if params.get("to"):
        try:
            date_to = datetime.strptime(params.get("to"), "%Y-%m-%d").date()
        except ValueError:
            date_to = None

    report = contabilidad_report_service.build_report_data(
        db,
        sucursal_id=sucursal_id,
        date_from=date_from,
        date_to=date_to,
        allowed_suc_ids=allowed_suc_ids,
    )

    fmt = (params.get("format") or "pdf").lower()
    if fmt in ("xlsx", "xls", "excel"):
        content, filename = contabilidad_report_service.build_report_excel(report)
        headers = {"Content-Disposition": f"attachment; filename={filename}"}
        return StreamingResponse(
            io.BytesIO(content),
            media_type="application/vnd.ms-excel",
            headers=headers,
        )
    if fmt == "pdf":
        content, filename = contabilidad_report_service.build_report_pdf(report)
        headers = {"Content-Disposition": f"attachment; filename={filename}"}
        return StreamingResponse(
            io.BytesIO(content),
            media_type="application/pdf",
            headers=headers,
        )

    raise HTTPException(status_code=400, detail="Formato de reporte invalido.")


@router.get("/inventario/movimientos")
async def inventario_movimientos(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    materiales = db.query(Material).order_by(Material.nombre).all()
    params = request.query_params
    sucursal_id = None
    if params.get("sucursal_id"):
        try:
            sucursal_id = int(params.get("sucursal_id"))
        except ValueError:
            sucursal_id = None
    if allowed_suc_ids is not None:
        if sucursal_id and sucursal_id not in allowed_suc_ids:
            sucursal_id = None
        if sucursal_id is None and len(allowed_suc_ids) == 1:
            sucursal_id = allowed_suc_ids[0]

    material_id = None
    if params.get("material_id"):
        try:
            material_id = int(params.get("material_id"))
        except ValueError:
            material_id = None
    tipo = params.get("tipo") or None

    query = db.query(InventarioMovimiento)
    if allowed_suc_ids is not None:
        if sucursal_id:
            query = query.join(Inventario, Inventario.id == InventarioMovimiento.inventario_id).filter(
                Inventario.sucursal_id == sucursal_id
            )
        else:
            query = query.join(Inventario, Inventario.id == InventarioMovimiento.inventario_id).filter(
                Inventario.sucursal_id.in_(allowed_suc_ids)
            )
    elif sucursal_id:
        query = query.join(Inventario, Inventario.id == InventarioMovimiento.inventario_id).filter(
            Inventario.sucursal_id == sucursal_id
        )
    if material_id:
        query = query.join(Inventario, Inventario.id == InventarioMovimiento.inventario_id).filter(
            Inventario.material_id == material_id
        )
    if tipo:
        query = query.filter(InventarioMovimiento.tipo == tipo)
    movimientos = query.order_by(InventarioMovimiento.created_at.desc()).limit(200).all()
    total_firmado = 0
    for mov in movimientos:
        total_firmado += float(_signed_inventario_qty(mov))

    return templates.TemplateResponse(
        "admin/inventario_movimientos.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "movimientos": movimientos,
            "sucursales": sucursales,
            "materiales": materiales,
            "sucursal_id": sucursal_id,
            "material_id": material_id,
            "tipo": tipo or "",
            "total_firmado": total_firmado,
        },
    )


@router.get("/inventario/movimientos/export")
async def inventario_movimientos_export(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    materiales = db.query(Material).order_by(Material.nombre).all()
    params = request.query_params
    sucursal_id = None
    if params.get("sucursal_id"):
        try:
            sucursal_id = int(params.get("sucursal_id"))
        except ValueError:
            sucursal_id = None
    if allowed_suc_ids is not None:
        if sucursal_id and sucursal_id not in allowed_suc_ids:
            sucursal_id = None
        if sucursal_id is None and len(allowed_suc_ids) == 1:
            sucursal_id = allowed_suc_ids[0]

    material_id = None
    if params.get("material_id"):
        try:
            material_id = int(params.get("material_id"))
        except ValueError:
            material_id = None
    tipo = params.get("tipo") or None
    fmt = params.get("format") or "csv"

    query = db.query(InventarioMovimiento)
    if allowed_suc_ids is not None:
        if sucursal_id:
            query = query.join(Inventario, Inventario.id == InventarioMovimiento.inventario_id).filter(
                Inventario.sucursal_id == sucursal_id
            )
        else:
            query = query.join(Inventario, Inventario.id == InventarioMovimiento.inventario_id).filter(
                Inventario.sucursal_id.in_(allowed_suc_ids)
            )
    elif sucursal_id:
        query = query.join(Inventario, Inventario.id == InventarioMovimiento.inventario_id).filter(
            Inventario.sucursal_id == sucursal_id
        )
    if material_id:
        query = query.join(Inventario, Inventario.id == InventarioMovimiento.inventario_id).filter(
            Inventario.material_id == material_id
        )
    if tipo:
        query = query.filter(InventarioMovimiento.tipo == tipo)
    movimientos = query.order_by(InventarioMovimiento.created_at.desc()).limit(1000).all()

    headers_xml = ["sucursal", "material", "tipo", "cantidad_kg", "saldo_resultante", "nota_id", "comentario", "fecha"]

    if fmt == "csv":
        import csv
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(headers_xml)
        for mov in movimientos:
            qty = _signed_inventario_qty(mov)
            writer.writerow([
                mov.inventario.sucursal.nombre if mov.inventario and mov.inventario.sucursal else mov.inventario_id,
                mov.inventario.material.nombre if mov.inventario and mov.inventario.material else "",
                mov.tipo,
                float(qty or 0),
                float(mov.saldo_resultante or 0),
                mov.nota_id or "",
                (mov.comentario or "").replace("\n", " "),
                mov.created_at.strftime("%Y-%m-%d %H:%M") if mov.created_at else "",
            ])
        output.seek(0)
        headers = {"Content-Disposition": "attachment; filename=movimientos_inventario.csv"}
        return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers=headers)

    import io
    rows = []
    rows.append("<Row>" + "".join([f"<Cell><Data ss:Type='String'>{h}</Data></Cell>" for h in headers_xml]) + "</Row>")
    for mov in movimientos:
        qty = _signed_inventario_qty(mov)
        vals = [
            mov.inventario.sucursal.nombre if mov.inventario and mov.inventario.sucursal else mov.inventario_id,
            mov.inventario.material.nombre if mov.inventario and mov.inventario.material else "",
            mov.tipo,
            float(qty or 0),
            float(mov.saldo_resultante or 0),
            mov.nota_id or "",
            (mov.comentario or "").replace("\\n", " "),
            mov.created_at.strftime("%Y-%m-%d %H:%M") if mov.created_at else "",
        ]
        rows.append("<Row>" + "".join([f"<Cell><Data ss:Type='String'>{v}</Data></Cell>" for v in vals]) + "</Row>")

    if fmt in ("xlsx", "xls", "excel"):
        workbook = f"""<?xml version="1.0"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:o="urn:schemas-microsoft-com:office:office"
 xmlns:x="urn:schemas-microsoft-com:office:excel"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
 <Worksheet ss:Name="Movimientos">
  <Table>
   {''.join(rows)}
 </Table>
 </Worksheet>
</Workbook>"""
        content = workbook.encode("utf-8")
        headers = {"Content-Disposition": "attachment; filename=movimientos_inventario.xls"}
        return StreamingResponse(io.BytesIO(content), media_type="application/vnd.ms-excel", headers=headers)

    # PDF simple
    def _escape_pdf(txt: str) -> str:
        return txt.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    header_line = " | ".join(headers_xml)
    text_lines = ["Movimientos de inventario", header_line]
    for mov in movimientos:
        qty = _signed_inventario_qty(mov)
        vals = [
            mov.inventario.sucursal.nombre if mov.inventario and mov.inventario.sucursal else str(mov.inventario_id),
            mov.inventario.material.nombre if mov.inventario and mov.inventario.material else "",
            mov.tipo,
            f"{float(qty or 0):.2f}",
            f"{float(mov.saldo_resultante or 0):.2f}",
            str(mov.nota_id or ""),
            (mov.comentario or "").replace("\\n", " "),
            mov.created_at.strftime("%Y-%m-%d %H:%M") if mov.created_at else "",
        ]
        text_lines.append(" | ".join(vals))

    stream_lines = [f"({_escape_pdf(line)}) Tj T*" for line in text_lines]
    stream_content = "BT /F1 10 Tf 12 TL 50 780 Td\n" + "\n".join(stream_lines) + "\nET"
    stream_bytes = stream_content.encode("latin-1", errors="ignore")
    len_stream = len(stream_bytes)

    objects = []
    def obj(num: int, body: str) -> None:
        objects.append((num, body.encode("latin-1") if isinstance(body, str) else body))

    obj(1, "<< /Type /Catalog /Pages 2 0 R >>")
    obj(2, "<< /Type /Pages /Count 1 /Kids [3 0 R] >>")
    obj(3, "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>")
    obj(4, f"<< /Length {len_stream} >>\nstream\n".encode() + stream_bytes + b"\nendstream")
    obj(5, "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    buffer = io.BytesIO()
    buffer.write(b"%PDF-1.4\n")
    offsets = [0]
    for num, body in objects:
        offsets.append(buffer.tell())
        buffer.write(f"{num} 0 obj\n".encode())
        buffer.write(body)
        buffer.write(b"\nendobj\n")
    xref_pos = buffer.tell()
    buffer.write(f"xref\n0 {len(offsets)}\n".encode())
    buffer.write(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        buffer.write(f"{off:010} 00000 n \n".encode())
    buffer.write(b"trailer\n")
    buffer.write(f"<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF".encode())

    headers = {"Content-Disposition": "attachment; filename=movimientos_inventario.pdf"}
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="application/pdf", headers=headers)


