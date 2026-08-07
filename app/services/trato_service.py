# app/services/trato_service.py
"""Tratos de venta de contenedores (punto 3, fase 2).

Réplica de la cadena de cálculo del Excel del cliente (PEDIDOS JORGE ALFARO):

    precio_lb_usd   = (LME × DESCUENTO / 1000) / 2.204623
    libras          = kg × 2.204623
    total_usd       = libras × precio_lb_usd
    total_pesos     = usd_tc1 × TC1 + usd_tc2 × TC2   (el pago puede partirse)
    precio_kg_mxn   = (precio_lb_usd × 2.204623) × TC
    premio          = precio_kg_mxn × premio_pct      (5.5 % por defecto, editable)
    precio_c_premio = precio_kg_mxn + premio
    total_venta     = kg × precio_c_premio

Los kilos vendidos del trato se LEEN de las notas de venta aprobadas
vinculadas; el trato jamás escribe sobre notas ni inventario.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session, joinedload

from app.models import (
    Nota,
    NotaEstado,
    TipoOperacion,
    TratoVenta,
    TratoVentaContenedor,
    TratoVentaEstado,
    TratoVentaNota,
)

# Factor exacto del Excel del cliente (libras por kilogramo).
LB_POR_KG = Decimal("2.204623")

ESTADO_LABELS = {
    TratoVentaEstado.abierto: "Abierto",
    TratoVentaEstado.completado: "Completado",
}


def _dec(value) -> Decimal | None:
    if value is None:
        return None
    return value if isinstance(value, Decimal) else Decimal(str(value))


def calcular_contenedor(
    *,
    kg,
    lme_usd_ton=None,
    descuento_factor=None,
    precio_lb_usd=None,
    tc1=None,
    usd_tc1=None,
    tc2=None,
    usd_tc2=None,
    premio_pct=None,
) -> dict:
    """Deriva todos los valores de un contenedor a partir de sus insumos.

    precio_lb_usd capturado directo tiene prioridad (caso EBONY sin LME);
    si no viene, se deriva de LME × descuento. Cuando el pago se parte en dos
    tipos de cambio, el TC efectivo del precio por kg es el ponderado real
    (total_pesos / total_usd); sin partirse, es TC1.
    """
    kg = _dec(kg) or Decimal("0")
    lme = _dec(lme_usd_ton)
    descuento = _dec(descuento_factor)
    precio_lb = _dec(precio_lb_usd)
    tc1 = _dec(tc1)
    usd_tc1 = _dec(usd_tc1)
    tc2 = _dec(tc2)
    usd_tc2 = _dec(usd_tc2)
    premio_pct = _dec(premio_pct) if premio_pct is not None else None

    if precio_lb is None and lme is not None and descuento is not None:
        precio_lb = (lme * descuento / Decimal("1000")) / LB_POR_KG

    libras = kg * LB_POR_KG
    total_usd = libras * precio_lb if precio_lb is not None else None

    total_pesos = None
    tc_efectivo = None
    if usd_tc1 is not None and tc1 is not None:
        total_pesos = usd_tc1 * tc1
        if usd_tc2 is not None and tc2 is not None:
            total_pesos += usd_tc2 * tc2
        usd_base = (usd_tc1 or Decimal("0")) + (usd_tc2 or Decimal("0"))
        if usd_base > 0:
            tc_efectivo = total_pesos / usd_base
    elif tc1 is not None:
        tc_efectivo = tc1
        if total_usd is not None:
            total_pesos = total_usd * tc1

    precio_kg_mxn = None
    premio_monto = None
    precio_con_premio = None
    total_venta = None
    if precio_lb is not None and tc_efectivo is not None:
        precio_kg_mxn = precio_lb * LB_POR_KG * tc_efectivo
        pct = premio_pct if premio_pct is not None else Decimal("0")
        premio_monto = precio_kg_mxn * pct / Decimal("100")
        precio_con_premio = precio_kg_mxn + premio_monto
        total_venta = kg * precio_con_premio

    return {
        "precio_lb_usd": precio_lb,
        "libras": libras,
        "total_usd": total_usd,
        "total_pesos": total_pesos,
        "tc_efectivo": tc_efectivo,
        "precio_kg_mxn": precio_kg_mxn,
        "premio_pct": premio_pct,
        "premio_monto": premio_monto,
        "precio_con_premio": precio_con_premio,
        "total_venta": total_venta,
    }


def calcular_contenedor_de_modelo(contenedor: TratoVentaContenedor, trato: TratoVenta) -> dict:
    """La misma cadena, tomando los insumos de un contenedor guardado."""
    premio = contenedor.premio_pct if contenedor.premio_pct is not None else trato.premio_pct
    return calcular_contenedor(
        kg=contenedor.kg,
        lme_usd_ton=contenedor.lme_usd_ton,
        descuento_factor=contenedor.descuento_factor,
        precio_lb_usd=contenedor.precio_lb_usd,
        tc1=contenedor.tc1,
        usd_tc1=contenedor.usd_tc1,
        tc2=contenedor.tc2,
        usd_tc2=contenedor.usd_tc2,
        premio_pct=premio,
    )


def resumen_trato(db: Session, trato: TratoVenta) -> dict:
    """Totales del trato: capturado en contenedores vs. vendido en notas."""
    kg_contenedores = Decimal("0")
    total_usd = Decimal("0")
    total_pesos = Decimal("0")
    total_venta = Decimal("0")
    contenedores_calc = []
    for contenedor in trato.contenedores:
        calc = calcular_contenedor_de_modelo(contenedor, trato)
        contenedores_calc.append((contenedor, calc))
        kg_contenedores += _dec(contenedor.kg) or Decimal("0")
        if calc["total_usd"] is not None:
            total_usd += calc["total_usd"]
        if calc["total_pesos"] is not None:
            total_pesos += calc["total_pesos"]
        if calc["total_venta"] is not None:
            total_venta += calc["total_venta"]

    kg_vendidos = Decimal("0")
    notas_rows = []
    for link in trato.notas_link:
        nota = link.nota
        if not nota:
            continue
        aprobada = nota.estado == NotaEstado.aprobada
        kg_nota = _dec(nota.total_kg_neto) or Decimal("0")
        if aprobada:
            kg_vendidos += kg_nota
        notas_rows.append({"link": link, "nota": nota, "aprobada": aprobada, "kg": kg_nota})

    kg_tratados = _dec(trato.kg_tratados) or Decimal("0")
    return {
        "contenedores_calc": contenedores_calc,
        "kg_tratados": kg_tratados,
        "kg_contenedores": kg_contenedores,
        "kg_vendidos": kg_vendidos,
        "kg_restantes": kg_tratados - kg_vendidos,
        "total_usd": total_usd,
        "total_pesos": total_pesos,
        "total_venta": total_venta,
        "notas_rows": notas_rows,
    }


def create_trato(
    db: Session,
    *,
    cliente_id: int,
    material_id: int,
    usuario_id: int | None,
    contrato: str | None,
    fecha_po,
    fecha_vencimiento,
    kg_tratados: Decimal,
    premio_pct: Decimal,
    comentarios: str | None,
) -> TratoVenta:
    if kg_tratados < 0:
        raise ValueError("Los kg tratados no pueden ser negativos.")
    if premio_pct < 0:
        raise ValueError("El premio no puede ser negativo.")
    trato = TratoVenta(
        cliente_id=cliente_id,
        material_id=material_id,
        usuario_id=usuario_id,
        contrato=(contrato or "").strip() or None,
        fecha_po=fecha_po,
        fecha_vencimiento=fecha_vencimiento,
        kg_tratados=kg_tratados,
        premio_pct=premio_pct,
        comentarios=(comentarios or "").strip() or None,
        estado=TratoVentaEstado.abierto,
    )
    db.add(trato)
    db.commit()
    db.refresh(trato)
    return trato


def _asignar_campos_contenedor(contenedor: TratoVentaContenedor, campos: dict) -> None:
    for campo in (
        "orden",
        "numero_contenedor",
        "fecha_carga",
        "kg",
        "peso_bascula_publica",
        "peso_puerto",
        "lme_usd_ton",
        "descuento_factor",
        "precio_lb_usd",
        "tc1",
        "usd_tc1",
        "tc2",
        "usd_tc2",
        "premio_pct",
        "comentarios",
    ):
        if campo in campos:
            setattr(contenedor, campo, campos[campo])


def add_contenedor(db: Session, *, trato_id: int, campos: dict) -> TratoVentaContenedor:
    trato = db.query(TratoVenta).get(trato_id)
    if not trato:
        raise ValueError("El trato no existe.")
    contenedor = TratoVentaContenedor(trato_id=trato.id)
    _asignar_campos_contenedor(contenedor, campos)
    if (_dec(contenedor.kg) or Decimal("0")) < 0:
        raise ValueError("Los kg del contenedor no pueden ser negativos.")
    db.add(contenedor)
    db.commit()
    db.refresh(contenedor)
    return contenedor


def update_contenedor(db: Session, *, contenedor_id: int, campos: dict) -> TratoVentaContenedor:
    contenedor = db.query(TratoVentaContenedor).get(contenedor_id)
    if not contenedor:
        raise ValueError("El contenedor no existe.")
    _asignar_campos_contenedor(contenedor, campos)
    if (_dec(contenedor.kg) or Decimal("0")) < 0:
        raise ValueError("Los kg del contenedor no pueden ser negativos.")
    db.add(contenedor)
    db.commit()
    db.refresh(contenedor)
    return contenedor


def delete_contenedor(db: Session, *, contenedor_id: int) -> None:
    contenedor = db.query(TratoVentaContenedor).get(contenedor_id)
    if not contenedor:
        raise ValueError("El contenedor no existe.")
    db.delete(contenedor)
    db.commit()


def link_nota(db: Session, *, trato_id: int, nota_id: int) -> TratoVentaNota:
    """Vincula una nota de venta aprobada del mismo cliente al trato."""
    trato = db.query(TratoVenta).get(trato_id)
    if not trato:
        raise ValueError("El trato no existe.")
    nota = db.query(Nota).get(nota_id)
    if not nota:
        raise ValueError("La nota no existe.")
    if nota.tipo_operacion != TipoOperacion.venta:
        raise ValueError("Solo se pueden vincular notas de venta.")
    if nota.estado != NotaEstado.aprobada:
        raise ValueError("Solo se pueden vincular notas aprobadas.")
    if nota.cliente_id != trato.cliente_id:
        raise ValueError("La nota pertenece a otro cliente.")
    existente = (
        db.query(TratoVentaNota).filter(TratoVentaNota.nota_id == nota_id).first()
    )
    if existente:
        raise ValueError("Esa nota ya está vinculada a un trato.")
    link = TratoVentaNota(trato_id=trato.id, nota_id=nota.id)
    db.add(link)
    db.commit()
    db.refresh(link)
    return link


def unlink_nota(db: Session, *, trato_id: int, nota_id: int) -> None:
    link = (
        db.query(TratoVentaNota)
        .filter(TratoVentaNota.trato_id == trato_id, TratoVentaNota.nota_id == nota_id)
        .first()
    )
    if not link:
        raise ValueError("Esa nota no está vinculada al trato.")
    db.delete(link)
    db.commit()


def set_completado(
    db: Session, *, trato_id: int, completado: bool, usuario_id: int | None
) -> TratoVenta:
    """El botón "completada" saca el trato de los pendientes de entrega."""
    trato = db.query(TratoVenta).get(trato_id)
    if not trato:
        raise ValueError("El trato no existe.")
    if completado:
        trato.estado = TratoVentaEstado.completado
        trato.completado_at = datetime.utcnow()
        trato.completado_by_user_id = usuario_id
    else:
        trato.estado = TratoVentaEstado.abierto
        trato.completado_at = None
        trato.completado_by_user_id = None
    db.add(trato)
    db.commit()
    db.refresh(trato)
    return trato


def delete_trato(db: Session, *, trato_id: int) -> None:
    """Solo se elimina un trato vacío: sin contenedores y sin notas vinculadas."""
    trato = db.query(TratoVenta).get(trato_id)
    if not trato:
        raise ValueError("El trato no existe.")
    if trato.contenedores:
        raise ValueError("El trato tiene contenedores; elimínalos primero.")
    if trato.notas_link:
        raise ValueError("El trato tiene notas vinculadas; quítalas primero.")
    db.delete(trato)
    db.commit()


def query_tratos(
    db: Session,
    *,
    cliente_id: int | None = None,
    estado: TratoVentaEstado | None = None,
) -> list[TratoVenta]:
    query = (
        db.query(TratoVenta)
        .options(
            joinedload(TratoVenta.cliente),
            joinedload(TratoVenta.material),
            joinedload(TratoVenta.contenedores),
            joinedload(TratoVenta.notas_link).joinedload(TratoVentaNota.nota),
        )
    )
    if cliente_id:
        query = query.filter(TratoVenta.cliente_id == cliente_id)
    if estado:
        query = query.filter(TratoVenta.estado == estado)
    return query.order_by(TratoVenta.created_at.desc(), TratoVenta.id.desc()).all()
