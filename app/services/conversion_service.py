# app/services/conversion_service.py
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    ConversionMaterial,
    Inventario,
    InventarioMovimiento,
    Material,
    Sucursal,
)


def _get_or_create_inventario(db: Session, *, sucursal_id: int, material_id: int) -> Inventario:
    inv = (
        db.query(Inventario)
        .filter(Inventario.sucursal_id == sucursal_id, Inventario.material_id == material_id)
        .first()
    )
    if inv:
        return inv
    inv = Inventario(
        sucursal_id=sucursal_id,
        material_id=material_id,
        stock_inicial=Decimal("0"),
        stock_actual=Decimal("0"),
    )
    db.add(inv)
    db.flush()
    return inv


def create_conversion(
    db: Session,
    *,
    sucursal_id: int,
    material_origen_id: int,
    cantidad_origen: Decimal,
    material_destino_id: int,
    cantidad_destino: Decimal,
    usuario_id: int | None,
    comentario: str | None = None,
) -> ConversionMaterial:
    if sucursal_id <= 0:
        raise ValueError("Sucursal invalida.")
    if material_origen_id == material_destino_id:
        raise ValueError("El material de origen y destino deben ser diferentes.")
    if cantidad_origen <= 0:
        raise ValueError("La cantidad de origen debe ser mayor a 0.")
    if cantidad_destino <= 0:
        raise ValueError("La cantidad de destino debe ser mayor a 0.")

    if not db.get(Sucursal, sucursal_id):
        raise ValueError("Sucursal no encontrada.")
    mat_origen = db.get(Material, material_origen_id)
    if not mat_origen:
        raise ValueError("Material de origen no encontrado.")
    mat_destino = db.get(Material, material_destino_id)
    if not mat_destino:
        raise ValueError("Material de destino no encontrado.")

    inv_origen = _get_or_create_inventario(db, sucursal_id=sucursal_id, material_id=material_origen_id)
    stock_origen = Decimal(str(inv_origen.stock_actual or 0))
    if cantidad_origen > stock_origen:
        raise ValueError("Stock insuficiente en el material de origen.")

    inv_destino = _get_or_create_inventario(db, sucursal_id=sucursal_id, material_id=material_destino_id)
    stock_destino = Decimal(str(inv_destino.stock_actual or 0))

    inv_origen.stock_actual = stock_origen - cantidad_origen
    inv_destino.stock_actual = stock_destino + cantidad_destino
    now = datetime.utcnow()
    inv_origen.updated_at = now
    inv_destino.updated_at = now

    conversion = ConversionMaterial(
        sucursal_id=sucursal_id,
        material_origen_id=material_origen_id,
        cantidad_origen=cantidad_origen,
        material_destino_id=material_destino_id,
        cantidad_destino=cantidad_destino,
        usuario_id=usuario_id,
        comentario=comentario or None,
        created_at=now,
    )
    db.add(conversion)
    db.flush()

    base_comment = comentario or f"Conversion #{conversion.id}: {mat_origen.nombre} -> {mat_destino.nombre}"
    mov_salida = InventarioMovimiento(
        inventario_id=inv_origen.id,
        nota_id=None,
        nota_material_id=None,
        tipo="conversion",
        cantidad_kg=cantidad_origen * Decimal("-1"),
        saldo_resultante=inv_origen.stock_actual,
        comentario=f"{base_comment} | Salida",
        usuario_id=usuario_id,
        created_at=now,
    )
    mov_entrada = InventarioMovimiento(
        inventario_id=inv_destino.id,
        nota_id=None,
        nota_material_id=None,
        tipo="conversion",
        cantidad_kg=cantidad_destino,
        saldo_resultante=inv_destino.stock_actual,
        comentario=f"{base_comment} | Entrada",
        usuario_id=usuario_id,
        created_at=now,
    )
    db.add(inv_origen)
    db.add(inv_destino)
    db.add(mov_salida)
    db.add(mov_entrada)
    db.commit()
    db.refresh(conversion)
    return conversion
