# app/models/material.py
from sqlalchemy import Boolean, Column, Integer, String
from sqlalchemy.orm import relationship

from app.db.base import Base


class Material(Base):
    __tablename__ = "materiales"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), nullable=False, unique=True, index=True)
    descripcion = Column(String(255), nullable=True)
    unidad_medida = Column(String(50), nullable=False, default="kg")
    activo = Column(Boolean, nullable=False, default=True)

    tablas_precios = relationship("TablaPrecio", back_populates="material")
