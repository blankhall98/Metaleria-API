# app/web/admin.py
import io
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, StreamingResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import or_, func
from urllib.parse import urlencode
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
    Cuenta,
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
_CUENTA_TIPOS = ("cuenta bancaria", "cuenta cheques")


def _movimiento_tipo_operacion(mov: MovimientoContable) -> str | None:
    tipo_raw = (mov.tipo or "").lower()
    if tipo_raw in ("compra", "venta"):
        return tipo_raw
    if mov.nota and mov.nota.tipo_operacion:
        return mov.nota.tipo_operacion.value
    return None


def _movimiento_label(tipo_raw: str, tipo_op: str | None) -> str:
    if tipo_raw == "pago":
        return f"PAGO {tipo_op.upper()}" if tipo_op else "PAGO"
    if tipo_raw == "reverso_pago":
        return f"REVERSO PAGO {tipo_op.upper()}" if tipo_op else "REVERSO PAGO"
    if tipo_raw == "reverso":
        return f"REVERSO {tipo_op.upper()}" if tipo_op else "REVERSO"
    if tipo_raw in ("compra", "venta"):
        return tipo_raw.upper()
    if tipo_raw == "ajuste":
        return "AJUSTE"
    return tipo_raw.upper() if tipo_raw else "-"


def _movimiento_naturaleza(tipo_raw: str, tipo_op: str | None) -> str:
    if tipo_raw == "compra":
        return "EGRESO"
    if tipo_raw == "venta":
        return "INGRESO"
    if tipo_raw == "pago":
        if tipo_op == "compra":
            return "EGRESO"
        if tipo_op == "venta":
            return "INGRESO"
    if tipo_raw == "reverso":
        if tipo_op == "compra":
            return "INGRESO"
        if tipo_op == "venta":
            return "EGRESO"
    if tipo_raw == "reverso_pago":
        if tipo_op == "compra":
            return "INGRESO"
        if tipo_op == "venta":
            return "EGRESO"
    if tipo_raw == "ajuste":
        return "AJUSTE"
    return "-"


def _movimiento_monto_firmado(mov: MovimientoContable, tipo_raw: str, tipo_op: str | None) -> Decimal:
    base = Decimal(str(mov.monto or 0))
    abs_val = abs(base)
    if tipo_raw == "compra":
        return -abs_val
    if tipo_raw == "venta":
        return abs_val
    if tipo_raw == "pago":
        if tipo_op == "compra":
            return -abs_val
        if tipo_op == "venta":
            return abs_val
        return base
    if tipo_raw == "reverso":
        if tipo_op == "compra":
            return abs_val
        if tipo_op == "venta":
            return -abs_val
        return base
    if tipo_raw == "reverso_pago":
        if tipo_op == "compra":
            return abs_val
        if tipo_op == "venta":
            return -abs_val
        return base
    return base


def _movimiento_display(mov: MovimientoContable) -> dict:
    tipo_raw = (mov.tipo or "").lower()
    tipo_op = _movimiento_tipo_operacion(mov)
    label = _movimiento_label(tipo_raw, tipo_op)
    naturaleza = _movimiento_naturaleza(tipo_raw, tipo_op)
    monto_firmado = _movimiento_monto_firmado(mov, tipo_raw, tipo_op)
    cuenta_label = ""
    if getattr(mov, "cuenta", None):
        cuenta_label = mov.cuenta.display_label
    else:
        cuenta_label = mov.cuenta_financiera or ""
    return {
        "id": mov.id,
        "tipo": label,
        "naturaleza": naturaleza,
        "monto_firmado": monto_firmado,
        "nota_id": mov.nota_id,
        "sucursal": mov.sucursal.nombre if mov.sucursal else mov.sucursal_id or "-",
        "usuario_id": mov.usuario_id or "-",
        "metodo_pago": mov.metodo_pago or "",
        "cuenta_financiera": cuenta_label,
        "comentario": (mov.comentario or "").replace("\n", " "),
        "created_at": mov.created_at,
    }


def _partner_payment_signed(mov: MovimientoContable) -> Decimal:
    base = Decimal(str(mov.monto or 0))
    tipo_raw = (mov.tipo or "").lower()
    if tipo_raw == "reverso_pago":
        return -abs(base)
    return abs(base)


def _movimiento_display_partner(mov: MovimientoContable) -> dict:
    view = _movimiento_display(mov)
    tipo_raw = (mov.tipo or "").lower()
    signed = _partner_payment_signed(mov)
    view["monto_firmado"] = signed
    if tipo_raw == "pago":
        view["naturaleza"] = "ABONO"
    elif tipo_raw == "reverso_pago":
        view["naturaleza"] = "REVERSO"
    return view


def _build_partner_ledger(
    db: Session,
    *,
    partner_type: str,
    partner_id: int,
    allowed_suc_ids: list[int] | None,
) -> list[dict]:
    if partner_type == "cliente":
        tipo_op = TipoOperacion.venta
        notes_query = db.query(Nota).filter(Nota.cliente_id == partner_id)
    else:
        tipo_op = TipoOperacion.compra
        notes_query = db.query(Nota).filter(Nota.proveedor_id == partner_id)

    notes_query = notes_query.filter(Nota.tipo_operacion == tipo_op, Nota.estado.in_([NotaEstado.aprobada, NotaEstado.cancelada]))
    notes_query = _apply_sucursal_filter(notes_query, allowed_suc_ids, None, Nota.sucursal_id)
    notas = notes_query.all()
    if not notas:
        return []

    note_ids = [n.id for n in notas]
    folio_map = _build_folio_map(notas)

    base_movs = {
        mov.nota_id: mov
        for mov in db.query(MovimientoContable)
        .filter(
            MovimientoContable.nota_id.in_(note_ids),
            MovimientoContable.tipo.in_([tipo_op.value]),
        )
        .all()
    }
    reversos = (
        db.query(MovimientoContable)
        .filter(
            MovimientoContable.nota_id.in_(note_ids),
            MovimientoContable.tipo.in_(["reverso", "reverso_pago"]),
        )
        .all()
    )
    pagos = (
        db.query(NotaPago)
        .filter(NotaPago.nota_id.in_(note_ids))
        .order_by(NotaPago.created_at.asc())
        .all()
    )

    events: list[dict] = []
    for nota in notas:
        base_mov = base_movs.get(nota.id)
        fecha = base_mov.created_at if base_mov and base_mov.created_at else nota.created_at
        total = Decimal(str(nota.total_monto or 0))
        events.append(
            {
                "fecha": fecha,
                "orden": 0,
                "tipo": "Nota aprobada",
                "nota_id": nota.id,
                "folio": folio_map.get(nota.id) or f"#{nota.id}",
                "cargo": total,
                "abono": Decimal("0"),
                "metodo": "-",
                "cuenta": "-",
                "comentario": nota.comentarios_admin or "",
            }
        )

    for pago in pagos:
        cuenta_label = pago.cuenta.display_label if pago.cuenta else (pago.cuenta_financiera or "-")
        events.append(
            {
                "fecha": pago.created_at,
                "orden": 1,
                "tipo": "Pago",
                "nota_id": pago.nota_id,
                "folio": folio_map.get(pago.nota_id) or f"#{pago.nota_id}",
                "cargo": Decimal("0"),
                "abono": Decimal(str(pago.monto or 0)),
                "metodo": pago.metodo_pago or "-",
                "cuenta": cuenta_label,
                "comentario": pago.comentario or "",
            }
        )

    for mov in reversos:
        monto = abs(Decimal(str(mov.monto or 0)))
        if mov.tipo == "reverso":
            events.append(
                {
                    "fecha": mov.created_at,
                    "orden": 2,
                    "tipo": "Devolucion",
                    "nota_id": mov.nota_id,
                    "folio": folio_map.get(mov.nota_id) or f"#{mov.nota_id}",
                    "cargo": Decimal("0"),
                    "abono": monto,
                    "metodo": mov.metodo_pago or "-",
                    "cuenta": mov.cuenta.display_label if mov.cuenta else (mov.cuenta_financiera or "-"),
                    "comentario": mov.comentario or "",
                }
            )
        elif mov.tipo == "reverso_pago":
            events.append(
                {
                    "fecha": mov.created_at,
                    "orden": 3,
                    "tipo": "Reverso pago",
                    "nota_id": mov.nota_id,
                    "folio": folio_map.get(mov.nota_id) or f"#{mov.nota_id}",
                    "cargo": monto,
                    "abono": Decimal("0"),
                    "metodo": mov.metodo_pago or "-",
                    "cuenta": mov.cuenta.display_label if mov.cuenta else (mov.cuenta_financiera or "-"),
                    "comentario": mov.comentario or "",
                }
            )

    events = [e for e in events if e["fecha"] is not None]
    events.sort(key=lambda e: (e["fecha"], e["orden"]))

    saldo = Decimal("0")
    for event in events:
        saldo += event["cargo"] - event["abono"]
        event["saldo"] = saldo

    return events

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


