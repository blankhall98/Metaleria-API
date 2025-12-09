# app/models/partner.py
from sqlalchemy import Column, Integer, String, Boolean

from app.db.base import Base


class Proveedor(Base):
    __tablename__ = "proveedores"

    id = Column(Integer, primary_key=True, index=True)
    nombre_completo = Column(String(200), nullable=False, index=True)

    # Datos de contacto (opcionales, pero útiles para búsqueda)
    telefono = Column(String(50), nullable=True, index=True)
    correo_electronico = Column(String(200), nullable=True, index=True)

    # Placas del vehículo principal asociado al proveedor
    # En la práctica suelen ser únicas, así que ya lo dejamos con unique=True
    placas = Column(String(50), nullable=True, unique=True, index=True)

    # Activo / inactivo en catálogo
    activo = Column(Boolean, nullable=False, default=True)


class Cliente(Base):
    __tablename__ = "clientes"

    id = Column(Integer, primary_key=True, index=True)
    nombre_completo = Column(String(200), nullable=False, index=True)

    telefono = Column(String(50), nullable=True, index=True)
    correo_electronico = Column(String(200), nullable=True, index=True)

    # Placas del vehículo (si aplica) para identificar mejor al cliente
    placas = Column(String(50), nullable=True, unique=True, index=True)

    activo = Column(Boolean, nullable=False, default=True)
