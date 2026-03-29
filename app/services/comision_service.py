# app/services/comision_service.py
from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from app.models import (
    Comisionario,
    ComisionarioNota,
    ComisionarioNotaMaterial,
    ComisionarioNotaEstado,
    ComisionarioPago,
    Cuenta,
    CuentaScrap360,
    CuentaScrap360Movimiento,
    Material,
)


def _safe_decimal(value: object) -> Decimal:
    try:
        return Decimal(str(value or 0))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _normalize_pago(nota: ComisionarioNota, monto: Decimal) -> Decimal:
    monto_val = _safe_decimal(monto)
    if monto_val <= Decimal("0"):
        raise ValueError("El monto del pago debe ser mayor a 0.")
    total = _safe_decimal(nota.total_monto)
    acumulado = _safe_decimal(nota.monto_pagado)
    saldo = total - acumulado
    if saldo < Decimal("0"):
        saldo = Decimal("0")
    if monto_val > saldo:
        raise ValueError("El pago excede el saldo pendiente.")
    return monto_val


def _validate_cuenta_comisionario(db: Session, comisionario_id: int, cuenta_id: int) -> Cuenta:
    cuenta = db.get(Cuenta, cuenta_id)
    if not cuenta or not cuenta.activo:
        raise ValueError("La cuenta seleccionada no existe o esta inactiva.")
    if cuenta.comisionario_id != comisionario_id:
        raise ValueError("La cuenta seleccionada no pertenece al comisionario.")
    return cuenta


def _validate_cuenta_scrap360(
    db: Session,
    *,
    cuenta_id: int,
    sucursal_id: int | None,
    metodo_pago: str | None,
) -> CuentaScrap360:
    cuenta = db.get(CuentaScrap360, cuenta_id)
    if not cuenta or not cuenta.activo:
        raise ValueError("La cuenta Scrap360 seleccionada no existe o esta inactiva.")
    if cuenta.sucursales and sucursal_id:
        allowed = {s.id for s in cuenta.sucursales}
        if sucursal_id not in allowed:
            raise ValueError("La cuenta Scrap360 no esta vinculada a esta sucursal.")
    if metodo_pago:
        metodo = metodo_pago.strip().lower()
        tipo = (cuenta.tipo or "").strip().lower()
        tipo_map = {
            "transferencia": "transferencia",
            "cheque": "cheques",
            "efectivo": "efectivo",
        }
        expected = tipo_map.get(metodo)
        if expected and tipo and tipo != expected:
            raise ValueError("La cuenta Scrap360 no coincide con el metodo de pago.")
    return cuenta


def _registrar_movimiento_scrap360(
    db: Session,
    *,
    cuenta: CuentaScrap360,
    usuario_id: int | None,
    monto: Decimal,
    comentario: str | None,
) -> CuentaScrap360Movimiento:
    monto_abs = abs(_safe_decimal(monto))
    saldo_actual = _safe_decimal(cuenta.saldo_actual)
    nuevo_saldo = saldo_actual - monto_abs
    cuenta.saldo_actual = nuevo_saldo
    cuenta.updated_at = datetime.utcnow()
    mov = CuentaScrap360Movimiento(
        cuenta_id=cuenta.id,
        nota_id=None,
        nota_pago_id=None,
        usuario_id=usuario_id,
        tipo="egreso",
        monto=monto_abs,
        saldo_resultante=nuevo_saldo,
        comentario=comentario or None,
    )
    db.add(cuenta)
    db.add(mov)
    return mov


