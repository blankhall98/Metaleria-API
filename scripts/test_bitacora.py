"""Pruebas de la bitácora de llamadas (punto 2, fase 2).

    python -m scripts.test_bitacora

SQLite en memoria con el esquema real. Cubre el parseo de precio libre,
el ciclo de estatus con auditoría y el borrado en cascada de las líneas.
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
    LlamadaProveedorEstatus,
    LlamadaProveedorMaterial,
    Material,
    Proveedor,
    Sucursal,
    SucursalStatus,
    User,
    UserRole,
)
from app.services import llamada_service


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
    prov = Proveedor(nombre_completo="Prov Llamadas", sucursal_id=suc.id, activo=True)
    cobre = Material(nombre="Cobre")
    db.add_all([admin, prov, cobre])
    db.flush()
    return suc, admin, prov, cobre


def test_parse_precio():
    print("B1 · el precio del Excel puede ser número o texto libre")
    num, texto = llamada_service.parse_precio("$95.50")
    check("número con símbolo se lee", num == Decimal("95.50") and texto == "$95.50")
    num, texto = llamada_service.parse_precio("1,250")
    check("número con coma se lee", num == Decimal("1250"))
    num, texto = llamada_service.parse_precio("Lista Viernes 22 Mayo")
    check("texto libre queda solo como texto", num is None and texto == "Lista Viernes 22 Mayo")
    num, texto = llamada_service.parse_precio("   ")
    check("vacío queda en nada", num is None and texto is None)


def test_ciclo_llamada():
    print("B2 · crear llamada, marcar entregada y regresarla a pendiente")
    db = fresh_session()
    _suc, admin, prov, cobre = seed_base(db)

    llamada = llamada_service.create_llamada(
        db,
        proveedor_id=prov.id,
        usuario_id=admin.id,
        fecha=date(2026, 8, 7),
        fecha_estimada_entrega="Semana del 25 al 29",
        comentarios="Llamar el miércoles",
        materiales=[
            {"material_id": cobre.id, "precio_raw": "95.5", "kg_aproximados": Decimal("1200")},
            {"material_id": None, "precio_raw": "NO CERRAMOS PRECIO", "kg_aproximados": None},
        ],
    )
    check("la llamada nace pendiente", llamada.estatus == LlamadaProveedorEstatus.pendiente)
    check("hereda la sucursal del proveedor", llamada.sucursal_id == prov.sucursal_id)
    check("dos líneas de material", len(llamada.materiales) == 2)
    linea_cobre = llamada.materiales[0]
    check(
        "línea con número guarda ambos",
        linea_cobre.precio_por_kg == Decimal("95.5") and linea_cobre.precio_texto == "95.5",
    )
    linea_libre = llamada.materiales[1]
    check(
        "línea de texto libre sin material",
        linea_libre.material_id is None
        and linea_libre.precio_por_kg is None
        and linea_libre.precio_texto == "NO CERRAMOS PRECIO",
    )

    llamada = llamada_service.set_estatus(
        db, llamada_id=llamada.id, estatus=LlamadaProveedorEstatus.entregado, usuario_id=admin.id
    )
    check(
        "entregada deja fecha y usuario",
        llamada.entregada_at is not None and llamada.entregada_by_user_id == admin.id,
    )
    llamada = llamada_service.set_estatus(
        db, llamada_id=llamada.id, estatus=LlamadaProveedorEstatus.no_confirmo, usuario_id=admin.id
    )
    check(
        "salir de entregada limpia la auditoría",
        llamada.entregada_at is None and llamada.entregada_by_user_id is None,
    )


def test_borrado_y_filtros():
    print("B3 · filtros de consulta y borrado en cascada")
    db = fresh_session()
    _suc, admin, prov, cobre = seed_base(db)
    l1 = llamada_service.create_llamada(
        db, proveedor_id=prov.id, usuario_id=admin.id, fecha=date(2026, 8, 1),
        fecha_estimada_entrega=None, comentarios=None,
        materiales=[{"material_id": cobre.id, "precio_raw": "90", "kg_aproximados": None}],
    )
    l2 = llamada_service.create_llamada(
        db, proveedor_id=prov.id, usuario_id=admin.id, fecha=date(2026, 8, 5),
        fecha_estimada_entrega=None, comentarios="Sin líneas", materiales=[],
    )
    llamada_service.set_estatus(
        db, llamada_id=l2.id, estatus=LlamadaProveedorEstatus.entregado, usuario_id=admin.id
    )

    pendientes = llamada_service.query_llamadas(
        db, estatus=LlamadaProveedorEstatus.pendiente
    )
    check("filtro por estatus", [ll.id for ll in pendientes] == [l1.id])
    recientes = llamada_service.query_llamadas(db, proveedor_id=prov.id)
    check("orden recientes primero", [ll.id for ll in recientes] == [l2.id, l1.id])
    por_sucursal = llamada_service.query_llamadas(db, sucursal_ids=[prov.sucursal_id])
    check("filtro por sucursal incluye ambas", len(por_sucursal) == 2)

    llamada_service.delete_llamada(db, llamada_id=l1.id)
    huerfanas = db.query(LlamadaProveedorMaterial).filter(
        LlamadaProveedorMaterial.llamada_id == l1.id
    ).count()
    check("el borrado se lleva las líneas", huerfanas == 0)
    check("solo queda la otra llamada", len(llamada_service.query_llamadas(db)) == 1)


if __name__ == "__main__":
    test_parse_precio()
    test_ciclo_llamada()
    test_borrado_y_filtros()
    print()
    if FALLAS:
        print(f"{len(FALLAS)} prueba(s) fallaron: {FALLAS}")
        sys.exit(1)
    print("Todas las pruebas de la bitácora en verde.")
