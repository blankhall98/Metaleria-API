# app/services/conversion_service.py
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import (
    ConversionMaterial,
    ConversionMaterialReversion,
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


def reverse_conversion(
    db: Session,
    conversion: ConversionMaterial,
    *,
    usuario_id: int | None,
    comentario: str | None = None,
) -> ConversionMaterial:
    existing_link = (
        db.query(ConversionMaterialReversion)
        .filter(ConversionMaterialReversion.conversion_id == conversion.id)
        .first()
    )
    if existing_link:
        raise ValueError("Esta conversion ya fue revertida.")

    is_reversal = (
        db.query(ConversionMaterialReversion)
        .filter(ConversionMaterialReversion.reversal_conversion_id == conversion.id)
        .first()
    )
    if is_reversal:
        raise ValueError("No puedes revertir una reversion de conversion.")

    inv_origen = _get_or_create_inventario(db, sucursal_id=conversion.sucursal_id, material_id=conversion.material_origen_id)
    inv_destino = _get_or_create_inventario(db, sucursal_id=conversion.sucursal_id, material_id=conversion.material_destino_id)

    restore_origin = Decimal(str(conversion.cantidad_origen or 0))
    restore_dest = Decimal(str(conversion.cantidad_destino or 0))
    stock_destino = Decimal(str(inv_destino.stock_actual or 0))
    if restore_dest > stock_destino:
        raise ValueError("No hay stock suficiente en el material destino para revertir esta conversion.")

    now = datetime.utcnow()
    inv_destino.stock_actual = stock_destino - restore_dest
    inv_origen.stock_actual = Decimal(str(inv_origen.stock_actual or 0)) + restore_origin
    inv_destino.updated_at = now
    inv_origen.updated_at = now

    reversal = ConversionMaterial(
        sucursal_id=conversion.sucursal_id,
        material_origen_id=conversion.material_destino_id,
        cantidad_origen=restore_dest,
        material_destino_id=conversion.material_origen_id,
        cantidad_destino=restore_origin,
        usuario_id=usuario_id,
        comentario=comentario or f"Reversion de conversion #{conversion.id}",
        created_at=now,
    )
    db.add(reversal)
    db.flush()

    base_comment = comentario or f"Reversion conversion #{conversion.id}"
    db.add(inv_origen)
    db.add(inv_destino)
    db.add(
        InventarioMovimiento(
            inventario_id=inv_destino.id,
            nota_id=None,
            nota_material_id=None,
            tipo="conversion",
            cantidad_kg=restore_dest * Decimal("-1"),
            saldo_resultante=inv_destino.stock_actual,
            comentario=f"{base_comment} | Salida",
            usuario_id=usuario_id,
            created_at=now,
        )
    )
    db.add(
        InventarioMovimiento(
            inventario_id=inv_origen.id,
            nota_id=None,
            nota_material_id=None,
            tipo="conversion",
            cantidad_kg=restore_origin,
            saldo_resultante=inv_origen.stock_actual,
            comentario=f"{base_comment} | Entrada",
            usuario_id=usuario_id,
            created_at=now,
        )
    )
    db.add(
        ConversionMaterialReversion(
            conversion_id=conversion.id,
            reversal_conversion_id=reversal.id,
            usuario_id=usuario_id,
            created_at=now,
        )
    )
    db.commit()
    db.refresh(reversal)
    return reversal
