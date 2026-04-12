import json
import logging
# app/web/worker.py
from datetime import datetime
from decimal import Decimal
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse
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
from app.services.firebase_storage import resolve_image_content_type, upload_image
from app.web.template_utils import create_templates

templates = create_templates()
settings = get_settings()
logger = logging.getLogger(__name__)

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


def _get_worker_partners(db: Session, current_user: dict) -> tuple[list[Proveedor], list[Proveedor], list[Cliente]]:
    sucursal_id = current_user.get("sucursal_id")
    proveedores_q = db.query(Proveedor).filter(Proveedor.activo.is_(True))
    clientes_q = db.query(Cliente).filter(Cliente.activo.is_(True))
    if sucursal_id:
        proveedores_q = proveedores_q.filter(Proveedor.sucursal_id == sucursal_id)
        clientes_q = clientes_q.filter(Cliente.sucursal_id == sucursal_id)
    proveedores = proveedores_q.order_by(Proveedor.nombre_completo).all()
    proveedores_venta = [p for p in proveedores if bool(getattr(p, "permite_ventas", False))]
    clientes = clientes_q.order_by(Cliente.nombre_completo).all()
    return proveedores, proveedores_venta, clientes


def _validate_worker_partner_selection(
    db: Session,
    *,
    tipo_operacion: TipoOperacion,
    proveedor_id: str | None,
    cliente_id: str | None,
    sucursal_id: int | None,
) -> None:
    if tipo_operacion == TipoOperacion.compra:
        if not proveedor_id:
            return
        try:
            pid = int(proveedor_id)
        except (TypeError, ValueError):
            raise ValueError("Proveedor invalido.")
        proveedor = db.get(Proveedor, pid)
        if not proveedor or not proveedor.activo:
            raise ValueError("Proveedor invalido.")
        if sucursal_id and proveedor.sucursal_id != sucursal_id:
            raise ValueError("El proveedor no pertenece a tu sucursal.")
        return
    if tipo_operacion == TipoOperacion.venta:
        if proveedor_id and cliente_id:
            raise ValueError("Selecciona solo una contraparte para la venta.")
        if proveedor_id:
            try:
                pid = int(proveedor_id)
            except (TypeError, ValueError):
                raise ValueError("Proveedor invalido.")
            proveedor = db.get(Proveedor, pid)
            if not proveedor or not proveedor.activo:
                raise ValueError("Proveedor invalido.")
            if sucursal_id and proveedor.sucursal_id != sucursal_id:
                raise ValueError("El proveedor no pertenece a tu sucursal.")
            if not bool(getattr(proveedor, "permite_ventas", False)):
                raise ValueError("Este proveedor no esta habilitado para compras directas.")
            return
        if cliente_id:
            try:
                cid = int(cliente_id)
            except (TypeError, ValueError):
                raise ValueError("Cliente invalido.")
            cliente = db.get(Cliente, cid)
            if not cliente or not cliente.activo:
                raise ValueError("Cliente invalido.")
            if sucursal_id and cliente.sucursal_id != sucursal_id:
                raise ValueError("El cliente no pertenece a tu sucursal.")


def _normalize_worker_partner_selection(
    *,
    tipo_operacion: TipoOperacion,
    venta_partner_kind: str | None,
    proveedor_compra_id: str | None,
    proveedor_venta_id: str | None,
    cliente_id: str | None,
) -> tuple[str | None, str | None, str]:
    partner_kind = "proveedor" if (venta_partner_kind or "").strip().lower() == "proveedor" else "cliente"
    proveedor_raw = (proveedor_compra_id or "").strip()
    cliente_raw = (cliente_id or "").strip()
    proveedor_venta_raw = (proveedor_venta_id or "").strip()
    if tipo_operacion == TipoOperacion.compra:
        return (proveedor_raw or None), None, "proveedor"
    if partner_kind == "proveedor":
        return (proveedor_venta_raw or None), None, "proveedor"
    return None, (cliente_raw or None), "cliente"


