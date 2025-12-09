# app/models/pricing.py
import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class TipoOperacion(str, enum.Enum):
    compra = "compra"
    venta = "venta"


class TipoCliente(str, enum.Enum):
    regular = "regular"
    mayorista = "mayorista"
    menudeo = "menudeo"


class TablaPrecio(Base):
    __tablename__ = "tablas_precios"
    __table_args__ = (
        UniqueConstraint(
            "material_id",
            "tipo_operacion",
            "tipo_cliente",
            "version",
            name="uq_tabla_precio_material_tipo_version",
        ),
    )

    id = Column(Integer, primary_key=True, index=True)

    material_id = Column(Integer, ForeignKey("materiales.id"), nullable=False, index=True)
    tipo_operacion = Column(
        Enum(TipoOperacion, name="tipo_operacion"),
        nullable=False,
    )
    tipo_cliente = Column(
        Enum(TipoCliente, name="tipo_cliente"),
        nullable=False,
    )

    precio_por_unidad = Column(Numeric(10, 2), nullable=False)

    # Versionado de la tabla de precios por combinación material + tipo_operacion + tipo_cliente
    version = Column(Integer, nullable=False, default=1)

    vigente_desde = Column(DateTime, nullable=False, default=datetime.utcnow)
    vigente_hasta = Column(DateTime, nullable=True)

    activo = Column(Boolean, nullable=False, default=True)

    material = relationship("Material", back_populates="tablas_precios")
