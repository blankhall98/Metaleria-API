"""Pruebas de la comisión generada al aprobar la nota (fase 2).

    python -m scripts.test_comision_auto

SQLite en memoria con el esquema real. Cubre: generación en la transacción de
la aprobación (prorrateo exacto del monto), aprobación sin comisión, bloqueo
de la cancelación con pagos vivos, cancelación compensatoria sin pagos,
restauración simétrica, y que el camino manual quede intacto.
"""

from __future__ import annotations

import sys
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
import app.models  # noqa: F401
from app.models import (
    Comisionario,
    ComisionarioNota,
    ComisionarioNotaEstado,
    Material,
    Nota,
    NotaEstado,
    NotaMaterial,
    Proveedor,
    Sucursal,
    SucursalStatus,
    TablaPrecio,
    TipoCliente,
    TipoOperacion,
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
        username="a", password_hash="x",
        nombre_completo="Admin Prueba", rol=UserRole.super_admin,
    )
    prov = Proveedor(nombre_completo="Prov Comisiones", sucursal_id=suc.id, activo=True)
    cobre = Material(nombre="Cobre")
    bronce = Material(nombre="Bronce")
    com = Comisionario(nombre_completo="Nena Comisiones", sucursal_id=suc.id, activo=True)
    db.add_all([admin, prov, cobre, bronce, com])
    db.flush()
    for mat in (cobre, bronce):
        db.add(
            TablaPrecio(
                material_id=mat.id,
                tipo_operacion=TipoOperacion.compra,
                tipo_cliente=TipoCliente.regular,
                precio_por_unidad=Decimal("10"),
                version=1,
                activo=True,
            )
        )
    db.flush()
    return suc, admin, prov, cobre, bronce, com


def nota_en_revision(db, *, suc, admin, prov, materiales):
    n = Nota(
        sucursal_id=suc.id,
        trabajador_id=admin.id,
        proveedor_id=prov.id,
        tipo_operacion=TipoOperacion.compra,
        estado=NotaEstado.en_revision,
    )
    db.add(n)
    db.flush()
    for mat, kg in materiales:
        db.add(
            NotaMaterial(
                nota_id=n.id,
                material_id=mat.id,
                kg_bruto=Decimal(str(kg)),
                kg_neto=Decimal(str(kg)),
                tipo_cliente=TipoCliente.regular,
            )
        )
    # Commit: los flujos de fallo hacen rollback y deben volver a ESTE estado,
    # no a una sesión vacía.
    db.commit()
    db.refresh(n)
    return n


def test_aprobar_con_comision():
    print("C1 · aprobar la nota genera su comisión (por material) en la misma transacción")
    db = fresh_session()
    suc, admin, prov, cobre, bronce, com = seed_base(db)
    n = nota_en_revision(db, suc=suc, admin=admin, prov=prov,
                         materiales=[(cobre, "5.550"), (bronce, "2.220")])
    nm_cobre = next(m for m in n.materiales if m.material_id == cobre.id)
    nm_bronce = next(m for m in n.materiales if m.material_id == bronce.id)
    # El caso de la clienta: comisión SOLO en algunos materiales, cada uno con
    # su precio por kg libre. Aquí solo el cobre, a 1.50/kg.
    note_service.approve_note(
        db, n, admin_id=admin.id,
        comisionario_id=com.id, comision_precios={nm_cobre.id: Decimal("1.50")},
    )
    check("la nota queda aprobada", n.estado == NotaEstado.aprobada)
    comision = db.query(ComisionarioNota).filter(ComisionarioNota.nota_id == n.id).one()
    check("la comisión nace aprobada", comision.estado == ComisionarioNotaEstado.aprobada)
    check("vinculada al comisionista", comision.comisionario_id == com.id)
    check("vive en la sucursal del comisionista", comision.sucursal_id == com.sucursal_id)
    check("solo el material marcado lleva comisión",
          len(comision.materiales) == 1 and comision.materiales[0].material_id == cobre.id)
    # 5.550 × 1.50 = 8.325 → 8.32: mismo redondeo (quantize a par) que la
    # captura manual, para que la nota automática y la manual den lo mismo.
    check("kg neto × precio/kg = subtotal (5.550 × 1.50 = 8.32)",
          Decimal(str(comision.materiales[0].subtotal)) == Decimal("8.32"),
          f"subtotal={comision.materiales[0].subtotal}")
    check("el total es la suma de subtotales",
          Decimal(str(comision.total_monto)) == Decimal("8.32"),
          f"total={comision.total_monto}")
    check("los kg son solo los del material marcado",
          Decimal(str(comision.total_kg)) == Decimal("5.550"),
          f"kg={comision.total_kg}")

    # Doble generación imposible: la nota ya está aprobada
    try:
        comision_service.create_comision_for_nota(
            db, nota=n, comisionario_id=com.id,
            precios_por_material={nm_bronce.id: Decimal("1")},
            admin_id=admin.id, commit=True,
        )
        check("segunda comisión para la misma nota rechazada", False)
    except ValueError:
        check("segunda comisión para la misma nota rechazada", True)


