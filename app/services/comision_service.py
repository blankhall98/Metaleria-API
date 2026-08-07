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
    if comisionario.sucursal_id and int(sucursal_id) != int(comisionario.sucursal_id):
        raise ValueError("La nota debe registrarse en la sucursal asignada al comisionario.")
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


def pay_comisionario_fifo(
    db: Session,
    *,
    comisionario_id: int,
    monto: Decimal,
    usuario_id: int | None,
    metodo_pago: str | None,
    cuenta_financiera: str | None,
    cuenta_scrap360_id: int | None,
    comentario: str | None,
) -> list[ComisionarioPago]:
    """Aplica un pago del comisionista a sus notas más antiguas (punto 6, fase 2).

    El monto se consume nota por nota en orden (created_at, id) ascendente hasta
    agotarse, generando un ComisionarioPago por nota consumida — así cada abono
    conserva su rastro por nota, igual que el pago individual. Todo ocurre en
    UNA transacción: o se aplican todos los pagos o ninguno.
    """
    comisionario = db.get(Comisionario, comisionario_id)
    if not comisionario:
        raise ValueError("Comisionario no encontrado.")

    monto_val = _safe_decimal(monto)
    if monto_val <= Decimal("0"):
        raise ValueError("El monto del pago debe ser mayor a 0.")

    metodo = (metodo_pago or "").strip().lower() or None
    if not metodo:
        raise ValueError("Selecciona un método de pago.")

    cuenta_id = None
    cuenta_label = None
    if metodo in ("transferencia", "cheque"):
        if not cuenta_financiera:
            raise ValueError("Debes indicar la cuenta para transferencia o cheque.")
        try:
            cuenta_id = int(str(cuenta_financiera).strip())
        except (TypeError, ValueError):
            raise ValueError("Selecciona una cuenta válida.")
        cuenta = _validate_cuenta_comisionario(db, comisionario_id, cuenta_id)
        cuenta_label = cuenta.display_label

    notas_pendientes = (
        db.query(ComisionarioNota)
        .filter(
            ComisionarioNota.comisionario_id == comisionario_id,
            ComisionarioNota.estado == ComisionarioNotaEstado.aprobada,
        )
        .order_by(ComisionarioNota.created_at.asc(), ComisionarioNota.id.asc())
        .all()
    )
    notas_con_saldo = [
        nota
        for nota in notas_pendientes
        if _safe_decimal(nota.total_monto) - _safe_decimal(nota.monto_pagado) > Decimal("0")
    ]
    saldo_total = sum(
        (_safe_decimal(n.total_monto) - _safe_decimal(n.monto_pagado) for n in notas_con_saldo),
        Decimal("0"),
    )
    if not notas_con_saldo:
        raise ValueError("El comisionista no tiene notas con saldo pendiente.")
    if monto_val > saldo_total:
        raise ValueError(
            f"El pago (${monto_val:,.2f}) excede el saldo total pendiente (${saldo_total:,.2f})."
        )

    restante = monto_val
    pagos: list[ComisionarioPago] = []
    for nota in notas_con_saldo:
        if restante <= Decimal("0"):
            break
        saldo_nota = _safe_decimal(nota.total_monto) - _safe_decimal(nota.monto_pagado)
        aplica = saldo_nota if saldo_nota < restante else restante

        pago = ComisionarioPago(
            nota_id=nota.id,
            usuario_id=usuario_id,
            cuenta_id=cuenta_id,
            cuenta_scrap360_id=cuenta_scrap360_id,
            monto=aplica,
            metodo_pago=metodo,
            cuenta_financiera=cuenta_label or None,
            comentario=comentario or None,
        )
        nota.monto_pagado = _safe_decimal(nota.monto_pagado) + aplica
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
                monto=aplica,
                comentario=comentario or f"Pago comisión (más antigua primero) nota #{nota.id}",
            )

        restante -= aplica
        pagos.append(pago)

    db.commit()
    for pago in pagos:
        db.refresh(pago)
    return pagos


def revert_comisionario_pago(
    db: Session,
    *,
    pago_id: int,
    usuario_id: int | None,
) -> ComisionarioPago:
    """Deshace un pago a comisionista (punto 12, fase 2).

    Mismo mecanismo que los pagos de nota: zero-out — el monto pasa a 0 con
    la etiqueta DESHECHO en el comentario (las agregaciones que suman pagos
    quedan correctas sin filtros nuevos) y el trío reverted_* deja candado y
    auditoría. El saldo de la nota se restaura y, si el pago descontó una
    Cuenta Scrap360, se reingresa con un movimiento compensatorio.
    Commit incluido.
    """
    pago = db.get(ComisionarioPago, pago_id)
    if not pago:
        raise ValueError("Pago no encontrado.")
    if pago.reverted_at:
        raise ValueError("Este pago ya fue deshecho.")
    monto = _safe_decimal(pago.monto)
    if monto <= Decimal("0"):
        raise ValueError("Este pago ya está en ceros.")

    nota = db.get(ComisionarioNota, pago.nota_id)
    if not nota:
        raise ValueError("La nota del pago no existe.")
    pagado_actual = _safe_decimal(nota.monto_pagado)
    if pagado_actual < monto:
        raise ValueError(
            "El pago ya no puede deshacerse: la nota tiene menos pagado que el monto del pago."
        )

    nota.monto_pagado = pagado_actual - monto
    nota.updated_at = datetime.utcnow()

    if pago.cuenta_scrap360_id:
        cuenta = db.get(CuentaScrap360, pago.cuenta_scrap360_id)
        if cuenta:
            nuevo_saldo = _safe_decimal(cuenta.saldo_actual) + monto
            cuenta.saldo_actual = nuevo_saldo
            cuenta.updated_at = datetime.utcnow()
            db.add(cuenta)
            db.add(
                CuentaScrap360Movimiento(
                    cuenta_id=cuenta.id,
                    usuario_id=usuario_id,
                    tipo="ingreso",
                    monto=monto,
                    saldo_resultante=nuevo_saldo,
                    comentario=f"Reversa de pago de comisión #{pago.id} (nota #{nota.id})",
                )
            )

    etiqueta = f"DESHECHO ${monto:,.2f}"
    pago.comentario = f"{etiqueta} — {pago.comentario}" if pago.comentario else etiqueta
    pago.monto = Decimal("0")
    pago.reverted_at = datetime.utcnow()
    pago.reverted_by_user_id = usuario_id

    db.add(nota)
    db.add(pago)
    db.commit()
    db.refresh(pago)
    return pago


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
