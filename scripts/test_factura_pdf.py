"""Pruebas de la fecha impresa en los PDF de nota (orden de compra/venta y comisión).

    python -m scripts.test_factura_pdf

Corre contra una base SQLite en memoria con el esquema real.

Lo que la clienta espera: el encabezado "Fecha" del PDF es la fecha en que se
hizo la nota — la misma que ve en la lista de notas y en el expediente del
socio (`nota.created_at`) — y NO la fecha en que descargó el archivo. La fecha
de descarga sigue existiendo, pero en el pie como "Generado", que es su lugar.

Sale distinto de cero si algo falla.
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from decimal import Decimal

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
import app.models  # noqa: F401 - registra todo el metadata
from app.models import (
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
)
from app.core.datetime_utils import format_datetime_local
from app.services import invoice_service


FALLAS: list[str] = []

# Fecha de captura de la nota de prueba: 23 jul 2026, 12:00 en México
# (America/Mexico_City = UTC−6 todo el año desde 2022).
CREADA_UTC = datetime(2026, 7, 23, 18, 0)
# Momento en que se "descarga" el PDF: 19 días después, otro mes.
DESCARGA_UTC = datetime(2026, 8, 11, 17, 30)


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


def pdf_text(pdf_bytes: bytes) -> str:
    """El generador escribe el stream sin comprimir: el texto se lee directo."""
    return pdf_bytes.decode("latin-1")


def campo(pdf_bytes: bytes, etiqueta: str) -> str:
    """Devuelve el valor dibujado para `etiqueta: ...` dentro del PDF."""
    match = re.search(rf"\({re.escape(etiqueta)}: ([^)]*)\)", pdf_text(pdf_bytes))
    return match.group(1) if match else ""


def seed_base(db):
    suc = Sucursal(nombre="Sucursal 2", estado=SucursalStatus.activa, direccion="Calle 1")
    db.add(suc)
    db.flush()
    worker = User(
        username="t",
        password_hash="x",
        nombre_completo="Trabajador Prueba",
        rol=UserRole.trabajador,
        sucursal_id=suc.id,
    )
    db.add(worker)
    db.flush()
    return suc, worker


def test_orden_de_compra_lleva_la_fecha_de_la_nota():
    """El encabezado 'Fecha' es la fecha de captura, no la de descarga."""
    print("T1 · orden de compra: fecha de la nota, no la de descarga")
    db = fresh_session()
    suc, worker = seed_base(db)
    prov = Proveedor(nombre_completo="Vidal Humberto", sucursal_id=suc.id, activo=True)
    db.add(prov)
    db.flush()

    n = Nota(
        sucursal_id=suc.id,
        trabajador_id=worker.id,
        proveedor_id=prov.id,
        tipo_operacion=TipoOperacion.compra,
        estado=NotaEstado.aprobada,
        total_monto=Decimal("1000"),
        monto_pagado=Decimal("0"),
        created_at=CREADA_UTC,
    )
    db.add(n)
    db.flush()

    pdf_bytes, _ = invoice_service.build_invoice_pdf(db, n, generated_at=DESCARGA_UTC)

    esperada = format_datetime_local(CREADA_UTC)
    check("la fecha del encabezado es la de captura", campo(pdf_bytes, "Fecha") == esperada,
          f"encabezado={campo(pdf_bytes, 'Fecha')!r} esperada={esperada!r}")
    check("y cae el 23 de julio", "23 jul 2026" in campo(pdf_bytes, "Fecha"),
          f"encabezado={campo(pdf_bytes, 'Fecha')!r}")
    check("la fecha de descarga no ocupa el encabezado",
          format_datetime_local(DESCARGA_UTC) != campo(pdf_bytes, "Fecha"))


def test_la_fecha_de_descarga_queda_en_el_pie():
    """No se pierde el dato: la descarga se imprime como 'Generado'."""
    print("T2 · la fecha de descarga baja al pie")
    db = fresh_session()
    suc, worker = seed_base(db)
    n = Nota(
        sucursal_id=suc.id,
        trabajador_id=worker.id,
        tipo_operacion=TipoOperacion.venta,
        estado=NotaEstado.aprobada,
        total_monto=Decimal("500"),
        created_at=CREADA_UTC,
    )
    db.add(n)
    db.flush()

    pdf_bytes, _ = invoice_service.build_invoice_pdf(db, n, generated_at=DESCARGA_UTC)
    check("el pie trae la fecha de generación",
          campo(pdf_bytes, "Generado") == format_datetime_local(DESCARGA_UTC),
          f"pie={campo(pdf_bytes, 'Generado')!r}")


def test_sin_generated_at_el_encabezado_no_cambia():
    """Como lo llama la web (sin `generated_at`): el encabezado sigue siendo la nota."""
    print("T3 · llamada real de la web, sin generated_at")
    db = fresh_session()
    suc, worker = seed_base(db)
    n = Nota(
        sucursal_id=suc.id,
        trabajador_id=worker.id,
        tipo_operacion=TipoOperacion.compra,
        estado=NotaEstado.aprobada,
        total_monto=Decimal("100"),
        created_at=CREADA_UTC,
    )
    db.add(n)
    db.flush()

    pdf_bytes, _ = invoice_service.build_invoice_pdf(db, n)
    check("encabezado = fecha de la nota",
          campo(pdf_bytes, "Fecha") == format_datetime_local(CREADA_UTC),
          f"encabezado={campo(pdf_bytes, 'Fecha')!r}")
    check("el pie NO repite la fecha de la nota",
          campo(pdf_bytes, "Generado") != format_datetime_local(CREADA_UTC))


def test_nota_de_comision_misma_regla():
    """La nota de comisión imprime su propia fecha, no la de descarga."""
    print("T4 · nota de comisión")
    db = fresh_session()
    suc, worker = seed_base(db)
    com = Comisionario(nombre_completo="Comisionista Prueba", sucursal_id=suc.id, activo=True)
    db.add(com)
    db.flush()

    n = ComisionarioNota(
        comisionario_id=com.id,
        sucursal_id=suc.id,
        admin_id=worker.id,
        estado=ComisionarioNotaEstado.aprobada,
        total_kg=Decimal("100"),
        total_monto=Decimal("250"),
        monto_pagado=Decimal("0"),
        created_at=CREADA_UTC,
    )
    db.add(n)
    db.flush()

    pdf_bytes, _ = invoice_service.build_comisionario_nota_pdf(db, n, generated_at=DESCARGA_UTC)
    check("encabezado = fecha de la nota de comisión",
          campo(pdf_bytes, "Fecha") == format_datetime_local(CREADA_UTC),
          f"encabezado={campo(pdf_bytes, 'Fecha')!r}")
    check("el pie trae la fecha de generación",
          campo(pdf_bytes, "Generado") == format_datetime_local(DESCARGA_UTC),
          f"pie={campo(pdf_bytes, 'Generado')!r}")


if __name__ == "__main__":
    test_orden_de_compra_lleva_la_fecha_de_la_nota()
    test_la_fecha_de_descarga_queda_en_el_pie()
    test_sin_generated_at_el_encabezado_no_cambia()
    test_nota_de_comision_misma_regla()
    print()
    if FALLAS:
        print(f"{len(FALLAS)} prueba(s) fallaron: {FALLAS}")
        sys.exit(1)
    print("Todas las pruebas de la fecha en el PDF en verde.")
