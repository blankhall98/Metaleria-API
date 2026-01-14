# app/models/cash.py
import enum
from datetime import datetime, date
from decimal import Decimal

from sqlalchemy import (
    Column,
    Integer,
    ForeignKey,
    DateTime,
    Date,
    Numeric,
    String,
    Enum,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class CorteCajaEstado(str, enum.Enum):
    abierto = "ABIERTO"
    cerrado = "CERRADO"


class CorteCajaMovimientoTipo(str, enum.Enum):
    ingreso = "INGRESO"
    egreso = "EGRESO"
    retiro = "RETIRO"
    deposito = "DEPOSITO"


class CorteCaja(Base):
    __tablename__ = "cortes_caja"
    __table_args__ = (
        UniqueConstraint("sucursal_id", "fecha", name="uq_corte_caja_sucursal_fecha"),
    )

    id = Column(Integer, primary_key=True, index=True)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id"), nullable=False, index=True)
    abierto_por_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    cerrado_por_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    fecha = Column(Date, nullable=False, index=True)
    estado = Column(
        Enum(
            CorteCajaEstado,
            name="corte_caja_estado",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        default=CorteCajaEstado.abierto,
        index=True,
    )

    saldo_inicial = Column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    saldo_calculado = Column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    saldo_cierre = Column(Numeric(12, 2), nullable=True)
    diferencia = Column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    motivo_diferencia = Column(String(255), nullable=True)
    comentarios_cierre = Column(Text, nullable=True)

    opened_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    sucursal = relationship("Sucursal")
    abierto_por = relationship("User", foreign_keys=[abierto_por_id])
    cerrado_por = relationship("User", foreign_keys=[cerrado_por_id])
    gastos = relationship("CorteCajaGasto", back_populates="corte", cascade="all, delete-orphan")
    movimientos = relationship("CorteCajaMovimiento", back_populates="corte", cascade="all, delete-orphan")
    denominaciones = relationship("CorteCajaDenominacion", back_populates="corte", cascade="all, delete-orphan")


class CorteCajaGasto(Base):
    __tablename__ = "corte_caja_gastos"

    id = Column(Integer, primary_key=True, index=True)
    corte_id = Column(Integer, ForeignKey("cortes_caja.id"), nullable=False, index=True)
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    descripcion = Column(String(255), nullable=False)
    categoria = Column(String(80), nullable=True)
    monto = Column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    corte = relationship("CorteCaja", back_populates="gastos")
    usuario = relationship("User")


class CorteCajaMovimiento(Base):
    __tablename__ = "corte_caja_movimientos"

    id = Column(Integer, primary_key=True, index=True)
    corte_id = Column(Integer, ForeignKey("cortes_caja.id"), nullable=False, index=True)
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    tipo = Column(
        Enum(
            CorteCajaMovimientoTipo,
            name="corte_caja_movimiento_tipo",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
    )
    descripcion = Column(String(255), nullable=False)
    monto = Column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    corte = relationship("CorteCaja", back_populates="movimientos")
    usuario = relationship("User")


class CorteCajaDenominacion(Base):
    __tablename__ = "corte_caja_denominaciones"

    id = Column(Integer, primary_key=True, index=True)
    corte_id = Column(Integer, ForeignKey("cortes_caja.id"), nullable=False, index=True)
    valor = Column(Numeric(12, 2), nullable=False)
    cantidad = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    corte = relationship("CorteCaja", back_populates="denominaciones")
