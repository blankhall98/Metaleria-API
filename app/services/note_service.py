# app/services/note_service.py
from datetime import date, datetime
import json
from decimal import Decimal
from typing import Iterable, Sequence

from sqlalchemy.orm import Session
from sqlalchemy import func

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
    NotaOriginal,
)


def _sum_decimal(values: Iterable[Decimal | float | int]) -> Decimal:
    total = Decimal("0")
    for v in values:
        total += Decimal(str(v or 0))
    return total


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
    total_monto = _sum_decimal(m.subtotal for m in nota.materiales if m.subtotal is not None)

    nota.total_kg_bruto = total_bruto
    nota.total_kg_descuento = total_desc
    nota.total_kg_neto = total_neto
    nota.total_monto = total_monto


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
                "precio_unitario": _as_str(nm.precio_unitario),
                "subtotal": _as_str(nm.subtotal),
                "orden": nm.orden,
                "evidencia_url": nm.evidencia_url,
                "subpesajes": subs,
            }
        )
    return {
        "nota_id": nota.id,
        "sucursal_id": nota.sucursal_id,
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
            "monto": _as_str(nota.total_monto),
        },
        "materiales": materials,
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
    letra = "C" if tipo_norm == TipoOperacion.compra else "V"
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


def _normalize_pago_incremental(nota: Nota, monto_pagado: Decimal | None) -> Decimal:
    pagado = Decimal(str(monto_pagado or 0))
    if pagado <= Decimal("0"):
        raise ValueError("El monto del pago debe ser mayor a 0.")
    total = Decimal(str(nota.total_monto or 0))
    acumulado = Decimal(str(nota.monto_pagado or 0))
    saldo = total - acumulado
    if saldo < Decimal("0"):
        saldo = Decimal("0")
    if pagado > saldo:
        raise ValueError("El pago excede el saldo pendiente de la nota.")
    return pagado


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


def create_draft_note(
    db: Session,
    *,
    sucursal_id: int,
    trabajador_id: int,
    tipo_operacion: TipoOperacion,
    materiales_payload: Sequence[dict],
    comentarios_trabajador: str | None = None,
    proveedor_id: int | None = None,
    cliente_id: int | None = None,
) -> Nota:
    """
    Crea una nota en BORRADOR con materiales y subpesajes opcionales.
    materiales_payload: lista de dicts con material_id, kg_bruto, kg_descuento, subpesajes=[{peso_kg, foto_url}]
    """
    nota = Nota(
        sucursal_id=sucursal_id,
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
    else:
        nota.cliente_id = cliente_id
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

    _recalc_totals(nota)
    apply_prices(db, nota)
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
) -> None:
    if nota.tipo_operacion != TipoOperacion.venta:
        return
    for nm in nota.materiales:
        inv = _get_or_create_inventario(db, nota.sucursal_id, nm.material_id)
        disponible = Decimal(str(inv.stock_actual or 0))
        requerido = Decimal(str(nm.kg_neto or 0))
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
    delta = Decimal(str(nm.kg_neto or 0))
    tipo_mov = "compra" if nota.tipo_operacion == TipoOperacion.compra else "venta"
    if tipo_mov == "venta":
        delta = -delta
    inv = _get_or_create_inventario(db, nota.sucursal_id, nm.material_id)
    nuevo_saldo = Decimal(str(inv.stock_actual or 0)) + delta
    # evitar negativos drásticos
    if nuevo_saldo < Decimal("0"):
        nuevo_saldo = Decimal("0")
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
    monto: Decimal | None = None,
    tipo: str | None = None,
) -> None:
    monto_val = Decimal(str(monto)) if monto is not None else Decimal(str(nota.total_monto or 0))
    tipo_mov = tipo or nota.tipo_operacion.value
    mov = MovimientoContable(
        nota_id=nota.id,
        sucursal_id=nota.sucursal_id,
        usuario_id=usuario_id,
        tipo=tipo_mov,
        monto=monto_val,
        metodo_pago=metodo_pago or nota.metodo_pago,
        cuenta_financiera=cuenta_financiera
        or (str(nota.cuenta_financiera_id) if nota.cuenta_financiera_id else None),
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
    comentario: str | None = None,
    commit: bool = True,
    registrar_contable: bool = True,
) -> NotaPago:
    if nota.estado != NotaEstado.aprobada:
        raise ValueError("Solo puedes registrar pagos en notas aprobadas.")
    monto = _normalize_pago_incremental(nota, monto_pagado)
    metodo = (metodo_pago or nota.metodo_pago or "").strip().lower() or None
    if metodo in ("transferencia", "cheque") and not cuenta_financiera:
        raise ValueError("Debes indicar la cuenta para transferencia o cheque.")

    pago = NotaPago(
        nota_id=nota.id,
        usuario_id=usuario_id,
        monto=monto,
        metodo_pago=metodo,
        cuenta_financiera=cuenta_financiera or None,
        comentario=comentario or None,
    )
    nota.monto_pagado = Decimal(str(nota.monto_pagado or 0)) + monto
    nota.updated_at = datetime.utcnow()
    db.add(pago)
    db.add(nota)
    if registrar_contable:
        _registrar_movimiento_contable(
            db,
            nota=nota,
            usuario_id=usuario_id,
            comentario=comentario or f"Pago nota #{nota.id}",
            metodo_pago=metodo,
            cuenta_financiera=cuenta_financiera or None,
            monto=monto,
            tipo="pago",
        )
    if commit:
        db.commit()
        db.refresh(pago)
        db.refresh(nota)
    return pago


