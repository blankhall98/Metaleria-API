# app/models/comision.py
import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Column,
    Integer,
    String,
    Boolean,
    ForeignKey,
    DateTime,
    Numeric,
    Enum,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class Comisionario(Base):
    __tablename__ = "comisionarios"

    id = Column(Integer, primary_key=True, index=True)
    nombre_completo = Column(String(200), nullable=False, index=True)
    telefono = Column(String(50), nullable=True, index=True)
    correo_electronico = Column(String(200), nullable=True, index=True)
    activo = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    cuentas = relationship("Cuenta", back_populates="comisionario")
    notas = relationship("ComisionarioNota", back_populates="comisionario", cascade="all, delete-orphan")


class ComisionarioNotaEstado(str, enum.Enum):
    borrador = "BORRADOR"
    aprobada = "APROBADA"
    cancelada = "CANCELADA"


class ComisionarioNota(Base):
    __tablename__ = "comisionario_notas"

    id = Column(Integer, primary_key=True, index=True)
    comisionario_id = Column(Integer, ForeignKey("comisionarios.id"), nullable=False, index=True)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id"), nullable=False, index=True)
    admin_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    estado = Column(
        Enum(
            ComisionarioNotaEstado,
            name="comisionario_nota_estado",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        index=True,
        default=ComisionarioNotaEstado.aprobada,
    )

    total_kg = Column(Numeric(12, 3), nullable=False, default=0)
    total_monto = Column(Numeric(12, 2), nullable=False, default=0)
    monto_pagado = Column(Numeric(12, 2), nullable=False, default=0)

    comentarios_admin = Column(String(255), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    comisionario = relationship("Comisionario", back_populates="notas")
    sucursal = relationship("Sucursal")
    admin = relationship("User")
    materiales = relationship(
        "ComisionarioNotaMaterial",
        back_populates="nota",
        cascade="all, delete-orphan",
    )
    pagos = relationship(
        "ComisionarioPago",
        back_populates="nota",
        cascade="all, delete-orphan",
    )


class ComisionarioNotaMaterial(Base):
    __tablename__ = "comisionario_nota_materiales"

    id = Column(Integer, primary_key=True, index=True)
    nota_id = Column(Integer, ForeignKey("comisionario_notas.id"), nullable=False, index=True)
    material_id = Column(Integer, ForeignKey("materiales.id"), nullable=False, index=True)

    kg_neto = Column(Numeric(12, 3), nullable=False, default=0)
    precio_por_kg = Column(Numeric(12, 5), nullable=False, default=0)
    subtotal = Column(Numeric(12, 2), nullable=False, default=0)

    nota = relationship("ComisionarioNota", back_populates="materiales")
    material = relationship("Material")


class ComisionarioPago(Base):
    __tablename__ = "comisionario_pagos"

    id = Column(Integer, primary_key=True, index=True)
    nota_id = Column(Integer, ForeignKey("comisionario_notas.id"), nullable=False, index=True)
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    cuenta_id = Column(Integer, ForeignKey("cuentas.id"), nullable=True, index=True)
    cuenta_scrap360_id = Column(Integer, ForeignKey("cuentas_scrap360.id"), nullable=True, index=True)

    monto = Column(Numeric(12, 2), nullable=False, default=Decimal("0"))
    metodo_pago = Column(String(50), nullable=True)
    cuenta_financiera = Column(String(100), nullable=True)
    comentario = Column(String(255), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    nota = relationship("ComisionarioNota", back_populates="pagos")
    usuario = relationship("User")
    cuenta = relationship("Cuenta")
    cuenta_scrap360 = relationship("CuentaScrap360")
