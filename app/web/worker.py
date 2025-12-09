# app/web/worker.py
from decimal import Decimal
from typing import List
import json

from fastapi import APIRouter, Depends, HTTPException, Request, Form
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.deps import get_db
from app.models import (
    Material,
    Proveedor,
    Cliente,
    Nota,
    NotaEstado,
    TipoOperacion,
)
from app.services import note_service

templates = Jinja2Templates(directory="app/templates")
settings = get_settings()

router = APIRouter(prefix="/web/worker", tags=["web-worker"])


def require_worker(request: Request) -> dict:
    user = request.session.get("user")
    if not user or user.get("rol") != "trabajador":
        raise HTTPException(status_code=403, detail="Solo trabajadores pueden acceder a esta sección.")
    return user


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
                peso = Decimal(str(item.get("peso_kg") or item.get("peso_neto") or 0))
                desc = Decimal(str(item.get("descuento_kg", 0)))
                if peso <= 0:
                    continue
                sub_list.append(
                    {
                        "peso_kg": peso,
                        "descuento_kg": desc,
                        "foto_url": item.get("foto_url"),
                    }
                )
            kg_bruto = sum(s["peso_kg"] + s["descuento_kg"] for s in sub_list)
            kg_desc = sum(s["descuento_kg"] for s in sub_list)
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
