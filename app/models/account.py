# app/models/account.py
from datetime import datetime

from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime, Numeric, Table
from sqlalchemy.orm import relationship

from app.db.base import Base


class Cuenta(Base):
    __tablename__ = "cuentas"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(120), nullable=False)
    tipo = Column(String(30), nullable=True)
    banco = Column(String(120), nullable=True)
    numero = Column(String(80), nullable=True)
    clabe = Column(String(80), nullable=True)
    titular = Column(String(120), nullable=True)
    referencia = Column(String(120), nullable=True)
    activo = Column(Boolean, nullable=False, default=True)

    sucursal_id = Column(Integer, ForeignKey("sucursales.id"), nullable=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=True, index=True)
    proveedor_id = Column(Integer, ForeignKey("proveedores.id"), nullable=True, index=True)
    comisionario_id = Column(Integer, ForeignKey("comisionarios.id"), nullable=True, index=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    sucursal = relationship("Sucursal")
    cliente = relationship("Cliente")
    proveedor = relationship("Proveedor")
    comisionario = relationship("Comisionario", back_populates="cuentas")

    @property
    def display_label(self) -> str:
        parts = [self.nombre]
        if self.banco:
            parts.append(self.banco)
        if self.numero:
            last4 = self.numero[-4:] if len(self.numero) >= 4 else self.numero
            parts.append(f"****{last4}")
        return " | ".join([p for p in parts if p])


cuentas_scrap360_sucursales = Table(
    "cuentas_scrap360_sucursales",
    Base.metadata,
    Column("cuenta_id", Integer, ForeignKey("cuentas_scrap360.id"), primary_key=True),
    Column("sucursal_id", Integer, ForeignKey("sucursales.id"), primary_key=True),
)


class CuentaScrap360(Base):
    __tablename__ = "cuentas_scrap360"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(120), nullable=False)
    tipo = Column(String(20), nullable=False)
    saldo_inicial = Column(Numeric(12, 2), nullable=False, default=0)
    saldo_actual = Column(Numeric(12, 2), nullable=False, default=0)
    activo = Column(Boolean, nullable=False, default=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    sucursales = relationship("Sucursal", secondary=cuentas_scrap360_sucursales)
    movimientos = relationship("CuentaScrap360Movimiento", back_populates="cuenta", cascade="all, delete-orphan")


class CuentaScrap360Movimiento(Base):
    __tablename__ = "cuentas_scrap360_movimientos"

    id = Column(Integer, primary_key=True, index=True)
    cuenta_id = Column(Integer, ForeignKey("cuentas_scrap360.id"), nullable=False, index=True)
    nota_id = Column(Integer, ForeignKey("notas.id"), nullable=True, index=True)
    nota_pago_id = Column(Integer, ForeignKey("nota_pagos.id"), nullable=True, index=True)
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    tipo = Column(String(20), nullable=False)  # ingreso, egreso, ajuste
    monto = Column(Numeric(12, 2), nullable=False, default=0)
    saldo_resultante = Column(Numeric(12, 2), nullable=False, default=0)
    comentario = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    cuenta = relationship("CuentaScrap360", back_populates="movimientos")
    nota = relationship("Nota")
    usuario = relationship("User")
