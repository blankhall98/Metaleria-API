# app/models/capital.py
from datetime import datetime

from sqlalchemy import (
    Column,
    Integer,
    String,
    Date,
    Text,
    ForeignKey,
    DateTime,
    Numeric,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.db.base import Base


class CapitalAjusteManual(Base):
    """Comodín manual del capital (punto 4, fase 2).

    Cubre lo que el sistema no conoce (dinero en proceso, préstamos, capital
    de socios…). Monto con signo: positivo suma al capital, negativo resta.
    Registro puro: se deshace con un ajuste compensatorio, nunca se edita.
    """

    __tablename__ = "capital_ajustes_manuales"

    id = Column(Integer, primary_key=True, index=True)
    sucursal_id = Column(Integer, ForeignKey("sucursales.id"), nullable=True, index=True)
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    monto = Column(Numeric(14, 2), nullable=False)
    concepto = Column(String(200), nullable=False)
    comentario = Column(String(255), nullable=True)

    reversal_of_id = Column(Integer, ForeignKey("capital_ajustes_manuales.id"), nullable=True)
    reverted_at = Column(DateTime, nullable=True)
    reverted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    sucursal = relationship("Sucursal")
    usuario = relationship("User", foreign_keys=[usuario_id])
    reverted_by = relationship("User", foreign_keys=[reverted_by_user_id])
    reversal_of = relationship("CapitalAjusteManual", remote_side=[id])


class CapitalSnapshot(Base):
    """Foto del capital de un día para un alcance de sucursales.

    Igual que el Excel de la clienta ("CAPITAL 06 DE JULIO"): la utilidad del
    período es la resta entre dos fotos. scope_key identifica el alcance
    ("global" o "suc:1-3" para sucursales fusionadas).
    """

    __tablename__ = "capital_snapshots"
    __table_args__ = (
        UniqueConstraint("fecha", "scope_key", name="uq_capital_snapshots_fecha_scope"),
    )

    id = Column(Integer, primary_key=True, index=True)
    fecha = Column(Date, nullable=False, index=True)
    scope_key = Column(String(120), nullable=False, index=True)
    scope_label = Column(String(200), nullable=True)

    activos = Column(Numeric(14, 2), nullable=False, default=0)
    pasivos = Column(Numeric(14, 2), nullable=False, default=0)
    capital = Column(Numeric(14, 2), nullable=False, default=0)
    tc_usd = Column(Numeric(10, 4), nullable=True)
    detalle = Column(Text, nullable=True)  # JSON con el desglose de renglones

    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    usuario = relationship("User")
