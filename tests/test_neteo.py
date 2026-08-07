"""Pruebas del motor de neteo (punto 7, fase 2).

Primeras pruebas automatizadas del proyecto. Cubren la pieza que decide si una
nota aprobada sigue "pendiente": la aplicación FIFO de los ajustes de saldo del
socio (AjusteSaldoPartner) sobre sus notas, incluido el caso del par
cliente↔proveedor vinculado que motivó el bug reportado por el cliente.

    .venv/Scripts/python -m pytest tests/test_neteo.py -q
"""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.base import Base
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
    UserStatus,
)
from app.services.note_service import build_effective_note_balance_map


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
    """Sucursal + trabajador mínimos para poder colgar notas."""
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
    db.flush()
    return {"sucursal": suc, "user": user}


def _nota(db, base, *, proveedor=None, cliente=None, tipo, total, pagado="0", dias=0):
    nota = Nota(
        sucursal_id=base["sucursal"].id,
        trabajador_id=base["user"].id,
        proveedor_id=proveedor.id if proveedor else None,
        cliente_id=cliente.id if cliente else None,
        tipo_operacion=tipo,
        estado=NotaEstado.aprobada,
        total_monto=Decimal(total),
        monto_pagado=Decimal(pagado),
        created_at=datetime(2026, 1, 1) + timedelta(days=dias),
    )
    db.add(nota)
    db.flush()
    return nota


def _ajuste(db, *, partner_type, partner_id, monto, sucursal_id=None):
    db.add(
        AjusteSaldoPartner(
            partner_type=partner_type,
            partner_id=partner_id,
            monto=Decimal(monto),
            sucursal_id=sucursal_id,
        )
    )
    db.flush()


def test_ajuste_negativo_cubre_nota_de_compra(db, base):
    """El caso de la captura del cliente: nota por pagar + ajuste 'DEP' negativo
    del mismo monto → la nota queda cubierta, no pendiente."""
    prov = Proveedor(nombre_completo="Almacenes La Victoria", sucursal_id=base["sucursal"].id)
    db.add(prov)
    db.flush()
    nota = _nota(db, base, proveedor=prov, tipo=TipoOperacion.compra, total="942274.80")
    _ajuste(db, partner_type="proveedor", partner_id=prov.id, monto="-942274.80")

    balances = build_effective_note_balance_map(db, [nota])
    b = balances[nota.id]
    assert b["saldo_pendiente"] == Decimal("0")
    assert b["saldo_cubierto_por_ajuste"] is True
    assert b["ajuste_aplicado"] == Decimal("942274.80")


def test_ajuste_parcial_deja_resto_pendiente(db, base):
    prov = Proveedor(nombre_completo="Prov Parcial", sucursal_id=base["sucursal"].id)
    db.add(prov)
    db.flush()
    nota = _nota(db, base, proveedor=prov, tipo=TipoOperacion.compra, total="1000")
    _ajuste(db, partner_type="proveedor", partner_id=prov.id, monto="-400")

    b = build_effective_note_balance_map(db, [nota])[nota.id]
    assert b["saldo_pendiente"] == Decimal("600")
    assert b["saldo_parcialmente_cubierto"] is True


def test_credito_se_aplica_fifo_a_la_nota_mas_antigua(db, base):
    prov = Proveedor(nombre_completo="Prov FIFO", sucursal_id=base["sucursal"].id)
    db.add(prov)
    db.flush()
    vieja = _nota(db, base, proveedor=prov, tipo=TipoOperacion.compra, total="300", dias=0)
    nueva = _nota(db, base, proveedor=prov, tipo=TipoOperacion.compra, total="300", dias=5)
    _ajuste(db, partner_type="proveedor", partner_id=prov.id, monto="-300")

    balances = build_effective_note_balance_map(db, [vieja, nueva])
    assert balances[vieja.id]["saldo_pendiente"] == Decimal("0")
    assert balances[nueva.id]["saldo_pendiente"] == Decimal("300")


def test_credito_del_cliente_cubre_nota_del_proveedor_vinculado(db, base):
    """El defecto de fondo del punto 7: un ajuste registrado sobre la identidad
    cliente debe alcanzar a las notas de compra de su proveedor vinculado.

    Signos: para la identidad cliente, un delta POSITIVO es "en contra del
    cliente" (nos debe más), y en el neto del par eso compensa lo que la
    empresa le debe por sus compras. Un delta negativo (a favor del cliente)
    es deuda nuestra y no cubre nada.
    """
    prov = Proveedor(nombre_completo="Socio Dual", sucursal_id=base["sucursal"].id)
    db.add(prov)
    db.flush()
    cli = Cliente(nombre_completo="Socio Dual", sucursal_id=base["sucursal"].id, linked_proveedor_id=prov.id)
    db.add(cli)
    db.flush()
    prov.linked_cliente_id = cli.id
    db.add(prov)
    db.flush()

    nota_compra = _nota(db, base, proveedor=prov, tipo=TipoOperacion.compra, total="500")
    _ajuste(db, partner_type="cliente", partner_id=cli.id, monto="500")

    b = build_effective_note_balance_map(db, [nota_compra])[nota_compra.id]
    assert b["saldo_pendiente"] == Decimal("0")
    assert b["saldo_cubierto_por_ajuste"] is True


def test_favor_del_cliente_vinculado_no_cubre_compras(db, base):
    """Un saldo a favor del cliente (delta negativo) es deuda de la empresa:
    no debe 'cubrir' la compra pendiente del par."""
    prov = Proveedor(nombre_completo="Socio Dual B", sucursal_id=base["sucursal"].id)
    db.add(prov)
    db.flush()
    cli = Cliente(nombre_completo="Socio Dual B", sucursal_id=base["sucursal"].id, linked_proveedor_id=prov.id)
    db.add(cli)
    db.flush()
    prov.linked_cliente_id = cli.id
    db.add(prov)
    db.flush()

    nota_compra = _nota(db, base, proveedor=prov, tipo=TipoOperacion.compra, total="500")
    _ajuste(db, partner_type="cliente", partner_id=cli.id, monto="-500")

    b = build_effective_note_balance_map(db, [nota_compra])[nota_compra.id]
    assert b["saldo_pendiente"] == Decimal("500")
    assert b["saldo_cubierto_por_ajuste"] is False


def test_credito_no_cruza_a_socios_sin_vinculo(db, base):
    prov = Proveedor(nombre_completo="Prov Aislado", sucursal_id=base["sucursal"].id)
    cli = Cliente(nombre_completo="Cliente Ajeno", sucursal_id=base["sucursal"].id)
    db.add_all([prov, cli])
    db.flush()

    nota_compra = _nota(db, base, proveedor=prov, tipo=TipoOperacion.compra, total="500")
    _ajuste(db, partner_type="cliente", partner_id=cli.id, monto="500")

    b = build_effective_note_balance_map(db, [nota_compra])[nota_compra.id]
    assert b["saldo_pendiente"] == Decimal("500")
    assert b["saldo_cubierto_por_ajuste"] is False


def test_nota_no_aprobada_no_recibe_credito(db, base):
    prov = Proveedor(nombre_completo="Prov Borrador", sucursal_id=base["sucursal"].id)
    db.add(prov)
    db.flush()
    nota = _nota(db, base, proveedor=prov, tipo=TipoOperacion.compra, total="500")
    nota.estado = NotaEstado.borrador
    db.flush()
    _ajuste(db, partner_type="proveedor", partner_id=prov.id, monto="-500")

    b = build_effective_note_balance_map(db, [nota])[nota.id]
    assert b["ajuste_aplicado"] == Decimal("0")
