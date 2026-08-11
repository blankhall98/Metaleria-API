"""Pruebas del neteo interno del socio: sus ventas cancelan sus compras.

    python -m scripts.test_neteo_mutuo

Corre contra una base SQLite en memoria con el esquema real.

El defecto que motivó estas pruebas (METALES YAIR, sucursal 02 MT): el socio
estaba en ceros y la lista de notas seguía mostrando $1,841,848 por saldar. El
crédito externo (`AjusteSaldoPartner`) es un ÚNICO escalar con signo, así que
sólo podía cubrir un lado; la nota del lado contrario no tenía de dónde
cobrarse. El motor ahora netea primero las notas del socio entre sí.

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


def seed_base(db, nombres=("02 MT",)):
    sucursales = [Sucursal(nombre=n, estado=SucursalStatus.activa) for n in nombres]
    db.add_all(sucursales)
    db.flush()
    worker = User(
        username="t",
        password_hash="x",
        nombre_completo="Trabajador Prueba",
        rol=UserRole.trabajador,
        sucursal_id=sucursales[0].id,
    )
    db.add(worker)
    db.flush()
    return sucursales, worker


def par_vinculado(db, sucursal, nombre="METALES YAIR"):
    cli = Cliente(nombre_completo=nombre, sucursal_id=sucursal.id, activo=True)
    db.add(cli)
    db.flush()
    prov = Proveedor(
        nombre_completo=nombre,
        sucursal_id=sucursal.id,
        activo=True,
        linked_cliente_id=cli.id,
    )
    db.add(prov)
    db.flush()
    cli.linked_proveedor_id = prov.id
    db.flush()
    return prov, cli


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
        created_at=datetime(2026, 6, 1) + timedelta(days=dias),
    )
    db.add(n)
    db.flush()
    return n


def test_caso_metales_yair():
    """El caso reportado: socio en ceros → las dos notas en ceros."""
    print("T1 · METALES YAIR: socio en ceros, notas en ceros")
    db = fresh_session()
    (suc,), worker = seed_base(db)
    prov, cli = par_vinculado(db, suc)

    venta = nota(db, sucursal=suc, worker=worker, tipo=TipoOperacion.venta,
                 total="920924", cliente=cli, dias=0)
    compra = nota(db, sucursal=suc, worker=worker, tipo=TipoOperacion.compra,
                  total="1387560", proveedor=prov, dias=1)
    # El ajuste que la clienta registró para dejarlo en ceros.
    db.add(AjusteSaldoPartner(partner_type="proveedor", partner_id=prov.id,
                              monto=Decimal("-466636"), sucursal_id=suc.id))
    db.flush()

    m = note_service.build_effective_note_balance_map(db, [venta, compra])
    check("la venta queda en cero", m[venta.id]["saldo_pendiente"] == Decimal("0"),
          f"venta={m[venta.id]['saldo_pendiente']}")
    check("la compra queda en cero", m[compra.id]["saldo_pendiente"] == Decimal("0"),
          f"compra={m[compra.id]['saldo_pendiente']}")
    check("la venta se cancela contra la compra (neteo, no ajuste)",
          m[venta.id]["neteo_aplicado"] == Decimal("920924")
          and m[venta.id]["ajuste_aplicado"] == Decimal("0"),
          f"neteo={m[venta.id]['neteo_aplicado']} ajuste={m[venta.id]['ajuste_aplicado']}")
    check("la compra usa neteo + ajuste",
          m[compra.id]["neteo_aplicado"] == Decimal("920924")
          and m[compra.id]["ajuste_aplicado"] == Decimal("466636"),
          f"neteo={m[compra.id]['neteo_aplicado']} ajuste={m[compra.id]['ajuste_aplicado']}")


def test_sin_ajuste_solo_netea_el_lado_menor():
    """Sin ajuste registrado, el neteo no puede inventar dinero: sólo cancela
    hasta donde alcanza el lado menor y el resto sigue pendiente."""
    print("T2 · sin ajuste: se netea el lado menor, el resto sigue pendiente")
    db = fresh_session()
    (suc,), worker = seed_base(db)
    prov, cli = par_vinculado(db, suc, nombre="Socio Sin Ajuste")

    venta = nota(db, sucursal=suc, worker=worker, tipo=TipoOperacion.venta,
                 total="920924", cliente=cli, dias=0)
    compra = nota(db, sucursal=suc, worker=worker, tipo=TipoOperacion.compra,
                  total="1387560", proveedor=prov, dias=1)

    m = note_service.build_effective_note_balance_map(db, [venta, compra])
    check("la venta queda en cero", m[venta.id]["saldo_pendiente"] == Decimal("0"),
          f"venta={m[venta.id]['saldo_pendiente']}")
    check("la compra conserva la diferencia",
          m[compra.id]["saldo_pendiente"] == Decimal("466636"),
          f"compra={m[compra.id]['saldo_pendiente']}")
    check("nada se atribuye a un ajuste inexistente",
          m[compra.id]["ajuste_aplicado"] == Decimal("0"))


def test_el_neteo_no_depende_del_filtro_de_sucursal():
    """Invariante del punto 7: el saldo efectivo es global. Pedir sólo una nota
    no puede resucitar la que ya se neteó en otra sucursal."""
    print("T3 · el neteo es global, no de la vista")
    db = fresh_session()
    (suc_a, suc_b), worker = seed_base(db, nombres=("02 MT", "01 Centro"))
    prov, cli = par_vinculado(db, suc_a, nombre="Socio Dos Sucursales")

    venta = nota(db, sucursal=suc_a, worker=worker, tipo=TipoOperacion.venta,
                 total="1000", cliente=cli, dias=0)
    compra = nota(db, sucursal=suc_b, worker=worker, tipo=TipoOperacion.compra,
                  total="1000", proveedor=prov, dias=1)

    # La vista sólo pide la venta; la compra que la cancela vive en otra sucursal.
    m = note_service.build_effective_note_balance_map(db, [venta])
    check("la venta sigue en cero aunque la contraparte no esté en la vista",
          m[venta.id]["saldo_pendiente"] == Decimal("0"),
          f"venta={m[venta.id]['saldo_pendiente']}")

    completo = note_service.build_effective_note_balance_map(db, [venta, compra])
    check("mismo resultado con la vista completa",
          completo[venta.id]["saldo_pendiente"] == Decimal("0")
          and completo[compra.id]["saldo_pendiente"] == Decimal("0"))


def test_socio_de_un_solo_signo_no_cambia():
    """Un proveedor con puras compras no tiene contraparte: nada que netear."""
    print("T4 · socio de un solo signo: sin cambios")
    db = fresh_session()
    (suc,), worker = seed_base(db)
    prov = Proveedor(nombre_completo="Proveedor Puro", sucursal_id=suc.id, activo=True)
    db.add(prov)
    db.flush()

    c1 = nota(db, sucursal=suc, worker=worker, tipo=TipoOperacion.compra,
              total="1000", proveedor=prov, dias=0)
    c2 = nota(db, sucursal=suc, worker=worker, tipo=TipoOperacion.compra,
              total="500", proveedor=prov, dias=1)

    m = note_service.build_effective_note_balance_map(db, [c1, c2])
    check("la primera compra sigue pendiente", m[c1.id]["saldo_pendiente"] == Decimal("1000"))
    check("la segunda compra sigue pendiente", m[c2.id]["saldo_pendiente"] == Decimal("500"))
    check("no se aplicó neteo", m[c1.id]["neteo_aplicado"] == Decimal("0")
          and m[c2.id]["neteo_aplicado"] == Decimal("0"))


def test_notas_espejo_de_transferencia_no_se_netean():
    """Las transferencias usan socios distintos ('Sucursal X'), cada uno con
    notas de un solo signo: el neteo no debe tocarlas."""
    print("T5 · transferencias entre sucursales intactas")
    db = fresh_session()
    (suc_a, suc_b), worker = seed_base(db, nombres=("01 Centro", "02 MT"))
    cli_destino = Cliente(nombre_completo="Sucursal 02 MT", sucursal_id=suc_b.id, activo=True)
    prov_origen = Proveedor(nombre_completo="Sucursal 01 Centro", sucursal_id=suc_a.id, activo=True)
    db.add_all([cli_destino, prov_origen])
    db.flush()

    salida = nota(db, sucursal=suc_a, worker=worker, tipo=TipoOperacion.venta,
                  total="7000", cliente=cli_destino, dias=0)
    entrada = nota(db, sucursal=suc_b, worker=worker, tipo=TipoOperacion.compra,
                   total="7000", proveedor=prov_origen, dias=0)

    m = note_service.build_effective_note_balance_map(db, [salida, entrada])
    check("la nota de salida no se netea", m[salida.id]["neteo_aplicado"] == Decimal("0"),
          f"neteo={m[salida.id]['neteo_aplicado']}")
    check("la nota de entrada no se netea", m[entrada.id]["neteo_aplicado"] == Decimal("0"),
          f"neteo={m[entrada.id]['neteo_aplicado']}")


def test_pagos_previos_se_respetan():
    """El neteo corre sobre el saldo pendiente, no sobre el total facturado."""
    print("T6 · el neteo respeta los pagos ya hechos")
    db = fresh_session()
    (suc,), worker = seed_base(db)
    prov, cli = par_vinculado(db, suc, nombre="Socio Con Pagos")

    # Nos deben 300 (venta), les debemos 1000 − 400 pagados = 600.
    venta = nota(db, sucursal=suc, worker=worker, tipo=TipoOperacion.venta,
                 total="300", cliente=cli, dias=0)
    compra = nota(db, sucursal=suc, worker=worker, tipo=TipoOperacion.compra,
                  total="1000", pagado="400", proveedor=prov, dias=1)

    m = note_service.build_effective_note_balance_map(db, [venta, compra])
    check("la venta se cancela completa", m[venta.id]["saldo_pendiente"] == Decimal("0"))
    check("la compra baja de 600 a 300", m[compra.id]["saldo_pendiente"] == Decimal("300"),
          f"compra={m[compra.id]['saldo_pendiente']}")


if __name__ == "__main__":
    test_caso_metales_yair()
    test_sin_ajuste_solo_netea_el_lado_menor()
    test_el_neteo_no_depende_del_filtro_de_sucursal()
    test_socio_de_un_solo_signo_no_cambia()
    test_notas_espejo_de_transferencia_no_se_netean()
    test_pagos_previos_se_respetan()
    print()
    if FALLAS:
        print(f"{len(FALLAS)} prueba(s) fallaron: {FALLAS}")
        sys.exit(1)
    print("Todas las pruebas del neteo mutuo en verde.")
