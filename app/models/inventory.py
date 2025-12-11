# app/models/inventory.py
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Column, Integer, ForeignKey, Numeric, String, DateTime
from sqlalchemy.orm import relationship

from app.db.base import Base


class Inventario(Base):
    __tablename__ = "inventarios"

    id = Column(Integer, primary_key=True, index=True)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id"), nullable=False, index=True)
    material_id = Column(Integer, ForeignKey("materiales.id"), nullable=False, index=True)

    stock_inicial = Column(Numeric(12, 3), nullable=False, default=Decimal("0"))
    stock_actual = Column(Numeric(12, 3), nullable=False, default=Decimal("0"))

    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    sucursal = relationship("Sucursal")
    material = relationship("Material")
    movimientos = relationship("InventarioMovimiento", back_populates="inventario", cascade="all, delete-orphan")


class InventarioMovimiento(Base):
    __tablename__ = "inventario_movimientos"

    id = Column(Integer, primary_key=True, index=True)
    inventario_id = Column(Integer, ForeignKey("inventarios.id"), nullable=False, index=True)
    nota_id = Column(Integer, ForeignKey("notas.id"), nullable=True, index=True)
    nota_material_id = Column(Integer, ForeignKey("nota_materiales.id"), nullable=True, index=True)

    tipo = Column(String(20), nullable=False)  # compra, venta, ajuste
    cantidad_kg = Column(Numeric(12, 3), nullable=False)
    saldo_resultante = Column(Numeric(12, 3), nullable=False)
    comentario = Column(String(255), nullable=True)
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    inventario = relationship("Inventario", back_populates="movimientos")
    nota = relationship("Nota")


class MovimientoContable(Base):
    __tablename__ = "movimientos_contables"

    id = Column(Integer, primary_key=True, index=True)
    nota_id = Column(Integer, ForeignKey("notas.id"), nullable=True, index=True)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id"), nullable=True, index=True)
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    tipo = Column(String(20), nullable=False)  # compra, venta, ajuste
    monto = Column(Numeric(12, 2), nullable=False)
    metodo_pago = Column(String(50), nullable=True)
    cuenta_financiera = Column(String(100), nullable=True)
    comentario = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    nota = relationship("Nota")
    sucursal = relationship("Sucursal")
