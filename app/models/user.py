# app/models/user.py
import enum

from sqlalchemy import Column, Integer, String, Boolean, Enum, ForeignKey, Table
from sqlalchemy.orm import relationship

from app.db.base import Base


class UserRole(str, enum.Enum):
    super_admin = "super_admin"
    admin = "admin"
    trabajador = "trabajador"


class UserStatus(str, enum.Enum):
    activo = "activo"
    inactivo = "inactivo"


admin_sucursales = Table(
    "admin_sucursales",
    Base.metadata,
    Column("user_id", Integer, ForeignKey("users.id"), primary_key=True),
    Column("sucursal_id", Integer, ForeignKey("sucursales.id"), primary_key=True),
)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    nombre_completo = Column(String(200), nullable=False)

    rol = Column(
        Enum(UserRole, name="user_role"),
        nullable=False,
    )
    estado = Column(
        Enum(UserStatus, name="user_status"),
        nullable=False,
        default=UserStatus.activo,
    )

    # Obligatorio para trabajadores a nivel de lógica de negocio
    sucursal_id = Column(Integer, ForeignKey("sucursales.id"), nullable=True, index=True)

    super_admin_original = Column(Boolean, nullable=False, default=False)

    sucursal = relationship("Sucursal", back_populates="usuarios")
    sucursales_admin = relationship(
        "Sucursal",
        secondary=admin_sucursales,
        back_populates="admins",
    )
