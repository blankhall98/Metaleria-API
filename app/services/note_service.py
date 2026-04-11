# app/services/note_service.py
from datetime import date, datetime
import json
from decimal import Decimal
from typing import Iterable, Sequence

from sqlalchemy.orm import Session
from sqlalchemy import func, or_

from app.models import (
    Nota,
    NotaEstado,
    NotaMaterial,
    Subpesaje,
    Material,
    TipoOperacion,
    TablaPrecio,
    TipoCliente,
    Inventario,
    InventarioMovimiento,
    MovimientoContable,
    NotaPago,
    NotaAjusteSaldo,
    NotaOriginal,
    NotaEvidenciaExtra,
    Cuenta,
    CuentaScrap360,
    CuentaScrap360Movimiento,
    User,
    UserRole,
    Proveedor,
    Cliente,
    Sucursal,
    NotaDevolucionParcial,
    NotaDevolucionParcialLinea,
    NotaDevolucionParcialAplicacion,
    NotaDevolucionTotal,
    InventarioAjusteManual,
)
from app.core.datetime_utils import format_datetime_local


def _sum_decimal(values: Iterable[Decimal | float | int]) -> Decimal:
    total = Decimal("0")
    for v in values:
        total += Decimal(str(v or 0))
    return total


def _is_compra_like(tipo_operacion: TipoOperacion | None) -> bool:
    return tipo_operacion == TipoOperacion.compra


def _uses_bank_payment_method(metodo_pago: str | None) -> bool:
    metodo = (metodo_pago or "").strip().lower()
    return metodo in ("transferencia", "cheque")


def _uses_cash_payment_method(metodo_pago: str | None) -> bool:
    metodo = (metodo_pago or "").strip().lower()
    return metodo == "efectivo"


def _inventory_qty(nm: NotaMaterial) -> Decimal:
    real_qty = getattr(nm, "kg_real", None)
    if real_qty is None:
        return Decimal(str(nm.kg_neto or 0))
    return Decimal(str(real_qty or 0))


def _ensure_inventory_qty(nm: NotaMaterial) -> Decimal:
    qty = _inventory_qty(nm)
    if getattr(nm, "kg_real", None) is None:
        nm.kg_real = qty
    return qty


def _normalize_kg_real(value: Decimal | float | int | str | None) -> Decimal:
    qty = Decimal(str(value or 0))
    if qty < Decimal("0"):
        raise ValueError("Los kilos reales no pueden ser negativos.")
    return qty.quantize(Decimal("0.001"))


def _apply_real_kg_overrides(
    nota: Nota,
    kg_real_override_map: dict[int, Decimal] | None = None,
) -> None:
    override_map = kg_real_override_map or {}
    for nm in nota.materiales:
        if nm.id in override_map:
            nm.kg_real = _normalize_kg_real(override_map[nm.id])
        else:
            nm.kg_real = Decimal(str(nm.kg_neto or 0))


def _partial_return_contable_amount(nota: Nota, monto_devolucion: Decimal, *, reverse: bool = False) -> Decimal:
    amount = Decimal(str(monto_devolucion or 0))
    signed = amount if _is_compra_like(nota.tipo_operacion) else -amount
    return -signed if reverse else signed


def _nota_partner_key(nota: Nota) -> tuple[str | None, int | None]:
    if nota.proveedor_id:
        return "proveedor", nota.proveedor_id
    if nota.cliente_id:
        return "cliente", nota.cliente_id
    return None, None


def get_inventory_sucursal_id(nota: Nota) -> int | None:
    if getattr(nota, "inventario_sucursal_id", None):
        return int(nota.inventario_sucursal_id)
    if nota.sucursal_id:
        return int(nota.sucursal_id)
    return None


def _recalc_material(nm: NotaMaterial) -> None:
    """Recalcula kg_bruto/neto/desc a partir de subpesajes si existen."""
    if nm.subpesajes:
        bruto_sum = _sum_decimal(sp.peso_kg for sp in nm.subpesajes)
        desc_sum = _sum_decimal(getattr(sp, "descuento_kg", 0) for sp in nm.subpesajes)
        neto_sum = bruto_sum - desc_sum
        nm.kg_neto = neto_sum
        nm.kg_descuento = desc_sum
        nm.kg_bruto = bruto_sum
    else:
        kg_desc = Decimal(str(nm.kg_descuento or 0))
        nm.kg_neto = Decimal(str(nm.kg_bruto or 0)) - kg_desc
    _ensure_inventory_qty(nm)


def _recalc_totals(nota: Nota) -> None:
    """
    Recalcula totales de kg y monto de la nota a partir de sus materiales.
    No persiste ni flushea; el caller decide el momento.
    """
    for m in nota.materiales:
        _recalc_material(m)

    total_bruto = _sum_decimal(m.kg_bruto for m in nota.materiales)
    total_desc = _sum_decimal(m.kg_descuento for m in nota.materiales)
    total_neto = _sum_decimal(m.kg_neto for m in nota.materiales)
    total_real = _sum_decimal(_inventory_qty(m) for m in nota.materiales)
    base_total = _sum_decimal(m.subtotal for m in nota.materiales if m.subtotal is not None)

    nota.total_kg_bruto = total_bruto
    nota.total_kg_descuento = total_desc
    nota.total_kg_neto = total_neto
    nota.total_kg_real = total_real

    iva_incluido = bool(nota.iva_incluido)
    iva_pct_raw = nota.iva_porcentaje if nota.iva_porcentaje is not None else Decimal("16.00")
    iva_pct = Decimal(str(iva_pct_raw or 0))
    if iva_pct < 0:
        iva_pct = Decimal("0")
    iva_monto = Decimal("0")
    if iva_incluido and iva_pct > 0 and base_total > 0:
        iva_monto = (base_total * iva_pct) / Decimal("100")

    nota.iva_incluido = iva_incluido
    nota.iva_porcentaje = iva_pct
    nota.iva_monto = iva_monto
    nota.total_monto = base_total + iva_monto if iva_incluido else base_total


def _as_str(value: object) -> str | None:
    if value is None:
        return None
    return str(value)


def _build_nota_snapshot(nota: Nota) -> dict:
    materials = []
    for nm in nota.materiales:
        subs = []
        for sp in nm.subpesajes or []:
            subs.append(
                {
                    "id": sp.id,
                    "peso_kg": _as_str(sp.peso_kg),
                    "descuento_kg": _as_str(getattr(sp, "descuento_kg", 0)),
                    "foto_url": sp.foto_url,
                    "created_at": sp.created_at.isoformat() if sp.created_at else None,
                }
            )
        materials.append(
            {
                "id": nm.id,
                "material_id": nm.material_id,
                "tipo_cliente": nm.tipo_cliente.value if nm.tipo_cliente else None,
                "kg_bruto": _as_str(nm.kg_bruto),
                "kg_descuento": _as_str(nm.kg_descuento),
                "kg_neto": _as_str(nm.kg_neto),
                "kg_real": _as_str(getattr(nm, "kg_real", None)),
                "precio_unitario": _as_str(nm.precio_unitario),
                "subtotal": _as_str(nm.subtotal),
                "orden": nm.orden,
                "evidencia_url": nm.evidencia_url,
                "subpesajes": subs,
            }
        )
    extra_evidencias = []
    for ev in nota.evidencias_extra or []:
        extra_evidencias.append(
            {
                "id": ev.id,
                "url": ev.url,
                "uploaded_by_id": ev.uploaded_by_id,
                "created_at": ev.created_at.isoformat() if ev.created_at else None,
            }
        )
    return {
        "nota_id": nota.id,
        "sucursal_id": nota.sucursal_id,
        "inventario_sucursal_id": get_inventory_sucursal_id(nota),
        "trabajador_id": nota.trabajador_id,
        "proveedor_id": nota.proveedor_id,
        "cliente_id": nota.cliente_id,
        "tipo_operacion": nota.tipo_operacion.value if nota.tipo_operacion else None,
        "estado": nota.estado.value if nota.estado else None,
        "comentarios_trabajador": nota.comentarios_trabajador,
        "totales": {
            "kg_bruto": _as_str(nota.total_kg_bruto),
            "kg_descuento": _as_str(nota.total_kg_descuento),
            "kg_neto": _as_str(nota.total_kg_neto),
            "kg_real": _as_str(getattr(nota, "total_kg_real", None)),
            "monto": _as_str(nota.total_monto),
            "iva_incluido": bool(nota.iva_incluido),
            "iva_porcentaje": _as_str(nota.iva_porcentaje),
            "iva_monto": _as_str(nota.iva_monto),
        },
        "materiales": materials,
        "evidencias_extra": extra_evidencias,
        "created_at": nota.created_at.isoformat() if nota.created_at else None,
    }


def _store_nota_snapshot(db: Session, nota: Nota) -> None:
    payload = json.dumps(_build_nota_snapshot(nota), ensure_ascii=True)
    if nota.original:
        nota.original.payload_json = payload
        nota.original.created_at = datetime.utcnow()
        db.add(nota.original)
    else:
        db.add(NotaOriginal(nota_id=nota.id, payload_json=payload))


def _has_base_contable_movement(db: Session, nota_id: int, tipo: str) -> bool:
    existing = (
        db.query(MovimientoContable)
        .filter(MovimientoContable.nota_id == nota_id, MovimientoContable.tipo == tipo)
        .first()
    )
    return existing is not None


def _normalize_tipo_operacion(
    tipo_operacion: TipoOperacion | str | None,
) -> TipoOperacion | None:
    if isinstance(tipo_operacion, TipoOperacion):
        return tipo_operacion
    if not tipo_operacion:
        return None
    try:
        return TipoOperacion(str(tipo_operacion))
    except ValueError:
        return None


def format_folio(
    *,
    sucursal_id: int | None,
    tipo_operacion: TipoOperacion | str | None,
    folio_seq: int | None,
) -> str | None:
    tipo_norm = _normalize_tipo_operacion(tipo_operacion)
    if not sucursal_id or not tipo_norm or not folio_seq:
        return None
    if tipo_norm == TipoOperacion.compra:
        letra = "C"
    elif tipo_norm == TipoOperacion.venta:
        letra = "V"
    else:
        return None
    return f"{str(int(sucursal_id)).zfill(2)}_{letra}_{int(folio_seq)}"


def _next_folio_seq(
    db: Session,
    *,
    sucursal_id: int,
    tipo_operacion: TipoOperacion,
) -> int:
    max_seq = (
        db.query(func.max(Nota.folio_seq))
        .filter(
            Nota.sucursal_id == sucursal_id,
            Nota.tipo_operacion == tipo_operacion,
        )
        .scalar()
    )
    if not max_seq:
        return 1
    return int(max_seq) + 1


def _normalize_pago_incremental(
    db: Session,
    nota: Nota,
    monto_pagado: Decimal | None,
) -> Decimal:
    pagado = Decimal(str(monto_pagado or 0))
    if pagado <= Decimal("0"):
        raise ValueError("El monto del pago debe ser mayor a 0.")
    snapshot = _effective_note_balance_snapshot(db, nota)
    saldo = Decimal(str(snapshot["saldo_pendiente"]))
    if saldo < Decimal("0"):
        saldo = Decimal("0")
    if pagado > saldo:
        raise ValueError("El pago excede el saldo pendiente de la nota.")
    return pagado


def _get_note_balance_adjustment_total(
    db: Session,
    *,
    nota_id: int,
) -> Decimal:
    total = (
        db.query(func.coalesce(func.sum(NotaAjusteSaldo.monto_delta), 0))
        .filter(NotaAjusteSaldo.nota_id == nota_id)
        .scalar()
    )
    return Decimal(str(total or 0))