def _build_notas_estado_links(folio_query: str | None) -> dict[str, str]:
    def build(estado: str | None) -> str:
        params: dict[str, str] = {}
        if folio_query:
            params["folio"] = folio_query
        if estado:
            params["estado"] = estado
        qs = urlencode(params)
        return f"/web/admin/notas?{qs}" if qs else "/web/admin/notas"

    return {
        "TODAS": build(None),
        "BORRADOR": build("BORRADOR"),
        "EN_REVISION": build("EN_REVISION"),
        "APROBADA": build("APROBADA"),
        "CANCELADA": build("CANCELADA"),
    }


def _filter_notes_by_query(notas: list[Nota], q: str | None) -> tuple[list[Nota], dict[int, str]]:
    folio_map = _build_folio_map(notas)
    if not q:
        return notas, folio_map
    term = q.strip().lower()
    if not term:
        return notas, folio_map
    filtered: list[Nota] = []
    for nota in notas:
        folio = (folio_map.get(nota.id) or "").lower()
        if term in str(nota.id) or (folio and term in folio):
            filtered.append(nota)
    return filtered, folio_map


def _build_partner_record_rows(notas: list[Nota], folio_map: dict[int, str]) -> list[dict]:
    rows: list[dict] = []
    for nota in notas:
        total = Decimal(str(nota.total_monto or 0))
        pagado = Decimal(str(nota.monto_pagado or 0))
        saldo_aplicable = nota.estado == NotaEstado.aprobada
        saldo = (total - pagado) if saldo_aplicable else Decimal("0")
        saldo_pendiente = saldo if saldo > Decimal("0") else Decimal("0")
        saldo_favor = -saldo if saldo < Decimal("0") else Decimal("0")
        rows.append(
            {
                "nota": nota,
                "folio": folio_map.get(nota.id) or "-",
                "total": total,
                "pagado": pagado,
                "saldo": saldo,
                "saldo_pendiente": saldo_pendiente,
                "saldo_favor": saldo_favor,
                "saldo_aplicable": saldo_aplicable,
            }
        )
    return rows

def _parse_owner_key(owner_key: str | None) -> tuple[str | None, int | None]:
    if not owner_key:
        return None, None
    try:
        owner_type, raw_id = owner_key.split(":", 1)
        owner_id = int(raw_id)
    except (ValueError, AttributeError):
        return None, None
    if owner_type not in ("sucursal", "cliente", "proveedor"):
        return None, None
    return owner_type, owner_id


def _build_owner_key_from_cuenta(cuenta: Cuenta | None) -> str:
    if not cuenta:
        return ""
    if cuenta.sucursal_id:
        return f"sucursal:{cuenta.sucursal_id}"
    if cuenta.cliente_id:
        return f"cliente:{cuenta.cliente_id}"
    if cuenta.proveedor_id:
        return f"proveedor:{cuenta.proveedor_id}"
    return ""


def _build_cuenta_owner_label(
    cuenta: Cuenta,
    sucursales_map: dict[int, str],
    clientes_map: dict[int, str],
    proveedores_map: dict[int, str],
) -> str:
    if cuenta.sucursal_id:
        return f"Sucursal: {sucursales_map.get(cuenta.sucursal_id, cuenta.sucursal_id)}"
    if cuenta.cliente_id:
        return f"Cliente: {clientes_map.get(cuenta.cliente_id, cuenta.cliente_id)}"
    if cuenta.proveedor_id:
        return f"Proveedor: {proveedores_map.get(cuenta.proveedor_id, cuenta.proveedor_id)}"
    return "Sin vinculo"


def _render_cuenta_form(
    request: Request,
    db: Session,
    current_user: dict,
    *,
    cuenta: Cuenta | None,
    owner_key: str,
    error: str | None,
    form_data: dict | None = None,
):
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    clientes = db.query(Cliente).order_by(Cliente.nombre_completo).all()
    proveedores = db.query(Proveedor).order_by(Proveedor.nombre_completo).all()
    return templates.TemplateResponse(
        "admin/cuenta_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "cuenta": cuenta,
            "sucursales": sucursales,
            "clientes": clientes,
            "proveedores": proveedores,
            "owner_key": owner_key or "",
            "error": error,
            "form_data": form_data,
        },
        status_code=400 if error else 200,
    )


def _get_cuentas_for_nota(db: Session, nota: Nota) -> tuple[list[Cuenta], list[Cuenta]]:
    cuentas_sucursal = (
        db.query(Cuenta)
        .filter(
            Cuenta.activo.is_(True),
            Cuenta.sucursal_id == nota.sucursal_id,
        )
        .order_by(Cuenta.nombre)
        .all()
    )
    cuentas_partner: list[Cuenta] = []
    if nota.tipo_operacion == TipoOperacion.compra and nota.proveedor_id:
        cuentas_partner = (
            db.query(Cuenta)
            .filter(
                Cuenta.activo.is_(True),
                Cuenta.proveedor_id == nota.proveedor_id,
            )
            .order_by(Cuenta.nombre)
            .all()
        )
    elif nota.tipo_operacion == TipoOperacion.venta and nota.cliente_id:
        cuentas_partner = (
            db.query(Cuenta)
            .filter(
                Cuenta.activo.is_(True),
                Cuenta.cliente_id == nota.cliente_id,
            )
            .order_by(Cuenta.nombre)
            .all()
        )
    return cuentas_sucursal, cuentas_partner

def _aggregate_partner_record_summary(notas: list[Nota]) -> dict:
    summary = {
        "total_notas": len(notas),
        "notas_aprobadas": 0,
        "notas_revision": 0,
        "notas_borrador": 0,
        "notas_canceladas": 0,
        "total_facturado": Decimal("0"),
        "total_pagado": Decimal("0"),
        "saldo_pendiente": Decimal("0"),
        "saldo_favor": Decimal("0"),
    }
    for nota in notas:
        if nota.estado == NotaEstado.aprobada:
            summary["notas_aprobadas"] += 1
            total = Decimal(str(nota.total_monto or 0))
            pagado = Decimal(str(nota.monto_pagado or 0))
            summary["total_facturado"] += total
            summary["total_pagado"] += pagado
            saldo = total - pagado
            if saldo > Decimal("0"):
                summary["saldo_pendiente"] += saldo
            elif saldo < Decimal("0"):
                summary["saldo_favor"] += -saldo
        elif nota.estado == NotaEstado.en_revision:
            summary["notas_revision"] += 1
        elif nota.estado == NotaEstado.borrador:
            summary["notas_borrador"] += 1
        elif nota.estado == NotaEstado.cancelada:
            summary["notas_canceladas"] += 1
    return summary

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

def _apply_sucursal_filter(query, allowed_ids: list[int] | None, sucursal_id: int | None, field):
    if allowed_ids is not None:
        if sucursal_id:
            query = query.filter(field == sucursal_id)
        else:
            query = query.filter(field.in_(allowed_ids))
    elif sucursal_id:
        query = query.filter(field == sucursal_id)
    return query


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