def test_dos_materiales_precios_distintos():
    print("C1b · dos materiales con precio por kg distinto")
    db = fresh_session()
    suc, admin, prov, cobre, bronce, com = seed_base(db)
    n = nota_en_revision(db, suc=suc, admin=admin, prov=prov,
                         materiales=[(cobre, "100"), (bronce, "40")])
    precios = {m.id: (Decimal("0.50") if m.material_id == cobre.id else Decimal("2")) for m in n.materiales}
    note_service.approve_note(db, n, admin_id=admin.id, comisionario_id=com.id, comision_precios=precios)
    comision = db.query(ComisionarioNota).filter(ComisionarioNota.nota_id == n.id).one()
    check("dos líneas", len(comision.materiales) == 2)
    check("total 100×0.50 + 40×2 = 130.00",
          Decimal(str(comision.total_monto)) == Decimal("130.00"), f"total={comision.total_monto}")
    check("kg 140", Decimal(str(comision.total_kg)) == Decimal("140.000"))
    # precio 0 en un material marcado se rechaza (aprobación completa abortada)
    n2 = nota_en_revision(db, suc=suc, admin=admin, prov=prov, materiales=[(cobre, "10")])
    try:
        note_service.approve_note(db, n2, admin_id=admin.id, comisionario_id=com.id,
                                  comision_precios={n2.materiales[0].id: Decimal("0")})
        check("precio 0 rechazado", False)
    except ValueError:
        check("precio 0 rechazado", True)
    db.rollback()
    db.refresh(n2)
    check("la nota sin comisión válida NO se aprobó", n2.estado == NotaEstado.en_revision)


def test_aprobar_sin_comision():
    print("C2 · aprobar sin comisionista no genera nada")
    db = fresh_session()
    suc, admin, prov, cobre, _bronce, _com = seed_base(db)
    n = nota_en_revision(db, suc=suc, admin=admin, prov=prov, materiales=[(cobre, "10")])
    note_service.approve_note(db, n, admin_id=admin.id)
    check("la nota queda aprobada", n.estado == NotaEstado.aprobada)
    check("sin comisión vinculada",
          db.query(ComisionarioNota).filter(ComisionarioNota.nota_id == n.id).count() == 0)


def test_comisionista_invalido_aborta_la_aprobacion():
    print("C3 · comisionista inválido: la aprobación completa se aborta")
    db = fresh_session()
    suc, admin, prov, cobre, _bronce, com = seed_base(db)
    com.activo = False
    db.flush()
    n = nota_en_revision(db, suc=suc, admin=admin, prov=prov, materiales=[(cobre, "10")])
    try:
        note_service.approve_note(
            db, n, admin_id=admin.id,
            comisionario_id=com.id, comision_precios={n.materiales[0].id: Decimal("10")},
        )
        check("aprobación con comisionista inactivo rechazada", False)
    except ValueError:
        check("aprobación con comisionista inactivo rechazada", True)
    db.rollback()
    db.refresh(n)
    check("la nota NO quedó aprobada (transacción única)",
          n.estado == NotaEstado.en_revision, f"estado={n.estado}")
    check("no quedó comisión huérfana",
          db.query(ComisionarioNota).count() == 0)