def ajustar_stock(
    db: Session,
    *,
    sucursal_id: int,
    material_id: int,
    cantidad_kg: Decimal,
    comentario: str | None,
    usuario_id: int | None,
) -> Inventario:
    """
    Ajuste manual de inventario (positivo suma, negativo resta). Registra movimiento y log contable en 0.
    """
    inv = _get_or_create_inventario(db, sucursal_id, material_id)
    saldo_actual = Decimal(str(inv.stock_actual or 0))
    delta = Decimal(str(cantidad_kg or 0))
    nuevo_saldo = saldo_actual + delta
    if nuevo_saldo < Decimal("0"):
        nuevo_saldo = Decimal("0")
    inv.stock_actual = nuevo_saldo
    inv.updated_at = datetime.utcnow()

    mov = InventarioMovimiento(
        inventario_id=inv.id,
        nota_id=None,
        nota_material_id=None,
        tipo="ajuste",
        cantidad_kg=delta,
        saldo_resultante=nuevo_saldo,
        comentario=comentario or "Ajuste manual",
        usuario_id=usuario_id,
    )
    db.add(inv)
    db.add(mov)

    movc = MovimientoContable(
        nota_id=None,
        sucursal_id=sucursal_id,
        usuario_id=usuario_id,
        tipo="ajuste",
        monto=Decimal("0"),
        comentario=comentario or "Ajuste inventario",
    )
    db.add(movc)
    db.commit()
    db.refresh(inv)
    return inv


