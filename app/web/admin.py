# app/web/admin.py
from collections import defaultdict
import io
import json
import logging
import re
import unicodedata

from fastapi import APIRouter, Depends, HTTPException, Request, Form, UploadFile, File
from fastapi.responses import RedirectResponse, StreamingResponse, JSONResponse
from sqlalchemy.orm import Session
from sqlalchemy import or_, func, and_
from sqlalchemy.exc import IntegrityError
from urllib.parse import urlencode
from decimal import Decimal, InvalidOperation
from datetime import datetime, date, time, timedelta, timezone
from typing import Iterable, List

from app.core.config import get_settings
from app.core.datetime_utils import format_date_iso, format_date_local, format_datetime_local, get_app_timezone, to_local_datetime
from app.core.security import hash_password
from app.services.auth import normalizar_password, normalizar_username
from app.db.deps import get_db
from app.models import (
    User,
    UserRole,
    UserStatus,
    Sucursal,
    SucursalStatus,
    Material,
    TablaPrecio,
    PriceChangeLog,
    TipoOperacion,
    TipoCliente,
    Proveedor,
    ProveedorPlaca,
    Cliente,
    ClientePlaca,
    AjusteSaldoPartner,
    Comisionario,
    ComisionarioNota,
    ComisionarioNotaMaterial,
    ComisionarioNotaEstado,
    ComisionarioPago,
    Nota,
    NotaEstado,
    NotaMaterial,
    Subpesaje,
    NotaEvidenciaExtra,
    NotaPago,
    NotaAjusteSaldo,
    ConversionMaterial,
    ConversionMaterialReversion,
    Cuenta,
    CuentaScrap360,
    CuentaScrap360Movimiento,
    CorteCaja,
    CorteCajaEstado,
    CorteCajaGasto,
    CorteCajaMovimiento,
    CorteCajaMovimientoTipo,
    CorteCajaDenominacion,
    Inventario,
    MovimientoContable,
    Material,
    InventarioMovimiento,
    InventarioValorPrecio,
    NotaDevolucionParcial,
    NotaDevolucionParcialLinea,
    NotaDevolucionTotal,
    InventarioAjusteManual,
)

from app.services.pricing_service import create_price_version
from app.services import (
    note_service,
    invoice_service,
    contabilidad_report_service,
    partner_report_service,
    scrap360_account_report_service,
    conversion_service,
    corte_caja_report_service,
    comision_service,
)
from app.services.evidence_service import build_evidence_groups
from app.services.firebase_storage import resolve_image_content_type, upload_image
from app.web.template_utils import create_templates

templates = create_templates()
settings = get_settings()
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/web/admin", tags=["web-admin"])

_TRANSFER_RELATED_NOTE_RE = re.compile(r"Nota (?:entrada|salida) #(\d+)")
_FOLIO_QUERY_RE = re.compile(r"^\s*(\d+)[-_]([CV])[_-](\d+)\s*$", re.IGNORECASE)
_CUENTA_TIPOS = ("cuenta bancaria", "cuenta cheques")
# TipoOperacion values are stored unaccented; these are their display labels.
TIPO_OPERACION_LABELS = {
    "compra": "Compra",
    "venta": "Venta",
    "comision": "Comisión",
}

_SCRAP360_TIPOS = ("transferencia", "cheques", "efectivo")
_SCRAP360_AJUSTE_DIRECCIONES = (
    ("entrada", "Entrada / deposito"),
    ("salida", "Salida / cargo"),
)
_SCRAP360_AJUSTE_CONCEPTOS = (
    ("deposito", "Depósito / dinero recibido"),
    ("comision_bancaria", "Comisión bancaria"),
    ("ajuste_manual", "Ajuste manual"),
    ("otro", "Otro"),
)
_CORTE_MOV_TIPOS = (
    ("INGRESO", "Ingreso"),
    ("EGRESO", "Egreso"),
    ("RETIRO", "Retiro"),
    ("DEPOSITO", "Deposito"),
)
_CORTE_GASTO_CATEGORIAS = (
    ("CAJA_CHICA", "Caja chica / operativo"),
    ("SERVICIOS", "Servicios"),
    ("VIATICOS", "Viaticos"),
    ("MANTENIMIENTO", "Mantenimiento"),
    ("ADMINISTRATIVO", "Administrativo"),
    ("OTRO", "Otro"),
)
_CORTE_MOV_CATEGORIAS = (
    ("DOTACION_EFECTIVO", "Dotacion de efectivo", "DEPOSITO"),
    ("SOBRANTE_VIATICOS", "Sobrante de viaticos", "INGRESO"),
    ("SOBRANTE_GASTOS", "Sobrante de gastos", "INGRESO"),
    ("AJUSTE_CAJA", "Ajuste de caja", "INGRESO"),
    ("DEPOSITO_BANCO", "Deposito a banco", "DEPOSITO"),
    ("RETIRO_CAJA", "Retiro de caja", "RETIRO"),
    ("OTRO", "Otro", "INGRESO"),
)
_CORTE_DENOMINACIONES = [
    {"label": "$0.10", "value": Decimal("0.10"), "key": "denom_0_1", "section": "MONEDAS"},
    {"label": "$0.20", "value": Decimal("0.20"), "key": "denom_0_2", "section": "MONEDAS"},
    {"label": "$0.50", "value": Decimal("0.50"), "key": "denom_0_5", "section": "MONEDAS"},
    {"label": "$1", "value": Decimal("1"), "key": "denom_1", "section": "MONEDAS"},
    {"label": "$2", "value": Decimal("2"), "key": "denom_2", "section": "MONEDAS"},
    {"label": "$5", "value": Decimal("5"), "key": "denom_5", "section": "MONEDAS"},
    {"label": "$10", "value": Decimal("10"), "key": "denom_10", "section": "MONEDAS"},
    {"label": "$20", "value": Decimal("20"), "key": "denom_20", "section": "BILLETES"},
    {"label": "$50", "value": Decimal("50"), "key": "denom_50", "section": "BILLETES"},
    {"label": "$100", "value": Decimal("100"), "key": "denom_100", "section": "BILLETES"},
    {"label": "$200", "value": Decimal("200"), "key": "denom_200", "section": "BILLETES"},
    {"label": "$500", "value": Decimal("500"), "key": "denom_500", "section": "BILLETES"},
    {"label": "$1,000", "value": Decimal("1000"), "key": "denom_1000", "section": "BILLETES"},
]


def _parse_corte_denominaciones_form(form) -> tuple[list[tuple[Decimal, int]], Decimal, dict[str, str], str | None]:
    form_data: dict[str, str] = {}
    denom_entries: list[tuple[Decimal, int]] = []
    saldo_total = Decimal("0")
    for denom in _CORTE_DENOMINACIONES:
        raw = (form.get(denom["key"]) or "").strip()
        form_data[denom["key"]] = raw
        if not raw:
            cantidad = 0
        else:
            try:
                cantidad = int(raw)
            except (ValueError, TypeError):
                return [], Decimal("0"), form_data, f"Cantidad invalida para {denom['label']}."
        if cantidad < 0:
            return [], Decimal("0"), form_data, "Las cantidades de denominaciones no pueden ser negativas."
        if cantidad:
            denom_entries.append((denom["value"], cantidad))
        saldo_total += denom["value"] * Decimal(str(cantidad))
    return denom_entries, saldo_total, form_data, None


def _replace_corte_denominaciones(db: Session, corte: CorteCaja, denom_entries: list[tuple[Decimal, int]]) -> None:
    db.query(CorteCajaDenominacion).filter(CorteCajaDenominacion.corte_id == corte.id).delete(synchronize_session=False)
    for valor, cantidad in denom_entries:
        db.add(
            CorteCajaDenominacion(
                corte_id=corte.id,
                valor=valor,
                cantidad=cantidad,
                created_at=datetime.utcnow(),
            )
        )


def _build_corte_denom_inputs(
    denom_map: dict[Decimal, int],
    form_data: dict | None = None,
) -> tuple[list[dict], list[dict], list[dict], Decimal, Decimal, Decimal]:
    form_data = form_data or {}
    denom_inputs: list[dict] = []
    monedas: list[dict] = []
    billetes: list[dict] = []
    total_monedas = Decimal("0")
    total_billetes = Decimal("0")

    for denom in _CORTE_DENOMINACIONES:
        key = denom["key"]
        if key in form_data:
            raw_val = form_data.get(key)
            count_val = raw_val if raw_val not in (None, "") else 0
        else:
            count_val = denom_map.get(denom["value"], 0)
        try:
            count_int = int(str(count_val))
        except (ValueError, TypeError):
            count_int = 0

        subtotal = denom["value"] * Decimal(str(count_int))
        item = {**denom, "count": count_val, "subtotal": subtotal}
        denom_inputs.append(item)
        if denom.get("section") == "MONEDAS":
            monedas.append(item)
            total_monedas += subtotal
        else:
            billetes.append(item)
            total_billetes += subtotal

    return denom_inputs, monedas, billetes, total_monedas, total_billetes, total_monedas + total_billetes


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
    if tipo_raw == "restauracion_pago":
        return f"RESTAURACION PAGO {tipo_op.upper()}" if tipo_op else "RESTAURACION PAGO"
    if tipo_raw == "reverso":
        return f"REVERSO {tipo_op.upper()}" if tipo_op else "REVERSO"
    if tipo_raw == "restauracion":
        return f"RESTAURACION {tipo_op.upper()}" if tipo_op else "RESTAURACION"
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
    if tipo_raw == "restauracion":
        if tipo_op == "compra":
            return "EGRESO"
        if tipo_op == "venta":
            return "INGRESO"
    if tipo_raw == "reverso_pago":
        if tipo_op == "compra":
            return "INGRESO"
        if tipo_op == "venta":
            return "EGRESO"
    if tipo_raw == "restauracion_pago":
        if tipo_op == "compra":
            return "EGRESO"
        if tipo_op == "venta":
            return "INGRESO"
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
    if tipo_raw == "restauracion":
        if tipo_op == "compra":
            return -abs_val
        if tipo_op == "venta":
            return abs_val
        return base
    if tipo_raw == "reverso_pago":
        if tipo_op == "compra":
            return abs_val
        if tipo_op == "venta":
            return -abs_val
        return base
    if tipo_raw == "restauracion_pago":
        if tipo_op == "compra":
            return -abs_val
        if tipo_op == "venta":
            return abs_val
        return base
    return base


def _movimiento_sucursal_id(mov: MovimientoContable) -> int | None:
    if mov.sucursal_id:
        return mov.sucursal_id
    if mov.nota and mov.nota.sucursal_id:
        return mov.nota.sucursal_id
    return None


def _movimiento_display(
    mov: MovimientoContable,
    sucursales_map: dict[int, Sucursal] | None = None,
    users_map: dict[int, str] | None = None,
) -> dict:
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
    sucursal_id = _movimiento_sucursal_id(mov)
    sucursal_label = "-"
    if sucursal_id and sucursales_map:
        suc = sucursales_map.get(sucursal_id)
        nombre = getattr(suc, "nombre", None) if suc is not None else None
        if nombre:
            sucursal_label = nombre
        elif suc is not None:
            sucursal_label = str(suc)
        else:
            sucursal_label = str(sucursal_id)
    elif mov.sucursal and mov.sucursal.nombre:
        sucursal_label = mov.sucursal.nombre
    elif sucursal_id:
        sucursal_label = str(sucursal_id)
    return {
        "id": mov.id,
        "tipo": label,
        "naturaleza": naturaleza,
        "monto_firmado": monto_firmado,
        "nota_id": mov.nota_id,
        "sucursal": sucursal_label,
        "usuario_id": (users_map or {}).get(mov.usuario_id) or ("-" if not mov.usuario_id else f"Usuario {mov.usuario_id}"),
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

def _nota_partner_key(nota: Nota) -> tuple[str | None, int | None]:
    if nota.proveedor_id:
        return "proveedor", nota.proveedor_id
    if nota.cliente_id:
        return "cliente", nota.cliente_id
    return None, None


def _build_partner_balance_group_maps(
    proveedores: list[Proveedor],
    clientes: list[Cliente],
) -> tuple[dict[int, tuple[str, int]], dict[int, tuple[str, int]]]:
    proveedores_map = {proveedor.id: proveedor for proveedor in proveedores if proveedor.id}
    clientes_map = {cliente.id: cliente for cliente in clientes if cliente.id}
    proveedor_groups = {
        proveedor_id: ("proveedor", proveedor_id)
        for proveedor_id in proveedores_map
    }
    cliente_groups: dict[int, tuple[str, int]] = {}

    for proveedor in proveedores:
        if not proveedor.id:
            continue
        linked_cliente_id = getattr(proveedor, "linked_cliente_id", None)
        if linked_cliente_id and linked_cliente_id in clientes_map:
            cliente_groups[linked_cliente_id] = ("proveedor", proveedor.id)

    for cliente in clientes:
        if not cliente.id:
            continue
        if cliente.id in cliente_groups:
            continue
        linked_proveedor_id = getattr(cliente, "linked_proveedor_id", None)
        if linked_proveedor_id and linked_proveedor_id in proveedores_map:
            cliente_groups[cliente.id] = ("proveedor", linked_proveedor_id)
        else:
            cliente_groups[cliente.id] = ("cliente", cliente.id)

    return proveedor_groups, cliente_groups


def _resolve_partner_balance_group_key(
    *,
    proveedor_id: int | None = None,
    cliente_id: int | None = None,
    proveedor_groups: dict[int, tuple[str, int]] | None = None,
    cliente_groups: dict[int, tuple[str, int]] | None = None,
) -> tuple[str, int] | None:
    if proveedor_id:
        proveedor_groups = proveedor_groups or {}
        return proveedor_groups.get(proveedor_id, ("proveedor", proveedor_id))
    if cliente_id:
        cliente_groups = cliente_groups or {}
        return cliente_groups.get(cliente_id, ("cliente", cliente_id))
    return None


def _build_partner_balance_group_metadata(
    proveedores: list[Proveedor],
    clientes: list[Cliente],
    *,
    proveedor_groups: dict[int, tuple[str, int]] | None = None,
    cliente_groups: dict[int, tuple[str, int]] | None = None,
) -> dict[tuple[str, int], dict[str, bool]]:
    metadata: dict[tuple[str, int], dict[str, bool]] = {}
    proveedor_groups = proveedor_groups or {}
    cliente_groups = cliente_groups or {}

    for proveedor in proveedores:
        if not proveedor.id:
            continue
        key = proveedor_groups.get(proveedor.id, ("proveedor", proveedor.id))
        bucket = metadata.setdefault(
            key,
            {"has_proveedor": False, "has_cliente": False},
        )
        bucket["has_proveedor"] = True

    for cliente in clientes:
        if not cliente.id:
            continue
        key = cliente_groups.get(cliente.id, ("cliente", cliente.id))
        bucket = metadata.setdefault(
            key,
            {"has_proveedor": False, "has_cliente": False},
        )
        bucket["has_cliente"] = True

    return metadata


# Punto 8 (fase 2): una sola clasificación de saldos para todo el sistema.
# La copia local divergía de la del reporte; la regla del par vinculado
# ("siempre en el bucket de clientes, con signo") vive en el service.
_classify_partner_group_balances = contabilidad_report_service._classify_partner_group_balances


# Punto 7 (fase 2): el motor de neteo vive ahora en note_service, consciente
# del par proveedor-cliente vinculado. Estos alias conservan los nombres que
# usan los call sites de este módulo.
_partner_note_sign = note_service.partner_note_sign
_signed_partner_amounts = note_service.signed_partner_amounts
_raw_note_payment_balance = note_service.raw_note_payment_balance
_get_note_balance_adjustment_totals_map = note_service._get_note_balance_adjustment_totals_map
_get_partner_adjustment_totals_map = note_service.get_partner_adjustment_totals_map
_build_effective_note_balance_map = note_service.build_effective_note_balance_map


def _note_balance_adjustment_signed(
    nota: Nota,
    *,
    note_delta: Decimal | None,
    partner_type: str | None,
) -> Decimal:
    delta = Decimal(str(note_delta or 0))
    sign = _partner_note_sign(partner_type, nota)
    return delta * sign


def _movimiento_display_partner(
    mov: MovimientoContable,
    sucursales_map: dict[int, Sucursal] | None = None,
) -> dict:
    view = _movimiento_display(mov, sucursales_map)
    tipo_raw = (mov.tipo or "").lower()
    signed = _partner_payment_signed(mov)
    view["monto_firmado"] = signed
    if tipo_raw == "pago":
        view["naturaleza"] = "ABONO"
    elif tipo_raw == "reverso_pago":
        view["naturaleza"] = "REVERSO"
    elif tipo_raw == "restauracion_pago":
        view["naturaleza"] = "RESTAURACION"
    return view


def _get_partner_adjustments(
    db: Session,
    *,
    partner_type: str,
    partner_id: int,
    allowed_suc_ids: list[int] | None = None,
    sucursal_id: int | None = None,
) -> list[AjusteSaldoPartner]:
    query = db.query(AjusteSaldoPartner).filter(
        AjusteSaldoPartner.partner_type == partner_type,
        AjusteSaldoPartner.partner_id == partner_id,
    )
    if allowed_suc_ids is not None:
        if sucursal_id:
            query = query.filter(AjusteSaldoPartner.sucursal_id == sucursal_id)
        else:
            query = query.filter(AjusteSaldoPartner.sucursal_id.in_(allowed_suc_ids))
    elif sucursal_id:
        query = query.filter(AjusteSaldoPartner.sucursal_id == sucursal_id)
    return query.order_by(AjusteSaldoPartner.created_at.asc()).all()


def _apply_movimiento_sucursal_filter(
    query,
    *,
    allowed_suc_ids: list[int] | None,
    sucursal_id: int | None,
):
    query = query.outerjoin(Nota, MovimientoContable.nota_id == Nota.id)
    if allowed_suc_ids is not None:
        if sucursal_id:
            return query.filter(
                or_(
                    MovimientoContable.sucursal_id == sucursal_id,
                    and_(
                        MovimientoContable.sucursal_id.is_(None),
                        Nota.sucursal_id == sucursal_id,
                    ),
                )
            )
        return query.filter(
            or_(
                MovimientoContable.sucursal_id.in_(allowed_suc_ids),
                and_(
                    MovimientoContable.sucursal_id.is_(None),
                    Nota.sucursal_id.in_(allowed_suc_ids),
                ),
            )
        )
    if sucursal_id:
        return query.filter(
            or_(
                MovimientoContable.sucursal_id == sucursal_id,
                and_(
                    MovimientoContable.sucursal_id.is_(None),
                    Nota.sucursal_id == sucursal_id,
                ),
            )
        )
    return query


def _sum_partner_adjustments(
    ajustes: Iterable[AjusteSaldoPartner],
) -> Decimal:
    total = Decimal("0")
    for ajuste in ajustes:
        total += Decimal(str(ajuste.monto or 0))
    return total


def _get_partner_adjustments_total(
    db: Session,
    *,
    partner_type: str,
    partner_id: int,
    allowed_suc_ids: list[int] | None = None,
    sucursal_id: int | None = None,
) -> Decimal:
    ajustes = _get_partner_adjustments(
        db,
        partner_type=partner_type,
        partner_id=partner_id,
        allowed_suc_ids=allowed_suc_ids,
        sucursal_id=sucursal_id,
    )
    return _sum_partner_adjustments(ajustes)


def _build_partner_ledger(
    db: Session,
    *,
    partner_type: str,
    partner_id: int,
    allowed_suc_ids: list[int] | None,
) -> list[dict]:
    if partner_type == "cliente":
        tipo_ops = [TipoOperacion.venta]
        notes_query = db.query(Nota).filter(Nota.cliente_id == partner_id)
    else:
        tipo_ops = [TipoOperacion.compra, TipoOperacion.venta]
        notes_query = db.query(Nota).filter(Nota.proveedor_id == partner_id)

    notes_query = notes_query.filter(
        Nota.tipo_operacion.in_(tipo_ops),
        Nota.estado.in_([NotaEstado.aprobada, NotaEstado.cancelada]),
    )
    notes_query = _apply_sucursal_filter(notes_query, allowed_suc_ids, None, Nota.sucursal_id)
    notas = notes_query.all()
    ajustes = _get_partner_adjustments(
        db,
        partner_type=partner_type,
        partner_id=partner_id,
        allowed_suc_ids=allowed_suc_ids,
    )
    if not notas and not ajustes:
        return []

    note_ids = [n.id for n in notas]
    folio_map = _build_folio_map(notas) if notas else {}

    base_movs = {}
    reversos = []
    pagos = []
    note_balance_adjustments: list[NotaAjusteSaldo] = []
    if note_ids:
        base_movs = {
            mov.nota_id: mov
            for mov in db.query(MovimientoContable)
            .filter(
                MovimientoContable.nota_id.in_(note_ids),
                MovimientoContable.tipo.in_([op.value for op in tipo_ops]),
            )
            .all()
        }
        reversos = (
            db.query(MovimientoContable)
            .filter(
                MovimientoContable.nota_id.in_(note_ids),
                MovimientoContable.tipo.in_(["reverso", "reverso_pago", "restauracion", "restauracion_pago"]),
            )
            .all()
        )
        pagos = (
            db.query(NotaPago)
            .filter(NotaPago.nota_id.in_(note_ids))
            .order_by(NotaPago.created_at.asc())
            .all()
        )
        note_balance_adjustments = (
            db.query(NotaAjusteSaldo)
            .filter(NotaAjusteSaldo.nota_id.in_(note_ids))
            .order_by(NotaAjusteSaldo.created_at.asc(), NotaAjusteSaldo.id.asc())
            .all()
        )

    events: list[dict] = []
    note_signs = {
        nota.id: _partner_note_sign(partner_type, nota)
        for nota in notas
    }
    for nota in notas:
        base_mov = base_movs.get(nota.id)
        fecha = base_mov.created_at if base_mov and base_mov.created_at else nota.created_at
        total = Decimal(str(nota.total_monto or 0))
        sign = note_signs.get(nota.id, Decimal("1"))
        delta = total * sign
        cargo = delta if delta >= 0 else Decimal("0")
        abono = Decimal("0") if delta >= 0 else -delta
        events.append(
            {
                "fecha": fecha,
                "orden": 0,
                "tipo": "Nota aprobada",
                "nota_id": nota.id,
                "folio": folio_map.get(nota.id) or f"#{nota.id}",
                "cargo": cargo,
                "abono": abono,
                "metodo": "-",
                "cuenta": "-",
                "comentario": nota.comentarios_admin or "",
            }
        )

    for pago in pagos:
        cuenta_label = pago.cuenta.display_label if pago.cuenta else (pago.cuenta_financiera or "-")
        sign = note_signs.get(pago.nota_id, Decimal("1"))
        delta = Decimal(str(pago.monto or 0)) * (-sign)
        cargo = delta if delta >= 0 else Decimal("0")
        abono = Decimal("0") if delta >= 0 else -delta
        events.append(
            {
                "fecha": pago.created_at,
                "orden": 1,
                "tipo": "Pago",
                "nota_id": pago.nota_id,
                "folio": folio_map.get(pago.nota_id) or f"#{pago.nota_id}",
                "cargo": cargo,
                "abono": abono,
                "metodo": pago.metodo_pago or "-",
                "cuenta": cuenta_label,
                "comentario": pago.comentario or "",
            }
        )

    for ajuste_nota in note_balance_adjustments:
        nota = next((n for n in notas if n.id == ajuste_nota.nota_id), None)
        if not nota:
            continue
        delta = _note_balance_adjustment_signed(
            nota,
            note_delta=Decimal(str(ajuste_nota.monto_delta or 0)),
            partner_type=partner_type,
        )
        cargo = delta if delta >= 0 else Decimal("0")
        abono = Decimal("0") if delta >= 0 else -delta
        tipo_label = "Ajuste saldo nota"
        if ajuste_nota.reversal_of_id:
            tipo_label = "Reversion ajuste saldo"
        events.append(
            {
                "fecha": ajuste_nota.created_at,
                "orden": 1,
                "tipo": tipo_label,
                "nota_id": ajuste_nota.nota_id,
                "folio": folio_map.get(ajuste_nota.nota_id) or f"#{ajuste_nota.nota_id}",
                "cargo": cargo,
                "abono": abono,
                "metodo": "-",
                "cuenta": "-",
                "comentario": ajuste_nota.comentario or "",
            }
        )

    for mov in reversos:
        monto = abs(Decimal(str(mov.monto or 0)))
        sign = note_signs.get(mov.nota_id, Decimal("1"))
        if mov.tipo == "reverso":
            delta = monto * (-sign)
            cargo = delta if delta >= 0 else Decimal("0")
            abono = Decimal("0") if delta >= 0 else -delta
            events.append(
                {
                    "fecha": mov.created_at,
                    "orden": 2,
                    "tipo": "Devolución",
                    "nota_id": mov.nota_id,
                    "folio": folio_map.get(mov.nota_id) or f"#{mov.nota_id}",
                    "cargo": cargo,
                    "abono": abono,
                    "metodo": mov.metodo_pago or "-",
                    "cuenta": mov.cuenta.display_label if mov.cuenta else (mov.cuenta_financiera or "-"),
                    "comentario": mov.comentario or "",
                }
            )
        elif mov.tipo == "reverso_pago":
            delta = monto * sign
            cargo = delta if delta >= 0 else Decimal("0")
            abono = Decimal("0") if delta >= 0 else -delta
            events.append(
                {
                    "fecha": mov.created_at,
                    "orden": 3,
                    "tipo": "Reverso de pago",
                    "nota_id": mov.nota_id,
                    "folio": folio_map.get(mov.nota_id) or f"#{mov.nota_id}",
                    "cargo": cargo,
                    "abono": abono,
                    "metodo": mov.metodo_pago or "-",
                    "cuenta": mov.cuenta.display_label if mov.cuenta else (mov.cuenta_financiera or "-"),
                    "comentario": mov.comentario or "",
                }
            )
        elif mov.tipo == "restauracion":
            delta = monto * sign
            cargo = delta if delta >= 0 else Decimal("0")
            abono = Decimal("0") if delta >= 0 else -delta
            events.append(
                {
                    "fecha": mov.created_at,
                    "orden": 2,
                    "tipo": "Restauración de devolución",
                    "nota_id": mov.nota_id,
                    "folio": folio_map.get(mov.nota_id) or f"#{mov.nota_id}",
                    "cargo": cargo,
                    "abono": abono,
                    "metodo": mov.metodo_pago or "-",
                    "cuenta": mov.cuenta.display_label if mov.cuenta else (mov.cuenta_financiera or "-"),
                    "comentario": mov.comentario or "",
                }
            )
        elif mov.tipo == "restauracion_pago":
            delta = monto * (-sign)
            cargo = delta if delta >= 0 else Decimal("0")
            abono = Decimal("0") if delta >= 0 else -delta
            events.append(
                {
                    "fecha": mov.created_at,
                    "orden": 3,
                    "tipo": "Restauración de pago",
                    "nota_id": mov.nota_id,
                    "folio": folio_map.get(mov.nota_id) or f"#{mov.nota_id}",
                    "cargo": cargo,
                    "abono": abono,
                    "metodo": mov.metodo_pago or "-",
                    "cuenta": mov.cuenta.display_label if mov.cuenta else (mov.cuenta_financiera or "-"),
                    "comentario": mov.comentario or "",
                }
            )

    for ajuste in ajustes:
        delta = Decimal(str(ajuste.monto or 0))
        cargo = delta if delta >= 0 else Decimal("0")
        abono = Decimal("0") if delta >= 0 else -delta
        events.append(
            {
                "fecha": ajuste.created_at,
                "orden": 4,
                "tipo": "Ajuste manual",
                "nota_id": None,
                "folio": "-",
                "cargo": cargo,
                "abono": abono,
                "metodo": "-",
                "cuenta": "-",
                "comentario": ajuste.comentario or "",
            }
        )

    events = [e for e in events if e["fecha"] is not None]
    events.sort(key=lambda e: (e["fecha"], e["orden"]))

    saldo = Decimal("0")
    for event in events:
        saldo += event["cargo"] - event["abono"]
        event["saldo"] = saldo

    return events

def _build_unified_partner_ledger(
    db: Session,
    *,
    proveedor_id: int | None,
    cliente_id: int | None,
    allowed_suc_ids: list[int] | None,
) -> list[dict]:
    notas_map: dict[int, Nota] = {}
    if proveedor_id:
        notes_query = db.query(Nota).filter(
            Nota.proveedor_id == proveedor_id,
            Nota.tipo_operacion.in_([TipoOperacion.compra, TipoOperacion.venta]),
        )
        notes_query = notes_query.filter(
            Nota.estado.in_([NotaEstado.aprobada, NotaEstado.cancelada]),
        )
        notes_query = _apply_sucursal_filter(notes_query, allowed_suc_ids, None, Nota.sucursal_id)
        for nota in notes_query.all():
            notas_map[nota.id] = nota
    if cliente_id:
        notes_query = db.query(Nota).filter(
            Nota.cliente_id == cliente_id,
            Nota.tipo_operacion == TipoOperacion.venta,
        )
        notes_query = notes_query.filter(
            Nota.estado.in_([NotaEstado.aprobada, NotaEstado.cancelada]),
        )
        notes_query = _apply_sucursal_filter(notes_query, allowed_suc_ids, None, Nota.sucursal_id)
        for nota in notes_query.all():
            notas_map.setdefault(nota.id, nota)

    notas = list(notas_map.values())

    ajustes: list[tuple[AjusteSaldoPartner, int]] = []
    if proveedor_id:
        ajustes.extend(
            [
                (a, 1)
                for a in _get_partner_adjustments(
                    db,
                    partner_type="proveedor",
                    partner_id=proveedor_id,
                    allowed_suc_ids=allowed_suc_ids,
                )
            ]
        )
    if cliente_id:
        ajustes.extend(
            [
                (a, -1)
                for a in _get_partner_adjustments(
                    db,
                    partner_type="cliente",
                    partner_id=cliente_id,
                    allowed_suc_ids=allowed_suc_ids,
                )
            ]
        )

    if not notas and not ajustes:
        return []

    note_ids = [n.id for n in notas]
    folio_map = _build_folio_map(notas) if notas else {}

    base_movs = {}
    reversos = []
    pagos = []
    note_balance_adjustments: list[NotaAjusteSaldo] = []
    if note_ids:
        base_movs = {
            mov.nota_id: mov
            for mov in db.query(MovimientoContable)
            .filter(
                MovimientoContable.nota_id.in_(note_ids),
                MovimientoContable.tipo.in_([TipoOperacion.compra.value, TipoOperacion.venta.value]),
            )
            .all()
        }
        reversos = (
            db.query(MovimientoContable)
            .filter(
                MovimientoContable.nota_id.in_(note_ids),
                MovimientoContable.tipo.in_(["reverso", "reverso_pago", "restauracion", "restauracion_pago"]),
            )
            .all()
        )
        pagos = (
            db.query(NotaPago)
            .filter(NotaPago.nota_id.in_(note_ids))
            .order_by(NotaPago.created_at.asc())
            .all()
        )
        note_balance_adjustments = (
            db.query(NotaAjusteSaldo)
            .filter(NotaAjusteSaldo.nota_id.in_(note_ids))
            .order_by(NotaAjusteSaldo.created_at.asc(), NotaAjusteSaldo.id.asc())
            .all()
        )

    def note_sign(nota: Nota) -> Decimal:
        return Decimal("1") if nota.tipo_operacion == TipoOperacion.compra else Decimal("-1")

    note_signs = {nota.id: note_sign(nota) for nota in notas}

    events: list[dict] = []
    for nota in notas:
        base_mov = base_movs.get(nota.id)
        fecha = base_mov.created_at if base_mov and base_mov.created_at else nota.created_at
        total = Decimal(str(nota.total_monto or 0))
        sign = note_signs.get(nota.id, Decimal("1"))
        delta = total * sign
        cargo = delta if delta >= 0 else Decimal("0")
        abono = Decimal("0") if delta >= 0 else -delta
        tipo_label = "Nota compra" if nota.tipo_operacion == TipoOperacion.compra else "Nota venta"
        events.append(
            {
                "fecha": fecha,
                "orden": 0,
                "tipo": tipo_label,
                "nota_id": nota.id,
                "folio": folio_map.get(nota.id) or f"#{nota.id}",
                "cargo": cargo,
                "abono": abono,
                "metodo": "-",
                "cuenta": "-",
                "comentario": nota.comentarios_admin or "",
            }
        )

    for pago in pagos:
        cuenta_label = pago.cuenta.display_label if pago.cuenta else (pago.cuenta_financiera or "-")
        sign = note_signs.get(pago.nota_id, Decimal("1"))
        delta = Decimal(str(pago.monto or 0)) * (-sign)
        cargo = delta if delta >= 0 else Decimal("0")
        abono = Decimal("0") if delta >= 0 else -delta
        nota = next((n for n in notas if n.id == pago.nota_id), None)
        tipo_label = "Pago compra" if nota and nota.tipo_operacion == TipoOperacion.compra else "Pago venta"
        events.append(
            {
                "fecha": pago.created_at,
                "orden": 1,
                "tipo": tipo_label,
                "nota_id": pago.nota_id,
                "folio": folio_map.get(pago.nota_id) or f"#{pago.nota_id}",
                "cargo": cargo,
                "abono": abono,
                "metodo": pago.metodo_pago or "-",
                "cuenta": cuenta_label,
                "comentario": pago.comentario or "",
            }
        )

    for ajuste_nota in note_balance_adjustments:
        nota = next((n for n in notas if n.id == ajuste_nota.nota_id), None)
        if not nota:
            continue
        sign = note_signs.get(ajuste_nota.nota_id, Decimal("1"))
        delta = Decimal(str(ajuste_nota.monto_delta or 0)) * sign
        cargo = delta if delta >= 0 else Decimal("0")
        abono = Decimal("0") if delta >= 0 else -delta
        tipo_label = "Ajuste saldo nota"
        if ajuste_nota.reversal_of_id:
            tipo_label = "Reversion ajuste saldo"
        events.append(
            {
                "fecha": ajuste_nota.created_at,
                "orden": 1,
                "tipo": tipo_label,
                "nota_id": ajuste_nota.nota_id,
                "folio": folio_map.get(ajuste_nota.nota_id) or f"#{ajuste_nota.nota_id}",
                "cargo": cargo,
                "abono": abono,
                "metodo": "-",
                "cuenta": "-",
                "comentario": ajuste_nota.comentario or "",
            }
        )

    for mov in reversos:
        monto = abs(Decimal(str(mov.monto or 0)))
        sign = note_signs.get(mov.nota_id, Decimal("1"))
        nota = next((n for n in notas if n.id == mov.nota_id), None)
        tipo_base = "compra" if nota and nota.tipo_operacion == TipoOperacion.compra else "venta"
        if mov.tipo == "reverso":
            delta = monto * (-sign)
            cargo = delta if delta >= 0 else Decimal("0")
            abono = Decimal("0") if delta >= 0 else -delta
            tipo_label = f"Devolución {tipo_base}"
            events.append(
                {
                    "fecha": mov.created_at,
                    "orden": 2,
                    "tipo": tipo_label,
                    "nota_id": mov.nota_id,
                    "folio": folio_map.get(mov.nota_id) or f"#{mov.nota_id}",
                    "cargo": cargo,
                    "abono": abono,
                    "metodo": mov.metodo_pago or "-",
                    "cuenta": mov.cuenta.display_label if mov.cuenta else (mov.cuenta_financiera or "-"),
                    "comentario": mov.comentario or "",
                }
            )
        elif mov.tipo == "reverso_pago":
            delta = monto * sign
            cargo = delta if delta >= 0 else Decimal("0")
            abono = Decimal("0") if delta >= 0 else -delta
            tipo_label = f"Reverso de pago {tipo_base}"
            events.append(
                {
                    "fecha": mov.created_at,
                    "orden": 3,
                    "tipo": tipo_label,
                    "nota_id": mov.nota_id,
                    "folio": folio_map.get(mov.nota_id) or f"#{mov.nota_id}",
                    "cargo": cargo,
                    "abono": abono,
                    "metodo": mov.metodo_pago or "-",
                    "cuenta": mov.cuenta.display_label if mov.cuenta else (mov.cuenta_financiera or "-"),
                    "comentario": mov.comentario or "",
                }
            )
        elif mov.tipo == "restauracion":
            delta = monto * sign
            cargo = delta if delta >= 0 else Decimal("0")
            abono = Decimal("0") if delta >= 0 else -delta
            tipo_label = f"Restauración de devolución {tipo_base}"
            events.append(
                {
                    "fecha": mov.created_at,
                    "orden": 2,
                    "tipo": tipo_label,
                    "nota_id": mov.nota_id,
                    "folio": folio_map.get(mov.nota_id) or f"#{mov.nota_id}",
                    "cargo": cargo,
                    "abono": abono,
                    "metodo": mov.metodo_pago or "-",
                    "cuenta": mov.cuenta.display_label if mov.cuenta else (mov.cuenta_financiera or "-"),
                    "comentario": mov.comentario or "",
                }
            )
        elif mov.tipo == "restauracion_pago":
            delta = monto * (-sign)
            cargo = delta if delta >= 0 else Decimal("0")
            abono = Decimal("0") if delta >= 0 else -delta
            tipo_label = f"Restauración de pago {tipo_base}"
            events.append(
                {
                    "fecha": mov.created_at,
                    "orden": 3,
                    "tipo": tipo_label,
                    "nota_id": mov.nota_id,
                    "folio": folio_map.get(mov.nota_id) or f"#{mov.nota_id}",
                    "cargo": cargo,
                    "abono": abono,
                    "metodo": mov.metodo_pago or "-",
                    "cuenta": mov.cuenta.display_label if mov.cuenta else (mov.cuenta_financiera or "-"),
                    "comentario": mov.comentario or "",
                }
            )

    for ajuste, sign in ajustes:
        delta = Decimal(str(ajuste.monto or 0)) * Decimal(str(sign))
        cargo = delta if delta >= 0 else Decimal("0")
        abono = Decimal("0") if delta >= 0 else -delta
        events.append(
            {
                "fecha": ajuste.created_at,
                "orden": 4,
                "tipo": "Ajuste manual",
                "nota_id": None,
                "folio": "-",
                "cargo": cargo,
                "abono": abono,
                "metodo": "-",
                "cuenta": "-",
                "comentario": ajuste.comentario or "",
            }
        )

    events = [e for e in events if e["fecha"] is not None]
    events.sort(key=lambda e: (e["fecha"], e["orden"]))

    saldo = Decimal("0")
    for event in events:
        saldo += event["cargo"] - event["abono"]
        event["saldo"] = saldo

    return events

def _aggregate_unified_partner_summary(
    *,
    compras: list[Nota],
    ventas: list[Nota],
    ajustes_proveedor: Decimal,
    ajustes_cliente: Decimal,
    note_adjustment_totals: dict[int, Decimal] | None = None,
) -> dict:
    summary = {
        "total_notas": len(compras) + len(ventas),
        "notas_aprobadas": 0,
        "notas_revision": 0,
        "notas_borrador": 0,
        "notas_canceladas": 0,
        "total_compras": Decimal("0"),
        "total_ventas": Decimal("0"),
        "total_pagado_compras": Decimal("0"),
        "total_cobrado_ventas": Decimal("0"),
        "saldo_pagar": Decimal("0"),
        "saldo_cobrar": Decimal("0"),
        "ajustes_proveedor": ajustes_proveedor,
        "ajustes_cliente": ajustes_cliente,
        "ajustes_nota_compras": Decimal("0"),
        "ajustes_nota_ventas": Decimal("0"),
        "saldo_neto": Decimal("0"),
        "saldo_neto_pendiente": Decimal("0"),
        "saldo_neto_favor": Decimal("0"),
    }
    note_adjustment_totals = note_adjustment_totals or {}

    for nota in compras:
        if nota.estado == NotaEstado.aprobada:
            summary["notas_aprobadas"] += 1
            total = Decimal(str(nota.total_monto or 0))
            pagado = Decimal(str(nota.monto_pagado or 0))
            summary["total_compras"] += total
            summary["total_pagado_compras"] += pagado
            summary["ajustes_nota_compras"] += Decimal(
                str(note_adjustment_totals.get(nota.id, Decimal("0")) or 0)
            )
        elif nota.estado == NotaEstado.en_revision:
            summary["notas_revision"] += 1
        elif nota.estado == NotaEstado.borrador:
            summary["notas_borrador"] += 1
        elif nota.estado == NotaEstado.cancelada:
            summary["notas_canceladas"] += 1

    for nota in ventas:
        if nota.estado == NotaEstado.aprobada:
            summary["notas_aprobadas"] += 1
            total = Decimal(str(nota.total_monto or 0))
            pagado = Decimal(str(nota.monto_pagado or 0))
            summary["total_ventas"] += total
            summary["total_cobrado_ventas"] += pagado
            summary["ajustes_nota_ventas"] += Decimal(
                str(note_adjustment_totals.get(nota.id, Decimal("0")) or 0)
            )
        elif nota.estado == NotaEstado.en_revision:
            summary["notas_revision"] += 1
        elif nota.estado == NotaEstado.borrador:
            summary["notas_borrador"] += 1
        elif nota.estado == NotaEstado.cancelada:
            summary["notas_canceladas"] += 1

    summary["saldo_pagar"] = (
        summary["total_compras"]
        - summary["total_pagado_compras"]
        + summary["ajustes_nota_compras"]
    )
    summary["saldo_cobrar"] = (
        summary["total_ventas"]
        - summary["total_cobrado_ventas"]
        + summary["ajustes_nota_ventas"]
    )

    neto = (summary["saldo_pagar"] + ajustes_proveedor) - (summary["saldo_cobrar"] + ajustes_cliente)
    summary["saldo_neto"] = neto
    if neto > Decimal("0"):
        summary["saldo_neto_pendiente"] = neto
    elif neto < Decimal("0"):
        summary["saldo_neto_favor"] = -neto

    return summary

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


def _extract_partner_placas(partner: Cliente | Proveedor) -> list[str]:
    placas = []
    if getattr(partner, "placas_rel", None):
        placas = [pl.placa for pl in partner.placas_rel if pl.placa]
    if not placas and getattr(partner, "placas", None):
        placas = [partner.placas]
    return _parse_placas("\n".join(placas))


def _filter_placas_for_cliente(db: Session, placas_list: list[str]) -> tuple[list[str], list[str]]:
    if not placas_list:
        return [], []
    existing_rel = db.query(ClientePlaca.placa).filter(ClientePlaca.placa.in_(placas_list)).all()
    existing_main = db.query(Cliente.placas).filter(Cliente.placas.in_(placas_list)).all()
    taken = {row[0] for row in existing_rel if row and row[0]}
    taken.update({row[0] for row in existing_main if row and row[0]})
    allowed = [pl for pl in placas_list if pl not in taken]
    skipped = [pl for pl in placas_list if pl in taken]
    return allowed, skipped


def _filter_placas_for_proveedor(db: Session, placas_list: list[str]) -> tuple[list[str], list[str]]:
    if not placas_list:
        return [], []
    existing_rel = db.query(ProveedorPlaca.placa).filter(ProveedorPlaca.placa.in_(placas_list)).all()
    existing_main = db.query(Proveedor.placas).filter(Proveedor.placas.in_(placas_list)).all()
    taken = {row[0] for row in existing_rel if row and row[0]}
    taken.update({row[0] for row in existing_main if row and row[0]})
    allowed = [pl for pl in placas_list if pl not in taken]
    skipped = [pl for pl in placas_list if pl in taken]
    return allowed, skipped


def _get_or_create_branch_cliente(db: Session, sucursal: Sucursal) -> Cliente:
    nombre = f"Sucursal {sucursal.nombre}"
    cliente = db.query(Cliente).filter(Cliente.nombre_completo == nombre).first()
    if cliente:
        if cliente.sucursal_id != sucursal.id:
            cliente.sucursal_id = sucursal.id
            db.add(cliente)
        return cliente
    cliente = Cliente(nombre_completo=nombre, sucursal_id=sucursal.id, activo=True)
    db.add(cliente)
    db.flush()
    return cliente


def _get_or_create_branch_proveedor(db: Session, sucursal: Sucursal) -> Proveedor:
    nombre = f"Sucursal {sucursal.nombre}"
    proveedor = db.query(Proveedor).filter(Proveedor.nombre_completo == nombre).first()
    if proveedor:
        if proveedor.sucursal_id != sucursal.id:
            proveedor.sucursal_id = sucursal.id
            db.add(proveedor)
        return proveedor
    proveedor = Proveedor(nombre_completo=nombre, sucursal_id=sucursal.id, activo=True)
    db.add(proveedor)
    db.flush()
    return proveedor


def _is_internal_partner_name(db: Session, nombre: str | None) -> bool:
    if not nombre or not nombre.startswith("Sucursal "):
        return False
    suc_name = nombre.replace("Sucursal ", "", 1).strip()
    if not suc_name:
        return False
    return db.query(Sucursal.id).filter(Sucursal.nombre == suc_name).first() is not None


def _parse_optional_int(raw: str | None) -> int | None:
    if raw is None:
        return None
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return None
    return value


def _get_formally_linked_cliente(db: Session, proveedor: Proveedor) -> Cliente | None:
    if proveedor.linked_cliente_id:
        linked = db.get(Cliente, proveedor.linked_cliente_id)
        if linked:
            return linked
    return db.query(Cliente).filter(Cliente.linked_proveedor_id == proveedor.id).first()


def _get_formally_linked_proveedor(db: Session, cliente: Cliente) -> Proveedor | None:
    if cliente.linked_proveedor_id:
        linked = db.get(Proveedor, cliente.linked_proveedor_id)
        if linked:
            return linked
    return db.query(Proveedor).filter(Proveedor.linked_cliente_id == cliente.id).first()


def _collect_proveedor_sales_bundle(
    db: Session,
    *,
    proveedor: Proveedor,
    allowed_suc_ids: list[int] | None,
    sucursal_id: int | None = None,
) -> dict:
    linked_cliente = None
    if not _is_internal_partner_name(db, proveedor.nombre_completo):
        linked_cliente = _get_formally_linked_cliente(db, proveedor)
        if linked_cliente and _is_internal_partner_name(db, linked_cliente.nombre_completo):
            linked_cliente = None

    ventas_directas_query = db.query(Nota).filter(
        Nota.proveedor_id == proveedor.id,
        Nota.tipo_operacion == TipoOperacion.venta,
    )
    ventas_directas_query = _apply_sucursal_filter(
        ventas_directas_query,
        allowed_suc_ids,
        sucursal_id,
        Nota.sucursal_id,
    )
    ventas_directas = ventas_directas_query.order_by(Nota.created_at.desc()).all()

    ventas_legado: list[Nota] = []
    if linked_cliente:
        ventas_legado_query = db.query(Nota).filter(
            Nota.cliente_id == linked_cliente.id,
            Nota.tipo_operacion == TipoOperacion.venta,
        )
        ventas_legado_query = _apply_sucursal_filter(
            ventas_legado_query,
            allowed_suc_ids,
            sucursal_id,
            Nota.sucursal_id,
        )
        ventas_legado = ventas_legado_query.order_by(Nota.created_at.desc()).all()

    ventas_map = {nota.id: nota for nota in ventas_directas}
    for nota in ventas_legado:
        ventas_map.setdefault(nota.id, nota)
    ventas = sorted(
        ventas_map.values(),
        key=lambda nota: nota.created_at or datetime.min,
        reverse=True,
    )
    return {
        "linked_cliente": linked_cliente,
        "ventas": ventas,
        "ventas_directas": ventas_directas,
        "ventas_legado": ventas_legado,
        "direct_enabled": bool(getattr(proveedor, "permite_ventas", False)) or bool(ventas_directas),
    }


def _get_linked_cliente(db: Session, proveedor: Proveedor) -> Cliente | None:
    linked = _get_formally_linked_cliente(db, proveedor)
    if linked:
        return linked
    placas_list = _extract_partner_placas(proveedor)
    return _find_existing_cliente_from_proveedor(
        db,
        placas_list=placas_list,
        correo=proveedor.correo_electronico,
        telefono=proveedor.telefono,
        sucursal_id=proveedor.sucursal_id,
    )


def _get_linked_proveedor(db: Session, cliente: Cliente) -> Proveedor | None:
    linked = _get_formally_linked_proveedor(db, cliente)
    if linked:
        return linked
    placas_list = _extract_partner_placas(cliente)
    return _find_existing_proveedor_from_cliente(
        db,
        placas_list=placas_list,
        correo=cliente.correo_electronico,
        telefono=cliente.telefono,
        sucursal_id=cliente.sucursal_id,
    )


def _normalize_partner_name(nombre: str | None) -> str:
    if not nombre:
        return ""
    return re.sub(r"\s+", " ", nombre).strip().upper()


def _find_cliente_by_exact_name(
    db: Session,
    *,
    nombre: str | None,
    sucursal_id: int | None = None,
) -> Cliente | None:
    normalized = _normalize_partner_name(nombre)
    if not normalized:
        return None
    query = db.query(Cliente)
    if sucursal_id:
        query = query.filter(Cliente.sucursal_id == sucursal_id)
    for cliente in query.order_by(Cliente.id).all():
        if _normalize_partner_name(cliente.nombre_completo) == normalized:
            return cliente
    return None


def _find_proveedor_by_exact_name(
    db: Session,
    *,
    nombre: str | None,
    sucursal_id: int | None = None,
) -> Proveedor | None:
    normalized = _normalize_partner_name(nombre)
    if not normalized:
        return None
    query = db.query(Proveedor)
    if sucursal_id:
        query = query.filter(Proveedor.sucursal_id == sucursal_id)
    for proveedor in query.order_by(Proveedor.id).all():
        if _normalize_partner_name(proveedor.nombre_completo) == normalized:
            return proveedor
    return None


def _build_counterpart_suggestion_for_proveedor(
    db: Session,
    proveedor: Proveedor,
) -> dict | None:
    if _is_internal_partner_name(db, proveedor.nombre_completo):
        return None

    linked_cliente = _get_formally_linked_cliente(db, proveedor)
    if linked_cliente and not _is_internal_partner_name(db, linked_cliente.nombre_completo):
        return {
            "candidate": linked_cliente,
            "reason": "Ya esta vinculado formalmente.",
            "can_link": True,
            "is_linked": True,
            "message": "Este proveedor ya opera tambien como cliente.",
        }

    placas_list = _extract_partner_placas(proveedor)
    candidate = _find_existing_cliente_from_proveedor(
        db,
        placas_list=placas_list,
        correo=proveedor.correo_electronico,
        telefono=proveedor.telefono,
        sucursal_id=proveedor.sucursal_id,
    )
    reason = "Coincidencia por placas, correo o telefono."
    if not candidate:
        candidate = _find_cliente_by_exact_name(
            db,
            nombre=proveedor.nombre_completo,
            sucursal_id=proveedor.sucursal_id,
        )
        reason = "Coincidencia exacta por nombre y sucursal."
    if not candidate or _is_internal_partner_name(db, candidate.nombre_completo):
        return None

    can_link = True
    message = "Se puede vincular sin crear un registro duplicado."
    if candidate.linked_proveedor_id and candidate.linked_proveedor_id != proveedor.id:
        can_link = False
        message = f"El cliente sugerido ya esta vinculado al proveedor ID {candidate.linked_proveedor_id}."
    elif candidate.sucursal_id and proveedor.sucursal_id and candidate.sucursal_id != proveedor.sucursal_id:
        can_link = False
        message = "La contraparte sugerida pertenece a otra sucursal."

    return {
        "candidate": candidate,
        "reason": reason,
        "can_link": can_link,
        "is_linked": False,
        "message": message,
    }


def _build_counterpart_suggestion_for_cliente(
    db: Session,
    cliente: Cliente,
) -> dict | None:
    if _is_internal_partner_name(db, cliente.nombre_completo):
        return None

    linked_proveedor = _get_formally_linked_proveedor(db, cliente)
    if linked_proveedor and not _is_internal_partner_name(db, linked_proveedor.nombre_completo):
        return {
            "candidate": linked_proveedor,
            "reason": "Ya esta vinculado formalmente.",
            "can_link": True,
            "is_linked": True,
            "message": "Este cliente ya opera tambien como proveedor.",
        }

    placas_list = _extract_partner_placas(cliente)
    candidate = _find_existing_proveedor_from_cliente(
        db,
        placas_list=placas_list,
        correo=cliente.correo_electronico,
        telefono=cliente.telefono,
        sucursal_id=cliente.sucursal_id,
    )
    reason = "Coincidencia por placas, correo o telefono."
    if not candidate:
        candidate = _find_proveedor_by_exact_name(
            db,
            nombre=cliente.nombre_completo,
            sucursal_id=cliente.sucursal_id,
        )
        reason = "Coincidencia exacta por nombre y sucursal."
    if not candidate or _is_internal_partner_name(db, candidate.nombre_completo):
        return None

    can_link = True
    message = "Se puede vincular sin crear un registro duplicado."
    if candidate.linked_cliente_id and candidate.linked_cliente_id != cliente.id:
        can_link = False
        message = f"El proveedor sugerido ya esta vinculado al cliente ID {candidate.linked_cliente_id}."
    elif candidate.sucursal_id and cliente.sucursal_id and candidate.sucursal_id != cliente.sucursal_id:
        can_link = False
        message = "La contraparte sugerida pertenece a otra sucursal."

    return {
        "candidate": candidate,
        "reason": reason,
        "can_link": can_link,
        "is_linked": False,
        "message": message,
    }


def _safe_next_admin_url(next_url: str | None, fallback: str) -> str:
    candidate = (next_url or "").strip()
    if candidate.startswith("/web/admin/"):
        return candidate
    return fallback


def _append_query_params(url: str, **params: str) -> str:
    clean_params = {key: value for key, value in params.items() if value}
    if not clean_params:
        return url
    joiner = "&" if "?" in url else "?"
    return f"{url}{joiner}{urlencode(clean_params)}"


def _unlink_cliente_proveedor(db: Session, *, cliente: Cliente | None = None, proveedor: Proveedor | None = None) -> None:
    if cliente and cliente.linked_proveedor_id:
        prev = db.get(Proveedor, cliente.linked_proveedor_id)
        if prev and prev.linked_cliente_id == cliente.id:
            prev.linked_cliente_id = None
            db.add(prev)
        cliente.linked_proveedor_id = None
        db.add(cliente)
    if proveedor and proveedor.linked_cliente_id:
        prev = db.get(Cliente, proveedor.linked_cliente_id)
        if prev and prev.linked_proveedor_id == proveedor.id:
            prev.linked_proveedor_id = None
            db.add(prev)
        proveedor.linked_cliente_id = None
        db.add(proveedor)


def _link_cliente_proveedor(db: Session, *, cliente: Cliente, proveedor: Proveedor) -> None:
    if _is_internal_partner_name(db, cliente.nombre_completo) or _is_internal_partner_name(
        db, proveedor.nombre_completo
    ):
        raise ValueError("No se puede vincular con una sucursal interna.")

    if not cliente.sucursal_id and proveedor.sucursal_id:
        cliente.sucursal_id = proveedor.sucursal_id
    if not proveedor.sucursal_id and cliente.sucursal_id:
        proveedor.sucursal_id = cliente.sucursal_id
    if cliente.sucursal_id and proveedor.sucursal_id and cliente.sucursal_id != proveedor.sucursal_id:
        raise ValueError("Cliente y proveedor deben pertenecer a la misma sucursal para vincularse.")

    if cliente.linked_proveedor_id and cliente.linked_proveedor_id != proveedor.id:
        raise ValueError("Este cliente ya está vinculado a otro proveedor. Desvincula primero.")
    if proveedor.linked_cliente_id and proveedor.linked_cliente_id != cliente.id:
        raise ValueError("Este proveedor ya está vinculado a otro cliente. Desvincula primero.")

    cliente.linked_proveedor_id = proveedor.id
    proveedor.linked_cliente_id = cliente.id
    db.add(cliente)
    db.add(proveedor)


def _list_linkable_proveedores(
    db: Session,
    *,
    allowed_suc_ids: list[int] | None = None,
    sucursal_id: int | None = None,
) -> list[Proveedor]:
    proveedores_query = db.query(Proveedor)
    proveedores_query = _apply_sucursal_filter(
        proveedores_query,
        allowed_suc_ids,
        sucursal_id,
        Proveedor.sucursal_id,
    )
    proveedores = proveedores_query.order_by(Proveedor.nombre_completo).all()
    return [p for p in proveedores if not _is_internal_partner_name(db, p.nombre_completo)]


def _list_linkable_clientes(
    db: Session,
    *,
    allowed_suc_ids: list[int] | None = None,
    sucursal_id: int | None = None,
) -> list[Cliente]:
    clientes_query = db.query(Cliente)
    clientes_query = _apply_sucursal_filter(
        clientes_query,
        allowed_suc_ids,
        sucursal_id,
        Cliente.sucursal_id,
    )
    clientes = clientes_query.order_by(Cliente.nombre_completo).all()
    return [c for c in clientes if not _is_internal_partner_name(db, c.nombre_completo)]


def _selected_sucursal_from_request(
    db: Session,
    *,
    raw_value: str | None,
    allowed_suc_ids: list[int] | None,
    default_id: int | None = None,
) -> tuple[int | None, str | None]:
    if raw_value is None or str(raw_value).strip() == "":
        return default_id, None
    sucursal_id = _parse_optional_int(raw_value)
    if sucursal_id is None:
        return default_id, "Sucursal invalida."
    if allowed_suc_ids is not None and sucursal_id not in allowed_suc_ids:
        return default_id, "Sucursal no autorizada."
    if not db.get(Sucursal, sucursal_id):
        return default_id, "Sucursal no encontrada."
    return sucursal_id, None


def _find_existing_cliente_from_proveedor(
    db: Session,
    *,
    placas_list: list[str],
    correo: str | None,
    telefono: str | None,
    sucursal_id: int | None = None,
) -> Cliente | None:
    base_query = db.query(Cliente)
    if sucursal_id:
        base_query = base_query.filter(Cliente.sucursal_id == sucursal_id)
    if placas_list:
        existing = (
            base_query
            .join(ClientePlaca, Cliente.id == ClientePlaca.cliente_id)
            .filter(ClientePlaca.placa.in_(placas_list))
            .first()
        )
        if existing:
            return existing
        existing = base_query.filter(Cliente.placas.in_(placas_list)).first()
        if existing:
            return existing
    if correo:
        existing = base_query.filter(Cliente.correo_electronico == correo).first()
        if existing:
            return existing
    if telefono:
        existing = base_query.filter(Cliente.telefono == telefono).first()
        if existing:
            return existing
    return None


def _find_existing_proveedor_from_cliente(
    db: Session,
    *,
    placas_list: list[str],
    correo: str | None,
    telefono: str | None,
    sucursal_id: int | None = None,
) -> Proveedor | None:
    base_query = db.query(Proveedor)
    if sucursal_id:
        base_query = base_query.filter(Proveedor.sucursal_id == sucursal_id)
    if placas_list:
        existing = (
            base_query
            .join(ProveedorPlaca, Proveedor.id == ProveedorPlaca.proveedor_id)
            .filter(ProveedorPlaca.placa.in_(placas_list))
            .first()
        )
        if existing:
            return existing
        existing = base_query.filter(Proveedor.placas.in_(placas_list)).first()
        if existing:
            return existing
    if correo:
        existing = base_query.filter(Proveedor.correo_electronico == correo).first()
        if existing:
            return existing
    if telefono:
        existing = base_query.filter(Proveedor.telefono == telefono).first()
        if existing:
            return existing
    return None


def _create_cliente_from_proveedor(
    db: Session,
    *,
    proveedor: Proveedor,
) -> tuple[Cliente, list[str]]:
    placas_list = _extract_partner_placas(proveedor)
    placas_ok, placas_skipped = _filter_placas_for_cliente(db, placas_list)
    cliente = Cliente(
        nombre_completo=proveedor.nombre_completo,
        sucursal_id=proveedor.sucursal_id,
        telefono=proveedor.telefono,
        correo_electronico=proveedor.correo_electronico,
        placas=placas_ok[0] if placas_ok else None,
        activo=proveedor.activo,
    )
    db.add(cliente)
    db.flush()
    _set_cliente_placas(db, cliente, placas_ok)
    return cliente, placas_skipped


def _create_proveedor_from_cliente(
    db: Session,
    *,
    cliente: Cliente,
) -> tuple[Proveedor, list[str]]:
    placas_list = _extract_partner_placas(cliente)
    placas_ok, placas_skipped = _filter_placas_for_proveedor(db, placas_list)
    proveedor = Proveedor(
        nombre_completo=cliente.nombre_completo,
        sucursal_id=cliente.sucursal_id,
        telefono=cliente.telefono,
        correo_electronico=cliente.correo_electronico,
        placas=placas_ok[0] if placas_ok else None,
        activo=cliente.activo,
    )
    db.add(proveedor)
    db.flush()
    _set_proveedor_placas(db, proveedor, placas_ok)
    return proveedor, placas_skipped


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
    if letter == "C":
        tipo_op = TipoOperacion.compra
    elif letter == "V":
        tipo_op = TipoOperacion.venta
    else:
        return None
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
        if folio:
            folio_map[nota.id] = folio
        elif nota.estado in (NotaEstado.borrador, NotaEstado.en_revision):
            folio_map[nota.id] = "Pendiente"
        else:
            folio_map[nota.id] = "-"
    return folio_map


def _build_notas_estado_links(
    folio_query: str | None,
    pago_filter: str | None = None,
    sucursal_id: int | None = None,
    proveedor_id: int | None = None,
    vencimiento_from: str | None = None,
    vencimiento_to: str | None = None,
    orden: str | None = None,
) -> dict[str, str]:
    def build(estado: str | None) -> str:
        params: dict[str, str] = {}
        if folio_query:
            params["folio"] = folio_query
        if sucursal_id:
            params["sucursal_id"] = str(sucursal_id)
        if proveedor_id:
            params["proveedor_id"] = str(proveedor_id)
        if vencimiento_from:
            params["vence_desde"] = vencimiento_from
        if vencimiento_to:
            params["vence_hasta"] = vencimiento_to
        if pago_filter and pago_filter != "TODAS":
            params["pago"] = pago_filter
        if orden and orden != "recientes":
            params["orden"] = orden
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


def _build_notas_pago_links(
    folio_query: str | None,
    estado_filter: str | None = None,
    sucursal_id: int | None = None,
    proveedor_id: int | None = None,
    vencimiento_from: str | None = None,
    vencimiento_to: str | None = None,
    orden: str | None = None,
) -> dict[str, str]:
    def build(pago: str | None) -> str:
        params: dict[str, str] = {}
        if folio_query:
            params["folio"] = folio_query
        if sucursal_id:
            params["sucursal_id"] = str(sucursal_id)
        if proveedor_id:
            params["proveedor_id"] = str(proveedor_id)
        if vencimiento_from:
            params["vence_desde"] = vencimiento_from
        if vencimiento_to:
            params["vence_hasta"] = vencimiento_to
        if estado_filter and estado_filter != "TODAS":
            params["estado"] = estado_filter
        if orden and orden != "recientes":
            params["orden"] = orden
        if pago and pago != "TODAS":
            params["pago"] = pago
        qs = urlencode(params)
        return f"/web/admin/notas?{qs}" if qs else "/web/admin/notas"

    return {
        "TODAS": build(None),
        "PAGADAS": build("PAGADAS"),
        "PENDIENTES": build("PENDIENTES"),
    }


def _build_notas_sucursal_links(
    sucursales: Iterable[Sucursal],
    *,
    folio_query: str | None = None,
    estado_filter: str | None = None,
    pago_filter: str | None = None,
    proveedor_id: int | None = None,
    vencimiento_from: str | None = None,
    vencimiento_to: str | None = None,
    orden: str | None = None,
) -> dict[str, str]:
    def build(target_sucursal_id: int | None) -> str:
        params: dict[str, str] = {}
        if folio_query:
            params["folio"] = folio_query
        if estado_filter and estado_filter != "TODAS":
            params["estado"] = estado_filter
        if pago_filter and pago_filter != "TODAS":
            params["pago"] = pago_filter
        if proveedor_id:
            params["proveedor_id"] = str(proveedor_id)
        if vencimiento_from:
            params["vence_desde"] = vencimiento_from
        if vencimiento_to:
            params["vence_hasta"] = vencimiento_to
        if orden and orden != "recientes":
            params["orden"] = orden
        if target_sucursal_id:
            params["sucursal_id"] = str(target_sucursal_id)
        qs = urlencode(params)
        return f"/web/admin/notas?{qs}" if qs else "/web/admin/notas"

    links = {"ALL": build(None)}
    for sucursal in sucursales:
        links[str(sucursal.id)] = build(sucursal.id)
    return links


def _build_notas_seguimiento_links(
    *,
    folio_query: str | None = None,
    estado_filter: str | None = None,
    pago_filter: str | None = None,
    sucursal_id: int | None = None,
    proveedor_id: int | None = None,
    vencimiento_from: str | None = None,
    vencimiento_to: str | None = None,
    orden: str | None = None,
) -> dict[str, str]:
    def build(seguimiento: str | None) -> str:
        params: dict[str, str] = {}
        if folio_query:
            params["folio"] = folio_query
        if estado_filter and estado_filter != "TODAS":
            params["estado"] = estado_filter
        if pago_filter and pago_filter != "TODAS":
            params["pago"] = pago_filter
        if sucursal_id:
            params["sucursal_id"] = str(sucursal_id)
        if proveedor_id:
            params["proveedor_id"] = str(proveedor_id)
        if vencimiento_from:
            params["vence_desde"] = vencimiento_from
        if vencimiento_to:
            params["vence_hasta"] = vencimiento_to
        if orden and orden != "recientes":
            params["orden"] = orden
        if seguimiento and seguimiento != "TODOS":
            params["seguimiento"] = seguimiento
        qs = urlencode(params)
        return f"/web/admin/notas?{qs}" if qs else "/web/admin/notas"

    return {
        "TODOS": build(None),
        "VENCIDAS": build("VENCIDAS"),
        "POR_VENCER": build("POR_VENCER"),
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


def _build_partner_record_rows(
    notas: list[Nota],
    folio_map: dict[int, str],
    partner_type: str | None = None,
    note_adjustment_totals: dict[int, Decimal] | None = None,
    effective_balance_map: dict[int, dict[str, Decimal | bool]] | None = None,
) -> list[dict]:
    rows: list[dict] = []
    note_adjustment_totals = note_adjustment_totals or {}
    effective_balance_map = effective_balance_map or {}
    for nota in notas:
        total, pagado = _signed_partner_amounts(nota, partner_type)
        balance_view = effective_balance_map.get(nota.id)
        if balance_view:
            saldo_aplicable = nota.estado == NotaEstado.aprobada
            note_delta_signed = Decimal(str(balance_view.get("ajuste_saldo_nota") or 0))
            saldo_original = Decimal(str(balance_view.get("saldo_original") or 0))
            saldo = Decimal(str(balance_view.get("saldo") or 0))
            saldo_pendiente = Decimal(str(balance_view.get("saldo_pendiente") or 0))
            saldo_favor = Decimal(str(balance_view.get("saldo_favor") or 0))
            saldo_pendiente_original = Decimal(str(balance_view.get("saldo_pendiente_original") or 0))
            saldo_favor_original = Decimal(str(balance_view.get("saldo_favor_original") or 0))
            ajuste_aplicado = Decimal(str(balance_view.get("ajuste_aplicado") or 0))
            saldo_cubierto_por_ajuste = bool(balance_view.get("saldo_cubierto_por_ajuste"))
            saldo_parcialmente_cubierto = bool(balance_view.get("saldo_parcialmente_cubierto"))
        else:
            saldo_aplicable = nota.estado == NotaEstado.aprobada
            note_delta_signed = _note_balance_adjustment_signed(
                nota,
                note_delta=note_adjustment_totals.get(nota.id, Decimal("0")),
                partner_type=partner_type,
            )
            saldo_original = (total - pagado) if saldo_aplicable else Decimal("0")
            saldo = (saldo_original + note_delta_signed) if saldo_aplicable else Decimal("0")
            saldo_pendiente = saldo if saldo > Decimal("0") else Decimal("0")
            saldo_favor = -saldo if saldo < Decimal("0") else Decimal("0")
            saldo_pendiente_original = saldo_original if saldo_original > Decimal("0") else Decimal("0")
            saldo_favor_original = -saldo_original if saldo_original < Decimal("0") else Decimal("0")
            ajuste_aplicado = Decimal("0")
            saldo_cubierto_por_ajuste = False
            saldo_parcialmente_cubierto = False
        rows.append(
            {
                "nota": nota,
                "folio": folio_map.get(nota.id) or "-",
                "total": total,
                "pagado": pagado,
                "saldo": saldo,
                "saldo_pendiente": saldo_pendiente,
                "saldo_favor": saldo_favor,
                "saldo_original": saldo_original,
                "saldo_pendiente_original": saldo_pendiente_original,
                "saldo_favor_original": saldo_favor_original,
                "ajuste_saldo_nota": note_delta_signed,
                "ajuste_aplicado": ajuste_aplicado,
                "saldo_cubierto_por_ajuste": saldo_cubierto_por_ajuste,
                "saldo_parcialmente_cubierto": saldo_parcialmente_cubierto,
                "saldo_aplicable": saldo_aplicable,
                "is_paid": saldo_aplicable and saldo_pendiente <= Decimal("0") and saldo_favor <= Decimal("0"),
            }
        )
    return rows


def _partner_adjustment_credit_pool(
    ajustes_delta: Decimal | None,
) -> Decimal:
    delta = ajustes_delta if ajustes_delta is not None else Decimal("0")
    return -delta if delta < Decimal("0") else Decimal("0")


def _apply_partner_adjustment_coverage(
    rows: list[dict],
    *,
    ajustes_delta: Decimal | None,
) -> dict[str, Decimal]:
    credito_total = _partner_adjustment_credit_pool(ajustes_delta)
    credito_disponible = credito_total
    if credito_disponible <= Decimal("0"):
        return {
            "credito_total": Decimal("0"),
            "credito_aplicado": Decimal("0"),
            "credito_restante": Decimal("0"),
        }

    rows_ordenadas = sorted(
        rows,
        key=lambda row: (
            row["nota"].created_at or datetime.min,
            row["nota"].id,
        ),
    )
    for row in rows_ordenadas:
        if credito_disponible <= Decimal("0"):
            break
        if not row.get("saldo_aplicable"):
            continue
        saldo_pendiente = Decimal(str(row.get("saldo_pendiente") or 0))
        if saldo_pendiente <= Decimal("0"):
            continue
        aplicado = min(saldo_pendiente, credito_disponible)
        if aplicado <= Decimal("0"):
            continue
        row["ajuste_aplicado"] = aplicado
        row["saldo"] = Decimal(str(row.get("saldo") or 0)) - aplicado
        row["saldo_pendiente"] = saldo_pendiente - aplicado
        row["saldo_cubierto_por_ajuste"] = row["saldo_pendiente"] <= Decimal("0")
        row["saldo_parcialmente_cubierto"] = (
            aplicado > Decimal("0")
            and row["saldo_pendiente"] > Decimal("0")
        )
        credito_disponible -= aplicado

    return {
        "credito_total": credito_total,
        "credito_aplicado": credito_total - credito_disponible,
        "credito_restante": credito_disponible,
    }


def _filter_partner_record_rows_by_query(
    rows: list[dict],
    q: str | None,
) -> list[dict]:
    if not q:
        return rows
    term = q.strip().lower()
    if not term:
        return rows
    filtered: list[dict] = []
    for row in rows:
        folio = (row.get("folio") or "").lower()
        nota = row.get("nota")
        if nota and (term in str(nota.id) or (folio and term in folio)):
            filtered.append(row)
    return filtered


def _filter_comisionario_notas(
    notas: list[ComisionarioNota],
    term: str | None,
) -> list[ComisionarioNota]:
    if not term:
        return notas
    term_clean = term.strip()
    if not term_clean:
        return notas
    filtered: list[ComisionarioNota] = []
    for nota in notas:
        if term_clean in str(nota.id):
            filtered.append(nota)
    return filtered


def _build_comisionario_summary(notas: list[ComisionarioNota]) -> dict:
    summary = {
        "total_notas": len(notas),
        "notas_aprobadas": 0,
        "notas_canceladas": 0,
        "total_facturado": Decimal("0"),
        "total_pagado": Decimal("0"),
        "saldo_pendiente": Decimal("0"),
        "saldo_favor": Decimal("0"),
    }
    for nota in notas:
        if nota.estado == ComisionarioNotaEstado.aprobada:
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
        elif nota.estado == ComisionarioNotaEstado.cancelada:
            summary["notas_canceladas"] += 1
    return summary


def _build_comisionario_ledger(
    notas: list[ComisionarioNota],
    pagos: list[ComisionarioPago],
) -> list[dict]:
    events: list[dict] = []
    for nota in notas:
        if nota.estado != ComisionarioNotaEstado.aprobada:
            continue
        total = Decimal(str(nota.total_monto or 0))
        events.append(
            {
                "fecha": nota.created_at,
                "orden": 0,
                "tipo": "Nota de comisión",
                "nota_id": nota.id,
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
                "cargo": Decimal("0"),
                "abono": Decimal(str(pago.monto or 0)),
                "metodo": pago.metodo_pago or "-",
                "cuenta": cuenta_label,
                "comentario": pago.comentario or "",
            }
        )
    events = [e for e in events if e["fecha"] is not None]
    events.sort(key=lambda e: (e["fecha"], e["orden"]))
    saldo = Decimal("0")
    for event in events:
        saldo += event["cargo"] - event["abono"]
        event["saldo"] = saldo
    return events


def _parse_kg_overrides(
    form,
    nota: Nota,
) -> tuple[dict[int, Decimal], dict[int, Decimal], dict[int, str], dict[int, str], str | None]:
    kg_neto_map: dict[int, Decimal] = {}
    kg_desc_map: dict[int, Decimal] = {}
    form_kg_neto_map: dict[int, str] = {}
    form_kg_desc_map: dict[int, str] = {}
    error = None

    for nm in nota.materiales:
        raw_neto = (form.get(f"kg_neto_{nm.id}") or "").strip()
        raw_desc = (form.get(f"kg_desc_{nm.id}") or "").strip()

        if raw_neto != "":
            form_kg_neto_map[nm.id] = raw_neto
            try:
                kg_val = Decimal(str(raw_neto))
            except (InvalidOperation, TypeError):
                error = "Kg neto inválido para un material."
                break
            if kg_val < Decimal("0"):
                error = "El kg neto no puede ser negativo."
                break
            kg_neto_map[nm.id] = kg_val

        if raw_desc != "":
            form_kg_desc_map[nm.id] = raw_desc
            try:
                kg_desc = Decimal(str(raw_desc))
            except (InvalidOperation, TypeError):
                error = "Kg descuento inválido para un material."
                break
            if kg_desc < Decimal("0"):
                error = "El kg descuento no puede ser negativo."
                break
            kg_desc_map[nm.id] = kg_desc

    return kg_neto_map, kg_desc_map, form_kg_neto_map, form_kg_desc_map, error


def _parse_real_kg_overrides(
    form,
    nota: Nota,
) -> tuple[dict[int, Decimal], dict[int, str], str | None]:
    kg_real_map: dict[int, Decimal] = {}
    form_kg_real_map: dict[int, str] = {}
    error = None

    for nm in nota.materiales:
        raw_real = (form.get(f"kg_real_{nm.id}") or "").strip()
        if raw_real == "":
            continue
        form_kg_real_map[nm.id] = raw_real
        try:
            kg_real = Decimal(str(raw_real))
        except (InvalidOperation, TypeError):
            error = "Kg reales invalidos para un material."
            break
        if kg_real < Decimal("0"):
            error = "Los kilos reales no pueden ser negativos."
            break
        kg_real_map[nm.id] = kg_real

    return kg_real_map, form_kg_real_map, error

def _parse_owner_key(owner_key: str | None) -> tuple[str | None, int | None]:
    if not owner_key:
        return None, None
    try:
        owner_type, raw_id = owner_key.split(":", 1)
        owner_id = int(raw_id)
    except (ValueError, AttributeError):
        return None, None
    if owner_type not in ("sucursal", "cliente", "proveedor", "comisionario"):
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
    if cuenta.comisionario_id:
        return f"comisionario:{cuenta.comisionario_id}"
    return ""


def _build_cuenta_owner_label(
    cuenta: Cuenta,
    sucursales_map: dict[int, str],
    clientes_map: dict[int, str],
    proveedores_map: dict[int, str],
    comisionarios_map: dict[int, str],
) -> str:
    if cuenta.sucursal_id:
        return f"Sucursal: {sucursales_map.get(cuenta.sucursal_id, cuenta.sucursal_id)}"
    if cuenta.cliente_id:
        return f"Cliente: {clientes_map.get(cuenta.cliente_id, cuenta.cliente_id)}"
    if cuenta.proveedor_id:
        return f"Proveedor: {proveedores_map.get(cuenta.proveedor_id, cuenta.proveedor_id)}"
    if cuenta.comisionario_id:
        return f"Comisionario: {comisionarios_map.get(cuenta.comisionario_id, cuenta.comisionario_id)}"
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
    sucursales = _active_sucursales(db)
    clientes = db.query(Cliente).order_by(Cliente.nombre_completo).all()
    proveedores = db.query(Proveedor).order_by(Proveedor.nombre_completo).all()
    comisionarios = _get_accessible_comisionarios(db, current_user)
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
            "comisionarios": comisionarios,
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
    partner_kind, partner_id = _nota_partner_key(nota)
    if partner_kind == "proveedor" and partner_id:
        cuentas_partner = (
            db.query(Cuenta)
            .filter(
                Cuenta.activo.is_(True),
                Cuenta.proveedor_id == partner_id,
            )
            .order_by(Cuenta.nombre)
            .all()
        )
    elif partner_kind == "cliente" and partner_id:
        cuentas_partner = (
            db.query(Cuenta)
            .filter(
                Cuenta.activo.is_(True),
                Cuenta.cliente_id == partner_id,
            )
            .order_by(Cuenta.nombre)
            .all()
        )
    return cuentas_sucursal, cuentas_partner


def _get_scrap360_cuentas_for_nota(db: Session, nota: Nota) -> list[CuentaScrap360]:
    if not nota.sucursal_id:
        return []
    cuentas = (
        db.query(CuentaScrap360)
        .join(CuentaScrap360.sucursales)
        .filter(
            Sucursal.id == nota.sucursal_id,
            CuentaScrap360.activo.is_(True),
        )
        .order_by(CuentaScrap360.nombre)
        .all()
    )
    return cuentas


def _apply_scrap360_adjustment(
    db: Session,
    *,
    cuenta: CuentaScrap360,
    monto: Decimal,
    comentario: str | None,
    usuario_id: int | None,
    nota_id: int | None = None,
    pago_id: int | None = None,
) -> CuentaScrap360Movimiento:
    monto_val = Decimal(str(monto or 0))
    saldo_actual = Decimal(str(cuenta.saldo_actual or 0))
    nuevo_saldo = saldo_actual + monto_val
    cuenta.saldo_actual = nuevo_saldo
    cuenta.updated_at = datetime.utcnow()
    mov = CuentaScrap360Movimiento(
        cuenta_id=cuenta.id,
        nota_id=nota_id,
        nota_pago_id=pago_id,
        usuario_id=usuario_id,
        tipo="ajuste",
        monto=monto_val,
        saldo_resultante=nuevo_saldo,
        comentario=comentario or None,
    )
    db.add(cuenta)
    db.add(mov)
    return mov


def _scrap360_concept_label(concepto: str | None) -> str:
    mapping = {key: label for key, label in _SCRAP360_AJUSTE_CONCEPTOS}
    return mapping.get((concepto or "").strip().lower(), "Ajuste manual")


def _scrap360_movement_breakdown(mov: CuentaScrap360Movimiento) -> tuple[Decimal, Decimal, str]:
    monto = Decimal(str(mov.monto or 0))
    tipo = (mov.tipo or "").strip().lower()
    if tipo == "ingreso":
        return abs(monto), Decimal("0"), "Entrada"
    if tipo == "egreso":
        return Decimal("0"), abs(monto), "Salida"
    if monto >= 0:
        return monto, Decimal("0"), "Ajuste a favor"
    return Decimal("0"), abs(monto), "Ajuste en contra"


def _build_cuenta_scrap360_detail_context(
    db: Session,
    *,
    request: Request,
    current_user: dict,
    cuenta: CuentaScrap360,
    error: str | None = None,
    ajuste_ok: bool = False,
    form_state: dict | None = None,
) -> dict:
    movimientos = (
        db.query(CuentaScrap360Movimiento)
        .filter(CuentaScrap360Movimiento.cuenta_id == cuenta.id)
        .order_by(CuentaScrap360Movimiento.created_at.desc(), CuentaScrap360Movimiento.id.desc())
        .limit(200)
        .all()
    )
    movimientos_full = (
        db.query(CuentaScrap360Movimiento)
        .filter(CuentaScrap360Movimiento.cuenta_id == cuenta.id)
        .order_by(CuentaScrap360Movimiento.created_at.asc(), CuentaScrap360Movimiento.id.asc())
        .all()
    )
    total_entradas = Decimal("0")
    total_salidas = Decimal("0")
    for mov in movimientos_full:
        entrada, salida, _ = _scrap360_movement_breakdown(mov)
        total_entradas += entrada
        total_salidas += salida

    suc_labels = ", ".join([s.nombre for s in cuenta.sucursales]) if cuenta.sucursales else "-"
    latest_comment = movimientos[0].comentario if movimientos else None
    form_state = form_state or {}
    return {
        "request": request,
        "env": settings.ENV,
        "user": current_user,
        "cuenta": cuenta,
        "sucursales_label": suc_labels,
        "movimientos": movimientos,
        "movimientos_total": len(movimientos_full),
        "total_entradas": total_entradas,
        "total_salidas": total_salidas,
        "ultimo_movimiento_comment": latest_comment,
        "ajuste_ok": ajuste_ok,
        "error": error,
        "ajuste_direcciones": _SCRAP360_AJUSTE_DIRECCIONES,
        "ajuste_conceptos": _SCRAP360_AJUSTE_CONCEPTOS,
        "form_ajuste_monto": form_state.get("form_ajuste_monto", ""),
        "form_ajuste_direccion": form_state.get("form_ajuste_direccion", "entrada"),
        "form_ajuste_concepto": form_state.get("form_ajuste_concepto", "deposito"),
        "form_ajuste_comentario": form_state.get("form_ajuste_comentario", ""),
    }

def _aggregate_partner_record_summary(
    notas: list[Nota],
    partner_type: str | None = None,
    ajustes_delta: Decimal | None = None,
    note_adjustment_totals: dict[int, Decimal] | None = None,
) -> dict:
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
    note_adjustment_totals = note_adjustment_totals or {}
    note_adjustments_signed_total = Decimal("0")
    for nota in notas:
        if nota.estado == NotaEstado.aprobada:
            summary["notas_aprobadas"] += 1
            total, pagado = _signed_partner_amounts(nota, partner_type)
            summary["total_facturado"] += total
            summary["total_pagado"] += pagado
            note_delta_signed = _note_balance_adjustment_signed(
                nota,
                note_delta=note_adjustment_totals.get(nota.id, Decimal("0")),
                partner_type=partner_type,
            )
            note_adjustments_signed_total += note_delta_signed
            saldo = total - pagado + note_delta_signed
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
    delta = ajustes_delta if ajustes_delta is not None else Decimal("0")
    summary["ajustes_delta"] = delta
    summary["ajustes_nota_delta"] = note_adjustments_signed_total
    if delta > Decimal("0"):
        summary["saldo_pendiente"] += delta
    elif delta < Decimal("0"):
        summary["saldo_favor"] += -delta
    return summary


def _compute_partner_adjustment_delta(
    *,
    partner_type: str,
    direction: str,
    monto: Decimal,
) -> Decimal:
    if direction not in ("favor", "contra"):
        raise ValueError("Tipo de ajuste invalido.")
    if monto <= Decimal("0"):
        raise ValueError("El monto del ajuste debe ser mayor a 0.")
    if partner_type == "proveedor":
        return monto if direction == "favor" else -monto
    return -monto if direction == "favor" else monto

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


def _get_accessible_comisionarios(
    db: Session,
    current_user: dict,
    *,
    activos_solamente: bool = False,
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    query = db.query(Comisionario)
    if allowed_suc_ids is not None:
        query = query.filter(Comisionario.sucursal_id.in_(allowed_suc_ids))
    if activos_solamente:
        query = query.filter(Comisionario.activo.is_(True))
    return query.order_by(Comisionario.nombre_completo).all()


def _apply_sucursal_filter(query, allowed_ids: list[int] | None, sucursal_id: int | None, field):
    if allowed_ids is not None:
        if sucursal_id:
            query = query.filter(field == sucursal_id)
        else:
            query = query.filter(field.in_(allowed_ids))
    elif sucursal_id:
        query = query.filter(field == sucursal_id)
    return query


def _build_inventario_movimientos_query(
    db: Session,
    *,
    allowed_suc_ids: list[int] | None,
    sucursal_id: int | None,
    material_id: int | None,
    tipo: str | None,
    created_from: datetime | None = None,
    created_to: datetime | None = None,
):
    query = (
        db.query(InventarioMovimiento)
        .join(Inventario, Inventario.id == InventarioMovimiento.inventario_id)
    )
    if allowed_suc_ids is not None:
        if sucursal_id:
            query = query.filter(Inventario.sucursal_id == sucursal_id)
        else:
            query = query.filter(Inventario.sucursal_id.in_(allowed_suc_ids))
    elif sucursal_id:
        query = query.filter(Inventario.sucursal_id == sucursal_id)
    if material_id:
        query = query.filter(Inventario.material_id == material_id)
    if tipo:
        query = query.filter(InventarioMovimiento.tipo == tipo)
    if created_from:
        query = query.filter(InventarioMovimiento.created_at >= created_from)
    if created_to:
        query = query.filter(InventarioMovimiento.created_at < created_to)
    return query


def _parse_inventory_date_param(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _inventory_local_date_range_to_utc(
    date_from: date | None,
    date_to: date | None,
) -> tuple[datetime | None, datetime | None]:
    if not date_from and not date_to:
        return None, None
    tz = get_app_timezone()
    start_utc = None
    end_utc = None
    if date_from:
        start_utc = (
            datetime.combine(date_from, time.min, tzinfo=tz)
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )
    if date_to:
        end_utc = (
            datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=tz)
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )
    return start_utc, end_utc


def _get_inventory_valuation_default_prices(
    db: Session,
    material_ids: list[int],
) -> dict[int, Decimal]:
    if not material_ids:
        return {}
    precios = (
        db.query(TablaPrecio)
        .filter(
            TablaPrecio.material_id.in_(material_ids),
            TablaPrecio.tipo_operacion == TipoOperacion.venta,
            TablaPrecio.tipo_cliente == TipoCliente.regular,
            TablaPrecio.activo.is_(True),
        )
        .order_by(TablaPrecio.material_id.asc(), TablaPrecio.version.desc())
        .all()
    )
    result: dict[int, Decimal] = {}
    for precio in precios:
        if precio.material_id not in result:
            result[precio.material_id] = Decimal(str(precio.precio_por_unidad or 0))
    return result


def _get_inventory_valuation_average_purchase_prices(
    db: Session,
    *,
    sucursal_id: int,
    material_ids: list[int],
) -> dict[int, Decimal]:
    if not material_ids:
        return {}
    rows = (
        db.query(
            NotaMaterial.material_id,
            func.coalesce(func.sum(NotaMaterial.kg_neto), 0),
            func.coalesce(func.sum(NotaMaterial.subtotal), 0),
        )
        .join(InventarioMovimiento, InventarioMovimiento.nota_material_id == NotaMaterial.id)
        .join(Inventario, Inventario.id == InventarioMovimiento.inventario_id)
        .join(Nota, Nota.id == NotaMaterial.nota_id)
        .filter(
            Inventario.sucursal_id == sucursal_id,
            InventarioMovimiento.tipo == "compra",
            Nota.estado == NotaEstado.aprobada,
            Nota.tipo_operacion == TipoOperacion.compra,
            NotaMaterial.material_id.in_(material_ids),
        )
        .group_by(NotaMaterial.material_id)
        .all()
    )
    result: dict[int, Decimal] = {}
    for material_id, total_kg, total_subtotal in rows:
        kg = Decimal(str(total_kg or 0))
        subtotal = Decimal(str(total_subtotal or 0))
        if kg > Decimal("0"):
            result[int(material_id)] = subtotal / kg
    return result


def _normalize_inventory_valuation_mode(mode_raw: str | None) -> str:
    return "promedio" if (mode_raw or "").strip().lower() == "promedio" else "manual"


def _build_inventario_valor_rows(
    db: Session,
    *,
    sucursal_id: int,
    valuation_mode: str = "manual",
) -> tuple[list[dict], Decimal, Decimal, int, int, int, int]:
    valuation_mode = _normalize_inventory_valuation_mode(valuation_mode)
    materiales = db.query(Material).order_by(Material.nombre.asc()).all()
    material_ids = [m.id for m in materiales]
    inventarios = (
        db.query(Inventario)
        .filter(Inventario.sucursal_id == sucursal_id)
        .all()
    )
    inventario_map = {inv.material_id: inv for inv in inventarios}
    valuation_map = {
        row.material_id: row
        for row in db.query(InventarioValorPrecio)
        .filter(InventarioValorPrecio.sucursal_id == sucursal_id)
        .all()
    }
    default_price_map = _get_inventory_valuation_default_prices(db, material_ids)
    average_purchase_map = _get_inventory_valuation_average_purchase_prices(
        db,
        sucursal_id=sucursal_id,
        material_ids=material_ids,
    )

    rows: list[dict] = []
    total_kg = Decimal("0")
    total_valor = Decimal("0")
    materiales_con_stock = 0
    materiales_con_precio = 0
    manual_count = 0
    automatic_count = 0
    for material in materiales:
        inventario = inventario_map.get(material.id)
        stock = Decimal(str(inventario.stock_actual if inventario else 0))
        valuation = valuation_map.get(material.id)
        source_key = "none"
        source_help = "Sin precio manual, sin precio de venta activo y sin historial suficiente de compra en la sucursal."
        precio_referencia = Decimal("0")
        if valuation_mode == "promedio":
            if material.id in average_purchase_map:
                source_key = "purchase_avg"
                source_help = "Modo promedio activo: se usa el promedio historico de compra en esta sucursal."
                precio_referencia = average_purchase_map.get(material.id, Decimal("0"))
                automatic_count += 1
            elif valuation is not None:
                source_key = "manual"
                source_help = "No hay promedio historico suficiente; se usa tu configuracion manual."
                precio_referencia = Decimal(str(valuation.precio_referencia))
                manual_count += 1
            elif material.id in default_price_map:
                source_key = "sale"
                source_help = "No hay promedio historico ni precio manual; se usa el precio de venta regular activo."
                precio_referencia = default_price_map.get(material.id, Decimal("0"))
                automatic_count += 1
        else:
            if valuation is not None:
                source_key = "manual"
                source_help = "Modo manual activo: se usa el precio capturado manualmente para esta sucursal."
                precio_referencia = Decimal(str(valuation.precio_referencia))
                manual_count += 1
            elif material.id in default_price_map:
                source_key = "sale"
                source_help = "No hay precio manual; se usa el precio de venta regular activo."
                precio_referencia = default_price_map.get(material.id, Decimal("0"))
                automatic_count += 1
            elif material.id in average_purchase_map:
                source_key = "purchase_avg"
                source_help = "No hay precio manual ni precio de venta activo; se usa el promedio historico de compra."
                precio_referencia = average_purchase_map.get(material.id, Decimal("0"))
                automatic_count += 1
        valor_total = stock * precio_referencia
        if stock > 0:
            materiales_con_stock += 1
            if precio_referencia > Decimal("0"):
                materiales_con_precio += 1
        total_kg += stock
        total_valor += valor_total
        rows.append(
            {
                "material_id": material.id,
                "material": material,
                "inventario": inventario,
                "stock_actual": stock,
                "precio_referencia": precio_referencia,
                "precio_guardado": valuation,
                "usa_default": valuation is None and material.id in default_price_map,
                "usa_promedio_compra": valuation is None and material.id not in default_price_map and material.id in average_purchase_map,
                "source_key": source_key,
                "source_help": source_help,
                "valor_total": valor_total,
                "updated_at": valuation.updated_at if valuation else None,
            }
        )
    sin_precio_count = max(materiales_con_stock - materiales_con_precio, 0)
    return rows, total_kg, total_valor, materiales_con_stock, manual_count, automatic_count, sin_precio_count


def _build_inventario_valor_summary(
    db: Session,
    *,
    sucursales: list[Sucursal],
    valuation_mode: str = "manual",
) -> list[dict]:
    valuation_mode = _normalize_inventory_valuation_mode(valuation_mode)
    summary_rows: list[dict] = []
    for sucursal in sucursales:
        rows, total_kg, total_valor, materiales_con_stock, manual_count, automatic_count, sin_precio_count = _build_inventario_valor_rows(
            db,
            sucursal_id=sucursal.id,
            valuation_mode=valuation_mode,
        )
        summary_rows.append(
            {
                "sucursal": sucursal,
                "total_kg": total_kg,
                "total_valor": total_valor,
                "materiales_con_stock": materiales_con_stock,
                "materiales_total": len(rows),
                "manual_count": manual_count,
                "automatic_count": automatic_count,
                "sin_precio_count": sin_precio_count,
            }
        )
    return summary_rows


def _build_capital_real_context(
    db: Session,
    *,
    allowed_suc_ids: list[int] | None = None,
    valuation_mode: str = "manual",
) -> dict:
    valuation_mode = _normalize_inventory_valuation_mode(valuation_mode)
    sucursales_query = db.query(Sucursal).order_by(Sucursal.nombre.asc())
    sucursales = _filter_sucursales_for_admin(sucursales_query.all(), allowed_suc_ids)
    sucursal_names = {s.nombre for s in sucursales if s.nombre}

    proveedores = db.query(Proveedor).order_by(Proveedor.nombre_completo.asc()).all()
    clientes = db.query(Cliente).order_by(Cliente.nombre_completo.asc()).all()
    proveedores_map = {p.id: p.nombre_completo for p in proveedores}
    clientes_map = {c.id: c.nombre_completo for c in clientes}

    notas_query = db.query(Nota).filter(Nota.estado == NotaEstado.aprobada)
    notas_query = _apply_sucursal_filter(notas_query, allowed_suc_ids, None, Nota.sucursal_id)
    notas = notas_query.all()
    note_adjustment_totals = _get_note_balance_adjustment_totals_map(
        db,
        [nota.id for nota in notas if nota.id],
    )

    def _is_internal_partner(nombre: str | None) -> bool:
        if not nombre or not nombre.startswith("Sucursal "):
            return False
        suc_name = nombre.replace("Sucursal ", "", 1).strip()
        return suc_name in sucursal_names

    # Punto 8 (fase 2): los saldos del capital se agrupan por socio — y el par
    # cliente↔proveedor vinculado como un solo grupo — y se clasifican con la
    # regla única del sistema (el par vive en el bucket de clientes con su
    # signo). Antes cada nota y cada ajuste se clasificaban sueltos por tipo,
    # así que el par quedaba repartido entre ambos buckets.
    ajustes_query = db.query(AjusteSaldoPartner)
    if allowed_suc_ids is not None:
        ajustes_query = ajustes_query.filter(AjusteSaldoPartner.sucursal_id.in_(allowed_suc_ids))
    ajustes = ajustes_query.all()

    capital_keys: set[tuple[str, int]] = set()
    for nota in notas:
        partner_kind, partner_id = _nota_partner_key(nota)
        if partner_kind and partner_id:
            capital_keys.add((partner_kind, partner_id))
    for ajuste in ajustes:
        if ajuste.partner_type and ajuste.partner_id:
            capital_keys.add((ajuste.partner_type, ajuste.partner_id))
    link_by_prov, link_by_cli = note_service._linked_partner_maps(db, capital_keys)

    def _capital_group(partner_type: str, partner_id: int) -> tuple:
        if partner_type == "proveedor" and partner_id in link_by_prov:
            return ("par", partner_id, link_by_prov[partner_id])
        if partner_type == "cliente" and partner_id in link_by_cli:
            return ("par", link_by_cli[partner_id], partner_id)
        return (partner_type, partner_id)

    group_balances: dict[tuple, Decimal] = defaultdict(lambda: Decimal("0"))
    group_meta: dict[tuple, dict[str, bool]] = {}

    def _capital_meta(key: tuple, partner_type: str) -> None:
        meta = group_meta.setdefault(key, {"has_proveedor": False, "has_cliente": False})
        if key[0] == "par":
            meta["has_proveedor"] = True
            meta["has_cliente"] = True
        elif partner_type == "proveedor":
            meta["has_proveedor"] = True
        else:
            meta["has_cliente"] = True

    for nota in notas:
        note_delta = Decimal(str(note_adjustment_totals.get(nota.id, Decimal("0")) or 0))
        total = Decimal(str(nota.total_monto or 0))
        pagado = Decimal(str(nota.monto_pagado or 0))
        saldo = total - pagado + note_delta

        partner_kind, partner_id = _nota_partner_key(nota)
        if not partner_kind or not partner_id:
            continue
        if partner_kind == "cliente":
            nombre = clientes_map.get(partner_id)
        else:
            nombre = proveedores_map.get(partner_id)
        if _is_internal_partner(nombre):
            continue

        # Vista proveedor: compras suman (por pagar), ventas restan.
        sign = Decimal("1") if nota.tipo_operacion == TipoOperacion.compra else Decimal("-1")
        key = _capital_group(partner_kind, partner_id)
        group_balances[key] += sign * saldo
        _capital_meta(key, partner_kind)

    for ajuste in ajustes:
        delta = Decimal(str(ajuste.monto or 0))
        if ajuste.partner_type == "cliente":
            nombre = clientes_map.get(ajuste.partner_id)
            if _is_internal_partner(nombre):
                continue
            key = _capital_group("cliente", ajuste.partner_id)
            group_balances[key] -= delta
            _capital_meta(key, "cliente")
        elif ajuste.partner_type == "proveedor":
            nombre = proveedores_map.get(ajuste.partner_id)
            if _is_internal_partner(nombre):
                continue
            key = _capital_group("proveedor", ajuste.partner_id)
            group_balances[key] += delta
            _capital_meta(key, "proveedor")

    _capital_totals = _classify_partner_group_balances(
        group_balances, group_metadata=group_meta
    )
    total_por_cobrar_clientes = _capital_totals["total_por_cobrar_clientes"]
    saldo_favor_clientes = _capital_totals["saldo_favor_clientes"]
    total_por_pagar_proveedores = _capital_totals["total_por_pagar_proveedores"]
    saldo_favor_empresa = _capital_totals["saldo_favor_empresa"]

    comisionarios_query = db.query(ComisionarioNota).filter(
        ComisionarioNota.estado == ComisionarioNotaEstado.aprobada
    )
    if allowed_suc_ids is not None:
        comisionarios_query = comisionarios_query.filter(ComisionarioNota.sucursal_id.in_(allowed_suc_ids))
    comisionarios_pendientes = Decimal("0")
    comisionarios_total = Decimal("0")
    for nota in comisionarios_query.all():
        total = Decimal(str(nota.total_monto or 0))
        pagado = Decimal(str(nota.monto_pagado or 0))
        pendiente = total - pagado
        comisionarios_total += total
        if pendiente > Decimal("0"):
            comisionarios_pendientes += pendiente

    cuentas_query = db.query(CuentaScrap360).filter(CuentaScrap360.activo.is_(True))
    if allowed_suc_ids:
        cuentas_query = cuentas_query.join(CuentaScrap360.sucursales).filter(Sucursal.id.in_(allowed_suc_ids))
    cuentas_scrap360 = cuentas_query.distinct().order_by(CuentaScrap360.nombre.asc()).all()

    saldo_bancos_chequeras = Decimal("0")
    saldo_efectivo = Decimal("0")
    cuentas_rows: list[dict] = []
    for cuenta in cuentas_scrap360:
        saldo_actual = Decimal(str(cuenta.saldo_actual or 0))
        if cuenta.tipo == "efectivo":
            saldo_efectivo += saldo_actual
        else:
            saldo_bancos_chequeras += saldo_actual
        cuentas_rows.append(
            {
                "cuenta": cuenta,
                "saldo_actual": saldo_actual,
                "sucursales_label": ", ".join(s.nombre for s in cuenta.sucursales) if cuenta.sucursales else "-",
            }
        )

    inventario_summary = _build_inventario_valor_summary(
        db,
        sucursales=sucursales,
        valuation_mode=valuation_mode,
    )
    valor_inventario = sum((Decimal(str(row["total_valor"] or 0)) for row in inventario_summary), Decimal("0"))
    inventario_materiales_con_stock = sum((int(row.get("materiales_con_stock") or 0) for row in inventario_summary), 0)
    inventario_manual_count = sum((int(row.get("manual_count") or 0) for row in inventario_summary), 0)
    inventario_automatic_count = sum((int(row.get("automatic_count") or 0) for row in inventario_summary), 0)
    inventario_sin_precio_count = sum((int(row.get("sin_precio_count") or 0) for row in inventario_summary), 0)

    activos_totales = (
        total_por_cobrar_clientes
        - saldo_favor_clientes
        + saldo_bancos_chequeras
        + saldo_efectivo
        + valor_inventario
    )
    pasivos_totales = (
        total_por_pagar_proveedores
        - saldo_favor_empresa
        + comisionarios_pendientes
    )
    capital_real = activos_totales - pasivos_totales

    asset_rows = [
        {
            "label": "Saldos de clientes",
            "amount": total_por_cobrar_clientes - saldo_favor_clientes,
            "detail": "Por cobrar menos saldos a favor de clientes.",
        },
        {
            "label": "Saldos de chequeras / transferencias",
            "amount": saldo_bancos_chequeras,
            "detail": "Cuentas Scrap360 tipo transferencia y cheques.",
        },
        {
            "label": "Saldo en efectivo",
            "amount": saldo_efectivo,
            "detail": "Cuentas Scrap360 tipo efectivo.",
        },
        {
            "label": "Valor inventario",
            "amount": valor_inventario,
            "detail": (
                "Valuación por material usando configuración manual por sucursal."
                if valuation_mode == "manual"
                else "Valuación por material usando promedio histórico de compra por sucursal."
            ),
        },
    ]
    liability_rows = [
        {
            "label": "Saldos de proveedores",
            "amount": total_por_pagar_proveedores - saldo_favor_empresa,
            "detail": "Por pagar menos saldos a favor de la empresa.",
        },
        {
            "label": "Saldos de comisionistas",
            "amount": comisionarios_pendientes,
            "detail": "Comisiones aprobadas pendientes por pagar.",
        },
    ]

    return {
        "scope_label": "Capital global autorizado",
        "activos_totales": activos_totales,
        "pasivos_totales": pasivos_totales,
        "capital_real": capital_real,
        "clientes_neto": total_por_cobrar_clientes - saldo_favor_clientes,
        "proveedores_neto": total_por_pagar_proveedores - saldo_favor_empresa,
        "saldo_bancos_chequeras": saldo_bancos_chequeras,
        "saldo_efectivo": saldo_efectivo,
        "valor_inventario": valor_inventario,
        "comisionarios_pendientes": comisionarios_pendientes,
        "comisionarios_total": comisionarios_total,
        "asset_rows": asset_rows,
        "liability_rows": liability_rows,
        "cuentas_rows": cuentas_rows,
        "inventario_summary": inventario_summary,
        "inventario_materiales_con_stock": inventario_materiales_con_stock,
        "inventario_manual_count": inventario_manual_count,
        "inventario_automatic_count": inventario_automatic_count,
        "inventario_sin_precio_count": inventario_sin_precio_count,
        "valuation_mode": valuation_mode,
        "valuation_mode_label": "Promedio de compra" if valuation_mode == "promedio" else "Configuración manual",
        "valuation_mode_help": (
            "Prioriza el promedio historico de compra por sucursal; si no existe, usa precio manual y luego precio de venta."
            if valuation_mode == "promedio"
            else "Prioriza los precios manuales capturados por sucursal; si faltan, usa precio de venta y luego promedio de compra."
        ),
    }


def _build_partner_record_context(
    request: Request,
    db: Session,
    current_user: dict,
    *,
    partner_type: str,
    partner: Cliente | Proveedor,
    q: str | None,
    ajuste_ok: bool = False,
    ajuste_error: str | None = None,
    form_state: dict | None = None,
    link_ok: bool = False,
    link_msg: str | None = None,
    link_warn: str | None = None,
    link_error: str | None = None,
    attendance_from: date | None = None,
    attendance_to: date | None = None,
    attendance_error: str | None = None,
) -> dict:
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    form_state = form_state or {}
    partner_is_internal = _is_internal_partner_name(db, partner.nombre_completo)
    linked_partner = None
    linked_partner_label = None
    provider_direct_sales_enabled = False
    provider_direct_sales_count = 0
    unified_summary = None
    unified_ledger_rows = None
    unified_ledger_final = Decimal("0")
    unified_ledger_label = None
    unified_ledger_help = None
    record_scope_label = None
    payments_scope_label = None
    if partner_type == "proveedor":
        tipo_operacion = TipoOperacion.compra
        partner_label = "Proveedor"
        partner_base = "proveedores"
        tipo_operacion_label = "Compras"
        total_facturado_label = "Total compras aprobadas"
        total_pagado_label = "Total pagado"
        saldo_pendiente_label = "Saldo pendiente (por pagar al proveedor)"
        saldo_favor_label = "Saldo a favor de la empresa"
        ledger_saldo_label = "Saldo acumulado (por pagar al proveedor)"
        ledger_saldo_help = "Saldo positivo indica pendiente por pagar. Saldo negativo indica saldo a favor de la empresa."
        ajuste_favor_label = "Saldo a favor del proveedor (la empresa debe pagar)"
        ajuste_contra_label = "Saldo en contra del proveedor (el proveedor debe pagar)"
    else:
        tipo_operacion = TipoOperacion.venta
        partner_label = "Cliente"
        partner_base = "clientes"
        tipo_operacion_label = "Ventas"
        total_facturado_label = "Total ventas aprobadas (neto)"
        total_pagado_label = "Total cobrado/pagado (neto)"
        saldo_pendiente_label = "Saldo neto (por cobrar al cliente)"
        saldo_favor_label = "Saldo a favor del cliente"
        ledger_saldo_label = "Saldo acumulado (por cobrar/pagar al cliente)"
        ledger_saldo_help = "Saldo positivo indica pendiente por cobrar. Saldo negativo indica saldo a favor del cliente."
        ajuste_favor_label = "Saldo a favor del cliente (la empresa debe pagar)"
        ajuste_contra_label = "Saldo en contra del cliente (el cliente debe pagar)"
    record_scope_label = tipo_operacion_label
    payments_scope_label = tipo_operacion_label
    compras: list[Nota] = []
    ventas: list[Nota] = []
    prov_id: int | None = None
    cli_id: int | None = None

    if not partner_is_internal:
        if partner_type == "proveedor":
            provider_bundle = _collect_proveedor_sales_bundle(
                db,
                proveedor=partner,
                allowed_suc_ids=allowed_suc_ids,
            )
            linked_partner = provider_bundle["linked_cliente"]
            linked_partner_label = "Cliente"
            provider_direct_sales_enabled = bool(provider_bundle["direct_enabled"])
            provider_direct_sales_count = len(provider_bundle["ventas_directas"])
        else:
            linked_partner = _get_formally_linked_proveedor(db, partner)
            if linked_partner and _is_internal_partner_name(db, linked_partner.nombre_completo):
                linked_partner = None
            linked_partner_label = "Proveedor"

    if partner_type == "proveedor":
        compras_query = db.query(Nota).filter(
            Nota.proveedor_id == partner.id,
            Nota.tipo_operacion == TipoOperacion.compra,
        )
        compras_query = _apply_sucursal_filter(compras_query, allowed_suc_ids, None, Nota.sucursal_id)
        compras = compras_query.order_by(Nota.created_at.desc()).all()
        ventas = provider_bundle["ventas"] if not partner_is_internal else []
        prov_id = partner.id
        cli_id = linked_partner.id if linked_partner else None
    elif linked_partner:
        compras_query = db.query(Nota).filter(
            Nota.proveedor_id == linked_partner.id,
            Nota.tipo_operacion == TipoOperacion.compra,
        )
        ventas_query = db.query(Nota).filter(
            Nota.cliente_id == partner.id,
            Nota.tipo_operacion == TipoOperacion.venta,
        )
        if allowed_suc_ids:
            compras_query = compras_query.filter(Nota.sucursal_id.in_(allowed_suc_ids))
            ventas_query = ventas_query.filter(Nota.sucursal_id.in_(allowed_suc_ids))
        compras = compras_query.order_by(Nota.created_at.desc()).all()
        ventas = ventas_query.order_by(Nota.created_at.desc()).all()
        prov_id = linked_partner.id
        cli_id = partner.id

    if (partner_type == "proveedor" and (ventas or provider_direct_sales_enabled or linked_partner)) or (
        partner_type == "cliente" and linked_partner
    ):
        ajustes_proveedor = _get_partner_adjustments_total(
            db,
            partner_type="proveedor",
            partner_id=prov_id,
            allowed_suc_ids=allowed_suc_ids,
        )
        ajustes_cliente = Decimal("0")
        if cli_id:
            ajustes_cliente = _get_partner_adjustments_total(
                db,
                partner_type="cliente",
                partner_id=cli_id,
                allowed_suc_ids=allowed_suc_ids,
            )
        unified_summary = _aggregate_unified_partner_summary(
            compras=compras,
            ventas=ventas,
            ajustes_proveedor=ajustes_proveedor,
            ajustes_cliente=ajustes_cliente,
        )
        unified_ledger_rows = _build_unified_partner_ledger(
            db,
            proveedor_id=prov_id,
            cliente_id=cli_id,
            allowed_suc_ids=allowed_suc_ids,
        )
        unified_ledger_final = (
            unified_ledger_rows[-1]["saldo"] if unified_ledger_rows else Decimal("0")
        )
        unified_ledger_label = "Saldo unificado (proveedor/cliente)"
        unified_ledger_help = (
            "Saldo positivo indica pendiente por pagar al partner. "
            "Saldo negativo indica pendiente por cobrar del partner."
        )

    notas_query = db.query(Nota).filter(
        (Nota.proveedor_id if partner_type == "proveedor" else Nota.cliente_id) == partner.id,
        Nota.tipo_operacion == tipo_operacion,
    )
    if allowed_suc_ids:
        notas_query = notas_query.filter(Nota.sucursal_id.in_(allowed_suc_ids))
    notas = notas_query.order_by(Nota.created_at.desc()).all()
    ajustes_delta = _get_partner_adjustments_total(
        db,
        partner_type=partner_type,
        partner_id=partner.id,
        allowed_suc_ids=allowed_suc_ids,
    )
    summary = _aggregate_partner_record_summary(
        notas,
        partner_type=partner_type,
        ajustes_delta=ajustes_delta,
    )
    ledger_rows = _build_partner_ledger(
        db,
        partner_type=partner_type,
        partner_id=partner.id,
        allowed_suc_ids=allowed_suc_ids,
    )
    ledger_final = ledger_rows[-1]["saldo"] if ledger_rows else Decimal("0")

    record_notes = notas
    record_partner_type = partner_type
    if unified_summary:
        summary = unified_summary
        ledger_rows = unified_ledger_rows or []
        ledger_final = unified_ledger_final
        ledger_saldo_label = unified_ledger_label or ledger_saldo_label
        ledger_saldo_help = unified_ledger_help or ledger_saldo_help
        record_notes = sorted(
            compras + ventas,
            key=lambda nota: nota.created_at or datetime.min,
            reverse=True,
        )
        record_partner_type = None
        record_scope_label = "Compras y ventas unificadas"
        payments_scope_label = "compras y ventas unificadas"
        note_ids = [nota.id for nota in record_notes]
        if note_ids:
            pagos = (
                db.query(NotaPago)
                .join(Nota, NotaPago.nota_id == Nota.id)
                .filter(Nota.id.in_(note_ids))
                .order_by(NotaPago.created_at.desc())
                .all()
            )
        else:
            pagos = []
    else:
        pagos_query = (
            db.query(NotaPago)
            .join(Nota, NotaPago.nota_id == Nota.id)
            .filter(
                (Nota.proveedor_id if partner_type == "proveedor" else Nota.cliente_id) == partner.id,
                Nota.tipo_operacion == tipo_operacion,
            )
        )
        if allowed_suc_ids:
            pagos_query = pagos_query.filter(Nota.sucursal_id.in_(allowed_suc_ids))
        pagos = pagos_query.order_by(NotaPago.created_at.desc()).all()

    # Punto 10 (fase 2): opción de ver el estado de cuenta con lo más reciente
    # arriba. El saldo acumulado se calcula SIEMPRE en orden cronológico (arriba);
    # aquí solo se invierte la presentación, y ledger_final ya quedó capturado.
    orden_historial_raw = (request.query_params.get("orden_historial") or "").strip().lower()
    orden_historial = "recientes" if orden_historial_raw == "recientes" else "cronologico"
    if orden_historial == "recientes":
        ledger_rows = list(reversed(ledger_rows))
    orden_historial_params = {
        key: value
        for key, value in request.query_params.items()
        if key != "orden_historial" and value
    }
    orden_historial_links = {
        "cronologico": _append_query_params(request.url.path, **orden_historial_params),
        "recientes": _append_query_params(
            request.url.path, **orden_historial_params, orden_historial="recientes"
        ),
    }

    folio_map = _build_folio_map(record_notes)
    note_adjustment_totals = _get_note_balance_adjustment_totals_map(
        db,
        [nota.id for nota in record_notes if nota.id],
    )
    # Punto 7 (fase 2): el neteo también aplica en la vista unificada del par
    # vinculado — antes este mapa se saltaba cuando unified_summary existía y
    # las notas ya neteadas seguían apareciendo como pendientes.
    effective_note_balances: dict[int, dict[str, Decimal | bool]] = {}
    if record_notes:
        effective_note_balances = _build_effective_note_balance_map(
            db,
            record_notes,
            allowed_suc_ids=allowed_suc_ids,
        )
    all_rows = _build_partner_record_rows(
        record_notes,
        folio_map,
        partner_type=record_partner_type,
        note_adjustment_totals=note_adjustment_totals,
        effective_balance_map=effective_note_balances,
    )
    coverage_summary = {
        "credito_total": Decimal("0"),
        "credito_aplicado": Decimal("0"),
        "credito_restante": Decimal("0"),
    }
    if not unified_summary and record_partner_type in {"cliente", "proveedor"}:
        credito_total = _partner_adjustment_credit_pool(ajustes_delta)
        credito_aplicado = sum((Decimal(str(row.get("ajuste_aplicado") or 0)) for row in all_rows), Decimal("0"))
        credito_restante = credito_total - credito_aplicado
        if credito_restante < Decimal("0"):
            credito_restante = Decimal("0")
        coverage_summary = {
            "credito_total": credito_total,
            "credito_aplicado": credito_aplicado,
            "credito_restante": credito_restante,
        }
        summary = _aggregate_partner_record_summary(
            notas,
            partner_type=partner_type,
            ajustes_delta=ajustes_delta,
            note_adjustment_totals=note_adjustment_totals,
        )
    elif unified_summary:
        summary = _aggregate_unified_partner_summary(
            compras=compras,
            ventas=ventas,
            ajustes_proveedor=ajustes_proveedor,
            ajustes_cliente=ajustes_cliente,
            note_adjustment_totals=note_adjustment_totals,
        )
    rows = _filter_partner_record_rows_by_query(all_rows, q)

    pago_inicial_total = Decimal("0")
    for pago in pagos:
        if pago.comentario and pago.comentario.lower().startswith("pago inicial"):
            pago_inicial_total += Decimal(str(pago.monto or 0))

    suc_query = db.query(Sucursal)
    if allowed_suc_ids:
        suc_query = suc_query.filter(Sucursal.id.in_(allowed_suc_ids))
    sucursales = {s.id: s for s in suc_query.all()}
    can_manage_partner = not _is_read_only_admin_user(current_user)
    attendance_rows: list[dict] = []
    attendance_total_historico = 0
    attendance_total_filtrado = 0
    attendance_range_label = "Historial completo"
    if partner_type == "proveedor":
        attendance_query = db.query(Nota).filter(
            Nota.proveedor_id == partner.id,
            Nota.estado != NotaEstado.borrador,
        )
        attendance_query = _apply_sucursal_filter(attendance_query, allowed_suc_ids, None, Nota.sucursal_id)
        attendance_notes = attendance_query.order_by(Nota.created_at.asc(), Nota.id.asc()).all()
        attendance_folio_map = _build_folio_map(attendance_notes) if attendance_notes else {}
        attendance_days: dict[date, dict] = {}
        for nota in attendance_notes:
            local_dt = to_local_datetime(nota.created_at) if nota.created_at else None
            if not local_dt:
                continue
            local_day = local_dt.date()
            day_data = attendance_days.setdefault(
                local_day,
                {
                    "fecha": local_day,
                    "sucursales": set(),
                    "notes_count": 0,
                    "folios": [],
                },
            )
            day_data["notes_count"] += 1
            if nota.sucursal_id and nota.sucursal_id in sucursales:
                day_data["sucursales"].add(sucursales[nota.sucursal_id].nombre)
            elif nota.sucursal and nota.sucursal.nombre:
                day_data["sucursales"].add(nota.sucursal.nombre)
            folio = attendance_folio_map.get(nota.id)
            if folio:
                day_data["folios"].append(folio)
        attendance_total_historico = len(attendance_days)
        if attendance_from or attendance_to:
            range_parts = []
            if attendance_from:
                range_parts.append(f"Desde {format_date_local(attendance_from, '%d/%m/%Y')}")
            if attendance_to:
                range_parts.append(f"Hasta {format_date_local(attendance_to, '%d/%m/%Y')}")
            attendance_range_label = " / ".join(range_parts) if range_parts else attendance_range_label
        filtered_days = []
        for attendance_day, row in attendance_days.items():
            if attendance_from and attendance_day < attendance_from:
                continue
            if attendance_to and attendance_day > attendance_to:
                continue
            filtered_days.append(
                {
                    "fecha": row["fecha"],
                    "fecha_label": format_date_local(row["fecha"], "%d/%m/%Y"),
                    "notes_count": row["notes_count"],
                    "sucursales": sorted(row["sucursales"]),
                    "sucursales_label": ", ".join(sorted(row["sucursales"])) if row["sucursales"] else "-",
                    "folios": row["folios"][:6],
                }
            )
        filtered_days.sort(key=lambda row: row["fecha"], reverse=True)
        attendance_rows = filtered_days
        attendance_total_filtrado = len(filtered_days)

    return {
        "request": request,
        "env": settings.ENV,
        "user": current_user,
        "partner": partner,
        "partner_label": partner_label,
        "partner_base": partner_base,
        "tipo_operacion_label": tipo_operacion_label,
        "record_rows": rows,
        "record_total_count": len(record_notes),
        "record_filtered_count": len(rows),
        "record_scope_label": record_scope_label,
        "payments_scope_label": payments_scope_label,
        "summary": summary,
        "ledger_rows": ledger_rows,
        "ledger_final": ledger_final,
        "ledger_saldo_label": ledger_saldo_label,
        "ledger_saldo_help": ledger_saldo_help,
        "orden_historial": orden_historial,
        "orden_historial_links": orden_historial_links,
        "total_facturado_label": total_facturado_label,
        "total_pagado_label": total_pagado_label,
        "saldo_pendiente_label": saldo_pendiente_label,
        "saldo_favor_label": saldo_favor_label,
        "pagos": pagos,
        "pago_inicial_total": pago_inicial_total,
        "folio_map": folio_map,
        "sucursales": sucursales,
        "q": q or "",
        "ajuste_ok": ajuste_ok,
        "ajuste_error": ajuste_error,
        "ajuste_favor_label": ajuste_favor_label,
        "ajuste_contra_label": ajuste_contra_label,
        "form_ajuste_direccion": form_state.get("ajuste_direccion", ""),
        "form_ajuste_monto": form_state.get("ajuste_monto", ""),
        "form_ajuste_comentario": form_state.get("ajuste_comentario", ""),
        "link_ok": link_ok,
        "link_msg": link_msg,
        "link_warn": link_warn,
        "link_error": link_error,
        "linked_partner": linked_partner,
        "linked_partner_label": linked_partner_label,
        "unified_enabled": bool(unified_summary),
        "partner_is_internal": partner_is_internal,
        "provider_direct_sales_enabled": provider_direct_sales_enabled,
        "provider_direct_sales_count": provider_direct_sales_count,
        "record_credito_ajuste_total": coverage_summary["credito_total"],
        "record_credito_ajuste_aplicado": coverage_summary["credito_aplicado"],
        "record_credito_ajuste_restante": coverage_summary["credito_restante"],
        "can_manage_partner": can_manage_partner,
        "can_view_partner_accounts": can_manage_partner,
        "attendance_enabled": partner_type == "proveedor",
        "attendance_rows": attendance_rows,
        "attendance_total_historico": attendance_total_historico,
        "attendance_total_filtrado": attendance_total_filtrado,
        "attendance_from": attendance_from.isoformat() if attendance_from else "",
        "attendance_to": attendance_to.isoformat() if attendance_to else "",
        "attendance_error": attendance_error,
        "attendance_range_label": attendance_range_label,
    }


def _partner_record_note_operation_label(nota: Nota, unified_enabled: bool) -> str:
    if nota.tipo_operacion == TipoOperacion.compra:
        return "Compra"
    if unified_enabled and nota.proveedor_id:
        return "Venta al proveedor"
    return "Venta"


def _partner_record_note_state_label(nota: Nota, *, is_paid: bool = False) -> str:
    if is_paid:
        return "Pagada"
    if nota.estado == NotaEstado.borrador:
        return "Borrador"
    if nota.estado == NotaEstado.en_revision:
        return "En revision"
    if nota.estado == NotaEstado.aprobada:
        return "Aprobada"
    if nota.estado == NotaEstado.cancelada:
        return "Cancelada"
    return "-"


def _build_partner_statement_report(context: dict) -> dict:
    partner = context["partner"]
    linked_partner = context.get("linked_partner")
    linked_summary = None
    if linked_partner:
        linked_summary = (
            f"Vinculado con {context.get('linked_partner_label') or 'partner'}: "
            f"{linked_partner.nombre_completo} (ID {linked_partner.id})"
        )
    elif context.get("provider_direct_sales_enabled"):
        linked_summary = "Opera compras y ventas en el mismo perfil."

    summary = context["summary"]
    summary_items: list[dict] = [
        {"label": "Notas totales", "value": summary.get("total_notas", 0), "type": "count"},
        {"label": "Notas aprobadas", "value": summary.get("notas_aprobadas", 0), "type": "count"},
        {"label": "Notas en revision", "value": summary.get("notas_revision", 0), "type": "count"},
        {"label": "Notas borrador", "value": summary.get("notas_borrador", 0), "type": "count"},
        {"label": "Notas canceladas", "value": summary.get("notas_canceladas", 0), "type": "count"},
    ]
    if context.get("unified_enabled"):
        summary_items.extend(
            [
                {"label": "Total compras aprobadas", "value": summary.get("total_compras", 0), "type": "money"},
                {"label": "Total pagado compras", "value": summary.get("total_pagado_compras", 0), "type": "money"},
                {"label": "Saldo por pagar compras", "value": summary.get("saldo_pagar", 0), "type": "money"},
                {"label": "Total ventas aprobadas", "value": summary.get("total_ventas", 0), "type": "money"},
                {"label": "Total cobrado ventas", "value": summary.get("total_cobrado_ventas", 0), "type": "money"},
                {"label": "Saldo por cobrar ventas", "value": summary.get("saldo_cobrar", 0), "type": "money"},
                {"label": "Ajustes proveedor", "value": summary.get("ajustes_proveedor", 0), "type": "money"},
                {"label": "Ajustes cliente", "value": summary.get("ajustes_cliente", 0), "type": "money"},
                {"label": "Saldo neto unificado", "value": summary.get("saldo_neto", 0), "type": "money"},
            ]
        )
    else:
        summary_items.extend(
            [
                {"label": context["total_facturado_label"], "value": summary.get("total_facturado", 0), "type": "money"},
                {"label": context["total_pagado_label"], "value": summary.get("total_pagado", 0), "type": "money"},
                {"label": context["saldo_pendiente_label"], "value": summary.get("saldo_pendiente", 0), "type": "money"},
                {"label": context["saldo_favor_label"], "value": summary.get("saldo_favor", 0), "type": "money"},
                {"label": "Ajustes manuales", "value": summary.get("ajustes_delta", 0), "type": "money"},
                {"label": "Ajustes de saldo en notas", "value": summary.get("ajustes_nota_delta", 0), "type": "money"},
            ]
        )

    notes_rows: list[dict] = []
    sucursales = context.get("sucursales") or {}
    unified_enabled = bool(context.get("unified_enabled"))
    for row in context.get("record_rows") or []:
        nota = row["nota"]
        sucursal = sucursales.get(nota.sucursal_id)
        notes_rows.append(
            {
                "folio": row.get("folio") or f"#{nota.id}",
                "operacion": _partner_record_note_operation_label(nota, unified_enabled),
                "estado": _partner_record_note_state_label(nota, is_paid=bool(row.get("is_paid"))),
                "fecha": nota.created_at,
                "sucursal": sucursal.nombre if sucursal else "-",
                "total": row.get("total") or Decimal("0"),
                "pagado": row.get("pagado") or Decimal("0"),
                "saldo_pendiente": row.get("saldo_pendiente") or Decimal("0"),
                "saldo_favor": row.get("saldo_favor") or Decimal("0"),
                "ajuste_aplicado": row.get("ajuste_aplicado") or Decimal("0"),
                "ajuste_nota": row.get("ajuste_saldo_nota") or Decimal("0"),
            }
        )

    return {
        "generated_at": datetime.utcnow(),
        "partner_label": context["partner_label"],
        "partner_name": partner.nombre_completo,
        "partner_id": partner.id,
        "scope_label": context.get("record_scope_label") or context.get("tipo_operacion_label") or "Historial completo",
        "linked_summary": linked_summary,
        "summary_items": summary_items,
        "ledger_label": context["ledger_saldo_label"],
        "ledger_help": context["ledger_saldo_help"],
        "ledger_final": context.get("ledger_final") or Decimal("0"),
        "ledger_rows": context.get("ledger_rows") or [],
        "notes_rows": notes_rows,
    }


def _build_provider_attendance_report(context: dict) -> dict:
    partner = context["partner"]
    return {
        "generated_at": datetime.utcnow(),
        "partner_name": partner.nombre_completo,
        "partner_id": partner.id,
        "range_label": context.get("attendance_range_label") or "Historial completo",
        "attendance_total_historico": context.get("attendance_total_historico") or 0,
        "attendance_total_filtrado": context.get("attendance_total_filtrado") or 0,
        "attendance_rows": context.get("attendance_rows") or [],
    }


def _ensure_nota_access(
    nota: Nota,
    allowed_ids: list[int] | None,
) -> None:
    if allowed_ids is None:
        return
    if nota.sucursal_id not in allowed_ids:
        raise HTTPException(status_code=403, detail="No tienes acceso a esta sucursal.")


def _ensure_partner_access(
    partner: Cliente | Proveedor,
    allowed_ids: list[int] | None,
) -> None:
    if allowed_ids is None:
        return
    if partner.sucursal_id not in allowed_ids:
        raise HTTPException(status_code=403, detail="No tienes acceso a esta sucursal.")


def _ensure_scrap360_access(cuenta: CuentaScrap360, allowed_ids: list[int] | None) -> None:
    if allowed_ids is None:
        return
    if not cuenta.sucursales:
        raise HTTPException(status_code=403, detail="No tienes acceso a esta cuenta.")
    allowed = {s.id for s in cuenta.sucursales}
    if not allowed.intersection(set(allowed_ids)):
        raise HTTPException(status_code=403, detail="No tienes acceso a esta cuenta.")


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


def require_viewer_or_admin_or_superadmin(request: Request) -> dict:
    user = request.session.get("user")
    if not user or user.get("rol") not in ("super_admin", "admin", "visor"):
        raise HTTPException(status_code=403, detail="No tienes acceso a esta seccion.")
    return user


def _is_read_only_admin_user(current_user: dict | None) -> bool:
    return bool(current_user) and current_user.get("rol") == UserRole.visor.value


def _user_delete_block_reason(
    db: Session,
    *,
    target_user: User,
    current_user_id: int | None,
) -> str | None:
    if current_user_id and target_user.id == current_user_id:
        return "No puedes eliminar tu propio usuario."
    if target_user.super_admin_original:
        return "No se puede eliminar el super admin original."
    if target_user.rol == UserRole.super_admin:
        remaining_superadmins = (
            db.query(User)
            .filter(User.rol == UserRole.super_admin, User.id != target_user.id)
            .count()
        )
        if remaining_superadmins == 0:
            return "Debe existir al menos un super admin en el sistema."
    worker_notes = db.query(Nota).filter(Nota.trabajador_id == target_user.id).count()
    if worker_notes:
        return f"No se puede eliminar: tiene {worker_notes} notas registradas como trabajador."
    return None


def _detach_user_references(db: Session, *, user_id: int) -> None:
    db.query(PriceChangeLog).filter(PriceChangeLog.user_id == user_id).update(
        {"user_id": None},
        synchronize_session=False,
    )
    db.query(AjusteSaldoPartner).filter(AjusteSaldoPartner.usuario_id == user_id).update(
        {"usuario_id": None},
        synchronize_session=False,
    )
    db.query(Nota).filter(Nota.admin_id == user_id).update(
        {"admin_id": None},
        synchronize_session=False,
    )
    db.query(NotaEvidenciaExtra).filter(NotaEvidenciaExtra.uploaded_by_id == user_id).update(
        {"uploaded_by_id": None},
        synchronize_session=False,
    )
    db.query(NotaPago).filter(NotaPago.usuario_id == user_id).update(
        {"usuario_id": None},
        synchronize_session=False,
    )
    db.query(NotaAjusteSaldo).filter(NotaAjusteSaldo.usuario_id == user_id).update(
        {"usuario_id": None},
        synchronize_session=False,
    )
    db.query(NotaAjusteSaldo).filter(NotaAjusteSaldo.reverted_by_user_id == user_id).update(
        {"reverted_by_user_id": None},
        synchronize_session=False,
    )
    db.query(NotaDevolucionParcial).filter(NotaDevolucionParcial.usuario_id == user_id).update(
        {"usuario_id": None},
        synchronize_session=False,
    )
    db.query(NotaDevolucionParcialLinea).filter(
        NotaDevolucionParcialLinea.reverted_by_user_id == user_id
    ).update(
        {"reverted_by_user_id": None},
        synchronize_session=False,
    )
    db.query(NotaDevolucionTotal).filter(NotaDevolucionTotal.usuario_id == user_id).update(
        {"usuario_id": None},
        synchronize_session=False,
    )
    db.query(NotaDevolucionTotal).filter(NotaDevolucionTotal.reverted_by_user_id == user_id).update(
        {"reverted_by_user_id": None},
        synchronize_session=False,
    )
    db.query(InventarioMovimiento).filter(InventarioMovimiento.usuario_id == user_id).update(
        {"usuario_id": None},
        synchronize_session=False,
    )
    db.query(MovimientoContable).filter(MovimientoContable.usuario_id == user_id).update(
        {"usuario_id": None},
        synchronize_session=False,
    )
    db.query(InventarioAjusteManual).filter(InventarioAjusteManual.usuario_id == user_id).update(
        {"usuario_id": None},
        synchronize_session=False,
    )
    db.query(InventarioAjusteManual).filter(
        InventarioAjusteManual.reverted_by_user_id == user_id
    ).update(
        {"reverted_by_user_id": None},
        synchronize_session=False,
    )
    db.query(CorteCaja).filter(CorteCaja.abierto_por_id == user_id).update(
        {"abierto_por_id": None},
        synchronize_session=False,
    )
    db.query(CorteCaja).filter(CorteCaja.cerrado_por_id == user_id).update(
        {"cerrado_por_id": None},
        synchronize_session=False,
    )
    db.query(CorteCajaGasto).filter(CorteCajaGasto.usuario_id == user_id).update(
        {"usuario_id": None},
        synchronize_session=False,
    )
    db.query(CorteCajaMovimiento).filter(CorteCajaMovimiento.usuario_id == user_id).update(
        {"usuario_id": None},
        synchronize_session=False,
    )
    db.query(CuentaScrap360Movimiento).filter(CuentaScrap360Movimiento.usuario_id == user_id).update(
        {"usuario_id": None},
        synchronize_session=False,
    )
    db.query(ConversionMaterial).filter(ConversionMaterial.usuario_id == user_id).update(
        {"usuario_id": None},
        synchronize_session=False,
    )
    db.query(ConversionMaterialReversion).filter(
        ConversionMaterialReversion.usuario_id == user_id
    ).update(
        {"usuario_id": None},
        synchronize_session=False,
    )
    db.query(ComisionarioNota).filter(ComisionarioNota.admin_id == user_id).update(
        {"admin_id": None},
        synchronize_session=False,
    )
    db.query(ComisionarioPago).filter(ComisionarioPago.usuario_id == user_id).update(
        {"usuario_id": None},
        synchronize_session=False,
    )


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


def _active_sucursales(db: Session) -> list[Sucursal]:
    """Sucursales elegibles en formularios de captura.

    Punto 5 (fase 2): una sucursal archivada deja de aparecer en los selectores
    de operaciones nuevas, pero las listas, filtros y reportes siguen mostrando
    todas para que su historial no desaparezca.
    """
    return (
        db.query(Sucursal)
        .filter(Sucursal.estado == SucursalStatus.activa)
        .order_by(Sucursal.nombre)
        .all()
    )


def _sucursal_archive_blockers(db: Session, sucursal: Sucursal) -> list[str]:
    """Razones que impiden archivar una sucursal, con su ruta de salida."""
    razones: list[str] = []
    corte_abierto = (
        db.query(CorteCaja)
        .filter(CorteCaja.sucursal_id == sucursal.id, CorteCaja.estado == CorteCajaEstado.abierto)
        .first()
    )
    if corte_abierto:
        razones.append("tiene un corte de caja abierto (ciérralo primero)")
    notas_abiertas = (
        db.query(Nota)
        .filter(
            Nota.sucursal_id == sucursal.id,
            Nota.estado.in_([NotaEstado.borrador, NotaEstado.en_revision]),
        )
        .count()
    )
    if notas_abiertas:
        plural = "s" if notas_abiertas != 1 else ""
        razones.append(f"tiene {notas_abiertas} nota{plural} sin aprobar (apruébalas o cancélalas)")
    usuarios_activos = (
        db.query(User)
        .filter(
            User.sucursal_id == sucursal.id,
            User.estado == UserStatus.activo,
            User.rol != UserRole.super_admin,
        )
        .count()
    )
    if usuarios_activos:
        plural = "s" if usuarios_activos != 1 else ""
        razones.append(
            f"tiene {usuarios_activos} usuario{plural} activo{plural} asignado{plural} (reasígnalos o desactívalos)"
        )
    stock_total = (
        db.query(func.coalesce(func.sum(Inventario.stock_actual), 0))
        .filter(Inventario.sucursal_id == sucursal.id)
        .scalar()
    )
    if Decimal(str(stock_total or 0)) > Decimal("0.005"):
        razones.append(
            f"conserva {Decimal(str(stock_total)):,.2f} kg en inventario (transfiérelos a otra sucursal)"
        )
    return razones


@router.post("/sucursales/{sucursal_id}/archivar")
async def sucursal_archivar(
    sucursal_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    sucursal = db.query(Sucursal).get(sucursal_id)
    if not sucursal:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")
    if sucursal.estado == SucursalStatus.inactiva:
        return RedirectResponse(url="/web/admin/sucursales", status_code=303)
    razones = _sucursal_archive_blockers(db, sucursal)
    if razones:
        detalle = f"No se puede archivar {sucursal.nombre}: " + "; ".join(razones) + "."
        return RedirectResponse(
            url=_append_query_params("/web/admin/sucursales", archivar_error=detalle),
            status_code=303,
        )
    sucursal.estado = SucursalStatus.inactiva
    db.add(sucursal)
    db.commit()
    return RedirectResponse(
        url=_append_query_params("/web/admin/sucursales", archivada=sucursal.nombre),
        status_code=303,
    )


@router.post("/sucursales/{sucursal_id}/reactivar")
async def sucursal_reactivar(
    sucursal_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    sucursal = db.query(Sucursal).get(sucursal_id)
    if not sucursal:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")
    if sucursal.estado == SucursalStatus.activa:
        return RedirectResponse(url="/web/admin/sucursales", status_code=303)
    sucursal.estado = SucursalStatus.activa
    db.add(sucursal)
    db.commit()
    return RedirectResponse(
        url=_append_query_params("/web/admin/sucursales", reactivada=sucursal.nombre),
        status_code=303,
    )


# ---------- USUARIOS ----------


def _user_access_labels(user: User, sucursales_map: dict[int, Sucursal]) -> list[str]:
    if user.rol == UserRole.admin:
        labels = [s.nombre for s in user.sucursales_admin]
        if not labels and user.sucursal_id and sucursales_map.get(user.sucursal_id):
            labels = [sucursales_map[user.sucursal_id].nombre]
        return labels or ["Sin sucursales"]
    if user.rol == UserRole.trabajador:
        if user.sucursal_id and sucursales_map.get(user.sucursal_id):
            return [sucursales_map[user.sucursal_id].nombre]
        return ["Sin sucursal"]
    if user.rol == UserRole.visor:
        return ["Consulta global"]
    return ["Control total"]


def _build_user_form_data(
    *,
    username: str = "",
    nombre_completo: str = "",
    rol: str = "admin",
    estado: str = "activo",
    sucursal_id: int | str | None = None,
    admin_sucursal_ids: list[int | str] | None = None,
    super_admin_original: bool = False,
) -> dict:
    return {
        "username": username,
        "nombre_completo": nombre_completo,
        "rol": rol,
        "estado": estado,
        "sucursal_id": "" if sucursal_id in (None, "") else str(sucursal_id),
        "admin_sucursal_ids": [str(sid) for sid in (admin_sucursal_ids or []) if sid not in (None, "")],
        "super_admin_original": bool(super_admin_original),
    }


@router.get("/users")
async def users_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    updated = request.query_params.get("updated") == "1"
    delete_ok = request.query_params.get("deleted") == "1"
    delete_error = (request.query_params.get("delete_error") or "").strip() or None
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
    users_summary = {
        "total": len(usuarios),
        "activos": sum(1 for u in usuarios if u.estado == UserStatus.activo),
        "admins": sum(1 for u in usuarios if u.rol == UserRole.admin),
        "trabajadores": sum(1 for u in usuarios if u.rol == UserRole.trabajador),
        "visores": sum(1 for u in usuarios if u.rol == UserRole.visor),
        "super_admins": sum(1 for u in usuarios if u.rol == UserRole.super_admin),
    }
    user_delete_meta = {}
    user_access_map = {}
    for u in usuarios:
        reason = _user_delete_block_reason(
            db,
            target_user=u,
            current_user_id=current_user.get("id"),
        )
        user_delete_meta[u.id] = {
            "can_delete": reason is None,
            "reason": reason,
        }
        user_access_map[u.id] = _user_access_labels(u, sucursales)

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
            "delete_ok": delete_ok,
            "delete_error": delete_error,
            "user_delete_meta": user_delete_meta,
            "user_access_map": user_access_map,
            "users_summary": users_summary,
        },
    )


@router.get("/users/nuevo")
async def user_new_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    sucursales = _active_sucursales(db)
    return templates.TemplateResponse(
        "admin/user_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "sucursales": sucursales,
            "error": None,
            "form_data": _build_user_form_data(),
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
    username = normalizar_username(username)
    nombre_completo = nombre_completo.strip()
    password = normalizar_password(password)

    sucursales = _active_sucursales(db)
    form_data = _build_user_form_data(
        username=username,
        nombre_completo=nombre_completo,
        rol=rol,
        sucursal_id=sucursal_id,
        admin_sucursal_ids=admin_sucursal_ids,
    )

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

    # Unicidad de username. Sin ignorar mayúsculas se pueden crear "Visor" y
    # "visor" a la vez, y después nadie sabe con cuál de las dos entrar.
    existing = db.query(User).filter(func.lower(User.username) == username.lower()).first()
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

    if user_role != UserRole.admin:
        selected_admin_suc_ids = []
    if user_role != UserRole.trabajador:
        sucursal_id = None

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
    sucursales = _active_sucursales(db)
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
            "form_data": _build_user_form_data(
                username=user.username,
                nombre_completo=user.nombre_completo,
                rol=user.rol.value,
                estado=user.estado.value,
                sucursal_id=user.sucursal_id,
                admin_sucursal_ids=admin_sucursal_ids,
                super_admin_original=user.super_admin_original,
            ),
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

    username = normalizar_username(username)
    nombre_completo = nombre_completo.strip()
    password = normalizar_password(password)

    sucursales = _active_sucursales(db)
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
                "admin_sucursal_ids": selected_admin_suc_ids,
                "error": msg,
                "form_data": _build_user_form_data(
                    username=username,
                    nombre_completo=nombre_completo,
                    rol=rol,
                    estado=estado,
                    sucursal_id=sucursal_id,
                    admin_sucursal_ids=admin_sucursal_ids,
                    super_admin_original=bool(super_admin_original),
                ),
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
    found: list[Sucursal] = []
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

    existing = (
        db.query(User)
        .filter(func.lower(User.username) == username.lower(), User.id != user.id)
        .first()
    )
    if existing:
        return render_error("Ya existe un usuario con ese username.")

    if user_role != UserRole.admin:
        selected_admin_suc_ids = []
        found = []
    if user_role != UserRole.trabajador:
        suc_id = None

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


@router.post("/users/{user_id}/eliminar")
async def user_delete(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado.")

    block_reason = _user_delete_block_reason(
        db,
        target_user=user,
        current_user_id=current_user.get("id"),
    )
    if block_reason:
        return RedirectResponse(
            url=f"/web/admin/users?{urlencode({'delete_error': block_reason})}",
            status_code=303,
        )

    if user.rol == UserRole.admin:
        user.sucursales_admin = []
    _detach_user_references(db, user_id=user.id)
    db.flush()
    db.delete(user)
    db.commit()
    return RedirectResponse(url="/web/admin/users?deleted=1", status_code=303)


# ---------- MATERIALES ----------


@router.get("/materiales")
async def materiales_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    params = request.query_params
    delete_ok = params.get("deleted") == "1"
    delete_error = (params.get("delete_error") or "").strip() or None
    materiales = db.query(Material).order_by(Material.orden_display, Material.nombre).all()
    return templates.TemplateResponse(
        "admin/materiales_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "materiales": materiales,
            "delete_ok": delete_ok,
            "delete_error": delete_error,
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


@router.post("/materiales/{material_id}/eliminar")
async def material_delete(
    material_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    material = db.get(Material, material_id)
    if not material:
        raise HTTPException(status_code=404, detail="Material no encontrado.")

    tiene_notas = db.query(NotaMaterial.id).filter(NotaMaterial.material_id == material_id).first()
    tiene_inventario = db.query(Inventario.id).filter(Inventario.material_id == material_id).first()
    tiene_precios = db.query(TablaPrecio.id).filter(TablaPrecio.material_id == material_id).first()
    tiene_conversion = (
        db.query(ConversionMaterial.id)
        .filter(
            or_(
                ConversionMaterial.material_origen_id == material_id,
                ConversionMaterial.material_destino_id == material_id,
            )
        )
        .first()
    )
    tiene_comision = (
        db.query(ComisionarioNotaMaterial.id)
        .filter(ComisionarioNotaMaterial.material_id == material_id)
        .first()
    )

    if tiene_notas or tiene_inventario or tiene_precios or tiene_conversion or tiene_comision:
        reasons = []
        if tiene_notas:
            reasons.append("notas")
        if tiene_inventario:
            reasons.append("inventario")
        if tiene_precios:
            reasons.append("precios")
        if tiene_conversion:
            reasons.append("conversiones")
        if tiene_comision:
            reasons.append("comisiones")
        msg = f"No se puede eliminar: tiene {', '.join(reasons)} asociados."
        return RedirectResponse(
            url=f"/web/admin/materiales?{urlencode({'delete_error': msg})}",
            status_code=303,
        )

    db.delete(material)
    db.commit()
    return RedirectResponse(url="/web/admin/materiales?deleted=1", status_code=303)


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

    tipos_operacion = [TipoOperacion.compra, TipoOperacion.venta]
    return templates.TemplateResponse(
        "admin/precio_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "material": material,
            "error": None,
            "tipos_operacion": tipos_operacion,
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
                "tipos_operacion": [TipoOperacion.compra, TipoOperacion.venta],
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
                "tipos_operacion": [TipoOperacion.compra, TipoOperacion.venta],
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

def _partner_form_sucursales(
    db: Session,
    current_user: dict,
) -> tuple[list[int] | None, list[Sucursal]]:
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    sucursales = _active_sucursales(db)
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    return allowed_suc_ids, sucursales


def _render_proveedor_form(
    request: Request,
    current_user: dict,
    *,
    proveedor: Proveedor | None,
    error: str | None,
    placas_text: str,
    clientes: list[Cliente],
    linked_cliente_id: int | None,
    sucursales: list[Sucursal],
    sucursal_id_selected: int | None,
    permite_ventas_selected: bool = False,
    counterpart_suggestion: dict | None = None,
    link_ok: bool = False,
    link_msg: str | None = None,
    link_warn: str | None = None,
    link_error: str | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        "admin/proveedor_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "proveedor": proveedor,
            "error": error,
            "placas_text": placas_text,
            "clientes": clientes,
            "linked_cliente_id": linked_cliente_id,
            "sucursales": sucursales,
            "sucursal_id_selected": sucursal_id_selected,
            "permite_ventas_selected": permite_ventas_selected,
            "counterpart_suggestion": counterpart_suggestion,
            "link_ok": link_ok,
            "link_msg": link_msg,
            "link_warn": link_warn,
            "link_error": link_error,
        },
        status_code=status_code,
    )


def _render_cliente_form(
    request: Request,
    current_user: dict,
    *,
    cliente: Cliente | None,
    error: str | None,
    placas_text: str,
    proveedores: list[Proveedor],
    linked_proveedor_id: int | None,
    sucursales: list[Sucursal],
    sucursal_id_selected: int | None,
    counterpart_suggestion: dict | None = None,
    link_ok: bool = False,
    link_msg: str | None = None,
    link_warn: str | None = None,
    link_error: str | None = None,
    status_code: int = 200,
):
    return templates.TemplateResponse(
        "admin/cliente_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "cliente": cliente,
            "error": error,
            "placas_text": placas_text,
            "proveedores": proveedores,
            "linked_proveedor_id": linked_proveedor_id,
            "sucursales": sucursales,
            "sucursal_id_selected": sucursal_id_selected,
            "counterpart_suggestion": counterpart_suggestion,
            "link_ok": link_ok,
            "link_msg": link_msg,
            "link_warn": link_warn,
            "link_error": link_error,
        },
        status_code=status_code,
    )


# ---------- PROVEEDORES ----------


@router.get("/proveedores")
async def proveedores_list(
    request: Request,
    q: str | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
):
    params = request.query_params
    modo_current = (params.get("modo") or "TODOS").strip().upper()
    if modo_current not in {"TODOS", "COMPRA_VENTA", "CON_SALDO"}:
        modo_current = "TODOS"
    delete_ok = params.get("deleted") == "1"
    delete_error = (params.get("delete_error") or "").strip()
    query = db.query(Proveedor)
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    sucursal_id = None
    sucursal_error = None
    if params.get("sucursal_id"):
        sucursal_id, sucursal_error = _selected_sucursal_from_request(
            db,
            raw_value=params.get("sucursal_id"),
            allowed_suc_ids=allowed_suc_ids,
        )
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    query = _apply_sucursal_filter(query, allowed_suc_ids, sucursal_id, Proveedor.sucursal_id)

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
    proveedores_view = []
    for proveedor in proveedores:
        bundle = _collect_proveedor_sales_bundle(
            db,
            proveedor=proveedor,
            allowed_suc_ids=allowed_suc_ids,
            sucursal_id=sucursal_id,
        )
        linked_cliente = bundle["linked_cliente"]

        compras_query = db.query(Nota).filter(
            Nota.proveedor_id == proveedor.id,
            Nota.tipo_operacion == TipoOperacion.compra,
        )
        compras_query = _apply_sucursal_filter(
            compras_query,
            allowed_suc_ids,
            sucursal_id,
            Nota.sucursal_id,
        )
        compras = compras_query.order_by(Nota.created_at.desc()).all()

        ventas = bundle["ventas"]
        note_adjustment_totals = _get_note_balance_adjustment_totals_map(
            db,
            [nota.id for nota in (compras + ventas) if nota.id],
        )

        ajustes_proveedor = _get_partner_adjustments_total(
            db,
            partner_type="proveedor",
            partner_id=proveedor.id,
            allowed_suc_ids=allowed_suc_ids,
            sucursal_id=sucursal_id,
        )
        ajustes_cliente = Decimal("0")
        if linked_cliente:
            ajustes_cliente = _get_partner_adjustments_total(
                db,
                partner_type="cliente",
                partner_id=linked_cliente.id,
                allowed_suc_ids=allowed_suc_ids,
                sucursal_id=sucursal_id,
            )

        unified_summary = _aggregate_unified_partner_summary(
            compras=compras,
            ventas=ventas,
            ajustes_proveedor=ajustes_proveedor,
            ajustes_cliente=ajustes_cliente,
            note_adjustment_totals=note_adjustment_totals,
        )
        proveedores_view.append(
            {
                "proveedor": proveedor,
                "linked_cliente": linked_cliente,
                "saldo_neto": unified_summary["saldo_neto"],
                "direct_enabled": bool(bundle["direct_enabled"]),
                "ventas_directas_count": len(bundle["ventas_directas"]),
                "ventas_total_count": len(ventas),
            }
        )

    total_proveedores = len(proveedores_view)
    compra_venta_count = sum(1 for row in proveedores_view if row["direct_enabled"])
    con_saldo_count = sum(1 for row in proveedores_view if abs(Decimal(str(row["saldo_neto"] or 0))) > Decimal("0.009"))
    legado_count = sum(1 for row in proveedores_view if row["linked_cliente"])

    if modo_current == "COMPRA_VENTA":
        proveedores_view = [row for row in proveedores_view if row["direct_enabled"]]
    elif modo_current == "CON_SALDO":
        proveedores_view = [
            row
            for row in proveedores_view
            if abs(Decimal(str(row["saldo_neto"] or 0))) > Decimal("0.009")
        ]

    return templates.TemplateResponse(
        "admin/proveedores_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "proveedores": proveedores_view,
            "q": q or "",
            "sucursales": sucursales,
            "sucursal_id": sucursal_id,
            "sucursal_error": sucursal_error,
            "modo_current": modo_current,
            "total_proveedores": total_proveedores,
            "compra_venta_count": compra_venta_count,
            "con_saldo_count": con_saldo_count,
            "legado_count": legado_count,
            "delete_ok": delete_ok,
            "delete_error": delete_error,
        },
    )


@router.post("/proveedores/{proveedor_id}/eliminar")
async def proveedor_delete(
    proveedor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    proveedor = db.get(Proveedor, proveedor_id)
    if not proveedor:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado.")

    tiene_notas = db.query(Nota.id).filter(Nota.proveedor_id == proveedor_id).first()
    tiene_cuentas = db.query(Cuenta.id).filter(Cuenta.proveedor_id == proveedor_id).first()
    tiene_ajustes = (
        db.query(AjusteSaldoPartner.id)
        .filter(
            AjusteSaldoPartner.partner_type == "proveedor",
            AjusteSaldoPartner.partner_id == proveedor_id,
        )
        .first()
    )
    if tiene_notas or tiene_cuentas or tiene_ajustes:
        reasons = []
        if tiene_notas:
            reasons.append("notas")
        if tiene_cuentas:
            reasons.append("cuentas")
        if tiene_ajustes:
            reasons.append("ajustes")
        msg = f"No se puede eliminar: tiene {', '.join(reasons)} asociados."
        return RedirectResponse(url=f"/web/admin/proveedores?{urlencode({'delete_error': msg})}", status_code=303)

    if proveedor.linked_cliente_id:
        cliente = db.get(Cliente, proveedor.linked_cliente_id)
        if cliente and cliente.linked_proveedor_id == proveedor.id:
            cliente.linked_proveedor_id = None
            db.add(cliente)

    db.delete(proveedor)
    db.commit()
    return RedirectResponse(url="/web/admin/proveedores?deleted=1", status_code=303)


@router.get("/proveedores/nuevo")
async def proveedor_new_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids, sucursales = _partner_form_sucursales(db, current_user)
    default_sucursal_id = sucursales[0].id if sucursales else None
    clientes_list = _list_linkable_clientes(
        db,
        allowed_suc_ids=allowed_suc_ids,
        sucursal_id=default_sucursal_id,
    )
    return _render_proveedor_form(
        request,
        current_user,
        proveedor=None,
        error=None,
        placas_text="",
        clientes=clientes_list,
        linked_cliente_id=None,
        sucursales=sucursales,
        sucursal_id_selected=default_sucursal_id,
        permite_ventas_selected=False,
    )


@router.post("/proveedores/nuevo")
async def proveedor_new_post(
    request: Request,
    nombre_completo: str = Form(...),
    telefono: str = Form(""),
    correo_electronico: str = Form(""),
    placas: str = Form(""),
    permite_ventas: str | None = Form(None),
    linked_cliente_id: str | None = Form(None),
    sucursal_id: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids, sucursales = _partner_form_sucursales(db, current_user)
    default_sucursal_id = sucursales[0].id if sucursales else None
    sucursal_id_selected, sucursal_error = _selected_sucursal_from_request(
        db,
        raw_value=sucursal_id,
        allowed_suc_ids=allowed_suc_ids,
        default_id=default_sucursal_id,
    )
    clientes_list = _list_linkable_clientes(
        db,
        allowed_suc_ids=allowed_suc_ids,
        sucursal_id=sucursal_id_selected,
    )

    nombre_completo = nombre_completo.strip()
    telefono = telefono.strip()
    correo_electronico = correo_electronico.strip()
    placas_list = _parse_placas(placas)
    permite_ventas_selected = bool(permite_ventas)
    linked_id = _parse_optional_int(linked_cliente_id)
    linked_cliente = db.get(Cliente, linked_id) if linked_id else None

    if sucursal_error:
        return _render_proveedor_form(
            request,
            current_user,
            proveedor=None,
            error=sucursal_error,
            placas_text=placas,
            clientes=clientes_list,
            linked_cliente_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            permite_ventas_selected=permite_ventas_selected,
            status_code=400,
        )
    if not sucursal_id_selected:
        return _render_proveedor_form(
            request,
            current_user,
            proveedor=None,
            error="Debes seleccionar una sucursal.",
            placas_text=placas,
            clientes=clientes_list,
            linked_cliente_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            permite_ventas_selected=permite_ventas_selected,
            status_code=400,
        )

    if linked_id and not linked_cliente:
        return _render_proveedor_form(
            request,
            current_user,
            proveedor=None,
            error="Cliente vinculado no encontrado.",
            placas_text=placas,
            clientes=clientes_list,
            linked_cliente_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            permite_ventas_selected=permite_ventas_selected,
            status_code=400,
        )
    if linked_cliente and _is_internal_partner_name(db, linked_cliente.nombre_completo):
        return _render_proveedor_form(
            request,
            current_user,
            proveedor=None,
            error="No puedes vincular una sucursal interna.",
            placas_text=placas,
            clientes=clientes_list,
            linked_cliente_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            permite_ventas_selected=permite_ventas_selected,
            status_code=400,
        )
    if linked_cliente and linked_cliente.sucursal_id != sucursal_id_selected:
        return _render_proveedor_form(
            request,
            current_user,
            proveedor=None,
            error="El cliente vinculado debe pertenecer a la misma sucursal.",
            placas_text=placas,
            clientes=clientes_list,
            linked_cliente_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            permite_ventas_selected=permite_ventas_selected,
            status_code=400,
        )

    if not nombre_completo:
        return _render_proveedor_form(
            request,
            current_user,
            proveedor=None,
            error="El nombre del proveedor es obligatorio.",
            placas_text=placas,
            clientes=clientes_list,
            linked_cliente_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            permite_ventas_selected=permite_ventas_selected,
            status_code=400,
        )

    conflict = _placas_conflict(db, placas_list, ProveedorPlaca, "proveedor_id", None)
    if conflict:
        return _render_proveedor_form(
            request,
            current_user,
            proveedor=None,
            error=conflict,
            placas_text=placas,
            clientes=clientes_list,
            linked_cliente_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            status_code=400,
        )

    proveedor = Proveedor(
        nombre_completo=nombre_completo,
        sucursal_id=sucursal_id_selected,
        telefono=telefono or None,
        correo_electronico=correo_electronico or None,
        placas=placas_list[0] if placas_list else None,
        activo=True,
        permite_ventas=permite_ventas_selected,
    )
    db.add(proveedor)
    db.flush()
    if linked_cliente:
        try:
            _link_cliente_proveedor(db, cliente=linked_cliente, proveedor=proveedor)
        except ValueError as exc:
            db.rollback()
            return _render_proveedor_form(
                request,
                current_user,
                proveedor=None,
                error=str(exc),
                placas_text=placas,
                clientes=clientes_list,
                linked_cliente_id=linked_id,
                sucursales=sucursales,
                sucursal_id_selected=sucursal_id_selected,
                permite_ventas_selected=permite_ventas_selected,
                status_code=400,
            )
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

    allowed_suc_ids, sucursales = _partner_form_sucursales(db, current_user)
    _ensure_partner_access(proveedor, allowed_suc_ids)
    clientes_list = _list_linkable_clientes(
        db,
        allowed_suc_ids=allowed_suc_ids,
        sucursal_id=proveedor.sucursal_id,
    )
    linked_cliente = _get_formally_linked_cliente(db, proveedor)
    if linked_cliente and linked_cliente not in clientes_list:
        clientes_list.append(linked_cliente)
    if linked_cliente:
        clientes_list = sorted(clientes_list, key=lambda c: c.nombre_completo or "")
    counterpart_suggestion = _build_counterpart_suggestion_for_proveedor(db, proveedor)
    link_ok = request.query_params.get("link_ok") == "1"
    link_msg = (request.query_params.get("link_msg") or "").strip() or None
    link_warn = (request.query_params.get("link_warn") or "").strip() or None
    link_error = (request.query_params.get("link_error") or "").strip() or None

    return _render_proveedor_form(
        request,
        current_user,
        proveedor=proveedor,
        error=None,
        placas_text="\n".join([pl.placa for pl in proveedor.placas_rel]) if proveedor.placas_rel else (proveedor.placas or ""),
        clientes=clientes_list,
        linked_cliente_id=linked_cliente.id if linked_cliente else proveedor.linked_cliente_id,
        sucursales=sucursales,
        sucursal_id_selected=proveedor.sucursal_id,
        permite_ventas_selected=bool(getattr(proveedor, "permite_ventas", False)),
        counterpart_suggestion=counterpart_suggestion,
        link_ok=link_ok,
        link_msg=link_msg,
        link_warn=link_warn,
        link_error=link_error,
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
    permite_ventas: str | None = Form(None),
    linked_cliente_id: str | None = Form(None),
    sucursal_id: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    proveedor = db.query(Proveedor).get(proveedor_id)
    if not proveedor:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado.")

    allowed_suc_ids, sucursales = _partner_form_sucursales(db, current_user)
    _ensure_partner_access(proveedor, allowed_suc_ids)
    sucursal_id_selected, sucursal_error = _selected_sucursal_from_request(
        db,
        raw_value=sucursal_id,
        allowed_suc_ids=allowed_suc_ids,
        default_id=proveedor.sucursal_id,
    )
    clientes_list = _list_linkable_clientes(
        db,
        allowed_suc_ids=allowed_suc_ids,
        sucursal_id=sucursal_id_selected,
    )

    nombre_completo = nombre_completo.strip()
    telefono = telefono.strip()
    correo_electronico = correo_electronico.strip()
    placas_list = _parse_placas(placas)
    permite_ventas_selected = bool(permite_ventas)
    existing_linked_cliente = _get_formally_linked_cliente(db, proveedor)
    linked_id = existing_linked_cliente.id if existing_linked_cliente else _parse_optional_int(linked_cliente_id)
    linked_cliente = db.get(Cliente, linked_id) if linked_id else None

    if sucursal_error:
        return _render_proveedor_form(
            request,
            current_user,
            proveedor=proveedor,
            error=sucursal_error,
            placas_text=placas,
            clientes=clientes_list,
            linked_cliente_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            permite_ventas_selected=permite_ventas_selected,
            status_code=400,
        )
    if not sucursal_id_selected:
        return _render_proveedor_form(
            request,
            current_user,
            proveedor=proveedor,
            error="Debes seleccionar una sucursal.",
            placas_text=placas,
            clientes=clientes_list,
            linked_cliente_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            permite_ventas_selected=permite_ventas_selected,
            status_code=400,
        )

    if linked_id and not linked_cliente:
        return _render_proveedor_form(
            request,
            current_user,
            proveedor=proveedor,
            error="Cliente vinculado no encontrado.",
            placas_text=placas,
            clientes=clientes_list,
            linked_cliente_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            permite_ventas_selected=permite_ventas_selected,
            status_code=400,
        )
    if linked_cliente and _is_internal_partner_name(db, linked_cliente.nombre_completo):
        return _render_proveedor_form(
            request,
            current_user,
            proveedor=proveedor,
            error="No puedes vincular una sucursal interna.",
            placas_text=placas,
            clientes=clientes_list,
            linked_cliente_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            permite_ventas_selected=permite_ventas_selected,
            status_code=400,
        )
    if linked_cliente and linked_cliente.sucursal_id != sucursal_id_selected:
        return _render_proveedor_form(
            request,
            current_user,
            proveedor=proveedor,
            error="El cliente vinculado debe pertenecer a la misma sucursal.",
            placas_text=placas,
            clientes=clientes_list,
            linked_cliente_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            permite_ventas_selected=permite_ventas_selected,
            status_code=400,
        )

    if not nombre_completo:
        return _render_proveedor_form(
            request,
            current_user,
            proveedor=proveedor,
            error="El nombre del proveedor es obligatorio.",
            placas_text=placas,
            clientes=clientes_list,
            linked_cliente_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            permite_ventas_selected=permite_ventas_selected,
            status_code=400,
        )

    conflict = _placas_conflict(db, placas_list, ProveedorPlaca, "proveedor_id", proveedor.id)
    if conflict:
        return _render_proveedor_form(
            request,
            current_user,
            proveedor=proveedor,
            error=conflict,
            placas_text=placas,
            clientes=clientes_list,
            linked_cliente_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            status_code=400,
        )

    proveedor.nombre_completo = nombre_completo
    proveedor.sucursal_id = sucursal_id_selected
    proveedor.telefono = telefono or None
    proveedor.correo_electronico = correo_electronico or None
    proveedor.placas = placas_list[0] if placas_list else None
    proveedor.activo = bool(activo)
    proveedor.permite_ventas = permite_ventas_selected

    _set_proveedor_placas(db, proveedor, placas_list)
    if linked_cliente:
        try:
            _link_cliente_proveedor(db, cliente=linked_cliente, proveedor=proveedor)
        except ValueError as exc:
            db.rollback()
            return _render_proveedor_form(
                request,
                current_user,
                proveedor=proveedor,
                error=str(exc),
                placas_text=placas,
                clientes=clientes_list,
                linked_cliente_id=linked_id,
                sucursales=sucursales,
                sucursal_id_selected=sucursal_id_selected,
                permite_ventas_selected=permite_ventas_selected,
                status_code=400,
            )
    db.commit()

    return RedirectResponse(url="/web/admin/proveedores", status_code=303)

@router.get("/proveedores/{proveedor_id}/record")
async def proveedor_record(
    proveedor_id: int,
    request: Request,
    q: str | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
):
    proveedor = db.get(Proveedor, proveedor_id)
    if not proveedor:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_partner_access(proveedor, allowed_suc_ids)
    ajuste_ok = request.query_params.get("ajuste") == "1"
    link_ok = request.query_params.get("link_ok") == "1"
    link_msg = (request.query_params.get("link_msg") or "").strip() or None
    link_warn = (request.query_params.get("link_warn") or "").strip() or None
    link_error = (request.query_params.get("link_error") or "").strip() or None
    attendance_from_raw = (request.query_params.get("attendance_from") or "").strip()
    attendance_to_raw = (request.query_params.get("attendance_to") or "").strip()
    attendance_from = None
    attendance_to = None
    attendance_error = None
    if attendance_from_raw:
        try:
            attendance_from = datetime.strptime(attendance_from_raw, "%Y-%m-%d").date()
        except ValueError:
            attendance_error = "La fecha inicial de asistencias es invalida."
    if attendance_to_raw:
        try:
            attendance_to = datetime.strptime(attendance_to_raw, "%Y-%m-%d").date()
        except ValueError:
            attendance_error = "La fecha final de asistencias es invalida."
    if attendance_from and attendance_to and attendance_from > attendance_to:
        attendance_from, attendance_to = attendance_to, attendance_from
    context = _build_partner_record_context(
        request,
        db,
        current_user,
        partner_type="proveedor",
        partner=proveedor,
        q=q,
        ajuste_ok=ajuste_ok,
        link_ok=link_ok,
        link_msg=link_msg,
        link_warn=link_warn,
        link_error=link_error,
        attendance_from=attendance_from,
        attendance_to=attendance_to,
        attendance_error=attendance_error,
    )
    return templates.TemplateResponse("admin/partner_record.html", context)


def _partner_statement_response(
    *,
    request: Request,
    db: Session,
    current_user: dict,
    partner_type: str,
    partner: Cliente | Proveedor,
    export_format: str,
) -> StreamingResponse:
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_partner_access(partner, allowed_suc_ids)
    context = _build_partner_record_context(
        request,
        db,
        current_user,
        partner_type=partner_type,
        partner=partner,
        q=None,
    )
    report = _build_partner_statement_report(context)
    fmt = (export_format or "pdf").strip().lower()
    if fmt == "pdf":
        content, filename = partner_report_service.build_partner_statement_pdf(report)
        media_type = "application/pdf"
    elif fmt in {"xls", "xlsx", "excel"}:
        content, filename = partner_report_service.build_partner_statement_excel(report)
        media_type = "application/vnd.ms-excel"
    else:
        raise HTTPException(status_code=400, detail="Formato invalido. Usa pdf o excel.")
    return StreamingResponse(
        io.BytesIO(content),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get("/proveedores/{proveedor_id}/estado-cuenta")
async def proveedor_estado_cuenta_export(
    proveedor_id: int,
    request: Request,
    format: str = "pdf",
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
):
    proveedor = db.get(Proveedor, proveedor_id)
    if not proveedor:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado.")
    return _partner_statement_response(
        request=request,
        db=db,
        current_user=current_user,
        partner_type="proveedor",
        partner=proveedor,
        export_format=format,
    )


@router.get("/proveedores/{proveedor_id}/asistencias")
async def proveedor_asistencias_export(
    proveedor_id: int,
    request: Request,
    format: str = "pdf",
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
):
    proveedor = db.get(Proveedor, proveedor_id)
    if not proveedor:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_partner_access(proveedor, allowed_suc_ids)

    attendance_from_raw = (request.query_params.get("attendance_from") or "").strip()
    attendance_to_raw = (request.query_params.get("attendance_to") or "").strip()
    attendance_from = None
    attendance_to = None
    if attendance_from_raw:
        try:
            attendance_from = datetime.strptime(attendance_from_raw, "%Y-%m-%d").date()
        except ValueError:
            attendance_from = None
    if attendance_to_raw:
        try:
            attendance_to = datetime.strptime(attendance_to_raw, "%Y-%m-%d").date()
        except ValueError:
            attendance_to = None
    if attendance_from and attendance_to and attendance_from > attendance_to:
        attendance_from, attendance_to = attendance_to, attendance_from

    context = _build_partner_record_context(
        request,
        db,
        current_user,
        partner_type="proveedor",
        partner=proveedor,
        q=None,
        attendance_from=attendance_from,
        attendance_to=attendance_to,
    )
    report = _build_provider_attendance_report(context)
    fmt = (format or "pdf").strip().lower()
    if fmt == "pdf":
        content, filename = partner_report_service.build_provider_attendance_pdf(report)
        media_type = "application/pdf"
    elif fmt in {"xls", "xlsx", "excel"}:
        content, filename = partner_report_service.build_provider_attendance_excel(report)
        media_type = "application/vnd.ms-excel"
    else:
        raise HTTPException(status_code=400, detail="Formato invalido. Usa pdf o excel.")
    return StreamingResponse(
        io.BytesIO(content),
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/proveedores/{proveedor_id}/crear-cliente")
async def proveedor_crear_cliente(
    proveedor_id: int,
    request: Request,
    next_url: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    proveedor = db.get(Proveedor, proveedor_id)
    if not proveedor:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_partner_access(proveedor, allowed_suc_ids)
    redirect_base = _safe_next_admin_url(next_url, f"/web/admin/proveedores/{proveedor_id}/record")
    if _is_internal_partner_name(db, proveedor.nombre_completo):
        msg = "No se puede crear como cliente porque es una sucursal interna."
        return RedirectResponse(
            url=_append_query_params(redirect_base, link_error=msg),
            status_code=303,
        )

    counterpart_suggestion = _build_counterpart_suggestion_for_proveedor(db, proveedor)
    existing = counterpart_suggestion["candidate"] if counterpart_suggestion else None
    if existing and counterpart_suggestion.get("is_linked"):
        return RedirectResponse(
            url=_append_query_params(redirect_base, link_warn="Este proveedor ya esta relacionado como cliente."),
            status_code=303,
        )
    if existing and not counterpart_suggestion.get("can_link"):
        return RedirectResponse(
            url=_append_query_params(
                redirect_base,
                link_error=counterpart_suggestion.get("message") or "No se pudo vincular la contraparte sugerida.",
            ),
            status_code=303,
        )
    if existing:
        try:
            _link_cliente_proveedor(db, cliente=existing, proveedor=proveedor)
            db.commit()
        except ValueError as exc:
            db.rollback()
            return RedirectResponse(
                url=_append_query_params(redirect_base, link_error=str(exc)),
                status_code=303,
            )
        return RedirectResponse(
            url=_append_query_params(
                redirect_base,
                link_ok="1",
                link_msg="Cliente existente vinculado correctamente.",
            ),
            status_code=303,
        )

    try:
        cliente, placas_skipped = _create_cliente_from_proveedor(db, proveedor=proveedor)
        _link_cliente_proveedor(db, cliente=cliente, proveedor=proveedor)
        db.commit()
        db.refresh(cliente)
    except ValueError as exc:
        db.rollback()
        msg = str(exc)
        return RedirectResponse(
            url=_append_query_params(redirect_base, link_error=msg),
            status_code=303,
        )
    except IntegrityError:
        db.rollback()
        msg = "No se pudo crear el cliente. Revisa placas o datos duplicados."
        return RedirectResponse(
            url=_append_query_params(redirect_base, link_error=msg),
            status_code=303,
        )

    return RedirectResponse(
        url=_append_query_params(
            redirect_base,
            link_ok="1",
            link_msg="Cliente creado desde proveedor.",
            link_warn=f"Placas omitidas por duplicado: {', '.join(placas_skipped)}." if placas_skipped else "",
        ),
        status_code=303,
    )


@router.post("/proveedores/{proveedor_id}/ajuste-saldo")
async def proveedor_ajuste_saldo(
    proveedor_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    proveedor = db.get(Proveedor, proveedor_id)
    if not proveedor:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_partner_access(proveedor, allowed_suc_ids)

    form = await request.form()
    direction = (form.get("ajuste_direccion") or "").strip().lower()
    monto_raw = (form.get("ajuste_monto") or "").strip()
    comentario = (form.get("ajuste_comentario") or "").strip()
    form_state = {
        "ajuste_direccion": direction,
        "ajuste_monto": monto_raw,
        "ajuste_comentario": comentario,
    }

    def render_error(msg: str):
        context = _build_partner_record_context(
            request,
            db,
            current_user,
            partner_type="proveedor",
            partner=proveedor,
            q=None,
            ajuste_error=msg,
            form_state=form_state,
        )
        return templates.TemplateResponse("admin/partner_record.html", context, status_code=400)

    if not monto_raw:
        return render_error("Debes indicar el monto del ajuste.")
    try:
        monto_val = Decimal(str(monto_raw))
    except (InvalidOperation, TypeError):
        return render_error("El monto del ajuste es invalido.")
    if not comentario:
        return render_error("Debes indicar un comentario para el ajuste.")
    try:
        delta = _compute_partner_adjustment_delta(
            partner_type="proveedor",
            direction=direction,
            monto=monto_val,
        )
    except ValueError as exc:
        return render_error(str(exc))

    ajuste = AjusteSaldoPartner(
        partner_type="proveedor",
        partner_id=proveedor_id,
        sucursal_id=proveedor.sucursal_id,
        monto=delta,
        comentario=comentario,
        usuario_id=current_user.get("id"),
    )
    db.add(ajuste)
    db.commit()

    return RedirectResponse(
        url=f"/web/admin/proveedores/{proveedor_id}/record?ajuste=1",
        status_code=303,
    )

# ---------- CLIENTES ----------


@router.get("/clientes")
async def clientes_list(
    request: Request,
    q: str | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
):
    params = request.query_params
    delete_ok = params.get("deleted") == "1"
    delete_error = (params.get("delete_error") or "").strip()
    query = db.query(Cliente)
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    sucursal_id = None
    sucursal_error = None
    if params.get("sucursal_id"):
        sucursal_id, sucursal_error = _selected_sucursal_from_request(
            db,
            raw_value=params.get("sucursal_id"),
            allowed_suc_ids=allowed_suc_ids,
        )
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    query = _apply_sucursal_filter(query, allowed_suc_ids, sucursal_id, Cliente.sucursal_id)

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
    clientes_view = []
    for cliente in clientes:
        linked_proveedor = None
        if not _is_internal_partner_name(db, cliente.nombre_completo):
            linked_proveedor = _get_formally_linked_proveedor(db, cliente)
            if linked_proveedor and _is_internal_partner_name(db, linked_proveedor.nombre_completo):
                linked_proveedor = None

        ventas_query = db.query(Nota).filter(
            Nota.cliente_id == cliente.id,
            Nota.tipo_operacion == TipoOperacion.venta,
        )
        ventas_query = _apply_sucursal_filter(
            ventas_query,
            allowed_suc_ids,
            sucursal_id,
            Nota.sucursal_id,
        )
        ventas = ventas_query.order_by(Nota.created_at.desc()).all()

        compras = []
        if linked_proveedor:
            compras_query = db.query(Nota).filter(
                Nota.proveedor_id == linked_proveedor.id,
                Nota.tipo_operacion == TipoOperacion.compra,
            )
            compras_query = _apply_sucursal_filter(
                compras_query,
                allowed_suc_ids,
                sucursal_id,
                Nota.sucursal_id,
            )
            compras = compras_query.order_by(Nota.created_at.desc()).all()
        note_adjustment_totals = _get_note_balance_adjustment_totals_map(
            db,
            [nota.id for nota in (compras + ventas) if nota.id],
        )

        ajustes_cliente = _get_partner_adjustments_total(
            db,
            partner_type="cliente",
            partner_id=cliente.id,
            allowed_suc_ids=allowed_suc_ids,
            sucursal_id=sucursal_id,
        )
        ajustes_proveedor = Decimal("0")
        if linked_proveedor:
            ajustes_proveedor = _get_partner_adjustments_total(
                db,
                partner_type="proveedor",
                partner_id=linked_proveedor.id,
                allowed_suc_ids=allowed_suc_ids,
                sucursal_id=sucursal_id,
            )

        unified_summary = _aggregate_unified_partner_summary(
            compras=compras,
            ventas=ventas,
            ajustes_proveedor=ajustes_proveedor,
            ajustes_cliente=ajustes_cliente,
            note_adjustment_totals=note_adjustment_totals,
        )
        clientes_view.append(
            {
                "cliente": cliente,
                "linked_proveedor": linked_proveedor,
                "saldo_neto": unified_summary["saldo_neto"],
            }
        )

    return templates.TemplateResponse(
        "admin/clientes_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "clientes": clientes_view,
            "q": q or "",
            "sucursales": sucursales,
            "sucursal_id": sucursal_id,
            "sucursal_error": sucursal_error,
            "delete_ok": delete_ok,
            "delete_error": delete_error,
        },
    )


@router.post("/clientes/{cliente_id}/eliminar")
async def cliente_delete(
    cliente_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")

    tiene_notas = db.query(Nota.id).filter(Nota.cliente_id == cliente_id).first()
    tiene_cuentas = db.query(Cuenta.id).filter(Cuenta.cliente_id == cliente_id).first()
    tiene_ajustes = (
        db.query(AjusteSaldoPartner.id)
        .filter(
            AjusteSaldoPartner.partner_type == "cliente",
            AjusteSaldoPartner.partner_id == cliente_id,
        )
        .first()
    )
    if tiene_notas or tiene_cuentas or tiene_ajustes:
        reasons = []
        if tiene_notas:
            reasons.append("notas")
        if tiene_cuentas:
            reasons.append("cuentas")
        if tiene_ajustes:
            reasons.append("ajustes")
        msg = f"No se puede eliminar: tiene {', '.join(reasons)} asociados."
        return RedirectResponse(url=f"/web/admin/clientes?{urlencode({'delete_error': msg})}", status_code=303)

    if cliente.linked_proveedor_id:
        proveedor = db.get(Proveedor, cliente.linked_proveedor_id)
        if proveedor and proveedor.linked_cliente_id == cliente.id:
            proveedor.linked_cliente_id = None
            db.add(proveedor)

    db.delete(cliente)
    db.commit()
    return RedirectResponse(url="/web/admin/clientes?deleted=1", status_code=303)


@router.get("/clientes/nuevo")
async def cliente_new_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids, sucursales = _partner_form_sucursales(db, current_user)
    default_sucursal_id = sucursales[0].id if sucursales else None
    proveedores_list = _list_linkable_proveedores(
        db,
        allowed_suc_ids=allowed_suc_ids,
        sucursal_id=default_sucursal_id,
    )
    return _render_cliente_form(
        request,
        current_user,
        cliente=None,
        error=None,
        placas_text="",
        proveedores=proveedores_list,
        linked_proveedor_id=None,
        sucursales=sucursales,
        sucursal_id_selected=default_sucursal_id,
    )


@router.post("/clientes/nuevo")
async def cliente_new_post(
    request: Request,
    nombre_completo: str = Form(...),
    telefono: str = Form(""),
    correo_electronico: str = Form(""),
    placas: str = Form(""),
    linked_proveedor_id: str | None = Form(None),
    sucursal_id: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids, sucursales = _partner_form_sucursales(db, current_user)
    default_sucursal_id = sucursales[0].id if sucursales else None
    sucursal_id_selected, sucursal_error = _selected_sucursal_from_request(
        db,
        raw_value=sucursal_id,
        allowed_suc_ids=allowed_suc_ids,
        default_id=default_sucursal_id,
    )
    proveedores_list = _list_linkable_proveedores(
        db,
        allowed_suc_ids=allowed_suc_ids,
        sucursal_id=sucursal_id_selected,
    )

    nombre_completo = nombre_completo.strip()
    telefono = telefono.strip()
    correo_electronico = correo_electronico.strip()
    placas_list = _parse_placas(placas)
    linked_id = _parse_optional_int(linked_proveedor_id)
    linked_proveedor = db.get(Proveedor, linked_id) if linked_id else None

    if sucursal_error:
        return _render_cliente_form(
            request,
            current_user,
            cliente=None,
            error=sucursal_error,
            placas_text=placas,
            proveedores=proveedores_list,
            linked_proveedor_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            status_code=400,
        )
    if not sucursal_id_selected:
        return _render_cliente_form(
            request,
            current_user,
            cliente=None,
            error="Debes seleccionar una sucursal.",
            placas_text=placas,
            proveedores=proveedores_list,
            linked_proveedor_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            status_code=400,
        )

    if linked_id and not linked_proveedor:
        return _render_cliente_form(
            request,
            current_user,
            cliente=None,
            error="Proveedor vinculado no encontrado.",
            placas_text=placas,
            proveedores=proveedores_list,
            linked_proveedor_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            status_code=400,
        )
    if linked_proveedor and _is_internal_partner_name(db, linked_proveedor.nombre_completo):
        return _render_cliente_form(
            request,
            current_user,
            cliente=None,
            error="No puedes vincular una sucursal interna.",
            placas_text=placas,
            proveedores=proveedores_list,
            linked_proveedor_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            status_code=400,
        )
    if linked_proveedor and linked_proveedor.sucursal_id != sucursal_id_selected:
        return _render_cliente_form(
            request,
            current_user,
            cliente=None,
            error="El proveedor vinculado debe pertenecer a la misma sucursal.",
            placas_text=placas,
            proveedores=proveedores_list,
            linked_proveedor_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            status_code=400,
        )

    if not nombre_completo:
        return _render_cliente_form(
            request,
            current_user,
            cliente=None,
            error="El nombre del cliente es obligatorio.",
            placas_text=placas,
            proveedores=proveedores_list,
            linked_proveedor_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            status_code=400,
        )

    conflict = _placas_conflict(db, placas_list, ClientePlaca, "cliente_id", None)
    if conflict:
        return _render_cliente_form(
            request,
            current_user,
            cliente=None,
            error=conflict,
            placas_text=placas,
            proveedores=proveedores_list,
            linked_proveedor_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            status_code=400,
        )

    cliente = Cliente(
        nombre_completo=nombre_completo,
        sucursal_id=sucursal_id_selected,
        telefono=telefono or None,
        correo_electronico=correo_electronico or None,
        placas=placas_list[0] if placas_list else None,
        activo=True,
    )
    db.add(cliente)
    db.flush()
    if linked_proveedor:
        try:
            _link_cliente_proveedor(db, cliente=cliente, proveedor=linked_proveedor)
        except ValueError as exc:
            db.rollback()
            return _render_cliente_form(
                request,
                current_user,
                cliente=None,
                error=str(exc),
                placas_text=placas,
                proveedores=proveedores_list,
                linked_proveedor_id=linked_id,
                sucursales=sucursales,
                sucursal_id_selected=sucursal_id_selected,
                status_code=400,
            )
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

    allowed_suc_ids, sucursales = _partner_form_sucursales(db, current_user)
    _ensure_partner_access(cliente, allowed_suc_ids)
    proveedores_list = _list_linkable_proveedores(
        db,
        allowed_suc_ids=allowed_suc_ids,
        sucursal_id=cliente.sucursal_id,
    )
    linked_proveedor = _get_formally_linked_proveedor(db, cliente)
    if linked_proveedor and linked_proveedor not in proveedores_list:
        proveedores_list.append(linked_proveedor)
    if linked_proveedor:
        proveedores_list = sorted(proveedores_list, key=lambda p: p.nombre_completo or "")
    counterpart_suggestion = _build_counterpart_suggestion_for_cliente(db, cliente)
    link_ok = request.query_params.get("link_ok") == "1"
    link_msg = (request.query_params.get("link_msg") or "").strip() or None
    link_warn = (request.query_params.get("link_warn") or "").strip() or None
    link_error = (request.query_params.get("link_error") or "").strip() or None

    return _render_cliente_form(
        request,
        current_user,
        cliente=cliente,
        error=None,
        placas_text="\n".join([pl.placa for pl in cliente.placas_rel]) if cliente.placas_rel else (cliente.placas or ""),
        proveedores=proveedores_list,
        linked_proveedor_id=linked_proveedor.id if linked_proveedor else cliente.linked_proveedor_id,
        sucursales=sucursales,
        sucursal_id_selected=cliente.sucursal_id,
        counterpart_suggestion=counterpart_suggestion,
        link_ok=link_ok,
        link_msg=link_msg,
        link_warn=link_warn,
        link_error=link_error,
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
    linked_proveedor_id: str | None = Form(None),
    sucursal_id: str | None = Form(None),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    cliente = db.query(Cliente).get(cliente_id)
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")

    allowed_suc_ids, sucursales = _partner_form_sucursales(db, current_user)
    _ensure_partner_access(cliente, allowed_suc_ids)
    sucursal_id_selected, sucursal_error = _selected_sucursal_from_request(
        db,
        raw_value=sucursal_id,
        allowed_suc_ids=allowed_suc_ids,
        default_id=cliente.sucursal_id,
    )
    proveedores_list = _list_linkable_proveedores(
        db,
        allowed_suc_ids=allowed_suc_ids,
        sucursal_id=sucursal_id_selected,
    )

    nombre_completo = nombre_completo.strip()
    telefono = telefono.strip()
    correo_electronico = correo_electronico.strip()
    placas_list = _parse_placas(placas)
    existing_linked_proveedor = _get_formally_linked_proveedor(db, cliente)
    linked_id = existing_linked_proveedor.id if existing_linked_proveedor else _parse_optional_int(linked_proveedor_id)
    linked_proveedor = db.get(Proveedor, linked_id) if linked_id else None

    if sucursal_error:
        return _render_cliente_form(
            request,
            current_user,
            cliente=cliente,
            error=sucursal_error,
            placas_text=placas,
            proveedores=proveedores_list,
            linked_proveedor_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            status_code=400,
        )
    if not sucursal_id_selected:
        return _render_cliente_form(
            request,
            current_user,
            cliente=cliente,
            error="Debes seleccionar una sucursal.",
            placas_text=placas,
            proveedores=proveedores_list,
            linked_proveedor_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            status_code=400,
        )

    if linked_id and not linked_proveedor:
        return _render_cliente_form(
            request,
            current_user,
            cliente=cliente,
            error="Proveedor vinculado no encontrado.",
            placas_text=placas,
            proveedores=proveedores_list,
            linked_proveedor_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            status_code=400,
        )
    if linked_proveedor and _is_internal_partner_name(db, linked_proveedor.nombre_completo):
        return _render_cliente_form(
            request,
            current_user,
            cliente=cliente,
            error="No puedes vincular una sucursal interna.",
            placas_text=placas,
            proveedores=proveedores_list,
            linked_proveedor_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            status_code=400,
        )
    if linked_proveedor and linked_proveedor.sucursal_id != sucursal_id_selected:
        return _render_cliente_form(
            request,
            current_user,
            cliente=cliente,
            error="El proveedor vinculado debe pertenecer a la misma sucursal.",
            placas_text=placas,
            proveedores=proveedores_list,
            linked_proveedor_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            status_code=400,
        )

    if not nombre_completo:
        return _render_cliente_form(
            request,
            current_user,
            cliente=cliente,
            error="El nombre del cliente es obligatorio.",
            placas_text=placas,
            proveedores=proveedores_list,
            linked_proveedor_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            status_code=400,
        )

    conflict = _placas_conflict(db, placas_list, ClientePlaca, "cliente_id", cliente.id)
    if conflict:
        return _render_cliente_form(
            request,
            current_user,
            cliente=cliente,
            error=conflict,
            placas_text=placas,
            proveedores=proveedores_list,
            linked_proveedor_id=linked_id,
            sucursales=sucursales,
            sucursal_id_selected=sucursal_id_selected,
            status_code=400,
        )

    cliente.nombre_completo = nombre_completo
    cliente.sucursal_id = sucursal_id_selected
    cliente.telefono = telefono or None
    cliente.correo_electronico = correo_electronico or None
    cliente.placas = placas_list[0] if placas_list else None
    cliente.activo = bool(activo)

    _set_cliente_placas(db, cliente, placas_list)
    if linked_proveedor:
        try:
            _link_cliente_proveedor(db, cliente=cliente, proveedor=linked_proveedor)
        except ValueError as exc:
            db.rollback()
            return _render_cliente_form(
                request,
                current_user,
                cliente=cliente,
                error=str(exc),
                placas_text=placas,
                proveedores=proveedores_list,
                linked_proveedor_id=linked_id,
                sucursales=sucursales,
                sucursal_id_selected=sucursal_id_selected,
                status_code=400,
            )
    db.commit()

    return RedirectResponse(url="/web/admin/clientes", status_code=303)

@router.get("/clientes/{cliente_id}/record")
async def cliente_record(
    cliente_id: int,
    request: Request,
    q: str | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
):
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_partner_access(cliente, allowed_suc_ids)
    ajuste_ok = request.query_params.get("ajuste") == "1"
    link_ok = request.query_params.get("link_ok") == "1"
    link_msg = (request.query_params.get("link_msg") or "").strip() or None
    link_warn = (request.query_params.get("link_warn") or "").strip() or None
    link_error = (request.query_params.get("link_error") or "").strip() or None
    context = _build_partner_record_context(
        request,
        db,
        current_user,
        partner_type="cliente",
        partner=cliente,
        q=q,
        ajuste_ok=ajuste_ok,
        link_ok=link_ok,
        link_msg=link_msg,
        link_warn=link_warn,
        link_error=link_error,
    )
    return templates.TemplateResponse("admin/partner_record.html", context)


@router.get("/clientes/{cliente_id}/estado-cuenta")
async def cliente_estado_cuenta_export(
    cliente_id: int,
    request: Request,
    format: str = "pdf",
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
):
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")
    return _partner_statement_response(
        request=request,
        db=db,
        current_user=current_user,
        partner_type="cliente",
        partner=cliente,
        export_format=format,
    )


@router.post("/clientes/{cliente_id}/crear-proveedor")
async def cliente_crear_proveedor(
    cliente_id: int,
    request: Request,
    next_url: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_partner_access(cliente, allowed_suc_ids)
    redirect_base = _safe_next_admin_url(next_url, f"/web/admin/clientes/{cliente_id}/record")
    if _is_internal_partner_name(db, cliente.nombre_completo):
        msg = "No se puede crear como proveedor porque es una sucursal interna."
        return RedirectResponse(
            url=_append_query_params(redirect_base, link_error=msg),
            status_code=303,
        )

    counterpart_suggestion = _build_counterpart_suggestion_for_cliente(db, cliente)
    existing = counterpart_suggestion["candidate"] if counterpart_suggestion else None
    if existing and counterpart_suggestion.get("is_linked"):
        return RedirectResponse(
            url=_append_query_params(redirect_base, link_warn="Este cliente ya esta relacionado como proveedor."),
            status_code=303,
        )
    if existing and not counterpart_suggestion.get("can_link"):
        return RedirectResponse(
            url=_append_query_params(
                redirect_base,
                link_error=counterpart_suggestion.get("message") or "No se pudo vincular la contraparte sugerida.",
            ),
            status_code=303,
        )
    if existing:
        try:
            _link_cliente_proveedor(db, cliente=cliente, proveedor=existing)
            db.commit()
        except ValueError as exc:
            db.rollback()
            return RedirectResponse(
                url=_append_query_params(redirect_base, link_error=str(exc)),
                status_code=303,
            )
        return RedirectResponse(
            url=_append_query_params(
                redirect_base,
                link_ok="1",
                link_msg="Proveedor existente vinculado correctamente.",
            ),
            status_code=303,
        )

    try:
        proveedor, placas_skipped = _create_proveedor_from_cliente(db, cliente=cliente)
        _link_cliente_proveedor(db, cliente=cliente, proveedor=proveedor)
        db.commit()
        db.refresh(proveedor)
    except ValueError as exc:
        db.rollback()
        msg = str(exc)
        return RedirectResponse(
            url=_append_query_params(redirect_base, link_error=msg),
            status_code=303,
        )
    except IntegrityError:
        db.rollback()
        msg = "No se pudo crear el proveedor. Revisa placas o datos duplicados."
        return RedirectResponse(
            url=_append_query_params(redirect_base, link_error=msg),
            status_code=303,
        )

    return RedirectResponse(
        url=_append_query_params(
            redirect_base,
            link_ok="1",
            link_msg="Proveedor creado desde cliente.",
            link_warn=f"Placas omitidas por duplicado: {', '.join(placas_skipped)}." if placas_skipped else "",
        ),
        status_code=303,
    )


@router.post("/clientes/{cliente_id}/ajuste-saldo")
async def cliente_ajuste_saldo(
    cliente_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    cliente = db.get(Cliente, cliente_id)
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_partner_access(cliente, allowed_suc_ids)

    form = await request.form()
    direction = (form.get("ajuste_direccion") or "").strip().lower()
    monto_raw = (form.get("ajuste_monto") or "").strip()
    comentario = (form.get("ajuste_comentario") or "").strip()
    form_state = {
        "ajuste_direccion": direction,
        "ajuste_monto": monto_raw,
        "ajuste_comentario": comentario,
    }

    def render_error(msg: str):
        context = _build_partner_record_context(
            request,
            db,
            current_user,
            partner_type="cliente",
            partner=cliente,
            q=None,
            ajuste_error=msg,
            form_state=form_state,
        )
        return templates.TemplateResponse("admin/partner_record.html", context, status_code=400)

    if not monto_raw:
        return render_error("Debes indicar el monto del ajuste.")
    try:
        monto_val = Decimal(str(monto_raw))
    except (InvalidOperation, TypeError):
        return render_error("El monto del ajuste es invalido.")
    if not comentario:
        return render_error("Debes indicar un comentario para el ajuste.")
    try:
        delta = _compute_partner_adjustment_delta(
            partner_type="cliente",
            direction=direction,
            monto=monto_val,
        )
    except ValueError as exc:
        return render_error(str(exc))

    ajuste = AjusteSaldoPartner(
        partner_type="cliente",
        partner_id=cliente_id,
        sucursal_id=cliente.sucursal_id,
        monto=delta,
        comentario=comentario,
        usuario_id=current_user.get("id"),
    )
    db.add(ajuste)
    db.commit()

    return RedirectResponse(
        url=f"/web/admin/clientes/{cliente_id}/record?ajuste=1",
        status_code=303,
    )


# ---------- COMISIONARIOS ----------


@router.get("/comisionarios")
async def comisionarios_list(
    request: Request,
    q: str | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    params = request.query_params
    delete_ok = params.get("deleted") == "1"
    delete_error = (params.get("delete_error") or "").strip()
    sucursal_id = None
    if params.get("sucursal_id"):
        try:
            sucursal_id = int(params.get("sucursal_id"))
        except ValueError:
            sucursal_id = None
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    query = db.query(Comisionario)
    query = _apply_sucursal_filter(query, allowed_suc_ids, sucursal_id, Comisionario.sucursal_id)
    if q:
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Comisionario.nombre_completo.ilike(term),
                Comisionario.telefono.ilike(term),
                Comisionario.correo_electronico.ilike(term),
            )
        )
    comisionarios = query.order_by(Comisionario.nombre_completo).all()
    return templates.TemplateResponse(
        "admin/comisionarios_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "comisionarios": comisionarios,
            "sucursales": sucursales,
            "q": q or "",
            "sucursal_id": sucursal_id,
            "delete_ok": delete_ok,
            "delete_error": delete_error,
        },
    )


@router.post("/comisionarios/{comisionario_id}/eliminar")
async def comisionario_delete(
    comisionario_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    comisionario = db.get(Comisionario, comisionario_id)
    if not comisionario:
        raise HTTPException(status_code=404, detail="Comisionario no encontrado.")

    tiene_notas = db.query(ComisionarioNota.id).filter(ComisionarioNota.comisionario_id == comisionario_id).first()
    tiene_cuentas = db.query(Cuenta.id).filter(Cuenta.comisionario_id == comisionario_id).first()
    if tiene_notas or tiene_cuentas:
        reasons = []
        if tiene_notas:
            reasons.append("notas")
        if tiene_cuentas:
            reasons.append("cuentas")
        msg = f"No se puede eliminar: tiene {', '.join(reasons)} asociados."
        return RedirectResponse(url=f"/web/admin/comisionarios?{urlencode({'delete_error': msg})}", status_code=303)

    db.delete(comisionario)
    db.commit()
    return RedirectResponse(url="/web/admin/comisionarios?deleted=1", status_code=303)


def _render_comisionario_form(
    request: Request,
    db: Session,
    current_user: dict,
    *,
    comisionario: Comisionario | None,
    error: str | None = None,
    form_data: dict | None = None,
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    sucursales = _active_sucursales(db)
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    form_data = form_data or {}
    if "sucursal_id" not in form_data:
        if comisionario and comisionario.sucursal_id:
            form_data["sucursal_id"] = str(comisionario.sucursal_id)
        elif len(sucursales) == 1:
            form_data["sucursal_id"] = str(sucursales[0].id)
        else:
            form_data["sucursal_id"] = ""
    return templates.TemplateResponse(
        "admin/comisionario_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "comisionario": comisionario,
            "sucursales": sucursales,
            "error": error,
            "form_data": form_data,
        },
        status_code=400 if error else 200,
    )


@router.get("/comisionarios/nuevo")
async def comisionario_new_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    return _render_comisionario_form(request, db, current_user, comisionario=None)


@router.post("/comisionarios/nuevo")
async def comisionario_new_post(
    request: Request,
    nombre_completo: str = Form(...),
    telefono: str = Form(""),
    correo_electronico: str = Form(""),
    sucursal_id: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nombre_completo = nombre_completo.strip()
    telefono = telefono.strip()
    correo_electronico = correo_electronico.strip()
    sucursal_raw = sucursal_id.strip()
    form_data = {
        "nombre_completo": nombre_completo,
        "telefono": telefono,
        "correo_electronico": correo_electronico,
        "sucursal_id": sucursal_raw,
    }
    if not nombre_completo:
        return _render_comisionario_form(
            request,
            db,
            current_user,
            comisionario=None,
            error="El nombre del comisionario es obligatorio.",
            form_data=form_data,
        )
    if not sucursal_raw:
        return _render_comisionario_form(
            request,
            db,
            current_user,
            comisionario=None,
            error="Selecciona la sucursal del comisionario.",
            form_data=form_data,
        )
    try:
        sucursal_id_int = int(sucursal_raw)
    except ValueError:
        return _render_comisionario_form(
            request,
            db,
            current_user,
            comisionario=None,
            error="Sucursal invalida.",
            form_data=form_data,
        )
    if not db.get(Sucursal, sucursal_id_int):
        return _render_comisionario_form(
            request,
            db,
            current_user,
            comisionario=None,
            error="Sucursal invalida.",
            form_data=form_data,
        )
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    if allowed_suc_ids is not None and sucursal_id_int not in allowed_suc_ids:
        return _render_comisionario_form(
            request,
            db,
            current_user,
            comisionario=None,
            error="Sucursal no autorizada.",
            form_data=form_data,
        )

    comisionario = Comisionario(
        nombre_completo=nombre_completo,
        telefono=telefono or None,
        correo_electronico=correo_electronico or None,
        sucursal_id=sucursal_id_int,
        activo=True,
    )
    db.add(comisionario)
    db.commit()
    return RedirectResponse(url="/web/admin/comisionarios", status_code=303)


@router.get("/comisionarios/{comisionario_id}/editar")
async def comisionario_edit_get(
    comisionario_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    comisionario = db.get(Comisionario, comisionario_id)
    if not comisionario:
        raise HTTPException(status_code=404, detail="Comisionario no encontrado.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    if allowed_suc_ids is not None and comisionario.sucursal_id not in allowed_suc_ids:
        raise HTTPException(status_code=403, detail="Sucursal no autorizada.")
    return _render_comisionario_form(request, db, current_user, comisionario=comisionario)


@router.post("/comisionarios/{comisionario_id}/editar")
async def comisionario_edit_post(
    comisionario_id: int,
    request: Request,
    nombre_completo: str = Form(...),
    telefono: str = Form(""),
    correo_electronico: str = Form(""),
    sucursal_id: str = Form(""),
    activo: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    comisionario = db.get(Comisionario, comisionario_id)
    if not comisionario:
        raise HTTPException(status_code=404, detail="Comisionario no encontrado.")
    nombre_completo = nombre_completo.strip()
    telefono = telefono.strip()
    correo_electronico = correo_electronico.strip()
    sucursal_raw = sucursal_id.strip()
    form_data = {
        "nombre_completo": nombre_completo,
        "telefono": telefono,
        "correo_electronico": correo_electronico,
        "sucursal_id": sucursal_raw,
        "activo": bool(activo),
    }
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    if allowed_suc_ids is not None and comisionario.sucursal_id not in allowed_suc_ids:
        raise HTTPException(status_code=403, detail="Sucursal no autorizada.")
    if not nombre_completo:
        return _render_comisionario_form(
            request,
            db,
            current_user,
            comisionario=comisionario,
            error="El nombre del comisionario es obligatorio.",
            form_data=form_data,
        )
    if not sucursal_raw:
        return _render_comisionario_form(
            request,
            db,
            current_user,
            comisionario=comisionario,
            error="Selecciona la sucursal del comisionario.",
            form_data=form_data,
        )
    try:
        sucursal_id_int = int(sucursal_raw)
    except ValueError:
        return _render_comisionario_form(
            request,
            db,
            current_user,
            comisionario=comisionario,
            error="Sucursal invalida.",
            form_data=form_data,
        )
    if not db.get(Sucursal, sucursal_id_int):
        return _render_comisionario_form(
            request,
            db,
            current_user,
            comisionario=comisionario,
            error="Sucursal invalida.",
            form_data=form_data,
        )
    if allowed_suc_ids is not None and sucursal_id_int not in allowed_suc_ids:
        return _render_comisionario_form(
            request,
            db,
            current_user,
            comisionario=comisionario,
            error="Sucursal no autorizada.",
            form_data=form_data,
        )

    comisionario.nombre_completo = nombre_completo
    comisionario.telefono = telefono or None
    comisionario.correo_electronico = correo_electronico or None
    comisionario.sucursal_id = sucursal_id_int
    comisionario.activo = bool(activo)
    comisionario.updated_at = datetime.utcnow()
    db.add(comisionario)
    db.commit()
    return RedirectResponse(url="/web/admin/comisionarios", status_code=303)


@router.get("/comisionarios/{comisionario_id}/record")
async def comisionario_record(
    comisionario_id: int,
    request: Request,
    q: str | None = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    comisionario = db.get(Comisionario, comisionario_id)
    if not comisionario:
        raise HTTPException(status_code=404, detail="Comisionario no encontrado.")

    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    if allowed_suc_ids is not None and comisionario.sucursal_id not in allowed_suc_ids:
        raise HTTPException(status_code=403, detail="Sucursal no autorizada.")
    notas_query = db.query(ComisionarioNota).filter(ComisionarioNota.comisionario_id == comisionario_id)
    if allowed_suc_ids:
        notas_query = notas_query.filter(
            or_(
                ComisionarioNota.sucursal_id.in_(allowed_suc_ids),
                ComisionarioNota.sucursal_id.is_(None),
            )
        )
    notas = notas_query.order_by(ComisionarioNota.created_at.desc()).all()
    notas_filtradas = _filter_comisionario_notas(notas, q)

    pagos_query = (
        db.query(ComisionarioPago)
        .join(ComisionarioNota, ComisionarioPago.nota_id == ComisionarioNota.id)
        .filter(ComisionarioNota.comisionario_id == comisionario_id)
    )
    if allowed_suc_ids:
        pagos_query = pagos_query.filter(
            or_(
                ComisionarioNota.sucursal_id.in_(allowed_suc_ids),
                ComisionarioNota.sucursal_id.is_(None),
            )
        )
    pagos = pagos_query.order_by(ComisionarioPago.created_at.desc()).all()

    summary = _build_comisionario_summary(notas)
    ledger_rows = _build_comisionario_ledger(notas, pagos)
    ledger_final = ledger_rows[-1]["saldo"] if ledger_rows else Decimal("0")

    suc_query = db.query(Sucursal)
    if allowed_suc_ids:
        suc_query = suc_query.filter(Sucursal.id.in_(allowed_suc_ids))
    sucursales = {s.id: s for s in suc_query.all()}

    # Punto 6 (fase 2): formulario de pago que se aplica a las notas más antiguas.
    cuentas_comisionario = (
        db.query(Cuenta)
        .filter(Cuenta.activo.is_(True), Cuenta.comisionario_id == comisionario.id)
        .order_by(Cuenta.nombre)
        .all()
    )
    cuentas_scrap360 = db.query(CuentaScrap360).filter(CuentaScrap360.activo.is_(True)).all()
    if comisionario.sucursal_id:
        cuentas_scrap360 = [
            c for c in cuentas_scrap360
            if not c.sucursales or comisionario.sucursal_id in {s.id for s in c.sucursales}
        ]

    return templates.TemplateResponse(
        "admin/comisionario_record.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "comisionario": comisionario,
            "record_rows": notas_filtradas,
            "record_total_count": len(notas),
            "record_filtered_count": len(notas_filtradas),
            "summary": summary,
            "ledger_rows": ledger_rows,
            "ledger_final": ledger_final,
            "pagos": pagos,
            "sucursales": sucursales,
            "cuentas": cuentas_comisionario,
            "cuentas_scrap360": cuentas_scrap360,
            "q": q or "",
        },
    )


@router.post("/comisionarios/{comisionario_id}/pago")
async def comisionario_pago_fifo(
    comisionario_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    """Punto 6 (fase 2): un solo pago que se aplica a las notas más antiguas."""
    comisionario = db.get(Comisionario, comisionario_id)
    if not comisionario:
        raise HTTPException(status_code=404, detail="Comisionario no encontrado.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    if allowed_suc_ids is not None and comisionario.sucursal_id not in allowed_suc_ids:
        raise HTTPException(status_code=403, detail="Sucursal no autorizada.")

    form = await request.form()
    record_url = f"/web/admin/comisionarios/{comisionario_id}/record"

    def _error(msg: str):
        return RedirectResponse(
            url=_append_query_params(record_url, pago_error=msg) + "#comisionario-registrar-pago",
            status_code=303,
        )

    monto_raw = (form.get("monto") or "").strip()
    if not monto_raw:
        return _error("Debes indicar el monto pagado.")
    try:
        monto_val = Decimal(str(monto_raw))
    except (InvalidOperation, TypeError):
        return _error("El monto pagado es inválido.")

    cuenta_scrap360_raw = (form.get("cuenta_scrap360_id") or "").strip()
    cuenta_scrap360_id = None
    if cuenta_scrap360_raw:
        try:
            cuenta_scrap360_id = int(cuenta_scrap360_raw)
        except (TypeError, ValueError):
            return _error("La cuenta Scrap360 es inválida.")

    try:
        pagos = comision_service.pay_comisionario_fifo(
            db,
            comisionario_id=comisionario_id,
            monto=monto_val,
            usuario_id=current_user.get("id"),
            metodo_pago=(form.get("metodo_pago") or "").strip().lower() or None,
            cuenta_financiera=(form.get("cuenta_financiera") or "").strip() or None,
            cuenta_scrap360_id=cuenta_scrap360_id,
            comentario=(form.get("comentario") or "").strip() or None,
        )
    except ValueError as exc:
        return _error(str(exc))

    return RedirectResponse(
        url=_append_query_params(
            record_url,
            pago_fifo=str(len(pagos)),
            pago_monto=f"{monto_val:,.2f}",
        )
        + "#comisionario-pagos",
        status_code=303,
    )


@router.get("/comisionarios/notas")
async def comisionario_notas_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    params = request.query_params
    q = (params.get("q") or "").strip()
    comisionario_id = None
    if params.get("comisionario_id"):
        try:
            comisionario_id = int(params.get("comisionario_id"))
        except ValueError:
            comisionario_id = None

    query = db.query(ComisionarioNota)
    if comisionario_id:
        query = query.filter(ComisionarioNota.comisionario_id == comisionario_id)

    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    if allowed_suc_ids:
        query = query.filter(
            or_(
                ComisionarioNota.sucursal_id.in_(allowed_suc_ids),
                ComisionarioNota.sucursal_id.is_(None),
            )
        )

    notas = query.order_by(ComisionarioNota.created_at.desc()).all()
    if q:
        notas = _filter_comisionario_notas(notas, q)

    comisionarios = _get_accessible_comisionarios(db, current_user)
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    comisionarios_map = {c.id: c.nombre_completo for c in comisionarios}
    sucursales_map = {s.id: s.nombre for s in sucursales}

    return templates.TemplateResponse(
        "admin/comisionario_notas_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "notas": notas,
            "comisionarios": comisionarios,
            "comisionarios_map": comisionarios_map,
            "sucursales_map": sucursales_map,
            "comisionario_id": comisionario_id,
            "q": q,
        },
    )


def _parse_comisionario_materiales(form) -> tuple[list[dict], str | None]:
    material_ids = form.getlist("material_id")
    kg_list = form.getlist("kg_neto")
    precio_list = form.getlist("precio_kg")
    rows: list[dict] = []
    for idx in range(max(len(material_ids), len(kg_list), len(precio_list))):
        material_raw = material_ids[idx] if idx < len(material_ids) else ""
        kg_raw = kg_list[idx] if idx < len(kg_list) else ""
        precio_raw = precio_list[idx] if idx < len(precio_list) else ""
        if not material_raw and not kg_raw and not precio_raw:
            continue
        try:
            material_id = int(material_raw)
        except (TypeError, ValueError):
            return [], "Selecciona un material valido."
        try:
            kg_neto = Decimal(str(kg_raw))
        except (InvalidOperation, TypeError):
            return [], "El kg neto es invalido."
        try:
            precio_kg = Decimal(str(precio_raw))
        except (InvalidOperation, TypeError):
            return [], "El precio por kg es invalido."
        rows.append(
            {
                "material_id": material_id,
                "kg_neto": kg_neto,
                "precio_por_kg": precio_kg,
            }
        )
    if not rows:
        return [], "Debes agregar al menos un material."
    return rows, None


@router.get("/comisionarios/notas/nueva")
async def comisionario_nota_new_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    sucursales = _active_sucursales(db)
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    comisionarios = _get_accessible_comisionarios(db, current_user, activos_solamente=True)
    materiales = db.query(Material).order_by(Material.orden_display, Material.nombre).all()
    preselect_id = None
    if request.query_params.get("comisionario_id"):
        try:
            preselect_id = int(request.query_params.get("comisionario_id"))
        except ValueError:
            preselect_id = None
    selected_comisionario = next((c for c in comisionarios if c.id == preselect_id), None)
    return templates.TemplateResponse(
        "admin/comisionario_nota_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "comisionarios": comisionarios,
            "sucursales": sucursales,
            "materiales": materiales,
            "form_rows": [{}],
            "form_comisionario_id": preselect_id or "",
            "form_sucursal_id": str(selected_comisionario.sucursal_id) if selected_comisionario else "",
            "form_comentario": "",
            "error": None,
        },
    )


@router.post("/comisionarios/notas/nueva")
async def comisionario_nota_new_post(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    form = await request.form()
    comisionario_raw = (form.get("comisionario_id") or "").strip()
    sucursal_raw = (form.get("sucursal_id") or "").strip()
    comentario = (form.get("comentario") or "").strip()

    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    sucursales = _active_sucursales(db)
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    comisionarios = _get_accessible_comisionarios(db, current_user, activos_solamente=True)
    materiales = db.query(Material).order_by(Material.orden_display, Material.nombre).all()

    def render_error(msg: str, rows: list[dict]):
        return templates.TemplateResponse(
            "admin/comisionario_nota_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "comisionarios": comisionarios,
                "sucursales": sucursales,
                "materiales": materiales,
                "form_rows": rows or [{}],
                "form_comisionario_id": comisionario_raw,
                "form_sucursal_id": sucursal_raw,
                "form_comentario": comentario,
                "error": msg,
            },
            status_code=400,
        )

    if not comisionario_raw:
        return render_error("Selecciona un comisionario.", [])
    try:
        comisionario_id = int(comisionario_raw)
    except ValueError:
        return render_error("Comisionario inválido.", [])
    comisionario = next((c for c in comisionarios if c.id == comisionario_id), None)
    if not comisionario:
        return render_error("Comisionario inválido o no autorizado.", [])

    expected_sucursal_id = comisionario.sucursal_id
    if not expected_sucursal_id:
        return render_error("El comisionario no tiene sucursal asignada.", [])
    if allowed_suc_ids and expected_sucursal_id not in allowed_suc_ids:
        return render_error("Sucursal no autorizada.", [])
    if sucursal_raw:
        try:
            selected_sucursal_id = int(sucursal_raw)
        except ValueError:
            return render_error("Sucursal invalida.", [])
        if selected_sucursal_id != expected_sucursal_id:
            return render_error("La nota debe registrarse en la sucursal asignada al comisionario.", [])
    sucursal_raw = str(expected_sucursal_id)

    materiales_rows, err = _parse_comisionario_materiales(form)
    if err:
        return render_error(err, materiales_rows)

    try:
        nota = comision_service.create_comisionario_nota(
            db,
            comisionario_id=comisionario_id,
            sucursal_id=expected_sucursal_id,
            admin_id=current_user.get("id"),
            comentario=comentario,
            materiales_payload=materiales_rows,
        )
    except ValueError as exc:
        return render_error(str(exc), materiales_rows)

    return RedirectResponse(url=f"/web/admin/comisionarios/notas/{nota.id}", status_code=303)


def _render_comisionario_nota_detail(
    request: Request,
    db: Session,
    current_user: dict,
    nota: ComisionarioNota,
    *,
    error: str | None = None,
    form_state: dict | None = None,
    pago_ok: bool = False,
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    if allowed_suc_ids and nota.sucursal_id and nota.sucursal_id not in allowed_suc_ids:
        raise HTTPException(status_code=403, detail="Sucursal no autorizada.")

    comisionario = db.get(Comisionario, nota.comisionario_id)
    materiales = (
        db.query(ComisionarioNotaMaterial)
        .filter(ComisionarioNotaMaterial.nota_id == nota.id)
        .all()
    )
    pagos = (
        db.query(ComisionarioPago)
        .filter(ComisionarioPago.nota_id == nota.id)
        .order_by(ComisionarioPago.created_at.desc())
        .all()
    )
    cuentas = (
        db.query(Cuenta)
        .filter(
            Cuenta.activo.is_(True),
            Cuenta.comisionario_id == nota.comisionario_id,
        )
        .order_by(Cuenta.nombre)
        .all()
    )
    cuentas_scrap360 = db.query(CuentaScrap360).filter(CuentaScrap360.activo.is_(True)).all()
    if nota.sucursal_id:
        cuentas_scrap360 = [
            c for c in cuentas_scrap360
            if not c.sucursales or nota.sucursal_id in {s.id for s in c.sucursales}
        ]

    total = Decimal(str(nota.total_monto or 0))
    pagado = Decimal(str(nota.monto_pagado or 0))
    saldo = total - pagado

    form_state = form_state or {}

    return templates.TemplateResponse(
        "admin/comisionario_nota_detail.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "nota": nota,
            "comisionario": comisionario,
            "materiales": materiales,
            "pagos": pagos,
            "cuentas": cuentas,
            "cuentas_scrap360": cuentas_scrap360,
            "total": total,
            "pagado": pagado,
            "saldo": saldo,
            "error": error,
            "form_monto": form_state.get("monto", ""),
            "form_metodo": form_state.get("metodo", ""),
            "form_cuenta": form_state.get("cuenta", ""),
            "form_cuenta_scrap360": form_state.get("cuenta_scrap360", ""),
            "form_comentario": form_state.get("comentario", ""),
            "pago_ok": pago_ok,
        },
        status_code=400 if error else 200,
    )


@router.get("/comisionarios/notas/{nota_id}")
async def comisionario_nota_detail(
    nota_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(ComisionarioNota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota de comisionario no encontrada.")
    pago_ok = request.query_params.get("pago") == "1"
    return _render_comisionario_nota_detail(request, db, current_user, nota, pago_ok=pago_ok)


@router.get("/comisionarios/notas/{nota_id}/pdf")
async def comisionario_nota_pdf(
    nota_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(ComisionarioNota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota de comisionario no encontrada.")
    if nota.estado != ComisionarioNotaEstado.aprobada:
        raise HTTPException(status_code=400, detail="La nota debe estar aprobada.")
    pdf_bytes, filename = invoice_service.build_comisionario_nota_pdf(db, nota)
    headers = {"Content-Disposition": f"attachment; filename={filename}"}
    return StreamingResponse(io.BytesIO(pdf_bytes), media_type="application/pdf", headers=headers)


@router.post("/comisionarios/notas/{nota_id}/pago")
async def comisionario_nota_add_pago(
    nota_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(ComisionarioNota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota de comisionario no encontrada.")

    form = await request.form()
    monto_raw = (form.get("monto") or "").strip()
    metodo = (form.get("metodo_pago") or "").strip().lower()
    cuenta_financiera = (form.get("cuenta_financiera") or "").strip()
    cuenta_scrap360_raw = (form.get("cuenta_scrap360_id") or "").strip()
    comentario = (form.get("comentario") or "").strip()
    form_state = {
        "monto": monto_raw,
        "metodo": metodo,
        "cuenta": cuenta_financiera,
        "cuenta_scrap360": cuenta_scrap360_raw,
        "comentario": comentario,
    }
    if not monto_raw:
        return _render_comisionario_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="Debes indicar el monto pagado.",
            form_state=form_state,
        )
    try:
        monto_val = Decimal(str(monto_raw))
    except (InvalidOperation, TypeError):
        return _render_comisionario_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="El monto pagado es invalido.",
            form_state=form_state,
        )

    cuenta_scrap360_id = None
    if cuenta_scrap360_raw:
        try:
            cuenta_scrap360_id = int(cuenta_scrap360_raw)
        except (TypeError, ValueError):
            return _render_comisionario_nota_detail(
                request,
                db,
                current_user,
                nota,
                error="La cuenta Scrap360 es invalida.",
                form_state=form_state,
            )

    try:
        comision_service.add_comisionario_pago(
            db,
            nota=nota,
            monto=monto_val,
            usuario_id=current_user.get("id"),
            metodo_pago=metodo or None,
            cuenta_financiera=cuenta_financiera or None,
            cuenta_scrap360_id=cuenta_scrap360_id,
            comentario=comentario or None,
        )
    except ValueError as exc:
        return _render_comisionario_nota_detail(
            request,
            db,
            current_user,
            nota,
            error=str(exc),
            form_state=form_state,
        )

    return RedirectResponse(url=f"/web/admin/comisionarios/notas/{nota_id}?pago=1", status_code=303)


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
    delete_ok = params.get("deleted") == "1"
    delete_error = (params.get("delete_error") or "").strip() or None

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
    elif owner_type == "comisionario":
        query = query.filter(Cuenta.comisionario_id == owner_id)

    if activo in ("1", "0"):
        query = query.filter(Cuenta.activo.is_(activo == "1"))

    cuentas = query.order_by(Cuenta.nombre).all()
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    clientes = db.query(Cliente).order_by(Cliente.nombre_completo).all()
    proveedores = db.query(Proveedor).order_by(Proveedor.nombre_completo).all()
    comisionarios = _get_accessible_comisionarios(db, current_user)
    sucursales_map = {s.id: s.nombre for s in sucursales}
    clientes_map = {c.id: c.nombre_completo for c in clientes}
    proveedores_map = {p.id: p.nombre_completo for p in proveedores}
    comisionarios_map = {c.id: c.nombre_completo for c in comisionarios}

    cuentas_view = [
        {
            "cuenta": cuenta,
            "owner_label": _build_cuenta_owner_label(
                cuenta,
                sucursales_map,
                clientes_map,
                proveedores_map,
                comisionarios_map,
            ),
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
            "comisionarios": comisionarios,
            "owner_key": owner_key or "",
            "owner_error": owner_error,
            "activo": activo or "",
            "q": q or "",
            "delete_ok": delete_ok,
            "delete_error": delete_error,
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
    comisionario_id = None
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
    elif owner_type == "comisionario":
        if not db.get(Comisionario, owner_id):
            return _render_cuenta_form(
                request,
                db,
                current_user,
                cuenta=None,
                owner_key="",
                error="Comisionario inválido.",
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
        comisionario_id = owner_id

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
        comisionario_id=comisionario_id,
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
    comisionario_id = None
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
    elif owner_type == "comisionario":
        if not db.get(Comisionario, owner_id):
            return _render_cuenta_form(
                request,
                db,
                current_user,
                cuenta=cuenta,
                owner_key="",
                error="Comisionario inválido.",
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
        comisionario_id = owner_id

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
    cuenta.comisionario_id = comisionario_id
    cuenta.updated_at = datetime.utcnow()
    db.add(cuenta)
    db.commit()

    return RedirectResponse(url=f"/web/admin/cuentas/{cuenta.id}", status_code=303)


@router.post("/cuentas/{cuenta_id}/eliminar")
async def cuenta_delete(
    cuenta_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    cuenta = db.get(Cuenta, cuenta_id)
    if not cuenta:
        raise HTTPException(status_code=404, detail="Cuenta no encontrada.")

    db.query(Nota).filter(Nota.cuenta_financiera_id == cuenta_id).update(
        {Nota.cuenta_financiera_id: None},
        synchronize_session=False,
    )
    db.query(NotaPago).filter(NotaPago.cuenta_id == cuenta_id).update(
        {NotaPago.cuenta_id: None},
        synchronize_session=False,
    )
    db.query(ComisionarioPago).filter(ComisionarioPago.cuenta_id == cuenta_id).update(
        {ComisionarioPago.cuenta_id: None},
        synchronize_session=False,
    )
    db.query(MovimientoContable).filter(MovimientoContable.cuenta_id == cuenta_id).update(
        {MovimientoContable.cuenta_id: None},
        synchronize_session=False,
    )

    db.delete(cuenta)
    db.commit()
    return RedirectResponse(url="/web/admin/cuentas?deleted=1", status_code=303)


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
    elif cuenta.comisionario_id:
        com = db.get(Comisionario, cuenta.comisionario_id)
        owner_label = f"Comisionario: {com.nombre_completo if com else cuenta.comisionario_id}"
        owner_kind = "comisionario"

    movimientos_query = db.query(MovimientoContable).filter(MovimientoContable.cuenta_id == cuenta_id)
    if owner_kind in ("proveedor", "cliente"):
        movimientos_query = movimientos_query.filter(
            MovimientoContable.tipo.in_(["pago", "reverso_pago", "restauracion_pago"])
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
        kpi_query = kpi_query.filter(MovimientoContable.tipo.in_(["pago", "reverso_pago", "restauracion_pago"]))
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

    tipo_filters: list[TipoOperacion] | None = None
    if owner_kind == "proveedor":
        tipo_filters = [TipoOperacion.compra, TipoOperacion.venta]
    elif owner_kind == "cliente":
        tipo_filters = [TipoOperacion.venta]

    notas_query = db.query(Nota).filter(Nota.cuenta_financiera_id == cuenta_id)
    if tipo_filters:
        notas_query = notas_query.filter(Nota.tipo_operacion.in_(tipo_filters))
    notas = notas_query.order_by(Nota.created_at.desc()).limit(200).all()

    notas_recon_query = db.query(Nota).filter(
        Nota.cuenta_financiera_id == cuenta_id,
        Nota.estado == NotaEstado.aprobada,
    )
    if tipo_filters:
        notas_recon_query = notas_recon_query.filter(Nota.tipo_operacion.in_(tipo_filters))
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
    if tipo_filters:
        pagos_match_query = pagos_match_query.filter(Nota.tipo_operacion.in_(tipo_filters))
    pagos_matched = pagos_match_query.all()

    recon_map: dict[tuple[str, int], dict] = {}
    for nota in notas_recon:
        partner_kind, partner_id = _nota_partner_key(nota)
        if not partner_kind or not partner_id:
            continue
        key = (partner_kind, partner_id)
        entry = recon_map.setdefault(
            key,
            {
                "expected": Decimal("0"),
                "paid": Decimal("0"),
                "notas": 0,
                "pagos": 0,
            },
        )
        sign = _partner_note_sign(partner_kind, nota)
        entry["expected"] += Decimal(str(nota.total_monto or 0)) * sign
        entry["notas"] += 1

    for pago in pagos_matched:
        nota = pago.nota
        if not nota:
            continue
        partner_kind, partner_id = _nota_partner_key(nota)
        if not partner_kind or not partner_id:
            continue
        key = (partner_kind, partner_id)
        entry = recon_map.setdefault(
            key,
            {
                "expected": Decimal("0"),
                "paid": Decimal("0"),
                "notas": 0,
                "pagos": 0,
            },
        )
        sign = _partner_note_sign(partner_kind, nota)
        entry["paid"] += Decimal(str(pago.monto or 0)) * sign
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
    if tipo_filters:
        pagos_fuera_cuenta_query = pagos_fuera_cuenta_query.filter(Nota.tipo_operacion.in_(tipo_filters))
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
    if tipo_filters:
        pagos_no_aprobados_query = pagos_no_aprobados_query.filter(Nota.tipo_operacion.in_(tipo_filters))
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
    partner_type = owner_kind if owner_kind in ("cliente", "proveedor") else None
    nota_rows = _build_partner_record_rows(notas, folio_map, partner_type=partner_type)
    pendiente_total = Decimal("0")
    saldo_favor_total = Decimal("0")
    for nota in notas:
        if nota.estado != NotaEstado.aprobada:
            continue
        total, pagado = _signed_partner_amounts(nota, partner_type)
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


# ---------- CUENTAS SCRAP360 ----------


@router.get("/cuentas-scrap360")
async def cuentas_scrap360_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    params = request.query_params
    q = (params.get("q") or "").strip()
    tipo = (params.get("tipo") or "").strip().lower()
    activo = (params.get("activo") or "").strip()
    delete_ok = params.get("deleted") == "1"
    delete_error = (params.get("delete_error") or "").strip() or None
    sucursal_id = None
    sucursal_error = None
    if params.get("sucursal_id"):
        try:
            sucursal_id = int(params.get("sucursal_id"))
        except ValueError:
            sucursal_id = None
            sucursal_error = "Sucursal invalida."

    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)

    query = db.query(CuentaScrap360)
    if q:
        term = f"%{q}%"
        query = query.filter(CuentaScrap360.nombre.ilike(term))
    if tipo:
        if tipo in _SCRAP360_TIPOS:
            query = query.filter(CuentaScrap360.tipo == tipo)
        else:
            tipo = ""
    if activo in ("1", "0"):
        query = query.filter(CuentaScrap360.activo.is_(activo == "1"))

    if allowed_suc_ids:
        query = query.join(CuentaScrap360.sucursales).filter(Sucursal.id.in_(allowed_suc_ids))
    if sucursal_id:
        if allowed_suc_ids and sucursal_id not in allowed_suc_ids:
            sucursal_id = None
            sucursal_error = "Sucursal no autorizada."
        else:
            query = query.join(CuentaScrap360.sucursales).filter(Sucursal.id == sucursal_id)

    cuentas = query.distinct().order_by(CuentaScrap360.nombre).all()
    cuentas_view = []
    for cuenta in cuentas:
        suc_labels = ", ".join([s.nombre for s in cuenta.sucursales]) if cuenta.sucursales else "-"
        cuentas_view.append(
            {
                "cuenta": cuenta,
                "sucursales_label": suc_labels,
            }
        )

    return templates.TemplateResponse(
        "admin/cuentas_scrap360_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "cuentas": cuentas_view,
            "sucursales": sucursales,
            "q": q,
            "tipo": tipo,
            "activo": activo,
            "sucursal_id": sucursal_id,
            "sucursal_error": sucursal_error,
            "tipos_scrap360": _SCRAP360_TIPOS,
            "delete_ok": delete_ok,
            "delete_error": delete_error,
        },
    )


@router.get("/cuentas-scrap360/nueva")
async def cuentas_scrap360_new_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    sucursales = _active_sucursales(db)
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    return templates.TemplateResponse(
        "admin/cuenta_scrap360_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "cuenta": None,
            "sucursales": sucursales,
            "selected_sucursales": [],
            "tipos_scrap360": _SCRAP360_TIPOS,
            "error": None,
            "form_nombre": "",
            "form_tipo": "",
            "form_saldo": "",
            "form_activo": True,
        },
    )


@router.post("/cuentas-scrap360/nueva")
async def cuentas_scrap360_new_post(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    sucursales = _active_sucursales(db)
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    form = await request.form()
    nombre = (form.get("nombre") or "").strip()
    tipo = (form.get("tipo") or "").strip().lower()
    saldo_raw = (form.get("saldo_inicial") or "").strip()
    activo = bool(form.get("activo"))
    sucursal_ids_raw = form.getlist("sucursal_ids")

    selected_ids: list[int] = []
    for raw in sucursal_ids_raw:
        try:
            val = int(raw)
        except (TypeError, ValueError):
            continue
        selected_ids.append(val)

    def render_error(msg: str):
        return templates.TemplateResponse(
            "admin/cuenta_scrap360_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "cuenta": None,
                "sucursales": sucursales,
                "selected_sucursales": selected_ids,
                "tipos_scrap360": _SCRAP360_TIPOS,
                "error": msg,
                "form_nombre": nombre,
                "form_tipo": tipo,
                "form_saldo": saldo_raw,
                "form_activo": activo,
            },
            status_code=400,
        )

    if not nombre:
        return render_error("El nombre es obligatorio.")
    if tipo not in _SCRAP360_TIPOS:
        return render_error("Tipo de cuenta invalido.")
    if not selected_ids:
        return render_error("Selecciona al menos una sucursal.")
    if allowed_suc_ids:
        for sid in selected_ids:
            if sid not in allowed_suc_ids:
                return render_error("Sucursal no autorizada.")
    saldo_inicial = Decimal("0")
    if saldo_raw:
        try:
            saldo_inicial = Decimal(str(saldo_raw))
        except (InvalidOperation, TypeError):
            return render_error("El saldo inicial es invalido.")

    sucursales_sel = db.query(Sucursal).filter(Sucursal.id.in_(selected_ids)).all()
    if len(sucursales_sel) != len(set(selected_ids)):
        return render_error("Sucursal no encontrada.")

    cuenta = CuentaScrap360(
        nombre=nombre,
        tipo=tipo,
        saldo_inicial=saldo_inicial,
        saldo_actual=Decimal("0"),
        activo=activo,
    )
    cuenta.sucursales = sucursales_sel
    db.add(cuenta)
    db.flush()

    if saldo_inicial != Decimal("0"):
        _apply_scrap360_adjustment(
            db,
            cuenta=cuenta,
            monto=saldo_inicial,
            comentario="Saldo inicial",
            usuario_id=current_user.get("id"),
        )
    else:
        cuenta.saldo_actual = saldo_inicial
        cuenta.updated_at = datetime.utcnow()
        db.add(cuenta)

    db.commit()
    return RedirectResponse(url=f"/web/admin/cuentas-scrap360/{cuenta.id}", status_code=303)


@router.get("/cuentas-scrap360/{cuenta_id}")
async def cuenta_scrap360_detail(
    cuenta_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    cuenta = db.get(CuentaScrap360, cuenta_id)
    if not cuenta:
        raise HTTPException(status_code=404, detail="Cuenta Scrap360 no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_scrap360_access(cuenta, allowed_suc_ids)
    ajuste_ok = request.query_params.get("ajuste") == "1"
    return templates.TemplateResponse(
        "admin/cuenta_scrap360_detail.html",
        _build_cuenta_scrap360_detail_context(
            db,
            request=request,
            current_user=current_user,
            cuenta=cuenta,
            ajuste_ok=ajuste_ok,
        ),
    )


@router.get("/cuentas-scrap360/{cuenta_id}/editar")
async def cuenta_scrap360_edit_get(
    cuenta_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    cuenta = db.get(CuentaScrap360, cuenta_id)
    if not cuenta:
        raise HTTPException(status_code=404, detail="Cuenta Scrap360 no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_scrap360_access(cuenta, allowed_suc_ids)
    sucursales = _active_sucursales(db)
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    selected_ids = [s.id for s in cuenta.sucursales]
    return templates.TemplateResponse(
        "admin/cuenta_scrap360_form.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "cuenta": cuenta,
            "sucursales": sucursales,
            "selected_sucursales": selected_ids,
            "tipos_scrap360": _SCRAP360_TIPOS,
            "error": None,
            "form_nombre": cuenta.nombre,
            "form_tipo": cuenta.tipo,
            "form_saldo": str(cuenta.saldo_inicial or ""),
            "form_activo": cuenta.activo,
        },
    )


@router.post("/cuentas-scrap360/{cuenta_id}/editar")
async def cuenta_scrap360_edit_post(
    cuenta_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    cuenta = db.get(CuentaScrap360, cuenta_id)
    if not cuenta:
        raise HTTPException(status_code=404, detail="Cuenta Scrap360 no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_scrap360_access(cuenta, allowed_suc_ids)
    sucursales = _active_sucursales(db)
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    form = await request.form()
    nombre = (form.get("nombre") or "").strip()
    tipo = (form.get("tipo") or "").strip().lower()
    activo = bool(form.get("activo"))
    sucursal_ids_raw = form.getlist("sucursal_ids")
    selected_ids: list[int] = []
    for raw in sucursal_ids_raw:
        try:
            val = int(raw)
        except (TypeError, ValueError):
            continue
        selected_ids.append(val)

    def render_error(msg: str):
        return templates.TemplateResponse(
            "admin/cuenta_scrap360_form.html",
            {
                "request": request,
                "env": settings.ENV,
                "user": current_user,
                "cuenta": cuenta,
                "sucursales": sucursales,
                "selected_sucursales": selected_ids,
                "tipos_scrap360": _SCRAP360_TIPOS,
                "error": msg,
                "form_nombre": nombre,
                "form_tipo": tipo,
                "form_saldo": str(cuenta.saldo_inicial or ""),
                "form_activo": activo,
            },
            status_code=400,
        )

    if not nombre:
        return render_error("El nombre es obligatorio.")
    if tipo not in _SCRAP360_TIPOS:
        return render_error("Tipo de cuenta invalido.")
    if not selected_ids:
        return render_error("Selecciona al menos una sucursal.")
    if allowed_suc_ids:
        for sid in selected_ids:
            if sid not in allowed_suc_ids:
                return render_error("Sucursal no autorizada.")

    sucursales_sel = db.query(Sucursal).filter(Sucursal.id.in_(selected_ids)).all()
    if len(sucursales_sel) != len(set(selected_ids)):
        return render_error("Sucursal no encontrada.")

    cuenta.nombre = nombre
    cuenta.tipo = tipo
    cuenta.activo = activo
    cuenta.sucursales = sucursales_sel
    cuenta.updated_at = datetime.utcnow()
    db.add(cuenta)
    db.commit()
    return RedirectResponse(url=f"/web/admin/cuentas-scrap360/{cuenta.id}", status_code=303)


@router.post("/cuentas-scrap360/{cuenta_id}/eliminar")
async def cuenta_scrap360_delete(
    cuenta_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    cuenta = db.get(CuentaScrap360, cuenta_id)
    if not cuenta:
        raise HTTPException(status_code=404, detail="Cuenta Scrap360 no encontrada.")

    db.query(NotaPago).filter(NotaPago.cuenta_scrap360_id == cuenta_id).update(
        {NotaPago.cuenta_scrap360_id: None},
        synchronize_session=False,
    )
    db.query(ComisionarioPago).filter(ComisionarioPago.cuenta_scrap360_id == cuenta_id).update(
        {ComisionarioPago.cuenta_scrap360_id: None},
        synchronize_session=False,
    )
    db.query(CuentaScrap360Movimiento).filter(CuentaScrap360Movimiento.cuenta_id == cuenta_id).delete(
        synchronize_session=False,
    )

    cuenta.sucursales = []
    db.flush()
    db.delete(cuenta)
    db.commit()
    return RedirectResponse(url="/web/admin/cuentas-scrap360?deleted=1", status_code=303)


@router.post("/cuentas-scrap360/{cuenta_id}/ajuste")
async def cuenta_scrap360_ajuste(
    cuenta_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    cuenta = db.get(CuentaScrap360, cuenta_id)
    if not cuenta:
        raise HTTPException(status_code=404, detail="Cuenta Scrap360 no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_scrap360_access(cuenta, allowed_suc_ids)

    form = await request.form()
    monto_raw = (form.get("monto") or "").strip()
    comentario = (form.get("comentario") or "").strip()
    direccion = (form.get("direccion") or "").strip().lower() or "entrada"
    concepto = (form.get("concepto") or "").strip().lower() or "deposito"
    form_state = {
        "form_ajuste_monto": monto_raw,
        "form_ajuste_direccion": direccion,
        "form_ajuste_concepto": concepto,
        "form_ajuste_comentario": comentario,
    }
    def render_error(msg: str):
        return templates.TemplateResponse(
            "admin/cuenta_scrap360_detail.html",
            _build_cuenta_scrap360_detail_context(
                db,
                request=request,
                current_user=current_user,
                cuenta=cuenta,
                error=msg,
                form_state=form_state,
            ),
            status_code=400,
        )

    if not monto_raw:
        return render_error("Debes indicar el monto del ajuste.")
    try:
        monto_val = Decimal(str(monto_raw))
    except (InvalidOperation, TypeError):
        return render_error("El monto del ajuste es invalido.")
    if monto_val <= Decimal("0"):
        return render_error("El monto del movimiento debe ser mayor a cero.")
    if direccion not in {key for key, _label in _SCRAP360_AJUSTE_DIRECCIONES}:
        return render_error("Selecciona si el movimiento es entrada o salida.")
    if concepto not in {key for key, _label in _SCRAP360_AJUSTE_CONCEPTOS}:
        return render_error("Selecciona un concepto valido.")
    if concepto == "otro" and not comentario:
        return render_error("Describe el movimiento cuando el concepto es Otro.")

    monto_signed = monto_val if direccion == "entrada" else -monto_val
    concepto_label = _scrap360_concept_label(concepto)
    comentario_full = concepto_label if not comentario else f"{concepto_label}: {comentario}"

    _apply_scrap360_adjustment(
        db,
        cuenta=cuenta,
        monto=monto_signed,
        comentario=comentario_full,
        usuario_id=current_user.get("id"),
    )
    db.commit()
    return RedirectResponse(url=f"/web/admin/cuentas-scrap360/{cuenta.id}?ajuste=1", status_code=303)


@router.get("/cuentas-scrap360/{cuenta_id}/estado")
async def cuenta_scrap360_statement(
    cuenta_id: int,
    request: Request,
    format: str = "pdf",
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    cuenta = db.get(CuentaScrap360, cuenta_id)
    if not cuenta:
        raise HTTPException(status_code=404, detail="Cuenta Scrap360 no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_scrap360_access(cuenta, allowed_suc_ids)

    movimientos = (
        db.query(CuentaScrap360Movimiento)
        .filter(CuentaScrap360Movimiento.cuenta_id == cuenta_id)
        .order_by(CuentaScrap360Movimiento.created_at.asc(), CuentaScrap360Movimiento.id.asc())
        .all()
    )
    report = scrap360_account_report_service.build_scrap360_account_statement(cuenta, movimientos)
    format_clean = (format or "pdf").strip().lower()
    if format_clean == "excel":
        content, filename = scrap360_account_report_service.build_scrap360_account_statement_excel(report)
        media_type = "application/vnd.ms-excel"
    else:
        content, filename = scrap360_account_report_service.build_scrap360_account_statement_pdf(report)
        media_type = "application/pdf"

    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(io.BytesIO(content), media_type=media_type, headers=headers)


# ---------- NOTAS ----------


def _build_note_price_map(db: Session) -> dict:
    mapping: dict = {}
    precios = db.query(TablaPrecio).filter(TablaPrecio.activo.is_(True)).all()
    for precio in precios:
        mapping.setdefault(precio.material_id, {}).setdefault(precio.tipo_operacion.value, {})[
            precio.tipo_cliente.value
        ] = float(precio.precio_por_unidad)
    return mapping


def _parse_note_materials_from_form(
    material_ids: list[str],
    kg_brutos: list[str],
    kg_descs: list[str],
    subpesos: list[str],
    tipos_cliente: list[str],
    kg_reals: list[str] | None = None,
) -> list[dict]:
    materiales_payload: list[dict] = []
    for idx, (mid, kg_b, kg_d, sub, tc) in enumerate(zip(material_ids, kg_brutos, kg_descs, subpesos, tipos_cliente)):
        if not mid:
            continue
        kg_bruto = Decimal(kg_b or "0")
        kg_desc = Decimal(kg_d or "0")
        if kg_bruto <= 0 and not sub:
            continue
        sub_list: list[dict] = []
        if sub:
            try:
                sub_json = json.loads(sub)
            except json.JSONDecodeError:
                raise ValueError("Formato de subpesajes invalido.")
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
            kg_bruto = sum((item["peso_kg"] for item in sub_list), Decimal("0"))
            kg_desc = sum((item["descuento_kg"] for item in sub_list), Decimal("0"))
            if kg_desc > kg_bruto:
                raise ValueError("El descuento no puede ser mayor que el peso bruto.")
        else:
            if kg_desc > kg_bruto:
                raise ValueError("El descuento no puede ser mayor que el peso bruto.")
        kg_real_str = ((kg_reals[idx] if kg_reals and idx < len(kg_reals) else "") or "").strip()
        kg_real_val: Decimal | None = None
        if kg_real_str:
            try:
                kg_real_val = Decimal(kg_real_str)
                if kg_real_val < Decimal("0"):
                    raise ValueError("Los kg reales de inventario no pueden ser negativos.")
            except InvalidOperation:
                raise ValueError("Kg reales de inventario invalidos.")
        materiales_payload.append(
            {
                "material_id": int(mid),
                "kg_bruto": kg_bruto,
                "kg_descuento": kg_desc,
                "subpesajes": sub_list,
                "tipo_cliente": tc or None,
                "kg_real": kg_real_val,
            }
        )
    return materiales_payload


def _render_admin_purchase_note_form(
    request: Request,
    db: Session,
    current_user: dict,
    *,
    error: str | None = None,
    status_code: int = 200,
    initial_state: dict | None = None,
    form_sucursal_id: str | None = None,
    form_title: str = "Nueva compra administrativa",
    action_url: str = "/web/admin/notas/compra-administrativa",
    force_tipo_operacion: str = "compra",
):
    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.orden_display, Material.nombre).all()
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    proveedores_query = db.query(Proveedor).filter(Proveedor.activo.is_(True))
    clientes_query = db.query(Cliente).filter(Cliente.activo.is_(True))
    if allowed_suc_ids:
        proveedores_query = proveedores_query.filter(Proveedor.sucursal_id.in_(allowed_suc_ids))
        clientes_query = clientes_query.filter(Cliente.sucursal_id.in_(allowed_suc_ids))
    proveedores = proveedores_query.order_by(Proveedor.nombre_completo).all()
    proveedores_venta = [p for p in proveedores if bool(getattr(p, "permite_ventas", False))]
    clientes = clientes_query.order_by(Cliente.nombre_completo).all()
    sucursales = _active_sucursales(db)
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
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
            "price_map": _build_note_price_map(db),
            "max_mb": settings.FIREBASE_MAX_MB,
            "form_title": form_title,
            "action_url": action_url,
            "submit_label": "Crear borrador administrativo",
            "initial_note_json": json.dumps(initial_state or {}, ensure_ascii=True),
            "review_comment": "",
            "back_url": "/web/admin/notas",
            "show_sucursal_picker": True,
            "sucursales": sucursales,
            "form_sucursal_id": form_sucursal_id or "",
            "force_tipo_operacion": force_tipo_operacion,
            "show_kg_real": True,
        },
        status_code=status_code,
    )


@router.get("/notas/compra-administrativa")
async def notas_compra_administrativa_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    return _render_admin_purchase_note_form(
        request,
        db,
        current_user,
        initial_state={"tipo_operacion": "compra", "venta_partner_kind": "proveedor", "materiales": [], "extra_evidencias": []},
        form_title="Nueva compra administrativa",
        action_url="/web/admin/notas/compra-administrativa",
        force_tipo_operacion="compra",
    )


@router.post("/notas/compra-administrativa")
async def notas_compra_administrativa_post(
    request: Request,
    sucursal_contable_id: str = Form(...),
    proveedor_compra_id: str = Form(""),
    material_id: List[str] = Form([]),
    kg_bruto: List[str] = Form([]),
    kg_descuento: List[str] = Form([]),
    subpesajes: List[str] = Form([]),
    tipo_cliente: List[str] = Form([]),
    kg_real: List[str] = Form([]),
    comentarios_trabajador: str = Form(""),
    extra_evidencias: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    initial_state = {
        "tipo_operacion": "compra",
        "venta_partner_kind": "proveedor",
        "proveedor_compra_id": proveedor_compra_id or "",
        "comentarios_trabajador": comentarios_trabajador or "",
        "materiales": [],
        "extra_evidencias": [],
    }

    def render_error(message: str):
        return _render_admin_purchase_note_form(
            request,
            db,
            current_user,
            error=message,
            status_code=400,
            initial_state=initial_state,
            form_sucursal_id=sucursal_contable_id,
        )

    try:
        sucursal_id = int(sucursal_contable_id)
    except (TypeError, ValueError):
        return render_error("Selecciona una sucursal contable valida.")
    if not proveedor_compra_id:
        return render_error("Selecciona un proveedor para la compra.")

    try:
        materiales_payload = _parse_note_materials_from_form(material_id, kg_bruto, kg_descuento, subpesajes, tipo_cliente, kg_real)
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

    try:
        nota = note_service.create_draft_note(
            db,
            sucursal_id=sucursal_id,
            trabajador_id=current_user.get("id"),
            tipo_operacion=TipoOperacion.compra,
            materiales_payload=materiales_payload,
            comentarios_trabajador=comentarios_trabajador,
            proveedor_id=int(proveedor_compra_id),
            extra_evidencias_payload=extra_evidencias_payload,
        )
    except ValueError as exc:
        return render_error(str(exc))

    return RedirectResponse(url=f"/web/admin/notas/{nota.id}", status_code=303)


@router.get("/notas/venta-administrativa")
async def notas_venta_administrativa_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    return _render_admin_purchase_note_form(
        request,
        db,
        current_user,
        initial_state={"tipo_operacion": "venta", "venta_partner_kind": "cliente", "materiales": [], "extra_evidencias": []},
        form_title="Nueva venta administrativa",
        action_url="/web/admin/notas/venta-administrativa",
        force_tipo_operacion="venta",
    )


@router.post("/notas/venta-administrativa")
async def notas_venta_administrativa_post(
    request: Request,
    sucursal_contable_id: str = Form(...),
    venta_partner_kind: str = Form("cliente"),
    proveedor_venta_id: str = Form(""),
    cliente_id: str = Form(""),
    material_id: List[str] = Form([]),
    kg_bruto: List[str] = Form([]),
    kg_descuento: List[str] = Form([]),
    subpesajes: List[str] = Form([]),
    tipo_cliente: List[str] = Form([]),
    kg_real: List[str] = Form([]),
    comentarios_trabajador: str = Form(""),
    extra_evidencias: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    venta_partner_kind_clean = "proveedor" if (venta_partner_kind or "").strip().lower() == "proveedor" else "cliente"
    proveedor_id = (proveedor_venta_id or "").strip() if venta_partner_kind_clean == "proveedor" else ""
    cliente_id_clean = (cliente_id or "").strip() if venta_partner_kind_clean == "cliente" else ""
    initial_state = {
        "tipo_operacion": "venta",
        "venta_partner_kind": venta_partner_kind_clean,
        "proveedor_venta_id": proveedor_id,
        "cliente_id": cliente_id_clean,
        "comentarios_trabajador": comentarios_trabajador or "",
        "materiales": [],
        "extra_evidencias": [],
    }

    def render_error(message: str):
        return _render_admin_purchase_note_form(
            request,
            db,
            current_user,
            error=message,
            status_code=400,
            initial_state=initial_state,
            form_sucursal_id=sucursal_contable_id,
            form_title="Nueva venta administrativa",
            action_url="/web/admin/notas/venta-administrativa",
            force_tipo_operacion="venta",
        )

    try:
        sucursal_id = int(sucursal_contable_id)
    except (TypeError, ValueError):
        return render_error("Selecciona una sucursal contable valida.")

    if venta_partner_kind_clean == "proveedor":
        if not proveedor_id:
            return render_error("Selecciona un proveedor para la venta.")
    else:
        if not cliente_id_clean:
            return render_error("Selecciona un cliente para la venta.")

    try:
        materiales_payload = _parse_note_materials_from_form(material_id, kg_bruto, kg_descuento, subpesajes, tipo_cliente, kg_real)
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

    try:
        nota = note_service.create_draft_note(
            db,
            sucursal_id=sucursal_id,
            trabajador_id=current_user.get("id"),
            tipo_operacion=TipoOperacion.venta,
            materiales_payload=materiales_payload,
            comentarios_trabajador=comentarios_trabajador,
            proveedor_id=int(proveedor_id) if proveedor_id else None,
            cliente_id=int(cliente_id_clean) if cliente_id_clean else None,
            extra_evidencias_payload=extra_evidencias_payload,
        )
    except ValueError as exc:
        return render_error(str(exc))

    return RedirectResponse(url=f"/web/admin/notas/{nota.id}", status_code=303)


@router.get("/notas")
async def notas_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    sucursales_list = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales_list = _filter_sucursales_for_admin(sucursales_list, allowed_suc_ids)
    sucursal_raw = (request.query_params.get("sucursal_id") or "").strip()
    sucursal_id = None
    if sucursal_raw:
        try:
            sucursal_id = int(sucursal_raw)
        except ValueError:
            sucursal_id = None
    if allowed_suc_ids is not None:
        if sucursal_id and sucursal_id not in allowed_suc_ids:
            sucursal_id = None
        if sucursal_id is None and len(allowed_suc_ids) == 1:
            sucursal_id = allowed_suc_ids[0]
    folio_query = (request.query_params.get("folio") or "").strip()
    proveedor_raw = (request.query_params.get("proveedor_id") or "").strip()
    proveedor_filter_id: int | None = None
    proveedor_filter_error = None
    if proveedor_raw:
        try:
            proveedor_filter_id = int(proveedor_raw)
        except ValueError:
            proveedor_filter_error = "Proveedor invalido."
    estado_raw = (request.query_params.get("estado") or "").strip().upper()
    pago_raw = (request.query_params.get("pago") or "").strip().upper()
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
    pago_aliases = {
        "TODOS": "TODAS",
        "TODAS": "TODAS",
        "PAGADA": "PAGADAS",
        "PAGADO": "PAGADAS",
        "LIQUIDADA": "PAGADAS",
        "LIQUIDADAS": "PAGADAS",
        "SALDADA": "PAGADAS",
        "SALDADAS": "PAGADAS",
        "PENDIENTE": "PENDIENTES",
        "POR_PAGAR": "PENDIENTES",
    }
    if pago_raw in pago_aliases:
        pago_raw = pago_aliases[pago_raw]
    pago_filter = None
    pago_current = "TODAS"
    if pago_raw in {"PAGADAS", "PENDIENTES"}:
        pago_filter = pago_raw
        pago_current = pago_raw
    pago_labels = {
        "TODAS": "Todas",
        "PAGADAS": "Pagadas",
        "PENDIENTES": "Pendientes por pagar",
    }
    pago_label = pago_labels.get(pago_current, "Todas")
    seguimiento_raw = (request.query_params.get("seguimiento") or "").strip().upper()
    seguimiento_aliases = {
        "TODOS": "TODOS",
        "TODAS": "TODOS",
        "VENCIDA": "VENCIDAS",
        "VENCIDAS": "VENCIDAS",
        "PORVENCER": "POR_VENCER",
        "POR_VENCER": "POR_VENCER",
        "PROXIMAS": "POR_VENCER",
    }
    seguimiento_raw = seguimiento_aliases.get(seguimiento_raw, seguimiento_raw)
    seguimiento_current = "TODOS"
    if seguimiento_raw in {"VENCIDAS", "POR_VENCER"}:
        seguimiento_current = seguimiento_raw
    seguimiento_labels = {
        "TODOS": "Todos",
        "VENCIDAS": "Vencidas",
        "POR_VENCER": "Por vencer",
    }
    seguimiento_label = seguimiento_labels.get(seguimiento_current, "Todos")
    hoy = date.today()
    alerta_dias = max(1, int(getattr(settings, "NOTA_VENCIMIENTO_ALERTA_DIAS", 5)))
    limite_alerta = hoy + timedelta(days=alerta_dias)
    vencimiento_from_raw = (request.query_params.get("vence_desde") or "").strip()
    vencimiento_to_raw = (request.query_params.get("vence_hasta") or "").strip()
    vencimiento_from = None
    vencimiento_to = None
    vencimiento_error = None
    if vencimiento_from_raw:
        try:
            vencimiento_from = datetime.strptime(vencimiento_from_raw, "%Y-%m-%d").date()
        except ValueError:
            vencimiento_error = "La fecha inicial de vencimiento no es valida."
    if vencimiento_to_raw:
        try:
            vencimiento_to = datetime.strptime(vencimiento_to_raw, "%Y-%m-%d").date()
        except ValueError:
            vencimiento_error = "La fecha final de vencimiento no es valida."
    if vencimiento_from and vencimiento_to and vencimiento_from > vencimiento_to:
        vencimiento_from, vencimiento_to = vencimiento_to, vencimiento_from
        vencimiento_from_raw = vencimiento_from.isoformat()
        vencimiento_to_raw = vencimiento_to.isoformat()
    proveedores_query = db.query(Proveedor).filter(Proveedor.activo.is_(True)).order_by(Proveedor.nombre_completo)
    if allowed_suc_ids:
        proveedores_query = proveedores_query.filter(Proveedor.sucursal_id.in_(allowed_suc_ids))
    proveedores_list = proveedores_query.all()
    proveedores = {p.id: p for p in proveedores_list}
    if proveedor_filter_id and proveedor_filter_id not in proveedores:
        proveedor_filter_error = "No tienes acceso a ese proveedor o no existe."
        proveedor_filter_id = None

    notas_scope_query = db.query(Nota).filter(
        Nota.tipo_operacion.in_([TipoOperacion.compra, TipoOperacion.venta]),
    )
    notas_scope_query = _apply_sucursal_filter(notas_scope_query, allowed_suc_ids, sucursal_id, Nota.sucursal_id)
    if proveedor_filter_id:
        notas_scope_query = notas_scope_query.filter(Nota.proveedor_id == proveedor_filter_id)
    notas_scope = notas_scope_query.order_by(Nota.created_at.desc(), Nota.id.desc()).all()
    note_effective_balances = _build_effective_note_balance_map(
        db,
        notas_scope,
        allowed_suc_ids=allowed_suc_ids,
        sucursal_id=sucursal_id,
    )

    def note_balance_view(nota: Nota) -> dict[str, Decimal | bool]:
        return note_effective_balances.get(nota.id) or {
            **_raw_note_payment_balance(nota),
            "ajuste_aplicado": Decimal("0"),
            "saldo_cubierto_por_ajuste": False,
            "saldo_parcialmente_cubierto": False,
        }

    def note_matches_due_window(nota: Nota) -> bool:
        if not vencimiento_from and not vencimiento_to:
            return True
        if not nota.fecha_caducidad_pago:
            return False
        if vencimiento_from and nota.fecha_caducidad_pago < vencimiento_from:
            return False
        if vencimiento_to and nota.fecha_caducidad_pago > vencimiento_to:
            return False
        return True

    def is_note_effectively_pending(nota: Nota) -> bool:
        if nota.estado != NotaEstado.aprobada:
            return False
        return Decimal(str(note_balance_view(nota)["saldo_pendiente"])) > Decimal("0")

    def is_note_effectively_paid(nota: Nota) -> bool:
        if nota.estado != NotaEstado.aprobada:
            return False
        return Decimal(str(note_balance_view(nota)["saldo_pendiente"])) <= Decimal("0")

    notas_revision = [nota for nota in notas_scope if nota.estado == NotaEstado.en_revision]
    notas_recientes = notas_scope[:10]
    notas_aprobadas = [nota for nota in notas_scope if nota.estado == NotaEstado.aprobada]
    notas_con_vencimiento = [
        nota
        for nota in notas_aprobadas
        if nota.fecha_caducidad_pago is not None
    ]
    estado_counts = {e.value: 0 for e in NotaEstado}
    for nota in notas_scope:
        if not note_matches_due_window(nota):
            continue
        if nota.estado and nota.estado.value in estado_counts:
            estado_counts[nota.estado.value] += 1
    estado_total = sum(1 for nota in notas_scope if note_matches_due_window(nota))

    pago_source = notas_scope
    if vencimiento_from or vencimiento_to:
        pago_source = [nota for nota in pago_source if note_matches_due_window(nota)]
    if estado_filter:
        pago_source = [nota for nota in pago_source if nota.estado == estado_filter]
    pago_counts = {
        "PAGADAS": sum(1 for nota in pago_source if is_note_effectively_paid(nota)),
        "PENDIENTES": sum(1 for nota in pago_source if is_note_effectively_pending(nota)),
    }
    pago_total = pago_counts["PAGADAS"] + pago_counts["PENDIENTES"]

    seguimiento_source = notas_scope
    if vencimiento_from or vencimiento_to:
        seguimiento_source = [nota for nota in seguimiento_source if note_matches_due_window(nota)]
    if estado_filter:
        seguimiento_source = [nota for nota in seguimiento_source if nota.estado == estado_filter]
    if pago_filter == "PAGADAS":
        seguimiento_source = [nota for nota in seguimiento_source if is_note_effectively_paid(nota)]
    elif pago_filter == "PENDIENTES":
        seguimiento_source = [nota for nota in seguimiento_source if is_note_effectively_pending(nota)]

    def is_overdue(nota: Nota) -> bool:
        return (
            nota.estado == NotaEstado.aprobada
            and nota.fecha_caducidad_pago is not None
            and nota.fecha_caducidad_pago < hoy
            and is_note_effectively_pending(nota)
        )

    def is_upcoming(nota: Nota) -> bool:
        return (
            nota.estado == NotaEstado.aprobada
            and nota.fecha_caducidad_pago is not None
            and nota.fecha_caducidad_pago >= hoy
            and nota.fecha_caducidad_pago <= limite_alerta
            and is_note_effectively_pending(nota)
        )

    seguimiento_counts = {
        "VENCIDAS": sum(1 for nota in seguimiento_source if is_overdue(nota)),
        "POR_VENCER": sum(1 for nota in seguimiento_source if is_upcoming(nota)),
    }

    notas_estado = list(notas_scope)
    if vencimiento_from or vencimiento_to:
        notas_estado = [nota for nota in notas_estado if note_matches_due_window(nota)]
    if estado_filter:
        notas_estado = [nota for nota in notas_estado if nota.estado == estado_filter]
    if pago_filter == "PAGADAS":
        notas_estado = [nota for nota in notas_estado if is_note_effectively_paid(nota)]
    elif pago_filter == "PENDIENTES":
        notas_estado = [nota for nota in notas_estado if is_note_effectively_pending(nota)]
    if seguimiento_current == "VENCIDAS":
        notas_estado = [nota for nota in notas_estado if is_overdue(nota)]
    elif seguimiento_current == "POR_VENCER":
        notas_estado = [nota for nota in notas_estado if is_upcoming(nota)]
    # Punto 9 (fase 2): el explorador puede ordenarse de la más antigua a la más
    # reciente. El resto de la página (recientes, revisión, vencidas) conserva su
    # orden propio. El corte a 200 se aplica DESPUÉS de ordenar, para que "antiguas"
    # muestre efectivamente las primeras notas y no las últimas.
    orden_notas_raw = (request.query_params.get("orden") or "").strip().lower()
    orden_notas = "antiguas" if orden_notas_raw == "antiguas" else "recientes"
    if orden_notas == "antiguas":
        notas_estado.sort(key=lambda item: (item.created_at or datetime.min, item.id))
    notas_estado = notas_estado[:200]

    saldo_vivo_total = Decimal("0")
    for nota in notas_aprobadas:
        if not note_matches_due_window(nota):
            continue
        saldo_vivo_total += Decimal(str(note_balance_view(nota)["saldo_pendiente"]))

    total_seleccion = Decimal("0")
    for nota in notas_estado:
        balance = note_balance_view(nota)
        if nota.estado == NotaEstado.aprobada:
            total_seleccion += Decimal(str(balance["saldo_pendiente"]))
        else:
            total_seleccion += Decimal(str(nota.total_monto or 0))

    notas_vencidas = []
    notas_por_vencer = []
    for nota in sorted(notas_con_vencimiento, key=lambda item: item.fecha_caducidad_pago or hoy):
        if not note_matches_due_window(nota):
            continue
        balance = note_balance_view(nota)
        saldo = Decimal(str(balance["saldo_pendiente"]))
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
            folio_error = "Formato de folio invalido. Usa 01_C_1 o 01_V_1."
        else:
            folio_sucursal_id, tipo_op, seq = parsed
            folio_result = (
                db.query(Nota)
                .filter(
                    Nota.sucursal_id == folio_sucursal_id,
                    Nota.tipo_operacion == tipo_op,
                    Nota.folio_seq == seq,
                )
                .first()
            )
            if folio_result and allowed_suc_ids and folio_result.sucursal_id not in allowed_suc_ids:
                folio_result = None
                folio_error = "No tienes acceso a esa sucursal."
            if folio_result and sucursal_id and folio_result.sucursal_id != sucursal_id:
                folio_result = None
                folio_error = "Ese folio pertenece a otra sucursal."
            if not folio_result and not folio_error:
                folio_error = "No se encontr\u00f3 una nota con ese folio."
    sucursales = {s.id: s for s in db.query(Sucursal).order_by(Sucursal.nombre).all()}
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
    estado_links = _build_notas_estado_links(
        folio_query,
        pago_current,
        sucursal_id,
        proveedor_filter_id,
        vencimiento_from_raw,
        vencimiento_to_raw,
        orden_notas,
    )
    pago_links = _build_notas_pago_links(
        folio_query,
        estado_current,
        sucursal_id,
        proveedor_filter_id,
        vencimiento_from_raw,
        vencimiento_to_raw,
        orden_notas,
    )
    sucursal_links = _build_notas_sucursal_links(
        sucursales_list,
        folio_query=folio_query,
        estado_filter=estado_current,
        pago_filter=pago_current,
        proveedor_id=proveedor_filter_id,
        vencimiento_from=vencimiento_from_raw,
        vencimiento_to=vencimiento_to_raw,
        orden=orden_notas,
    )
    seguimiento_links = _build_notas_seguimiento_links(
        folio_query=folio_query,
        estado_filter=estado_current,
        pago_filter=pago_current,
        sucursal_id=sucursal_id,
        proveedor_id=proveedor_filter_id,
        vencimiento_from=vencimiento_from_raw,
        vencimiento_to=vencimiento_to_raw,
        orden=orden_notas,
    )
    orden_base_params = {
        key: value
        for key, value in request.query_params.items()
        if key != "orden" and value
    }
    orden_links = {
        "recientes": _append_query_params("/web/admin/notas", **orden_base_params),
        "antiguas": _append_query_params("/web/admin/notas", **orden_base_params, orden="antiguas"),
    }
    sucursal_label = "Todas las sucursales"
    if sucursal_id:
        sucursal = sucursales.get(sucursal_id)
        sucursal_label = sucursal.nombre if sucursal else f"Sucursal {sucursal_id}"
    proveedor_filter = proveedores.get(proveedor_filter_id) if proveedor_filter_id else None
    if vencimiento_from and vencimiento_to:
        vencimiento_scope_label = f"{format_date_local(vencimiento_from)} al {format_date_local(vencimiento_to)}"
    elif vencimiento_from:
        vencimiento_scope_label = f"Desde {format_date_local(vencimiento_from)}"
    elif vencimiento_to:
        vencimiento_scope_label = f"Hasta {format_date_local(vencimiento_to)}"
    else:
        vencimiento_scope_label = "Todas"

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
            "hoy": hoy,
            "limite_alerta": limite_alerta,
            "alerta_dias": alerta_dias,
            "saldo_vivo_total": saldo_vivo_total,
            "notas_aprobadas_total": len(notas_aprobadas),
            "sucursales": sucursales,
            "sucursal_id": sucursal_id,
            "sucursal_label": sucursal_label,
            "sucursal_links": sucursal_links,
            "proveedores": proveedores,
            "proveedores_list": proveedores_list,
            "clientes": clientes,
            "folio_query": folio_query,
            "folio_error": folio_error,
            "folio_result": folio_result,
            "folio_map": folio_map,
            "proveedor_filter_id": proveedor_filter_id,
            "proveedor_filter": proveedor_filter,
            "proveedor_filter_error": proveedor_filter_error,
            "vencimiento_from": vencimiento_from_raw,
            "vencimiento_to": vencimiento_to_raw,
            "vencimiento_scope_label": vencimiento_scope_label,
            "vencimiento_error": vencimiento_error,
            "note_effective_balances": note_effective_balances,
            "notas_estado": notas_estado,
            "estado_current": estado_current,
            "estado_label": estado_label,
            "estado_counts": estado_counts,
            "estado_total": estado_total,
            "estado_links": estado_links,
            "pago_current": pago_current,
            "pago_label": pago_label,
            "pago_counts": pago_counts,
            "pago_total": pago_total,
            "pago_links": pago_links,
            "total_seleccion": total_seleccion,
            "seguimiento_current": seguimiento_current,
            "seguimiento_label": seguimiento_label,
            "seguimiento_counts": seguimiento_counts,
            "seguimiento_links": seguimiento_links,
            "orden_notas": orden_notas,
            "orden_links": orden_links,
        },
    )


def _transfer_stock_map(db: Session, sucursales: list[Sucursal]) -> str:
    """{sucursal_id: {material_id: kg}} como JSON para el hint de existencias.

    Transferir sin ver la disponibilidad del origen era el hueco operativo
    de la pantalla: se podía capturar más de lo que hay.
    """
    import json

    suc_ids = [s.id for s in sucursales]
    if not suc_ids:
        return "{}"
    rows = db.query(Inventario).filter(Inventario.sucursal_id.in_(suc_ids)).all()
    mapping: dict[str, dict[str, float]] = {}
    for inv in rows:
        mapping.setdefault(str(inv.sucursal_id), {})[str(inv.material_id)] = float(inv.stock_actual or 0)
    return json.dumps(mapping)


@router.get("/transferencias")
async def transferencias_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.orden_display, Material.nombre).all()
    sucursales = _active_sucursales(db)
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
            "stock_map_json": _transfer_stock_map(db, sucursales),
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
    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.orden_display, Material.nombre).all()
    sucursales = _active_sucursales(db)
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
                "stock_map_json": _transfer_stock_map(db, sucursales),
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


def _parse_precio_overrides(
    form: dict,
    nota: Nota,
) -> tuple[
    dict[int, dict[str, Decimal]],
    dict[int, str],
    dict[int, str],
    dict[int, str],
    str | None,
]:
    precio_override_map: dict[int, dict[str, Decimal]] = {}
    form_precio_unit_map: dict[int, str] = {}
    form_subtotal_map: dict[int, str] = {}
    form_precio_mode_map: dict[int, str] = {}

    for nm in nota.materiales:
        unit_raw = (form.get(f"precio_unitario_{nm.id}") or "").strip()
        subtotal_raw = (form.get(f"subtotal_{nm.id}") or "").strip()
        mode_raw = (form.get(f"precio_mode_{nm.id}") or "").strip().lower()

        if unit_raw != "":
            form_precio_unit_map[nm.id] = unit_raw
        if subtotal_raw != "":
            form_subtotal_map[nm.id] = subtotal_raw
        if mode_raw:
            if mode_raw not in ("unit", "subtotal"):
                mode_raw = "unit"
            form_precio_mode_map[nm.id] = mode_raw

        if not unit_raw and not subtotal_raw:
            continue

        selected_mode = None
        selected_raw = None
        if mode_raw == "subtotal" and subtotal_raw:
            selected_mode = "subtotal"
            selected_raw = subtotal_raw
        elif mode_raw != "subtotal" and unit_raw:
            selected_mode = "unit"
            selected_raw = unit_raw
        elif subtotal_raw:
            selected_mode = "subtotal"
            selected_raw = subtotal_raw
        elif unit_raw:
            selected_mode = "unit"
            selected_raw = unit_raw

        if selected_raw is None:
            continue
        try:
            value = Decimal(str(selected_raw))
        except (InvalidOperation, TypeError):
            return (
                precio_override_map,
                form_precio_unit_map,
                form_subtotal_map,
                form_precio_mode_map,
                "El precio ingresado es invalido.",
            )
        if value < 0:
            return (
                precio_override_map,
                form_precio_unit_map,
                form_subtotal_map,
                form_precio_mode_map,
                "El precio no puede ser negativo.",
            )

        if selected_mode == "unit":
            precio_override_map[nm.id] = {"precio_unitario": value}
            form_precio_mode_map[nm.id] = "unit"
        else:
            precio_override_map[nm.id] = {"subtotal": value}
            form_precio_mode_map[nm.id] = "subtotal"

    return (
        precio_override_map,
        form_precio_unit_map,
        form_subtotal_map,
        form_precio_mode_map,
        None,
    )



def _render_nota_detail(
    request: Request,
    db: Session,
    current_user: dict,
    nota: Nota,
    error: str | None = None,
    form_state: dict | None = None,
    pago_updated: bool = False,
    pago_reverted: bool = False,
    pago_inicial_updated: bool = False,
    precios_updated: bool = False,
    edit_updated: bool = False,
    devolucion_parcial_updated: bool = False,
    devolucion_parcial_reverted: bool = False,
    devolucion_total_reverted: bool = False,
):
    sucursal = db.get(Sucursal, nota.sucursal_id) if nota.sucursal_id else None
    inventario_sucursal_id = note_service.get_inventory_sucursal_id(nota)
    inventario_sucursal = db.get(Sucursal, inventario_sucursal_id) if inventario_sucursal_id else None
    proveedor = db.get(Proveedor, nota.proveedor_id) if nota.proveedor_id else None
    cliente = db.get(Cliente, nota.cliente_id) if nota.cliente_id else None
    trabajador = db.get(User, nota.trabajador_id) if nota.trabajador_id else None
    partner_kind, _ = _nota_partner_key(nota)
    if partner_kind == "proveedor":
        partner_label = "Proveedor"
        partner_name = proveedor.nombre_completo if proveedor else "-"
    elif partner_kind == "cliente":
        partner_label = "Cliente"
        partner_name = cliente.nombre_completo if cliente else "-"
    else:
        partner_label = "Partner"
        partner_name = "-"
    if nota.tipo_operacion == TipoOperacion.compra:
        operation_label = "Compra"
    elif nota.tipo_operacion == TipoOperacion.venta:
        operation_label = "Venta"
    else:
        operation_label = TIPO_OPERACION_LABELS.get(
            nota.tipo_operacion.value if nota.tipo_operacion else "", "Nota"
        )
    proveedores = db.query(Proveedor).filter(Proveedor.activo.is_(True)).order_by(Proveedor.nombre_completo).all()
    clientes = db.query(Cliente).filter(Cliente.activo.is_(True)).order_by(Cliente.nombre_completo).all()
    inv_movs = db.query(InventarioMovimiento).filter(InventarioMovimiento.nota_id == nota.id).all()
    note_adjustments = (
        db.query(InventarioAjusteManual)
        .filter(InventarioAjusteManual.nota_id == nota.id)
        .order_by(InventarioAjusteManual.created_at.desc(), InventarioAjusteManual.id.desc())
        .all()
    )
    note_balance_adjustments = (
        db.query(NotaAjusteSaldo)
        .filter(NotaAjusteSaldo.nota_id == nota.id)
        .order_by(NotaAjusteSaldo.created_at.desc(), NotaAjusteSaldo.id.desc())
        .all()
    )
    note_adjustment_mov_ids = {
        adj.inventario_movimiento_id
        for adj in note_adjustments
        if adj.inventario_movimiento_id is not None
    }
    note_adjustments_by_mov_id = {
        adj.inventario_movimiento_id: adj
        for adj in note_adjustments
        if adj.inventario_movimiento_id is not None
    }
    pagos = (
        db.query(NotaPago)
        .filter(NotaPago.nota_id == nota.id)
        .order_by(NotaPago.created_at.desc())
        .all()
    )
    devoluciones_parciales = (
        db.query(NotaDevolucionParcial)
        .filter(NotaDevolucionParcial.nota_id == nota.id)
        .order_by(NotaDevolucionParcial.created_at.desc(), NotaDevolucionParcial.id.desc())
        .all()
    )
    devoluciones_totales = (
        db.query(NotaDevolucionTotal)
        .filter(NotaDevolucionTotal.nota_id == nota.id)
        .order_by(NotaDevolucionTotal.created_at.desc(), NotaDevolucionTotal.id.desc())
        .all()
    )
    devolucion_total_activa = next((d for d in devoluciones_totales if not d.reverted_at), None)
    pago_inicial_total = Decimal("0")
    for pago in pagos:
        if pago.comentario and pago.comentario.lower().startswith("pago inicial"):
            pago_inicial_total += Decimal(str(pago.monto or 0))
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
            if mov.id in note_adjustment_mov_ids:
                continue
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
    note_balance_adjustment_total = _get_note_balance_adjustment_totals_map(db, [nota.id]).get(nota.id, Decimal("0"))
    balance_view = _raw_note_payment_balance(
        nota,
        note_adjustment_delta=note_balance_adjustment_total,
    )
    saldo_pendiente = Decimal(str(balance_view["saldo_pendiente"]))
    saldo_a_favor = Decimal(str(balance_view["saldo_favor"]))
    pagos_activos = [p for p in pagos if Decimal(str(p.monto or 0)) > Decimal("0")]
    pagos_revertidos = [p for p in pagos if Decimal(str(p.monto or 0)) <= Decimal("0")]
    pagos_activos_total = sum((Decimal(str(p.monto or 0)) for p in pagos_activos), Decimal("0"))
    iva_monto = Decimal(str(nota.iva_monto or 0))
    total_monto = Decimal(str(nota.total_monto or 0))
    subtotal_sin_iva = total_monto - iva_monto if iva_monto else total_monto
    if subtotal_sin_iva < Decimal("0"):
        subtotal_sin_iva = Decimal("0")
    iva_pct_raw = nota.iva_porcentaje if nota.iva_porcentaje is not None else Decimal("16.00")
    iva_pct = Decimal(str(iva_pct_raw or 0))
    folio = note_service.format_folio(
        sucursal_id=nota.sucursal_id,
        tipo_operacion=nota.tipo_operacion,
        folio_seq=nota.folio_seq,
    )
    if not folio and nota.estado in (NotaEstado.borrador, NotaEstado.en_revision):
        folio = "Pendiente"
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
    cuentas_scrap360 = _get_scrap360_cuentas_for_nota(db, nota)
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    cash_sucursales = _filter_sucursales_for_admin(
        _active_sucursales(db),
        allowed_suc_ids,
    )
    note_material_options: list[dict] = []
    note_inventory_stock_map: dict[int, Decimal] = {}
    material_ids = [m.material_id for m in nota.materiales if m.material_id]
    if inventario_sucursal_id and material_ids:
        inv_rows = (
            db.query(Inventario)
            .filter(
                Inventario.sucursal_id == inventario_sucursal_id,
                Inventario.material_id.in_(material_ids),
            )
            .all()
        )
        note_inventory_stock_map = {
            inv.material_id: Decimal(str(inv.stock_actual or 0))
            for inv in inv_rows
        }
    for nm in nota.materiales:
        material_name = nm.material.nombre if nm.material else f"Material #{nm.material_id}"
        note_material_options.append(
            {
                "nota_material_id": nm.id,
                "material_id": nm.material_id,
                "material_name": material_name,
                "kg_neto": Decimal(str(nm.kg_neto or 0)),
                "stock_actual": note_inventory_stock_map.get(nm.material_id, Decimal("0")),
            }
        )
    if partner_kind == "cliente":
        cuentas_partner_label = "Cliente"
    elif partner_kind == "proveedor":
        cuentas_partner_label = "Proveedor"
    else:
        cuentas_partner_label = "Partner"
    can_manage_note = not _is_read_only_admin_user(current_user)
    base_form_state = {
        "form_metodo": None,
        "form_cuenta": None,
        "form_fecha": None,
        "form_comentarios": None,
        "form_pagado": None,
        "form_cuenta_scrap360": None,
        "form_caja_sucursal_id": nota.sucursal_id,
        "form_iva_incluido": None,
        "form_iva_porcentaje": None,
        "form_pago_monto": None,
        "form_pago_metodo": None,
        "form_pago_cuenta": None,
        "form_pago_comentario": None,
        "form_pago_cuenta_scrap360": None,
        "form_pago_caja_sucursal_id": nota.sucursal_id,
        "form_pago_inicial_monto": None,
        "form_pago_inicial_metodo": None,
        "form_pago_inicial_cuenta": None,
        "form_pago_inicial_comentario": None,
        "form_pago_inicial_cuenta_scrap360": None,
        "form_pago_inicial_caja_sucursal_id": nota.sucursal_id,
        "form_devol_kg_map": {},
        "form_devol_precio_map": {},
        "form_devol_comment": None,
        "form_precio_unit_map": {},
        "form_subtotal_map": {},
        "form_precio_mode_map": {},
        "form_kg_real_map": {},
        "form_ajuste_nota_material_id": None,
        "form_ajuste_operacion": "aumentar",
        "form_ajuste_cantidad": None,
        "form_ajuste_comentario": None,
        "form_ajuste_saldo_tipo": "reducir",
        "form_ajuste_saldo_monto": None,
        "form_ajuste_saldo_comentario": None,
        "form_inventario_sucursal_id": inventario_sucursal_id or nota.sucursal_id,
    }
    context = {
        "request": request,
        "env": settings.ENV,
        "user": current_user,
        "nota": nota,
        "sucursal": sucursal,
        "inventario_sucursal": inventario_sucursal,
        "inventario_sucursal_diff": bool(inventario_sucursal and sucursal and inventario_sucursal.id != sucursal.id),
        "proveedor": proveedor,
        "cliente": cliente,
        "trabajador": trabajador,
        "partner_label": partner_label,
        "partner_name": partner_name,
        "operation_label": operation_label,
        "tipos_cliente": list(TipoCliente),
        "inv_movs": inv_movs,
        "note_adjustments": note_adjustments,
        "note_balance_adjustments": note_balance_adjustments,
        "note_adjustments_by_mov_id": note_adjustments_by_mov_id,
        "note_adjustment_active_count": len(
            [adj for adj in note_adjustments if not adj.reverted_at and not adj.reversal_of_id]
        ),
        "note_adjustment_reverted_count": len(
            [adj for adj in note_adjustments if adj.reverted_at]
        ),
        "note_adjustment_reversion_count": len(
            [adj for adj in note_adjustments if adj.reversal_of_id]
        ),
        "note_balance_adjustment_total": note_balance_adjustment_total,
        "note_balance_adjustment_active_count": len(
            [adj for adj in note_balance_adjustments if not adj.reverted_at and not adj.reversal_of_id]
        ),
        "note_balance_adjustment_reverted_count": len(
            [adj for adj in note_balance_adjustments if adj.reverted_at]
        ),
        "note_balance_adjustment_reversion_count": len(
            [adj for adj in note_balance_adjustments if adj.reversal_of_id]
        ),
        "note_material_options": note_material_options,
        "pagos": pagos,
        "pago_inicial_total": pago_inicial_total,
        "price_map_json": price_map_json,
        "price_map_by_material": price_map_by_material,
        "saldo_pendiente": saldo_pendiente,
        "saldo_a_favor": saldo_a_favor,
        "pagos_activos_total": pagos_activos_total,
        "pagos_activos_count": len(pagos_activos),
        "pagos_revertidos_count": len(pagos_revertidos),
        "subtotal_sin_iva": subtotal_sin_iva,
        "iva_monto": iva_monto,
        "iva_pct": iva_pct,
        "folio": folio,
        "is_transfer": is_transfer,
        "transfer_related": transfer_related,
        "transfer_related_sucursal": transfer_related_sucursal,
        "cuentas_sucursal": cuentas_sucursal,
        "cuentas_partner": cuentas_partner,
        "cuentas_partner_label": cuentas_partner_label,
        "cuentas_scrap360": cuentas_scrap360,
        "inventory_sucursales": cash_sucursales,
        "cash_sucursales": cash_sucursales,
        "pago_updated": pago_updated,
        "pago_reverted": pago_reverted,
        "ajuste_manual_updated": request.query_params.get("ajuste_manual") == "1",
        "ajuste_manual_reverted": request.query_params.get("ajuste_manual_revertido") == "1",
        "ajuste_saldo_updated": request.query_params.get("ajuste_saldo") == "1",
        "ajuste_saldo_reverted": request.query_params.get("ajuste_saldo_revertido") == "1",
        "pago_inicial_updated": pago_inicial_updated,
        "precios_updated": precios_updated,
        "edit_updated": edit_updated,
        "devolucion_parcial_updated": devolucion_parcial_updated,
        "devolucion_parcial_reverted": devolucion_parcial_reverted,
        "devolucion_total_reverted": devolucion_total_reverted,
        "devolucion_check": devolucion_check,
        "devoluciones_parciales": devoluciones_parciales,
        "devoluciones_totales": devoluciones_totales,
        "devolucion_total_activa": devolucion_total_activa,
        "error": error,
        "proveedores": proveedores,
        "clientes": clientes,
        "can_manage_note": can_manage_note,
        "can_edit_note": current_user.get("rol") == UserRole.super_admin.value,
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
    form_precio_unit_map: dict[int, str] | None = None,
    form_subtotal_map: dict[int, str] | None = None,
    form_precio_mode_map: dict[int, str] | None = None,
    form_kg_neto_map: dict[int, str] | None = None,
    form_kg_desc_map: dict[int, str] | None = None,
    form_kg_real_map: dict[int, str] | None = None,
    saved: bool = False,
):
    sucursal = db.get(Sucursal, nota.sucursal_id) if nota.sucursal_id else None
    inventario_sucursal_id = note_service.get_inventory_sucursal_id(nota)
    inventario_sucursal = db.get(Sucursal, inventario_sucursal_id) if inventario_sucursal_id else None
    proveedor = db.get(Proveedor, nota.proveedor_id) if nota.proveedor_id else None
    cliente = db.get(Cliente, nota.cliente_id) if nota.cliente_id else None
    trabajador = db.get(User, nota.trabajador_id) if nota.trabajador_id else None
    partner_kind, _ = _nota_partner_key(nota)
    if partner_kind == "proveedor":
        partner_label = "Proveedor"
        partner_name = proveedor.nombre_completo if proveedor else "-"
    elif partner_kind == "cliente":
        partner_label = "Cliente"
        partner_name = cliente.nombre_completo if cliente else "-"
    else:
        partner_label = "Partner"
        partner_name = "-"
    if nota.tipo_operacion == TipoOperacion.compra:
        operation_label = "Compra"
    elif nota.tipo_operacion == TipoOperacion.venta:
        operation_label = "Venta"
    else:
        operation_label = TIPO_OPERACION_LABELS.get(
            nota.tipo_operacion.value if nota.tipo_operacion else "", "Nota"
        )
    note_balance_adjustment_total = _get_note_balance_adjustment_totals_map(db, [nota.id]).get(nota.id, Decimal("0"))
    balance_view = _raw_note_payment_balance(
        nota,
        note_adjustment_delta=note_balance_adjustment_total,
    )
    saldo_pendiente = Decimal(str(balance_view["saldo_pendiente"]))
    saldo_a_favor = Decimal(str(balance_view["saldo_favor"]))
    iva_monto = Decimal(str(nota.iva_monto or 0))
    total_monto = Decimal(str(nota.total_monto or 0))
    subtotal_sin_iva = total_monto - iva_monto if iva_monto else total_monto
    if subtotal_sin_iva < Decimal("0"):
        subtotal_sin_iva = Decimal("0")
    iva_pct_raw = nota.iva_porcentaje if nota.iva_porcentaje is not None else Decimal("16.00")
    iva_pct = Decimal(str(iva_pct_raw or 0))
    folio = note_service.format_folio(
        sucursal_id=nota.sucursal_id,
        tipo_operacion=nota.tipo_operacion,
        folio_seq=nota.folio_seq,
    )
    if not folio and nota.estado in (NotaEstado.borrador, NotaEstado.en_revision):
        folio = "Pendiente"
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
            "inventario_sucursal": inventario_sucursal,
            "inventario_sucursal_diff": bool(inventario_sucursal and sucursal and inventario_sucursal.id != sucursal.id),
            "proveedor": proveedor,
            "cliente": cliente,
            "trabajador": trabajador,
            "partner_label": partner_label,
            "partner_name": partner_name,
            "operation_label": operation_label,
            "tipos_cliente": list(TipoCliente),
            "saldo_pendiente": saldo_pendiente,
            "saldo_a_favor": saldo_a_favor,
            "note_balance_adjustment_total": note_balance_adjustment_total,
            "subtotal_sin_iva": subtotal_sin_iva,
            "iva_monto": iva_monto,
            "iva_pct": iva_pct,
            "folio": folio,
            "is_transfer": is_transfer,
            "transfer_related": transfer_related,
            "transfer_related_sucursal": transfer_related_sucursal,
            "comentario_edicion": comentario_edicion or "",
            "form_precio_unit_map": form_precio_unit_map or {},
            "form_subtotal_map": form_subtotal_map or {},
            "form_precio_mode_map": form_precio_mode_map or {},
            "form_kg_neto_map": form_kg_neto_map or {},
            "form_kg_desc_map": form_kg_desc_map or {},
            "form_kg_real_map": form_kg_real_map or {},
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
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_nota_access(nota, allowed_suc_ids)

    pago_updated = request.query_params.get("pago") == "1"
    pago_reverted = request.query_params.get("pago_revertido") == "1"
    pago_inicial_updated = request.query_params.get("pago_inicial") == "1"
    precios_updated = request.query_params.get("precios") == "1"
    edit_updated = request.query_params.get("edit") == "1"
    devolucion_parcial_updated = request.query_params.get("devolucion_parcial") == "1"
    devolucion_parcial_reverted = request.query_params.get("devolucion_parcial_revertida") == "1"
    devolucion_total_reverted = request.query_params.get("devolucion_total_revertida") == "1"
    return _render_nota_detail(
        request,
        db,
        current_user,
        nota,
        pago_updated=pago_updated,
        pago_reverted=pago_reverted,
        pago_inicial_updated=pago_inicial_updated,
        precios_updated=precios_updated,
        edit_updated=edit_updated,
        devolucion_parcial_updated=devolucion_parcial_updated,
        devolucion_parcial_reverted=devolucion_parcial_reverted,
        devolucion_total_reverted=devolucion_total_reverted,
    )


@router.get("/notas/{nota_id}/evidencias")
async def notas_evidencias(
    nota_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
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
    partner_kind, _ = _nota_partner_key(nota)
    if partner_kind == "proveedor":
        partner_label = "Proveedor"
        partner_name = proveedor.nombre_completo if proveedor else "-"
    elif partner_kind == "cliente":
        partner_label = "Cliente"
        partner_name = cliente.nombre_completo if cliente else "-"
    else:
        partner_label = "Partner"
        partner_name = "-"

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
            "can_upload": not _is_read_only_admin_user(current_user),
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
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_nota_access(nota, allowed_suc_ids)
    if nota.estado != NotaEstado.aprobada:
        raise HTTPException(status_code=400, detail="La nota debe estar aprobada.")
    if nota.tipo_operacion not in (TipoOperacion.compra, TipoOperacion.venta):
        raise HTTPException(status_code=400, detail="La nota no genera factura.")

    pdf_bytes, filename = invoice_service.build_invoice_pdf(db, nota)
    if current_user.get("rol") == UserRole.super_admin.value:
        try:
            needs_upload = not (
                nota.factura_url
                and nota.factura_generada_at
                and nota.updated_at
                and nota.factura_generada_at >= nota.updated_at
            )
            if needs_upload:
                factura_url = invoice_service.upload_invoice_pdf(pdf_bytes, filename, nota.id)
                if factura_url:
                    nota.factura_url = factura_url
                    nota.factura_generada_at = datetime.utcnow()
                    db.add(nota)
                    db.commit()
        except Exception:
            db.rollback()

    disposition = "attachment"
    if (request.query_params.get("inline") or "").strip() == "1":
        disposition = "inline"
    headers = {"Content-Disposition": f'{disposition}; filename="{filename}"'}
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
    (
        kg_neto_override_map,
        kg_desc_override_map,
        form_kg_neto_map,
        form_kg_desc_map,
        kg_error,
    ) = _parse_kg_overrides(form, nota)
    (
        kg_real_override_map,
        form_kg_real_map,
        kg_real_error,
    ) = _parse_real_kg_overrides(form, nota)
    if kg_error:
        return _render_nota_edit(
            request,
            db,
            current_user,
            nota,
            error=kg_error,
            comentario_edicion=comentario_edicion,
            form_kg_neto_map=form_kg_neto_map,
            form_kg_desc_map=form_kg_desc_map,
            form_kg_real_map=form_kg_real_map,
        )
    if kg_real_error:
        return _render_nota_edit(
            request,
            db,
            current_user,
            nota,
            error=kg_real_error,
            comentario_edicion=comentario_edicion,
            form_kg_neto_map=form_kg_neto_map,
            form_kg_desc_map=form_kg_desc_map,
            form_kg_real_map=form_kg_real_map,
        )
    (
        precio_override_map,
        form_precio_unit_map,
        form_subtotal_map,
        form_precio_mode_map,
        precio_error,
    ) = _parse_precio_overrides(form, nota)
    if precio_error:
        return _render_nota_edit(
            request,
            db,
            current_user,
            nota,
            error=precio_error,
            comentario_edicion=comentario_edicion,
            form_kg_neto_map=form_kg_neto_map,
            form_kg_desc_map=form_kg_desc_map,
            form_kg_real_map=form_kg_real_map,
            form_precio_unit_map=form_precio_unit_map,
            form_subtotal_map=form_subtotal_map,
            form_precio_mode_map=form_precio_mode_map,
        )

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
                kg_desc = kg_desc_override_map.get(nm.id, Decimal(str(nm.kg_descuento or 0)))
                kg_neto = kg_neto_override_map.get(nm.id, Decimal(str(nm.kg_neto or 0)))
                kg_mode = (form.get(f"kg_mode_{nm.id}") or "").strip().lower()
                if kg_mode not in ("net", "desc"):
                    if nm.id in kg_neto_override_map and nm.id not in kg_desc_override_map:
                        kg_mode = "net"
                    else:
                        kg_mode = "desc"

                if kg_bruto <= 0:
                    raise ValueError("El kg bruto debe ser mayor a 0.")
                if kg_desc < 0 or kg_neto < 0:
                    raise ValueError("Ni el kg neto ni el descuento pueden ser negativos.")

                if kg_mode == "net":
                    if kg_neto > kg_bruto:
                        raise ValueError("El kg neto no puede ser mayor al kg bruto.")
                    kg_desc = kg_bruto - kg_neto
                else:
                    if kg_desc > kg_bruto:
                        raise ValueError("El kg descuento no puede ser mayor al kg bruto.")
                    kg_neto = kg_bruto - kg_desc

                kg_override_map[nm.id] = (kg_bruto, kg_desc)

        note_service.edit_note_by_superadmin(
            db,
            nota,
            tipo_cliente_map=tipo_cliente_map,
            kg_override_map=kg_override_map,
            subpesaje_map=subpesaje_map,
            kg_real_override_map=kg_real_override_map or None,
            precio_override_map=precio_override_map or None,
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
            form_kg_neto_map=form_kg_neto_map,
            form_kg_desc_map=form_kg_desc_map,
            form_kg_real_map=form_kg_real_map,
            form_precio_unit_map=form_precio_unit_map,
            form_subtotal_map=form_subtotal_map,
            form_precio_mode_map=form_precio_mode_map,
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

    content = await file.read()
    resolved_content_type = resolve_image_content_type(file.filename, file.content_type)
    if not resolved_content_type:
        logger.warning(
            "Admin evidence upload rejected: invalid content type",
            extra={
                "user_id": current_user["id"],
                "nota_id": nota_id,
                "subpesaje_id": subpesaje_id,
                "upload_name": file.filename,
                "content_type": file.content_type,
            },
        )
        return RedirectResponse(
            url=f"/web/admin/notas/{nota_id}/evidencias?error=tipo",
            status_code=303,
        )
    if not content:
        logger.warning(
            "Admin evidence upload rejected: empty file",
            extra={
                "user_id": current_user["id"],
                "nota_id": nota_id,
                "subpesaje_id": subpesaje_id,
                "upload_name": file.filename,
            },
        )
        return RedirectResponse(
            url=f"/web/admin/notas/{nota_id}/evidencias?error=vacio",
            status_code=303,
        )
    max_bytes = settings.FIREBASE_MAX_MB * 1024 * 1024
    if len(content) > max_bytes:
        logger.warning(
            "Admin evidence upload rejected: file too large",
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
            url=f"/web/admin/notas/{nota_id}/evidencias?error=peso",
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
            "Admin evidence upload failed",
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
    auto_open_pdf = (form.get("auto_open_pdf") or "").strip() == "1"
    print_window = (form.get("print_window") or "").strip()
    fecha_caducidad_pago_raw = (form.get("fecha_caducidad_pago") or "").strip()
    metodo_pago = (form.get("metodo_pago") or "").strip().lower()
    numero_cheque = (form.get("numero_cheque") or "").strip()
    cuenta_financiera = (form.get("cuenta_financiera") or "").strip()
    cuenta_scrap360_raw = (form.get("cuenta_scrap360_id") or "").strip()
    inventario_sucursal_raw = (form.get("inventario_sucursal_id") or "").strip()
    caja_sucursal_raw = (form.get("caja_sucursal_id") or "").strip()
    monto_pagado_raw = (form.get("monto_pagado") or "").strip()
    iva_incluido = form.get("iva_incluido") is not None
    iva_porcentaje_raw = (form.get("iva_porcentaje") or "").strip()
    form_state = {
        "form_metodo": metodo_pago,
        "form_numero_cheque": numero_cheque,
        "form_cuenta": cuenta_financiera,
        "form_fecha": fecha_caducidad_pago_raw,
        "form_comentarios": comentarios_admin,
        "form_pagado": monto_pagado_raw,
        "form_cuenta_scrap360": cuenta_scrap360_raw,
        "form_inventario_sucursal_id": inventario_sucursal_raw,
        "form_caja_sucursal_id": caja_sucursal_raw,
        "form_iva_incluido": iva_incluido,
        "form_iva_porcentaje": iva_porcentaje_raw,
    }
    kg_neto_override_map = None
    kg_desc_override_map = None
    precio_override_map = None
    kg_real_override_map = None
    if current_user.get("rol") == UserRole.super_admin.value:
        (
            kg_neto_override_map,
            kg_desc_override_map,
            form_kg_neto_map,
            form_kg_desc_map,
            kg_error,
        ) = _parse_kg_overrides(form, nota)
        (
            kg_real_override_map,
            form_kg_real_map,
            kg_real_error,
        ) = _parse_real_kg_overrides(form, nota)
        form_state.update(
            {
                "form_kg_neto_map": form_kg_neto_map,
                "form_kg_desc_map": form_kg_desc_map,
                "form_kg_real_map": form_kg_real_map,
            }
        )
        if kg_error:
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error=kg_error,
                form_state=form_state,
            )
        if kg_real_error:
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error=kg_real_error,
                form_state=form_state,
            )
        (
            precio_override_map,
            form_precio_unit_map,
            form_subtotal_map,
            form_precio_mode_map,
            precio_error,
        ) = _parse_precio_overrides(form, nota)
        form_state.update(
            {
                "form_precio_unit_map": form_precio_unit_map,
                "form_subtotal_map": form_subtotal_map,
                "form_precio_mode_map": form_precio_mode_map,
            }
        )
        if precio_error:
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error=precio_error,
                form_state=form_state,
            )
        if not precio_override_map:
            precio_override_map = None
        if not kg_neto_override_map:
            kg_neto_override_map = None
        if not kg_desc_override_map:
            kg_desc_override_map = None
        if not kg_real_override_map:
            kg_real_override_map = None

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

    iva_porcentaje = None
    if iva_porcentaje_raw:
        try:
            iva_porcentaje = Decimal(str(iva_porcentaje_raw))
        except (InvalidOperation, TypeError):
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error="El porcentaje de IVA es invalido.",
                form_state=form_state,
            )

    cuenta_scrap360_id = None
    if cuenta_scrap360_raw:
        try:
            cuenta_scrap360_id = int(cuenta_scrap360_raw)
        except (TypeError, ValueError):
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error="La cuenta Scrap360 es invalida.",
                form_state=form_state,
            )

    inventario_sucursal_id = None
    if inventario_sucursal_raw:
        try:
            inventario_sucursal_id = int(inventario_sucursal_raw)
        except (TypeError, ValueError):
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error="La sucursal de inventario es invalida.",
                form_state=form_state,
            )

    caja_sucursal_id = None
    if caja_sucursal_raw:
        try:
            caja_sucursal_id = int(caja_sucursal_raw)
        except (TypeError, ValueError):
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error="La sucursal de caja es invalida.",
                form_state=form_state,
            )
    if metodo_pago == "efectivo" and caja_sucursal_id and allowed_suc_ids is not None and caja_sucursal_id not in allowed_suc_ids:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="No tienes acceso a la sucursal de caja seleccionada.",
            form_state=form_state,
        )

    try:
        if kg_neto_override_map or kg_desc_override_map:
            for nm in nota.materiales:
                if nm.subpesajes:
                    if (kg_neto_override_map and nm.id in kg_neto_override_map) or (
                        kg_desc_override_map and nm.id in kg_desc_override_map
                    ):
                        raise ValueError("No puedes ajustar kg neto/desc en materiales con subpesajes.")
                    continue

                kg_bruto = Decimal(str(nm.kg_bruto or 0))
                kg_neto = Decimal(str(nm.kg_neto or 0))
                kg_desc = Decimal(str(nm.kg_descuento or 0))

                if kg_neto_override_map and nm.id in kg_neto_override_map:
                    kg_neto = kg_neto_override_map[nm.id]
                if kg_desc_override_map and nm.id in kg_desc_override_map:
                    kg_desc = kg_desc_override_map[nm.id]

                if kg_neto < 0 or kg_desc < 0:
                    raise ValueError("Los kilogramos no pueden ser negativos.")

                if (kg_neto_override_map and nm.id in kg_neto_override_map) and (
                    kg_desc_override_map and nm.id in kg_desc_override_map
                ):
                    if kg_neto + kg_desc > kg_bruto:
                        raise ValueError("Kg neto + descuento no puede exceder el kg bruto.")
                elif kg_desc_override_map and nm.id in kg_desc_override_map:
                    if kg_desc > kg_bruto:
                        raise ValueError("El descuento no puede ser mayor que el kg bruto.")
                    kg_neto = kg_bruto - kg_desc
                elif kg_neto_override_map and nm.id in kg_neto_override_map:
                    if kg_neto > kg_bruto:
                        kg_neto = kg_bruto
                    kg_desc = kg_bruto - kg_neto

                nm.kg_neto = kg_neto
                nm.kg_descuento = kg_desc
                if kg_real_override_map and nm.id in kg_real_override_map:
                    nm.kg_real = kg_real_override_map[nm.id]
                else:
                    nm.kg_real = kg_neto
                db.add(nm)
            note_service._recalc_totals(nota)
            db.add(nota)
        note_service.approve_note(
            db,
            nota,
            tipo_cliente_map=tipo_cliente_map or None,
            precio_override_map=precio_override_map,
            kg_real_override_map=kg_real_override_map,
            admin_id=current_user.get("id"),
            comentarios_admin=comentarios_admin,
            fecha_caducidad_pago=fecha_caducidad_pago,
            metodo_pago=metodo_pago,
            numero_cheque=numero_cheque or None,
            cuenta_financiera=cuenta_financiera or None,
            cuenta_scrap360_id=cuenta_scrap360_id,
            monto_pagado=monto_pagado,
            iva_incluido=iva_incluido,
            iva_porcentaje=iva_porcentaje,
            inventario_sucursal_id=inventario_sucursal_id,
            caja_sucursal_id=caja_sucursal_id,
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

    if (
        current_user.get("rol") == UserRole.super_admin.value
        and auto_open_pdf
        and nota.tipo_operacion in (TipoOperacion.compra, TipoOperacion.venta)
    ):
        params = {"approved": "1", "auto_open_pdf": "1"}
        if print_window:
            params["print_window"] = print_window[:80]
        return RedirectResponse(
            url=f"/web/admin/notas/{nota.id}?{urlencode(params)}",
            status_code=303,
        )

    return RedirectResponse(url="/web/admin/notas?approved=1", status_code=303)


@router.post("/notas/{nota_id}/referencia-pago")
async def notas_referencia_pago(
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
            request, db, current_user, nota,
            error="Solo puedes editar la referencia de pago en notas aprobadas.",
        )
    form = await request.form()
    metodo_pago_raw = (form.get("metodo_pago") or "").strip().lower()
    numero_cheque_raw = (form.get("numero_cheque") or "").strip()
    cuenta_financiera_raw = (form.get("cuenta_financiera") or "").strip()

    cuenta_id: int | None = None
    if cuenta_financiera_raw:
        try:
            cuenta_id = int(cuenta_financiera_raw)
            if not db.get(Cuenta, cuenta_id):
                cuenta_id = None
        except (TypeError, ValueError):
            cuenta_id = None

    cuenta_scrap360_raw = (form.get("cuenta_scrap360_id") or "").strip()
    cuenta_scrap360_id: int | None = None
    if cuenta_scrap360_raw:
        try:
            cuenta_scrap360_id = int(cuenta_scrap360_raw)
            if not db.get(CuentaScrap360, cuenta_scrap360_id):
                cuenta_scrap360_id = None
        except (TypeError, ValueError):
            cuenta_scrap360_id = None

    nota.metodo_pago = metodo_pago_raw or None
    nota.numero_cheque = numero_cheque_raw or None
    nota.cuenta_financiera_id = cuenta_id
    nota.cuenta_scrap360_id = cuenta_scrap360_id
    db.add(nota)
    db.commit()
    return RedirectResponse(
        url=f"/web/admin/notas/{nota.id}?success=referencia_pago",
        status_code=303,
    )


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
    cuenta_scrap360_raw = (form.get("cuenta_scrap360_id") or "").strip()
    monto_pagado_raw = (form.get("monto_pagado") or "").strip()
    iva_incluido = form.get("iva_incluido") is not None
    iva_porcentaje_raw = (form.get("iva_porcentaje") or "").strip()
    form_state = {
        "form_metodo": metodo_pago,
        "form_cuenta": cuenta_financiera,
        "form_fecha": fecha_caducidad_pago_raw,
        "form_comentarios": comentarios_admin,
        "form_pagado": monto_pagado_raw,
        "form_cuenta_scrap360": cuenta_scrap360_raw,
        "form_iva_incluido": iva_incluido,
        "form_iva_porcentaje": iva_porcentaje_raw,
    }
    precio_override_map = None
    if current_user.get("rol") == UserRole.super_admin.value:
        (
            precio_override_map,
            form_precio_unit_map,
            form_subtotal_map,
            form_precio_mode_map,
            precio_error,
        ) = _parse_precio_overrides(form, nota)
        form_state.update(
            {
                "form_precio_unit_map": form_precio_unit_map,
                "form_subtotal_map": form_subtotal_map,
                "form_precio_mode_map": form_precio_mode_map,
            }
        )
        if precio_error:
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error=precio_error,
                form_state=form_state,
            )
        if not precio_override_map:
            precio_override_map = None

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
    if not tipo_cliente_map and not precio_override_map:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="No hay cambios de precio para actualizar.",
            form_state=form_state,
        )

    try:
        note_service.set_tipo_cliente_and_prices(
            db,
            nota,
            tipo_cliente_map,
            precio_override_map=precio_override_map,
        )
    except ValueError as exc:
        db.rollback()
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error=str(exc),
            form_state=form_state,
        )
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
    cuenta_scrap360_raw = (form.get("pago_cuenta_scrap360_id") or "").strip()
    caja_sucursal_raw = (form.get("pago_caja_sucursal_id") or "").strip()
    comentario = (form.get("pago_comentario") or "").strip()
    form_state = {
        "form_pago_monto": monto_pagado_raw,
        "form_pago_metodo": metodo_pago,
        "form_pago_cuenta": cuenta_financiera,
        "form_pago_comentario": comentario,
        "form_pago_cuenta_scrap360": cuenta_scrap360_raw,
        "form_pago_caja_sucursal_id": caja_sucursal_raw,
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

    cuenta_scrap360_id = None
    if cuenta_scrap360_raw:
        try:
            cuenta_scrap360_id = int(cuenta_scrap360_raw)
        except (TypeError, ValueError):
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error="La cuenta Scrap360 es invalida.",
                form_state=form_state,
            )

    caja_sucursal_id = None
    if caja_sucursal_raw:
        try:
            caja_sucursal_id = int(caja_sucursal_raw)
        except (TypeError, ValueError):
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error="La sucursal de caja es invalida.",
                form_state=form_state,
            )
    if metodo_pago == "efectivo" and caja_sucursal_id and allowed_suc_ids is not None and caja_sucursal_id not in allowed_suc_ids:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="No tienes acceso a la sucursal de caja seleccionada.",
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
            cuenta_scrap360_id=cuenta_scrap360_id,
            caja_sucursal_id=caja_sucursal_id,
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


@router.post("/notas/{nota_id}/pago/{pago_id}/deshacer")
async def notas_deshacer_pago(
    nota_id: int,
    pago_id: int,
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
            error="Solo puedes deshacer pagos en notas aprobadas.",
        )

    pago = (
        db.query(NotaPago)
        .filter(
            NotaPago.id == pago_id,
            NotaPago.nota_id == nota.id,
        )
        .first()
    )
    if not pago:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="Pago no encontrado en esta nota.",
        )

    comentario = f"Deshacer abono por admin (pago #{pago.id})"
    try:
        note_service.undo_payment(
            db,
            nota,
            pago,
            usuario_id=current_user.get("id"),
            comentario=comentario,
        )
    except ValueError as e:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error=str(e),
        )

    return RedirectResponse(url=f"/web/admin/notas/{nota_id}?pago_revertido=1", status_code=303)


@router.post("/notas/{nota_id}/ajuste-manual")
async def notas_ajuste_manual_post(
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
            error="Solo puedes registrar ajustes manuales en notas aprobadas.",
        )

    form = await request.form()
    nota_material_raw = (form.get("ajuste_nota_material_id") or "").strip()
    operacion = (form.get("ajuste_operacion") or "").strip().lower()
    cantidad_raw = (form.get("ajuste_cantidad_kg") or "").strip()
    comentario = (form.get("ajuste_comentario") or "").strip()
    form_state = {
        "form_ajuste_nota_material_id": nota_material_raw,
        "form_ajuste_operacion": operacion or "aumentar",
        "form_ajuste_cantidad": cantidad_raw,
        "form_ajuste_comentario": comentario,
    }

    try:
        nota_material_id = int(nota_material_raw)
    except (TypeError, ValueError):
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="Selecciona un material valido dentro de la nota.",
            form_state=form_state,
        )

    nota_material = (
        db.query(NotaMaterial)
        .filter(
            NotaMaterial.id == nota_material_id,
            NotaMaterial.nota_id == nota.id,
        )
        .first()
    )
    if not nota_material:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="El material seleccionado no pertenece a esta nota.",
            form_state=form_state,
        )

    if operacion not in {"aumentar", "disminuir"}:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="Selecciona un tipo de ajuste valido.",
            form_state=form_state,
        )

    try:
        cantidad = Decimal(str(cantidad_raw))
    except (InvalidOperation, TypeError):
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="La cantidad del ajuste es invalida.",
            form_state=form_state,
        )
    if cantidad <= Decimal("0"):
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="La cantidad del ajuste debe ser mayor a cero.",
            form_state=form_state,
        )

    delta = cantidad if operacion == "aumentar" else (cantidad * Decimal("-1"))
    material_name = (
        nota_material.material.nombre
        if nota_material.material
        else f"Material #{nota_material.material_id}"
    )
    comentario_final = comentario or (
        f"Ajuste manual ligado a nota #{nota.id} - {material_name}"
    )

    try:
        note_service.ajustar_stock(
            db,
            sucursal_id=note_service.get_inventory_sucursal_id(nota),
            material_id=nota_material.material_id,
            cantidad_kg=delta,
            comentario=comentario_final,
            usuario_id=current_user.get("id"),
            nota_id=nota.id,
            nota_material_id=nota_material.id,
        )
    except ValueError as exc:
        db.rollback()
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error=str(exc),
            form_state=form_state,
        )

    return RedirectResponse(
        url=f"/web/admin/notas/{nota_id}?ajuste_manual=1#note-manual-adjustments",
        status_code=303,
    )


@router.post("/notas/{nota_id}/ajuste-manual/{ajuste_id}/revertir")
async def notas_ajuste_manual_revertir(
    nota_id: int,
    ajuste_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_nota_access(nota, allowed_suc_ids)

    ajuste = (
        db.query(InventarioAjusteManual)
        .filter(
            InventarioAjusteManual.id == ajuste_id,
            InventarioAjusteManual.nota_id == nota.id,
        )
        .first()
    )
    if not ajuste:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="El ajuste manual ligado a esta nota no fue encontrado.",
        )

    try:
        note_service.reverse_manual_inventory_adjustment(
            db,
            ajuste,
            usuario_id=current_user.get("id"),
            comentario=f"Reversion ajuste manual nota #{nota.id} - ajuste #{ajuste.id}",
        )
    except ValueError as exc:
        db.rollback()
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error=str(exc),
        )

    return RedirectResponse(
        url=f"/web/admin/notas/{nota_id}?ajuste_manual_revertido=1#note-manual-adjustments",
        status_code=303,
    )


@router.post("/notas/{nota_id}/ajuste-saldo")
async def notas_ajuste_saldo_post(
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
            error="Solo puedes ajustar saldo en notas aprobadas.",
        )

    form = await request.form()
    ajuste_tipo = (form.get("ajuste_saldo_tipo") or "").strip().lower()
    monto_raw = (form.get("ajuste_saldo_monto") or "").strip()
    comentario = (form.get("ajuste_saldo_comentario") or "").strip()
    form_state = {
        "form_ajuste_saldo_tipo": ajuste_tipo or "reducir",
        "form_ajuste_saldo_monto": monto_raw,
        "form_ajuste_saldo_comentario": comentario,
    }

    if ajuste_tipo not in {"reducir", "aumentar"}:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="Selecciona un tipo de ajuste de saldo valido.",
            form_state=form_state,
        )
    try:
        monto_val = Decimal(str(monto_raw))
    except (InvalidOperation, TypeError):
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="El monto del ajuste de saldo es invalido.",
            form_state=form_state,
        )
    if monto_val <= Decimal("0"):
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="El monto del ajuste de saldo debe ser mayor a cero.",
            form_state=form_state,
        )
    if not comentario:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="Debes indicar un comentario para el ajuste de saldo.",
            form_state=form_state,
        )

    delta = -monto_val if ajuste_tipo == "reducir" else monto_val
    try:
        note_service.adjust_note_balance(
            db,
            nota,
            monto_delta=delta,
            usuario_id=current_user.get("id"),
            comentario=comentario,
        )
    except ValueError as exc:
        db.rollback()
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error=str(exc),
            form_state=form_state,
        )

    return RedirectResponse(
        url=f"/web/admin/notas/{nota_id}?ajuste_saldo=1#note-balance-adjustments",
        status_code=303,
    )


@router.post("/notas/{nota_id}/ajuste-saldo/{ajuste_id}/revertir")
async def notas_ajuste_saldo_revertir(
    nota_id: int,
    ajuste_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_nota_access(nota, allowed_suc_ids)

    ajuste = (
        db.query(NotaAjusteSaldo)
        .filter(
            NotaAjusteSaldo.id == ajuste_id,
            NotaAjusteSaldo.nota_id == nota.id,
        )
        .first()
    )
    if not ajuste:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="El ajuste de saldo ligado a esta nota no fue encontrado.",
        )

    try:
        note_service.reverse_note_balance_adjustment(
            db,
            ajuste,
            usuario_id=current_user.get("id"),
            comentario=f"Reversion ajuste saldo nota #{nota.id} - ajuste #{ajuste.id}",
        )
    except ValueError as exc:
        db.rollback()
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error=str(exc),
        )

    return RedirectResponse(
        url=f"/web/admin/notas/{nota_id}?ajuste_saldo_revertido=1#note-balance-adjustments",
        status_code=303,
    )


@router.post("/notas/{nota_id}/pago-inicial")
async def notas_ajustar_pago_inicial(
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
            error="Solo puedes ajustar el pago inicial en notas aprobadas.",
        )

    form = await request.form()
    monto_raw = (form.get("pago_inicial_monto") or "").strip()
    metodo_pago = (form.get("pago_inicial_metodo") or "").strip().lower()
    cuenta_financiera = (form.get("pago_inicial_cuenta") or "").strip()
    cuenta_scrap360_raw = (form.get("pago_inicial_cuenta_scrap360_id") or "").strip()
    caja_sucursal_raw = (form.get("pago_inicial_caja_sucursal_id") or "").strip()
    comentario = (form.get("pago_inicial_comentario") or "").strip()
    form_state = {
        "form_pago_inicial_monto": monto_raw,
        "form_pago_inicial_metodo": metodo_pago,
        "form_pago_inicial_cuenta": cuenta_financiera,
        "form_pago_inicial_comentario": comentario,
        "form_pago_inicial_cuenta_scrap360": cuenta_scrap360_raw,
        "form_pago_inicial_caja_sucursal_id": caja_sucursal_raw,
    }
    if monto_raw == "":
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="Debes indicar el monto objetivo del pago inicial.",
            form_state=form_state,
        )
    try:
        monto_objetivo = Decimal(str(monto_raw))
    except (InvalidOperation, TypeError):
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="El monto objetivo es invalido.",
            form_state=form_state,
        )

    cuenta_scrap360_id = None
    if cuenta_scrap360_raw:
        try:
            cuenta_scrap360_id = int(cuenta_scrap360_raw)
        except (TypeError, ValueError):
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error="La cuenta Scrap360 es invalida.",
                form_state=form_state,
            )

    caja_sucursal_id = None
    if caja_sucursal_raw:
        try:
            caja_sucursal_id = int(caja_sucursal_raw)
        except (TypeError, ValueError):
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error="La sucursal de caja es invalida.",
                form_state=form_state,
            )
    if metodo_pago == "efectivo" and caja_sucursal_id and allowed_suc_ids is not None and caja_sucursal_id not in allowed_suc_ids:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="No tienes acceso a la sucursal de caja seleccionada.",
            form_state=form_state,
        )

    try:
        note_service.adjust_initial_payment(
            db,
            nota,
            monto_objetivo=monto_objetivo,
            usuario_id=current_user.get("id"),
            metodo_pago=metodo_pago or None,
            cuenta_financiera=cuenta_financiera or None,
            cuenta_scrap360_id=cuenta_scrap360_id,
            caja_sucursal_id=caja_sucursal_id,
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

    return RedirectResponse(url=f"/web/admin/notas/{nota_id}?pago_inicial=1", status_code=303)


@router.post("/notas/{nota_id}/devolucion-parcial")
async def notas_devolucion_parcial(
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
            error="Solo puedes aplicar devolucion parcial en notas aprobadas.",
        )

    form = await request.form()
    comentario = (form.get("comentario_devolucion_parcial") or "").strip()
    form_devol_kg_map: dict[int, str] = {}
    form_devol_precio_map: dict[int, str] = {}
    devoluciones_payload: list[dict] = []

    for nm in nota.materiales:
        kg_raw = (form.get(f"devol_kg_{nm.id}") or "").strip()
        precio_raw = (form.get(f"devol_precio_{nm.id}") or "").strip()
        if kg_raw:
            form_devol_kg_map[nm.id] = kg_raw
        if precio_raw:
            form_devol_precio_map[nm.id] = precio_raw

        if not kg_raw and not precio_raw:
            continue

        try:
            kg_devolucion = Decimal(str(kg_raw or 0))
        except (InvalidOperation, TypeError):
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error="El kg de devolucion es invalido.",
                form_state={
                    "form_devol_kg_map": form_devol_kg_map,
                    "form_devol_precio_map": form_devol_precio_map,
                    "form_devol_comment": comentario,
                },
            )
        if kg_devolucion < Decimal("0"):
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error="El kg de devolucion no puede ser negativo.",
                form_state={
                    "form_devol_kg_map": form_devol_kg_map,
                    "form_devol_precio_map": form_devol_precio_map,
                    "form_devol_comment": comentario,
                },
            )
        if kg_devolucion == Decimal("0"):
            continue

        try:
            precio_devolucion = (
                Decimal(str(precio_raw))
                if precio_raw
                else Decimal(str(nm.precio_unitario or 0))
            )
        except (InvalidOperation, TypeError):
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error="El precio de devolucion es invalido.",
                form_state={
                    "form_devol_kg_map": form_devol_kg_map,
                    "form_devol_precio_map": form_devol_precio_map,
                    "form_devol_comment": comentario,
                },
            )
        if precio_devolucion < Decimal("0"):
            return _render_nota_detail(
                request,
                db,
                current_user,
                nota,
                error="El precio de devolucion no puede ser negativo.",
                form_state={
                    "form_devol_kg_map": form_devol_kg_map,
                    "form_devol_precio_map": form_devol_precio_map,
                    "form_devol_comment": comentario,
                },
            )

        devoluciones_payload.append(
            {
                "nota_material_id": nm.id,
                "kg_devolucion": kg_devolucion,
                "precio_unitario_devolucion": precio_devolucion,
            }
        )

    if not devoluciones_payload:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="Debes indicar al menos un material con kg de devolucion mayor a 0.",
            form_state={
                "form_devol_kg_map": form_devol_kg_map,
                "form_devol_precio_map": form_devol_precio_map,
                "form_devol_comment": comentario,
            },
        )

    try:
        note_service.partial_return_approved_note(
            db,
            nota,
            devoluciones_payload=devoluciones_payload,
            admin_id=current_user.get("id"),
            comentario=comentario or None,
        )
    except ValueError as e:
        db.rollback()
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error=str(e),
            form_state={
                "form_devol_kg_map": form_devol_kg_map,
                "form_devol_precio_map": form_devol_precio_map,
                "form_devol_comment": comentario,
            },
        )

    return RedirectResponse(url=f"/web/admin/notas/{nota_id}?devolucion_parcial=1", status_code=303)


@router.post("/notas/{nota_id}/devolucion-parcial/{linea_id}/revertir")
async def notas_revertir_devolucion_parcial(
    nota_id: int,
    linea_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_nota_access(nota, allowed_suc_ids)

    linea = (
        db.query(NotaDevolucionParcialLinea)
        .join(NotaDevolucionParcial, NotaDevolucionParcialLinea.devolucion_id == NotaDevolucionParcial.id)
        .filter(
            NotaDevolucionParcialLinea.id == linea_id,
            NotaDevolucionParcial.nota_id == nota.id,
        )
        .first()
    )
    if not linea:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="La linea de devolucion parcial no fue encontrada.",
        )

    try:
        note_service.reverse_partial_return_line(
            db,
            nota,
            linea,
            admin_id=current_user.get("id"),
            comentario=f"Reversion devolucion parcial linea #{linea.id}",
        )
    except ValueError as e:
        db.rollback()
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error=str(e),
        )

    return RedirectResponse(url=f"/web/admin/notas/{nota_id}?devolucion_parcial_revertida=1", status_code=303)


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
    return RedirectResponse(url=f"/web/admin/notas/{nota_id}?cancelled=1", status_code=303)


@router.post("/notas/{nota_id}/devolucion-total/{devolucion_id}/revertir")
async def notas_revertir_devolucion_total(
    nota_id: int,
    devolucion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    _ensure_nota_access(nota, allowed_suc_ids)

    devolucion_total = (
        db.query(NotaDevolucionTotal)
        .filter(
            NotaDevolucionTotal.id == devolucion_id,
            NotaDevolucionTotal.nota_id == nota.id,
        )
        .first()
    )
    if not devolucion_total:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="La devolucion total no fue encontrada.",
        )

    try:
        note_service.reverse_total_return(
            db,
            nota,
            devolucion_total,
            admin_id=current_user.get("id"),
            comentario=f"Reversion devolucion total #{devolucion_total.id}",
        )
    except ValueError as e:
        db.rollback()
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error=str(e),
        )

    return RedirectResponse(url=f"/web/admin/notas/{nota_id}?devolucion_total_revertida=1", status_code=303)


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
    if nota.estado != NotaEstado.en_revision:
        return _render_nota_detail(
            request, db, current_user, nota, error="Solo puedes devolver notas en revision."
        )
    form = await request.form()
    comentarios_admin = (form.get("comentarios_admin") or "").strip()
    if not comentarios_admin:
        return _render_nota_detail(
            request,
            db,
            current_user,
            nota,
            error="Debes agregar un comentario para devolver la nota al trabajador.",
        )
    note_service.update_state(
        db,
        nota,
        new_state=NotaEstado.borrador,
        admin_id=current_user.get("id"),
        comentarios_admin=comentarios_admin,
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
    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.orden_display, Material.nombre).all()
    sucursales = _active_sucursales(db)
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


def _render_inventario_aumentar(
    request: Request,
    db: Session,
    current_user: dict,
    *,
    error: str | None = None,
    form_data: dict | None = None,
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.orden_display, Material.nombre).all()
    sucursales = _active_sucursales(db)
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    suc_ids = [s.id for s in sucursales]
    inv_rows = db.query(Inventario).filter(Inventario.sucursal_id.in_(suc_ids)).all() if suc_ids else []
    inv_map: dict[int, dict[int, float]] = {}
    for inv in inv_rows:
        inv_map.setdefault(inv.sucursal_id, {})[inv.material_id] = float(inv.stock_actual or 0)
    form_data = form_data or {}
    return templates.TemplateResponse(
        "admin/inventario_aumentar.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "materiales": materiales,
            "sucursales": sucursales,
            "inv_map": inv_map,
            "error": error,
            "form_sucursal_id": form_data.get("sucursal_id", ""),
            "form_material_id": form_data.get("material_id", ""),
            "form_operacion": form_data.get("operacion", ""),
            "form_cantidad": form_data.get("cantidad_kg", ""),
            "form_comentario": form_data.get("comentario", ""),
        },
        status_code=400 if error else 200,
    )


@router.get("/inventario/aumentar")
async def inventario_aumentar_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    return _render_inventario_aumentar(request, db, current_user)


@router.post("/inventario/aumentar")
async def inventario_aumentar_post(
    request: Request,
    sucursal_id: str = Form(...),
    material_id: str = Form(...),
    operacion: str = Form(""),
    cantidad_kg: str = Form(""),
    comentario: str = Form(""),
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    form_data = {
        "sucursal_id": sucursal_id,
        "material_id": material_id,
        "operacion": operacion,
        "cantidad_kg": cantidad_kg,
        "comentario": comentario,
    }

    if allowed_suc_ids:
        if not sucursal_id:
            if len(allowed_suc_ids) == 1:
                sucursal_id = str(allowed_suc_ids[0])
                form_data["sucursal_id"] = sucursal_id
            else:
                return _render_inventario_aumentar(request, db, current_user, error="Selecciona una sucursal valida.", form_data=form_data)

    try:
        suc_id = int(sucursal_id)
        mat_id = int(material_id)
    except ValueError:
        return _render_inventario_aumentar(request, db, current_user, error="Sucursal o material invalido.", form_data=form_data)

    if allowed_suc_ids and suc_id not in allowed_suc_ids:
        return _render_inventario_aumentar(request, db, current_user, error="Sucursal no autorizada.", form_data=form_data)

    operacion = (operacion or "").strip().lower()
    if operacion not in ("aumentar", "disminuir"):
        return _render_inventario_aumentar(request, db, current_user, error="Selecciona una operacion valida.", form_data=form_data)

    cantidad_raw = (cantidad_kg or "").strip()
    if not cantidad_raw:
        return _render_inventario_aumentar(request, db, current_user, error="Debes indicar la cantidad.", form_data=form_data)
    try:
        cantidad_val = Decimal(str(cantidad_raw))
    except (InvalidOperation, TypeError):
        return _render_inventario_aumentar(request, db, current_user, error="Cantidad invalida.", form_data=form_data)
    if cantidad_val <= 0:
        return _render_inventario_aumentar(request, db, current_user, error="La cantidad debe ser mayor a cero.", form_data=form_data)

    suc = db.get(Sucursal, suc_id)
    if not suc:
        return _render_inventario_aumentar(request, db, current_user, error="Sucursal no encontrada.", form_data=form_data)
    mat = db.get(Material, mat_id)
    if not mat:
        return _render_inventario_aumentar(request, db, current_user, error="Material no encontrado.", form_data=form_data)

    delta = cantidad_val if operacion == "aumentar" else -cantidad_val
    comentario = (comentario or "").strip()
    if not comentario:
        comentario = "Aumento manual" if operacion == "aumentar" else "Disminucion manual"

    try:
        note_service.ajustar_stock(
            db,
            sucursal_id=suc.id,
            material_id=mat.id,
            cantidad_kg=delta,
            comentario=comentario,
            usuario_id=current_user.get("id"),
        )
    except ValueError as exc:
        return _render_inventario_aumentar(
            request,
            db,
            current_user,
            error=str(exc),
            form_data=form_data,
        )
    return RedirectResponse(url="/web/admin/inventario", status_code=303)


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
    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.orden_display, Material.nombre).all()
    sucursales = _active_sucursales(db)
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
        delta = nuevo_stock - stock_actual
    else:
        try:
            delta = Decimal(str(cantidad_kg))
        except (InvalidOperation, TypeError):
            return render_error("Cantidad inválida.")

    comentario = (comentario or "").strip() or "Ajuste manual"

    try:
        note_service.ajustar_stock(
            db,
            sucursal_id=suc.id,
            material_id=mat.id,
            cantidad_kg=delta,
            comentario=comentario,
            usuario_id=current_user.get("id"),
        )
    except ValueError as exc:
        return render_error(str(exc))
    return RedirectResponse(url="/web/admin/inventario?ajuste=1", status_code=303)


def _render_conversiones_materiales(
    request: Request,
    db: Session,
    current_user: dict,
    *,
    error: str | None = None,
    ok: bool = False,
    form_data: dict | None = None,
):
    sucursales = _active_sucursales(db)
    materiales = db.query(Material).filter(Material.activo.is_(True)).order_by(Material.orden_display, Material.nombre).all()
    conversions = (
        db.query(ConversionMaterial)
        .order_by(ConversionMaterial.created_at.desc())
        .limit(200)
        .all()
    )
    conversion_ids = [c.id for c in conversions]
    reversion_links = (
        db.query(ConversionMaterialReversion)
        .filter(
            or_(
                ConversionMaterialReversion.conversion_id.in_(conversion_ids or [-1]),
                ConversionMaterialReversion.reversal_conversion_id.in_(conversion_ids or [-1]),
            )
        )
        .all()
        if conversion_ids
        else []
    )
    reversion_by_conversion_id = {link.conversion_id: link for link in reversion_links}
    reversion_by_reversal_id = {link.reversal_conversion_id: link for link in reversion_links}
    suc_ids = [s.id for s in sucursales]
    inv_rows = db.query(Inventario).filter(Inventario.sucursal_id.in_(suc_ids)).all() if suc_ids else []
    inv_map: dict[int, dict[int, float]] = {}
    for inv in inv_rows:
        inv_map.setdefault(inv.sucursal_id, {})[inv.material_id] = float(inv.stock_actual or 0)

    return templates.TemplateResponse(
        "admin/conversiones_materiales.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "sucursales": sucursales,
            "materiales": materiales,
            "conversions": conversions,
            "reversion_by_conversion_id": reversion_by_conversion_id,
            "reversion_by_reversal_id": reversion_by_reversal_id,
            "inv_map": inv_map,
            "error": error,
            "ok": ok,
            "form_data": form_data or {},
        },
        status_code=400 if error else 200,
    )


@router.get("/conversiones-materiales")
async def conversiones_materiales_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    ok = request.query_params.get("ok") == "1"
    return _render_conversiones_materiales(request, db, current_user, ok=ok)


@router.post("/conversiones-materiales")
async def conversiones_materiales_post(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    form = await request.form()
    sucursal_raw = (form.get("sucursal_id") or "").strip()
    origen_raw = (form.get("material_origen_id") or "").strip()
    destino_raw = (form.get("material_destino_id") or "").strip()
    cantidad_origen_raw = (form.get("cantidad_origen") or "").strip()
    cantidad_destino_raw = (form.get("cantidad_destino") or "").strip()
    comentario = (form.get("comentario") or "").strip()

    form_data = {
        "sucursal_id": sucursal_raw,
        "material_origen_id": origen_raw,
        "material_destino_id": destino_raw,
        "cantidad_origen": cantidad_origen_raw,
        "cantidad_destino": cantidad_destino_raw,
        "comentario": comentario,
    }

    try:
        sucursal_id = int(sucursal_raw)
        material_origen_id = int(origen_raw)
        material_destino_id = int(destino_raw)
    except ValueError:
        return _render_conversiones_materiales(
            request,
            db,
            current_user,
            error="Sucursal o material invalido.",
            form_data=form_data,
        )

    try:
        cantidad_origen = Decimal(str(cantidad_origen_raw))
        cantidad_destino = Decimal(str(cantidad_destino_raw))
    except (InvalidOperation, TypeError):
        return _render_conversiones_materiales(
            request,
            db,
            current_user,
            error="Las cantidades son invalidas.",
            form_data=form_data,
        )

    try:
        conversion_service.create_conversion(
            db,
            sucursal_id=sucursal_id,
            material_origen_id=material_origen_id,
            cantidad_origen=cantidad_origen,
            material_destino_id=material_destino_id,
            cantidad_destino=cantidad_destino,
            usuario_id=current_user.get("id"),
            comentario=comentario or None,
        )
    except ValueError as exc:
        db.rollback()
        return _render_conversiones_materiales(
            request,
            db,
            current_user,
            error=str(exc),
            form_data=form_data,
        )

    return RedirectResponse(url="/web/admin/conversiones-materiales?ok=1", status_code=303)


@router.get("/conversiones-materiales/{conversion_id}")
async def conversion_material_detail(
    conversion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    conversion = db.get(ConversionMaterial, conversion_id)
    if not conversion:
        raise HTTPException(status_code=404, detail="Conversión no encontrada.")

    reversion_link = (
        db.query(ConversionMaterialReversion)
        .filter(
            or_(
                ConversionMaterialReversion.conversion_id == conversion.id,
                ConversionMaterialReversion.reversal_conversion_id == conversion.id,
            )
        )
        .first()
    )
    original_conversion = conversion
    reversal_conversion = None
    is_reversal = False
    if reversion_link:
        if reversion_link.reversal_conversion_id == conversion.id:
            is_reversal = True
            original_conversion = reversion_link.conversion
            reversal_conversion = conversion
        else:
            reversal_conversion = reversion_link.reversal_conversion

    inv_origen = (
        db.query(Inventario)
        .filter(
            Inventario.sucursal_id == conversion.sucursal_id,
            Inventario.material_id == conversion.material_origen_id,
        )
        .first()
    )
    inv_destino = (
        db.query(Inventario)
        .filter(
            Inventario.sucursal_id == conversion.sucursal_id,
            Inventario.material_id == conversion.material_destino_id,
        )
        .first()
    )

    return templates.TemplateResponse(
        "admin/conversion_detail.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "conversion": conversion,
            "reversion_link": reversion_link,
            "original_conversion": original_conversion,
            "reversal_conversion": reversal_conversion,
            "is_reversal": is_reversal,
            "inv_origen": inv_origen,
            "inv_destino": inv_destino,
            "error": request.query_params.get("error") or None,
            "reverted_ok": request.query_params.get("revertida") == "1",
        },
    )


@router.post("/conversiones-materiales/{conversion_id}/revertir")
async def conversion_material_reverse(
    conversion_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    conversion = db.get(ConversionMaterial, conversion_id)
    if not conversion:
        raise HTTPException(status_code=404, detail="Conversión no encontrada.")

    try:
        reversal = conversion_service.reverse_conversion(
            db,
            conversion,
            usuario_id=current_user.get("id"),
            comentario=f"Reversion conversion #{conversion.id}",
        )
    except ValueError as exc:
        return RedirectResponse(
            url=f"/web/admin/conversiones-materiales/{conversion_id}?{urlencode({'error': str(exc)})}",
            status_code=303,
        )

    return RedirectResponse(
        url=f"/web/admin/conversiones-materiales/{reversal.id}?revertida=1",
        status_code=303,
    )


@router.get("/inventario")
async def inventario_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
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
    negative_inventarios = [inv for inv in inventarios if Decimal(str(inv.stock_actual or 0)) < Decimal("0")]
    negative_inventory_count = len(negative_inventarios)
    negative_inventory_kg = sum((Decimal(str(inv.stock_actual or 0)) for inv in negative_inventarios), Decimal("0"))
    return templates.TemplateResponse(
        "admin/inventario_list.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "inventarios": inventarios,
            "sucursales": sucursales,
            "sucursal_id": sucursal_id,
            "negative_inventory_count": negative_inventory_count,
            "negative_inventory_kg": negative_inventory_kg,
            "can_manage_inventory": not _is_read_only_admin_user(current_user),
        },
    )


@router.get("/inventario/valor")
async def inventario_valor_get(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
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

    selected_sucursal = db.get(Sucursal, sucursal_id) if sucursal_id else None
    rows: list[dict] = []
    total_kg = Decimal("0")
    total_valor = Decimal("0")
    materiales_con_stock = 0
    manual_count = 0
    automatic_count = 0
    sin_precio_count = 0
    sucursal_summary: list[dict] = []
    if selected_sucursal:
        rows, total_kg, total_valor, materiales_con_stock, manual_count, automatic_count, sin_precio_count = _build_inventario_valor_rows(
            db,
            sucursal_id=selected_sucursal.id,
        )
    else:
        sucursal_summary = _build_inventario_valor_summary(db, sucursales=sucursales)

    return templates.TemplateResponse(
        "admin/inventario_valor.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "sucursales": sucursales,
            "sucursal_id": sucursal_id,
            "selected_sucursal": selected_sucursal,
            "rows": rows,
            "total_kg": total_kg,
            "total_valor": total_valor,
            "materiales_con_stock": materiales_con_stock,
            "manual_count": manual_count,
            "automatic_count": automatic_count,
            "sin_precio_count": sin_precio_count,
            "sucursal_summary": sucursal_summary,
            "saved": request.query_params.get("saved") == "1",
            "error": request.query_params.get("error"),
            "can_manage_inventory_value": not _is_read_only_admin_user(current_user),
        },
    )


@router.post("/inventario/valor")
async def inventario_valor_post(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    if _is_read_only_admin_user(current_user):
        raise HTTPException(status_code=403, detail="No tienes permisos para editar el valor del inventario.")

    form = await request.form()
    sucursal_raw = (form.get("sucursal_id") or "").strip()
    try:
        sucursal_id = int(sucursal_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Sucursal invalida.")

    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    if allowed_suc_ids is not None and sucursal_id not in allowed_suc_ids:
        raise HTTPException(status_code=403, detail="Sucursal no autorizada.")

    sucursal = db.get(Sucursal, sucursal_id)
    if not sucursal:
        raise HTTPException(status_code=404, detail="Sucursal no encontrada.")

    materiales = db.query(Material).all()
    existing_map = {
        row.material_id: row
        for row in db.query(InventarioValorPrecio)
        .filter(InventarioValorPrecio.sucursal_id == sucursal_id)
        .all()
    }

    for material in materiales:
        raw = (form.get(f"precio_{material.id}") or "").strip()
        existing = existing_map.get(material.id)
        if not raw:
            if existing:
                db.delete(existing)
            continue
        try:
            precio = Decimal(str(raw))
        except (InvalidOperation, TypeError):
            return RedirectResponse(
                url=f"/web/admin/inventario/valor?sucursal_id={sucursal_id}&error=Precio%20invalido%20en%20la%20tabla",
                status_code=303,
            )
        if precio < Decimal("0"):
            return RedirectResponse(
                url=f"/web/admin/inventario/valor?sucursal_id={sucursal_id}&error=El%20precio%20no%20puede%20ser%20negativo",
                status_code=303,
            )
        if existing:
            existing.precio_referencia = precio
            existing.usuario_id = current_user.get("id")
            existing.updated_at = datetime.utcnow()
            db.add(existing)
        else:
            db.add(
                InventarioValorPrecio(
                    sucursal_id=sucursal_id,
                    material_id=material.id,
                    precio_referencia=precio,
                    usuario_id=current_user.get("id"),
                )
            )
    db.commit()
    return RedirectResponse(url=f"/web/admin/inventario/valor?sucursal_id={sucursal_id}&saved=1", status_code=303)


@router.get("/capital")
async def capital_real_view(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    valuation_mode = _normalize_inventory_valuation_mode(request.query_params.get("inventario_base"))
    capital_context = _build_capital_real_context(
        db,
        allowed_suc_ids=allowed_suc_ids,
        valuation_mode=valuation_mode,
    )
    return templates.TemplateResponse(
        "admin/capital_real.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            **capital_context,
        },
    )


@router.get("/contabilidad")
async def contabilidad_list(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
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
    proveedor_balance_groups, cliente_balance_groups = _build_partner_balance_group_maps(
        proveedores,
        clientes,
    )
    partner_group_metadata = _build_partner_balance_group_metadata(
        proveedores,
        clientes,
        proveedor_groups=proveedor_balance_groups,
        cliente_groups=cliente_balance_groups,
    )

    notas_query = db.query(Nota).filter(Nota.estado == NotaEstado.aprobada)
    notas_query = _apply_sucursal_filter(notas_query, allowed_suc_ids, sucursal_id, Nota.sucursal_id)
    notas_aprobadas = notas_query.all()
    note_adjustment_totals = _get_note_balance_adjustment_totals_map(
        db,
        [nota.id for nota in notas_aprobadas if nota.id],
    )
    partner_group_balances: dict[tuple[str, int], Decimal] = defaultdict(lambda: Decimal("0"))
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
        diff = total - pagado + Decimal(str(note_adjustment_totals.get(nota.id, Decimal("0")) or 0))
        if nota.tipo_operacion == TipoOperacion.venta:
            partner_kind, partner_id = _nota_partner_key(nota)
            nombre = clientes_map.get(partner_id) if partner_kind == "cliente" else proveedores_map.get(partner_id)
            if _is_internal_partner(nombre):
                continue
            group_key = _resolve_partner_balance_group_key(
                proveedor_id=nota.proveedor_id,
                cliente_id=nota.cliente_id,
                proveedor_groups=proveedor_balance_groups,
                cliente_groups=cliente_balance_groups,
            )
            if not group_key:
                continue
            notas_consideradas += 1
            total_ventas_aprobadas += total
            total_cobrado_clientes += pagado
            partner_group_balances[group_key] -= diff
        elif nota.tipo_operacion == TipoOperacion.compra:
            partner_kind, partner_id = _nota_partner_key(nota)
            if partner_kind == "cliente":
                nombre = clientes_map.get(partner_id)
            else:
                nombre = proveedores_map.get(partner_id)
            if _is_internal_partner(nombre):
                continue
            group_key = _resolve_partner_balance_group_key(
                proveedor_id=nota.proveedor_id,
                cliente_id=nota.cliente_id,
                proveedor_groups=proveedor_balance_groups,
                cliente_groups=cliente_balance_groups,
            )
            if not group_key:
                continue
            notas_consideradas += 1
            total_compras_aprobadas += total
            total_pagado_proveedores += pagado
            partner_group_balances[group_key] += diff

    def _apply_ajuste_sucursal_filter(query):
        if allowed_suc_ids is not None:
            if sucursal_id:
                return query.filter(AjusteSaldoPartner.sucursal_id == sucursal_id)
            return query.filter(AjusteSaldoPartner.sucursal_id.in_(allowed_suc_ids))
        if sucursal_id:
            return query.filter(AjusteSaldoPartner.sucursal_id == sucursal_id)
        return query

    ajustes_clientes = _apply_ajuste_sucursal_filter(
        db.query(AjusteSaldoPartner).filter(AjusteSaldoPartner.partner_type == "cliente")
    ).all()
    for ajuste in ajustes_clientes:
        nombre = clientes_map.get(ajuste.partner_id)
        if _is_internal_partner(nombre):
            continue
        delta = Decimal(str(ajuste.monto or 0))
        group_key = _resolve_partner_balance_group_key(
            cliente_id=ajuste.partner_id,
            proveedor_groups=proveedor_balance_groups,
            cliente_groups=cliente_balance_groups,
        )
        if not group_key:
            continue
        partner_group_balances[group_key] -= delta

    ajustes_proveedores = _apply_ajuste_sucursal_filter(
        db.query(AjusteSaldoPartner).filter(AjusteSaldoPartner.partner_type == "proveedor")
    ).all()
    for ajuste in ajustes_proveedores:
        nombre = proveedores_map.get(ajuste.partner_id)
        if _is_internal_partner(nombre):
            continue
        delta = Decimal(str(ajuste.monto or 0))
        group_key = _resolve_partner_balance_group_key(
            proveedor_id=ajuste.partner_id,
            proveedor_groups=proveedor_balance_groups,
            cliente_groups=cliente_balance_groups,
        )
        if not group_key:
            continue
        partner_group_balances[group_key] += delta

    classified_totals = _classify_partner_group_balances(
        partner_group_balances,
        group_metadata=partner_group_metadata,
    )
    total_por_cobrar = classified_totals["total_por_cobrar_clientes"]
    saldo_favor_clientes = classified_totals["saldo_favor_clientes"]
    total_por_pagar = classified_totals["total_por_pagar_proveedores"]
    saldo_favor_empresa = classified_totals["saldo_favor_empresa"]

    comisiones_pendientes = Decimal("0")
    comisiones_query = db.query(ComisionarioNota).filter(ComisionarioNota.estado == ComisionarioNotaEstado.aprobada)
    if allowed_suc_ids is not None:
        if sucursal_id:
            comisiones_query = comisiones_query.filter(ComisionarioNota.sucursal_id == sucursal_id)
        else:
            comisiones_query = comisiones_query.filter(ComisionarioNota.sucursal_id.in_(allowed_suc_ids))
    elif sucursal_id:
        comisiones_query = comisiones_query.filter(ComisionarioNota.sucursal_id == sucursal_id)
    for nota in comisiones_query.all():
        total = Decimal(str(nota.total_monto or 0))
        pagado = Decimal(str(nota.monto_pagado or 0))
        pendiente = total - pagado
        if pendiente > Decimal("0"):
            comisiones_pendientes += pendiente

    neto_por_cobrar = total_por_cobrar - saldo_favor_clientes
    neto_por_pagar = (total_por_pagar - saldo_favor_empresa) + comisiones_pendientes
    saldo_neto = neto_por_cobrar - neto_por_pagar
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
                    note_adjustment_totals = _get_note_balance_adjustment_totals_map(
                        db,
                        [nota.id for nota in notas_p if nota.id],
                    )
                    record_rows = _build_partner_record_rows(
                        notas_p,
                        folio_map,
                        partner_type="cliente",
                        note_adjustment_totals=note_adjustment_totals,
                    )
                    ajustes_delta = _get_partner_adjustments_total(
                        db,
                        partner_type="cliente",
                        partner_id=partner_id,
                        allowed_suc_ids=allowed_suc_ids,
                        sucursal_id=sucursal_id,
                    )
                    summary = _aggregate_partner_record_summary(
                        notas_p,
                        partner_type="cliente",
                        ajustes_delta=ajustes_delta,
                        note_adjustment_totals=note_adjustment_totals,
                    )
                    unified_enabled = False
                    linked_partner = None
                    if not _is_internal_partner_name(db, partner.nombre_completo):
                        linked_partner = _get_formally_linked_proveedor(db, partner)
                        if linked_partner and _is_internal_partner_name(db, linked_partner.nombre_completo):
                            linked_partner = None
                    if linked_partner:
                        compras_q = (
                            db.query(Nota)
                            .filter(
                                Nota.proveedor_id == linked_partner.id,
                                Nota.tipo_operacion == TipoOperacion.compra,
                            )
                        )
                        compras_q = _apply_sucursal_filter(compras_q, allowed_suc_ids, sucursal_id, Nota.sucursal_id)
                        compras = compras_q.order_by(Nota.created_at.desc()).all()
                        unified_note_adjustments = _get_note_balance_adjustment_totals_map(
                            db,
                            [nota.id for nota in (compras + notas_p) if nota.id],
                        )
                        ajustes_proveedor = _get_partner_adjustments_total(
                            db,
                            partner_type="proveedor",
                            partner_id=linked_partner.id,
                            allowed_suc_ids=allowed_suc_ids,
                            sucursal_id=sucursal_id,
                        )
                        summary = _aggregate_unified_partner_summary(
                            compras=compras,
                            ventas=notas_p,
                            ajustes_proveedor=ajustes_proveedor,
                            ajustes_cliente=ajustes_delta,
                            note_adjustment_totals=unified_note_adjustments,
                        )
                        unified_enabled = True
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
                        "tipo_operacion_label": "Ventas",
                        "record_rows": record_rows,
                        "record_total_count": len(notas_p),
                        "summary": summary,
                        "pagos": pagos_p,
                        "folio_map": folio_map,
                        "record_link": f"/web/admin/clientes/{partner_id}/record",
                        "total_facturado_label": "Total ventas aprobadas (neto)",
                        "total_pagado_label": "Total cobrado/pagado (neto)",
                        "saldo_pendiente_label": "Saldo neto (por cobrar al cliente)",
                        "saldo_favor_label": "Saldo a favor del cliente",
                        "unified_enabled": unified_enabled,
                        "linked_partner": linked_partner,
                        "linked_partner_label": "Proveedor",
                    }
            elif partner_type == "proveedor":
                partner = db.get(Proveedor, partner_id)
                if not partner:
                    partner_error = "Proveedor no encontrado."
                else:
                    compras_q = (
                        db.query(Nota)
                        .filter(
                            Nota.proveedor_id == partner_id,
                            Nota.tipo_operacion == TipoOperacion.compra,
                        )
                    )
                    compras_q = _apply_sucursal_filter(compras_q, allowed_suc_ids, sucursal_id, Nota.sucursal_id)
                    compras = compras_q.order_by(Nota.created_at.desc()).all()

                    provider_bundle = _collect_proveedor_sales_bundle(
                        db,
                        proveedor=partner,
                        allowed_suc_ids=allowed_suc_ids,
                        sucursal_id=sucursal_id,
                    )
                    linked_cliente = provider_bundle["linked_cliente"]
                    ventas = provider_bundle["ventas"]
                    provider_direct_sales_enabled = bool(provider_bundle["direct_enabled"])
                    provider_direct_sales_count = len(provider_bundle["ventas_directas"])

                    ajustes_proveedor = _get_partner_adjustments_total(
                        db,
                        partner_type="proveedor",
                        partner_id=partner_id,
                        allowed_suc_ids=allowed_suc_ids,
                        sucursal_id=sucursal_id,
                    )
                    ajustes_cliente = Decimal("0")
                    if linked_cliente:
                        ajustes_cliente = _get_partner_adjustments_total(
                            db,
                            partner_type="cliente",
                            partner_id=linked_cliente.id,
                            allowed_suc_ids=allowed_suc_ids,
                            sucursal_id=sucursal_id,
                        )

                    unified_enabled = bool(ventas or provider_direct_sales_enabled or linked_cliente)
                    if unified_enabled:
                        summary = _aggregate_unified_partner_summary(
                            compras=compras,
                            ventas=ventas,
                            ajustes_proveedor=ajustes_proveedor,
                            ajustes_cliente=ajustes_cliente,
                            note_adjustment_totals=_get_note_balance_adjustment_totals_map(
                                db,
                                [nota.id for nota in (compras + ventas) if nota.id],
                            ),
                        )
                        record_notes = sorted(
                            compras + ventas,
                            key=lambda nota: nota.created_at or datetime.min,
                            reverse=True,
                        )
                        folio_map = _build_folio_map(record_notes)
                        record_rows = _build_partner_record_rows(
                            record_notes,
                            folio_map,
                            partner_type=None,
                            note_adjustment_totals=_get_note_balance_adjustment_totals_map(
                                db,
                                [nota.id for nota in record_notes if nota.id],
                            ),
                        )
                        note_ids = [nota.id for nota in record_notes]
                        if note_ids:
                            pagos_p = (
                                db.query(NotaPago)
                                .join(Nota, NotaPago.nota_id == Nota.id)
                                .filter(Nota.id.in_(note_ids))
                                .order_by(NotaPago.created_at.desc())
                                .all()
                            )
                        else:
                            pagos_p = []
                    else:
                        folio_map = _build_folio_map(compras)
                        note_adjustment_totals = _get_note_balance_adjustment_totals_map(
                            db,
                            [nota.id for nota in compras if nota.id],
                        )
                        record_rows = _build_partner_record_rows(
                            compras,
                            folio_map,
                            partner_type="proveedor",
                            note_adjustment_totals=note_adjustment_totals,
                        )
                        summary = _aggregate_partner_record_summary(
                            compras,
                            partner_type="proveedor",
                            ajustes_delta=ajustes_proveedor,
                            note_adjustment_totals=note_adjustment_totals,
                        )
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
                        "tipo_operacion_label": "Compras y ventas" if unified_enabled else "Compras",
                        "record_rows": record_rows,
                        "record_total_count": len(record_rows),
                        "summary": summary,
                        "pagos": pagos_p,
                        "folio_map": folio_map,
                        "record_link": f"/web/admin/proveedores/{partner_id}/record",
                        "total_facturado_label": "Total compras aprobadas",
                        "total_pagado_label": "Total pagado",
                        "saldo_pendiente_label": "Saldo pendiente (por pagar al proveedor)",
                        "saldo_favor_label": "Saldo a favor de la empresa",
                        "unified_enabled": unified_enabled,
                        "linked_partner": linked_cliente,
                        "linked_partner_label": "Cliente",
                        "provider_direct_sales_enabled": provider_direct_sales_enabled,
                        "provider_direct_sales_count": provider_direct_sales_count,
                    }
            else:
                partner_error = "Seleccion invalida."
                partner_key = ""

    date_from = params.get("from")
    date_to = params.get("to")
    export_query = request.url.query
    fmt = params.get("format") or "csv"
    query = db.query(MovimientoContable)
    query = _apply_movimiento_sucursal_filter(
        query,
        allowed_suc_ids=allowed_suc_ids,
        sucursal_id=sucursal_id,
    )
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
    users_map = {u.id: u.nombre_completo for u in db.query(User).all()}
    movimientos_view = [_movimiento_display(m, sucursales_map, users_map) for m in movimientos]
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
            "neto_por_cobrar": neto_por_cobrar,
            "neto_por_pagar": neto_por_pagar,
            "comisiones_pendientes": comisiones_pendientes,
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
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
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
    query = _apply_movimiento_sucursal_filter(
        query,
        allowed_suc_ids=allowed_suc_ids,
        sucursal_id=sucursal_id,
    )
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

    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales_map = {s.id: s for s in sucursales}
    users_map = {u.id: u.nombre_completo for u in db.query(User).all()}
    movimientos_view = [_movimiento_display(m, sucursales_map, users_map) for m in movimientos]

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
                format_datetime_local(m["created_at"]) if m["created_at"] else "",
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
                format_datetime_local(m["created_at"]) if m["created_at"] else "",
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
            format_datetime_local(m["created_at"]) if m["created_at"] else "",
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
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
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


# ---------- CORTE DE CAJA ----------


def _parse_corte_fecha(raw: str | None) -> tuple[date, str | None]:
    if not raw:
        return date.today(), None
    try:
        return datetime.strptime(raw, "%Y-%m-%d").date(), None
    except ValueError:
        return date.today(), "Fecha invalida."


def _corte_local_day_bounds(fecha: date) -> tuple[datetime, datetime]:
    tz = get_app_timezone()
    start_local = datetime.combine(fecha, time.min).replace(tzinfo=tz)
    end_local = datetime.combine(fecha + timedelta(days=1), time.min).replace(tzinfo=tz)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)
    return start_utc, end_utc


def _corte_mov_category_label(categoria: str | None) -> str:
    catalog = {code: label for code, label, _ in _CORTE_MOV_CATEGORIAS}
    return catalog.get((categoria or "").upper(), categoria or "-")


def _corte_mov_default_type(categoria: str | None) -> str:
    catalog = {code: tipo for code, _, tipo in _CORTE_MOV_CATEGORIAS}
    return catalog.get((categoria or "").upper(), "INGRESO")


def _corte_gasto_category_label(categoria: str | None) -> str:
    catalog = {code: label for code, label in _CORTE_GASTO_CATEGORIAS}
    return catalog.get((categoria or "").upper(), categoria or "-")


def _cash_sign_for_nota(nota: Nota | None) -> Decimal:
    if not nota:
        return Decimal("0")
    if nota.tipo_operacion == TipoOperacion.compra:
        return Decimal("-1")
    return Decimal("1")


def _partner_name_for_nota(
    nota: Nota | None,
    proveedores_map: dict[int, str],
    clientes_map: dict[int, str],
) -> str:
    if not nota:
        return "-"
    partner_kind, partner_id = _nota_partner_key(nota)
    if partner_kind == "proveedor":
        return proveedores_map.get(partner_id, "-")
    if partner_kind == "cliente":
        return clientes_map.get(partner_id, "-")
    return "-"


def _resolve_corte_window(
    *,
    corte: CorteCaja | None,
    fecha: date,
) -> tuple[datetime, datetime]:
    if corte:
        start_dt = corte.opened_at or _corte_local_day_bounds(fecha)[0]
        if corte.estado == CorteCajaEstado.cerrado and corte.closed_at:
            end_dt = corte.closed_at
        else:
            end_dt = datetime.utcnow()
        if end_dt <= start_dt:
            end_dt = start_dt + timedelta(seconds=1)
        return start_dt, end_dt
    return _corte_local_day_bounds(fecha)


def _build_corte_cash_movimientos(
    db: Session,
    *,
    sucursal_id: int,
    start_dt: datetime,
    end_dt: datetime,
) -> dict:
    movs = (
        db.query(MovimientoContable)
        .filter(
            MovimientoContable.tipo.in_(["pago", "reverso_pago", "restauracion_pago"]),
            MovimientoContable.metodo_pago == "efectivo",
            or_(
                MovimientoContable.caja_sucursal_id == sucursal_id,
                and_(
                    MovimientoContable.caja_sucursal_id.is_(None),
                    MovimientoContable.sucursal_id == sucursal_id,
                ),
            ),
            MovimientoContable.created_at >= start_dt,
            MovimientoContable.created_at < end_dt,
        )
        .order_by(MovimientoContable.created_at.asc(), MovimientoContable.id.asc())
        .all()
    )

    note_ids = {m.nota_id for m in movs if m.nota_id}
    notas = db.query(Nota).filter(Nota.id.in_(note_ids)).all() if note_ids else []
    nota_map = {n.id: n for n in notas}
    folio_map = _build_folio_map(notas)
    prov_ids = {n.proveedor_id for n in notas if n.proveedor_id}
    cli_ids = {n.cliente_id for n in notas if n.cliente_id}
    proveedores_map = (
        {
            p.id: p.nombre_completo
            for p in db.query(Proveedor).filter(Proveedor.id.in_(prov_ids)).all()
        }
        if prov_ids
        else {}
    )
    clientes_map = (
        {
            c.id: c.nombre_completo
            for c in db.query(Cliente).filter(Cliente.id.in_(cli_ids)).all()
        }
        if cli_ids
        else {}
    )
    suc_ids = {
        mov.caja_sucursal_id
        for mov in movs
        if mov.caja_sucursal_id
    }
    suc_ids.update({mov.sucursal_id for mov in movs if mov.sucursal_id})
    sucursales_map = (
        {s.id: s for s in db.query(Sucursal).filter(Sucursal.id.in_(suc_ids)).all()}
        if suc_ids
        else {}
    )

    movimientos: list[dict] = []
    total_ingresos = Decimal("0")
    total_egresos = Decimal("0")
    neto = Decimal("0")
    ventas_efectivo_rows: list[dict] = []
    compras_efectivo_rows: list[dict] = []
    ventas_efectivo_total = Decimal("0")
    compras_efectivo_total = Decimal("0")

    for mov in movs:
        tipo_raw = (mov.tipo or "").lower()
        tipo_op = _movimiento_tipo_operacion(mov)
        signed = _movimiento_monto_firmado(mov, tipo_raw, tipo_op)
        neto += signed
        if signed >= 0:
            total_ingresos += signed
        else:
            total_egresos += abs(signed)
        nota = mov.nota or (nota_map.get(mov.nota_id) if mov.nota_id else None)
        caja_sucursal_label = "-"
        caja_sucursal_id = mov.caja_sucursal_id or mov.sucursal_id
        if caja_sucursal_id and sucursales_map.get(caja_sucursal_id):
            caja_sucursal_label = sucursales_map[caja_sucursal_id].nombre
        elif caja_sucursal_id:
            caja_sucursal_label = str(caja_sucursal_id)

        row = {
            "fecha": mov.created_at,
            "tipo": _movimiento_label(tipo_raw, tipo_op),
            "detalle": tipo_op or "-",
            "nota_id": mov.nota_id,
            "folio": folio_map.get(mov.nota_id) or (f"#{mov.nota_id}" if mov.nota_id else "-"),
            "partner": _partner_name_for_nota(nota, proveedores_map, clientes_map),
            "monto": signed,
            "monto_abs": abs(signed),
            "comentario": mov.comentario or "",
            "caja_sucursal": caja_sucursal_label,
            "nota_tipo": tipo_op or "-",
        }
        movimientos.append(row)
        if tipo_op == "venta" and signed > 0:
            ventas_efectivo_rows.append(row)
            ventas_efectivo_total += signed
        if tipo_op == "compra" and signed < 0:
            compras_efectivo_rows.append(row)
            compras_efectivo_total += abs(signed)

    movimientos.sort(key=lambda m: (m["fecha"] or datetime.min, m["tipo"], m["folio"]))
    ventas_efectivo_rows.sort(key=lambda m: (m["fecha"] or datetime.min, m["folio"]))
    compras_efectivo_rows.sort(key=lambda m: (m["fecha"] or datetime.min, m["folio"]))

    return {
        "movimientos": movimientos,
        "ingresos": total_ingresos,
        "egresos": total_egresos,
        "neto": neto,
        "total": len(movimientos),
        "ventas_efectivo_rows": ventas_efectivo_rows,
        "ventas_efectivo_total": ventas_efectivo_total,
        "compras_efectivo_rows": compras_efectivo_rows,
        "compras_efectivo_total": compras_efectivo_total,
    }


def _corte_mov_sign(tipo_raw: CorteCajaMovimientoTipo | str | None) -> Decimal:
    tipo_val = ""
    if isinstance(tipo_raw, CorteCajaMovimientoTipo):
        tipo_val = tipo_raw.value
    elif hasattr(tipo_raw, "value"):
        tipo_val = str(tipo_raw.value)
    else:
        tipo_val = str(tipo_raw or "")
    tipo_val = tipo_val.upper()
    if tipo_val in ("INGRESO", "DEPOSITO"):
        return Decimal("1")
    return Decimal("-1")


def _get_previous_closed_corte(
    db: Session,
    *,
    sucursal_id: int,
    fecha: date,
) -> CorteCaja | None:
    return (
        db.query(CorteCaja)
        .filter(
            CorteCaja.sucursal_id == sucursal_id,
            CorteCaja.estado == CorteCajaEstado.cerrado,
            CorteCaja.fecha < fecha,
        )
        .order_by(CorteCaja.fecha.desc(), CorteCaja.id.desc())
        .first()
    )


def _build_corte_manual_movimientos(
    db: Session,
    *,
    corte_id: int,
) -> dict:
    movimientos_db = (
        db.query(CorteCajaMovimiento)
        .filter(CorteCajaMovimiento.corte_id == corte_id)
        .order_by(CorteCajaMovimiento.created_at.asc())
        .all()
    )
    movimientos: list[dict] = []
    movimientos_by_categoria: dict[str, list[dict]] = defaultdict(list)
    total_ingresos = Decimal("0")
    total_egresos = Decimal("0")
    neto = Decimal("0")
    totals_by_tipo = {tipo: Decimal("0") for tipo, _ in _CORTE_MOV_TIPOS}
    tipo_labels = dict(_CORTE_MOV_TIPOS)
    totals_by_categoria: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))

    for mov in movimientos_db:
        tipo_val = mov.tipo.value if isinstance(mov.tipo, CorteCajaMovimientoTipo) else str(mov.tipo or "").upper()
        monto = Decimal(str(mov.monto or 0))
        totals_by_tipo[tipo_val] = totals_by_tipo.get(tipo_val, Decimal("0")) + monto
        signed = monto * _corte_mov_sign(tipo_val)
        neto += signed
        if signed >= 0:
            total_ingresos += signed
        else:
            total_egresos += abs(signed)
        row = {
            "fecha": mov.created_at,
            "tipo": tipo_val,
            "tipo_label": tipo_labels.get(tipo_val, tipo_val.title()),
            "categoria": (mov.categoria or "").upper() or None,
            "categoria_label": _corte_mov_category_label(mov.categoria),
            "descripcion": mov.descripcion,
            "usuario": mov.usuario.nombre_completo if mov.usuario else "-",
            "monto": signed,
            "monto_abs": abs(signed),
        }
        movimientos.append(row)
        if row["categoria"]:
            movimientos_by_categoria[row["categoria"]].append(row)
            totals_by_categoria[row["categoria"]] += signed

    return {
        "movimientos": movimientos,
        "ingresos": total_ingresos,
        "egresos": total_egresos,
        "neto": neto,
        "total": len(movimientos),
        "totals_by_tipo": totals_by_tipo,
        "movimientos_by_categoria": movimientos_by_categoria,
        "totals_by_categoria": totals_by_categoria,
    }


def _build_corte_note_relations(
    db: Session,
    *,
    sucursal_id: int,
    start_dt: datetime,
    end_dt: datetime,
) -> dict:
    base_movs = (
        db.query(MovimientoContable)
        .filter(
            MovimientoContable.sucursal_id == sucursal_id,
            MovimientoContable.tipo.in_(["compra", "venta"]),
            MovimientoContable.created_at >= start_dt,
            MovimientoContable.created_at < end_dt,
        )
        .order_by(MovimientoContable.created_at.asc(), MovimientoContable.id.asc())
        .all()
    )
    note_ids = []
    for mov in base_movs:
        if mov.nota_id and mov.nota_id not in note_ids:
            note_ids.append(mov.nota_id)

    notas = db.query(Nota).filter(Nota.id.in_(note_ids)).all() if note_ids else []
    nota_map = {nota.id: nota for nota in notas}
    folio_map = _build_folio_map(notas)
    prov_ids = {n.proveedor_id for n in notas if n.proveedor_id}
    cli_ids = {n.cliente_id for n in notas if n.cliente_id}
    scrap360_ids = {n.cuenta_scrap360_id for n in notas if n.cuenta_scrap360_id}
    scrap360_map = (
        {c.id: c.nombre for c in db.query(CuentaScrap360).filter(CuentaScrap360.id.in_(scrap360_ids)).all()}
        if scrap360_ids
        else {}
    )
    proveedores_map = (
        {
            p.id: p.nombre_completo
            for p in db.query(Proveedor).filter(Proveedor.id.in_(prov_ids)).all()
        }
        if prov_ids
        else {}
    )
    clientes_map = (
        {
            c.id: c.nombre_completo
            for c in db.query(Cliente).filter(Cliente.id.in_(cli_ids)).all()
        }
        if cli_ids
        else {}
    )

    compras_rows: list[dict] = []
    ventas_rows: list[dict] = []
    compras_total = Decimal("0")
    ventas_total = Decimal("0")
    compras_kg = Decimal("0")
    ventas_kg = Decimal("0")

    for mov in base_movs:
        nota = mov.nota or nota_map.get(mov.nota_id)
        if not nota:
            continue
        partner = _partner_name_for_nota(nota, proveedores_map, clientes_map)
        target_rows = compras_rows if nota.tipo_operacion == TipoOperacion.compra else ventas_rows
        cuenta_label = nota.cuenta.display_label if nota.cuenta else None
        for nm in nota.materiales:
            material_name = nm.material.nombre if nm.material else f"Material #{nm.material_id}"
            kg_neto = Decimal(str(nm.kg_neto or 0))
            precio_unitario = Decimal(str(nm.precio_unitario or 0))
            subtotal = Decimal(str(nm.subtotal or 0))
            row = {
                "fecha": mov.created_at,
                "nota_id": nota.id,
                "folio": folio_map.get(nota.id) or f"#{nota.id}",
                "partner": partner,
                "material": material_name,
                "material_orden_display": nm.material.orden_display if nm.material else 999,
                "kg_neto": kg_neto,
                "precio_unitario": precio_unitario,
                "subtotal": subtotal,
                "total_nota": Decimal(str(nota.total_monto or 0)),
                "metodo_pago": nota.metodo_pago or "-",
                "numero_cheque": nota.numero_cheque or "",
                "cuenta_financiera_id": nota.cuenta_financiera_id,
                "cuenta_financiera_label": cuenta_label or "",
                "cuenta_scrap360_id": nota.cuenta_scrap360_id,
                "cuenta_scrap360_nombre": scrap360_map.get(nota.cuenta_scrap360_id, "") if nota.cuenta_scrap360_id else "",
                "es_efectivo": (nota.metodo_pago or "").strip().lower() == "efectivo",
            }
            target_rows.append(row)
            if nota.tipo_operacion == TipoOperacion.compra:
                compras_total += subtotal
                compras_kg += kg_neto
            else:
                ventas_total += subtotal
                ventas_kg += kg_neto

    compras_rows.sort(key=lambda row: (row["fecha"] or datetime.min, row["folio"], row["material_orden_display"]))
    ventas_rows.sort(key=lambda row: (row["fecha"] or datetime.min, row["folio"], row["material_orden_display"]))

    # Assign color keys: cash → "cash", per-account → "account-N", no account → "transfer"
    account_color_map: dict = {}
    color_counter = 1
    for row in compras_rows + ventas_rows:
        if row["es_efectivo"]:
            row["color_key"] = "cash"
        elif row["cuenta_financiera_id"]:
            acct_id = row["cuenta_financiera_id"]
            if acct_id not in account_color_map:
                account_color_map[acct_id] = color_counter
                color_counter += 1
            row["color_key"] = f"account-{account_color_map[acct_id]}"
        else:
            row["color_key"] = "transfer"

    # Build Scrap360 account summary (non-cash notes only, deduplicated by nota_id)
    _s360: dict[int | None, dict] = {}
    for _row, _side in [(r, "compra") for r in compras_rows] + [(r, "venta") for r in ventas_rows]:
        if _row["es_efectivo"]:
            continue
        _key = _row["cuenta_scrap360_id"] or None
        _nombre = _row["cuenta_scrap360_nombre"] or "Sin especificar"
        if _key not in _s360:
            _s360[_key] = {
                "nombre": _nombre,
                "compras_total": Decimal("0"),
                "ventas_total": Decimal("0"),
                "compras_notas": set(),
                "ventas_notas": set(),
            }
        _nota_id = _row["nota_id"]
        _total = Decimal(str(_row.get("total_nota") or 0))
        if _side == "compra" and _nota_id not in _s360[_key]["compras_notas"]:
            _s360[_key]["compras_notas"].add(_nota_id)
            _s360[_key]["compras_total"] += _total
        elif _side == "venta" and _nota_id not in _s360[_key]["ventas_notas"]:
            _s360[_key]["ventas_notas"].add(_nota_id)
            _s360[_key]["ventas_total"] += _total
    scrap360_summary = sorted(
        [
            {
                "cuenta_scrap360_id": k,
                "nombre": v["nombre"],
                "compras_total": v["compras_total"],
                "ventas_total": v["ventas_total"],
                "compras_notas": len(v["compras_notas"]),
                "ventas_notas": len(v["ventas_notas"]),
            }
            for k, v in _s360.items()
        ],
        key=lambda x: (1 if x["cuenta_scrap360_id"] is None else 0, x["nombre"]),
    )

    return {
        "compras_rows": compras_rows,
        "ventas_rows": ventas_rows,
        "compras_total": compras_total,
        "ventas_total": ventas_total,
        "compras_kg": compras_kg,
        "ventas_kg": ventas_kg,
        "compras_notas": len({row["nota_id"] for row in compras_rows}),
        "ventas_notas": len({row["nota_id"] for row in ventas_rows}),
        "scrap360_summary": scrap360_summary,
    }


def _build_corte_color_legend(all_rows: list[dict]) -> list[dict]:
    """Build a list of {color_key, label, metodo, cuenta_label} for the corte legend."""
    seen: dict[str, dict] = {}
    for row in all_rows:
        key = row.get("color_key", "transfer")
        if key not in seen:
            metodo = (row.get("metodo_pago") or "-").capitalize()
            cuenta = row.get("cuenta_financiera_label") or ""
            if key == "cash":
                label = "Efectivo"
            elif cuenta:
                label = f"{metodo} — {cuenta}"
            else:
                label = metodo
            seen[key] = {"color_key": key, "label": label}
    # Sort: cash first, then account-N in order, transfer last
    def sort_key(item: dict) -> tuple:
        k = item["color_key"]
        if k == "cash":
            return (0, 0)
        if k.startswith("account-"):
            try:
                return (1, int(k.split("-")[1]))
            except (IndexError, ValueError):
                return (1, 9999)
        return (2, 0)
    return sorted(seen.values(), key=sort_key)


def _render_corte_caja(
    request: Request,
    db: Session,
    current_user: dict,
    *,
    sucursal_id: int | None,
    fecha: date,
    allowed_suc_ids: list[int] | None,
    error: str | None = None,
    success: str | None = None,
    form_data: dict | None = None,
):
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    cuentas_scrap360 = db.query(CuentaScrap360).filter(CuentaScrap360.activo.is_(True)).order_by(CuentaScrap360.nombre).all()
    if allowed_suc_ids is not None:
        if sucursal_id and sucursal_id not in allowed_suc_ids:
            sucursal_id = None
        if sucursal_id is None and len(allowed_suc_ids) == 1:
            sucursal_id = allowed_suc_ids[0]
    if sucursal_id is None and sucursales:
        sucursal_id = sucursales[0].id

    corte = None
    if sucursal_id:
        corte = (
            db.query(CorteCaja)
            .filter(CorteCaja.sucursal_id == sucursal_id, CorteCaja.fecha == fecha)
            .first()
        )
    previous_closed_corte = None
    auto_saldo_inicial = Decimal("0")
    if sucursal_id and not corte:
        previous_closed_corte = _get_previous_closed_corte(db, sucursal_id=sucursal_id, fecha=fecha)
        if previous_closed_corte and previous_closed_corte.saldo_cierre is not None:
            auto_saldo_inicial = Decimal(str(previous_closed_corte.saldo_cierre or 0))
    historial = []
    if sucursal_id:
        historial = (
            db.query(CorteCaja)
            .filter(CorteCaja.sucursal_id == sucursal_id)
            .order_by(CorteCaja.fecha.desc())
            .limit(15)
            .all()
        )
    sucursal_actual = db.get(Sucursal, sucursal_id) if sucursal_id else None

    gastos = []
    gastos_total = Decimal("0")
    if corte:
        gastos = (
            db.query(CorteCajaGasto)
            .filter(CorteCajaGasto.corte_id == corte.id)
            .order_by(CorteCajaGasto.created_at.desc())
            .all()
        )
        for gasto in gastos:
            gastos_total += Decimal(str(gasto.monto or 0))

    corte_window_start, corte_window_end = _resolve_corte_window(corte=corte, fecha=fecha)

    cash_data = None
    if sucursal_id:
        cash_data = _build_corte_cash_movimientos(
            db,
            sucursal_id=sucursal_id,
            start_dt=corte_window_start,
            end_dt=corte_window_end,
        )
    else:
        cash_data = {
            "movimientos": [],
            "ingresos": Decimal("0"),
            "egresos": Decimal("0"),
            "neto": Decimal("0"),
            "total": 0,
            "ventas_efectivo_rows": [],
            "ventas_efectivo_total": Decimal("0"),
            "compras_efectivo_rows": [],
            "compras_efectivo_total": Decimal("0"),
        }

    manual_data = {
        "movimientos": [],
        "ingresos": Decimal("0"),
        "egresos": Decimal("0"),
        "neto": Decimal("0"),
        "total": 0,
        "totals_by_tipo": {},
        "movimientos_by_categoria": {},
        "totals_by_categoria": {},
    }
    if corte:
        manual_data = _build_corte_manual_movimientos(db, corte_id=corte.id)

    relaciones = (
        _build_corte_note_relations(
            db,
            sucursal_id=sucursal_id,
            start_dt=corte_window_start,
            end_dt=corte_window_end,
        )
        if sucursal_id
        else {
            "compras_rows": [],
            "ventas_rows": [],
            "compras_total": Decimal("0"),
            "ventas_total": Decimal("0"),
            "compras_kg": Decimal("0"),
            "ventas_kg": Decimal("0"),
            "compras_notas": 0,
            "ventas_notas": 0,
            "scrap360_summary": [],
        }
    )

    denominaciones = []
    denom_map: dict[Decimal, int] = {}
    if corte:
        denominaciones = (
            db.query(CorteCajaDenominacion)
            .filter(CorteCajaDenominacion.corte_id == corte.id)
            .order_by(CorteCajaDenominacion.valor.desc())
            .all()
        )
        denom_map = {Decimal(str(d.valor or 0)): int(d.cantidad or 0) for d in denominaciones}

    saldo_inicial = Decimal(str(corte.saldo_inicial or 0)) if corte else Decimal("0")
    saldo_calculado_actual = saldo_inicial + cash_data["neto"] + manual_data["neto"] - gastos_total
    saldo_calculado = saldo_calculado_actual
    if corte and corte.estado == CorteCajaEstado.cerrado:
        saldo_calculado = Decimal(str(corte.saldo_calculado or 0))

    form_data = form_data or {}
    form_saldo_inicial = (form_data.get("saldo_inicial") or "").strip()
    if not form_saldo_inicial and not corte:
        form_saldo_inicial = f"{auto_saldo_inicial:.2f}"
    (
        denom_inputs,
        denom_monedas,
        denom_billetes,
        denom_total_monedas,
        denom_total_billetes,
        denom_total,
    ) = _build_corte_denom_inputs(denom_map, form_data)

    movimientos_by_categoria = manual_data.get("movimientos_by_categoria", {})
    totals_by_categoria = manual_data.get("totals_by_categoria", {})
    dotaciones_rows = list(movimientos_by_categoria.get("DOTACION_EFECTIVO", []))
    sobrantes_viaticos_rows = list(movimientos_by_categoria.get("SOBRANTE_VIATICOS", []))
    sobrantes_gastos_rows = list(movimientos_by_categoria.get("SOBRANTE_GASTOS", []))
    otros_ajustes_rows = [
        mov
        for mov in manual_data.get("movimientos", [])
        if (mov.get("categoria") or "") not in {"DOTACION_EFECTIVO", "SOBRANTE_VIATICOS", "SOBRANTE_GASTOS"}
    ]
    dotaciones_total = abs(Decimal(str(totals_by_categoria.get("DOTACION_EFECTIVO", 0) or 0)))
    sobrantes_viaticos_total = abs(Decimal(str(totals_by_categoria.get("SOBRANTE_VIATICOS", 0) or 0)))
    sobrantes_gastos_total = abs(Decimal(str(totals_by_categoria.get("SOBRANTE_GASTOS", 0) or 0)))
    otros_ajustes_total = sum((Decimal(str(mov.get("monto_abs") or 0)) for mov in otros_ajustes_rows), Decimal("0"))
    diferencia_provisional = denom_total - saldo_calculado_actual

    return templates.TemplateResponse(
        "admin/corte_caja.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "sucursales": sucursales,
            "sucursal_actual": sucursal_actual,
            "sucursal_id": sucursal_id,
            "fecha": fecha.strftime("%Y-%m-%d"),
            "corte": corte,
            "corte_window_start": corte_window_start,
            "corte_window_end": corte_window_end,
            "gastos": gastos,
            "gastos_total": gastos_total,
            "cash_movs": cash_data["movimientos"],
            "cash_ingresos": cash_data["ingresos"],
            "cash_egresos": cash_data["egresos"],
            "cash_total": cash_data["total"],
            "ventas_efectivo_rows": cash_data["ventas_efectivo_rows"],
            "ventas_efectivo_total": cash_data["ventas_efectivo_total"],
            "compras_efectivo_rows": cash_data["compras_efectivo_rows"],
            "compras_efectivo_total": cash_data["compras_efectivo_total"],
            "manual_movs": manual_data["movimientos"],
            "manual_ingresos": manual_data["ingresos"],
            "manual_egresos": manual_data["egresos"],
            "manual_neto": manual_data["neto"],
            "manual_total": manual_data["total"],
            "manual_totals_by_tipo": manual_data["totals_by_tipo"],
            "dotaciones_rows": dotaciones_rows,
            "dotaciones_total": dotaciones_total,
            "sobrantes_viaticos_rows": sobrantes_viaticos_rows,
            "sobrantes_viaticos_total": sobrantes_viaticos_total,
            "sobrantes_gastos_rows": sobrantes_gastos_rows,
            "sobrantes_gastos_total": sobrantes_gastos_total,
            "otros_ajustes_rows": otros_ajustes_rows,
            "otros_ajustes_total": otros_ajustes_total,
            "compras_rows": relaciones["compras_rows"],
            "ventas_rows": relaciones["ventas_rows"],
            "compras_total": relaciones["compras_total"],
            "ventas_total": relaciones["ventas_total"],
            "compras_kg": relaciones["compras_kg"],
            "ventas_kg": relaciones["ventas_kg"],
            "compras_notas": relaciones["compras_notas"],
            "ventas_notas": relaciones["ventas_notas"],
            "scrap360_summary": relaciones["scrap360_summary"],
            "corte_color_legend": _build_corte_color_legend(
                relaciones["compras_rows"] + relaciones["ventas_rows"]
            ),
            "saldo_inicial": saldo_inicial,
            "saldo_calculado_actual": saldo_calculado_actual,
            "saldo_calculado": saldo_calculado,
            "denominaciones": denominaciones,
            "denom_inputs": denom_inputs,
            "denom_monedas": denom_monedas,
            "denom_billetes": denom_billetes,
            "denom_total_monedas": denom_total_monedas,
            "denom_total_billetes": denom_total_billetes,
            "denom_total": denom_total,
            "diferencia_provisional": diferencia_provisional,
            "historial": historial,
            "error": error,
            "success": success,
            "next_ready": request.query_params.get("next_ready") == "1",
            "next_fecha_query": request.query_params.get("next_fecha") or "",
            "corte_mov_tipos": _CORTE_MOV_TIPOS,
            "corte_mov_categorias": _CORTE_MOV_CATEGORIAS,
            "corte_gasto_categorias": _CORTE_GASTO_CATEGORIAS,
            "cuentas_scrap360": cuentas_scrap360,
            "form_saldo_inicial": form_saldo_inicial,
            "auto_saldo_inicial": auto_saldo_inicial,
            "previous_closed_corte": previous_closed_corte,
            "form_gasto_desc": form_data.get("gasto_desc", ""),
            "form_gasto_monto": form_data.get("gasto_monto", ""),
            "form_gasto_categoria": form_data.get("gasto_categoria", ""),
            "form_mov_tipo": form_data.get("mov_tipo", ""),
            "form_mov_categoria": form_data.get("mov_categoria", ""),
            "form_mov_desc": form_data.get("mov_desc", ""),
            "form_mov_monto": form_data.get("mov_monto", ""),
            "form_saldo_cierre": form_data.get("saldo_cierre", f"{denom_total:.2f}"),
            "form_cierre_comentarios": form_data.get("cierre_comentarios", ""),
            "form_motivo_diferencia": form_data.get("motivo_diferencia", ""),
        },
        status_code=400 if error else 200,
    )


@router.get("/corte-caja")
async def corte_caja_list(
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
    fecha, fecha_error = _parse_corte_fecha(params.get("fecha"))
    success = params.get("success")
    return _render_corte_caja(
        request,
        db,
        current_user,
        sucursal_id=sucursal_id,
        fecha=fecha,
        allowed_suc_ids=allowed_suc_ids,
        error=fecha_error,
        success=success,
    )


@router.post("/corte-caja/abrir")
async def corte_caja_abrir(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    form = await request.form()
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    sucursal_raw = (form.get("sucursal_id") or "").strip()
    fecha_raw = (form.get("fecha") or "").strip()
    saldo_raw = (form.get("saldo_inicial") or "").strip()

    try:
        sucursal_id = int(sucursal_raw)
    except (ValueError, TypeError):
        sucursal_id = None
    fecha, fecha_error = _parse_corte_fecha(fecha_raw)
    if fecha_error:
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=sucursal_id,
            fecha=fecha,
            allowed_suc_ids=allowed_suc_ids,
            error=fecha_error,
            form_data={"saldo_inicial": saldo_raw},
        )

    if not sucursal_id:
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=None,
            fecha=fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="Selecciona una sucursal valida.",
            form_data={"saldo_inicial": saldo_raw},
        )
    if allowed_suc_ids is not None and sucursal_id not in allowed_suc_ids:
        raise HTTPException(status_code=403, detail="No tienes acceso a esta sucursal.")
    sucursal_obj = db.query(Sucursal).get(sucursal_id)
    if sucursal_obj and sucursal_obj.estado == SucursalStatus.inactiva:
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=sucursal_id,
            fecha=fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="Esta sucursal está archivada: puedes consultar sus cortes históricos, pero no abrir uno nuevo.",
            form_data={"saldo_inicial": saldo_raw},
        )

    if not saldo_raw:
        previous_closed_corte = _get_previous_closed_corte(db, sucursal_id=sucursal_id, fecha=fecha)
        if previous_closed_corte and previous_closed_corte.saldo_cierre is not None:
            saldo_raw = f"{Decimal(str(previous_closed_corte.saldo_cierre or 0)):.2f}"
        else:
            saldo_raw = "0.00"

    try:
        saldo_inicial = Decimal(str(saldo_raw))
    except (InvalidOperation, TypeError):
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=sucursal_id,
            fecha=fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="El saldo inicial es invalido.",
            form_data={"saldo_inicial": saldo_raw},
        )
    if saldo_inicial < Decimal("0"):
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=sucursal_id,
            fecha=fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="El saldo inicial no puede ser negativo.",
            form_data={"saldo_inicial": saldo_raw},
        )

    existing = (
        db.query(CorteCaja)
        .filter(CorteCaja.sucursal_id == sucursal_id, CorteCaja.fecha == fecha)
        .first()
    )
    if existing:
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=sucursal_id,
            fecha=fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="Ya existe un corte de caja para esa fecha.",
            form_data={"saldo_inicial": saldo_raw},
        )

    corte = CorteCaja(
        sucursal_id=sucursal_id,
        fecha=fecha,
        estado=CorteCajaEstado.abierto,
        saldo_inicial=saldo_inicial,
        abierto_por_id=current_user.get("id"),
        opened_at=datetime.utcnow(),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(corte)
    db.commit()

    return RedirectResponse(
        url=f"/web/admin/corte-caja?sucursal_id={sucursal_id}&fecha={fecha.isoformat()}&success=open",
        status_code=303,
    )


@router.post("/corte-caja/{corte_id}/gastos")
async def corte_caja_add_gasto(
    corte_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    form = await request.form()
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    corte = db.get(CorteCaja, corte_id)
    if not corte:
        raise HTTPException(status_code=404, detail="Corte de caja no encontrado.")
    if corte.estado != CorteCajaEstado.abierto:
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=corte.sucursal_id,
            fecha=corte.fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="El corte ya esta cerrado.",
        )
    if allowed_suc_ids is not None and corte.sucursal_id not in allowed_suc_ids:
        raise HTTPException(status_code=403, detail="No tienes acceso a esta sucursal.")

    descripcion = (form.get("descripcion") or "").strip()
    categoria = (form.get("categoria") or "").strip().upper()
    monto_raw = (form.get("monto") or "").strip()
    if not descripcion:
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=corte.sucursal_id,
            fecha=corte.fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="La descripcion del gasto es obligatoria.",
            form_data={"gasto_desc": descripcion, "gasto_monto": monto_raw, "gasto_categoria": categoria},
        )
    try:
        monto = Decimal(str(monto_raw))
    except (InvalidOperation, TypeError):
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=corte.sucursal_id,
            fecha=corte.fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="El monto del gasto es invalido.",
            form_data={"gasto_desc": descripcion, "gasto_monto": monto_raw, "gasto_categoria": categoria},
        )
    if monto <= Decimal("0"):
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=corte.sucursal_id,
            fecha=corte.fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="El monto del gasto debe ser mayor a 0.",
            form_data={"gasto_desc": descripcion, "gasto_monto": monto_raw, "gasto_categoria": categoria},
        )

    allowed_categories = {code for code, _ in _CORTE_GASTO_CATEGORIAS}
    if categoria and categoria not in allowed_categories:
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=corte.sucursal_id,
            fecha=corte.fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="La categoria del gasto es invalida.",
            form_data={"gasto_desc": descripcion, "gasto_monto": monto_raw, "gasto_categoria": categoria},
        )

    gasto = CorteCajaGasto(
        corte_id=corte.id,
        usuario_id=current_user.get("id"),
        descripcion=descripcion,
        categoria=categoria or None,
        monto=monto,
        created_at=datetime.utcnow(),
    )
    db.add(gasto)
    db.commit()

    return RedirectResponse(
        url=f"/web/admin/corte-caja?sucursal_id={corte.sucursal_id}&fecha={corte.fecha.isoformat()}&success=gasto",
        status_code=303,
    )


@router.post("/corte-caja/{corte_id}/movimientos")
async def corte_caja_add_movimiento(
    corte_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    form = await request.form()
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    corte = db.get(CorteCaja, corte_id)
    if not corte:
        raise HTTPException(status_code=404, detail="Corte de caja no encontrado.")
    if corte.estado != CorteCajaEstado.abierto:
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=corte.sucursal_id,
            fecha=corte.fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="El corte ya esta cerrado.",
        )
    if allowed_suc_ids is not None and corte.sucursal_id not in allowed_suc_ids:
        raise HTTPException(status_code=403, detail="No tienes acceso a esta sucursal.")

    tipo_raw = (form.get("tipo") or "").strip().upper()
    categoria = (form.get("categoria") or "").strip().upper()
    descripcion = (form.get("descripcion") or "").strip()
    monto_raw = (form.get("monto") or "").strip()

    allowed_types = {t[0] for t in _CORTE_MOV_TIPOS}
    allowed_categories = {code for code, _, _ in _CORTE_MOV_CATEGORIAS}
    if categoria and categoria not in allowed_categories:
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=corte.sucursal_id,
            fecha=corte.fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="La categoria del movimiento es invalida.",
            form_data={"mov_tipo": tipo_raw, "mov_categoria": categoria, "mov_desc": descripcion, "mov_monto": monto_raw},
        )
    if not tipo_raw and categoria:
        tipo_raw = _corte_mov_default_type(categoria)
    if tipo_raw not in allowed_types:
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=corte.sucursal_id,
            fecha=corte.fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="El tipo de movimiento es invalido.",
            form_data={"mov_tipo": tipo_raw, "mov_categoria": categoria, "mov_desc": descripcion, "mov_monto": monto_raw},
        )
    if not descripcion:
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=corte.sucursal_id,
            fecha=corte.fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="La descripcion del movimiento es obligatoria.",
            form_data={"mov_tipo": tipo_raw, "mov_categoria": categoria, "mov_desc": descripcion, "mov_monto": monto_raw},
        )
    try:
        monto = Decimal(str(monto_raw))
    except (InvalidOperation, TypeError):
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=corte.sucursal_id,
            fecha=corte.fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="El monto del movimiento es invalido.",
            form_data={"mov_tipo": tipo_raw, "mov_categoria": categoria, "mov_desc": descripcion, "mov_monto": monto_raw},
        )
    if monto <= Decimal("0"):
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=corte.sucursal_id,
            fecha=corte.fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="El monto del movimiento debe ser mayor a 0.",
            form_data={"mov_tipo": tipo_raw, "mov_categoria": categoria, "mov_desc": descripcion, "mov_monto": monto_raw},
        )

    movimiento = CorteCajaMovimiento(
        corte_id=corte.id,
        usuario_id=current_user.get("id"),
        tipo=CorteCajaMovimientoTipo(tipo_raw),
        categoria=categoria or None,
        descripcion=descripcion,
        monto=monto,
        created_at=datetime.utcnow(),
    )
    db.add(movimiento)
    db.commit()

    return RedirectResponse(
        url=f"/web/admin/corte-caja?sucursal_id={corte.sucursal_id}&fecha={corte.fecha.isoformat()}&success=movimiento",
        status_code=303,
    )


@router.post("/corte-caja/{corte_id}/arqueo")
async def corte_caja_guardar_arqueo(
    corte_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    form = await request.form()
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    corte = db.get(CorteCaja, corte_id)
    if not corte:
        raise HTTPException(status_code=404, detail="Corte de caja no encontrado.")
    if corte.estado != CorteCajaEstado.abierto:
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=corte.sucursal_id,
            fecha=corte.fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="El corte ya esta cerrado.",
        )
    if allowed_suc_ids is not None and corte.sucursal_id not in allowed_suc_ids:
        raise HTTPException(status_code=403, detail="No tienes acceso a esta sucursal.")

    denom_entries, saldo_contado, form_data, denom_error = _parse_corte_denominaciones_form(form)
    if denom_error:
        form_data["saldo_cierre"] = f"{saldo_contado:.2f}"
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=corte.sucursal_id,
            fecha=corte.fecha,
            allowed_suc_ids=allowed_suc_ids,
            error=denom_error,
            form_data=form_data,
        )

    _replace_corte_denominaciones(db, corte, denom_entries)
    corte.updated_at = datetime.utcnow()
    db.add(corte)
    db.commit()

    return RedirectResponse(
        url=f"/web/admin/corte-caja?sucursal_id={corte.sucursal_id}&fecha={corte.fecha.isoformat()}&success=arqueo",
        status_code=303,
    )


@router.post("/corte-caja/{corte_id}/cerrar")
async def corte_caja_cerrar(
    corte_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_superadmin),
):
    form = await request.form()
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    corte = db.get(CorteCaja, corte_id)
    if not corte:
        raise HTTPException(status_code=404, detail="Corte de caja no encontrado.")
    if corte.estado != CorteCajaEstado.abierto:
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=corte.sucursal_id,
            fecha=corte.fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="El corte ya esta cerrado.",
        )

    comentarios = (form.get("comentarios_cierre") or "").strip()
    motivo_diferencia = (form.get("motivo_diferencia") or "").strip()
    denom_entries, saldo_cierre, denom_form_data, denom_error = _parse_corte_denominaciones_form(form)
    form_data = {
        **denom_form_data,
        "cierre_comentarios": comentarios,
        "motivo_diferencia": motivo_diferencia,
    }
    if denom_error:
        form_data["saldo_cierre"] = f"{saldo_cierre:.2f}"
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=corte.sucursal_id,
            fecha=corte.fecha,
            allowed_suc_ids=allowed_suc_ids,
            error=denom_error,
            form_data=form_data,
        )
    form_data["saldo_cierre"] = f"{saldo_cierre:.2f}"

    corte_window_start, _ = _resolve_corte_window(corte=corte, fecha=corte.fecha)
    closed_at = datetime.utcnow()
    cash_data = _build_corte_cash_movimientos(
        db,
        sucursal_id=corte.sucursal_id,
        start_dt=corte_window_start,
        end_dt=closed_at,
    )
    manual_data = _build_corte_manual_movimientos(db, corte_id=corte.id)
    gastos_total = sum((Decimal(str(g.monto or 0)) for g in corte.gastos), Decimal("0"))
    saldo_calculado = Decimal(str(corte.saldo_inicial or 0)) + cash_data["neto"] + manual_data["neto"] - gastos_total
    diferencia = saldo_cierre - saldo_calculado
    if diferencia != Decimal("0") and not motivo_diferencia:
        return _render_corte_caja(
            request,
            db,
            current_user,
            sucursal_id=corte.sucursal_id,
            fecha=corte.fecha,
            allowed_suc_ids=allowed_suc_ids,
            error="El motivo de la diferencia es obligatorio.",
            form_data=form_data,
        )

    corte.saldo_calculado = saldo_calculado
    corte.saldo_cierre = saldo_cierre
    corte.diferencia = diferencia
    corte.motivo_diferencia = motivo_diferencia or None
    corte.comentarios_cierre = comentarios or None
    corte.estado = CorteCajaEstado.cerrado
    corte.cerrado_por_id = current_user.get("id")
    corte.closed_at = closed_at
    corte.updated_at = datetime.utcnow()
    db.add(corte)
    _replace_corte_denominaciones(db, corte, denom_entries)
    next_fecha = corte.fecha + timedelta(days=1)
    next_corte = (
        db.query(CorteCaja)
        .filter(CorteCaja.sucursal_id == corte.sucursal_id, CorteCaja.fecha == next_fecha)
        .first()
    )
    if not next_corte:
        next_corte = CorteCaja(
            sucursal_id=corte.sucursal_id,
            fecha=next_fecha,
            estado=CorteCajaEstado.abierto,
            saldo_inicial=saldo_cierre,
            abierto_por_id=current_user.get("id"),
            opened_at=closed_at,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(next_corte)
    db.commit()

    return RedirectResponse(
        url=f"/web/admin/corte-caja?sucursal_id={corte.sucursal_id}&fecha={corte.fecha.isoformat()}&success=close&next_ready=1&next_fecha={next_fecha.isoformat()}",
        status_code=303,
    )


@router.get("/corte-caja/{corte_id}/reporte")
async def corte_caja_reporte(
    corte_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    corte = db.get(CorteCaja, corte_id)
    if not corte:
        raise HTTPException(status_code=404, detail="Corte de caja no encontrado.")
    if allowed_suc_ids is not None and corte.sucursal_id not in allowed_suc_ids:
        raise HTTPException(status_code=403, detail="No tienes acceso a esta sucursal.")

    sucursal = db.get(Sucursal, corte.sucursal_id)
    corte_window_start, corte_window_end = _resolve_corte_window(corte=corte, fecha=corte.fecha)
    cash_data = _build_corte_cash_movimientos(
        db,
        sucursal_id=corte.sucursal_id,
        start_dt=corte_window_start,
        end_dt=corte_window_end,
    )
    manual_data = _build_corte_manual_movimientos(db, corte_id=corte.id)
    relaciones = _build_corte_note_relations(
        db,
        sucursal_id=corte.sucursal_id,
        start_dt=corte_window_start,
        end_dt=corte_window_end,
    )
    gastos = (
        db.query(CorteCajaGasto)
        .filter(CorteCajaGasto.corte_id == corte.id)
        .order_by(CorteCajaGasto.created_at.asc())
        .all()
    )
    denominaciones = (
        db.query(CorteCajaDenominacion)
        .filter(CorteCajaDenominacion.corte_id == corte.id)
        .order_by(CorteCajaDenominacion.valor.desc())
        .all()
    )
    gastos_total = sum((Decimal(str(g.monto or 0)) for g in gastos), Decimal("0"))
    saldo_calculado = (
        Decimal(str(corte.saldo_calculado or 0))
        if corte.estado == CorteCajaEstado.cerrado
        else Decimal(str(corte.saldo_inicial or 0)) + cash_data["neto"] + manual_data["neto"] - gastos_total
    )
    report = corte_caja_report_service.build_report(
        corte=corte,
        sucursal=sucursal,
        cash_data=cash_data,
        manual_data=manual_data,
        gastos=gastos,
        denominaciones=denominaciones,
        saldo_calculado=saldo_calculado,
        compras_rows=relaciones["compras_rows"],
        ventas_rows=relaciones["ventas_rows"],
    )

    fmt = (request.query_params.get("format") or "pdf").lower()
    if fmt in ("xlsx", "xls", "excel"):
        content, filename = corte_caja_report_service.build_report_excel(report)
        headers = {"Content-Disposition": f"attachment; filename={filename}"}
        return StreamingResponse(
            io.BytesIO(content),
            media_type="application/vnd.ms-excel",
            headers=headers,
        )
    if fmt == "pdf":
        content, filename = corte_caja_report_service.build_report_pdf(report)
        headers = {"Content-Disposition": f"attachment; filename={filename}"}
        return StreamingResponse(
            io.BytesIO(content),
            media_type="application/pdf",
            headers=headers,
        )
    raise HTTPException(status_code=400, detail="Formato de reporte invalido.")


@router.post("/notas/{nota_id}/pago-detalle")
async def nota_pago_detalle(
    request: Request,
    nota_id: int,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    form = await request.form()
    _raw_redirect = (form.get("redirect_to") or "").strip()
    redirect_to = _raw_redirect if _raw_redirect.startswith("/web/") else "/web/admin/corte-caja"

    nota = db.get(Nota, nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada.")

    metodo = (nota.metodo_pago or "").strip().lower()
    if metodo not in ("cheque", "transferencia"):
        raise HTTPException(status_code=400, detail="Solo se puede editar detalle de pago para cheque o transferencia.")

    if metodo == "cheque":
        numero_cheque_raw = (form.get("numero_cheque") or "").strip()
        nota.numero_cheque = numero_cheque_raw or None

    cuenta_scrap360_id_raw = (form.get("cuenta_scrap360_id") or "").strip()
    if cuenta_scrap360_id_raw:
        try:
            cuenta_scrap360_id = int(cuenta_scrap360_id_raw)
        except ValueError:
            raise HTTPException(status_code=400, detail="ID de cuenta invalido.")
        cuenta = db.get(CuentaScrap360, cuenta_scrap360_id)
        if not cuenta:
            raise HTTPException(status_code=400, detail="Cuenta Scrap360 no encontrada.")
        nota.cuenta_scrap360_id = cuenta_scrap360_id
    else:
        nota.cuenta_scrap360_id = None

    db.commit()
    return RedirectResponse(url=redirect_to, status_code=303)


@router.get("/inventario/movimientos")
async def inventario_movimientos(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
):
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    sucursales = db.query(Sucursal).order_by(Sucursal.nombre).all()
    sucursales = _filter_sucursales_for_admin(sucursales, allowed_suc_ids)
    materiales = db.query(Material).order_by(Material.orden_display, Material.nombre).all()
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
    date_from_raw = (params.get("from") or "").strip()
    date_to_raw = (params.get("to") or "").strip()
    date_from = _parse_inventory_date_param(date_from_raw)
    date_to = _parse_inventory_date_param(date_to_raw)
    date_error = None
    if date_from_raw and date_from is None:
        date_error = "La fecha inicial no es valida."
    elif date_to_raw and date_to is None:
        date_error = "La fecha final no es valida."
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from
    created_from, created_to = _inventory_local_date_range_to_utc(date_from, date_to)

    query = _build_inventario_movimientos_query(
        db,
        allowed_suc_ids=allowed_suc_ids,
        sucursal_id=sucursal_id,
        material_id=material_id,
        tipo=tipo,
        created_from=created_from,
        created_to=created_to,
    )
    movimientos = query.order_by(InventarioMovimiento.created_at.desc()).limit(200).all()
    mov_ids = [mov.id for mov in movimientos]
    ajustes_by_mov_id = {}
    if mov_ids:
        ajustes_by_mov_id = {
            ajuste.inventario_movimiento_id: ajuste
            for ajuste in db.query(InventarioAjusteManual)
            .filter(InventarioAjusteManual.inventario_movimiento_id.in_(mov_ids))
            .all()
        }
    movimiento_note_meta: dict[int, dict] = {}
    movimiento_note_ids = sorted({mov.nota_id for mov in movimientos if mov.nota_id})
    if movimiento_note_ids:
        movimiento_notas = db.query(Nota).filter(Nota.id.in_(movimiento_note_ids)).all()
        movimiento_folio_map = _build_folio_map(movimiento_notas)
        movimiento_prov_ids = {nota.proveedor_id for nota in movimiento_notas if nota.proveedor_id}
        movimiento_cli_ids = {nota.cliente_id for nota in movimiento_notas if nota.cliente_id}
        movimiento_proveedores_map = (
            {
                proveedor.id: proveedor.nombre_completo
                for proveedor in db.query(Proveedor).filter(Proveedor.id.in_(movimiento_prov_ids)).all()
            }
            if movimiento_prov_ids
            else {}
        )
        movimiento_clientes_map = (
            {
                cliente.id: cliente.nombre_completo
                for cliente in db.query(Cliente).filter(Cliente.id.in_(movimiento_cli_ids)).all()
            }
            if movimiento_cli_ids
            else {}
        )
        movimiento_note_meta = {
            nota.id: {
                "folio": movimiento_folio_map.get(nota.id) or f"#{nota.id}",
                "partner": _partner_name_for_nota(nota, movimiento_proveedores_map, movimiento_clientes_map),
            }
            for nota in movimiento_notas
        }
    total_firmado = 0
    for mov in movimientos:
        total_firmado += float(_signed_inventario_qty(mov))

    compra_summary_query = _build_inventario_movimientos_query(
        db,
        allowed_suc_ids=allowed_suc_ids,
        sucursal_id=sucursal_id,
        material_id=material_id,
        tipo="compra",
        created_from=created_from,
        created_to=created_to,
    )
    compra_movimientos = compra_summary_query.order_by(InventarioMovimiento.created_at.desc()).all()
    compra_total_kg = Decimal("0")
    compra_total_monto = Decimal("0")
    compra_rows_map: dict[int, dict] = {}
    compra_material_rows_map: dict[int, dict] = {}
    compra_note_ids: list[int] = []
    compra_nota_material_ids = [
        mov.nota_material_id
        for mov in compra_movimientos
        if mov.nota_material_id
    ]
    compra_nota_material_map: dict[int, NotaMaterial] = {}
    if compra_nota_material_ids:
        compra_nota_material_map = {
            nm.id: nm
            for nm in db.query(NotaMaterial).filter(NotaMaterial.id.in_(compra_nota_material_ids)).all()
        }
    for mov in compra_movimientos:
        qty = _signed_inventario_qty(mov) or Decimal("0")
        compra_total_kg += qty
        subtotal = Decimal("0")
        if mov.nota_material_id and compra_nota_material_map.get(mov.nota_material_id):
            subtotal = Decimal(str(compra_nota_material_map[mov.nota_material_id].subtotal or 0))
            compra_total_monto += subtotal
        material = mov.inventario.material if mov.inventario and mov.inventario.material else None
        if material:
            if material.id not in compra_material_rows_map:
                compra_material_rows_map[material.id] = {
                    "material_id": material.id,
                    "material": material.nombre,
                    "cantidad_kg": Decimal("0"),
                    "monto": Decimal("0"),
                    "nota_ids": set(),
                    "sucursales": set(),
                }
            material_row = compra_material_rows_map[material.id]
            material_row["cantidad_kg"] += qty
            material_row["monto"] += subtotal
            if mov.nota_id:
                material_row["nota_ids"].add(mov.nota_id)
            if mov.inventario.sucursal:
                material_row["sucursales"].add(mov.inventario.sucursal.nombre)
        if mov.nota_id:
            if mov.nota_id not in compra_rows_map:
                compra_rows_map[mov.nota_id] = {
                    "nota_id": mov.nota_id,
                    "cantidad_kg": Decimal("0"),
                    "monto": Decimal("0"),
                    "fecha": mov.created_at,
                    "latest_fecha": mov.created_at,
                    "sucursal": mov.inventario.sucursal.nombre if mov.inventario and mov.inventario.sucursal else "-",
                    "materiales": set(),
                }
                compra_note_ids.append(mov.nota_id)
            row = compra_rows_map[mov.nota_id]
            row["cantidad_kg"] += qty
            row["monto"] += subtotal
            if mov.created_at and (row["fecha"] is None or mov.created_at < row["fecha"]):
                row["fecha"] = mov.created_at
            if mov.created_at and (row["latest_fecha"] is None or mov.created_at > row["latest_fecha"]):
                row["latest_fecha"] = mov.created_at
            if mov.inventario and mov.inventario.material:
                row["materiales"].add(mov.inventario.material.nombre)

    compra_note_map: dict[int, Nota] = {}
    compra_proveedores: dict[int, Proveedor] = {}
    compra_folio_map: dict[int, str] = {}
    if compra_note_ids:
        compra_notas = db.query(Nota).filter(Nota.id.in_(compra_note_ids)).all()
        compra_note_map = {nota.id: nota for nota in compra_notas}
        prov_ids = {nota.proveedor_id for nota in compra_notas if nota.proveedor_id}
        if prov_ids:
            compra_proveedores = {
                proveedor.id: proveedor
                for proveedor in db.query(Proveedor).filter(Proveedor.id.in_(prov_ids)).all()
            }
        compra_folio_map = _build_folio_map(compra_notas)

    compra_notas_rows = []
    for nota_id, row in sorted(
        compra_rows_map.items(),
        key=lambda item: item[1]["latest_fecha"] or datetime.min,
        reverse=True,
    ):
        nota = compra_note_map.get(nota_id)
        proveedor = compra_proveedores.get(nota.proveedor_id) if nota and nota.proveedor_id else None
        compra_notas_rows.append(
            {
                "nota_id": nota_id,
                "folio": compra_folio_map.get(nota_id) or f"#{nota_id}",
                "fecha": row["fecha"],
                "sucursal": row["sucursal"],
                "proveedor": proveedor.nombre_completo if proveedor else "-",
                "cantidad_kg": row["cantidad_kg"],
                "monto": row["monto"],
                "materiales": sorted(row["materiales"]),
            }
        )

    selected_material = next((m for m in materiales if material_id and m.id == material_id), None)
    compra_material_rows = []
    for row in sorted(compra_material_rows_map.values(), key=lambda item: item["material"].lower()):
        cantidad_kg = row["cantidad_kg"]
        monto = row["monto"]
        precio_promedio = (monto / cantidad_kg) if cantidad_kg > Decimal("0") else Decimal("0")
        compra_material_rows.append(
            {
                "material_id": row["material_id"],
                "material": row["material"],
                "cantidad_kg": cantidad_kg,
                "monto": monto,
                "precio_promedio": precio_promedio,
                "notas_count": len(row["nota_ids"]),
                "sucursales": sorted(row["sucursales"]),
            }
        )
    precio_promedio_compra = None
    if selected_material:
        selected_material_row = next(
            (row for row in compra_material_rows if row["material_id"] == selected_material.id),
            None,
        )
        precio_promedio_compra = (
            selected_material_row["precio_promedio"]
            if selected_material_row
            else Decimal("0")
        )
    # Ajuste summary
    ajuste_summary_query = _build_inventario_movimientos_query(
        db,
        allowed_suc_ids=allowed_suc_ids,
        sucursal_id=sucursal_id,
        material_id=material_id,
        tipo="ajuste",
        created_from=created_from,
        created_to=created_to,
    )
    ajuste_movimientos_all = ajuste_summary_query.order_by(InventarioMovimiento.created_at.desc()).all()
    ajuste_total_kg = sum((Decimal(str(m.cantidad_kg or 0)) for m in ajuste_movimientos_all), Decimal("0"))
    ajuste_mov_ids = [m.id for m in ajuste_movimientos_all]
    ajuste_detail_map: dict[int, int] = {}
    if ajuste_mov_ids:
        for aj in db.query(InventarioAjusteManual).filter(
            InventarioAjusteManual.inventario_movimiento_id.in_(ajuste_mov_ids)
        ).all():
            if aj.inventario_movimiento_id:
                ajuste_detail_map[aj.inventario_movimiento_id] = aj.id
    ajuste_rows = [
        {
            "mov_id": mov.id,
            "sucursal": mov.inventario.sucursal.nombre if mov.inventario and mov.inventario.sucursal else "-",
            "material": mov.inventario.material.nombre if mov.inventario and mov.inventario.material else "-",
            "cantidad_kg": Decimal(str(mov.cantidad_kg or 0)),
            "saldo_resultante": mov.saldo_resultante,
            "comentario": mov.comentario,
            "created_at": mov.created_at,
            "ajuste_id": ajuste_detail_map.get(mov.id),
        }
        for mov in ajuste_movimientos_all
    ]

    date_scope_label = "Todo el historial"
    if date_from and date_to:
        date_scope_label = f"{format_date_local(date_from)} al {format_date_local(date_to)}"
    elif date_from:
        date_scope_label = f"Desde {format_date_local(date_from)}"
    elif date_to:
        date_scope_label = f"Hasta {format_date_local(date_to)}"

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
            "date_from": format_date_iso(date_from) if date_from else "",
            "date_to": format_date_iso(date_to) if date_to else "",
            "date_error": date_error,
            "selected_material": selected_material,
            "date_scope_label": date_scope_label,
            "total_firmado": total_firmado,
            "compra_total_kg": compra_total_kg,
            "compra_total_monto": compra_total_monto,
            "compra_total_notas": len(compra_notas_rows),
            "compra_total_movimientos": len(compra_movimientos),
            "compra_notas_rows": compra_notas_rows,
            "compra_material_rows": compra_material_rows,
            "precio_promedio_compra": precio_promedio_compra,
            "ajuste_rows": ajuste_rows,
            "ajuste_total_kg": ajuste_total_kg,
            "ajustes_by_mov_id": ajustes_by_mov_id,
            "movimiento_note_meta": movimiento_note_meta,
            "can_manage_inventory": not _is_read_only_admin_user(current_user),
            "can_export_inventory": not _is_read_only_admin_user(current_user),
        },
    )


@router.get("/inventario/ajustes/{ajuste_id}")
async def inventario_ajuste_detail(
    ajuste_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_viewer_or_admin_or_superadmin),
):
    ajuste = db.get(InventarioAjusteManual, ajuste_id)
    if not ajuste:
        raise HTTPException(status_code=404, detail="Ajuste manual no encontrado.")

    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    if allowed_suc_ids and ajuste.sucursal_id not in allowed_suc_ids:
        raise HTTPException(status_code=403, detail="Sin acceso a esta sucursal.")

    reversion = None
    if not ajuste.reversal_of_id:
        reversion = (
            db.query(InventarioAjusteManual)
            .filter(InventarioAjusteManual.reversal_of_id == ajuste.id)
            .first()
        )
    original = ajuste.reversal_of if ajuste.reversal_of_id else ajuste

    return templates.TemplateResponse(
        "admin/inventario_ajuste_detail.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "ajuste": ajuste,
            "original": original,
            "reversion": reversion,
            "is_reversal": bool(ajuste.reversal_of_id),
            "error": request.query_params.get("error") or None,
            "reverted_ok": request.query_params.get("revertida") == "1",
            "can_manage_inventory_adjustment": not _is_read_only_admin_user(current_user),
        },
    )


@router.post("/inventario/ajustes/{ajuste_id}/revertir")
async def inventario_ajuste_reverse(
    ajuste_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    ajuste = db.get(InventarioAjusteManual, ajuste_id)
    if not ajuste:
        raise HTTPException(status_code=404, detail="Ajuste manual no encontrado.")

    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)
    if allowed_suc_ids and ajuste.sucursal_id not in allowed_suc_ids:
        raise HTTPException(status_code=403, detail="Sin acceso a esta sucursal.")

    try:
        note_service.reverse_manual_inventory_adjustment(
            db,
            ajuste,
            usuario_id=current_user.get("id"),
            comentario=f"Reversion ajuste manual #{ajuste.id}",
        )
    except ValueError as exc:
        return RedirectResponse(
            url=f"/web/admin/inventario/ajustes/{ajuste_id}?{urlencode({'error': str(exc)})}",
            status_code=303,
        )

    reversion = (
        db.query(InventarioAjusteManual)
        .filter(InventarioAjusteManual.reversal_of_id == ajuste.id)
        .order_by(InventarioAjusteManual.created_at.desc())
        .first()
    )
    target_id = reversion.id if reversion else ajuste.id
    return RedirectResponse(
        url=f"/web/admin/inventario/ajustes/{target_id}?revertida=1",
        status_code=303,
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
    materiales = db.query(Material).order_by(Material.orden_display, Material.nombre).all()
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
    date_from = _parse_inventory_date_param((params.get("from") or "").strip())
    date_to = _parse_inventory_date_param((params.get("to") or "").strip())
    if date_from and date_to and date_from > date_to:
        date_from, date_to = date_to, date_from
    created_from, created_to = _inventory_local_date_range_to_utc(date_from, date_to)
    fmt = params.get("format") or "csv"

    query = _build_inventario_movimientos_query(
        db,
        allowed_suc_ids=allowed_suc_ids,
        sucursal_id=sucursal_id,
        material_id=material_id,
        tipo=tipo,
        created_from=created_from,
        created_to=created_to,
    )
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
                format_datetime_local(mov.created_at) if mov.created_at else "",
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
            format_datetime_local(mov.created_at) if mov.created_at else "",
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
            format_datetime_local(mov.created_at) if mov.created_at else "",
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


# ---------- REPORTE DE ASISTENCIAS ----------


@router.get("/reporte-asistencias")
async def reporte_asistencias(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    params = request.query_params
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)

    sucursal_id = None
    if params.get("sucursal_id"):
        try:
            sucursal_id = int(params["sucursal_id"])
        except ValueError:
            pass

    tz = get_app_timezone()
    today = date.today()

    fecha_inicio_raw = (params.get("fecha_inicio") or "").strip()
    fecha_fin_raw = (params.get("fecha_fin") or "").strip()

    try:
        fecha_inicio = datetime.strptime(fecha_inicio_raw, "%Y-%m-%d").date() if fecha_inicio_raw else today.replace(day=1)
    except ValueError:
        fecha_inicio = today.replace(day=1)

    try:
        fecha_fin = datetime.strptime(fecha_fin_raw, "%Y-%m-%d").date() if fecha_fin_raw else today
    except ValueError:
        fecha_fin = today

    start_local = datetime.combine(fecha_inicio, time.min).replace(tzinfo=tz)
    end_local = datetime.combine(fecha_fin + timedelta(days=1), time.min).replace(tzinfo=tz)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)
    end_utc = end_local.astimezone(timezone.utc).replace(tzinfo=None)

    q = db.query(Nota).filter(
        Nota.estado == NotaEstado.aprobada,
        Nota.proveedor_id.isnot(None),
        Nota.created_at >= start_utc,
        Nota.created_at < end_utc,
    )
    if sucursal_id is not None:
        q = q.filter(Nota.sucursal_id == sucursal_id)
    if allowed_suc_ids is not None:
        q = q.filter(Nota.sucursal_id.in_(allowed_suc_ids))

    notas = q.order_by(Nota.created_at.asc()).all()

    prov_ids = {n.proveedor_id for n in notas}
    proveedores_map: dict[int, str] = (
        {p.id: p.nombre_completo for p in db.query(Proveedor).filter(Proveedor.id.in_(prov_ids)).all()}
        if prov_ids
        else {}
    )
    folio_map = _build_folio_map(notas)

    rows: list[dict] = []
    for nota in notas:
        prov_name = proveedores_map.get(nota.proveedor_id) or f"Proveedor #{nota.proveedor_id}"
        fecha_local = to_local_datetime(nota.created_at) if nota.created_at else None
        rows.append({
            "proveedor_id": nota.proveedor_id,
            "proveedor_nombre": prov_name,
            "fecha": fecha_local,
            "folio": folio_map.get(nota.id) or f"#{nota.id}",
            "nota_id": nota.id,
        })

    rows.sort(key=lambda r: (r["proveedor_nombre"].lower(), r["fecha"] or datetime.min))

    summary_map: dict[int, dict] = {}
    for row in rows:
        pid = row["proveedor_id"]
        if pid not in summary_map:
            summary_map[pid] = {"nombre": row["proveedor_nombre"], "total": 0}
        summary_map[pid]["total"] += 1

    summary = sorted(summary_map.values(), key=lambda s: (-s["total"], s["nombre"].lower()))

    sucursales = _filter_sucursales_for_admin(
        db.query(Sucursal).order_by(Sucursal.nombre).all(),
        allowed_suc_ids,
    )

    return templates.TemplateResponse(
        "admin/reporte_asistencias.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "sucursales": sucursales,
            "sucursal_id": sucursal_id,
            "fecha_inicio": fecha_inicio.isoformat(),
            "fecha_fin": fecha_fin.isoformat(),
            "rows": rows,
            "summary": summary,
            "total_asistencias": len(rows),
            "total_proveedores": len(summary_map),
            "searched": bool(fecha_inicio_raw or fecha_fin_raw or params.get("sucursal_id")),
        },
    )


# ---------- REPORTE DE SALDOS ----------


@router.get("/reporte-saldos")
async def reporte_saldos(
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(require_admin_or_superadmin),
):
    params = request.query_params
    allowed_suc_ids = _get_allowed_sucursal_ids(db, current_user)

    sucursal_id = None
    if params.get("sucursal_id"):
        try:
            sucursal_id = int(params["sucursal_id"])
        except ValueError:
            pass

    sucursales = _filter_sucursales_for_admin(
        db.query(Sucursal).order_by(Sucursal.nombre).all(),
        allowed_suc_ids,
    )

    # ── PROVEEDORES ──────────────────────────────────────────────────────────
    proveedores_q = _apply_sucursal_filter(
        db.query(Proveedor), allowed_suc_ids, sucursal_id, Proveedor.sucursal_id
    ).order_by(Proveedor.nombre_completo).all()

    proveedores_rows: list[dict] = []
    for proveedor in proveedores_q:
        if _is_internal_partner_name(db, proveedor.nombre_completo):
            continue
        # Punto 8 (fase 2): el par vinculado vive en la tabla de clientes con
        # su saldo neto y su signo. Emitirlo también aquí lo contaba doble en
        # los totales globales y lo mostraba "como proveedor", que es lo que
        # la clienta rechaza.
        if _get_formally_linked_cliente(db, proveedor):
            continue
        bundle = _collect_proveedor_sales_bundle(
            db, proveedor=proveedor, allowed_suc_ids=allowed_suc_ids, sucursal_id=sucursal_id
        )
        compras_q = _apply_sucursal_filter(
            db.query(Nota).filter(
                Nota.proveedor_id == proveedor.id,
                Nota.tipo_operacion == TipoOperacion.compra,
            ),
            allowed_suc_ids, sucursal_id, Nota.sucursal_id,
        ).order_by(Nota.created_at.desc()).all()
        ventas = bundle["ventas"]
        linked_cliente = bundle["linked_cliente"]

        note_adj = _get_note_balance_adjustment_totals_map(
            db, [n.id for n in (compras_q + ventas) if n.id]
        )
        ajustes_p = _get_partner_adjustments_total(
            db, partner_type="proveedor", partner_id=proveedor.id,
            allowed_suc_ids=allowed_suc_ids, sucursal_id=sucursal_id,
        )
        ajustes_c = Decimal("0")
        if linked_cliente:
            ajustes_c = _get_partner_adjustments_total(
                db, partner_type="cliente", partner_id=linked_cliente.id,
                allowed_suc_ids=allowed_suc_ids, sucursal_id=sucursal_id,
            )

        unified = _aggregate_unified_partner_summary(
            compras=compras_q, ventas=ventas,
            ajustes_proveedor=ajustes_p, ajustes_cliente=ajustes_c,
            note_adjustment_totals=note_adj,
        )
        saldo_neto = Decimal(str(unified["saldo_neto"] or 0))
        if abs(saldo_neto) < Decimal("0.01"):
            continue
        proveedores_rows.append({
            "nombre": proveedor.nombre_completo,
            "partner_url": f"/web/admin/proveedores/{proveedor.id}/record",
            "saldo_neto": saldo_neto,
            "a_pagar": saldo_neto if saldo_neto > 0 else Decimal("0"),
            "a_cobrar": -saldo_neto if saldo_neto < 0 else Decimal("0"),
        })

    # ── CLIENTES ──────────────────────────────────────────────────────────────
    clientes_q = _apply_sucursal_filter(
        db.query(Cliente), allowed_suc_ids, sucursal_id, Cliente.sucursal_id
    ).order_by(Cliente.nombre_completo).all()

    clientes_rows: list[dict] = []
    for cliente in clientes_q:
        if _is_internal_partner_name(db, cliente.nombre_completo):
            continue
        linked_proveedor = _get_formally_linked_proveedor(db, cliente)
        if linked_proveedor and _is_internal_partner_name(db, linked_proveedor.nombre_completo):
            linked_proveedor = None

        ventas_q = _apply_sucursal_filter(
            db.query(Nota).filter(
                Nota.cliente_id == cliente.id,
                Nota.tipo_operacion == TipoOperacion.venta,
            ),
            allowed_suc_ids, sucursal_id, Nota.sucursal_id,
        ).order_by(Nota.created_at.desc()).all()

        compras_cli: list[Nota] = []
        if linked_proveedor:
            compras_cli = _apply_sucursal_filter(
                db.query(Nota).filter(
                    Nota.proveedor_id == linked_proveedor.id,
                    Nota.tipo_operacion == TipoOperacion.compra,
                ),
                allowed_suc_ids, sucursal_id, Nota.sucursal_id,
            ).order_by(Nota.created_at.desc()).all()

        note_adj = _get_note_balance_adjustment_totals_map(
            db, [n.id for n in (compras_cli + ventas_q) if n.id]
        )
        ajustes_c = _get_partner_adjustments_total(
            db, partner_type="cliente", partner_id=cliente.id,
            allowed_suc_ids=allowed_suc_ids, sucursal_id=sucursal_id,
        )
        ajustes_p = Decimal("0")
        if linked_proveedor:
            ajustes_p = _get_partner_adjustments_total(
                db, partner_type="proveedor", partner_id=linked_proveedor.id,
                allowed_suc_ids=allowed_suc_ids, sucursal_id=sucursal_id,
            )

        unified = _aggregate_unified_partner_summary(
            compras=compras_cli, ventas=ventas_q,
            ajustes_proveedor=ajustes_p, ajustes_cliente=ajustes_c,
            note_adjustment_totals=note_adj,
        )
        saldo_neto = Decimal(str(unified["saldo_neto"] or 0))
        if abs(saldo_neto) < Decimal("0.01"):
            continue
        clientes_rows.append({
            "nombre": cliente.nombre_completo,
            "partner_url": f"/web/admin/clientes/{cliente.id}/record",
            "saldo_neto": saldo_neto,
            "a_pagar": saldo_neto if saldo_neto > 0 else Decimal("0"),
            "a_cobrar": -saldo_neto if saldo_neto < 0 else Decimal("0"),
        })

    # ── TOTALS ───────────────────────────────────────────────────────────────
    prov_total_a_pagar = sum((r["a_pagar"] for r in proveedores_rows), Decimal("0"))
    prov_total_a_cobrar = sum((r["a_cobrar"] for r in proveedores_rows), Decimal("0"))
    cli_total_a_cobrar = sum((r["a_cobrar"] for r in clientes_rows), Decimal("0"))
    cli_total_a_pagar = sum((r["a_pagar"] for r in clientes_rows), Decimal("0"))

    global_a_pagar = prov_total_a_pagar + cli_total_a_pagar
    global_a_cobrar = prov_total_a_cobrar + cli_total_a_cobrar
    global_neto = global_a_cobrar - global_a_pagar  # positive = we collect more than we owe

    # Punto 11 (fase 2): además del orden por cantidad, orden alfabético para
    # localizar a un socio por nombre. El sort vive aquí (no en Jinja) y los
    # acentos no alteran el orden (Álvarez junto a Alvarez).
    def _sort_alfabetico(row: dict) -> str:
        nombre = row.get("nombre") or ""
        return unicodedata.normalize("NFKD", nombre).encode("ascii", "ignore").decode().lower()

    orden_saldos_raw = (request.query_params.get("orden") or "").strip().lower()
    orden_saldos = "alfabetico" if orden_saldos_raw == "alfabetico" else "cantidad"
    if orden_saldos == "alfabetico":
        proveedores_rows.sort(key=_sort_alfabetico)
        clientes_rows.sort(key=_sort_alfabetico)
    else:
        proveedores_rows.sort(key=lambda r: r["a_pagar"], reverse=True)
        clientes_rows.sort(key=lambda r: r["a_cobrar"], reverse=True)
    orden_saldos_params = {
        key: value
        for key, value in request.query_params.items()
        if key != "orden" and value
    }
    orden_saldos_links = {
        "cantidad": _append_query_params("/web/admin/reporte-saldos", **orden_saldos_params),
        "alfabetico": _append_query_params(
            "/web/admin/reporte-saldos", **orden_saldos_params, orden="alfabetico"
        ),
    }

    return templates.TemplateResponse(
        "admin/reporte_saldos.html",
        {
            "request": request,
            "env": settings.ENV,
            "user": current_user,
            "sucursales": sucursales,
            "sucursal_id": sucursal_id,
            "proveedores_rows": proveedores_rows,
            "clientes_rows": clientes_rows,
            "orden_saldos": orden_saldos,
            "orden_saldos_links": orden_saldos_links,
            "prov_total_a_pagar": prov_total_a_pagar,
            "prov_total_a_cobrar": prov_total_a_cobrar,
            "cli_total_a_cobrar": cli_total_a_cobrar,
            "cli_total_a_pagar": cli_total_a_pagar,
            "global_a_pagar": global_a_pagar,
            "global_a_cobrar": global_a_cobrar,
            "global_neto": global_neto,
        },
    )

