"""Pruebas del motor de neteo y la clasificación de saldos (fase 2, puntos 7 y 8).

    python -m scripts.test_neteo

Corre contra una base SQLite en memoria con el esquema real. Cada prueba
declara el comportamiento que la clienta espera:

- Punto 7: una nota neteada está en ceros en TODAS las vistas — el crédito de
  un socio es global, no de una sucursal, y el reparto FIFO no cambia según
  el filtro con el que se mire.
- Punto 8: el par cliente↔proveedor vive en el bucket de clientes con su
  signo (debe $5,000, le compro $15,000 → el saldo de clientes BAJA $10,000;
  nunca aparece como deudor en proveedores).

Sale distinto de cero si algo falla.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
import app.models  # noqa: F401 - registra todo el metadata
from app.models import (
    AjusteSaldoPartner,
    Cliente,
    Nota,
    NotaEstado,
    Proveedor,
    Sucursal,
    SucursalStatus,
    TipoOperacion,
    User,
    UserRole,
)
from app.services import note_service
from app.services.contabilidad_report_service import _classify_partner_group_balances


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
    suc_a = Sucursal(nombre="Sucursal A", estado=SucursalStatus.activa)
    suc_b = Sucursal(nombre="Sucursal B", estado=SucursalStatus.activa)
    db.add_all([suc_a, suc_b])
    db.flush()
    worker = User(
        username="t",
        password_hash="x",
        nombre_completo="Trabajador Prueba",
        rol=UserRole.trabajador,
        sucursal_id=suc_a.id,
    )
    db.add(worker)
    db.flush()
    return suc_a, suc_b, worker


def nota(db, *, sucursal, worker, tipo, total, pagado=0, proveedor=None, cliente=None, dias=0):
    n = Nota(
        sucursal_id=sucursal.id,
        trabajador_id=worker.id,
        proveedor_id=proveedor.id if proveedor else None,
        cliente_id=cliente.id if cliente else None,
        tipo_operacion=tipo,
        estado=NotaEstado.aprobada,
        total_monto=Decimal(str(total)),
        monto_pagado=Decimal(str(pagado)),
        created_at=datetime(2026, 1, 1) + timedelta(days=dias),
    )
    db.add(n)
    db.flush()
    return n


def test_credito_global_ignora_filtro_de_sucursal():
    """Punto 7: el crédito neteado no desaparece al filtrar por sucursal."""
    print("T1 · crédito global vs. vista filtrada por sucursal")
    db = fresh_session()
    suc_a, suc_b, worker = seed_base(db)
    prov = Proveedor(nombre_completo="Proveedor Neteado", sucursal_id=suc_a.id, activo=True)
    db.add(prov)
    db.flush()

    n1 = nota(db, sucursal=suc_a, worker=worker, tipo=TipoOperacion.compra, total=1000, proveedor=prov, dias=0)
    n2 = nota(db, sucursal=suc_a, worker=worker, tipo=TipoOperacion.compra, total=1000, proveedor=prov, dias=1)
    # El neteo se registró sin sucursal (o desde otra pantalla): es del socio.
    db.add(AjusteSaldoPartner(partner_type="proveedor", partner_id=prov.id, monto=Decimal("-2000"), sucursal_id=None))
    db.flush()

    global_map = note_service.build_effective_note_balance_map(db, [n1, n2])
    check(
        "vista global: ambas notas en ceros",
        all(global_map[n.id]["saldo_pendiente"] == Decimal("0") for n in (n1, n2)),
        f"pendientes: {[str(global_map[n.id]['saldo_pendiente']) for n in (n1, n2)]}",
    )

    filtrado = note_service.build_effective_note_balance_map(
        db, [n1, n2], allowed_suc_ids=[suc_a.id, suc_b.id], sucursal_id=suc_a.id
    )
    check(
        "vista filtrada por sucursal: siguen en ceros",
        all(filtrado[n.id]["saldo_pendiente"] == Decimal("0") for n in (n1, n2)),
        f"pendientes: {[str(filtrado[n.id]['saldo_pendiente']) for n in (n1, n2)]}",
    )


def test_fifo_global_consistente_en_vista_parcial():
    """Punto 7: la vista parcial reparte el crédito igual que la global."""
    print("T2 · reparto FIFO idéntico entre vista global y parcial")
    db = fresh_session()
    suc_a, suc_b, worker = seed_base(db)
    prov = Proveedor(nombre_completo="Proveedor FIFO", sucursal_id=suc_a.id, activo=True)
    db.add(prov)
    db.flush()

    vieja = nota(db, sucursal=suc_a, worker=worker, tipo=TipoOperacion.compra, total=1000, proveedor=prov, dias=0)
    nueva = nota(db, sucursal=suc_b, worker=worker, tipo=TipoOperacion.compra, total=1000, proveedor=prov, dias=5)
    # El crédito alcanza para una sola: FIFO global la asigna a la más vieja.
    db.add(AjusteSaldoPartner(partner_type="proveedor", partner_id=prov.id, monto=Decimal("-1000"), sucursal_id=None))
    db.flush()

    global_map = note_service.build_effective_note_balance_map(db, [vieja, nueva])
    check("global: la vieja queda cubierta", global_map[vieja.id]["saldo_pendiente"] == Decimal("0"))
    check("global: la nueva sigue pendiente", global_map[nueva.id]["saldo_pendiente"] == Decimal("1000"))

    # La vista de la sucursal B solo pide la nota nueva; el crédito ya se
    # consumió globalmente en la vieja, así que la nueva DEBE verse pendiente.
    parcial = note_service.build_effective_note_balance_map(
        db, [nueva], allowed_suc_ids=[suc_a.id, suc_b.id], sucursal_id=suc_b.id
    )
    check(
        "parcial (suc B): la nueva sigue pendiente — el crédito no se reasigna",
        parcial[nueva.id]["saldo_pendiente"] == Decimal("1000"),
        f"pendiente: {parcial[nueva.id]['saldo_pendiente']}",
    )


def test_neteo_por_par_vinculado():
    """Regresión: el crédito del cliente cubre notas del proveedor ligado."""
    print("T3 · el par vinculado se netea como un solo grupo")
    db = fresh_session()
    suc_a, _, worker = seed_base(db)
    cli = Cliente(nombre_completo="Socio Dual", sucursal_id=suc_a.id, activo=True)
    db.add(cli)
    db.flush()
    prov = Proveedor(nombre_completo="Socio Dual", sucursal_id=suc_a.id, activo=True, linked_cliente_id=cli.id)
    db.add(prov)
    db.flush()
    cli.linked_proveedor_id = prov.id
    db.flush()

    compra = nota(db, sucursal=suc_a, worker=worker, tipo=TipoOperacion.compra, total=800, proveedor=prov)
    # Crédito registrado del lado CLIENTE: cargo al cliente (+) compensa la compra.
    db.add(AjusteSaldoPartner(partner_type="cliente", partner_id=cli.id, monto=Decimal("800"), sucursal_id=None))
    db.flush()

    result = note_service.build_effective_note_balance_map(db, [compra])
    check(
        "la compra del proveedor queda cubierta por el crédito del cliente",
        result[compra.id]["saldo_pendiente"] == Decimal("0"),
        f"pendiente: {result[compra.id]['saldo_pendiente']}",
    )


def test_clasificacion_par_como_cliente():
    """Punto 8: el par SIEMPRE en el bucket de clientes, con signo."""
    print("T4 · clasificación del par en bucket clientes (ejemplo de la clienta)")
    # Convención del mapa: vista proveedor (positivo = por pagar al socio).
    # Cliente debe 5,000 (vista prov: −5,000) y le compramos 15,000 (+15,000):
    # neto +10,000 en vista proveedor = el par NOS ES deudor... no: le debemos
    # 10,000 netos. La clienta: eso RESTA del saldo de clientes.
    balances = {("par", 1, 1): Decimal("10000")}
    metadata = {("par", 1, 1): {"has_proveedor": True, "has_cliente": True}}
    tot = _classify_partner_group_balances(balances, group_metadata=metadata)
    check(
        "por cobrar a clientes baja 10,000",
        tot["total_por_cobrar_clientes"] == Decimal("-10000"),
        f"por_cobrar={tot['total_por_cobrar_clientes']}",
    )
    check("nada en por pagar a proveedores", tot["total_por_pagar_proveedores"] == Decimal("0"),
          f"por_pagar={tot['total_por_pagar_proveedores']}")

    # Sentido contrario: el par nos debe 4,000 (vista prov: −4,000) → suma a clientes.
    balances2 = {("par", 2, 2): Decimal("-4000")}
    metadata2 = {("par", 2, 2): {"has_proveedor": True, "has_cliente": True}}
    tot2 = _classify_partner_group_balances(balances2, group_metadata=metadata2)
    check(
        "el par que nos debe suma al por cobrar de clientes",
        tot2["total_por_cobrar_clientes"] == Decimal("4000"),
        f"por_cobrar={tot2['total_por_cobrar_clientes']}",
    )
    check("proveedores intacto", tot2["total_por_pagar_proveedores"] == Decimal("0"))


def test_no_vinculado_conserva_su_bucket():
    """Punto 8: un socio sin vínculo no cambia de bucket al cambiar de signo."""
    print("T5 · el no vinculado queda en su bucket con el signo")
    # Proveedor puro con saldo a favor nuestro (vista prov: −3,000): queda en
    # el lado proveedor como favor de la empresa, no brinca a clientes.
    tot = _classify_partner_group_balances(
        {("proveedor", 9): Decimal("-3000")},
        group_metadata={("proveedor", 9): {"has_proveedor": True, "has_cliente": False}},
    )
    check("favor de la empresa en el lado proveedor", tot["saldo_favor_empresa"] == Decimal("3000"))
    check("clientes intacto", tot["total_por_cobrar_clientes"] == Decimal("0"))


if __name__ == "__main__":
    test_credito_global_ignora_filtro_de_sucursal()
    test_fifo_global_consistente_en_vista_parcial()
    test_neteo_por_par_vinculado()
    test_clasificacion_par_como_cliente()
    test_no_vinculado_conserva_su_bucket()
    print()
    if FALLAS:
        print(f"{len(FALLAS)} prueba(s) fallaron: {FALLAS}")
        sys.exit(1)
    print("Todas las pruebas del neteo en verde.")
