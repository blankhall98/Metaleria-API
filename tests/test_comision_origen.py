"""Origen de una nota de comisión generada al aprobar (solicitud 04-sep-2026).

La clienta quiere ver en la nota de comisión de qué nota y de qué proveedor
viene la comisión. `comision_service.origen_por_comision` resuelve, para un
lote de notas de comisión, el folio de la nota de origen y su socio.

    python -m pytest tests/test_comision_origen.py -q
"""

from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import (
    Cliente,
    Comisionario,
    ComisionarioNota,
    ComisionarioNotaEstado,
    Nota,
    NotaEstado,
    Proveedor,
    Sucursal,
    SucursalStatus,
    TipoOperacion,
    User,
    UserRole,
    UserStatus,
)
from app.services.comision_service import origen_por_comision


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
    comisionario = Comisionario(nombre_completo="COMISIONISTA", sucursal_id=suc.id)
    db.add_all([user, comisionario])
    db.flush()
    return {"sucursal": suc, "user": user, "comisionario": comisionario}


def _nota(db, base, *, proveedor=None, cliente=None, tipo=TipoOperacion.compra, folio_seq=None):
    nota = Nota(
        sucursal_id=base["sucursal"].id,
        trabajador_id=base["user"].id,
        proveedor_id=proveedor.id if proveedor else None,
        cliente_id=cliente.id if cliente else None,
        tipo_operacion=tipo,
        estado=NotaEstado.aprobada,
        folio_seq=folio_seq,
        total_monto=Decimal("100"),
        created_at=datetime(2026, 9, 4, 12, 0),
    )
    db.add(nota)
    db.flush()
    return nota


def _comision(db, base, nota=None):
    cn = ComisionarioNota(
        comisionario_id=base["comisionario"].id,
        sucursal_id=base["sucursal"].id,
        nota_id=nota.id if nota else None,
        estado=ComisionarioNotaEstado.aprobada,
    )
    db.add(cn)
    db.flush()
    return cn


def test_origen_resuelve_folio_y_proveedor_de_la_nota_de_compra(db, base):
    gilberto = Proveedor(nombre_completo="GILBERTO JAUREGUI", sucursal_id=base["sucursal"].id)
    db.add(gilberto)
    db.flush()
    nota = _nota(db, base, proveedor=gilberto, folio_seq=637)
    cn = _comision(db, base, nota)

    origen = origen_por_comision(db, [cn])

    assert origen[cn.id] == {
        "nota_id": nota.id,
        "folio": "01_C_637",
        "partner_type": "proveedor",
        "partner_id": gilberto.id,
        "partner_name": "GILBERTO JAUREGUI",
    }


def test_origen_resuelve_cliente_en_nota_de_venta(db, base):
    cliente = Cliente(nombre_completo="ANA", sucursal_id=base["sucursal"].id)
    db.add(cliente)
    db.flush()
    nota = _nota(db, base, cliente=cliente, tipo=TipoOperacion.venta, folio_seq=12)
    cn = _comision(db, base, nota)

    origen = origen_por_comision(db, [cn])

    assert origen[cn.id]["folio"] == "01_V_12"
    assert origen[cn.id]["partner_type"] == "cliente"
    assert origen[cn.id]["partner_name"] == "ANA"


def test_origen_omite_comisiones_capturadas_a_mano(db, base):
    manual = _comision(db, base)
    gilberto = Proveedor(nombre_completo="GILBERTO JAUREGUI", sucursal_id=base["sucursal"].id)
    db.add(gilberto)
    db.flush()
    auto = _comision(db, base, _nota(db, base, proveedor=gilberto, folio_seq=1))

    origen = origen_por_comision(db, [manual, auto])

    assert manual.id not in origen
    assert auto.id in origen


def test_origen_sin_folio_usa_el_id_de_la_nota(db, base):
    gilberto = Proveedor(nombre_completo="GILBERTO JAUREGUI", sucursal_id=base["sucursal"].id)
    db.add(gilberto)
    db.flush()
    nota = _nota(db, base, proveedor=gilberto, folio_seq=None)
    cn = _comision(db, base, nota)

    origen = origen_por_comision(db, [cn])

    assert origen[cn.id]["folio"] == f"#{nota.id}"


def test_origen_con_lista_vacia(db, base):
    assert origen_por_comision(db, []) == {}
