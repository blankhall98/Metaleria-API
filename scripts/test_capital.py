"""Pruebas del capital contable diario (punto 4, fase 2).

    python -m scripts.test_capital

Valida las reglas nuevas del motor: el efectivo sale del último corte
CERRADO, las cuentas USD entran solo con TC manual y el comodín neto suma
o resta con reversas compensatorias.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
import app.models  # noqa: F401
from app.models import (
    CapitalAjusteManual,
    CorteCaja,
    CorteCajaEstado,
    CuentaScrap360,
    Sucursal,
    SucursalStatus,
    User,
    UserRole,
)


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


def seed(db):
    suc = Sucursal(nombre="Sucursal Test", estado=SucursalStatus.activa)
    db.add(suc)
    db.flush()
    admin = User(username="a", password_hash="x", nombre_completo="Admin", rol=UserRole.super_admin)
    db.add(admin)
    db.flush()
    return suc, admin


def contexto(db, tc_usd=None):
    from app.web.admin import _build_capital_real_context

    return _build_capital_real_context(db, allowed_suc_ids=None, tc_usd=tc_usd)


def test_efectivo_de_cortes():
    print("C1 · el efectivo sale del último corte CERRADO, no de cuentas")
    db = fresh_session()
    suc, _admin = seed(db)
    db.add_all([
        CorteCaja(sucursal_id=suc.id, fecha=date(2026, 8, 5),
                  estado=CorteCajaEstado.cerrado, saldo_cierre=Decimal("12000")),
        CorteCaja(sucursal_id=suc.id, fecha=date(2026, 8, 6),
                  estado=CorteCajaEstado.cerrado, saldo_cierre=Decimal("15500")),
        CorteCaja(sucursal_id=suc.id, fecha=date(2026, 8, 7),
                  estado=CorteCajaEstado.abierto, saldo_calculado=Decimal("99999")),
        CuentaScrap360(nombre="Caja vieja", tipo="efectivo", moneda="MXN",
                       saldo_inicial=0, saldo_actual=Decimal("7777"), activo=True),
    ])
    db.commit()
    ctx = contexto(db)
    check("usa el corte cerrado más reciente", ctx["saldo_efectivo"] == Decimal("15500"),
          f"= {ctx['saldo_efectivo']}")
    fila = ctx["cuentas_rows"][0]
    check("la cuenta tipo efectivo no suma", fila["es_efectivo"] and fila["valor_mxn"] is None)


def test_usd_con_tc():
    print("C2 · cuentas USD entran solo con TC manual")
    db = fresh_session()
    seed(db)
    db.add_all([
        CuentaScrap360(nombre="Monex USD", tipo="transferencia", moneda="USD",
                       saldo_inicial=0, saldo_actual=Decimal("10000"), activo=True),
        CuentaScrap360(nombre="BBVA MXN", tipo="transferencia", moneda="MXN",
                       saldo_inicial=0, saldo_actual=Decimal("50000"), activo=True),
    ])
    db.commit()
    sin_tc = contexto(db)
    check("sin TC la USD no suma y se advierte",
          sin_tc["saldo_bancos_chequeras"] == Decimal("50000") and sin_tc["usd_sin_tc"],
          f"= {sin_tc['saldo_bancos_chequeras']}")
    con_tc = contexto(db, tc_usd=Decimal("17.5"))
    check("con TC la USD entra convertida",
          con_tc["saldo_bancos_chequeras"] == Decimal("225000") and not con_tc["usd_sin_tc"],
          f"= {con_tc['saldo_bancos_chequeras']}")


def test_comodin():
    print("C3 · comodín manual con reversa compensatoria")
    db = fresh_session()
    suc, admin = seed(db)
    favor = CapitalAjusteManual(monto=Decimal("80000"), concepto="Dinero en proceso",
                                usuario_id=admin.id)
    contra = CapitalAjusteManual(monto=Decimal("-30000"), concepto="Préstamo de socio",
                                 sucursal_id=suc.id, usuario_id=admin.id)
    db.add_all([favor, contra])
    db.commit()
    ctx = contexto(db)
    check("neto del comodín", ctx["comodin_neto"] == Decimal("50000"))
    check("neto positivo suma en activos",
          any(r["label"] == "Comodín manual" and r["amount"] == Decimal("50000") for r in ctx["asset_rows"]))
    check("capital refleja el comodín", ctx["capital_real"] == Decimal("50000"),
          f"= {ctx['capital_real']}")

    # La reversa niega el monto y el neto queda solo con el negativo.
    db.add(CapitalAjusteManual(monto=Decimal("-80000"),
                               concepto="Reversa de: Dinero en proceso",
                               reversal_of_id=favor.id, usuario_id=admin.id))
    db.commit()
    ctx2 = contexto(db)
    check("tras la reversa el neto es -30000", ctx2["comodin_neto"] == Decimal("-30000"))
    check("neto negativo pasa a pasivos",
          any(r["label"] == "Comodín manual" and r["amount"] == Decimal("30000") for r in ctx2["liability_rows"]))
    check("capital final", ctx2["capital_real"] == Decimal("-30000"),
          f"= {ctx2['capital_real']}")


if __name__ == "__main__":
    test_efectivo_de_cortes()
    test_usd_con_tc()
    test_comodin()
    print()
    if FALLAS:
        print(f"{len(FALLAS)} prueba(s) fallaron: {FALLAS}")
        sys.exit(1)
    print("Todas las pruebas del capital en verde.")
