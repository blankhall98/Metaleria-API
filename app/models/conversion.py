# app/models/conversion.py
from datetime import datetime

from sqlalchemy import Column, Integer, ForeignKey, Numeric, String, DateTime
from sqlalchemy.orm import relationship

from app.db.base import Base


class ConversionMaterial(Base):
    __tablename__ = "conversiones_material"

    id = Column(Integer, primary_key=True, index=True)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id"), nullable=False, index=True)
    material_origen_id = Column(Integer, ForeignKey("materiales.id"), nullable=False, index=True)
    cantidad_origen = Column(Numeric(12, 3), nullable=False)
    material_destino_id = Column(Integer, ForeignKey("materiales.id"), nullable=False, index=True)
    cantidad_destino = Column(Numeric(12, 3), nullable=False)
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    comentario = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    sucursal = relationship("Sucursal")
    material_origen = relationship("Material", foreign_keys=[material_origen_id])
    material_destino = relationship("Material", foreign_keys=[material_destino_id])
    usuario = relationship("User")