def _build_worker_note_partner_map(db: Session, notas: list[Nota]) -> dict[int, dict[str, str]]:
    proveedor_ids = sorted({nota.proveedor_id for nota in notas if nota.proveedor_id})
    cliente_ids = sorted({nota.cliente_id for nota in notas if nota.cliente_id})
    proveedores_map = {
        proveedor.id: proveedor
        for proveedor in db.query(Proveedor).filter(Proveedor.id.in_(proveedor_ids)).all()
    } if proveedor_ids else {}
    clientes_map = {
        cliente.id: cliente
        for cliente in db.query(Cliente).filter(Cliente.id.in_(cliente_ids)).all()
    } if cliente_ids else {}

    partner_map: dict[int, dict[str, str]] = {}
    for nota in notas:
        partner_name = "-"
        partner_kind = ""
        if nota.proveedor_id:
            proveedor = proveedores_map.get(nota.proveedor_id)
            if proveedor:
                partner_name = proveedor.nombre_completo
                partner_kind = "Proveedor"
        elif nota.cliente_id:
            cliente = clientes_map.get(nota.cliente_id)
            if cliente:
                partner_name = cliente.nombre_completo
                partner_kind = "Cliente"
        partner_map[nota.id] = {
            "name": partner_name,
            "kind": partner_kind,
        }
    return partner_map


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
    folio_map = {}
    for nota in notas:
        folio = note_service.format_folio(
            sucursal_id=nota.sucursal_id,
            tipo_operacion=nota.tipo_operacion,
            folio_seq=nota.folio_seq,
        )
        if folio:
            folio_map[nota.id] = folio
        elif nota.estado.value in ("BORRADOR", "EN_REVISION"):
            folio_map[nota.id] = "Pendiente"
        else:
            folio_map[nota.id] = "-"
    partner_map = _build_worker_note_partner_map(db, notas)
    return templates.TemplateResponse(
        "worker/notes_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "notas": notas,
            "folio_map": folio_map,
            "partner_map": partner_map,
            "success": request.query_params.get("success"),
        },
    )


def _render_worker_note_form(
    request: Request,
    current_user: dict,
    *,
    materiales: list[Material],
    proveedores: list[Proveedor],
    proveedores_venta: list[Proveedor],
    clientes: list[Cliente],
    price_map: dict,
    error: str | None = None,
    status_code: int = 200,
    form_title: str | None = None,
    action_url: str | None = None,
    submit_label: str | None = None,
    initial_state: dict | None = None,
    review_comment: str | None = None,
):
    return templates.TemplateResponse(
        "worker/notes_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "materiales": materiales,
            "proveedores": proveedores,
            "proveedores_venta": proveedores_venta,
            "clientes": clientes,
            "error": error,
            "price_map": price_map,
            "max_mb": settings.FIREBASE_MAX_MB,
            "form_title": form_title,
            "action_url": action_url,
            "submit_label": submit_label,
            "initial_note_json": json.dumps(initial_state or {}, ensure_ascii=True),
            "review_comment": review_comment or "",
        },
        status_code=status_code,
    )


