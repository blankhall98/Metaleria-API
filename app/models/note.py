# app/models/note.py
import enum
from decimal import Decimal
from datetime import datetime, date

from sqlalchemy import (
    Column,
    Integer,
    ForeignKey,
    Boolean,
    Numeric,
    String,
    DateTime,
    Date,
    Enum,
    Text,
)
from sqlalchemy.orm import relationship

from app.db.base import Base
from app.models.pricing import TipoOperacion, TipoCliente


class NotaEstado(str, enum.Enum):
    borrador = "BORRADOR"
    en_revision = "EN_REVISION"
    aprobada = "APROBADA"
    cancelada = "CANCELADA"


class Nota(Base):
    __tablename__ = "notas"

    id = Column(Integer, primary_key=True, index=True)
    nota_origen_id = Column(Integer, ForeignKey("notas.id"), nullable=True, index=True)

    sucursal_id = Column(Integer, ForeignKey("sucursales.id"), nullable=False, index=True)
    inventario_sucursal_id = Column(Integer, ForeignKey("sucursales.id"), nullable=True, index=True)
    trabajador_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    admin_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    proveedor_id = Column(Integer, ForeignKey("proveedores.id"), nullable=True, index=True)
    cliente_id = Column(Integer, ForeignKey("clientes.id"), nullable=True, index=True)

    tipo_operacion = Column(Enum(TipoOperacion, name="tipo_operacion"), nullable=False)
    estado = Column(
        Enum(
            NotaEstado,
            name="nota_estado",
            values_callable=lambda enum_cls: [member.value for member in enum_cls],
        ),
        nullable=False,
        index=True,
    )

    total_kg_bruto = Column(Numeric(12, 3), nullable=False, default=0)
    total_kg_descuento = Column(Numeric(12, 3), nullable=False, default=0)
    total_kg_neto = Column(Numeric(12, 3), nullable=False, default=0)
    total_kg_real = Column(Numeric(12, 3), nullable=False, default=0)
    total_monto = Column(Numeric(12, 2), nullable=False, default=0)
    monto_pagado = Column(Numeric(12, 2), nullable=False, default=0)
    folio_seq = Column(Integer, nullable=True, index=True)

    iva_incluido = Column(Boolean, nullable=False, default=False)
    iva_porcentaje = Column(Numeric(5, 2), nullable=False, default=Decimal("16.00"))
    iva_monto = Column(Numeric(12, 2), nullable=False, default=0)

    factura_url = Column(String(255), nullable=True)
    factura_generada_at = Column(DateTime, nullable=True)

    metodo_pago = Column(String(50), nullable=True)
    cuenta_financiera_id = Column(Integer, ForeignKey("cuentas.id"), nullable=True)
    fecha_caducidad_pago = Column(Date, nullable=True)

    comentarios_trabajador = Column(Text, nullable=True)
    comentarios_admin = Column(Text, nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    materiales = relationship("NotaMaterial", back_populates="nota", cascade="all, delete-orphan")
    pagos = relationship("NotaPago", back_populates="nota", cascade="all, delete-orphan")
    saldo_ajustes = relationship(
        "NotaAjusteSaldo",
        back_populates="nota",
        cascade="all, delete-orphan",
    )
    original = relationship("NotaOriginal", back_populates="nota", uselist=False, cascade="all, delete-orphan")
    cuenta = relationship("Cuenta", foreign_keys=[cuenta_financiera_id])
    evidencias_extra = relationship(
        "NotaEvidenciaExtra",
        back_populates="nota",
        cascade="all, delete-orphan",
    )


class NotaMaterial(Base):
    __tablename__ = "nota_materiales"

    id = Column(Integer, primary_key=True, index=True)
    nota_id = Column(Integer, ForeignKey("notas.id"), nullable=False, index=True)
    material_id = Column(Integer, ForeignKey("materiales.id"), nullable=False, index=True)
    evidencia_url = Column(String(255), nullable=True)

    kg_bruto = Column(Numeric(12, 3), nullable=False, default=0)
    kg_descuento = Column(Numeric(12, 3), nullable=False, default=0)
    kg_neto = Column(Numeric(12, 3), nullable=False, default=0)
    kg_real = Column(Numeric(12, 3), nullable=False, default=0)

    precio_unitario = Column(Numeric(12, 5), nullable=True)
    subtotal = Column(Numeric(12, 2), nullable=True)

    version_precio_id = Column(Integer, ForeignKey("tablas_precios.id"), nullable=True, index=True)
    orden = Column(Integer, nullable=True)
    tipo_cliente = Column(Enum(TipoCliente, name="tipo_cliente"), nullable=True)

    nota = relationship("Nota", back_populates="materiales")
    material = relationship("Material")
    subpesajes = relationship("Subpesaje", back_populates="nota_material", cascade="all, delete-orphan")


class Subpesaje(Base):
    __tablename__ = "subpesajes"

    id = Column(Integer, primary_key=True, index=True)
    nota_material_id = Column(Integer, ForeignKey("nota_materiales.id"), nullable=False, index=True)
    peso_kg = Column(Numeric(12, 3), nullable=False)
    descuento_kg = Column(Numeric(12, 3), nullable=False, default=0)
    foto_url = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    nota_material = relationship("NotaMaterial", back_populates="subpesajes")


class NotaOriginal(Base):
    """
    Snapshot del payload enviado por el trabajador al pasar a EN_REVISION.
    """
    __tablename__ = "nota_originales"

    id = Column(Integer, primary_key=True, index=True)
    nota_id = Column(Integer, ForeignKey("notas.id"), nullable=False, unique=True, index=True)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    nota = relationship("Nota", back_populates="original")


class NotaEvidenciaExtra(Base):
    __tablename__ = "nota_evidencias_extra"

    id = Column(Integer, primary_key=True, index=True)
    nota_id = Column(Integer, ForeignKey("notas.id"), nullable=False, index=True)
    url = Column(String(255), nullable=False)
    uploaded_by_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    nota = relationship("Nota", back_populates="evidencias_extra")
    usuario = relationship("User")


class NotaPago(Base):
    __tablename__ = "nota_pagos"

    id = Column(Integer, primary_key=True, index=True)
    nota_id = Column(Integer, ForeignKey("notas.id"), nullable=False, index=True)
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    cuenta_id = Column(Integer, ForeignKey("cuentas.id"), nullable=True, index=True)
    cuenta_scrap360_id = Column(Integer, ForeignKey("cuentas_scrap360.id"), nullable=True, index=True)
    caja_sucursal_id = Column(Integer, ForeignKey("sucursales.id"), nullable=True, index=True)

    monto = Column(Numeric(12, 2), nullable=False, default=0)
    metodo_pago = Column(String(50), nullable=True)
    cuenta_financiera = Column(String(100), nullable=True)
    comentario = Column(String(255), nullable=True)

    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    nota = relationship("Nota", back_populates="pagos")
    usuario = relationship("User")
    cuenta = relationship("Cuenta")
    cuenta_scrap360 = relationship("CuentaScrap360")
    caja_sucursal = relationship("Sucursal")


class NotaAjusteSaldo(Base):
    __tablename__ = "nota_ajustes_saldo"

    id = Column(Integer, primary_key=True, index=True)
    nota_id = Column(Integer, ForeignKey("notas.id"), nullable=False, index=True)
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)

    # Delta aplicado al saldo de la nota:
    # negativo reduce saldo pendiente / aumenta saldo a favor
    # positivo aumenta saldo pendiente / reduce saldo a favor
    monto_delta = Column(Numeric(12, 2), nullable=False, default=0)
    saldo_anterior = Column(Numeric(12, 2), nullable=False, default=0)
    saldo_resultante = Column(Numeric(12, 2), nullable=False, default=0)
    comentario = Column(String(255), nullable=True)

    reversal_of_id = Column(Integer, ForeignKey("nota_ajustes_saldo.id"), nullable=True, index=True)
    reverted_at = Column(DateTime, nullable=True)
    reverted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    comentario_reversion = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    nota = relationship("Nota", back_populates="saldo_ajustes", foreign_keys=[nota_id])
    usuario = relationship("User", foreign_keys=[usuario_id])
    reverted_by = relationship("User", foreign_keys=[reverted_by_user_id])


