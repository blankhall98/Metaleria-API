# app/services/pricing_service.py
from datetime import datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.models import TablaPrecio, TipoOperacion, TipoCliente, PriceChangeLog


def create_price_version(
    db: Session,
    *,
    material_id: int,
    tipo_operacion: TipoOperacion,
    tipo_cliente: TipoCliente,
    precio: Decimal,
    user_id: int | None = None,
    source: str = "api",
) -> TablaPrecio:
    """
    Crea una nueva versión de precio para (material, tipo_operacion, tipo_cliente),
    desactiva versiones previas activas y registra el cambio en PriceChangeLog.
    """
    now = datetime.utcnow()

    existing_q = db.query(TablaPrecio).filter(
        TablaPrecio.material_id == material_id,
        TablaPrecio.tipo_operacion == tipo_operacion,
        TablaPrecio.tipo_cliente == tipo_cliente,
    )

    last = (
        existing_q
        .order_by(TablaPrecio.version.desc())
        .first()
    )

    old_price = last.precio_por_unidad if last else None
    old_version = last.version if last else None
    next_version = (last.version + 1) if last else 1

    # Desactivar versiones activas anteriores
    for row in existing_q.filter(TablaPrecio.activo.is_(True)).all():
        row.activo = False
        row.vigente_hasta = now
        db.add(row)

    # Nueva versión
    tp = TablaPrecio(
        material_id=material_id,
        tipo_operacion=tipo_operacion,
        tipo_cliente=tipo_cliente,
        precio_por_unidad=precio,
        version=next_version,
        vigente_desde=now,
        vigente_hasta=None,
        activo=True,
    )
    db.add(tp)
    db.flush()  # para tener tp.id si lo necesitáramos

    # Log de auditoría
    log = PriceChangeLog(
        material_id=material_id,
        tipo_operacion=tipo_operacion,
        tipo_cliente=tipo_cliente,
        old_precio_por_unidad=old_price,
        new_precio_por_unidad=precio,
        old_version=old_version,
        new_version=next_version,
        user_id=user_id,
        source=source,
        created_at=now,
    )
    db.add(log)

    db.commit()
    db.refresh(tp)
    return tp