@router.get("/proveedores/{proveedor_id}/record")
async def proveedor_record(
    proveedor_id: int,
    request: Request,
    q: str | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    proveedor = db.get(Proveedor, proveedor_id)
    if not proveedor:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado.")

    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    notas_query = (
        db.query(Nota)
        .filter(
            Nota.proveedor_id == proveedor_id,
            Nota.tipo_operacion == TipoOperacion.compra,
            *([Nota.sucursal_id.in_(allowed_suc_ids)] if allowed_suc_ids else []),
        )
        .order_by(Nota.created_at.desc())
    )
    notas = notas_query.all()
    notas_filtradas, folio_map = _filter_notes_by_query(notas, q)
    rows = _build_partner_record_rows(notas_filtradas, folio_map)
    summary = _aggregate_partner_record_summary(notas)
    ledger_rows = _build_partner_ledger(
        db,
        partner_type="proveedor",
        partner_id=proveedor_id,
        allowed_suc_ids=allowed_suc_ids,
    )
    ledger_final = ledger_rows[-1]["saldo"] if ledger_rows else Decimal("0")
    ledger_saldo_label = "Saldo acumulado (por pagar al proveedor)"
    ledger_saldo_help = "Saldo positivo indica pendiente por pagar. Saldo negativo indica saldo a favor de la empresa."

    pagos_query = (
        db.query(NotaPago)
        .join(Nota, NotaPago.nota_id == Nota.id)
        .filter(
            Nota.proveedor_id == proveedor_id,
            Nota.tipo_operacion == TipoOperacion.compra,
            *([Nota.sucursal_id.in_(allowed_suc_ids)] if allowed_suc_ids else []),
        )
        .order_by(NotaPago.created_at.desc())
    )
    pagos = pagos_query.all()

    suc_query = db.query(Sucursal)
    if allowed_suc_ids:
        suc_query = suc_query.filter(Sucursal.id.in_(allowed_suc_ids))
    sucursales = {s.id: s for s in suc_query.all()}

    return templates.TemplateResponse(
        "admin/partner_record.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "partner": proveedor,
            "partner_label": "Proveedor",
            "partner_base": "proveedores",
            "tipo_operacion_label": "Compra",
            "record_rows": rows,
            "record_total_count": len(notas),
            "record_filtered_count": len(notas_filtradas),
            "summary": summary,
            "ledger_rows": ledger_rows,
            "ledger_final": ledger_final,
            "ledger_saldo_label": ledger_saldo_label,
            "ledger_saldo_help": ledger_saldo_help,
            "total_facturado_label": "Total compras aprobadas",
            "total_pagado_label": "Total pagado",
            "saldo_pendiente_label": "Saldo pendiente (por pagar al proveedor)",
            "saldo_favor_label": "Saldo a favor de la empresa",
            "pagos": pagos,
            "folio_map": folio_map,
            "sucursales": sucursales,
            "q": q or "",
        },
    )

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


@router.get("/clientes/{cliente_id}/record")
async def cliente_record(
    cliente_id: int,
    request: Request,
    q: str | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")

    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    notas_query = (
        db.query(Nota)
        .filter(
            Nota.cliente_id == cliente_id,
            Nota.tipo_operacion == TipoOperacion.venta,
            *([Nota.sucursal_id.in_(allowed_suc_ids)] if allowed_suc_ids else []),
        )
        .order_by(Nota.created_at.desc())
    )
    notas = notas_query.all()
    notas_filtradas, folio_map = _filter_notes_by_query(notas, q)
    rows = _build_partner_record_rows(notas_filtradas, folio_map)
    summary = _aggregate_partner_record_summary(notas)
    ledger_rows = _build_partner_ledger(
        db,
        partner_type="cliente",
        partner_id=cliente_id,
        allowed_suc_ids=allowed_suc_ids,
    )
    ledger_final = ledger_rows[-1]["saldo"] if ledger_rows else Decimal("0")
    ledger_saldo_label = "Saldo acumulado (por cobrar al cliente)"
    ledger_saldo_help = "Saldo positivo indica pendiente por cobrar. Saldo negativo indica saldo a favor del cliente."

    pagos_query = (
        db.query(NotaPago)
        .join(Nota, NotaPago.nota_id == Nota.id)
        .filter(
            Nota.cliente_id == cliente_id,
            Nota.tipo_operacion == TipoOperacion.venta,
            *([Nota.sucursal_id.in_(allowed_suc_ids)] if allowed_suc_ids else []),
        )
        .order_by(NotaPago.created_at.desc())
    )
    pagos = pagos_query.all()

    suc_query = db.query(Sucursal)
    if allowed_suc_ids:
        suc_query = suc_query.filter(Sucursal.id.in_(allowed_suc_ids))
    sucursales = {s.id: s for s in suc_query.all()}

    return templates.TemplateResponse(
        "admin/partner_record.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "partner": cliente,
            "partner_label": "Cliente",
            "partner_base": "clientes",
            "tipo_operacion_label": "Venta",
            "record_rows": rows,
            "record_total_count": len(notas),
            "record_filtered_count": len(notas_filtradas),
            "summary": summary,
            "ledger_rows": ledger_rows,
            "ledger_final": ledger_final,
            "ledger_saldo_label": ledger_saldo_label,
            "ledger_saldo_help": ledger_saldo_help,
            "total_facturado_label": "Total ventas aprobadas",
            "total_pagado_label": "Total cobrado",
            "saldo_pendiente_label": "Saldo pendiente (por cobrar al cliente)",
            "saldo_favor_label": "Saldo a favor del cliente",
            "pagos": pagos,
            "folio_map": folio_map,
            "sucursales": sucursales,
            "q": q or "",
        },
    )


# ---------- CUENTAS ----------


@router.get("/cuentas")
async def cuentas_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    params = request.query_params
    q = (params.get("q") or "").strip()
    owner_key = (params.get("owner_key") or "").strip()
    activo = (params.get("activo") or "").strip()

    query = db.query(Cuenta)
    if q:
        term = f"%{q}%"
        query = query.filter(
            or_(
                Cuenta.nombre.ilike(term),
                Cuenta.banco.ilike(term),
                Cuenta.numero.ilike(term),
                Cuenta.clabe.ilike(term),
                Cuenta.titular.ilike(term),
                Cuenta.referencia.ilike(term),
            )
        )

    owner_error = None
    owner_type, owner_id = _parse_owner_key(owner_key)
    if owner_key and not owner_type:
        owner_error = "Vinculo invalido."
        owner_key = ""
    elif owner_type == "sucursal":
        query = query.filter(Cuenta.sucursal_id == owner_id)
    elif owner_type == "cliente":
        query = query.filter(Cuenta.cliente_id == owner_id)
    elif owner_type == "proveedor":
        query = query.filter(Cuenta.proveedor_id == owner_id)

    if activo in ("1", "0"):
        query = query.filter(Cuenta.activo.is_(activo == "1"))

    cuentas = query.order_by(Cuenta.nombre).all()
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    clientes = db.query(Cliente).order_by(Cliente.nombre_completo).all()
    proveedores = db.query(Proveedor).order_by(Proveedor.nombre_completo).all()
    sucursales_map = {s.id: s.nombre for s in sucursales}
    clientes_map = {c.id: c.nombre_completo for c in clientes}
    proveedores_map = {p.id: p.nombre_completo for p in proveedores}

    cuentas_view = [
        {
            "cuenta": cuenta,
            "owner_label": _build_cuenta_owner_label(cuenta, sucursales_map, clientes_map, proveedores_map),
        }
        for cuenta in cuentas
    ]

    return templates.TemplateResponse(
        "admin/cuentas_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "cuentas": cuentas_view,
            "sucursales": sucursales,
            "clientes": clientes,
            "proveedores": proveedores,
            "owner_key": owner_key or "",
            "owner_error": owner_error,
            "activo": activo or "",
            "q": q or "",
        },
    )


@router.get("/cuentas/nueva")
async def cuenta_new_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    owner_key = (request.query_params.get("owner_key") or "").strip()
    owner_type, _ = _parse_owner_key(owner_key)
    error = None
    if owner_key and not owner_type:
        error = "Vinculo invalido."
        owner_key = ""
    return _render_cuenta_form(
        request,
        db,
        current_user,
        cuenta=None,
        owner_key=owner_key,
        error=error,
        form_data=None,
    )


