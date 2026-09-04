"""Pruebas de las agregaciones de kilos por material (solicitudes sep-2026).

Cubren el servicio que alimenta la tarjeta "Kilos por material" del expediente
del socio (punto 1) y el ranking de proveedores por material (punto 2):
solo notas aprobadas, base kg_neto, rango de fechas semiabierto sobre
created_at, alcance de sucursales y exclusión de socios internos.

    python -m pytest tests/test_kilos_material.py -q
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    Cliente,
    Material,
    Nota,
    NotaEstado,
    NotaMaterial,
    Proveedor,
    Sucursal,
    SucursalStatus,
    TipoOperacion,
    User,
    UserRole,
    UserStatus,
)
from app.services.kilos_material_service import kg_por_material, ranking_por_material


DIA_0 = datetime(2026, 8, 1, 12, 0)


@pytest.fixture()
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, autoflush=False)()
    yield session
    session.close()


@pytest.fixture()
def base(db):
    suc = Sucursal(nombre="Central", estado=SucursalStatus.activa)
    db.add(suc)
    db.flush()
    user = User(
        username="qa",
        password_hash="x",
        nombre_completo="QA",
        rol=UserRole.trabajador,
        estado=UserStatus.activo,
        sucursal_id=suc.id,
    )
    db.add(user)
    bronce = Material(nombre="Bronce", orden_display=1)
    radiador = Material(nombre="Radiador", orden_display=2)
    db.add_all([bronce, radiador])
    db.flush()
    return {"sucursal": suc, "user": user, "bronce": bronce, "radiador": radiador}


def _proveedor(db, base, nombre, **kw):
    prov = Proveedor(nombre_completo=nombre, sucursal_id=base["sucursal"].id, **kw)
    db.add(prov)
    db.flush()
    return prov


def _nota(
    db,
    base,
    *,
    proveedor=None,
    cliente=None,
    tipo=TipoOperacion.compra,
    estado=NotaEstado.aprobada,
    dias=0,
    sucursal=None,
    lineas,
):
    """lineas: lista de (material, kg, precio)."""
    suc = sucursal or base["sucursal"]
    nota = Nota(
        sucursal_id=suc.id,
        trabajador_id=base["user"].id,
        proveedor_id=proveedor.id if proveedor else None,
        cliente_id=cliente.id if cliente else None,
        tipo_operacion=tipo,
        estado=estado,
        created_at=DIA_0 + timedelta(days=dias),
    )
    db.add(nota)
    db.flush()
    total = Decimal("0")
    for material, kg, precio in lineas:
        kg_d = Decimal(kg)
        precio_d = Decimal(precio)
        subtotal = (kg_d * precio_d).quantize(Decimal("0.01"))
        db.add(
            NotaMaterial(
                nota_id=nota.id,
                material_id=material.id,
                kg_bruto=kg_d,
                kg_neto=kg_d,
                kg_real=kg_d,
                precio_unitario=precio_d,
                subtotal=subtotal,
            )
        )
        total += subtotal
    nota.total_monto = total
    db.flush()
    return nota


# ---------- punto 1: kilos por material de un socio ----------


def test_kg_por_material_suma_lineas_de_notas_aprobadas(db, base):
    bravo = _proveedor(db, base, "BRAVO")
    _nota(db, base, proveedor=bravo, lineas=[(base["bronce"], "3195", "156")])
    _nota(
        db,
        base,
        proveedor=bravo,
        dias=1,
        lineas=[(base["bronce"], "3220", "159"), (base["radiador"], "348", "150")],
    )
    # Borrador y cancelada no cuentan.
    _nota(db, base, proveedor=bravo, dias=2, estado=NotaEstado.borrador, lineas=[(base["bronce"], "999", "100")])
    _nota(db, base, proveedor=bravo, dias=3, estado=NotaEstado.cancelada, lineas=[(base["bronce"], "999", "100")])

    rows = kg_por_material(db, tipo_operacion=TipoOperacion.compra, proveedor_id=bravo.id)

    assert [r["material_nombre"] for r in rows] == ["Bronce", "Radiador"]
    bronce, radiador = rows
    assert bronce["kg"] == Decimal("6415.000")
    assert bronce["notas"] == 2
    assert bronce["importe"] == Decimal("1010400.00")
    assert radiador["kg"] == Decimal("348.000")
    assert radiador["notas"] == 1
    assert radiador["importe"] == Decimal("52200.00")


def test_kg_por_material_no_mezcla_socios(db, base):
    bravo = _proveedor(db, base, "BRAVO")
    otro = _proveedor(db, base, "OTRO")
    _nota(db, base, proveedor=bravo, lineas=[(base["bronce"], "100", "150")])
    _nota(db, base, proveedor=otro, lineas=[(base["bronce"], "900", "150")])

    rows = kg_por_material(db, tipo_operacion=TipoOperacion.compra, proveedor_id=bravo.id)

    assert len(rows) == 1
    assert rows[0]["kg"] == Decimal("100.000")


def test_kg_por_material_respeta_rango_semiabierto(db, base):
    bravo = _proveedor(db, base, "BRAVO")
    _nota(db, base, proveedor=bravo, dias=0, lineas=[(base["bronce"], "10", "150")])
    _nota(db, base, proveedor=bravo, dias=5, lineas=[(base["bronce"], "20", "150")])
    _nota(db, base, proveedor=bravo, dias=10, lineas=[(base["bronce"], "40", "150")])

    rows = kg_por_material(
        db,
        tipo_operacion=TipoOperacion.compra,
        proveedor_id=bravo.id,
        start_utc=DIA_0 + timedelta(days=5),
        end_utc=DIA_0 + timedelta(days=10),
    )

    assert len(rows) == 1
    assert rows[0]["kg"] == Decimal("20.000")
    assert rows[0]["notas"] == 1


def test_kg_por_material_respeta_alcance_de_sucursales(db, base):
    otra = Sucursal(nombre="Norte", estado=SucursalStatus.activa)
    db.add(otra)
    db.flush()
    bravo = _proveedor(db, base, "BRAVO")
    _nota(db, base, proveedor=bravo, lineas=[(base["bronce"], "10", "150")])
    _nota(db, base, proveedor=bravo, sucursal=otra, lineas=[(base["bronce"], "90", "150")])

    rows = kg_por_material(
        db,
        tipo_operacion=TipoOperacion.compra,
        proveedor_id=bravo.id,
        allowed_suc_ids=[base["sucursal"].id],
    )

    assert rows[0]["kg"] == Decimal("10.000")


def test_kg_por_material_separa_compras_de_ventas(db, base):
    bravo = _proveedor(db, base, "BRAVO", permite_ventas=True)
    _nota(db, base, proveedor=bravo, tipo=TipoOperacion.compra, lineas=[(base["bronce"], "10", "150")])
    _nota(db, base, proveedor=bravo, tipo=TipoOperacion.venta, lineas=[(base["bronce"], "7", "180")])

    compras = kg_por_material(db, tipo_operacion=TipoOperacion.compra, proveedor_id=bravo.id)
    ventas = kg_por_material(db, tipo_operacion=TipoOperacion.venta, proveedor_id=bravo.id)

    assert compras[0]["kg"] == Decimal("10.000")
    assert ventas[0]["kg"] == Decimal("7.000")


def test_kg_por_material_une_identidades_del_par_vinculado(db, base):
    """Ventas del par vinculado: al cliente y al proveedor con permite_ventas."""
    bravo = _proveedor(db, base, "BRAVO", permite_ventas=True)
    cliente = Cliente(nombre_completo="BRAVO", sucursal_id=base["sucursal"].id)
    db.add(cliente)
    db.flush()
    _nota(db, base, proveedor=bravo, tipo=TipoOperacion.venta, lineas=[(base["bronce"], "7", "180")])
    _nota(db, base, cliente=cliente, tipo=TipoOperacion.venta, lineas=[(base["bronce"], "3", "180")])

    ventas = kg_por_material(
        db,
        tipo_operacion=TipoOperacion.venta,
        proveedor_id=bravo.id,
        cliente_id=cliente.id,
    )

    assert ventas[0]["kg"] == Decimal("10.000")
    assert ventas[0]["notas"] == 2


# ---------- punto 2: ranking de proveedores por material ----------


def test_ranking_ordena_proveedores_de_mayor_a_menor(db, base):
    bravo = _proveedor(db, base, "BRAVO")
    alfa = _proveedor(db, base, "ALFA")
    zeta = _proveedor(db, base, "ZETA")
    _nota(db, base, proveedor=bravo, lineas=[(base["bronce"], "3195", "156")])
    _nota(db, base, proveedor=bravo, dias=1, lineas=[(base["bronce"], "1659", "159")])
    _nota(db, base, proveedor=alfa, lineas=[(base["bronce"], "500", "150")])
    _nota(db, base, proveedor=zeta, lineas=[(base["bronce"], "500", "150")])
    # Otro material no cuenta para este ranking.
    _nota(db, base, proveedor=alfa, dias=2, lineas=[(base["radiador"], "9000", "100")])

    reporte = ranking_por_material(
        db,
        material_id=base["bronce"].id,
        start_utc=DIA_0,
        end_utc=DIA_0 + timedelta(days=30),
    )

    rows = reporte["rows"]
    assert [r["nombre"] for r in rows] == ["BRAVO", "ALFA", "ZETA"]
    assert rows[0]["kg"] == Decimal("4854.000")
    assert rows[0]["notas"] == 2
    assert rows[0]["importe"] == Decimal("762201.00")
    assert rows[1]["notas"] == 1
    assert reporte["total_kg"] == Decimal("5854.000")
    assert reporte["total_notas"] == 4
    assert rows[0]["pct"] == Decimal("82.9")
    assert rows[1]["pct"] == Decimal("8.5")


def test_ranking_excluye_socios_internos_y_respeta_rango(db, base):
    bravo = _proveedor(db, base, "BRAVO")
    interno = _proveedor(db, base, "Sucursal Central")
    _nota(db, base, proveedor=bravo, lineas=[(base["bronce"], "100", "150")])
    _nota(db, base, proveedor=interno, lineas=[(base["bronce"], "5000", "150")])
    _nota(db, base, proveedor=bravo, dias=40, lineas=[(base["bronce"], "777", "150")])

    reporte = ranking_por_material(
        db,
        material_id=base["bronce"].id,
        start_utc=DIA_0,
        end_utc=DIA_0 + timedelta(days=30),
    )

    assert [r["nombre"] for r in reporte["rows"]] == ["BRAVO"]
    assert reporte["rows"][0]["kg"] == Decimal("100.000")
    assert reporte["rows"][0]["pct"] == Decimal("100.0")


def test_ranking_filtra_por_sucursal_elegida(db, base):
    otra = Sucursal(nombre="Norte", estado=SucursalStatus.activa)
    db.add(otra)
    db.flush()
    bravo = _proveedor(db, base, "BRAVO")
    alfa = _proveedor(db, base, "ALFA")
    _nota(db, base, proveedor=bravo, lineas=[(base["bronce"], "100", "150")])
    _nota(db, base, proveedor=alfa, sucursal=otra, lineas=[(base["bronce"], "900", "150")])

    reporte = ranking_por_material(
        db,
        material_id=base["bronce"].id,
        start_utc=DIA_0,
        end_utc=DIA_0 + timedelta(days=30),
        sucursal_id=otra.id,
    )

    assert [r["nombre"] for r in reporte["rows"]] == ["ALFA"]


def test_ranking_sin_notas_devuelve_vacio(db, base):
    reporte = ranking_por_material(
        db,
        material_id=base["bronce"].id,
        start_utc=DIA_0,
        end_utc=DIA_0 + timedelta(days=30),
    )

    assert reporte["rows"] == []
    assert reporte["total_kg"] == Decimal("0")