@router.get("/notes/nueva")
async def notes_new_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_worker),
):
    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.orden_display, Material.nombre).all()
    proveedores, proveedores_venta, clientes = _get_worker_partners(db, current_user)
    price_map = _get_price_map(db)
    return _render_worker_note_form(
        request,
        current_user,
        materiales=materiales,
        proveedores=proveedores,
        proveedores_venta=proveedores_venta,
        clientes=clientes,
        price_map=price_map,
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


def _build_worker_note_initial_state(nota: Nota) -> dict:
    materiales: list[dict] = []
    for nm in sorted(list(nota.materiales or []), key=lambda m: (m.orden or 0, m.id or 0)):
        subpesajes = []
        if nm.subpesajes:
            for sp in sorted(list(nm.subpesajes), key=lambda s: s.id or 0):
                subpesajes.append(
                    {
                        "peso_kg": float(Decimal(str(sp.peso_kg or 0))),
                        "descuento_kg": float(Decimal(str(getattr(sp, "descuento_kg", 0) or 0))),
                        "foto_url": sp.foto_url or None,
                    }
                )
        else:
            subpesajes.append(
                {
                    "peso_kg": float(Decimal(str(nm.kg_bruto or 0))),
                    "descuento_kg": float(Decimal(str(nm.kg_descuento or 0))),
                    "foto_url": nm.evidencia_url or None,
                }
            )
        materiales.append(
            {
                "material_id": nm.material_id,
                "tipo_cliente": nm.tipo_cliente.value if nm.tipo_cliente else "",
                "subpesajes": subpesajes,
            }
        )

    extra_evidencias = [ev.url for ev in sorted(list(nota.evidencias_extra or []), key=lambda e: e.id or 0) if ev.url]
    venta_partner_kind = "proveedor" if nota.tipo_operacion == TipoOperacion.venta and nota.proveedor_id else "cliente"
    return {
        "tipo_operacion": nota.tipo_operacion.value if nota.tipo_operacion else "compra",
        "venta_partner_kind": venta_partner_kind,
        "proveedor_compra_id": nota.proveedor_id if nota.tipo_operacion == TipoOperacion.compra and nota.proveedor_id else "",
        "proveedor_venta_id": nota.proveedor_id if nota.tipo_operacion == TipoOperacion.venta and nota.proveedor_id else "",
        "cliente_id": nota.cliente_id or "",
        "comentarios_trabajador": nota.comentarios_trabajador or "",
        "materiales": materiales,
        "extra_evidencias": extra_evidencias,
    }


@router.post("/notes/nueva")
async def notes_new_post(
    request: Request,
    tipo_operacion: str = Form(...),
    venta_partner_kind: str = Form("cliente"),
    proveedor_compra_id: str = Form(""),
    proveedor_venta_id: str = Form(""),
    cliente_id: str = Form(""),
    material_id: List[str] = Form([]),
    kg_bruto: List[str] = Form([]),
    kg_descuento: List[str] = Form([]),
    subpesajes: List[str] = Form([]),
    tipo_cliente: List[str] = Form([]),
    comentarios_trabajador: str = Form(""),
    extra_evidencias: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_worker),
):
    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.orden_display, Material.nombre).all()
    proveedores, proveedores_venta, clientes = _get_worker_partners(db, current_user)
    price_map = _get_price_map(db)
    initial_state = {
        "tipo_operacion": tipo_operacion or "compra",
        "venta_partner_kind": venta_partner_kind or "cliente",
        "proveedor_compra_id": proveedor_compra_id or "",
        "proveedor_venta_id": proveedor_venta_id or "",
        "cliente_id": cliente_id or "",
        "comentarios_trabajador": comentarios_trabajador or "",
        "materiales": [],
        "extra_evidencias": [],
    }

    def render_error(message: str):
        return _render_worker_note_form(
            request,
            current_user,
            materiales=materiales,
            proveedores=proveedores,
            proveedores_venta=proveedores_venta,
            clientes=clientes,
            price_map=price_map,
            error=message,
            status_code=400,
            initial_state=initial_state,
        )

    try:
        tipo_op = TipoOperacion(tipo_operacion)
    except ValueError:
        return render_error("Tipo de operacion invalido.")
    if tipo_op not in (TipoOperacion.compra, TipoOperacion.venta):
        return render_error("Tipo de operacion no permitido.")

    try:
        materiales_payload = _parse_materials_from_form(material_id, kg_bruto, kg_descuento, subpesajes, tipo_cliente)
        initial_state["materiales"] = materiales_payload
    except ValueError as exc:
        return render_error(str(exc))
    if not materiales_payload:
        return render_error("Debes agregar al menos un material con peso.")

    extra_evidencias_payload: list[str] = []
    if extra_evidencias:
        try:
            loaded = json.loads(extra_evidencias)
        except json.JSONDecodeError:
            return render_error("Formato de evidencia extra invalido.")
        if not isinstance(loaded, list):
            return render_error("Formato de evidencia extra invalido.")
        extra_evidencias_payload = [str(url) for url in loaded if url]
        initial_state["extra_evidencias"] = extra_evidencias_payload

    proveedor_id, cliente_id_normalized, venta_partner_kind = _normalize_worker_partner_selection(
        tipo_operacion=tipo_op,
        venta_partner_kind=venta_partner_kind,
        proveedor_compra_id=proveedor_compra_id,
        proveedor_venta_id=proveedor_venta_id,
        cliente_id=cliente_id,
    )
    initial_state["venta_partner_kind"] = venta_partner_kind
    initial_state["proveedor_compra_id"] = proveedor_id if tipo_op == TipoOperacion.compra else ""
    initial_state["proveedor_venta_id"] = proveedor_id if tipo_op == TipoOperacion.venta and venta_partner_kind == "proveedor" else ""
    initial_state["cliente_id"] = cliente_id_normalized or ""

    if tipo_op == TipoOperacion.compra and not proveedor_id:
        return render_error("Selecciona un proveedor para la compra.")
    if tipo_op == TipoOperacion.venta and venta_partner_kind == "cliente" and not cliente_id_normalized:
        return render_error("Selecciona un cliente para la venta.")
    if tipo_op == TipoOperacion.venta and venta_partner_kind == "proveedor" and not proveedor_id:
        return render_error("Selecciona un proveedor para la venta.")

    try:
        _validate_worker_partner_selection(
            db,
            tipo_operacion=tipo_op,
            proveedor_id=proveedor_id,
            cliente_id=cliente_id_normalized,
            sucursal_id=current_user.get("sucursal_id"),
        )
        note_service.create_draft_note(
            db,
            sucursal_id=current_user.get("sucursal_id"),
            trabajador_id=current_user.get("id"),
            tipo_operacion=tipo_op,
            materiales_payload=materiales_payload,
            comentarios_trabajador=comentarios_trabajador,
            proveedor_id=int(proveedor_id) if proveedor_id else None,
            cliente_id=int(cliente_id_normalized) if cliente_id_normalized else None,
            extra_evidencias_payload=extra_evidencias_payload,
        )
    except ValueError as exc:
        return render_error(str(exc))

    return RedirectResponse(url="/web/worker/notes", status_code=303)


