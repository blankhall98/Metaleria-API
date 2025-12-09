# app/api/pricing.py
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from app.services.pricing_service import create_price_version
from app.db.deps import get_db
from app.models import TablaPrecio, TipoOperacion, TipoCliente, Material

router = APIRouter(prefix="/pricing", tags=["pricing"])


class TablaPrecioBase(BaseModel):
    material_id: int
    tipo_operacion: TipoOperacion
    tipo_cliente: TipoCliente
    precio_por_unidad: Decimal

    @field_validator("precio_por_unidad")
    @classmethod
    def validate_price(cls, v: Decimal) -> Decimal:
        if v <= 0:
            raise ValueError("El precio debe ser mayor a 0.")
        return v


class TablaPrecioCreate(TablaPrecioBase):
    pass


class TablaPrecioOut(BaseModel):
    id: int
    material_id: int
    tipo_operacion: TipoOperacion
    tipo_cliente: TipoCliente
    precio_por_unidad: Decimal
    version: int
    vigente_desde: datetime
    vigente_hasta: datetime | None
    activo: bool

    class Config:
        from_attributes = True


@router.get("/", response_model=List[TablaPrecioOut])
def list_pricing(
    material_id: int | None = Query(None),
    only_active: bool = Query(True),
    db: Session = Depends(get_db),
):
    query = db.query(TablaPrecio)
    if material_id is not None:
        query = query.filter(TablaPrecio.material_id == material_id)
    if only_active:
        query = query.filter(TablaPrecio.activo.is_(True))

    rows = (
        query
        .order_by(
            TablaPrecio.material_id,
            TablaPrecio.tipo_operacion,
            TablaPrecio.tipo_cliente,
            TablaPrecio.version.desc(),
        )
        .all()
    )
    return rows

@router.post("/", response_model=TablaPrecioOut, status_code=201)
def create_pricing(
    data: TablaPrecioCreate,
    db: Session = Depends(get_db),
):
    material = db.query(Material).get(data.material_id)
    if not material:
        raise HTTPException(status_code=404, detail="Material no encontrado.")

    tp = create_price_version(
        db,
        material_id=data.material_id,
        tipo_operacion=data.tipo_operacion,
        tipo_cliente=data.tipo_cliente,
        precio=data.precio_por_unidad,
        user_id=None,          # por ahora sin usuario (llamada API interna)
        source="api",
    )
    return tp


@router.get("/{precio_id}", response_model=TablaPrecioOut)
def get_pricing(precio_id: int, db: Session = Depends(get_db)):
    tp = db.query(TablaPrecio).get(precio_id)
    if not tp:
        raise HTTPException(status_code=404, detail="Registro de precio no encontrado.")
    return tp
