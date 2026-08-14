# app/services/llamada_service.py
"""Bitácora de llamadas con proveedores (punto 2, fase 2).

La bitácora es un registro de seguimiento, no un documento contable: no toca
inventario ni saldos. Aun así el servicio es dueño de sus transacciones, como
el resto de los servicios del sistema.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from sqlalchemy import or_
from sqlalchemy.orm import Session, joinedload

from app.models import (
    LlamadaProveedor,
    LlamadaProveedorEstatus,
    LlamadaProveedorMaterial,
    Proveedor,
)

ESTATUS_LABELS = {
    LlamadaProveedorEstatus.pendiente: "Pendiente",
    LlamadaProveedorEstatus.entregado: "Entregado",
    LlamadaProveedorEstatus.no_confirmo: "No confirmó",
}


def parse_precio(raw: str | None) -> tuple[Decimal | None, str | None]:
    """El precio del Excel a veces es número y a veces texto libre.

    Devuelve (precio_numerico, precio_texto): si el texto se puede leer como
    número queda en ambos; si no, solo como texto.
    """
    texto = (raw or "").strip()
    if not texto:
        return None, None
    limpio = texto.replace("$", "").replace(",", "").strip()
    try:
        return Decimal(limpio), texto
    except InvalidOperation:
        return None, texto


def create_llamada(
    db: Session,
    *,
    proveedor_id: int,
    usuario_id: int | None,
    fecha: date,
    fecha_estimada_entrega: str | None,
    comentarios: str | None,
    materiales: list[dict],
) -> LlamadaProveedor:
    """Registra una llamada con sus líneas de material cotizado.

    materiales: [{material_id: int|None, precio_raw: str|None, kg_aproximados: Decimal|None}]
    Una llamada puede no tener líneas (seguimiento puro en comentarios).
    """
    proveedor = db.query(Proveedor).get(proveedor_id)
    if not proveedor:
        raise ValueError("El proveedor no existe.")

    llamada = LlamadaProveedor(
        proveedor_id=proveedor_id,
        sucursal_id=proveedor.sucursal_id,
        usuario_id=usuario_id,
        fecha=fecha,
        fecha_estimada_entrega=(fecha_estimada_entrega or "").strip() or None,
        comentarios=(comentarios or "").strip() or None,
        estatus=LlamadaProveedorEstatus.pendiente,
    )
    db.add(llamada)
    db.flush()

    for linea in materiales:
        precio_num, precio_texto = parse_precio(linea.get("precio_raw"))
        db.add(
            LlamadaProveedorMaterial(
                llamada_id=llamada.id,
                material_id=linea.get("material_id"),
                precio_por_kg=precio_num,
                precio_texto=precio_texto,
                kg_aproximados=linea.get("kg_aproximados"),
            )
        )

    db.commit()
    db.refresh(llamada)
    return llamada


def _derivar_estatus_cabecera(llamada: LlamadaProveedor) -> LlamadaProveedorEstatus:
    """El estatus de la cabecera se deriva de las líneas (fase 2).

    Alguna pendiente → PENDIENTE (la llamada sigue viva); todas entregadas →
    ENTREGADO; todas sin confirmar → NO_CONFIRMO; mezcla de entregado y no
    confirmó sin pendientes → ENTREGADO (lo confirmado ya llegó y no queda
    trabajo por hacer). Una llamada sin líneas conserva su estatus manual.
    """
    lineas = llamada.materiales or []
    if not lineas:
        return llamada.estatus
    estados = {linea.estatus for linea in lineas}
    if LlamadaProveedorEstatus.pendiente in estados:
        return LlamadaProveedorEstatus.pendiente
    if estados == {LlamadaProveedorEstatus.no_confirmo}:
        return LlamadaProveedorEstatus.no_confirmo
    return LlamadaProveedorEstatus.entregado


def _aplicar_estatus(obj, estatus: LlamadaProveedorEstatus, usuario_id: int | None) -> None:
    """Asigna estatus + auditoría de entrega a una llamada o a una línea."""
    obj.estatus = estatus
    if estatus == LlamadaProveedorEstatus.entregado:
        if obj.entregada_at is None:
            obj.entregada_at = datetime.utcnow()
            obj.entregada_by_user_id = usuario_id
    else:
        obj.entregada_at = None
        obj.entregada_by_user_id = None


def set_estatus(
    db: Session,
    *,
    llamada_id: int,
    estatus: LlamadaProveedorEstatus,
    usuario_id: int | None,
) -> LlamadaProveedor:
    """Cambia el estatus de la llamada COMPLETA; ENTREGADO deja auditoría.

    Cascadea a todas las líneas: si no, el estatus derivado de las líneas
    contradiría el que se acaba de fijar a mano.
    """
    llamada = db.query(LlamadaProveedor).get(llamada_id)
    if not llamada:
        raise ValueError("La llamada no existe.")
    for linea in llamada.materiales:
        _aplicar_estatus(linea, estatus, usuario_id)
        db.add(linea)
    _aplicar_estatus(llamada, estatus, usuario_id)
    db.add(llamada)
    db.commit()
    db.refresh(llamada)
    return llamada


def set_material_estatus(
    db: Session,
    *,
    material_linea_id: int,
    estatus: LlamadaProveedorEstatus,
    usuario_id: int | None,
) -> LlamadaProveedor:
    """Marca la entrega de UNA línea (un viaje); la cabecera se deriva.

    Devuelve la llamada para que la ruta valide el alcance de sucursal.
    """
    linea = db.query(LlamadaProveedorMaterial).get(material_linea_id)
    if not linea:
        raise ValueError("La línea de material no existe.")
    llamada = linea.llamada
    _aplicar_estatus(linea, estatus, usuario_id)
    db.add(linea)

    derivado = _derivar_estatus_cabecera(llamada)
    if derivado != llamada.estatus:
        _aplicar_estatus(llamada, derivado, usuario_id)
    db.add(llamada)
    db.commit()
    db.refresh(llamada)
    return llamada


def delete_llamada(db: Session, *, llamada_id: int) -> None:
    """Elimina una llamada y sus líneas. La bitácora no es contable: borrar es seguro."""
    llamada = db.query(LlamadaProveedor).get(llamada_id)
    if not llamada:
        raise ValueError("La llamada no existe.")
    db.delete(llamada)
    db.commit()


def query_llamadas(
    db: Session,
    *,
    proveedor_id: int | None = None,
    estatus: LlamadaProveedorEstatus | None = None,
    sucursal_ids: list[int] | None = None,
) -> list[LlamadaProveedor]:
    query = (
        db.query(LlamadaProveedor)
        .options(
            joinedload(LlamadaProveedor.proveedor),
            joinedload(LlamadaProveedor.sucursal),
            joinedload(LlamadaProveedor.usuario),
            joinedload(LlamadaProveedor.materiales).joinedload(LlamadaProveedorMaterial.material),
        )
    )
    if proveedor_id:
        query = query.filter(LlamadaProveedor.proveedor_id == proveedor_id)
    if estatus:
        query = query.filter(LlamadaProveedor.estatus == estatus)
    if sucursal_ids:
        # Igual que las notas de comisión: lo que no tiene sucursal es visible
        # para cualquier admin con alcance.
        query = query.filter(
            or_(
                LlamadaProveedor.sucursal_id.in_(sucursal_ids),
                LlamadaProveedor.sucursal_id.is_(None),
            )
        )
    return (
        query.order_by(LlamadaProveedor.fecha.desc(), LlamadaProveedor.id.desc()).all()
    )