@router.get("/notes/{nota_id}/editar")
async def notes_edit_get(
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
    if nota.estado != NotaEstado.borrador:
        return RedirectResponse(url="/web/worker/notes?success=2", status_code=303)

    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.orden_display, Material.nombre).all()
    proveedores, proveedores_venta, clientes = _get_worker_partners(db, current_user)
    price_map = _get_price_map(db)
    initial_state = _build_worker_note_initial_state(nota)

    return _render_worker_note_form(
        request,
        current_user,
        materiales=materiales,
        proveedores=proveedores,
        proveedores_venta=proveedores_venta,
        clientes=clientes,
        price_map=price_map,
        form_title="Editar nota",
        action_url=f"/web/worker/notes/{nota.id}/editar",
        submit_label="Guardar cambios",
        initial_state=initial_state,
        review_comment=nota.comentarios_admin or "",
    )


@router.post("/notes/{nota_id}/editar")
async def notes_edit_post(
    nota_id: int,
    request: Request,
    tipo_operacion: str = Form(...),
    venta_partner_kind: str = Form("cliente"),
    proveedor_compra_id: str = Form(""),
    proveedor_venta_id: str = Form(""),
    cliente_id: str = Form(""),
    material_id: List[str] = Form([]),
    kg_bruto: List[str] = Form([]),
    kg_descuento: List[str] = Form([]),
    subpesajes: List[str] = Form([]),
    tipo_cliente: List[str] = Form([]),
    comentarios_trabajador: str = Form(""),
    extra_evidencias: str = Form(""),
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
    if nota.estado != NotaEstado.borrador:
        return RedirectResponse(url="/web/worker/notes?success=2", status_code=303)

    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.orden_display, Material.nombre).all()
    proveedores, proveedores_venta, clientes = _get_worker_partners(db, current_user)
    price_map = _get_price_map(db)
    initial_state = _build_worker_note_initial_state(nota)

    def render_error(message: str):
        return _render_worker_note_form(
            request,
            current_user,
            materiales=materiales,
            proveedores=proveedores,
            proveedores_venta=proveedores_venta,
            clientes=clientes,
            price_map=price_map,
            error=message,
            status_code=400,
            form_title="Editar nota",
            action_url=f"/web/worker/notes/{nota.id}/editar",
            submit_label="Guardar cambios",
            initial_state=initial_state,
            review_comment=nota.comentarios_admin or "",
        )

    try:
        tipo_op = TipoOperacion(tipo_operacion)
    except ValueError:
        return render_error("Tipo de operacion invalido.")
    if tipo_op not in (TipoOperacion.compra, TipoOperacion.venta):
        return render_error("Tipo de operacion no permitido.")

    try:
        materiales_payload = _parse_materials_from_form(material_id, kg_bruto, kg_descuento, subpesajes, tipo_cliente)
        initial_state["materiales"] = materiales_payload
    except ValueError as exc:
        return render_error(str(exc))
    if not materiales_payload:
        return render_error("Debes agregar al menos un material con peso.")

    extra_evidencias_payload: list[str] = []
    if extra_evidencias:
        try:
            loaded = json.loads(extra_evidencias)
        except json.JSONDecodeError:
            return render_error("Formato de evidencia extra invalido.")
        if not isinstance(loaded, list):
            return render_error("Formato de evidencia extra invalido.")
        extra_evidencias_payload = [str(url) for url in loaded if url]
        initial_state["extra_evidencias"] = extra_evidencias_payload

    proveedor_id, cliente_id_normalized, venta_partner_kind = _normalize_worker_partner_selection(
        tipo_operacion=tipo_op,
        venta_partner_kind=venta_partner_kind,
        proveedor_compra_id=proveedor_compra_id,
        proveedor_venta_id=proveedor_venta_id,
        cliente_id=cliente_id,
    )
    initial_state["tipo_operacion"] = tipo_op.value
    initial_state["venta_partner_kind"] = venta_partner_kind
    initial_state["proveedor_compra_id"] = proveedor_id if tipo_op == TipoOperacion.compra else ""
    initial_state["proveedor_venta_id"] = proveedor_id if tipo_op == TipoOperacion.venta and venta_partner_kind == "proveedor" else ""
    initial_state["cliente_id"] = cliente_id_normalized or ""
    initial_state["comentarios_trabajador"] = comentarios_trabajador or ""

    if tipo_op == TipoOperacion.compra and not proveedor_id:
        return render_error("Selecciona un proveedor para la compra.")
    if tipo_op == TipoOperacion.venta and venta_partner_kind == "cliente" and not cliente_id_normalized:
        return render_error("Selecciona un cliente para la venta.")
    if tipo_op == TipoOperacion.venta and venta_partner_kind == "proveedor" and not proveedor_id:
        return render_error("Selecciona un proveedor para la venta.")

    try:
        _validate_worker_partner_selection(
            db,
            tipo_operacion=tipo_op,
            proveedor_id=proveedor_id,
            cliente_id=cliente_id_normalized,
            sucursal_id=current_user.get("sucursal_id"),
        )
        note_service.update_worker_note(
            db,
            nota,
            tipo_operacion=tipo_op,
            materiales_payload=materiales_payload,
            comentarios_trabajador=comentarios_trabajador,
            proveedor_id=int(proveedor_id) if proveedor_id else None,
            cliente_id=int(cliente_id_normalized) if cliente_id_normalized else None,
            extra_evidencias_payload=extra_evidencias_payload,
        )
    except ValueError as exc:
        return render_error(str(exc))

    return RedirectResponse(url="/web/worker/notes?success=3", status_code=303)


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
    proveedor = db.get(Proveedor, nota.proveedor_id) if nota.proveedor_id else None
    cliente = db.get(Cliente, nota.cliente_id) if nota.cliente_id else None
    if proveedor:
        partner_label = "Proveedor"
        partner_name = proveedor.nombre_completo
    elif cliente:
        partner_label = "Cliente"
        partner_name = cliente.nombre_completo

    evidence_groups = build_evidence_groups(nota)
    total_sub = sum(len(g["subpesajes"]) for g in evidence_groups)
    missing = sum(
        1
        for g in evidence_groups
        for sp in g["subpesajes"]
        if not sp.get("foto_url")
    )
    can_upload = nota.estado in (NotaEstado.borrador, NotaEstado.en_revision)
    extra_evidencias = sorted(
        list(nota.evidencias_extra or []),
        key=lambda e: e.created_at or datetime.min,
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
            "trabajador_name": current_user.get("username"),
            "evidence_groups": evidence_groups,
            "total_subpesajes": total_sub,
            "missing_subpesajes": missing,
            "extra_evidencias": extra_evidencias,
            "extra_evidencias_total": len(extra_evidencias),
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

    content = await file.read()
    resolved_content_type = resolve_image_content_type(file.filename, file.content_type)
    if not resolved_content_type:
        logger.warning(
            "Worker evidence upload rejected: invalid content type",
            extra={
                "user_id": current_user["id"],
                "nota_id": nota_id,
                "subpesaje_id": subpesaje_id,
                "upload_name": file.filename,
                "content_type": file.content_type,
            },
        )
        return RedirectResponse(
            url=f"/web/worker/notes/{nota_id}/evidencias?error=tipo",
            status_code=303,
        )
    if not content:
        logger.warning(
            "Worker evidence upload rejected: empty file",
            extra={
                "user_id": current_user["id"],
                "nota_id": nota_id,
                "subpesaje_id": subpesaje_id,
                "upload_name": file.filename,
            },
        )
        return RedirectResponse(
            url=f"/web/worker/notes/{nota_id}/evidencias?error=vacio",
            status_code=303,
        )
    max_bytes = settings.FIREBASE_MAX_MB * 1024 * 1024
    if len(content) > max_bytes:
        logger.warning(
            "Worker evidence upload rejected: file too large",
            extra={
                "user_id": current_user["id"],
                "nota_id": nota_id,
                "subpesaje_id": subpesaje_id,
                "upload_name": file.filename,
                "content_type": resolved_content_type,
                "size_bytes": len(content),
                "max_bytes": max_bytes,
            },
        )
        return RedirectResponse(
            url=f"/web/worker/notes/{nota_id}/evidencias?error=peso",
            status_code=303,
        )

    try:
        url = upload_image(
            content=content,
            filename=file.filename or "evidencia",
            content_type=resolved_content_type,
            folder=f"evidencias/nota_{nota_id}/sub_{subpesaje_id}",
        )
    except Exception:
        logger.exception(
            "Worker evidence upload failed",
            extra={
                "user_id": current_user["id"],
                "nota_id": nota_id,
                "subpesaje_id": subpesaje_id,
                "upload_name": file.filename,
                "content_type": resolved_content_type,
                "size_bytes": len(content),
            },
        )
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

