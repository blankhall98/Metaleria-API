# app/web/admin.py
from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import or_
from decimal import Decimal, InvalidOperation
from datetime import datetime

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
    Cliente,
    Nota,
    NotaEstado,
)

from app.services.pricing_service import create_price_version
from app.services import note_service

templates = Jinja2Templates(directory="app/templates")
settings = get_settings()

router = APIRouter(prefix="/web/admin", tags=["web-admin"])


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
    current_user: dict = Depends(require_superadmin),
):
    return templates.TemplateResponse(
        "admin/sucursal_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "error": None,
        },
    )


@router.post("/sucursales/nueva")
async def sucursal_new_post(
    request: Request,
    nombre: str = Form(...),
    direccion: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    nombre = nombre.strip()
    direccion = direccion.strip()

    if not nombre:
        return templates.TemplateResponse(
            "admin/sucursal_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "error": "El nombre de la sucursal es obligatorio.",
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
            },
            status_code=400,
        )

    sucursal = Sucursal(
        nombre=nombre,
        direccion=direccion or None,
        estado=SucursalStatus.activa,
    )
    db.add(sucursal)
    db.commit()

    return RedirectResponse(url="/web/admin/sucursales", status_code=303)


# ---------- USUARIOS ----------


@router.get("/users")
async def users_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    usuarios = (
        db.query(User)
        .order_by(User.id.desc())
        .all()
    )
    sucursales = {s.id: s for s in db.query(Sucursal).all()}

    return templates.TemplateResponse(
        "admin/users_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "usuarios": usuarios,
            "sucursales_map": sucursales,
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
    placas = placas.strip()

    if not nombre_completo:
        return templates.TemplateResponse(
            "admin/proveedor_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "proveedor": None,
                "error": "El nombre del proveedor es obligatorio.",
            },
            status_code=400,
        )

    if placas:
        existing = db.query(Proveedor).filter(Proveedor.placas == placas).first()
        if existing:
            return templates.TemplateResponse(
                "admin/proveedor_form.html",
                {
                    "request": request,
                    "env": settings.ENV,
                    "user": current_user,
                    "proveedor": None,
                    "error": "Ya existe un proveedor con esas placas.",
                },
                status_code=400,
            )

    proveedor = Proveedor(
        nombre_completo=nombre_completo,
        telefono=telefono or None,
        correo_electronico=correo_electronico or None,
        placas=placas or None,
        activo=True,
    )
    db.add(proveedor)
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
    placas = placas.strip()

    if not nombre_completo:
        return templates.TemplateResponse(
            "admin/proveedor_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "proveedor": proveedor,
                "error": "El nombre del proveedor es obligatorio.",
            },
            status_code=400,
        )

    if placas:
        existing = (
            db.query(Proveedor)
            .filter(Proveedor.placas == placas, Proveedor.id != proveedor.id)
            .first()
        )
        if existing:
            return templates.TemplateResponse(
                "admin/proveedor_form.html",
                {
                    "request": request,
                    "env": settings.ENV,
                    "user": current_user,
                    "proveedor": proveedor,
                    "error": "Ya existe otro proveedor con esas placas.",
                },
                status_code=400,
            )

    proveedor.nombre_completo = nombre_completo
    proveedor.telefono = telefono or None
    proveedor.correo_electronico = correo_electronico or None
    proveedor.placas = placas or None
    proveedor.activo = bool(activo)

    db.add(proveedor)
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
    placas = placas.strip()

    if not nombre_completo:
        return templates.TemplateResponse(
            "admin/cliente_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "cliente": None,
                "error": "El nombre del cliente es obligatorio.",
            },
            status_code=400,
        )

    if placas:
        existing = db.query(Cliente).filter(Cliente.placas == placas).first()
        if existing:
            return templates.TemplateResponse(
                "admin/cliente_form.html",
                {
                    "request": request,
                    "env": settings.ENV,
                    "user": current_user,
                    "cliente": None,
                    "error": "Ya existe un cliente con esas placas.",
                },
                status_code=400,
            )

    cliente = Cliente(
        nombre_completo=nombre_completo,
        telefono=telefono or None,
        correo_electronico=correo_electronico or None,
        placas=placas or None,
        activo=True,
    )
    db.add(cliente)
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
    placas = placas.strip()

    if not nombre_completo:
        return templates.TemplateResponse(
            "admin/cliente_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "cliente": cliente,
                "error": "El nombre del cliente es obligatorio.",
            },
            status_code=400,
        )

    if placas:
        existing = (
            db.query(Cliente)
            .filter(Cliente.placas == placas, Cliente.id != cliente.id)
            .first()
        )
        if existing:
            return templates.TemplateResponse(
                "admin/cliente_form.html",
                {
                    "request": request,
                    "env": settings.ENV,
                    "user": current_user,
                    "cliente": cliente,
                    "error": "Ya existe otro cliente con esas placas.",
                },
                status_code=400,
            )

    cliente.nombre_completo = nombre_completo
    cliente.telefono = telefono or None
    cliente.correo_electronico = correo_electronico or None
    cliente.placas = placas or None
    cliente.activo = bool(activo)

    db.add(cliente)
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

    sucursal = db.get(Sucursal, nota.sucursal_id) if nota.sucursal_id else None
    proveedor = db.get(Proveedor, nota.proveedor_id) if nota.proveedor_id else None
    cliente = db.get(Cliente, nota.cliente_id) if nota.cliente_id else None

    return templates.TemplateResponse(
        "admin/note_detail.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "nota": nota,
            "sucursal": sucursal,
            "proveedor": proveedor,
            "cliente": cliente,
            "tipos_cliente": list(TipoCliente),
        },
    )


