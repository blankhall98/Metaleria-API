# app/models/branch.py
import enum

from sqlalchemy import Column, Integer, String, Enum
from sqlalchemy.orm import relationship

from app.db.base import Base


class SucursalStatus(str, enum.Enum):
    activa = "activa"
    inactiva = "inactiva"


class Sucursal(Base):
    __tablename__ = "sucursales"

    id = Column(Integer, primary_key=True, index=True)
    nombre = Column(String(100), nullable=False, unique=True, index=True)
    direccion = Column(String(255), nullable=True)
    estado = Column(
        Enum(SucursalStatus, name="sucursal_status"),
        nullable=False,
        default=SucursalStatus.activa,
    )
    logo_url = Column(String(255), nullable=True)

    usuarios = relationship("User", back_populates="sucursal")