def create_comisionario_nota(
    db: Session,
    *,
    comisionario_id: int,
    sucursal_id: int | None,
    admin_id: int | None,
    comentario: str | None,
    materiales_payload: list[dict],
) -> ComisionarioNota:
    comisionario = db.get(Comisionario, comisionario_id)
    if not comisionario:
        raise ValueError("Comisionario no encontrado.")
    if not sucursal_id:
        raise ValueError("Debes seleccionar una sucursal para la nota de comisionario.")
    if not materiales_payload:
        raise ValueError("Debes agregar al menos un material.")

    nota = ComisionarioNota(
        comisionario_id=comisionario_id,
        sucursal_id=sucursal_id,
        admin_id=admin_id,
        estado=ComisionarioNotaEstado.aprobada,
        comentarios_admin=comentario or None,
    )
    total_kg = Decimal("0")
    total_monto = Decimal("0")

    for item in materiales_payload:
        material_id = int(item.get("material_id"))
        material = db.get(Material, material_id)
        if not material:
            raise ValueError("Material no encontrado.")
        kg_neto = _safe_decimal(item.get("kg_neto"))
        precio_por_kg = _safe_decimal(item.get("precio_por_kg"))
        if kg_neto <= Decimal("0") or precio_por_kg < Decimal("0"):
            raise ValueError("Kg y precio por kg deben ser mayores o iguales a 0.")
        subtotal = (kg_neto * precio_por_kg).quantize(Decimal("0.01"))
        total_kg += kg_neto
        total_monto += subtotal
        nota.materiales.append(
            ComisionarioNotaMaterial(
                material_id=material_id,
                kg_neto=kg_neto,
                precio_por_kg=precio_por_kg,
                subtotal=subtotal,
            )
        )

    nota.total_kg = total_kg
    nota.total_monto = total_monto
    nota.monto_pagado = Decimal("0")

    db.add(nota)
    db.commit()
    db.refresh(nota)
    return nota


def add_comisionario_pago(
    db: Session,
    *,
    nota: ComisionarioNota,
    monto: Decimal,
    usuario_id: int | None,
    metodo_pago: str | None,
    cuenta_financiera: str | None,
    cuenta_scrap360_id: int | None,
    comentario: str | None,
) -> ComisionarioPago:
    if nota.estado != ComisionarioNotaEstado.aprobada:
        raise ValueError("Solo puedes registrar pagos en notas aprobadas.")

    monto_val = _normalize_pago(nota, monto)
    metodo = (metodo_pago or "").strip().lower() or None
    if not metodo:
        raise ValueError("Selecciona un metodo de pago.")

    cuenta_id = None
    cuenta_label = None
    if metodo in ("transferencia", "cheque"):
        if not cuenta_financiera:
            raise ValueError("Debes indicar la cuenta para transferencia o cheque.")
        try:
            cuenta_id = int(str(cuenta_financiera).strip())
        except (TypeError, ValueError):
            raise ValueError("Selecciona una cuenta valida.")
        cuenta = _validate_cuenta_comisionario(db, nota.comisionario_id, cuenta_id)
        cuenta_label = cuenta.display_label
    else:
        cuenta_financiera = None

    pago = ComisionarioPago(
        nota_id=nota.id,
        usuario_id=usuario_id,
        cuenta_id=cuenta_id,
        cuenta_scrap360_id=cuenta_scrap360_id,
        monto=monto_val,
        metodo_pago=metodo,
        cuenta_financiera=cuenta_label or (cuenta_financiera or None),
        comentario=comentario or None,
    )
    nota.monto_pagado = _safe_decimal(nota.monto_pagado) + monto_val
    nota.updated_at = datetime.utcnow()
    db.add(pago)
    db.add(nota)
    db.flush()

    if cuenta_scrap360_id:
        cuenta_scrap = _validate_cuenta_scrap360(
            db,
            cuenta_id=cuenta_scrap360_id,
            sucursal_id=nota.sucursal_id,
            metodo_pago=metodo,
        )
        _registrar_movimiento_scrap360(
            db,
            cuenta=cuenta_scrap,
            usuario_id=usuario_id,
            monto=monto_val,
            comentario=comentario or f"Pago comision nota #{nota.id}",
        )

    db.commit()
    db.refresh(pago)
    db.refresh(nota)
    return pago
