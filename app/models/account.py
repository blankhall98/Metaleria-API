# app/models/account.py
from datetime import datetime

from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime
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

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    sucursal = relationship("Sucursal")
    cliente = relationship("Cliente")
    proveedor = relationship("Proveedor")

    @property
    def display_label(self) -> str:
        parts = [self.nombre]
        if self.banco:
            parts.append(self.banco)
        if self.numero:
            last4 = self.numero[-4:] if len(self.numero) >= 4 else self.numero
            parts.append(f"****{last4}")
        return " | ".join([p for p in parts if p])
