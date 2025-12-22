# app/web/admin.py
import os
import uuid
from fastapi import APIRouter, Depends, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import or_
from decimal import Decimal, InvalidOperation
from datetime import datetime
from typing import List

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
    Inventario,
    MovimientoContable,
    Material,
    InventarioMovimiento,
)

from app.services.pricing_service import create_price_version
from app.services import note_service
from app.services.evidence_service import build_evidence_groups
from app.services.firebase_storage import upload_image

templates = Jinja2Templates(directory="app/templates")
settings = get_settings()

router = APIRouter(prefix="/web/admin", tags=["web-admin"])

LOGOS_DIR = os.path.join("app", "static", "uploads", "logos")
os.makedirs(LOGOS_DIR, exist_ok=True)

def _save_logo(upload: UploadFile | None) -> str | None:
    if not upload or not upload.filename:
        return None
    _, ext = os.path.splitext(upload.filename.lower())
    if ext not in [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"]:
        ext = ".png"
    filename = f"logo_{uuid.uuid4().hex}{ext}"
    dest_path = os.path.join(LOGOS_DIR, filename)
    data = upload.file.read()
    with open(dest_path, "wb") as f:
        f.write(data)
    return f"/static/uploads/logos/{filename}"


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
    logo_url: str = Form(""),
    logo_file: UploadFile | None = File(None),
    admin_ids: List[str] = Form([]),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    nombre = nombre.strip()
    direccion = direccion.strip()
    logo_url = logo_url.strip()
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
        logo_url=logo_url or None,
    )
    db.add(sucursal)
    db.commit()
    db.refresh(sucursal)

    saved_logo = _save_logo(logo_file)
    if saved_logo:
        sucursal.logo_url = saved_logo
        db.add(sucursal)
        db.commit()

    selected_ids = {int(aid) for aid in admin_ids if aid}
    if selected_ids:
        for admin in admins:
            if admin.id in selected_ids:
                admin.sucursal_id = sucursal.id
                db.add(admin)
    db.commit()

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
    selected_admin_ids = [adm.id for adm in admins if adm.sucursal_id == sucursal.id]
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
    logo_url: str = Form(""),
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
    logo_url = logo_url.strip()
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
    new_logo = _save_logo(logo_file)
    if new_logo:
        sucursal.logo_url = new_logo
    elif logo_url:
        sucursal.logo_url = logo_url
    db.add(sucursal)

    selected_ids_set = set(selected_admin_ids)
    for adm in admins:
        if adm.id in selected_ids_set:
            adm.sucursal_id = sucursal.id
        elif adm.sucursal_id == sucursal.id:
            adm.sucursal_id = None
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

    # Validar sucursal para trabajador
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
        sucursal_id=sucursal_id,
        super_admin_original=False,
    )

    db.add(user)
    db.commit()

    return RedirectResponse(url="/web/admin/users", status_code=303)


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
    notas_revision = (
        db.query(Nota)
        .filter(Nota.estado == NotaEstado.en_revision)
        .order_by(Nota.id.desc())
        .all()
    )
    notas_recientes = (
        db.query(Nota)
        .order_by(Nota.id.desc())
        .limit(10)
        .all()
    )
    sucursales = {s.id: s for s in db.query(Sucursal).all()}
    proveedores = {p.id: p for p in db.query(Proveedor).all()}
    clientes = {c.id: c for c in db.query(Cliente).all()}

    return templates.TemplateResponse(
        "admin/notes_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "notas_revision": notas_revision,
            "notas_recientes": notas_recientes,
            "sucursales": sucursales,
            "proveedores": proveedores,
            "clientes": clientes,
        },
    )


