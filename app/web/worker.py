# app/web/worker.py
from decimal import Decimal
from typing import List
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.deps import get_db
from app.models import (
    Material,
    Proveedor,
    Cliente,
    Sucursal,
    Nota,
    NotaEstado,
    TipoOperacion,
    TablaPrecio,
    TipoCliente,
    NotaMaterial,
    Subpesaje,
)
from app.services import note_service
from app.services.evidence_service import build_evidence_groups
from app.services.firebase_storage import upload_image

templates = Jinja2Templates(directory="app/templates")
settings = get_settings()

router = APIRouter(prefix="/web/worker", tags=["web-worker"])


def require_worker(request: Request) -> dict:
    user = request.session.get("user")
    if not user or user.get("rol") != "trabajador":
        raise HTTPException(status_code=403, detail="Solo trabajadores pueden acceder a esta sección.")
    return user


def _get_price_map(db: Session) -> dict:
    """
    Retorna mapping {material_id: {tipo_operacion: {tipo_cliente: precio}}}
    solo con precios activos.
    """
    mapping: dict = {}
    precios = (
        db.query(TablaPrecio)
        .filter(TablaPrecio.activo.is_(True))
        .all()
    )
    for p in precios:
        mapping.setdefault(p.material_id, {}).setdefault(p.tipo_operacion.value, {})[p.tipo_cliente.value] = float(p.precio_por_unidad)
    return mapping


@router.get("/notes")
async def notes_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_worker),
):
    notas = (
        db.query(Nota)
        .filter(Nota.trabajador_id == current_user["id"])
        .order_by(Nota.id.desc())
        .all()
    )
    return templates.TemplateResponse(
        "worker/notes_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "notas": notas,
            "success": request.query_params.get("success"),
        },
    )


@router.get("/notes/nueva")
async def notes_new_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_worker),
):
    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.nombre).all()
    proveedores = db.query(Proveedor).filter(Proveedor.activo.is_(True)).order_by(Proveedor.nombre_completo).all()
    clientes = db.query(Cliente).filter(Cliente.activo.is_(True)).order_by(Cliente.nombre_completo).all()
    price_map = _get_price_map(db)
    return templates.TemplateResponse(
        "worker/notes_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "materiales": materiales,
            "proveedores": proveedores,
            "clientes": clientes,
            "error": None,
            "price_map": price_map,
            "max_mb": settings.FIREBASE_MAX_MB,
        },
    )


def _parse_materials_from_form(
    material_ids: List[str],
    kg_brutos: List[str],
    kg_descs: List[str],
    subpesos: List[str],
    tipos_cliente: List[str],
) -> List[dict]:
    materiales = []
    for mid, kg_b, kg_d, sub, tc in zip(material_ids, kg_brutos, kg_descs, subpesos, tipos_cliente):
        if not mid:
            continue
        kg_bruto = Decimal(kg_b or "0")
        kg_desc = Decimal(kg_d or "0")
        if kg_bruto <= 0 and not sub:
            # sin pesajes ni peso, omitir
            continue
        sub_list = []
        if sub:
            try:
                sub_json = json.loads(sub)
            except json.JSONDecodeError:
                raise ValueError("Formato de subpesajes inválido.")
            for item in sub_json:
                peso_bruto = Decimal(str(item.get("peso_kg") or item.get("peso_bruto") or 0))
                desc = Decimal(str(item.get("descuento_kg", 0)))
                if peso_bruto <= 0:
                    continue
                sub_list.append(
                    {
                        "peso_kg": peso_bruto,
                        "descuento_kg": desc,
                        "foto_url": item.get("foto_url"),
                    }
                )
            kg_bruto = sum(s["peso_kg"] for s in sub_list)
            kg_desc = sum(s["descuento_kg"] for s in sub_list)
            if kg_desc > kg_bruto:
                raise ValueError("El descuento no puede ser mayor que el peso bruto.")
        else:
            if kg_desc > kg_bruto:
                raise ValueError("El descuento no puede ser mayor que el peso bruto.")
        materiales.append(
            {
                "material_id": int(mid),
                "kg_bruto": kg_bruto,
                "kg_descuento": kg_desc,
                "subpesajes": sub_list,
                "tipo_cliente": tc or None,
            }
        )
    return materiales