def test_cancelar_nota_cancela_comision_sin_pagos():
    print("C4 · cancelar la nota cancela su comisión (compensatorio, sin borrar)")
    db = fresh_session()
    suc, admin, prov, cobre, _bronce, com = seed_base(db)
    n = nota_en_revision(db, suc=suc, admin=admin, prov=prov, materiales=[(cobre, "10")])
    note_service.approve_note(
        db, n, admin_id=admin.id,
        comisionario_id=com.id, comision_precios={n.materiales[0].id: Decimal("20")},
    )
    note_service.cancel_approved_note(db, n, admin_id=admin.id)
    check("la nota queda cancelada", n.estado == NotaEstado.cancelada)
    comision = db.query(ComisionarioNota).filter(ComisionarioNota.nota_id == n.id).one()
    check("la comisión queda CANCELADA, no borrada",
          comision.estado == ComisionarioNotaEstado.cancelada)

    # Restaurar la nota revive la comisión
    devolucion = n.devoluciones_totales[0] if hasattr(n, "devoluciones_totales") and n.devoluciones_totales else None
    if devolucion is None:
        from app.models import NotaDevolucionTotal
        devolucion = db.query(NotaDevolucionTotal).filter(NotaDevolucionTotal.nota_id == n.id).one()
    note_service.reverse_total_return(db, n, devolucion, admin_id=admin.id)
    check("la nota vuelve a aprobada", n.estado == NotaEstado.aprobada)
    db.refresh(comision)
    check("la comisión revive con la nota",
          comision.estado == ComisionarioNotaEstado.aprobada)


def test_cancelar_nota_con_comision_pagada_se_bloquea():
    print("C5 · comisión con pagos vivos: la cancelación de la nota se bloquea")
    db = fresh_session()
    suc, admin, prov, cobre, _bronce, com = seed_base(db)
    n = nota_en_revision(db, suc=suc, admin=admin, prov=prov, materiales=[(cobre, "10")])
    note_service.approve_note(
        db, n, admin_id=admin.id,
        comisionario_id=com.id, comision_precios={n.materiales[0].id: Decimal("30")},
    )
    comision = db.query(ComisionarioNota).filter(ComisionarioNota.nota_id == n.id).one()
    comision_service.add_comisionario_pago(
        db, nota=comision, monto=Decimal("100"), usuario_id=admin.id,
        metodo_pago="efectivo", cuenta_financiera=None,
        cuenta_scrap360_id=None, comentario="abono",
    )
    try:
        note_service.cancel_approved_note(db, n, admin_id=admin.id)
        check("cancelación bloqueada por pagos de comisión", False)
    except ValueError as e:
        check("cancelación bloqueada por pagos de comisión",
              "comisión" in str(e) or "comision" in str(e), str(e))
    db.rollback()
    db.refresh(n)
    check("la nota sigue aprobada", n.estado == NotaEstado.aprobada)
    db.refresh(comision)
    check("la comisión sigue aprobada", comision.estado == ComisionarioNotaEstado.aprobada)

    # Deshecho el pago, la cancelación procede
    pago = comision.pagos[0]
    comision_service.revert_comisionario_pago(db, pago_id=pago.id, usuario_id=admin.id)
    note_service.cancel_approved_note(db, n, admin_id=admin.id)
    check("con el pago deshecho la cancelación procede", n.estado == NotaEstado.cancelada)
    db.refresh(comision)
    check("y la comisión se cancela junto con la nota",
          comision.estado == ComisionarioNotaEstado.cancelada)


def test_camino_manual_intacto():
    print("C6 · la captura manual de comisiones no cambia")
    db = fresh_session()
    suc, admin, _prov, cobre, _bronce, com = seed_base(db)
    manual = comision_service.create_comisionario_nota(
        db,
        comisionario_id=com.id,
        sucursal_id=suc.id,
        admin_id=admin.id,
        comentario="captura manual",
        materiales_payload=[{"material_id": cobre.id, "kg_neto": "100", "precio_por_kg": "0.5"}],
    )
    check("nace aprobada", manual.estado == ComisionarioNotaEstado.aprobada)
    check("sin nota de origen", manual.nota_id is None)
    check("total por líneas kg×precio", Decimal(str(manual.total_monto)) == Decimal("50.00"))
    # Cancelar una nota cualquiera no toca las comisiones manuales
    check("cancel_comision_for_nota ignora las manuales",
          comision_service.cancel_comision_for_nota(db, nota_id=999999) is None)


if __name__ == "__main__":
    test_aprobar_con_comision()
    test_dos_materiales_precios_distintos()
    test_aprobar_sin_comision()
    test_comisionista_invalido_aborta_la_aprobacion()
    test_cancelar_nota_cancela_comision_sin_pagos()
    test_cancelar_nota_con_comision_pagada_se_bloquea()
    test_camino_manual_intacto()
    print()
    if FALLAS:
        print(f"{len(FALLAS)} prueba(s) fallaron: {FALLAS}")
        sys.exit(1)
    print("Todas las pruebas de la comisión automática en verde.")