def _render_nota_detail(
    request: Request,
    db: Session,
    current_user: dict,
    nota: Nota,
    error: str | None = None,
    form_state: dict | None = None,
):
    sucursal = db.get(Sucursal, nota.sucursal_id) if nota.sucursal_id else None
    proveedor = db.get(Proveedor, nota.proveedor_id) if nota.proveedor_id else None
    cliente = db.get(Cliente, nota.cliente_id) if nota.cliente_id else None
    trabajador = db.get(User, nota.trabajador_id) if nota.trabajador_id else None
    inv_movs = db.query(InventarioMovimiento).filter(InventarioMovimiento.nota_id == nota.id).all()
    base_form_state = {
        "form_metodo": None,
        "form_cuenta": None,
        "form_fecha": None,
        "form_comentarios": None,
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

    return _render_nota_detail(request, db, current_user, nota)


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
    form_state = {
        "form_metodo": metodo_pago,
        "form_cuenta": cuenta_financiera,
        "form_fecha": fecha_caducidad_pago_raw,
        "form_comentarios": comentarios_admin,
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

    return RedirectResponse(url="/web/admin/notas?approved=1", status_code=303)


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
    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.nombre).all()
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    if current_user.get("rol") == UserRole.admin.value:
        sucursales = [s for s in sucursales if s.id == current_user.get("sucursal_id")]
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
    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.nombre).all()
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    if current_user.get("rol") == UserRole.admin.value:
        sucursal_id = str(current_user.get("sucursal_id"))
        sucursales = [s for s in sucursales if s.id == current_user.get("sucursal_id")]

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

    try:
        suc_id = int(sucursal_id)
        mat_id = int(material_id)
    except ValueError:
        return render_error("Sucursal o material inválido.")

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
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    allowed_suc_id = None
    if current_user.get("rol") == UserRole.admin.value:
        allowed_suc_id = current_user.get("sucursal_id")

    sel = request.query_params.get("sucursal_id")
    sucursal_id = None
    if sel:
        try:
            sucursal_id = int(sel)
        except ValueError:
            sucursal_id = None
    if allowed_suc_id:
        sucursal_id = allowed_suc_id

    query = db.query(Inventario)
    if sucursal_id:
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
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    params = request.query_params
    sucursal_id = None
    if params.get("sucursal_id"):
        try:
            sucursal_id = int(params.get("sucursal_id"))
        except ValueError:
            sucursal_id = None
    if current_user.get("rol") == UserRole.admin.value:
        sucursal_id = current_user.get("sucursal_id")

    date_from = params.get("from")
    date_to = params.get("to")
    export_query = request.url.query
    fmt = params.get("format") or "csv"
    query = db.query(MovimientoContable)
    if sucursal_id:
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
    params = request.query_params
    sucursal_id = None
    if params.get("sucursal_id"):
        try:
            sucursal_id = int(params.get("sucursal_id"))
        except ValueError:
            sucursal_id = None
    if current_user.get("rol") == UserRole.admin.value:
        sucursal_id = current_user.get("sucursal_id")

    date_from = params.get("from")
    date_to = params.get("to")
    fmt = params.get("format") or "csv"
    query = db.query(MovimientoContable)
    if sucursal_id:
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


@router.get("/inventario/movimientos")
async def inventario_movimientos(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    materiales = db.query(Material).order_by(Material.nombre).all()
    params = request.query_params
    sucursal_id = None
    if params.get("sucursal_id"):
        try:
            sucursal_id = int(params.get("sucursal_id"))
        except ValueError:
            sucursal_id = None
    if current_user.get("rol") == UserRole.admin.value:
        sucursal_id = current_user.get("sucursal_id")

    material_id = None
    if params.get("material_id"):
        try:
            material_id = int(params.get("material_id"))
        except ValueError:
            material_id = None
    tipo = params.get("tipo") or None

    query = db.query(InventarioMovimiento)
    if sucursal_id:
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
        delta = float(mov.cantidad_kg or 0)
        if mov.tipo == "venta":
            delta = -delta
        total_firmado += delta

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
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    materiales = db.query(Material).order_by(Material.nombre).all()
    params = request.query_params
    sucursal_id = None
    if params.get("sucursal_id"):
        try:
            sucursal_id = int(params.get("sucursal_id"))
        except ValueError:
            sucursal_id = None
    if current_user.get("rol") == UserRole.admin.value:
        sucursal_id = current_user.get("sucursal_id")

    material_id = None
    if params.get("material_id"):
        try:
            material_id = int(params.get("material_id"))
        except ValueError:
            material_id = None
    tipo = params.get("tipo") or None
    fmt = params.get("format") or "csv"

    query = db.query(InventarioMovimiento)
    if sucursal_id:
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
            writer.writerow([
                mov.inventario.sucursal.nombre if mov.inventario and mov.inventario.sucursal else mov.inventario_id,
                mov.inventario.material.nombre if mov.inventario and mov.inventario.material else "",
                mov.tipo,
                float(mov.cantidad_kg or 0),
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
        vals = [
            mov.inventario.sucursal.nombre if mov.inventario and mov.inventario.sucursal else mov.inventario_id,
            mov.inventario.material.nombre if mov.inventario and mov.inventario.material else "",
            mov.tipo,
            float(mov.cantidad_kg or 0),
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
        vals = [
            mov.inventario.sucursal.nombre if mov.inventario and mov.inventario.sucursal else str(mov.inventario_id),
            mov.inventario.material.nombre if mov.inventario and mov.inventario.material else "",
            mov.tipo,
            f"{float(mov.cantidad_kg or 0):.2f}",
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


