"""Pruebas de las reversas del punto 12 (fase 2).

    python -m scripts.test_reversas

SQLite en memoria con el esquema real. Cada flujo verifica el efecto completo
de deshacer: el registro queda marcado, los saldos se restauran y una segunda
reversa se rechaza.
"""

from __future__ import annotations

import sys
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
import app.models  # noqa: F401
from app.models import (
    AjusteSaldoPartner,
    Comisionario,
    ComisionarioNota,
    ComisionarioNotaEstado,
    CuentaScrap360,
    CuentaScrap360Movimiento,
    Proveedor,
    Sucursal,
    SucursalStatus,
    User,
    UserRole,
)
from app.services import comision_service, note_service


FALLAS: list[str] = []


def check(nombre: str, cond: bool, detalle: str = "") -> None:
    if cond:
        print(f"  ok    {nombre}")
    else:
        print(f"  FALLA {nombre}  {detalle}")
        FALLAS.append(nombre)


def fresh_session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False)()


def seed_base(db):
    suc = Sucursal(nombre="Sucursal Test", estado=SucursalStatus.activa)
    db.add(suc)
    db.flush()
    admin = User(
        username="a",
        password_hash="x",
        nombre_completo="Admin Prueba",
        rol=UserRole.super_admin,
    )
    db.add(admin)
    db.flush()
    return suc, admin


def test_reversa_ajuste_partner():
    print("R1 · deshacer ajuste manual de saldo de socio (compensatorio)")
    db = fresh_session()
    suc, admin = seed_base(db)
    prov = Proveedor(nombre_completo="Prov Ajustado", sucursal_id=suc.id, activo=True)
    db.add(prov)
    db.flush()
    ajuste = AjusteSaldoPartner(
        partner_type="proveedor", partner_id=prov.id, sucursal_id=suc.id,
        monto=Decimal("500"), comentario="Saldo inicial",
    )
    db.add(ajuste)
    db.commit()

    reversa = note_service.revert_partner_adjustment(db, ajuste_id=ajuste.id, usuario_id=admin.id)
    check("la reversa niega el monto", Decimal(str(reversa.monto)) == Decimal("-500"))
    check("la reversa enlaza el original", reversa.reversal_of_id == ajuste.id)
    db.refresh(ajuste)
    check("el original queda marcado", ajuste.reverted_at is not None and ajuste.reverted_by_user_id == admin.id)
    total = note_service.get_partner_adjustment_totals_map(db, {("proveedor", prov.id)})
    check("el neto del socio vuelve a cero", total[("proveedor", prov.id)] == Decimal("0"),
          f"neto={total[('proveedor', prov.id)]}")

    try:
        note_service.revert_partner_adjustment(db, ajuste_id=ajuste.id, usuario_id=admin.id)
        check("segunda reversa rechazada", False)
    except ValueError:
        check("segunda reversa rechazada", True)
    try:
        note_service.revert_partner_adjustment(db, ajuste_id=reversa.id, usuario_id=admin.id)
        check("no se revierte una reversa", False)
    except ValueError:
        check("no se revierte una reversa", True)


def test_reversa_pago_comisionista():
    print("R2 · deshacer pago a comisionista (zero-out + Scrap360)")
    db = fresh_session()
    suc, admin = seed_base(db)
    com = Comisionario(nombre_completo="Comisionista", sucursal_id=suc.id, activo=True)
    cuenta = CuentaScrap360(nombre="Caja Fuerte", tipo="efectivo", saldo_inicial=Decimal("1000"), saldo_actual=Decimal("1000"), activo=True)
    db.add_all([com, cuenta])
    db.flush()
    nota = ComisionarioNota(
        comisionario_id=com.id, sucursal_id=suc.id,
        estado=ComisionarioNotaEstado.aprobada,
        total_monto=Decimal("300"), monto_pagado=Decimal("0"),
    )
    db.add(nota)
    db.commit()

    pagos = comision_service.pay_comisionario_fifo(
        db, comisionario_id=com.id, monto=Decimal("300"), usuario_id=admin.id,
        metodo_pago="efectivo", cuenta_financiera=None,
        cuenta_scrap360_id=cuenta.id, comentario=None,
    )
    db.refresh(nota); db.refresh(cuenta)
    check("el pago dejó la nota saldada", Decimal(str(nota.monto_pagado)) == Decimal("300"))
    check("el pago descontó la cuenta", Decimal(str(cuenta.saldo_actual)) == Decimal("700"))

    pago = pagos[0]
    comision_service.revert_comisionario_pago(db, pago_id=pago.id, usuario_id=admin.id)
    db.refresh(nota); db.refresh(cuenta); db.refresh(pago)
    check("el saldo de la nota se restaura", Decimal(str(nota.monto_pagado)) == Decimal("0"))
    check("la cuenta se reingresa", Decimal(str(cuenta.saldo_actual)) == Decimal("1000"),
          f"saldo={cuenta.saldo_actual}")
    check("el pago queda en ceros con etiqueta", Decimal(str(pago.monto)) == Decimal("0") and "DESHECHO" in (pago.comentario or ""))
    check("el pago queda marcado", pago.reverted_at is not None)
    mov_reversa = (
        db.query(CuentaScrap360Movimiento)
        .filter(CuentaScrap360Movimiento.tipo == "ingreso")
        .first()
    )
    check("existe el reingreso en la bitácora", mov_reversa is not None)

    try:
        comision_service.revert_comisionario_pago(db, pago_id=pago.id, usuario_id=admin.id)
        check("segunda reversa rechazada", False)
    except ValueError:
        check("segunda reversa rechazada", True)


def test_reversa_movimiento_scrap360():
    print("R3 · deshacer movimiento manual de tesorería (compensatorio)")
    from app.web.admin import _apply_scrap360_adjustment, _revert_scrap360_movimiento

    db = fresh_session()
    _suc, admin = seed_base(db)
    cuenta = CuentaScrap360(nombre="Chequera", tipo="transferencia", saldo_inicial=Decimal("0"), saldo_actual=Decimal("0"), activo=True)
    db.add(cuenta)
    db.flush()
    mov = _apply_scrap360_adjustment(db, cuenta=cuenta, monto=Decimal("250"), comentario="Depósito", usuario_id=admin.id)
    db.commit()
    check("el depósito subió el saldo", Decimal(str(cuenta.saldo_actual)) == Decimal("250"))

    reversa = _revert_scrap360_movimiento(db, movimiento=mov, usuario_id=admin.id)
    db.commit()
    db.refresh(cuenta); db.refresh(mov)
    check("el saldo se restaura", Decimal(str(cuenta.saldo_actual)) == Decimal("0"))
    check("la reversa enlaza el original", reversa.reversal_of_id == mov.id)
    check("el original queda marcado", mov.reverted_at is not None)

    try:
        _revert_scrap360_movimiento(db, movimiento=mov, usuario_id=admin.id)
        check("segunda reversa rechazada", False)
    except ValueError:
        check("segunda reversa rechazada", True)
    try:
        _revert_scrap360_movimiento(db, movimiento=reversa, usuario_id=admin.id)
        check("no se revierte una reversa", False)
    except ValueError:
        check("no se revierte una reversa", True)


if __name__ == "__main__":
    test_reversa_ajuste_partner()
    test_reversa_pago_comisionista()
    test_reversa_movimiento_scrap360()
    print()
    if FALLAS:
        print(f"{len(FALLAS)} prueba(s) fallaron: {FALLAS}")
        sys.exit(1)
    print("Todas las pruebas de reversas en verde.")