def approve_note(
    db: Session,
    nota: Nota,
    *,
    tipo_cliente_map: dict[int, TipoCliente] | None = None,
    admin_id: int | None = None,
    comentarios_admin: str | None = None,
    fecha_caducidad_pago: date | None = None,
    metodo_pago: str | None = None,
    cuenta_financiera: str | None = None,
    monto_pagado: Decimal | None = None,
) -> Nota:
    """
    Aprueba una nota aplicando precios, recalculando totales y registrando inventario/contable.
    """
    if nota.estado not in (NotaEstado.en_revision, NotaEstado.borrador):
        raise ValueError("Solo se puede aprobar desde borrador o en revisión.")
    if tipo_cliente_map:
        for nm in nota.materiales:
            if nm.id in tipo_cliente_map:
                nm.tipo_cliente = tipo_cliente_map[nm.id]
    apply_prices(db, nota)
    _validar_stock_para_venta(db, nota)
    metodo_pago_clean = (metodo_pago or "").strip().lower() or None
    cuenta_id: int | None = None
    if metodo_pago_clean in ("transferencia", "cheque"):
        if cuenta_financiera:
            try:
                cuenta_id = int(cuenta_financiera)
            except (TypeError, ValueError):
                raise ValueError("La cuenta debe ser un número para transferencia o cheque.")
        else:
            raise ValueError("Debes indicar la cuenta para transferencia o cheque.")
    elif metodo_pago_clean == "efectivo":
        cuenta_id = None
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
            cuenta_financiera=str(nota.cuenta_financiera_id) if nota.cuenta_financiera_id else None,
            tipo=nota.tipo_operacion.value,
        )
    pago_inicial: Decimal | None = None
    if metodo_pago_clean == "efectivo":
        pago_inicial = Decimal(str(nota.total_monto or 0))
    elif monto_pagado is not None and Decimal(str(monto_pagado)) > Decimal("0"):
        pago_inicial = monto_pagado
    if pago_inicial is not None and Decimal(str(pago_inicial)) > Decimal("0"):
        add_payment(
            db,
            nota,
            monto_pagado=pago_inicial,
            usuario_id=admin_id,
            metodo_pago=metodo_pago_clean,
            cuenta_financiera=str(cuenta_id) if cuenta_id else None,
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
) -> Nota:
    """
    Cancela una nota aprobada, revierte inventario y registra reversos contables.
    """
    if nota.estado != NotaEstado.aprobada:
        raise ValueError("Solo puedes cancelar notas aprobadas.")
    if nota.tipo_operacion is None:
        raise ValueError("La nota no tiene tipo de operacion valido.")

    base_tipo = nota.tipo_operacion.value
    if not _has_base_contable_movement(db, nota.id, base_tipo):
        _registrar_movimiento_contable(
            db,
            nota=nota,
            usuario_id=admin_id,
            comentario=f"Movimiento base generado para cancelacion nota #{nota.id}",
            metodo_pago=nota.metodo_pago,
            cuenta_financiera=str(nota.cuenta_financiera_id) if nota.cuenta_financiera_id else None,
            tipo=base_tipo,
        )

    comment_base = comentarios_admin or f"Cancelacion nota #{nota.id}"

    for nm in nota.materiales:
        delta = Decimal(str(nm.kg_neto or 0))
        if nota.tipo_operacion == TipoOperacion.compra:
            signed_delta = -delta
        else:
            signed_delta = delta
        inv = _get_or_create_inventario(db, nota.sucursal_id, nm.material_id)
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
        cuenta_financiera=str(nota.cuenta_financiera_id) if nota.cuenta_financiera_id else None,
        monto=Decimal(str(nota.total_monto or 0)) * Decimal("-1"),
        tipo="reverso",
    )

    for pago in nota.pagos or []:
        _registrar_movimiento_contable(
            db,
            nota=nota,
            usuario_id=admin_id,
            comentario=f"Reverso pago nota #{nota.id}",
            metodo_pago=pago.metodo_pago or nota.metodo_pago,
            cuenta_financiera=pago.cuenta_financiera,
            monto=Decimal(str(pago.monto or 0)) * Decimal("-1"),
            tipo="reverso_pago",
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
    if nota.tipo_operacion == TipoOperacion.compra:
        nota.proveedor_id = proveedor_id
        nota.cliente_id = None
    else:
        nota.cliente_id = cliente_id
        nota.proveedor_id = None
    db.add(nota)
    db.commit()
    db.refresh(nota)
    return nota


def set_tipo_cliente_and_prices(
    db: Session,
    nota: Nota,
    tipo_cliente_map: dict[int, TipoCliente],
) -> Nota:
    """
    Actualiza el tipo_cliente por material y recalcula precios/subtotales.
    """
    for nm in nota.materiales:
        if nm.id in tipo_cliente_map:
            nm.tipo_cliente = tipo_cliente_map[nm.id]
            db.add(nm)
    apply_prices(db, nota)
    db.commit()
    db.refresh(nota)
    return nota


def edit_note_by_superadmin(
    db: Session,
    nota: Nota,
    *,
    tipo_cliente_map: dict[int, TipoCliente] | None = None,
    kg_override_map: dict[int, tuple[Decimal, Decimal]] | None = None,
    subpesaje_map: dict[int, tuple[Decimal, Decimal]] | None = None,
    admin_id: int | None = None,
    comentario: str | None = None,
) -> Nota:
    """
    Edita una nota (solo super admin). Si esta aprobada, registra ajustes en inventario y contabilidad.
    """
    old_total = Decimal(str(nota.total_monto or 0))
    old_kg_map = {nm.id: Decimal(str(nm.kg_neto or 0)) for nm in nota.materiales}
    old_tipo_cli_map = {nm.id: nm.tipo_cliente for nm in nota.materiales}

    tipo_cliente_map = tipo_cliente_map or {}
    kg_override_map = kg_override_map or {}
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

    nota.total_kg_bruto = _sum_decimal(m.kg_bruto for m in nota.materiales)
    nota.total_kg_descuento = _sum_decimal(m.kg_descuento for m in nota.materiales)
    nota.total_kg_neto = _sum_decimal(m.kg_neto for m in nota.materiales)
    nota.total_monto = _sum_decimal(m.subtotal for m in nota.materiales if m.subtotal is not None)
    nota.updated_at = datetime.utcnow()

    new_total = Decimal(str(nota.total_monto or 0))
    pagado_actual = Decimal(str(nota.monto_pagado or 0))
    if new_total < pagado_actual:
        raise ValueError("El total no puede ser menor al monto pagado.")

    if nota.estado == NotaEstado.aprobada:
        comment_base = f"Edicion nota #{nota.id}"
        if comentario:
            comment_base = f"{comment_base}: {comentario}"

        for nm in nota.materiales:
            old_kg = old_kg_map.get(nm.id, Decimal("0"))
            new_kg = Decimal(str(nm.kg_neto or 0))
            delta = new_kg - old_kg
            if delta == 0:
                continue
            stock_delta = delta if nota.tipo_operacion == TipoOperacion.compra else -delta
            inv = _get_or_create_inventario(db, nota.sucursal_id, nm.material_id)
            new_stock = Decimal(str(inv.stock_actual or 0)) + stock_delta
            if new_stock < Decimal("0"):
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
            _registrar_movimiento_contable(
                db,
                nota=nota,
                usuario_id=admin_id,
                comentario=comment_base,
                metodo_pago=nota.metodo_pago,
                cuenta_financiera=str(nota.cuenta_financiera_id) if nota.cuenta_financiera_id else None,
                monto=delta_total,
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
            if precio_unitario <= 0:
                raise ValueError("El precio unitario debe ser mayor a 0.")
            nm = NotaMaterial(
                nota=nota,
                material_id=material_id,
                kg_bruto=kg_bruto,
                kg_descuento=kg_desc,
                kg_neto=kg_neto,
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
