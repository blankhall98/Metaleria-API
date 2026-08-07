# app/models/trato.py
import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    ForeignKey,
    DateTime,
    Numeric,
    Enum,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class TratoVentaEstado(str, enum.Enum):
    abierto = "ABIERTO"
    completado = "COMPLETADO"


class TratoVenta(Base):
    """Trato de venta de contenedores (punto 3, fase 2).

    Réplica del Excel del cliente: un contrato con un comprador por un material,
    del que se van cargando contenedores. Los kilos vendidos se LEEN de las
    notas de venta aprobadas vinculadas — nunca se escriben de vuelta.
    """

    __tablename__ = "tratos_venta"

    id = Column(Integer, primary_key=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=False, index=True)
    material_id = Column(Integer, ForeignKey("materiales.id"), nullable=False, index=True)
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    contrato = Column(String(100), nullable=True)
    fecha_po = Column(Date, nullable=True)
    fecha_vencimiento = Column(Date, nullable=True)

    kg_tratados = Column(Numeric(14, 3), nullable=False, default=0)
    # Porcentaje por defecto del premio; cada contenedor puede sobrescribirlo
    # (el Excel muestra 5.5 % casi siempre y 6 % a veces).
    premio_pct = Column(Numeric(6, 3), nullable=False, default=Decimal("5.5"))

    comentarios = Column(String(500), nullable=True)

    estado = Column(
        Enum(
            TratoVentaEstado,
            name="trato_venta_estado",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        index=True,
        default=TratoVentaEstado.abierto,
    )
    completado_at = Column(DateTime, nullable=True)
    completado_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    cliente = relationship("Cliente")
    material = relationship("Material")
    usuario = relationship("User", foreign_keys=[usuario_id])
    completado_by = relationship("User", foreign_keys=[completado_by_user_id])
    contenedores = relationship(
        "TratoVentaContenedor",
        back_populates="trato",
        cascade="all, delete-orphan",
        order_by="TratoVentaContenedor.id",
    )
    notas_link = relationship(
        "TratoVentaNota",
        back_populates="trato",
        cascade="all, delete-orphan",
        order_by="TratoVentaNota.id",
    )


class TratoVentaContenedor(Base):
    """Un contenedor (caja) dentro de un trato.

    La cadena de cálculo vive en trato_service.calcular_contenedor; aquí solo
    se guardan los insumos capturados. precio_lb_usd se captura directo cuando
    el material no cotiza por LME (caso EBONY del Excel).
    """

    __tablename__ = "tratos_venta_contenedores"

    id = Column(Integer, primary_key=True, index=True)
    trato_id = Column(Integer, ForeignKey("tratos_venta.id"), nullable=False, index=True)

    orden = Column(Integer, nullable=True)
    numero_contenedor = Column(String(60), nullable=True)
    fecha_carga = Column(Date, nullable=True)

    # kg queda en 0 hasta que el contenedor se carga (así lo lleva el Excel).
    kg = Column(Numeric(14, 3), nullable=False, default=0)
    # Columnas pedidas en la junta del 07-ago; el Excel no las tiene.
    peso_bascula_publica = Column(Numeric(14, 3), nullable=True)
    peso_puerto = Column(Numeric(14, 3), nullable=True)

    lme_usd_ton = Column(Numeric(12, 5), nullable=True)
    descuento_factor = Column(Numeric(8, 5), nullable=True)
    precio_lb_usd = Column(Numeric(12, 5), nullable=True)

    # El pago puede partirse en dos tipos de cambio (usd_tc1 × TC1 + usd_tc2 × TC2).
    tc1 = Column(Numeric(10, 4), nullable=True)
    usd_tc1 = Column(Numeric(14, 2), nullable=True)
    tc2 = Column(Numeric(10, 4), nullable=True)
    usd_tc2 = Column(Numeric(14, 2), nullable=True)

    premio_pct = Column(Numeric(6, 3), nullable=True)
    comentarios = Column(String(255), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    trato = relationship("TratoVenta", back_populates="contenedores")


class TratoVentaNota(Base):
    """Vínculo trato ↔ nota de venta. Los kilos vendidos se leen de la nota."""

    __tablename__ = "tratos_venta_notas"
    __table_args__ = (UniqueConstraint("nota_id", name="uq_tratos_venta_notas_nota_id"),)

    id = Column(Integer, primary_key=True, index=True)
    trato_id = Column(Integer, ForeignKey("tratos_venta.id"), nullable=False, index=True)
    nota_id = Column(Integer, ForeignKey("notas.id"), nullable=False, index=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    trato = relationship("TratoVenta", back_populates="notas_link")
    nota = relationship("Nota")