class NotaDevolucionParcial(Base):
    __tablename__ = "nota_devoluciones_parciales"

    id = Column(Integer, primary_key=True, index=True)
    nota_id = Column(Integer, ForeignKey("notas.id"), nullable=False, index=True)
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    comentario = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    nota = relationship("Nota")
    usuario = relationship("User")
    lineas = relationship(
        "NotaDevolucionParcialLinea",
        back_populates="devolucion",
        cascade="all, delete-orphan",
    )


class NotaDevolucionParcialLinea(Base):
    __tablename__ = "nota_devoluciones_parciales_lineas"

    id = Column(Integer, primary_key=True, index=True)
    devolucion_id = Column(Integer, ForeignKey("nota_devoluciones_parciales.id"), nullable=False, index=True)
    nota_material_id = Column(Integer, ForeignKey("nota_materiales.id"), nullable=False, index=True)
    material_id = Column(Integer, ForeignKey("materiales.id"), nullable=True, index=True)

    kg_devolucion = Column(Numeric(12, 3), nullable=False, default=0)
    kg_real_devolucion = Column(Numeric(12, 3), nullable=False, default=0)
    precio_unitario_devolucion = Column(Numeric(12, 5), nullable=False, default=0)
    monto_devolucion = Column(Numeric(12, 2), nullable=False, default=0)

    reverted_at = Column(DateTime, nullable=True)
    reverted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    comentario_reversion = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    devolucion = relationship("NotaDevolucionParcial", back_populates="lineas")
    nota_material = relationship("NotaMaterial")
    material = relationship("Material")
    reverted_by = relationship("User", foreign_keys=[reverted_by_user_id])
    aplicaciones = relationship(
        "NotaDevolucionParcialAplicacion",
        back_populates="linea",
        cascade="all, delete-orphan",
    )


class NotaDevolucionParcialAplicacion(Base):
    __tablename__ = "nota_devoluciones_parciales_aplicaciones"

    id = Column(Integer, primary_key=True, index=True)
    linea_id = Column(Integer, ForeignKey("nota_devoluciones_parciales_lineas.id"), nullable=False, index=True)
    subpesaje_id = Column(Integer, ForeignKey("subpesajes.id"), nullable=True, index=True)
    kg_aplicado = Column(Numeric(12, 3), nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    linea = relationship("NotaDevolucionParcialLinea", back_populates="aplicaciones")
    subpesaje = relationship("Subpesaje")


class NotaDevolucionTotal(Base):
    __tablename__ = "nota_devoluciones_totales"

    id = Column(Integer, primary_key=True, index=True)
    nota_id = Column(Integer, ForeignKey("notas.id"), nullable=False, index=True)
    usuario_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    comentario = Column(String(255), nullable=True)

    reverted_at = Column(DateTime, nullable=True)
    reverted_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    comentario_reversion = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)

    nota = relationship("Nota")
    usuario = relationship("User", foreign_keys=[usuario_id])
    reverted_by = relationship("User", foreign_keys=[reverted_by_user_id])