def _parse_tipo_cliente_map(form_data) -> dict[int, TipoCliente]:
    mapping: dict[int, TipoCliente] = {}
    for key, value in form_data.items():
        if not key.startswith("tipo_cliente_"):
            continue
        try:
            nm_id = int(key.replace("tipo_cliente_", ""))
        except ValueError:
            continue
        if not value:
            continue
        try:
            mapping[nm_id] = TipoCliente(value)
        except ValueError:
            continue
    return mapping


@router.post("/notas/{nota_id}/aprobar")
async def notas_aprobar(
    nota_id: int,
    request: Request,
    fecha_caducidad_pago: str = Form(""),
    comentarios_admin: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    if nota.estado not in (NotaEstado.en_revision, NotaEstado.borrador):
        return RedirectResponse(url=f"/web/admin/notas/{nota.id}", status_code=303)

    form_data = await request.form()
    tipo_cli_map = _parse_tipo_cliente_map(form_data)
    note_service.set_tipo_cliente_and_prices(db, nota, tipo_cli_map)

    fecha_cad = None
    if fecha_caducidad_pago:
        try:
            fecha_cad = datetime.strptime(fecha_caducidad_pago, "%Y-%m-%d").date()
        except ValueError:
            fecha_cad = None

    note_service.update_state(
        db,
        nota,
        new_state=NotaEstado.aprobada,
        admin_id=current_user.get("id"),
        comentarios_admin=comentarios_admin,
        fecha_caducidad_pago=fecha_cad,
    )
    return RedirectResponse(url="/web/admin/notas", status_code=303)


@router.post("/notas/{nota_id}/cancelar")
async def notas_cancelar(
    nota_id: int,
    comentarios_admin: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    if nota.estado == NotaEstado.cancelada:
        return RedirectResponse(url=f"/web/admin/notas/{nota.id}", status_code=303)

    note_service.update_state(
        db,
        nota,
        new_state=NotaEstado.cancelada,
        admin_id=current_user.get("id"),
        comentarios_admin=comentarios_admin,
    )
    return RedirectResponse(url="/web/admin/notas", status_code=303)


@router.post("/notas/{nota_id}/eliminar")
async def notas_eliminar(
    nota_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    db.delete(nota)
    db.commit()
    return RedirectResponse(url="/web/admin/notas", status_code=303)