def _effective_note_balance_snapshot(
    db: Session,
    nota: Nota,
) -> dict[str, Decimal]:
    total = Decimal(str(nota.total_monto or 0))
    pagado = Decimal(str(nota.monto_pagado or 0))
    ajuste_delta = _get_note_balance_adjustment_total(db, nota_id=nota.id)
    saldo = total - pagado + ajuste_delta
    saldo_pendiente = saldo if saldo > Decimal("0") else Decimal("0")
    saldo_favor = -saldo if saldo < Decimal("0") else Decimal("0")
    return {
        "total": total,
        "pagado": pagado,
        "ajuste_delta": ajuste_delta,
        "saldo": saldo,
        "saldo_pendiente": saldo_pendiente,
        "saldo_favor": saldo_favor,
        "total_efectivo": total + ajuste_delta,
    }


def _parse_cuenta_id(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _parse_cuenta_scrap360_id(raw: str | None) -> int | None:
    if not raw:
        return None
    try:
        return int(str(raw).strip())
    except (TypeError, ValueError):
        return None


def _resolve_cuenta_id(db: Session, nota: Nota, raw: str | None) -> int | None:
    cuenta_id = _parse_cuenta_id(raw)
    if cuenta_id is not None:
        return cuenta_id
    if not raw:
        return None
    raw_clean = str(raw).strip()
    if not raw_clean:
        return None
    conds = []
    if nota.sucursal_id:
        conds.append(Cuenta.sucursal_id == nota.sucursal_id)
    partner_kind, partner_id = _nota_partner_key(nota)
    if partner_kind == "proveedor" and partner_id:
        conds.append(Cuenta.proveedor_id == partner_id)
    elif partner_kind == "cliente" and partner_id:
        conds.append(Cuenta.cliente_id == partner_id)
    if not conds:
        return None
    allowed = (
        db.query(Cuenta)
        .filter(Cuenta.activo.is_(True), or_(*conds))
        .all()
    )
    matches = [
        c
        for c in allowed
        if raw_clean in (c.display_label, c.nombre or "", c.referencia or "")
    ]
    if len(matches) == 1:
        return matches[0].id
    return None


def _resolve_nota_sucursal(
    db: Session,
    *,
    sucursal_id: int | None,
    trabajador_id: int,
) -> int:
    if not trabajador_id:
        raise ValueError("Trabajador invalido.")
    usuario = db.get(User, trabajador_id)
    if not usuario:
        raise ValueError("Trabajador no encontrado.")
    if usuario.rol == UserRole.trabajador:
        if not usuario.sucursal_id:
            raise ValueError("El trabajador no tiene una sucursal asignada.")
        if sucursal_id and int(sucursal_id) != usuario.sucursal_id:
            raise ValueError("La sucursal no coincide con la asignada al trabajador.")
        return int(usuario.sucursal_id)
    if not sucursal_id:
        raise ValueError("Debes indicar la sucursal de la nota.")
    return int(sucursal_id)


def _validate_partner_for_nota_sucursal(
    db: Session,
    *,
    tipo_operacion: TipoOperacion,
    sucursal_id: int,
    proveedor_id: int | None,
    cliente_id: int | None,
) -> None:
    if tipo_operacion == TipoOperacion.compra:
        if cliente_id:
            raise ValueError("Una compra no puede vincularse a un cliente.")
        if not proveedor_id:
            return
        proveedor = db.get(Proveedor, proveedor_id)
        if not proveedor:
            raise ValueError("Proveedor no encontrado.")
        if not proveedor.activo:
            raise ValueError("Proveedor invalido.")
        if proveedor.sucursal_id != sucursal_id:
            raise ValueError("El proveedor no pertenece a la sucursal de la nota.")
        return

    if tipo_operacion == TipoOperacion.venta:
        if proveedor_id and cliente_id:
            raise ValueError("Selecciona solo una contraparte para la venta.")
        if not proveedor_id and not cliente_id:
            raise ValueError("Debes seleccionar un cliente o proveedor para la venta.")
        if proveedor_id:
            proveedor = db.get(Proveedor, proveedor_id)
            if not proveedor:
                raise ValueError("Proveedor no encontrado.")
            if not proveedor.activo:
                raise ValueError("Proveedor invalido.")
            if proveedor.sucursal_id != sucursal_id:
                raise ValueError("El proveedor no pertenece a la sucursal de la nota.")
            if not bool(getattr(proveedor, "permite_ventas", False)):
                raise ValueError("Este proveedor no esta habilitado para ventas directas.")
            return
        if cliente_id:
            cliente = db.get(Cliente, cliente_id)
            if not cliente:
                raise ValueError("Cliente no encontrado.")
            if not cliente.activo:
                raise ValueError("Cliente invalido.")
            if cliente.sucursal_id != sucursal_id:
                raise ValueError("El cliente no pertenece a la sucursal de la nota.")
            return


def _validate_cuenta_for_nota(db: Session, nota: Nota, cuenta_id: int) -> Cuenta:
    cuenta = db.get(Cuenta, cuenta_id)
    if not cuenta or not cuenta.activo:
        raise ValueError("La cuenta seleccionada no existe o esta inactiva.")
    if cuenta.sucursal_id and cuenta.sucursal_id == nota.sucursal_id:
        return cuenta
    partner_kind, partner_id = _nota_partner_key(nota)
    if partner_kind == "proveedor" and cuenta.proveedor_id == partner_id:
        return cuenta
    elif partner_kind == "cliente" and cuenta.cliente_id == partner_id:
        return cuenta
    raise ValueError("La cuenta seleccionada no esta vinculada a esta nota.")



def _validate_cuenta_scrap360_for_nota(
    db: Session,
    nota: Nota,
    cuenta_id: int | None,
    metodo_pago: str | None,
) -> CuentaScrap360 | None:
    if cuenta_id is None:
        return None
    cuenta = db.get(CuentaScrap360, cuenta_id)
    if not cuenta or not cuenta.activo:
        raise ValueError("La cuenta Scrap360 seleccionada no existe o esta inactiva.")
    if cuenta.sucursales and nota.sucursal_id:
        allowed = {s.id for s in cuenta.sucursales}
        if nota.sucursal_id not in allowed:
            raise ValueError("La cuenta Scrap360 no esta vinculada a esta sucursal.")
    if metodo_pago:
        metodo = metodo_pago.strip().lower()
        tipo = (cuenta.tipo or "").strip().lower()
        if _uses_cash_payment_method(metodo):
            if tipo and tipo != "efectivo":
                raise ValueError("La cuenta Scrap360 no coincide con el metodo de pago.")
        elif _uses_bank_payment_method(metodo):
            if tipo and tipo not in {"transferencia", "cheques"}:
                raise ValueError("La cuenta Scrap360 debe ser bancaria para transferencias o cheques.")
        elif tipo and tipo == "efectivo":
            raise ValueError("La cuenta Scrap360 no coincide con el metodo de pago.")
    return cuenta


def _registrar_movimiento_cuenta_scrap360(
    db: Session,
    *,
    cuenta: CuentaScrap360,
    nota: Nota | None,
    pago_id: int | None,
    usuario_id: int | None,
    tipo: str,
    monto: Decimal,
    comentario: str | None,
) -> CuentaScrap360Movimiento:
    monto_abs = abs(Decimal(str(monto or 0)))
    saldo_actual = Decimal(str(cuenta.saldo_actual or 0))
    if tipo == "ingreso":
        nuevo_saldo = saldo_actual + monto_abs
    elif tipo == "egreso":
        nuevo_saldo = saldo_actual - monto_abs
    elif tipo == "ajuste":
        nuevo_saldo = saldo_actual + Decimal(str(monto or 0))
    else:
        raise ValueError("Tipo de movimiento Scrap360 invalido.")
    cuenta.saldo_actual = nuevo_saldo
    cuenta.updated_at = datetime.utcnow()
    mov = CuentaScrap360Movimiento(
        cuenta_id=cuenta.id,
        nota_id=nota.id if nota else None,
        nota_pago_id=pago_id,
        usuario_id=usuario_id,
        tipo=tipo,
        monto=monto_abs if tipo != "ajuste" else Decimal(str(monto or 0)),
        saldo_resultante=nuevo_saldo,
        comentario=comentario or None,
    )
    db.add(cuenta)
    db.add(mov)
    return mov


def apply_prices(
    db: Session,
    nota: Nota,
) -> None:
    """
    Asigna precio_unitario/subtotal a cada material según la tabla de precios activa
    para la combinación (material, tipo_operacion, tipo_cliente).
    """
    for nm in nota.materiales:
        tipo_cli = nm.tipo_cliente or TipoCliente.regular
        tp = (
            db.query(TablaPrecio)
            .filter(
                TablaPrecio.material_id == nm.material_id,
                TablaPrecio.tipo_operacion == nota.tipo_operacion,
                TablaPrecio.tipo_cliente == tipo_cli,
                TablaPrecio.activo.is_(True),
            )
            .order_by(TablaPrecio.version.desc())
            .first()
        )
        if tp:
            nm.precio_unitario = tp.precio_por_unidad
            nm.version_precio_id = tp.id
            nm.subtotal = Decimal(str(tp.precio_por_unidad)) * Decimal(str(nm.kg_neto or 0))
        else:
            nm.precio_unitario = None
            nm.version_precio_id = None
            nm.subtotal = None
        db.add(nm)
    _recalc_totals(nota)


def _apply_precio_overrides(
    nota: Nota,
    precio_override_map: dict[int, dict[str, Decimal]] | None,
) -> None:
    if not precio_override_map:
        return
    for nm in nota.materiales:
        payload = precio_override_map.get(nm.id) if nm.id else None
        if not payload:
            continue
        unit_raw = payload.get("precio_unitario")
        subtotal_raw = payload.get("subtotal")
        if unit_raw is None and subtotal_raw is None:
            continue
        kg_neto = Decimal(str(nm.kg_neto or 0))
        if unit_raw is not None:
            unit_val = Decimal(str(unit_raw))
            if unit_val < 0:
                raise ValueError("El precio unitario no puede ser negativo.")
            subtotal_val = unit_val * kg_neto
        else:
            subtotal_val = Decimal(str(subtotal_raw))
            if subtotal_val < 0:
                raise ValueError("El subtotal no puede ser negativo.")
            if kg_neto <= 0:
                if subtotal_val != 0:
                    raise ValueError("No se puede calcular el precio unitario sin kg neto.")
                unit_val = Decimal("0")
            else:
                unit_val = subtotal_val / kg_neto
        nm.precio_unitario = unit_val
        nm.subtotal = subtotal_val
        nm.version_precio_id = None


def create_draft_note(
    db: Session,
    *,
    sucursal_id: int | None,
    trabajador_id: int,
    tipo_operacion: TipoOperacion,
    materiales_payload: Sequence[dict],
    comentarios_trabajador: str | None = None,
    proveedor_id: int | None = None,
    cliente_id: int | None = None,
    extra_evidencias_payload: Sequence[str] | None = None,
) -> Nota:
    """
    Crea una nota en BORRADOR con materiales y subpesajes opcionales.
    materiales_payload: lista de dicts con material_id, kg_bruto, kg_descuento, subpesajes=[{peso_kg, foto_url}]
    extra_evidencias_payload: lista de URLs de evidencia extra.
    """
    if tipo_operacion not in (TipoOperacion.compra, TipoOperacion.venta):
        raise ValueError("Tipo de operacion invalido.")
    sucursal_id = _resolve_nota_sucursal(
        db,
        sucursal_id=sucursal_id,
        trabajador_id=trabajador_id,
    )
    _validate_partner_for_nota_sucursal(
        db,
        tipo_operacion=tipo_operacion,
        sucursal_id=sucursal_id,
        proveedor_id=proveedor_id,
        cliente_id=cliente_id,
    )
    nota = Nota(
        sucursal_id=sucursal_id,
        inventario_sucursal_id=sucursal_id,
        trabajador_id=trabajador_id,
        tipo_operacion=tipo_operacion,
        estado=NotaEstado.borrador,
        comentarios_trabajador=(comentarios_trabajador or "").strip() or None,
        folio_seq=_next_folio_seq(
            db,
            sucursal_id=sucursal_id,
            tipo_operacion=tipo_operacion,
        ),
    )
    # Asignar partner si viene
    if tipo_operacion == TipoOperacion.compra:
        nota.proveedor_id = proveedor_id
        nota.cliente_id = None
    else:
        nota.proveedor_id = proveedor_id if proveedor_id and not cliente_id else None
        nota.cliente_id = cliente_id if cliente_id else None
    db.add(nota)
    db.flush()

    for idx, mp in enumerate(materiales_payload):
        material = db.get(Material, mp["material_id"])
        if not material:
            raise ValueError(f"Material {mp['material_id']} no existe")

        sub_list_payload = mp.get("subpesajes", []) or []
        if sub_list_payload:
            bruto_sum = _sum_decimal(sp.get("peso_kg", 0) for sp in sub_list_payload)
            desc_sum = _sum_decimal(sp.get("descuento_kg", 0) for sp in sub_list_payload)
            kg_bruto = bruto_sum
            kg_descuento = desc_sum
            kg_neto = bruto_sum - desc_sum
            if kg_neto < 0:
                kg_neto = Decimal("0")
        else:
            kg_bruto = Decimal(str(mp.get("kg_bruto", 0)))
            kg_descuento = Decimal(str(mp.get("kg_descuento", 0)))
            if kg_descuento > kg_bruto:
                raise ValueError("El descuento no puede ser mayor que el peso bruto.")
            kg_neto = kg_bruto - kg_descuento

        tipo_cli_raw = mp.get("tipo_cliente")
        tipo_cli = None
        if tipo_cli_raw:
            try:
                tipo_cli = TipoCliente(tipo_cli_raw)
            except Exception:
                tipo_cli = None

        nm = NotaMaterial(
            nota_id=nota.id,
            material_id=material.id,
            kg_bruto=kg_bruto,
            kg_descuento=kg_descuento,
            kg_neto=kg_neto,
            kg_real=kg_neto,
            orden=idx,
            evidencia_url=mp.get("evidencia_url") or None,
            tipo_cliente=tipo_cli,
        )
        db.add(nm)
        db.flush()

        for sp in sub_list_payload:
            sub = Subpesaje(
                nota_material_id=nm.id,
                peso_kg=Decimal(str(sp.get("peso_kg", 0))),
                descuento_kg=Decimal(str(sp.get("descuento_kg", 0))),
                foto_url=(sp.get("foto_url") or None),
            )
            db.add(sub)

    extra_evidencias_payload = extra_evidencias_payload or []
    seen_urls: set[str] = set()
    for raw_url in extra_evidencias_payload:
        url = (raw_url or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        db.add(
            NotaEvidenciaExtra(
                nota_id=nota.id,
                url=url,
                uploaded_by_id=trabajador_id,
            )
        )

    _recalc_totals(nota)
    apply_prices(db, nota)
    db.commit()
    db.refresh(nota)
    return nota


def update_worker_note(
    db: Session,
    nota: Nota,
    *,
    tipo_operacion: TipoOperacion,
    materiales_payload: Sequence[dict],
    comentarios_trabajador: str | None = None,
    proveedor_id: int | None = None,
    cliente_id: int | None = None,
    extra_evidencias_payload: Sequence[str] | None = None,
) -> Nota:
    """
    Permite al trabajador editar su nota mientras no este aprobada/cancelada.
    """
    if nota.estado != NotaEstado.borrador:
        raise ValueError("Solo puedes editar notas en borrador.")
    if tipo_operacion not in (TipoOperacion.compra, TipoOperacion.venta):
        raise ValueError("Tipo de operacion invalido.")

    old_tipo = nota.tipo_operacion
    if old_tipo != tipo_operacion:
        nota.folio_seq = _next_folio_seq(
            db,
            sucursal_id=nota.sucursal_id,
            tipo_operacion=tipo_operacion,
        )

    _validate_partner_for_nota_sucursal(
        db,
        tipo_operacion=tipo_operacion,
        sucursal_id=nota.sucursal_id,
        proveedor_id=proveedor_id,
        cliente_id=cliente_id,
    )
    nota.tipo_operacion = tipo_operacion
    nota.inventario_sucursal_id = nota.sucursal_id
    nota.comentarios_trabajador = (comentarios_trabajador or "").strip() or None
    if tipo_operacion == TipoOperacion.compra:
        nota.proveedor_id = proveedor_id
        nota.cliente_id = None
    else:
        nota.proveedor_id = proveedor_id if proveedor_id and not cliente_id else None
        nota.cliente_id = cliente_id if cliente_id else None

    nota.materiales.clear()
    db.flush()

    for idx, mp in enumerate(materiales_payload):
        material = db.get(Material, mp["material_id"])
        if not material:
            raise ValueError(f"Material {mp['material_id']} no existe")

        sub_list_payload = mp.get("subpesajes", []) or []
        if sub_list_payload:
            bruto_sum = _sum_decimal(sp.get("peso_kg", 0) for sp in sub_list_payload)
            desc_sum = _sum_decimal(sp.get("descuento_kg", 0) for sp in sub_list_payload)
            kg_bruto = bruto_sum
            kg_descuento = desc_sum
            kg_neto = bruto_sum - desc_sum
            if kg_neto < 0:
                kg_neto = Decimal("0")
        else:
            kg_bruto = Decimal(str(mp.get("kg_bruto", 0)))
            kg_descuento = Decimal(str(mp.get("kg_descuento", 0)))
            if kg_descuento > kg_bruto:
                raise ValueError("El descuento no puede ser mayor que el peso bruto.")
            kg_neto = kg_bruto - kg_descuento

        tipo_cli_raw = mp.get("tipo_cliente")
        tipo_cli = None
        if tipo_cli_raw:
            try:
                tipo_cli = TipoCliente(tipo_cli_raw)
            except Exception:
                tipo_cli = None

        nm = NotaMaterial(
            nota_id=nota.id,
            material_id=material.id,
            kg_bruto=kg_bruto,
            kg_descuento=kg_descuento,
            kg_neto=kg_neto,
            kg_real=kg_neto,
            orden=idx,
            evidencia_url=mp.get("evidencia_url") or None,
            tipo_cliente=tipo_cli,
        )
        db.add(nm)
        db.flush()

        for sp in sub_list_payload:
            sub = Subpesaje(
                nota_material_id=nm.id,
                peso_kg=Decimal(str(sp.get("peso_kg", 0))),
                descuento_kg=Decimal(str(sp.get("descuento_kg", 0))),
                foto_url=(sp.get("foto_url") or None),
            )
            db.add(sub)

    nota.evidencias_extra.clear()
    extra_evidencias_payload = extra_evidencias_payload or []
    seen_urls: set[str] = set()
    for raw_url in extra_evidencias_payload:
        url = (raw_url or "").strip()
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        db.add(
            NotaEvidenciaExtra(
                nota_id=nota.id,
                url=url,
                uploaded_by_id=nota.trabajador_id,
            )
        )

    _recalc_totals(nota)
    apply_prices(db, nota)
    nota.updated_at = datetime.utcnow()
    if nota.estado == NotaEstado.en_revision:
        _store_nota_snapshot(db, nota)
    db.add(nota)
    db.commit()
    db.refresh(nota)
    return nota


def _get_or_create_inventario(db: Session, sucursal_id: int, material_id: int) -> Inventario:
    inv = (
        db.query(Inventario)
        .filter(Inventario.sucursal_id == sucursal_id, Inventario.material_id == material_id)
        .first()
    )
    if inv:
        return inv
    inv = Inventario(
        sucursal_id=sucursal_id,
        material_id=material_id,
        stock_inicial=Decimal("0"),
        stock_actual=Decimal("0"),
    )
    db.add(inv)
    db.flush()
    return inv


def _validar_stock_para_venta(
    db: Session,
    nota: Nota,
    *,
    allow_negative: bool = False,
) -> None:
    if nota.tipo_operacion != TipoOperacion.venta:
        return
    inventario_sucursal_id = get_inventory_sucursal_id(nota)
    if not inventario_sucursal_id:
        raise ValueError("La nota no tiene una sucursal de inventario asignada.")
    if allow_negative:
        return
    for nm in nota.materiales:
        inv = _get_or_create_inventario(db, inventario_sucursal_id, nm.material_id)
        disponible = Decimal(str(inv.stock_actual or 0))
        requerido = _inventory_qty(nm)
        if requerido > disponible:
            nombre_mat = nm.material.nombre if nm.material else f"Material {nm.material_id}"
            raise ValueError(f"Stock insuficiente de {nombre_mat}: disponible {disponible}, requerido {requerido}.")


def _registrar_movimiento_inventario(
    db: Session,
    *,
    nota: Nota,
    nm: NotaMaterial,
    usuario_id: int | None,
) -> None:
    if nota.tipo_operacion not in (TipoOperacion.compra, TipoOperacion.venta):
        return
    inventario_sucursal_id = get_inventory_sucursal_id(nota)
    if not inventario_sucursal_id:
        raise ValueError("La nota no tiene una sucursal de inventario asignada.")
    delta = _inventory_qty(nm)
    tipo_mov = "compra" if nota.tipo_operacion == TipoOperacion.compra else "venta"
    if tipo_mov == "venta":
        delta = -delta
    inv = _get_or_create_inventario(db, inventario_sucursal_id, nm.material_id)
    nuevo_saldo = Decimal(str(inv.stock_actual or 0)) + delta
    inv.stock_actual = nuevo_saldo
    inv.updated_at = datetime.utcnow()
    mov = InventarioMovimiento(
        inventario_id=inv.id,
        nota_id=nota.id,
        nota_material_id=nm.id,
        tipo=tipo_mov,
        cantidad_kg=abs(delta),
        saldo_resultante=nuevo_saldo,
        comentario=f"Auto ({tipo_mov}) nota #{nota.id}",
        usuario_id=usuario_id,
    )
    db.add(inv)
    db.add(mov)


def _registrar_movimiento_contable(
    db: Session,
    *,
    nota: Nota,
    usuario_id: int | None,
    comentario: str | None = None,
    metodo_pago: str | None = None,
    cuenta_financiera: str | None = None,
    cuenta_id: int | None = None,
    cuenta_label: str | None = None,
    caja_sucursal_id: int | None = None,
    monto: Decimal | None = None,
    tipo: str | None = None,
) -> None:
    monto_val = Decimal(str(monto)) if monto is not None else Decimal(str(nota.total_monto or 0))
    tipo_mov = tipo or nota.tipo_operacion.value
    resolved_id = cuenta_id if cuenta_id is not None else nota.cuenta_financiera_id
    resolved_label = cuenta_label
    if resolved_id and not resolved_label:
        cuenta = db.get(Cuenta, resolved_id)
        if cuenta:
            resolved_label = cuenta.display_label
    if not resolved_label:
        resolved_label = cuenta_financiera or None
    mov = MovimientoContable(
        nota_id=nota.id,
        sucursal_id=nota.sucursal_id,
        caja_sucursal_id=caja_sucursal_id,
        usuario_id=usuario_id,
        tipo=tipo_mov,
        monto=monto_val,
        metodo_pago=metodo_pago or nota.metodo_pago,
        cuenta_financiera=resolved_label,
        cuenta_id=resolved_id,
        comentario=comentario or None,
    )
    db.add(mov)


def add_payment(
    db: Session,
    nota: Nota,
    *,
    monto_pagado: Decimal,
    usuario_id: int | None = None,
    metodo_pago: str | None = None,
    cuenta_financiera: str | None = None,
    cuenta_scrap360_id: int | None = None,
    caja_sucursal_id: int | None = None,
    comentario: str | None = None,
    commit: bool = True,
    registrar_contable: bool = True,
) -> NotaPago:
    if nota.estado != NotaEstado.aprobada:
        raise ValueError("Solo puedes registrar pagos en notas aprobadas.")
    if nota.tipo_operacion not in (TipoOperacion.compra, TipoOperacion.venta):
        raise ValueError("Tipo de operacion no soportado para pagos.")
    monto = _normalize_pago_incremental(db, nota, monto_pagado)
    metodo = (metodo_pago or nota.metodo_pago or "").strip().lower() or None
    cuenta_id: int | None = None
    cuenta_label: str | None = None
    if _uses_bank_payment_method(metodo) and cuenta_financiera:
        cuenta_id = _resolve_cuenta_id(db, nota, cuenta_financiera)
        if cuenta_id is None:
            raise ValueError("Selecciona una cuenta valida.")
        cuenta = _validate_cuenta_for_nota(db, nota, cuenta_id)
        cuenta_label = cuenta.display_label
    caja_sucursal_resolved: int | None = None
    if _uses_cash_payment_method(metodo):
        caja_sucursal_resolved = int(caja_sucursal_id or nota.sucursal_id or 0) or None
        if caja_sucursal_resolved is None or not db.get(Sucursal, caja_sucursal_resolved):
            raise ValueError("Selecciona una sucursal de caja valida para efectivo.")
    elif caja_sucursal_id:
        caja_sucursal_resolved = None

    cuenta_scrap = _validate_cuenta_scrap360_for_nota(db, nota, cuenta_scrap360_id, metodo)

    pago = NotaPago(
        nota_id=nota.id,
        usuario_id=usuario_id,
        cuenta_id=cuenta_id,
        cuenta_scrap360_id=cuenta_scrap.id if cuenta_scrap else None,
        caja_sucursal_id=caja_sucursal_resolved,
        monto=monto,
        metodo_pago=metodo,
        cuenta_financiera=cuenta_label or (cuenta_financiera or None),
        comentario=comentario or None,
    )
    nota.monto_pagado = Decimal(str(nota.monto_pagado or 0)) + monto
    nota.updated_at = datetime.utcnow()
    db.add(pago)
    db.add(nota)
    db.flush()
    if registrar_contable:
        _registrar_movimiento_contable(
            db,
            nota=nota,
            usuario_id=usuario_id,
            comentario=comentario or f"Pago nota #{nota.id}",
            metodo_pago=metodo,
            cuenta_label=cuenta_label,
            cuenta_id=cuenta_id,
            caja_sucursal_id=caja_sucursal_resolved,
            monto=monto,
            tipo="pago",
        )
        if cuenta_scrap:
            tipo_mov = "egreso" if _is_compra_like(nota.tipo_operacion) else "ingreso"
            _registrar_movimiento_cuenta_scrap360(
                db,
                cuenta=cuenta_scrap,
                nota=nota,
                pago_id=pago.id,
                usuario_id=usuario_id,
                tipo=tipo_mov,
                monto=monto,
                comentario=comentario or f"Pago nota #{nota.id}",
            )
    if commit:
        db.commit()
        db.refresh(pago)
        db.refresh(nota)
    return pago


def undo_payment(
    db: Session,
    nota: Nota,
    pago: NotaPago,
    *,
    usuario_id: int | None = None,
    comentario: str | None = None,
    commit: bool = True,
) -> NotaPago:
    if nota.estado != NotaEstado.aprobada:
        raise ValueError("Solo puedes deshacer pagos en notas aprobadas.")
    if nota.tipo_operacion not in (TipoOperacion.compra, TipoOperacion.venta):
        raise ValueError("Tipo de operacion no soportado para deshacer pagos.")
    if pago.nota_id != nota.id:
        raise ValueError("El pago no pertenece a la nota.")

    monto_revertir = Decimal(str(pago.monto or 0))
    if monto_revertir <= Decimal("0"):
        raise ValueError("Este pago ya fue deshecho o no tiene monto valido.")

    comentario_base = comentario or f"Deshacer pago #{pago.id} nota #{nota.id}"
    cuenta_label = pago.cuenta.display_label if pago.cuenta else (pago.cuenta_financiera or None)

    _registrar_movimiento_contable(
        db,
        nota=nota,
        usuario_id=usuario_id,
        comentario=comentario_base,
        metodo_pago=pago.metodo_pago or nota.metodo_pago,
        cuenta_label=cuenta_label,
        cuenta_id=pago.cuenta_id,
        caja_sucursal_id=pago.caja_sucursal_id,
        monto=monto_revertir,
        tipo="reverso_pago",
    )

    if pago.cuenta_scrap360_id:
        cuenta_scrap = db.get(CuentaScrap360, pago.cuenta_scrap360_id)
        if cuenta_scrap:
            tipo_mov = "ingreso" if _is_compra_like(nota.tipo_operacion) else "egreso"
            _registrar_movimiento_cuenta_scrap360(
                db,
                cuenta=cuenta_scrap,
                nota=nota,
                pago_id=pago.id,
                usuario_id=usuario_id,
                tipo=tipo_mov,
                monto=monto_revertir,
                comentario=comentario_base,
            )

    nota.monto_pagado = Decimal(str(nota.monto_pagado or 0)) - monto_revertir
    if nota.monto_pagado < Decimal("0"):
        nota.monto_pagado = Decimal("0")
    nota.updated_at = datetime.utcnow()

    stamp = format_datetime_local(datetime.utcnow())
    comentario_prev = (pago.comentario or "").strip()
    tag = f"DESHECHO {stamp} | monto original {monto_revertir:.2f}"
    if comentario_prev:
        pago.comentario = f"{comentario_prev} | {tag}"
    else:
        pago.comentario = tag
    pago.monto = Decimal("0")

    db.add(nota)
    db.add(pago)
    if commit:
        db.commit()
        db.refresh(pago)
        db.refresh(nota)
    return pago


def adjust_initial_payment(
    db: Session,
    nota: Nota,
    *,
    monto_objetivo: Decimal,
    usuario_id: int | None = None,
    metodo_pago: str | None = None,
    cuenta_financiera: str | None = None,
    cuenta_scrap360_id: int | None = None,
    caja_sucursal_id: int | None = None,
    comentario: str | None = None,
) -> None:
    if nota.estado != NotaEstado.aprobada:
        raise ValueError("Solo puedes ajustar pagos en notas aprobadas.")
    if monto_objetivo < Decimal("0"):
        raise ValueError("El monto objetivo no puede ser negativo.")
    snapshot = _effective_note_balance_snapshot(db, nota)
    total_efectivo = Decimal(str(snapshot["total_efectivo"]))
    if total_efectivo < Decimal("0"):
        total_efectivo = Decimal("0")
    if monto_objetivo > total_efectivo:
        raise ValueError("El pago inicial no puede exceder el saldo efectivo de la nota.")

    pagos_iniciales = (
        db.query(NotaPago)
        .filter(
            NotaPago.nota_id == nota.id,
            NotaPago.comentario.ilike("Pago inicial%"),
        )
        .order_by(NotaPago.created_at.asc())
        .all()
    )
    actual = sum((Decimal(str(p.monto or 0)) for p in pagos_iniciales), Decimal("0"))
    otros_pagos = Decimal(str(nota.monto_pagado or 0)) - actual
    if otros_pagos < Decimal("0"):
        otros_pagos = Decimal("0")
    if monto_objetivo + otros_pagos > total_efectivo:
        raise ValueError("El pago inicial ajustado excede el saldo efectivo disponible.")
    delta = monto_objetivo - actual
    if delta == 0:
        return

    if delta > 0:
        metodo = (metodo_pago or nota.metodo_pago or "").strip().lower() or None
        add_payment(
            db,
            nota,
            monto_pagado=delta,
            usuario_id=usuario_id,
            metodo_pago=metodo,
            cuenta_financiera=cuenta_financiera,
            cuenta_scrap360_id=cuenta_scrap360_id,
            caja_sucursal_id=caja_sucursal_id,
            comentario=comentario or "Pago inicial (ajuste)",
            commit=False,
            registrar_contable=True,
        )
        db.commit()
        db.refresh(nota)
        return

    remaining = -delta
    if remaining > actual:
        raise ValueError("El ajuste excede el pago inicial registrado.")

    for pago in reversed(pagos_iniciales):
        if remaining <= 0:
            break
        monto = Decimal(str(pago.monto or 0))
        if monto <= 0:
            continue
        revertir = monto if monto <= remaining else remaining
        remaining -= revertir
        nuevo_monto = monto - revertir
        if nuevo_monto <= Decimal("0"):
            db.delete(pago)
        else:
            pago.monto = nuevo_monto
            db.add(pago)

        _registrar_movimiento_contable(
            db,
            nota=nota,
            usuario_id=usuario_id,
            comentario=comentario or "Reverso pago inicial",
            metodo_pago=pago.metodo_pago or nota.metodo_pago,
            cuenta_label=pago.cuenta.display_label if pago.cuenta else (pago.cuenta_financiera or None),
            cuenta_id=pago.cuenta_id,
            caja_sucursal_id=pago.caja_sucursal_id,
            monto=revertir,
            tipo="reverso_pago",
        )
        if pago.cuenta_scrap360_id:
            cuenta_scrap = db.get(CuentaScrap360, pago.cuenta_scrap360_id)
            if cuenta_scrap:
                tipo_mov = "ingreso" if _is_compra_like(nota.tipo_operacion) else "egreso"
                _registrar_movimiento_cuenta_scrap360(
                    db,
                    cuenta=cuenta_scrap,
                    nota=nota,
                    pago_id=pago.id,
                    usuario_id=usuario_id,
                    tipo=tipo_mov,
                    monto=revertir,
                    comentario=comentario or "Reverso pago inicial",
                )

    nota.monto_pagado = Decimal(str(nota.monto_pagado or 0)) + delta
    if nota.monto_pagado < Decimal("0"):
        nota.monto_pagado = Decimal("0")
    nota.updated_at = datetime.utcnow()
    db.add(nota)
    db.commit()
    db.refresh(nota)

def ajustar_stock(
    db: Session,
    *,
    sucursal_id: int,
    material_id: int,
    cantidad_kg: Decimal,
    comentario: str | None,
    usuario_id: int | None,
    nota_id: int | None = None,
    nota_material_id: int | None = None,
    reversal_of_id: int | None = None,
) -> Inventario:
    """
    Ajuste manual de inventario (positivo suma, negativo resta).
    Puede ser general o ligado a una nota/material de nota especificos.
    """
    inv = _get_or_create_inventario(db, sucursal_id, material_id)
    saldo_actual = Decimal(str(inv.stock_actual or 0))
    delta = Decimal(str(cantidad_kg or 0))
    nuevo_saldo = saldo_actual + delta
    inv.stock_actual = nuevo_saldo
    inv.updated_at = datetime.utcnow()

    mov = InventarioMovimiento(
        inventario_id=inv.id,
        nota_id=nota_id,
        nota_material_id=nota_material_id,
        tipo="ajuste",
        cantidad_kg=delta,
        saldo_resultante=nuevo_saldo,
        comentario=comentario or "Ajuste manual",
        usuario_id=usuario_id,
    )
    db.add(inv)
    db.add(mov)

    movc = MovimientoContable(
        nota_id=nota_id,
        sucursal_id=sucursal_id,
        usuario_id=usuario_id,
        tipo="ajuste",
        monto=Decimal("0"),
        comentario=comentario or "Ajuste inventario",
    )
    db.add(movc)
    db.flush()

    ajuste = InventarioAjusteManual(
        sucursal_id=sucursal_id,
        material_id=material_id,
        nota_id=nota_id,
        nota_material_id=nota_material_id,
        inventario_movimiento_id=mov.id,
        usuario_id=usuario_id,
        cantidad_kg=delta,
        stock_anterior=saldo_actual,
        stock_resultante=nuevo_saldo,
        comentario=comentario or "Ajuste manual",
        reversal_of_id=reversal_of_id,
    )
    db.add(ajuste)
    if reversal_of_id:
        original = db.get(InventarioAjusteManual, reversal_of_id)
        if original:
            original.reverted_at = datetime.utcnow()
            original.reverted_by_user_id = usuario_id
            original.comentario_reversion = comentario or f"Reversion ajuste manual #{reversal_of_id}"
            db.add(original)
    db.commit()
    db.refresh(inv)
    return inv


def reverse_manual_inventory_adjustment(
    db: Session,
    ajuste: InventarioAjusteManual,
    *,
    usuario_id: int | None = None,
    comentario: str | None = None,
) -> InventarioAjusteManual:
    if ajuste.reversal_of_id:
        raise ValueError("No puedes revertir una reversion de inventario.")
    if ajuste.reverted_at:
        raise ValueError("Este ajuste manual ya fue revertido.")

    delta_original = Decimal(str(ajuste.cantidad_kg or 0))
    delta_reversion = -delta_original

    comment = comentario or f"Reversion ajuste manual #{ajuste.id}"
    ajustar_stock(
        db,
        sucursal_id=ajuste.sucursal_id,
        material_id=ajuste.material_id,
        cantidad_kg=delta_reversion,
        comentario=comment,
        usuario_id=usuario_id,
        nota_id=ajuste.nota_id,
        nota_material_id=ajuste.nota_material_id,
        reversal_of_id=ajuste.id,
    )
    db.refresh(ajuste)
    return ajuste


def adjust_note_balance(
    db: Session,
    nota: Nota,
    *,
    monto_delta: Decimal,
    usuario_id: int | None = None,
    comentario: str | None = None,
    reversal_of_id: int | None = None,
) -> NotaAjusteSaldo:
    if nota.estado != NotaEstado.aprobada:
        raise ValueError("Solo puedes ajustar saldo en notas aprobadas.")
    delta = Decimal(str(monto_delta or 0))
    if delta == Decimal("0"):
        raise ValueError("El ajuste de saldo no puede ser cero.")

    snapshot = _effective_note_balance_snapshot(db, nota)
    saldo_anterior = Decimal(str(snapshot["saldo"]))
    saldo_resultante = saldo_anterior + delta

    ajuste = NotaAjusteSaldo(
        nota_id=nota.id,
        usuario_id=usuario_id,
        monto_delta=delta,
        saldo_anterior=saldo_anterior,
        saldo_resultante=saldo_resultante,
        comentario=comentario or "Ajuste de saldo ligado a nota",
        reversal_of_id=reversal_of_id,
    )
    db.add(ajuste)

    if reversal_of_id:
        original = db.get(NotaAjusteSaldo, reversal_of_id)
        if original:
            original.reverted_at = datetime.utcnow()
            original.reverted_by_user_id = usuario_id
            original.comentario_reversion = comentario or f"Reversion ajuste saldo #{reversal_of_id}"
            db.add(original)

    nota.updated_at = datetime.utcnow()
    db.add(nota)
    db.commit()
    db.refresh(ajuste)
    db.refresh(nota)
    return ajuste


def reverse_note_balance_adjustment(
    db: Session,
    ajuste: NotaAjusteSaldo,
    *,
    usuario_id: int | None = None,
    comentario: str | None = None,
) -> NotaAjusteSaldo:
    if ajuste.reversal_of_id:
        raise ValueError("No puedes revertir una reversion de saldo.")
    if ajuste.reverted_at:
        raise ValueError("Este ajuste de saldo ya fue revertido.")
    nota = db.get(Nota, ajuste.nota_id)
    if not nota:
        raise ValueError("La nota del ajuste ya no existe.")
    if nota.estado != NotaEstado.aprobada:
        raise ValueError("Solo puedes revertir ajustes de saldo en notas aprobadas.")

    delta_reversion = -Decimal(str(ajuste.monto_delta or 0))
    comment = comentario or f"Reversion ajuste saldo nota #{nota.id} - ajuste #{ajuste.id}"
    adjust_note_balance(
        db,
        nota,
        monto_delta=delta_reversion,
        usuario_id=usuario_id,
        comentario=comment,
        reversal_of_id=ajuste.id,
    )
    db.refresh(ajuste)
    return ajuste



def approve_note(
    db: Session,
    nota: Nota,
    *,
    tipo_cliente_map: dict[int, TipoCliente] | None = None,
    precio_override_map: dict[int, dict[str, Decimal]] | None = None,
    kg_real_override_map: dict[int, Decimal] | None = None,
    admin_id: int | None = None,
    comentarios_admin: str | None = None,
    fecha_caducidad_pago: date | None = None,
    metodo_pago: str | None = None,
    cuenta_financiera: str | None = None,
    cuenta_scrap360_id: int | None = None,
    monto_pagado: Decimal | None = None,
    iva_incluido: bool | None = None,
    iva_porcentaje: Decimal | None = None,
    inventario_sucursal_id: int | None = None,
    caja_sucursal_id: int | None = None,
) -> Nota:
    """
    Aprueba una nota aplicando precios, recalculando totales y registrando inventario/contable.
    """
    if nota.estado not in (NotaEstado.en_revision, NotaEstado.borrador):
        raise ValueError("Solo se puede aprobar desde borrador o en revisión.")
    if nota.sucursal_id is None:
        raise ValueError("La nota no tiene una sucursal asignada.")
    if nota.tipo_operacion not in (TipoOperacion.compra, TipoOperacion.venta):
        raise ValueError("Tipo de operacion no soportado.")
    if tipo_cliente_map:
        for nm in nota.materiales:
            if nm.id in tipo_cliente_map:
                nm.tipo_cliente = tipo_cliente_map[nm.id]
    if iva_incluido is not None:
        nota.iva_incluido = bool(iva_incluido)
    iva_pct = None
    if iva_porcentaje is not None:
        iva_pct = Decimal(str(iva_porcentaje))
    elif nota.iva_porcentaje is None:
        iva_pct = Decimal("16.00")
    if iva_pct is not None:
        if iva_pct < 0 or iva_pct > 100:
            raise ValueError("El porcentaje de IVA debe estar entre 0 y 100.")
        nota.iva_porcentaje = iva_pct
    if nota.tipo_operacion == TipoOperacion.compra:
        resolved_inventario_sucursal_id = inventario_sucursal_id
        if resolved_inventario_sucursal_id is None:
            resolved_inventario_sucursal_id = get_inventory_sucursal_id(nota)
        if resolved_inventario_sucursal_id is None:
            resolved_inventario_sucursal_id = nota.sucursal_id
        if not resolved_inventario_sucursal_id:
            raise ValueError("Debes indicar la sucursal donde se registrara el inventario.")
        sucursal_inventario = db.get(Sucursal, int(resolved_inventario_sucursal_id))
        if not sucursal_inventario:
            raise ValueError("La sucursal de inventario seleccionada no existe.")
        nota.inventario_sucursal_id = int(resolved_inventario_sucursal_id)
    else:
        nota.inventario_sucursal_id = nota.sucursal_id
    apply_prices(db, nota)
    _apply_precio_overrides(nota, precio_override_map)
    _apply_real_kg_overrides(nota, kg_real_override_map)
    _recalc_totals(nota)
    _validar_stock_para_venta(db, nota, allow_negative=True)
    metodo_pago_clean = (metodo_pago or "").strip().lower() or None
    cuenta_id: int | None = None
    cuenta_label: str | None = None
    caja_sucursal_resolved: int | None = None
    pago_inicial: Decimal | None = None
    if monto_pagado is not None and Decimal(str(monto_pagado)) > Decimal("0"):
        pago_inicial = Decimal(str(monto_pagado))
    if _uses_bank_payment_method(metodo_pago_clean):
        if cuenta_financiera:
            cuenta_id = _resolve_cuenta_id(db, nota, cuenta_financiera)
            if cuenta_id is None:
                raise ValueError("Selecciona una cuenta valida.")
            cuenta = _validate_cuenta_for_nota(db, nota, cuenta_id)
            cuenta_label = cuenta.display_label
    elif _uses_cash_payment_method(metodo_pago_clean):
        cuenta_id = None
        if pago_inicial and pago_inicial > Decimal("0"):
            caja_sucursal_resolved = int(caja_sucursal_id or nota.sucursal_id or 0) or None
            if caja_sucursal_resolved is None or not db.get(Sucursal, caja_sucursal_resolved):
                raise ValueError("Selecciona una sucursal de caja valida para efectivo.")
    nota.metodo_pago = metodo_pago_clean
    nota.cuenta_financiera_id = cuenta_id
    update_state(
        db,
        nota,
        new_state=NotaEstado.aprobada,
        admin_id=admin_id,
        comentarios_admin=comentarios_admin,
        fecha_caducidad_pago=fecha_caducidad_pago,
        commit=False,
    )
    # registrar inventario y contabilidad
    for nm in nota.materiales:
        _registrar_movimiento_inventario(db, nota=nota, nm=nm, usuario_id=admin_id)
    if nota.tipo_operacion and not _has_base_contable_movement(db, nota.id, nota.tipo_operacion.value):
        _registrar_movimiento_contable(
            db,
            nota=nota,
            usuario_id=admin_id,
            comentario=comentarios_admin or f"Nota aprobada #{nota.id}",
            metodo_pago=nota.metodo_pago,
            cuenta_label=None,
            cuenta_id=None,
            caja_sucursal_id=caja_sucursal_resolved,
            tipo=nota.tipo_operacion.value,
        )
    if pago_inicial is not None and Decimal(str(pago_inicial)) > Decimal("0"):
        add_payment(
            db,
            nota,
            monto_pagado=pago_inicial,
            usuario_id=admin_id,
            metodo_pago=metodo_pago_clean,
            cuenta_financiera=cuenta_label or (str(cuenta_id) if cuenta_id else None),
            cuenta_scrap360_id=cuenta_scrap360_id,
            caja_sucursal_id=caja_sucursal_resolved,
            comentario="Pago inicial",
            commit=False,
            registrar_contable=True,
        )
    db.commit()
    db.refresh(nota)
    return nota


def update_state(
    db: Session,
    nota: Nota,
    *,
    new_state: NotaEstado,
    admin_id: int | None = None,
    comentarios_admin: str | None = None,
    fecha_caducidad_pago: date | None = None,
    commit: bool = True,
) -> Nota:
    """
    Transición de estados con campos adicionales para admins.
    """
    nota.estado = new_state
    if admin_id is not None:
        nota.admin_id = admin_id
    if comentarios_admin is not None:
        nota.comentarios_admin = comentarios_admin.strip() or None
    if fecha_caducidad_pago is not None:
        nota.fecha_caducidad_pago = fecha_caducidad_pago
    nota.updated_at = datetime.utcnow()

    _recalc_totals(nota)
    db.add(nota)
    if commit:
        db.commit()
        db.refresh(nota)
    return nota


def reverse_partial_return_line(
    db: Session,
    nota: Nota,
    linea: NotaDevolucionParcialLinea,
    *,
    admin_id: int | None = None,
    comentario: str | None = None,
    commit: bool = True,
) -> Nota:
    if nota.estado != NotaEstado.aprobada:
        raise ValueError("Solo puedes revertir devoluciones parciales en notas aprobadas.")
    if linea.devolucion.nota_id != nota.id:
        raise ValueError("La linea no pertenece a esta nota.")
    if linea.reverted_at:
        raise ValueError("Esta linea de devolucion parcial ya fue revertida.")

    nm = linea.nota_material
    if not nm:
        raise ValueError("No se encontro el material original de la devolucion.")

    kg_restore = Decimal(str(linea.kg_devolucion or 0))
    kg_real_restore = _normalize_kg_real(getattr(linea, "kg_real_devolucion", None) or kg_restore)
    monto_restore = Decimal(str(linea.monto_devolucion or 0))
    if kg_restore <= Decimal("0"):
        raise ValueError("La linea no tiene kg validos para revertir.")

    if nm.subpesajes:
        for aplicacion in linea.aplicaciones:
            sp = aplicacion.subpesaje
            if not sp:
                raise ValueError("Falta un subpesaje necesario para revertir esta accion.")
            kg_aplicado = Decimal(str(aplicacion.kg_aplicado or 0))
            desc_actual = Decimal(str(sp.descuento_kg or 0))
            if desc_actual < kg_aplicado:
                raise ValueError("Los subpesajes actuales ya no permiten revertir esta devolucion.")
            sp.descuento_kg = desc_actual - kg_aplicado
            db.add(sp)
    else:
        desc_actual = Decimal(str(nm.kg_descuento or 0))
        if desc_actual < kg_restore:
            raise ValueError("El descuento actual del material no permite revertir esta devolucion.")
        nm.kg_descuento = desc_actual - kg_restore

    _recalc_material(nm)
    nm.kg_real = _normalize_kg_real(_inventory_qty(nm) + kg_real_restore)
    nm.subtotal = Decimal(str(nm.subtotal or 0)) + monto_restore
    if Decimal(str(nm.kg_neto or 0)) > Decimal("0"):
        nm.precio_unitario = Decimal(str(nm.subtotal or 0)) / Decimal(str(nm.kg_neto or 0))
    else:
        nm.precio_unitario = Decimal("0")
    nm.version_precio_id = None
    db.add(nm)

    stock_delta = kg_real_restore if _is_compra_like(nota.tipo_operacion) else -kg_real_restore
    inv = _get_or_create_inventario(db, get_inventory_sucursal_id(nota), nm.material_id)
    new_stock = Decimal(str(inv.stock_actual or 0)) + stock_delta
    if new_stock < Decimal("0"):
        raise ValueError("No hay stock suficiente para revertir esta devolucion parcial.")
    inv.stock_actual = new_stock
    inv.updated_at = datetime.utcnow()
    db.add(inv)
    db.add(
        InventarioMovimiento(
            inventario_id=inv.id,
            nota_id=nota.id,
            nota_material_id=nm.id,
            tipo="ajuste",
            cantidad_kg=stock_delta,
            saldo_resultante=new_stock,
            comentario=comentario or f"Reversion devolucion parcial #{linea.id}",
            usuario_id=admin_id,
        )
    )

    _recalc_totals(nota)
    nota.updated_at = datetime.utcnow()
    db.add(nota)

    _registrar_movimiento_contable(
        db,
        nota=nota,
        usuario_id=admin_id,
        comentario=comentario or f"Reversion devolucion parcial #{linea.id}",
        metodo_pago=nota.metodo_pago,
        cuenta_id=nota.cuenta_financiera_id,
        monto=_partial_return_contable_amount(nota, monto_restore, reverse=True),
        tipo="ajuste",
    )

    linea.reverted_at = datetime.utcnow()
    linea.reverted_by_user_id = admin_id
    linea.comentario_reversion = comentario or f"Revertida por admin en nota #{nota.id}"
    db.add(linea)

    if commit:
        db.commit()
        db.refresh(nota)
    return nota


def send_to_revision(
    db: Session,
    nota: Nota,
) -> Nota:
    """
    Pasa una nota de BORRADOR a EN_REVISION. Solo válido desde BORRADOR.
    """
    if nota.estado != NotaEstado.borrador:
        raise ValueError("Solo notas en borrador pueden enviarse a revisión.")
    # aplicar precios (default regular si no se especificó tipo_cliente)
    for nm in nota.materiales:
        if nm.tipo_cliente is None:
            nm.tipo_cliente = TipoCliente.regular
    apply_prices(db, nota)
    nota.estado = NotaEstado.en_revision
    nota.updated_at = datetime.utcnow()
    _recalc_totals(nota)
    _store_nota_snapshot(db, nota)
    db.add(nota)
    db.commit()
    db.refresh(nota)
    return nota


def cancel_approved_note(
    db: Session,
    nota: Nota,
    *,
    admin_id: int | None = None,
    comentarios_admin: str | None = None,
    commit: bool = True,
) -> Nota:
    """
    Cancela una nota aprobada, revierte inventario y registra reversos contables.
    """
    if nota.estado != NotaEstado.aprobada:
        raise ValueError("Solo puedes cancelar notas aprobadas.")
    if nota.tipo_operacion not in (TipoOperacion.compra, TipoOperacion.venta):
        raise ValueError("Tipo de operacion no soportado para devolucion.")

    base_tipo = nota.tipo_operacion.value
    if not _has_base_contable_movement(db, nota.id, base_tipo):
        _registrar_movimiento_contable(
            db,
            nota=nota,
            usuario_id=admin_id,
            comentario=f"Movimiento base generado para cancelacion nota #{nota.id}",
            metodo_pago=nota.metodo_pago,
            cuenta_id=nota.cuenta_financiera_id,
            tipo=base_tipo,
        )

    comment_base = comentarios_admin or f"Cancelacion nota #{nota.id}"
    devolucion_total = NotaDevolucionTotal(
        nota_id=nota.id,
        usuario_id=admin_id,
        comentario=comentarios_admin or None,
        created_at=datetime.utcnow(),
    )
    db.add(devolucion_total)

    for nm in nota.materiales:
        delta = _inventory_qty(nm)
        signed_delta = -delta if nota.tipo_operacion == TipoOperacion.compra else delta
        inv = _get_or_create_inventario(db, get_inventory_sucursal_id(nota), nm.material_id)
        nuevo_saldo = Decimal(str(inv.stock_actual or 0)) + signed_delta
        if nuevo_saldo < Decimal("0"):
            nombre_mat = nm.material.nombre if nm.material else f"Material {nm.material_id}"
            raise ValueError(f"Stock insuficiente para revertir {nombre_mat}.")
        inv.stock_actual = nuevo_saldo
        inv.updated_at = datetime.utcnow()
        mov = InventarioMovimiento(
            inventario_id=inv.id,
            nota_id=nota.id,
            nota_material_id=nm.id,
            tipo="ajuste",
            cantidad_kg=signed_delta,
            saldo_resultante=nuevo_saldo,
            comentario=comment_base,
            usuario_id=admin_id,
        )
        db.add(inv)
        db.add(mov)

    _registrar_movimiento_contable(
        db,
        nota=nota,
        usuario_id=admin_id,
        comentario=comment_base,
        metodo_pago=nota.metodo_pago,
        cuenta_id=None,
        monto=Decimal(str(nota.total_monto or 0)) * Decimal("-1"),
        tipo="reverso",
    )

    for pago in nota.pagos or []:
        monto_pago = Decimal(str(pago.monto or 0))
        if monto_pago <= Decimal("0"):
            continue
        _registrar_movimiento_contable(
            db,
            nota=nota,
            usuario_id=admin_id,
            comentario=f"Reverso pago nota #{nota.id}",
            metodo_pago=pago.metodo_pago or nota.metodo_pago,
            cuenta_label=pago.cuenta.display_label if pago.cuenta else (pago.cuenta_financiera or None),
            cuenta_id=pago.cuenta_id,
            monto=monto_pago * Decimal("-1"),
            tipo="reverso_pago",
        )
        if pago.cuenta_scrap360_id:
            cuenta_scrap = db.get(CuentaScrap360, pago.cuenta_scrap360_id)
            if cuenta_scrap:
                tipo_mov = "ingreso" if _is_compra_like(nota.tipo_operacion) else "egreso"
                _registrar_movimiento_cuenta_scrap360(
                    db,
                    cuenta=cuenta_scrap,
                    nota=nota,
                    pago_id=pago.id,
                    usuario_id=admin_id,
                    tipo=tipo_mov,
                    monto=monto_pago,
                    comentario=f"Reverso pago nota #{nota.id}",
                )

    nota.estado = NotaEstado.cancelada
    if comentarios_admin is not None:
        nota.comentarios_admin = comentarios_admin.strip() or None
    if admin_id is not None:
        nota.admin_id = admin_id
    nota.factura_url = None
    nota.factura_generada_at = None
    nota.updated_at = datetime.utcnow()
    db.add(nota)
    if commit:
        db.commit()
        db.refresh(nota)
    return nota


def reverse_total_return(
    db: Session,
    nota: Nota,
    devolucion_total: NotaDevolucionTotal,
    *,
    admin_id: int | None = None,
    comentario: str | None = None,
    commit: bool = True,
) -> Nota:
    if nota.estado != NotaEstado.cancelada:
        raise ValueError("Solo puedes revertir una devolucion total cuando la nota esta cancelada.")
    if devolucion_total.nota_id != nota.id:
        raise ValueError("La devolucion total no pertenece a esta nota.")
    if devolucion_total.reverted_at:
        raise ValueError("Esta devolucion total ya fue revertida.")
    if nota.tipo_operacion not in (TipoOperacion.compra, TipoOperacion.venta):
        raise ValueError("Tipo de operacion no soportado para reversion.")

    comment_base = comentario or f"Reversion devolucion total #{devolucion_total.id} nota #{nota.id}"

    for nm in nota.materiales:
        delta = _inventory_qty(nm)
        signed_delta = delta if nota.tipo_operacion == TipoOperacion.compra else -delta
        inv = _get_or_create_inventario(db, get_inventory_sucursal_id(nota), nm.material_id)
        nuevo_saldo = Decimal(str(inv.stock_actual or 0)) + signed_delta
        inv.stock_actual = nuevo_saldo
        inv.updated_at = datetime.utcnow()
        db.add(inv)
        db.add(
            InventarioMovimiento(
                inventario_id=inv.id,
                nota_id=nota.id,
                nota_material_id=nm.id,
                tipo="ajuste",
                cantidad_kg=signed_delta,
                saldo_resultante=nuevo_saldo,
                comentario=comment_base,
                usuario_id=admin_id,
            )
        )

    _registrar_movimiento_contable(
        db,
        nota=nota,
        usuario_id=admin_id,
        comentario=comment_base,
        metodo_pago=nota.metodo_pago,
        cuenta_id=nota.cuenta_financiera_id,
        monto=Decimal(str(nota.total_monto or 0)),
        tipo="restauracion",
    )

    for pago in nota.pagos or []:
        monto_pago = Decimal(str(pago.monto or 0))
        if monto_pago <= Decimal("0"):
            continue
        _registrar_movimiento_contable(
            db,
            nota=nota,
            usuario_id=admin_id,
            comentario=f"Restauracion pago nota #{nota.id}",
            metodo_pago=pago.metodo_pago or nota.metodo_pago,
            cuenta_label=pago.cuenta.display_label if pago.cuenta else (pago.cuenta_financiera or None),
            cuenta_id=pago.cuenta_id,
            monto=monto_pago,
            tipo="restauracion_pago",
        )
        if pago.cuenta_scrap360_id:
            cuenta_scrap = db.get(CuentaScrap360, pago.cuenta_scrap360_id)
            if cuenta_scrap:
                tipo_mov = "egreso" if _is_compra_like(nota.tipo_operacion) else "ingreso"
                _registrar_movimiento_cuenta_scrap360(
                    db,
                    cuenta=cuenta_scrap,
                    nota=nota,
                    pago_id=pago.id,
                    usuario_id=admin_id,
                    tipo=tipo_mov,
                    monto=monto_pago,
                    comentario=f"Restauracion pago nota #{nota.id}",
                )

    nota.estado = NotaEstado.aprobada
    if admin_id is not None:
        nota.admin_id = admin_id
    nota.updated_at = datetime.utcnow()
    db.add(nota)

    devolucion_total.reverted_at = datetime.utcnow()
    devolucion_total.reverted_by_user_id = admin_id
    devolucion_total.comentario_reversion = comentario or None
    db.add(devolucion_total)

    if commit:
        db.commit()
        db.refresh(nota)
    return nota


def partial_return_approved_note(
    db: Session,
    nota: Nota,
    *,
    devoluciones_payload: Sequence[dict],
    admin_id: int | None = None,
    comentario: str | None = None,
    commit: bool = True,
) -> Nota:
    """
    Aplica una devolucion parcial sobre una nota aprobada.
    Reduce kg netos por material y descuenta el monto segun precio de devolucion por kg.
    No genera reembolso automatico: si queda sobrepago, permanece como saldo a favor.
    """
    if nota.estado != NotaEstado.aprobada:
        raise ValueError("Solo puedes aplicar devoluciones parciales en notas aprobadas.")
    if nota.tipo_operacion not in (TipoOperacion.compra, TipoOperacion.venta):
        raise ValueError("Tipo de operacion no soportado para devolucion parcial.")
    if not devoluciones_payload:
        raise ValueError("Debes indicar al menos un material para devolucion.")

    devoluciones_map: dict[int, dict[str, Decimal]] = {}
    for item in devoluciones_payload:
        try:
            nm_id = int(item.get("nota_material_id"))
        except (TypeError, ValueError):
            raise ValueError("Material de devolucion invalido.")
        kg_devolucion = Decimal(str(item.get("kg_devolucion") or 0))
        precio_devolucion = Decimal(str(item.get("precio_unitario_devolucion") or 0))
        if kg_devolucion < Decimal("0"):
            raise ValueError("El kg de devolucion no puede ser negativo.")
        if precio_devolucion < Decimal("0"):
            raise ValueError("El precio de devolucion no puede ser negativo.")
        if kg_devolucion == Decimal("0"):
            continue
        monto_devolucion = kg_devolucion * precio_devolucion
        if nm_id not in devoluciones_map:
            devoluciones_map[nm_id] = {
                "kg_devolucion": Decimal("0"),
                "monto_devolucion": Decimal("0"),
                "precio_unitario_devolucion": precio_devolucion,
            }
        devoluciones_map[nm_id]["kg_devolucion"] += kg_devolucion
        devoluciones_map[nm_id]["monto_devolucion"] += monto_devolucion
        devoluciones_map[nm_id]["precio_unitario_devolucion"] = (
            devoluciones_map[nm_id]["monto_devolucion"] / devoluciones_map[nm_id]["kg_devolucion"]
            if devoluciones_map[nm_id]["kg_devolucion"] > Decimal("0")
            else Decimal("0")
        )

    if not devoluciones_map:
        raise ValueError("Debes indicar un kg mayor a 0 en al menos un material.")

    materiales_by_id = {nm.id: nm for nm in nota.materiales}
    ajustes_material: dict[int, dict[str, Decimal]] = {}

    for nm_id, payload in devoluciones_map.items():
        nm = materiales_by_id.get(nm_id)
        if not nm:
            raise ValueError("Un material de devolucion no pertenece a la nota.")
        kg_actual = Decimal(str(nm.kg_neto or 0))
        kg_real_actual = _inventory_qty(nm)
        subtotal_actual = Decimal(str(nm.subtotal or 0))
        kg_devolucion = payload["kg_devolucion"]
        monto_devolucion = payload["monto_devolucion"]
        if kg_actual <= Decimal("0"):
            raise ValueError("No se puede devolver un material con kg neto 0.")
        if kg_devolucion > kg_actual:
            mat_name = nm.material.nombre if nm.material else f"Material {nm.material_id}"
            raise ValueError(f"La devolucion excede los kg netos disponibles de {mat_name}.")
        if monto_devolucion > subtotal_actual:
            mat_name = nm.material.nombre if nm.material else f"Material {nm.material_id}"
            raise ValueError(f"El monto de devolucion excede el subtotal disponible de {mat_name}.")
        kg_real_devolucion = Decimal("0")
        if kg_actual > Decimal("0") and kg_real_actual > Decimal("0"):
            kg_real_devolucion = _normalize_kg_real((kg_real_actual * kg_devolucion) / kg_actual)
            if kg_real_devolucion > kg_real_actual:
                kg_real_devolucion = _normalize_kg_real(kg_real_actual)

        kg_nuevo = kg_actual - kg_devolucion
        kg_real_nuevo = kg_real_actual - kg_real_devolucion
        subtotal_nuevo = subtotal_actual - monto_devolucion
        if subtotal_nuevo < Decimal("0"):
            subtotal_nuevo = Decimal("0")
        if kg_nuevo <= Decimal("0"):
            if subtotal_nuevo > Decimal("0"):
                mat_name = nm.material.nombre if nm.material else f"Material {nm.material_id}"
                raise ValueError(
                    f"La devolucion deja subtotal positivo con kg neto 0 en {mat_name}. "
                    "Ajusta el precio de devolucion."
                )
            kg_nuevo = Decimal("0")
            subtotal_nuevo = Decimal("0")
        ajustes_material[nm_id] = {
            "kg_devolucion": kg_devolucion,
            "kg_real_devolucion": kg_real_devolucion,
            "kg_nuevo": kg_nuevo,
            "kg_real_nuevo": kg_real_nuevo,
            "subtotal_nuevo": subtotal_nuevo,
            "monto_devolucion": monto_devolucion,
            "precio_unitario_devolucion": payload["precio_unitario_devolucion"],
            "aplicaciones": [],
        }

    old_total = Decimal(str(nota.total_monto or 0))
    comment_base = comentario or f"Devolucion parcial nota #{nota.id}"
    devolucion = NotaDevolucionParcial(
        nota_id=nota.id,
        usuario_id=admin_id,
        comentario=comentario or None,
        created_at=datetime.utcnow(),
    )
    db.add(devolucion)
    db.flush()

    for nm_id, ajuste in ajustes_material.items():
        nm = materiales_by_id[nm_id]
        kg_devolucion = ajuste["kg_devolucion"]

        if nm.subpesajes:
            remaining = kg_devolucion
            for sp in sorted(nm.subpesajes, key=lambda s: s.id, reverse=True):
                peso = Decimal(str(sp.peso_kg or 0))
                desc = Decimal(str(getattr(sp, "descuento_kg", 0) or 0))
                neto_sp = peso - desc
                if neto_sp <= Decimal("0"):
                    continue
                take = neto_sp if neto_sp <= remaining else remaining
                sp.descuento_kg = desc + take
                db.add(sp)
                ajuste["aplicaciones"].append({"subpesaje_id": sp.id, "kg_aplicado": take})
                remaining -= take
                if remaining <= Decimal("0"):
                    break
            if remaining > Decimal("0"):
                raise ValueError("No se pudo aplicar la devolucion parcial sobre subpesajes.")
        else:
            nm.kg_descuento = Decimal(str(nm.kg_descuento or 0)) + kg_devolucion
            if nm.kg_descuento > Decimal(str(nm.kg_bruto or 0)):
                raise ValueError("La devolucion excede el kg bruto del material.")

        _recalc_material(nm)
        nm.kg_real = _normalize_kg_real(ajuste["kg_real_nuevo"])
        subtotal_nuevo = ajuste["subtotal_nuevo"]
        nm.subtotal = subtotal_nuevo
        if Decimal(str(nm.kg_neto or 0)) > Decimal("0"):
            nm.precio_unitario = subtotal_nuevo / Decimal(str(nm.kg_neto or 0))
        else:
            nm.precio_unitario = Decimal("0")
        nm.version_precio_id = None
        db.add(nm)

    for nm_id, ajuste in ajustes_material.items():
        nm = materiales_by_id[nm_id]
        kg_devolucion = ajuste["kg_devolucion"]
        kg_real_devolucion = ajuste["kg_real_devolucion"]
        stock_delta = -kg_real_devolucion if _is_compra_like(nota.tipo_operacion) else kg_real_devolucion
        inv = _get_or_create_inventario(db, get_inventory_sucursal_id(nota), nm.material_id)
        new_stock = Decimal(str(inv.stock_actual or 0)) + stock_delta
        if new_stock < Decimal("0"):
            nombre_mat = nm.material.nombre if nm.material else f"Material {nm.material_id}"
            raise ValueError(f"Stock insuficiente para devolver {nombre_mat}.")
        inv.stock_actual = new_stock
        inv.updated_at = datetime.utcnow()
        mov = InventarioMovimiento(
            inventario_id=inv.id,
            nota_id=nota.id,
            nota_material_id=nm.id,
            tipo="ajuste",
            cantidad_kg=stock_delta,
            saldo_resultante=new_stock,
            comentario=comment_base,
            usuario_id=admin_id,
        )
        db.add(inv)
        db.add(mov)

        linea = NotaDevolucionParcialLinea(
            devolucion_id=devolucion.id,
            nota_material_id=nm.id,
            material_id=nm.material_id,
            kg_devolucion=kg_devolucion,
            kg_real_devolucion=kg_real_devolucion,
            precio_unitario_devolucion=ajuste["precio_unitario_devolucion"],
            monto_devolucion=ajuste["monto_devolucion"],
            created_at=datetime.utcnow(),
        )
        db.add(linea)
        db.flush()
        for aplicacion in ajuste["aplicaciones"]:
            db.add(
                NotaDevolucionParcialAplicacion(
                    linea_id=linea.id,
                    subpesaje_id=aplicacion["subpesaje_id"],
                    kg_aplicado=aplicacion["kg_aplicado"],
                    created_at=datetime.utcnow(),
                )
            )

    _recalc_totals(nota)
    nota.updated_at = datetime.utcnow()
    db.add(nota)

    new_total = Decimal(str(nota.total_monto or 0))
    delta_total = new_total - old_total
    if delta_total != Decimal("0"):
        ajuste_monto = delta_total
        if _is_compra_like(nota.tipo_operacion):
            ajuste_monto = -delta_total
        _registrar_movimiento_contable(
            db,
            nota=nota,
            usuario_id=admin_id,
            comentario=comment_base,
            metodo_pago=nota.metodo_pago,
            cuenta_id=nota.cuenta_financiera_id,
            monto=ajuste_monto,
            tipo="ajuste",
        )

    if commit:
        db.commit()
        db.refresh(nota)
    return nota


def attach_partner(
    db: Session,
    nota: Nota,
    *,
    proveedor_id: int | None = None,
    cliente_id: int | None = None,
) -> Nota:
    """
    Asigna proveedor o cliente según tipo_operacion. Mantiene consistencia.
    """
    _validate_partner_for_nota_sucursal(
        db,
        tipo_operacion=nota.tipo_operacion,
        sucursal_id=nota.sucursal_id,
        proveedor_id=proveedor_id,
        cliente_id=cliente_id,
    )
    if nota.tipo_operacion == TipoOperacion.compra:
        nota.proveedor_id = proveedor_id
        nota.cliente_id = None
    elif nota.tipo_operacion == TipoOperacion.venta:
        nota.proveedor_id = proveedor_id if proveedor_id and not cliente_id else None
        nota.cliente_id = cliente_id if cliente_id else None
    else:
        raise ValueError("Tipo de operacion no soportado.")
    db.add(nota)
    db.commit()
    db.refresh(nota)
    return nota


def set_tipo_cliente_and_prices(
    db: Session,
    nota: Nota,
    tipo_cliente_map: dict[int, TipoCliente],
    precio_override_map: dict[int, dict[str, Decimal]] | None = None,
) -> Nota:
    """
    Actualiza el tipo_cliente por material y recalcula precios/subtotales.
    """
    for nm in nota.materiales:
        if nm.id in tipo_cliente_map:
            nm.tipo_cliente = tipo_cliente_map[nm.id]
            db.add(nm)
    apply_prices(db, nota)
    if precio_override_map:
        _apply_precio_overrides(nota, precio_override_map)
        _recalc_totals(nota)
    db.commit()
    db.refresh(nota)
    return nota



def edit_note_by_superadmin(
    db: Session,
    nota: Nota,
    *,
    tipo_cliente_map: dict[int, TipoCliente] | None = None,
    kg_override_map: dict[int, tuple[Decimal, Decimal]] | None = None,
    kg_real_override_map: dict[int, Decimal] | None = None,
    subpesaje_map: dict[int, tuple[Decimal, Decimal]] | None = None,
    precio_override_map: dict[int, dict[str, Decimal]] | None = None,
    admin_id: int | None = None,
    comentario: str | None = None,
) -> Nota:
    """
    Edita una nota (solo super admin). Si esta aprobada, registra ajustes en inventario y contabilidad.
    """
    if nota.tipo_operacion not in (TipoOperacion.compra, TipoOperacion.venta):
        raise ValueError("Tipo de operacion no soportado para edicion.")
    old_total = Decimal(str(nota.total_monto or 0))
    old_kg_map = {nm.id: Decimal(str(nm.kg_neto or 0)) for nm in nota.materiales}
    old_real_kg_map = {nm.id: _inventory_qty(nm) for nm in nota.materiales}
    old_tipo_cli_map = {nm.id: nm.tipo_cliente for nm in nota.materiales}

    tipo_cliente_map = tipo_cliente_map or {}
    kg_override_map = kg_override_map or {}
    kg_real_override_map = kg_real_override_map or {}
    subpesaje_map = subpesaje_map or {}

    for nm in nota.materiales:
        if nm.id in tipo_cliente_map:
            nm.tipo_cliente = tipo_cliente_map[nm.id]
        if nm.subpesajes:
            for sp in nm.subpesajes:
                if sp.id in subpesaje_map:
                    peso, desc = subpesaje_map[sp.id]
                    sp.peso_kg = peso
                    sp.descuento_kg = desc
                    db.add(sp)
        elif nm.id in kg_override_map:
            kg_bruto, kg_desc = kg_override_map[nm.id]
            nm.kg_bruto = kg_bruto
            nm.kg_descuento = kg_desc
        db.add(nm)

    for nm in nota.materiales:
        _recalc_material(nm)
        old_kg = old_kg_map.get(nm.id, Decimal("0"))
        old_real = old_real_kg_map.get(nm.id, old_kg)
        current_kg = Decimal(str(nm.kg_neto or 0))
        if nm.id in kg_real_override_map:
            nm.kg_real = _normalize_kg_real(kg_real_override_map[nm.id])
        elif old_real == old_kg:
            nm.kg_real = _normalize_kg_real(current_kg)
        else:
            nm.kg_real = _normalize_kg_real(old_real)
        tipo_cli = nm.tipo_cliente or TipoCliente.regular
        needs_reprice = old_tipo_cli_map.get(nm.id) != tipo_cli or nm.precio_unitario is None
        if needs_reprice:
            tp = (
                db.query(TablaPrecio)
                .filter(
                    TablaPrecio.material_id == nm.material_id,
                    TablaPrecio.tipo_operacion == nota.tipo_operacion,
                    TablaPrecio.tipo_cliente == tipo_cli,
                    TablaPrecio.activo.is_(True),
                )
                .order_by(TablaPrecio.version.desc())
                .first()
            )
            if tp:
                nm.precio_unitario = tp.precio_por_unidad
                nm.version_precio_id = tp.id
            else:
                nm.precio_unitario = None
                nm.version_precio_id = None
        if nm.precio_unitario is not None:
            nm.subtotal = Decimal(str(nm.precio_unitario)) * Decimal(str(nm.kg_neto or 0))
        else:
            nm.subtotal = None
        db.add(nm)

    _apply_precio_overrides(nota, precio_override_map)
    _recalc_totals(nota)
    nota.updated_at = datetime.utcnow()

    new_total = Decimal(str(nota.total_monto or 0))

    if nota.estado == NotaEstado.aprobada:
        comment_base = f"Edicion nota #{nota.id}"
        if comentario:
            comment_base = f"{comment_base}: {comentario}"

        for nm in nota.materiales:
            old_kg = old_real_kg_map.get(nm.id, Decimal("0"))
            new_kg = _inventory_qty(nm)
            delta = new_kg - old_kg
            if delta == 0:
                continue
            stock_delta = delta if nota.tipo_operacion == TipoOperacion.compra else -delta
            inv = _get_or_create_inventario(db, get_inventory_sucursal_id(nota), nm.material_id)
            new_stock = Decimal(str(inv.stock_actual or 0)) + stock_delta
            if new_stock < Decimal("0") and nota.tipo_operacion != TipoOperacion.venta:
                nombre_mat = nm.material.nombre if nm.material else f"Material {nm.material_id}"
                raise ValueError(f"Stock insuficiente para ajustar {nombre_mat}.")
            inv.stock_actual = new_stock
            inv.updated_at = datetime.utcnow()
            mov = InventarioMovimiento(
                inventario_id=inv.id,
                nota_id=nota.id,
                nota_material_id=nm.id,
                tipo="ajuste",
                cantidad_kg=stock_delta,
                saldo_resultante=new_stock,
                comentario=comment_base,
                usuario_id=admin_id,
            )
            db.add(inv)
            db.add(mov)

        delta_total = new_total - old_total
        if delta_total != 0:
            ajuste_monto = delta_total
            if _is_compra_like(nota.tipo_operacion):
                ajuste_monto = -delta_total
            _registrar_movimiento_contable(
                db,
                nota=nota,
                usuario_id=admin_id,
                comentario=comment_base,
                metodo_pago=nota.metodo_pago,
                cuenta_financiera=str(nota.cuenta_financiera_id) if nota.cuenta_financiera_id else None,
                monto=ajuste_monto,
                tipo="ajuste",
            )

    db.commit()
    db.refresh(nota)
    return nota


def create_transfer_notes(
    db: Session,
    *,
    origen_sucursal_id: int,
    destino_sucursal_id: int,
    cliente_id: int,
    proveedor_id: int,
    materiales_payload: Sequence[dict],
    admin_id: int | None,
    comentario: str | None = None,
    origen_nombre: str | None = None,
    destino_nombre: str | None = None,
) -> tuple[Nota, Nota]:
    """
    Crea dos notas aprobadas para una transferencia entre sucursales.
    """
    if not materiales_payload:
        raise ValueError("Debes incluir al menos un material.")
    if not admin_id:
        raise ValueError("Usuario invalido.")
    if origen_sucursal_id == destino_sucursal_id:
        raise ValueError("La sucursal de origen y destino deben ser diferentes.")

    def _build_note(
        *,
        sucursal_id: int,
        tipo_operacion: TipoOperacion,
        partner_id: int,
    ) -> Nota:
        nota = Nota(
            sucursal_id=sucursal_id,
            inventario_sucursal_id=sucursal_id,
            trabajador_id=admin_id,
            admin_id=admin_id,
            tipo_operacion=tipo_operacion,
            estado=NotaEstado.aprobada,
            folio_seq=_next_folio_seq(
                db,
                sucursal_id=sucursal_id,
                tipo_operacion=tipo_operacion,
            ),
        )
        if tipo_operacion == TipoOperacion.compra:
            nota.proveedor_id = partner_id
        else:
            nota.cliente_id = partner_id
        db.add(nota)
        db.flush()

        for idx, mp in enumerate(materiales_payload):
            material_id = mp.get("material_id")
            if not db.get(Material, material_id):
                raise ValueError(f"Material {material_id} no existe")
            tipo_cli_raw = mp.get("tipo_cliente") or TipoCliente.regular
            tipo_cli = tipo_cli_raw if isinstance(tipo_cli_raw, TipoCliente) else TipoCliente(str(tipo_cli_raw))
            kg_bruto = Decimal(str(mp.get("kg_bruto", 0)))
            kg_desc = Decimal(str(mp.get("kg_descuento", 0)))
            kg_neto = kg_bruto - kg_desc
            precio_unitario = Decimal(str(mp.get("precio_unitario", 0)))
            if precio_unitario < 0:
                raise ValueError("El precio unitario no puede ser negativo.")
            nm = NotaMaterial(
                nota=nota,
                material_id=material_id,
                kg_bruto=kg_bruto,
                kg_descuento=kg_desc,
                kg_neto=kg_neto,
                kg_real=kg_neto,
                orden=idx,
                tipo_cliente=tipo_cli,
            )
            nm.precio_unitario = precio_unitario
            nm.version_precio_id = None
            nm.subtotal = precio_unitario * Decimal(str(kg_neto or 0))
            db.add(nm)
        db.flush()

        _recalc_totals(nota)
        nota.metodo_pago = None
        nota.monto_pagado = Decimal("0")
        nota.updated_at = datetime.utcnow()
        db.add(nota)
        return nota

    nota_salida = _build_note(
        sucursal_id=origen_sucursal_id,
        tipo_operacion=TipoOperacion.venta,
        partner_id=cliente_id,
    )
    nota_entrada = _build_note(
        sucursal_id=destino_sucursal_id,
        tipo_operacion=TipoOperacion.compra,
        partner_id=proveedor_id,
    )

    _validar_stock_para_venta(db, nota_salida)

    comment_base = comentario or "Transferencia entre sucursales"
    destino_txt = destino_nombre or "sucursal destino"
    origen_txt = origen_nombre or "sucursal origen"
    nota_salida.comentarios_admin = f"{comment_base}. Destino: {destino_txt}. Nota entrada #{nota_entrada.id}"
    nota_entrada.comentarios_admin = f"{comment_base}. Origen: {origen_txt}. Nota salida #{nota_salida.id}"
    db.add(nota_salida)
    db.add(nota_entrada)

    for nm in nota_salida.materiales:
        _registrar_movimiento_inventario(db, nota=nota_salida, nm=nm, usuario_id=admin_id)
    for nm in nota_entrada.materiales:
        _registrar_movimiento_inventario(db, nota=nota_entrada, nm=nm, usuario_id=admin_id)

    db.commit()
    db.refresh(nota_salida)
    db.refresh(nota_entrada)
    return nota_salida, nota_entrada