@router.post("/notes/nueva")
async def notes_new_post(
    request: Request,
    tipo_operacion: str = Form(...),
    proveedor_id: str = Form(""),
    cliente_id: str = Form(""),
    material_id: List[str] = Form([]),
    kg_bruto: List[str] = Form([]),
    kg_descuento: List[str] = Form([]),
    subpesajes: List[str] = Form([]),
    tipo_cliente: List[str] = Form([]),
    comentarios_trabajador: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_worker),
):
    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.nombre).all()
    proveedores = db.query(Proveedor).filter(Proveedor.activo.is_(True)).order_by(Proveedor.nombre_completo).all()
    clientes = db.query(Cliente).filter(Cliente.activo.is_(True)).order_by(Cliente.nombre_completo).all()
    price_map = _get_price_map(db)

    try:
        tipo_op = TipoOperacion(tipo_operacion)
    except ValueError:
        return templates.TemplateResponse(
            "worker/notes_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "materiales": materiales,
                "proveedores": proveedores,
                "clientes": clientes,
                "error": "Tipo de operación inválido.",
                "price_map": price_map,
            "max_mb": settings.FIREBASE_MAX_MB,
            },
            status_code=400,
        )

    try:
        materiales_payload = _parse_materials_from_form(material_id, kg_bruto, kg_descuento, subpesajes, tipo_cliente)
    except ValueError as e:
        return templates.TemplateResponse(
            "worker/notes_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "materiales": materiales,
                "proveedores": proveedores,
                "clientes": clientes,
                "error": str(e),
                "price_map": price_map,
            "max_mb": settings.FIREBASE_MAX_MB,
            },
            status_code=400,
        )

    if not materiales_payload:
        return templates.TemplateResponse(
            "worker/notes_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "materiales": materiales,
                "proveedores": proveedores,
                "clientes": clientes,
                "error": "Debes agregar al menos un material con peso.",
                "price_map": price_map,
            "max_mb": settings.FIREBASE_MAX_MB,
            },
            status_code=400,
        )

    if tipo_op == TipoOperacion.compra and not proveedor_id:
        return templates.TemplateResponse(
            "worker/notes_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "materiales": materiales,
                "proveedores": proveedores,
                "clientes": clientes,
                "error": "Selecciona un proveedor para la compra.",
                "price_map": price_map,
            "max_mb": settings.FIREBASE_MAX_MB,
            },
            status_code=400,
        )
    if tipo_op == TipoOperacion.venta and not cliente_id:
        return templates.TemplateResponse(
            "worker/notes_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "materiales": materiales,
                "proveedores": proveedores,
                "clientes": clientes,
                "error": "Selecciona un cliente para la venta.",
                "price_map": price_map,
            "max_mb": settings.FIREBASE_MAX_MB,
            },
            status_code=400,
        )

    try:
        nota = note_service.create_draft_note(
            db,
            sucursal_id=current_user.get("sucursal_id"),
            trabajador_id=current_user.get("id"),
            tipo_operacion=tipo_op,
            materiales_payload=materiales_payload,
            comentarios_trabajador=comentarios_trabajador,
            proveedor_id=int(proveedor_id) if proveedor_id else None,
            cliente_id=int(cliente_id) if cliente_id else None,
        )
    except ValueError as e:
        return templates.TemplateResponse(
            "worker/notes_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "materiales": materiales,
                "proveedores": proveedores,
                "clientes": clientes,
                "error": str(e),
                "price_map": price_map,
            "max_mb": settings.FIREBASE_MAX_MB,
            },
            status_code=400,
        )

    return RedirectResponse(url="/web/worker/notes", status_code=303)


@router.post("/notes/{nota_id}/enviar")
async def notes_send_revision(
    nota_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_worker),
):
    nota = (
        db.query(Nota)
        .filter(Nota.id == nota_id, Nota.trabajador_id == current_user["id"])
        .first()
    )
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    try:
        note_service.send_to_revision(db, nota)
    except ValueError as e:
        return RedirectResponse(
            url="/web/worker/notes?success=0",
            status_code=303,
        )
    return RedirectResponse(
        url="/web/worker/notes?success=1",
        status_code=303,
    )


@router.get("/notes/{nota_id}/evidencias")
async def notes_evidencias(
    nota_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_worker),
):
    nota = (
        db.query(Nota)
        .filter(Nota.id == nota_id, Nota.trabajador_id == current_user["id"])
        .first()
    )
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")

    sucursal = db.get(Sucursal, nota.sucursal_id) if nota.sucursal_id else None
    partner_name = "-"
    partner_label = "Partner"
    if nota.tipo_operacion.value == "compra":
        partner_label = "Proveedor"
        proveedor = db.get(Proveedor, nota.proveedor_id) if nota.proveedor_id else None
        partner_name = proveedor.nombre_completo if proveedor else "-"
    else:
        partner_label = "Cliente"
        cliente = db.get(Cliente, nota.cliente_id) if nota.cliente_id else None
        partner_name = cliente.nombre_completo if cliente else "-"

    evidence_groups = build_evidence_groups(nota)
    total_sub = sum(len(g["subpesajes"]) for g in evidence_groups)
    missing = sum(
        1
        for g in evidence_groups
        for sp in g["subpesajes"]
        if not sp.get("foto_url")
    )
    can_upload = nota.estado in (NotaEstado.borrador, NotaEstado.en_revision)

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
            "trabajador_name": current_user.get("username"),
            "evidence_groups": evidence_groups,
            "total_subpesajes": total_sub,
            "missing_subpesajes": missing,
            "can_upload": can_upload,
            "upload_action_base": f"/web/worker/notes/{nota.id}/subpesajes",
            "back_url": "/web/worker/notes",
            "max_mb": settings.FIREBASE_MAX_MB,
            "capture_mode": "environment",
            "updated": request.query_params.get("updated"),
            "error": request.query_params.get("error"),
        },
    )


@router.post("/notes/{nota_id}/subpesajes/{subpesaje_id}/evidencia")
async def notes_subpesaje_upload(
    nota_id: int,
    subpesaje_id: int,
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_worker),
):
    nota = (
        db.query(Nota)
        .filter(Nota.id == nota_id, Nota.trabajador_id == current_user["id"])
        .first()
    )
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    if nota.estado in (NotaEstado.aprobada, NotaEstado.cancelada):
        return RedirectResponse(
            url=f"/web/worker/notes/{nota_id}/evidencias?error=estado",
            status_code=303,
        )

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
            url=f"/web/worker/notes/{nota_id}/evidencias?error=tipo",
            status_code=303,
        )

    content = await file.read()
    max_bytes = settings.FIREBASE_MAX_MB * 1024 * 1024
    if len(content) > max_bytes:
        return RedirectResponse(
            url=f"/web/worker/notes/{nota_id}/evidencias?error=peso",
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
            url=f"/web/worker/notes/{nota_id}/evidencias?error=upload",
            status_code=303,
        )

    subpesaje.foto_url = url
    db.add(subpesaje)
    db.commit()

    return RedirectResponse(
        url=f"/web/worker/notes/{nota_id}/evidencias?updated=1",
        status_code=303,
    )
