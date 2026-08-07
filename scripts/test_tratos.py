"""Pruebas de los tratos de venta de contenedores (punto 3, fase 2).

    python -m scripts.test_tratos

La cadena de cálculo replica el Excel del cliente (PEDIDOS JORGE ALFARO);
estas pruebas fijan cada fórmula con valores conocidos y cuidan la regla de
oro del módulo: los kilos vendidos se leen de notas aprobadas, jamás se
escriben de vuelta.
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
    Cliente,
    Material,
    Nota,
    NotaEstado,
    Sucursal,
    SucursalStatus,
    TipoOperacion,
    TratoVentaEstado,
    User,
    UserRole,
)
from app.services import trato_service
from app.services.trato_service import LB_POR_KG, calcular_contenedor


FALLAS: list[str] = []


def check(nombre: str, cond: bool, detalle: str = "") -> None:
    if cond:
        print(f"  ok    {nombre}")
    else:
        print(f"  FALLA {nombre}  {detalle}")
        FALLAS.append(nombre)


def casi(a: Decimal | None, b: str, tol: str = "0.01") -> bool:
    if a is None:
        return False
    return abs(a - Decimal(b)) <= Decimal(tol)


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
    cliente = Cliente(nombre_completo="Jorge Alfaro", sucursal_id=suc.id, activo=True)
    cobre = Material(nombre="Cobre")
    db.add_all([admin, cliente, cobre])
    db.flush()
    return suc, admin, cliente, cobre


def test_cadena_lme():
    print("T1 · cadena LME - precio/lb - totales (fórmulas del Excel)")
    # LME 9,000 USD/ton, descuento 0.665, 25,000 kg, TC 17.5, premio 5.5 %
    calc = calcular_contenedor(
        kg=Decimal("25000"),
        lme_usd_ton=Decimal("9000"),
        descuento_factor=Decimal("0.665"),
        tc1=Decimal("17.5"),
        premio_pct=Decimal("5.5"),
    )
    # precio_lb = (9000 × 0.665 / 1000) / 2.204623 = 5.985 / 2.204623
    check("precio por libra", casi(calc["precio_lb_usd"], "2.71475", "0.0001"),
          f"= {calc['precio_lb_usd']}")
    check("libras", calc["libras"] == Decimal("25000") * LB_POR_KG)
    # total_usd = libras × precio_lb = kg × (LME × desc / 1000) = 25000 × 5.985
    check("total USD", casi(calc["total_usd"], "149625.00"), f"= {calc['total_usd']}")
    # precio_kg_mxn = precio_lb × 2.204623 × 17.5 = 5.985 × 17.5
    check("precio kg MXN", casi(calc["precio_kg_mxn"], "104.7375", "0.0001"),
          f"= {calc['precio_kg_mxn']}")
    # premio 5.5 % y precio con premio
    check("premio", casi(calc["premio_monto"], "5.760563", "0.0001"))
    check("precio con premio", casi(calc["precio_con_premio"], "110.498063", "0.0001"))
    # total_venta = 25000 × 110.498… y total_pesos = total_usd × TC
    check("total venta MXN", casi(calc["total_venta"], "2762451.56", "0.05"),
          f"= {calc['total_venta']}")
    check("total pesos sin partir", casi(calc["total_pesos"], "2618437.50", "0.01"))


def test_tc_partido_y_ebony():
    print("T2 · pago partido en dos TC y precio directo sin LME (EBONY)")
    calc = calcular_contenedor(
        kg=Decimal("10000"),
        lme_usd_ton=Decimal("9000"),
        descuento_factor=Decimal("0.665"),
        tc1=Decimal("17.0"), usd_tc1=Decimal("40000"),
        tc2=Decimal("18.0"), usd_tc2=Decimal("19850"),
        premio_pct=Decimal("5.5"),
    )
    # total_pesos = 40000×17 + 19850×18 = 680,000 + 357,300
    check("total pesos partido", casi(calc["total_pesos"], "1037300.00"))
    # TC efectivo ponderado = 1,037,300 / 59,850
    check("TC ponderado", casi(calc["tc_efectivo"], "17.33165", "0.0001"),
          f"= {calc['tc_efectivo']}")
    # el precio por kg usa el TC ponderado real del pago
    esperado = Decimal("5.985") * calc["tc_efectivo"]
    check("precio kg con TC ponderado", casi(calc["precio_kg_mxn"], str(esperado), "0.0001"))

    ebony = calcular_contenedor(
        kg=Decimal("5000"),
        precio_lb_usd=Decimal("1.25"),
        tc1=Decimal("17.5"),
        premio_pct=Decimal("6"),
    )
    check("EBONY usa el precio directo", ebony["precio_lb_usd"] == Decimal("1.25"))
    # precio_kg = 1.25 × 2.204623 × 17.5 = 48.2261…; premio 6 %
    check("EBONY precio kg", casi(ebony["precio_kg_mxn"], "48.226128", "0.0001"))
    check("EBONY premio 6 %", casi(ebony["premio_monto"], "2.893568", "0.0001"))

    vacio = calcular_contenedor(kg=Decimal("0"), lme_usd_ton=Decimal("9000"),
                                descuento_factor=Decimal("0.665"), tc1=Decimal("17.5"),
                                premio_pct=Decimal("5.5"))
    check("contenedor sin cargar vale 0", vacio["total_usd"] == 0 and vacio["total_venta"] == 0)


def test_trato_y_notas():
    print("T3 · trato: kilos vendidos leídos de notas aprobadas del cliente")
    db = fresh_session()
    suc, admin, cliente, cobre = seed_base(db)
    trato = trato_service.create_trato(
        db, cliente_id=cliente.id, material_id=cobre.id, usuario_id=admin.id,
        contrato="V26-01", fecha_po=date(2026, 8, 1), fecha_vencimiento=date(2026, 12, 31),
        kg_tratados=Decimal("75000"), premio_pct=Decimal("5.5"), comentarios=None,
    )
    check("el trato nace abierto", trato.estado == TratoVentaEstado.abierto)

    trato_service.add_contenedor(db, trato_id=trato.id, campos={
        "orden": 1, "numero_contenedor": "MSKU-1", "kg": Decimal("25000"),
        "lme_usd_ton": Decimal("9000"), "descuento_factor": Decimal("0.665"),
        "tc1": Decimal("17.5"),
    })
    trato_service.add_contenedor(db, trato_id=trato.id, campos={
        "orden": 2, "numero_contenedor": "MSKU-2", "kg": Decimal("0"),
        "premio_pct": Decimal("6"),
    })

    nota_ok = Nota(
        sucursal_id=suc.id, trabajador_id=admin.id, cliente_id=cliente.id, tipo_operacion=TipoOperacion.venta,
        estado=NotaEstado.aprobada,
        total_kg_neto=Decimal("25000"), total_kg_real=Decimal("25050"),
    )
    nota_borrador = Nota(
        sucursal_id=suc.id, trabajador_id=admin.id, cliente_id=cliente.id, tipo_operacion=TipoOperacion.venta,
        estado=NotaEstado.borrador,
        total_kg_neto=Decimal("10000"),
    )
    otra_compra = Nota(
        sucursal_id=suc.id, trabajador_id=admin.id, cliente_id=cliente.id, tipo_operacion=TipoOperacion.compra,
        estado=NotaEstado.aprobada,
        total_kg_neto=Decimal("500"),
    )
    db.add_all([nota_ok, nota_borrador, otra_compra])
    db.commit()

    trato_service.link_nota(db, trato_id=trato.id, nota_id=nota_ok.id)
    for nota_mala, motivo in ((nota_borrador, "borrador"), (otra_compra, "compra")):
        try:
            trato_service.link_nota(db, trato_id=trato.id, nota_id=nota_mala.id)
            check(f"rechaza vincular nota {motivo}", False)
        except ValueError:
            check(f"rechaza vincular nota {motivo}", True)
    try:
        trato_service.link_nota(db, trato_id=trato.id, nota_id=nota_ok.id)
        check("rechaza doble vínculo", False)
    except ValueError:
        check("rechaza doble vínculo", True)

    db.refresh(trato)
    resumen = trato_service.resumen_trato(db, trato)
    check("kg tratados", resumen["kg_tratados"] == Decimal("75000"))
    check("kg en contenedores", resumen["kg_contenedores"] == Decimal("25000"))
    check("kg vendidos solo de aprobadas", resumen["kg_vendidos"] == Decimal("25000"),
          f"= {resumen['kg_vendidos']}")
    check("kg restantes", resumen["kg_restantes"] == Decimal("50000"))
    check("la nota no se tocó", Decimal(str(nota_ok.total_kg_neto)) == Decimal("25000"))

    trato = trato_service.set_completado(db, trato_id=trato.id, completado=True, usuario_id=admin.id)
    check("completada con auditoría",
          trato.estado == TratoVentaEstado.completado and trato.completado_at is not None)
    trato = trato_service.set_completado(db, trato_id=trato.id, completado=False, usuario_id=admin.id)
    check("reabrir limpia la auditoría",
          trato.estado == TratoVentaEstado.abierto and trato.completado_at is None)

    try:
        trato_service.delete_trato(db, trato_id=trato.id)
        check("no se elimina un trato con contenido", False)
    except ValueError:
        check("no se elimina un trato con contenido", True)


if __name__ == "__main__":
    test_cadena_lme()
    test_tc_partido_y_ebony()
    test_trato_y_notas()
    print()
    if FALLAS:
        print(f"{len(FALLAS)} prueba(s) fallaron: {FALLAS}")
        sys.exit(1)
    print("Todas las pruebas de tratos en verde.")