@router.post("/cuentas/nueva")
async def cuenta_new_post(
    request: Request,
    nombre: str = Form(...),
    tipo: str = Form(""),
    banco: str = Form(""),
    numero: str = Form(""),
    clabe: str = Form(""),
    titular: str = Form(""),
    referencia: str = Form(""),
    owner_key: str = Form(""),
    activo: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nombre = nombre.strip()
    tipo = tipo.strip().lower()
    banco = banco.strip()
    numero = numero.strip()
    clabe = clabe.strip()
    titular = titular.strip()
    referencia = referencia.strip()
    owner_key = (owner_key or "").strip()

    if not nombre:
        return _render_cuenta_form(
            request,
            db,
            current_user,
            cuenta=None,
            owner_key=owner_key,
            error="El nombre de la cuenta es obligatorio.",
            form_data={
                "nombre": nombre,
                "tipo": tipo,
                "banco": banco,
                "numero": numero,
                "clabe": clabe,
                "titular": titular,
                "referencia": referencia,
                "activo": bool(activo),
            },
        )

    if tipo and tipo not in _CUENTA_TIPOS:
        return _render_cuenta_form(
            request,
            db,
            current_user,
            cuenta=None,
            owner_key=owner_key,
            error="Selecciona un tipo de cuenta valido.",
            form_data={
                "nombre": nombre,
                "tipo": tipo,
                "banco": banco,
                "numero": numero,
                "clabe": clabe,
                "titular": titular,
                "referencia": referencia,
                "activo": bool(activo),
            },
        )

    owner_type, owner_id = _parse_owner_key(owner_key)
    if owner_key and not owner_type:
        return _render_cuenta_form(
            request,
            db,
            current_user,
            cuenta=None,
            owner_key="",
            error="Vinculo invalido.",
            form_data={
                "nombre": nombre,
                "tipo": tipo,
                "banco": banco,
                "numero": numero,
                "clabe": clabe,
                "titular": titular,
                "referencia": referencia,
                "activo": bool(activo),
            },
        )

    sucursal_id = None
    cliente_id = None
    proveedor_id = None
    if owner_type == "sucursal":
        if not db.get(Sucursal, owner_id):
            return _render_cuenta_form(
                request,
                db,
                current_user,
                cuenta=None,
                owner_key="",
                error="Sucursal invalida.",
                form_data={
                    "nombre": nombre,
                    "tipo": tipo,
                    "banco": banco,
                    "numero": numero,
                    "clabe": clabe,
                    "titular": titular,
                    "referencia": referencia,
                    "activo": bool(activo),
                },
            )
        sucursal_id = owner_id
    elif owner_type == "cliente":
        if not db.get(Cliente, owner_id):
            return _render_cuenta_form(
                request,
                db,
                current_user,
                cuenta=None,
                owner_key="",
                error="Cliente invalido.",
                form_data={
                    "nombre": nombre,
                    "tipo": tipo,
                    "banco": banco,
                    "numero": numero,
                    "clabe": clabe,
                    "titular": titular,
                    "referencia": referencia,
                    "activo": bool(activo),
                },
            )
        cliente_id = owner_id
    elif owner_type == "proveedor":
        if not db.get(Proveedor, owner_id):
            return _render_cuenta_form(
                request,
                db,
                current_user,
                cuenta=None,
                owner_key="",
                error="Proveedor invalido.",
                form_data={
                    "nombre": nombre,
                    "tipo": tipo,
                    "banco": banco,
                    "numero": numero,
                    "clabe": clabe,
                    "titular": titular,
                    "referencia": referencia,
                    "activo": bool(activo),
                },
            )
        proveedor_id = owner_id

    cuenta = Cuenta(
        nombre=nombre,
        tipo=tipo or None,
        banco=banco or None,
        numero=numero or None,
        clabe=clabe or None,
        titular=titular or None,
        referencia=referencia or None,
        activo=bool(activo),
        sucursal_id=sucursal_id,
        cliente_id=cliente_id,
        proveedor_id=proveedor_id,
    )
    db.add(cuenta)
    db.commit()

    redirect_url = "/web/admin/cuentas"
    if owner_key:
        redirect_url = f"/web/admin/cuentas?owner_key={owner_key}"
    return RedirectResponse(url=redirect_url, status_code=303)


@router.get("/cuentas/{cuenta_id}/editar")
async def cuenta_edit_get(
    cuenta_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    cuenta = db.get(Cuenta, cuenta_id)
    if not cuenta:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")
    owner_key = _build_owner_key_from_cuenta(cuenta)
    return _render_cuenta_form(
        request,
        db,
        current_user,
        cuenta=cuenta,
        owner_key=owner_key,
        error=None,
        form_data=None,
    )


@router.post("/cuentas/{cuenta_id}/editar")
async def cuenta_edit_post(
    cuenta_id: int,
    request: Request,
    nombre: str = Form(...),
    tipo: str = Form(""),
    banco: str = Form(""),
    numero: str = Form(""),
    clabe: str = Form(""),
    titular: str = Form(""),
    referencia: str = Form(""),
    owner_key: str = Form(""),
    activo: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    cuenta = db.get(Cuenta, cuenta_id)
    if not cuenta:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")

    nombre = nombre.strip()
    tipo = tipo.strip().lower()
    banco = banco.strip()
    numero = numero.strip()
    clabe = clabe.strip()
    titular = titular.strip()
    referencia = referencia.strip()
    owner_key = (owner_key or "").strip()

    if not nombre:
        return _render_cuenta_form(
            request,
            db,
            current_user,
            cuenta=cuenta,
            owner_key=owner_key,
            error="El nombre de la cuenta es obligatorio.",
            form_data={
                "nombre": nombre,
                "tipo": tipo,
                "banco": banco,
                "numero": numero,
                "clabe": clabe,
                "titular": titular,
                "referencia": referencia,
                "activo": bool(activo),
            },
        )

    if tipo and tipo not in _CUENTA_TIPOS:
        return _render_cuenta_form(
            request,
            db,
            current_user,
            cuenta=cuenta,
            owner_key=owner_key,
            error="Selecciona un tipo de cuenta valido.",
            form_data={
                "nombre": nombre,
                "tipo": tipo,
                "banco": banco,
                "numero": numero,
                "clabe": clabe,
                "titular": titular,
                "referencia": referencia,
                "activo": bool(activo),
            },
        )

    owner_type, owner_id = _parse_owner_key(owner_key)
    if owner_key and not owner_type:
        return _render_cuenta_form(
            request,
            db,
            current_user,
            cuenta=cuenta,
            owner_key="",
            error="Vinculo invalido.",
            form_data={
                "nombre": nombre,
                "tipo": tipo,
                "banco": banco,
                "numero": numero,
                "clabe": clabe,
                "titular": titular,
                "referencia": referencia,
                "activo": bool(activo),
            },
        )

    sucursal_id = None
    cliente_id = None
    proveedor_id = None
    if owner_type == "sucursal":
        if not db.get(Sucursal, owner_id):
            return _render_cuenta_form(
                request,
                db,
                current_user,
                cuenta=cuenta,
                owner_key="",
                error="Sucursal invalida.",
                form_data={
                    "nombre": nombre,
                    "tipo": tipo,
                    "banco": banco,
                    "numero": numero,
                    "clabe": clabe,
                    "titular": titular,
                    "referencia": referencia,
                    "activo": bool(activo),
                },
            )
        sucursal_id = owner_id
    elif owner_type == "cliente":
        if not db.get(Cliente, owner_id):
            return _render_cuenta_form(
                request,
                db,
                current_user,
                cuenta=cuenta,
                owner_key="",
                error="Cliente invalido.",
                form_data={
                    "nombre": nombre,
                    "tipo": tipo,
                    "banco": banco,
                    "numero": numero,
                    "clabe": clabe,
                    "titular": titular,
                    "referencia": referencia,
                    "activo": bool(activo),
                },
            )
        cliente_id = owner_id
    elif owner_type == "proveedor":
        if not db.get(Proveedor, owner_id):
            return _render_cuenta_form(
                request,
                db,
                current_user,
                cuenta=cuenta,
                owner_key="",
                error="Proveedor invalido.",
                form_data={
                    "nombre": nombre,
                    "tipo": tipo,
                    "banco": banco,
                    "numero": numero,
                    "clabe": clabe,
                    "titular": titular,
                    "referencia": referencia,
                    "activo": bool(activo),
                },
            )
        proveedor_id = owner_id

    cuenta.nombre = nombre
    cuenta.tipo = tipo or None
    cuenta.banco = banco or None
    cuenta.numero = numero or None
    cuenta.clabe = clabe or None
    cuenta.titular = titular or None
    cuenta.referencia = referencia or None
    cuenta.activo = bool(activo)
    cuenta.sucursal_id = sucursal_id
    cuenta.cliente_id = cliente_id
    cuenta.proveedor_id = proveedor_id
    cuenta.updated_at = datetime.utcnow()
    db.add(cuenta)
    db.commit()

    return RedirectResponse(url=f"/web/admin/cuentas/{cuenta.id}", status_code=303)


@router.get("/cuentas/{cuenta_id}")
async def cuenta_detail(
    cuenta_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    cuenta = db.get(Cuenta, cuenta_id)
    if not cuenta:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")

    owner_label = "Sin vinculo"
    owner_kind = "general"
    if cuenta.sucursal_id:
        suc = db.get(Sucursal, cuenta.sucursal_id)
        owner_label = f"Sucursal: {suc.nombre if suc else cuenta.sucursal_id}"
        owner_kind = "sucursal"
    elif cuenta.cliente_id:
        cli = db.get(Cliente, cuenta.cliente_id)
        owner_label = f"Cliente: {cli.nombre_completo if cli else cuenta.cliente_id}"
        owner_kind = "cliente"
    elif cuenta.proveedor_id:
        prov = db.get(Proveedor, cuenta.proveedor_id)
        owner_label = f"Proveedor: {prov.nombre_completo if prov else cuenta.proveedor_id}"
        owner_kind = "proveedor"

    movimientos_query = db.query(MovimientoContable).filter(MovimientoContable.cuenta_id == cuenta_id)
    if owner_kind in ("proveedor", "cliente"):
        movimientos_query = movimientos_query.filter(
            MovimientoContable.tipo.in_(["pago", "reverso_pago"])
        )
    movimientos = (
        movimientos_query
        .order_by(MovimientoContable.created_at.desc())
        .limit(200)
        .all()
    )
    if owner_kind in ("proveedor", "cliente"):
        movimientos_view = [_movimiento_display_partner(m) for m in movimientos]
    else:
        movimientos_view = [_movimiento_display(m) for m in movimientos]
    total_ingresos = Decimal("0")
    total_egresos = Decimal("0")
    saldo_neto = Decimal("0")
    for mov in movimientos_view:
        saldo_neto += mov["monto_firmado"]
        if mov["monto_firmado"] >= 0:
            total_ingresos += mov["monto_firmado"]
        else:
            total_egresos += abs(mov["monto_firmado"])

    today = date.today()
    start_month = date(today.year, today.month, 1)

    def _shift_month(base: date, offset: int) -> date:
        month_idx = (base.month - 1) + offset
        year = base.year + (month_idx // 12)
        month = (month_idx % 12) + 1
        return date(year, month, 1)

    kpi_months: list[dict] = []
    month_map: dict[str, dict] = {}
    for offset in range(-11, 1):
        month_date = _shift_month(start_month, offset)
        key = f"{month_date.year}-{month_date.month:02d}"
        row = {
            "label": key,
            "ingresos": Decimal("0"),
            "egresos": Decimal("0"),
            "saldo": Decimal("0"),
            "movs": 0,
        }
        kpi_months.append(row)
        month_map[key] = row

    start_kpi = _shift_month(start_month, -11)
    start_dt = datetime(start_kpi.year, start_kpi.month, 1)
    kpi_query = db.query(MovimientoContable).filter(
        MovimientoContable.cuenta_id == cuenta_id,
        MovimientoContable.created_at >= start_dt,
    )
    if owner_kind in ("proveedor", "cliente"):
        kpi_query = kpi_query.filter(MovimientoContable.tipo.in_(["pago", "reverso_pago"]))
    kpi_movs = kpi_query.order_by(MovimientoContable.created_at.asc()).all()
    for mov in kpi_movs:
        if not mov.created_at:
            continue
        key = mov.created_at.strftime("%Y-%m")
        row = month_map.get(key)
        if not row:
            continue
        if owner_kind in ("proveedor", "cliente"):
            signed = _partner_payment_signed(mov)
        else:
            tipo_raw = (mov.tipo or "").lower()
            tipo_op = _movimiento_tipo_operacion(mov)
            signed = _movimiento_monto_firmado(mov, tipo_raw, tipo_op)
        row["saldo"] += signed
        if signed >= 0:
            row["ingresos"] += signed
        else:
            row["egresos"] += abs(signed)
        row["movs"] += 1

    kpi_current = kpi_months[-1] if kpi_months else None
    kpi_promedio = None
    kpi_best = None
    kpi_worst = None
    kpi_movs_total = sum((row["movs"] for row in kpi_months), 0)
    if kpi_months:
        total_net = sum((row["saldo"] for row in kpi_months), Decimal("0"))
        kpi_promedio = total_net / Decimal(len(kpi_months))
        kpi_best = max(kpi_months, key=lambda r: r["saldo"])
        kpi_worst = min(kpi_months, key=lambda r: r["saldo"])

    pagos = (
        db.query(NotaPago)
        .filter(NotaPago.cuenta_id == cuenta_id)
        .order_by(NotaPago.created_at.desc())
        .limit(200)
        .all()
    )
    pagos_total = Decimal("0")
    for pago in pagos:
        pagos_total += Decimal(str(pago.monto or 0))

    tipo_filter = None
    if owner_kind == "proveedor":
        tipo_filter = TipoOperacion.compra
    elif owner_kind == "cliente":
        tipo_filter = TipoOperacion.venta

    notas_query = db.query(Nota).filter(Nota.cuenta_financiera_id == cuenta_id)
    if tipo_filter:
        notas_query = notas_query.filter(Nota.tipo_operacion == tipo_filter)
    notas = notas_query.order_by(Nota.created_at.desc()).limit(200).all()

    notas_recon_query = db.query(Nota).filter(
        Nota.cuenta_financiera_id == cuenta_id,
        Nota.estado == NotaEstado.aprobada,
    )
    if tipo_filter:
        notas_recon_query = notas_recon_query.filter(Nota.tipo_operacion == tipo_filter)
    notas_recon = notas_recon_query.all()

    pagos_match_query = (
        db.query(NotaPago)
        .join(Nota, NotaPago.nota_id == Nota.id)
        .filter(
            NotaPago.cuenta_id == cuenta_id,
            Nota.cuenta_financiera_id == cuenta_id,
            Nota.estado == NotaEstado.aprobada,
        )
    )
    if tipo_filter:
        pagos_match_query = pagos_match_query.filter(Nota.tipo_operacion == tipo_filter)
    pagos_matched = pagos_match_query.all()

    recon_map: dict[tuple[str, int], dict] = {}
    for nota in notas_recon:
        if nota.tipo_operacion == TipoOperacion.compra:
            key = ("proveedor", nota.proveedor_id or 0)
        else:
            key = ("cliente", nota.cliente_id or 0)
        if not key[1]:
            continue
        entry = recon_map.setdefault(
            key,
            {
                "expected": Decimal("0"),
                "paid": Decimal("0"),
                "notas": 0,
                "pagos": 0,
            },
        )
        entry["expected"] += Decimal(str(nota.total_monto or 0))
        entry["notas"] += 1

    for pago in pagos_matched:
        nota = pago.nota
        if not nota:
            continue
        if nota.tipo_operacion == TipoOperacion.compra:
            key = ("proveedor", nota.proveedor_id or 0)
        else:
            key = ("cliente", nota.cliente_id or 0)
        if not key[1]:
            continue
        entry = recon_map.setdefault(
            key,
            {
                "expected": Decimal("0"),
                "paid": Decimal("0"),
                "notas": 0,
                "pagos": 0,
            },
        )
        entry["paid"] += Decimal(str(pago.monto or 0))
        entry["pagos"] += 1

    pagos_sin_nota_query = (
        db.query(NotaPago)
        .outerjoin(Nota, NotaPago.nota_id == Nota.id)
        .filter(NotaPago.cuenta_id == cuenta_id, Nota.id.is_(None))
    )
    pagos_sin_nota_count = pagos_sin_nota_query.order_by(None).count()
    pagos_sin_nota = pagos_sin_nota_query.order_by(NotaPago.created_at.desc()).limit(50).all()

    pagos_fuera_cuenta_query = (
        db.query(NotaPago)
        .join(Nota, NotaPago.nota_id == Nota.id)
        .filter(
            NotaPago.cuenta_id == cuenta_id,
            or_(Nota.cuenta_financiera_id.is_(None), Nota.cuenta_financiera_id != cuenta_id),
        )
    )
    if tipo_filter:
        pagos_fuera_cuenta_query = pagos_fuera_cuenta_query.filter(Nota.tipo_operacion == tipo_filter)
    pagos_fuera_cuenta_count = pagos_fuera_cuenta_query.order_by(None).count()
    pagos_fuera_cuenta = pagos_fuera_cuenta_query.order_by(NotaPago.created_at.desc()).limit(50).all()

    pagos_no_aprobados_query = (
        db.query(NotaPago)
        .join(Nota, NotaPago.nota_id == Nota.id)
        .filter(
            NotaPago.cuenta_id == cuenta_id,
            Nota.cuenta_financiera_id == cuenta_id,
            Nota.estado != NotaEstado.aprobada,
        )
    )
    if tipo_filter:
        pagos_no_aprobados_query = pagos_no_aprobados_query.filter(Nota.tipo_operacion == tipo_filter)
    pagos_no_aprobados_count = pagos_no_aprobados_query.order_by(None).count()
    pagos_no_aprobados = pagos_no_aprobados_query.order_by(NotaPago.created_at.desc()).limit(50).all()

    notas_for_folio = list(notas)
    note_ids = {n.id for n in notas}
    for pago in pagos:
        if pago.nota_id:
            note_ids.add(pago.nota_id)
    for pago in pagos_fuera_cuenta:
        if pago.nota_id:
            note_ids.add(pago.nota_id)
    for pago in pagos_no_aprobados:
        if pago.nota_id:
            note_ids.add(pago.nota_id)
    extra_ids = note_ids - {n.id for n in notas}
    if extra_ids:
        notas_extra = db.query(Nota).filter(Nota.id.in_(extra_ids)).all()
        notas_for_folio.extend(notas_extra)
    folio_map = _build_folio_map(notas_for_folio)
    nota_rows = _build_partner_record_rows(notas, folio_map)
    pendiente_total = Decimal("0")
    saldo_favor_total = Decimal("0")
    for nota in notas:
        if nota.estado != NotaEstado.aprobada:
            continue
        total = Decimal(str(nota.total_monto or 0))
        pagado = Decimal(str(nota.monto_pagado or 0))
        saldo = total - pagado
        if saldo >= 0:
            pendiente_total += saldo
        else:
            saldo_favor_total += -saldo

    suc_ids = {n.sucursal_id for n in notas_for_folio if n.sucursal_id}
    sucursales_map = {}
    if suc_ids:
        sucursales_map = {
            s.id: s for s in db.query(Sucursal).filter(Sucursal.id.in_(suc_ids)).all()
        }
    prov_ids = {n.proveedor_id for n in notas_for_folio if n.proveedor_id}
    cli_ids = {n.cliente_id for n in notas_for_folio if n.cliente_id}
    for key in recon_map:
        if key[0] == "proveedor":
            prov_ids.add(key[1])
        elif key[0] == "cliente":
            cli_ids.add(key[1])
    proveedores_map = {}
    clientes_map = {}
    if prov_ids:
        proveedores_map = {
            p.id: p for p in db.query(Proveedor).filter(Proveedor.id.in_(prov_ids)).all()
        }
    if cli_ids:
        clientes_map = {
            c.id: c for c in db.query(Cliente).filter(Cliente.id.in_(cli_ids)).all()
        }

    recon_rows: list[dict] = []
    for key, data in recon_map.items():
        partner_kind, partner_id = key
        partner = proveedores_map.get(partner_id) if partner_kind == "proveedor" else clientes_map.get(partner_id)
        partner_name = partner.nombre_completo if partner else f"ID {partner_id}"
        expected = data["expected"]
        paid = data["paid"]
        pending = expected - paid
        recon_rows.append(
            {
                "partner_kind": partner_kind,
                "partner_id": partner_id,
                "partner_name": partner_name,
                "expected": expected,
                "paid": paid,
                "pending": pending,
                "notas": data["notas"],
                "pagos": data["pagos"],
            }
        )
    recon_rows.sort(key=lambda r: r["pending"], reverse=True)

    recon_totals = {
        "expected": sum((row["expected"] for row in recon_rows), Decimal("0")),
        "paid": sum((row["paid"] for row in recon_rows), Decimal("0")),
        "pending": sum((row["pending"] for row in recon_rows), Decimal("0")),
        "notas": sum((row["notas"] for row in recon_rows), 0),
        "pagos": sum((row["pagos"] for row in recon_rows), 0),
    }
    recon_alerts_total = pagos_sin_nota_count + pagos_fuera_cuenta_count + pagos_no_aprobados_count

    return templates.TemplateResponse(
        "admin/cuenta_detail.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "cuenta": cuenta,
            "owner_label": owner_label,
            "owner_kind": owner_kind,
            "movimientos": movimientos_view,
            "pagos": pagos,
            "pagos_total": pagos_total,
            "nota_rows": nota_rows,
            "folio_map": folio_map,
            "sucursales_map": sucursales_map,
            "proveedores_map": proveedores_map,
            "clientes_map": clientes_map,
            "recon_rows": recon_rows,
            "recon_totals": recon_totals,
            "recon_alerts_total": recon_alerts_total,
            "pagos_sin_nota": pagos_sin_nota,
            "pagos_sin_nota_count": pagos_sin_nota_count,
            "pagos_fuera_cuenta": pagos_fuera_cuenta,
            "pagos_fuera_cuenta_count": pagos_fuera_cuenta_count,
            "pagos_no_aprobados": pagos_no_aprobados,
            "pagos_no_aprobados_count": pagos_no_aprobados_count,
            "movimientos_total": len(movimientos_view),
            "total_ingresos": total_ingresos,
            "total_egresos": total_egresos,
            "saldo_neto": saldo_neto,
            "notas_total": len(nota_rows),
            "pendiente_total": pendiente_total,
            "saldo_favor_total": saldo_favor_total,
            "kpi_months": kpi_months,
            "kpi_current": kpi_current,
            "kpi_promedio": kpi_promedio,
            "kpi_best": kpi_best,
            "kpi_worst": kpi_worst,
            "kpi_movs_total": kpi_movs_total,
        },
    )


# ---------- NOTAS ----------


@router.get("/notas")
async def notas_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    folio_query = (request.query_params.get("folio") or "").strip()
    estado_raw = (request.query_params.get("estado") or "").strip().upper()
    estado_aliases = {
        "REVISION": "EN_REVISION",
        "ENREVISION": "EN_REVISION",
        "APROBADO": "APROBADA",
        "CANCELADO": "CANCELADA",
        "TODOS": "",
        "TODAS": "",
    }
    if estado_raw in estado_aliases:
        estado_raw = estado_aliases[estado_raw]
    estado_filter = None
    estado_current = "TODAS"
    if estado_raw and estado_raw in {e.value for e in NotaEstado}:
        estado_filter = NotaEstado(estado_raw)
        estado_current = estado_filter.value
    estado_labels = {
        "TODAS": "Todas",
        "BORRADOR": "Borrador",
        "EN_REVISION": "En revision",
        "APROBADA": "Aprobadas",
        "CANCELADA": "Canceladas",
    }
    estado_label = estado_labels.get(estado_current, "Todas")
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
    estado_counts = {e.value: 0 for e in NotaEstado}
    counts_query = db.query(Nota.estado, func.count(Nota.id))
    if allowed_suc_ids:
        counts_query = counts_query.filter(Nota.sucursal_id.in_(allowed_suc_ids))
    counts_query = counts_query.group_by(Nota.estado).all()
    for estado, cantidad in counts_query:
        if estado and estado.value in estado_counts:
            estado_counts[estado.value] = int(cantidad or 0)
    estado_total = sum(estado_counts.values())
    notas_estado_query = db.query(Nota)
    if allowed_suc_ids:
        notas_estado_query = notas_estado_query.filter(Nota.sucursal_id.in_(allowed_suc_ids))
    if estado_filter:
        notas_estado_query = notas_estado_query.filter(Nota.estado == estado_filter)
    notas_estado = notas_estado_query.order_by(Nota.created_at.desc()).limit(200).all()

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
    notas_folio.extend(notas_estado)
    if folio_result:
        notas_folio.append(folio_result)
    folio_map = _build_folio_map(notas_folio)
    estado_links = _build_notas_estado_links(folio_query)

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
            "notas_estado": notas_estado,
            "estado_current": estado_current,
            "estado_label": estado_label,
            "estado_counts": estado_counts,
            "estado_total": estado_total,
            "estado_links": estado_links,
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
        if precio_val < 0:
            return render_error("El precio unitario no puede ser negativo.", rows)
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
    devolucion_check = None
    if nota.estado == NotaEstado.cancelada:
        cont_movs = (
            db.query(MovimientoContable)
            .filter(MovimientoContable.nota_id == nota.id)
            .all()
        )
        cont_saldo = Decimal("0")
        for mov in cont_movs:
            tipo_raw = (mov.tipo or "").lower()
            tipo_op = _movimiento_tipo_operacion(mov)
            cont_saldo += _movimiento_monto_firmado(mov, tipo_raw, tipo_op)
        inv_saldo = Decimal("0")
        for mov in inv_movs:
            inv_saldo += _signed_inventario_qty(mov)
        devolucion_check = {
            "contabilidad_saldo": cont_saldo,
            "contabilidad_ok": abs(cont_saldo) <= Decimal("0.01"),
            "contabilidad_movs": len(cont_movs),
            "inventario_saldo": inv_saldo,
            "inventario_ok": abs(inv_saldo) <= Decimal("0.001"),
            "inventario_movs": len(inv_movs),
            "aplica": bool(cont_movs or inv_movs),
        }
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
    cuentas_sucursal, cuentas_partner = _get_cuentas_for_nota(db, nota)
    cuentas_partner_label = "Proveedor" if nota.tipo_operacion == TipoOperacion.compra else "Cliente"
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
        "cuentas_sucursal": cuentas_sucursal,
        "cuentas_partner": cuentas_partner,
        "cuentas_partner_label": cuentas_partner_label,
        "pago_updated": pago_updated,
        "precios_updated": precios_updated,
        "edit_updated": edit_updated,
        "devolucion_check": devolucion_check,
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
            "trabajador_name": trabajador.nombre_completo if trabajador else None,
            "evidence_groups": evidence_groups,
            "total_subpesajes": total_sub,
            "missing_subpesajes": missing,
            "extra_evidencias": extra_evidencias,
            "extra_evidencias_total": len(extra_evidencias),
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
    if nota.factura_url and nota.factura_generada_at and nota.updated_at:
        if nota.factura_generada_at >= nota.updated_at:
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
    form = await request.form()
    comentarios_admin = (form.get("comentarios_admin") or "").strip()
    if nota.estado == NotaEstado.aprobada:
        try:
            note_service.cancel_approved_note(
                db,
                nota,
                admin_id=current_user.get("id"),
                comentarios_admin=comentarios_admin,
            )
        except ValueError as e:
            return _render_nota_detail(
                request, db, current_user, nota, error=str(e)
            )
    else:
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
    cuenta_id = None
    cuenta_error = None
    if params.get("cuenta_id"):
        try:
            cuenta_id = int(params.get("cuenta_id"))
        except ValueError:
            cuenta_id = None
            cuenta_error = "Cuenta invalida."
    if allowed_suc_ids is not None:
        if sucursal_id and sucursal_id not in allowed_suc_ids:
            sucursal_id = None
        if sucursal_id is None and len(allowed_suc_ids) == 1:
            sucursal_id = allowed_suc_ids[0]
    if cuenta_id:
        if not db.get(Cuenta, cuenta_id):
            cuenta_error = "Cuenta no encontrada."
            cuenta_id = None

    proveedores = db.query(Proveedor).order_by(Proveedor.nombre_completo).all()
    clientes = db.query(Cliente).order_by(Cliente.nombre_completo).all()
    cuentas = db.query(Cuenta).order_by(Cuenta.nombre).all()
    sucursales_all = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales_map = {s.id: s for s in sucursales}
    sucursal_names = {s.nombre for s in sucursales_all if s.nombre}
    proveedores_map = {p.id: p.nombre_completo for p in proveedores}
    clientes_map = {c.id: c.nombre_completo for c in clientes}

    notas_query = db.query(Nota).filter(Nota.estado == NotaEstado.aprobada)
    notas_query = _apply_sucursal_filter(notas_query, allowed_suc_ids, sucursal_id, Nota.sucursal_id)
    notas_aprobadas = notas_query.all()
    total_por_cobrar = Decimal("0")
    total_por_pagar = Decimal("0")
    saldo_favor_clientes = Decimal("0")
    saldo_favor_empresa = Decimal("0")
    total_ventas_aprobadas = Decimal("0")
    total_compras_aprobadas = Decimal("0")
    total_cobrado_clientes = Decimal("0")
    total_pagado_proveedores = Decimal("0")
    notas_consideradas = 0

    def _is_internal_partner(nombre: str | None) -> bool:
        if not nombre or not nombre.startswith("Sucursal "):
            return False
        suc_name = nombre.replace("Sucursal ", "", 1).strip()
        return suc_name in sucursal_names

    for nota in notas_aprobadas:
        total = Decimal(str(nota.total_monto or 0))
        pagado = Decimal(str(nota.monto_pagado or 0))
        diff = total - pagado
        if nota.tipo_operacion == TipoOperacion.venta:
            nombre = clientes_map.get(nota.cliente_id)
            if _is_internal_partner(nombre):
                continue
            notas_consideradas += 1
            total_ventas_aprobadas += total
            total_cobrado_clientes += pagado
            if diff >= Decimal("0"):
                total_por_cobrar += diff
            else:
                saldo_favor_clientes += -diff
        elif nota.tipo_operacion == TipoOperacion.compra:
            nombre = proveedores_map.get(nota.proveedor_id)
            if _is_internal_partner(nombre):
                continue
            notas_consideradas += 1
            total_compras_aprobadas += total
            total_pagado_proveedores += pagado
            if diff >= Decimal("0"):
                total_por_pagar += diff
            else:
                saldo_favor_empresa += -diff

    saldo_neto = total_por_cobrar - total_por_pagar
    saldo_scope = "Todas las sucursales"
    if sucursal_id:
        suc = sucursales_map.get(sucursal_id)
        saldo_scope = f"Sucursal {suc.nombre}" if suc else f"Sucursal {sucursal_id}"

    partner_key = (params.get("partner_key") or "").strip()
    partner_context = None
    partner_error = None
    if partner_key:
        try:
            partner_type, raw_id = partner_key.split(":", 1)
            partner_id = int(raw_id)
        except (ValueError, AttributeError):
            partner_error = "Seleccion invalida."
            partner_key = ""
        else:
            if partner_type == "cliente":
                partner = db.get(Cliente, partner_id)
                if not partner:
                    partner_error = "Cliente no encontrado."
                else:
                    notas_p = (
                        db.query(Nota)
                        .filter(
                            Nota.cliente_id == partner_id,
                            Nota.tipo_operacion == TipoOperacion.venta,
                        )
                    )
                    notas_p = _apply_sucursal_filter(notas_p, allowed_suc_ids, sucursal_id, Nota.sucursal_id)
                    notas_p = notas_p.order_by(Nota.created_at.desc()).all()
                    folio_map = _build_folio_map(notas_p)
                    record_rows = _build_partner_record_rows(notas_p, folio_map)
                    summary = _aggregate_partner_record_summary(notas_p)
                    pagos_p = (
                        db.query(NotaPago)
                        .join(Nota, NotaPago.nota_id == Nota.id)
                        .filter(
                            Nota.cliente_id == partner_id,
                            Nota.tipo_operacion == TipoOperacion.venta,
                        )
                    )
                    pagos_p = _apply_sucursal_filter(pagos_p, allowed_suc_ids, sucursal_id, Nota.sucursal_id)
                    pagos_p = pagos_p.order_by(NotaPago.created_at.desc()).all()
                    partner_context = {
                        "partner": partner,
                        "partner_label": "Cliente",
                        "tipo_operacion_label": "Venta",
                        "record_rows": record_rows,
                        "record_total_count": len(notas_p),
                        "summary": summary,
                        "pagos": pagos_p,
                        "folio_map": folio_map,
                        "record_link": f"/web/admin/clientes/{partner_id}/record",
                        "total_facturado_label": "Total ventas aprobadas",
                        "total_pagado_label": "Total cobrado",
                        "saldo_pendiente_label": "Saldo pendiente (por cobrar al cliente)",
                        "saldo_favor_label": "Saldo a favor del cliente",
                    }
            elif partner_type == "proveedor":
                partner = db.get(Proveedor, partner_id)
                if not partner:
                    partner_error = "Proveedor no encontrado."
                else:
                    notas_p = (
                        db.query(Nota)
                        .filter(
                            Nota.proveedor_id == partner_id,
                            Nota.tipo_operacion == TipoOperacion.compra,
                        )
                    )
                    notas_p = _apply_sucursal_filter(notas_p, allowed_suc_ids, sucursal_id, Nota.sucursal_id)
                    notas_p = notas_p.order_by(Nota.created_at.desc()).all()
                    folio_map = _build_folio_map(notas_p)
                    record_rows = _build_partner_record_rows(notas_p, folio_map)
                    summary = _aggregate_partner_record_summary(notas_p)
                    pagos_p = (
                        db.query(NotaPago)
                        .join(Nota, NotaPago.nota_id == Nota.id)
                        .filter(
                            Nota.proveedor_id == partner_id,
                            Nota.tipo_operacion == TipoOperacion.compra,
                        )
                    )
                    pagos_p = _apply_sucursal_filter(pagos_p, allowed_suc_ids, sucursal_id, Nota.sucursal_id)
                    pagos_p = pagos_p.order_by(NotaPago.created_at.desc()).all()
                    partner_context = {
                        "partner": partner,
                        "partner_label": "Proveedor",
                        "tipo_operacion_label": "Compra",
                        "record_rows": record_rows,
                        "record_total_count": len(notas_p),
                        "summary": summary,
                        "pagos": pagos_p,
                        "folio_map": folio_map,
                        "record_link": f"/web/admin/proveedores/{partner_id}/record",
                        "total_facturado_label": "Total compras aprobadas",
                        "total_pagado_label": "Total pagado",
                        "saldo_pendiente_label": "Saldo pendiente (por pagar al proveedor)",
                        "saldo_favor_label": "Saldo a favor de la empresa",
                    }
            else:
                partner_error = "Seleccion invalida."
                partner_key = ""

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
    if cuenta_id:
        query = query.filter(MovimientoContable.cuenta_id == cuenta_id)
    movimientos = query.order_by(MovimientoContable.created_at.desc()).limit(200).all()
    movimientos_view = [_movimiento_display(m) for m in movimientos]
    total_filtrado = sum((m["monto_firmado"] for m in movimientos_view), Decimal("0"))
    return templates.TemplateResponse(
        "admin/contabilidad_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "movimientos": movimientos_view,
            "sucursales": sucursales,
            "sucursal_id": sucursal_id,
            "date_from": date_from or "",
            "date_to": date_to or "",
            "cuenta_id": cuenta_id,
            "total_filtrado": total_filtrado,
            "export_query": export_query,
            "proveedores": proveedores,
            "clientes": clientes,
            "cuentas": cuentas,
            "sucursales_map": sucursales_map,
            "total_por_cobrar": total_por_cobrar,
            "total_por_pagar": total_por_pagar,
            "saldo_favor_clientes": saldo_favor_clientes,
            "saldo_favor_empresa": saldo_favor_empresa,
            "saldo_neto": saldo_neto,
            "saldo_scope": saldo_scope,
            "total_ventas_aprobadas": total_ventas_aprobadas,
            "total_compras_aprobadas": total_compras_aprobadas,
            "total_cobrado_clientes": total_cobrado_clientes,
            "total_pagado_proveedores": total_pagado_proveedores,
            "notas_consideradas": notas_consideradas,
            "partner_key": partner_key,
            "partner_context": partner_context,
            "partner_error": partner_error,
            "cuenta_error": cuenta_error,
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
    cuenta_id = None
    if params.get("cuenta_id"):
        try:
            cuenta_id = int(params.get("cuenta_id"))
        except ValueError:
            cuenta_id = None
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
    if cuenta_id:
        query = query.filter(MovimientoContable.cuenta_id == cuenta_id)
    movimientos = query.order_by(MovimientoContable.created_at.desc()).limit(1000).all()

    movimientos_view = [_movimiento_display(m) for m in movimientos]

    if fmt == "csv":
        import csv
        import io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow([
            "id", "tipo", "naturaleza", "monto_firmado", "nota_id", "sucursal",
            "usuario_id", "metodo_pago", "cuenta_financiera", "comentario", "created_at",
        ])
        for m in movimientos_view:
            writer.writerow([
                m["id"],
                m["tipo"],
                m["naturaleza"],
                float(m["monto_firmado"] or 0),
                m["nota_id"] or "",
                m["sucursal"],
                m["usuario_id"],
                m["metodo_pago"],
                m["cuenta_financiera"],
                m["comentario"],
                m["created_at"].strftime("%Y-%m-%d %H:%M") if m["created_at"] else "",
            ])
        output.seek(0)
        headers = {"Content-Disposition": "attachment; filename=movimientos_contables.csv"}
        return StreamingResponse(iter([output.getvalue()]), media_type="text/csv", headers=headers)

    headers_xml = ["id", "tipo", "naturaleza", "monto_firmado", "nota_id", "sucursal", "usuario_id", "metodo_pago", "cuenta_financiera", "comentario", "created_at"]

    if fmt in ("xlsx", "xls", "excel"):
        import io
        rows = []
        rows.append("<Row>" + "".join([f"<Cell><Data ss:Type='String'>{h}</Data></Cell>" for h in headers_xml]) + "</Row>")
        for m in movimientos_view:
            vals = [
                m["id"],
                m["tipo"],
                m["naturaleza"],
                float(m["monto_firmado"] or 0),
                m["nota_id"] or "",
                m["sucursal"],
                m["usuario_id"],
                m["metodo_pago"],
                m["cuenta_financiera"],
                m["comentario"].replace("\\n", " "),
                m["created_at"].strftime("%Y-%m-%d %H:%M") if m["created_at"] else "",
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
    cuenta_label = "Cuenta: Todas"
    if cuenta_id:
        cuenta = db.get(Cuenta, cuenta_id)
        if cuenta:
            cuenta_label = f"Cuenta: {cuenta.display_label}"
    range_label = f"Rango: {date_from or '---'} a {date_to or '---'}"
    text_lines = ["Movimientos contables", suc_label, cuenta_label, range_label, "", header_line]
    for m in movimientos_view:
        vals = [
            str(m["id"]),
            m["tipo"],
            m["naturaleza"],
            f"{float(m['monto_firmado'] or 0):.2f}",
            str(m["nota_id"] or ""),
            str(m["sucursal"] or ""),
            str(m["usuario_id"] or ""),
            m["metodo_pago"],
            m["cuenta_financiera"],
            m["comentario"].replace("\n", " "),
            m["created_at"].strftime("%Y-%m-%d %H:%M") if m["created_at"] else "",
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
    cuenta_id = None
    if params.get("cuenta_id"):
        try:
            cuenta_id = int(params.get("cuenta_id"))
        except ValueError:
            cuenta_id = None
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
        cuenta_id=cuenta_id,
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


