# app/models/llamada.py
import enum
from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    ForeignKey,
    DateTime,
    Numeric,
    Enum,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class LlamadaProveedorEstatus(str, enum.Enum):
    """Estatus de entrega de una llamada (punto 2, fase 2).

    El Excel de la clienta usa tres estados reales, no un booleano:
    PENDIENTE, ENTREGADO y NO CONFIRMÓ.
    """

    pendiente = "PENDIENTE"
    entregado = "ENTREGADO"
    no_confirmo = "NO_CONFIRMO"


class LlamadaProveedor(Base):
    """Cabecera de una llamada de seguimiento con un proveedor.

    Una llamada agrupa N materiales cotizados (líneas). El precio cotizado va
    desacoplado de las tablas de precios: es lo que se habló por teléfono.
    """

    __tablename__ = "llamadas_proveedor"

    id = Column(Integer, primary_key=True, index=True)
    proveedor_id = Column(Integer, ForeignKey("proveedores.id"), nullable=False, index=True)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id"), nullable=True, index=True)
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    fecha = Column(Date, nullable=False, index=True)
    # A veces es una fecha, a veces texto libre ("Semana del 25 al 29 de mayo").
    fecha_estimada_entrega = Column(String(120), nullable=True)
    comentarios = Column(String(500), nullable=True)

    estatus = Column(
        Enum(
            LlamadaProveedorEstatus,
            name="llamada_proveedor_estatus",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        index=True,
        default=LlamadaProveedorEstatus.pendiente,
    )
    entregada_at = Column(DateTime, nullable=True)
    entregada_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    proveedor = relationship("Proveedor")
    sucursal = relationship("Sucursal")
    usuario = relationship("User", foreign_keys=[usuario_id])
    entregada_by = relationship("User", foreign_keys=[entregada_by_user_id])
    materiales = relationship(
        "LlamadaProveedorMaterial",
        back_populates="llamada",
        cascade="all, delete-orphan",
        order_by="LlamadaProveedorMaterial.id",
    )


class LlamadaProveedorMaterial(Base):
    """Línea de material cotizado en una llamada.

    material_id nulo = "sin definir aún" (así viene en el Excel). El precio se
    captura como texto tal cual se dijo ("Lista Viernes 22 Mayo"); cuando es un
    número también se guarda en precio_por_kg para poder formatearlo.
    """

    __tablename__ = "llamadas_proveedor_materiales"

    id = Column(Integer, primary_key=True, index=True)
    llamada_id = Column(Integer, ForeignKey("llamadas_proveedor.id"), nullable=False, index=True)
    material_id = Column(Integer, ForeignKey("materiales.id"), nullable=True, index=True)

    precio_por_kg = Column(Numeric(12, 5), nullable=True)
    precio_texto = Column(String(120), nullable=True)
    kg_aproximados = Column(Numeric(12, 3), nullable=True)

    llamada = relationship("LlamadaProveedor", back_populates="materiales")
    material = relationship("Material")
