# app/api/notes.py
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.models import Nota, NotaEstado, NotaMaterial, Subpesaje, TipoOperacion
from app.services import note_service

router = APIRouter(prefix="/notes", tags=["notes"])


class SubpesajeIn(BaseModel):
    peso_kg: float
    foto_url: str | None = None

    @field_validator("peso_kg")
    def validate_peso(cls, v):
        if v <= 0:
            raise ValueError("peso_kg debe ser mayor a 0")
        return v


class NotaMaterialIn(BaseModel):
    material_id: int
    kg_bruto: float = Field(..., gt=0)
    kg_descuento: float = Field(0, ge=0)
    subpesajes: List[SubpesajeIn] = []

    @field_validator("kg_descuento")
    @classmethod
    def validate_descuento(cls, v, info):
        kg_bruto = info.data.get("kg_bruto")
        if kg_bruto is not None and v > kg_bruto:
            raise ValueError("El descuento no puede ser mayor que el peso bruto")
        return v


class NotaCreate(BaseModel):
    sucursal_id: int
    trabajador_id: int
    tipo_operacion: TipoOperacion
    materiales: List[NotaMaterialIn]
    comentarios_trabajador: str | None = None


class NotaOutMaterial(BaseModel):
    id: int
    material_id: int
    kg_bruto: float
    kg_descuento: float
    kg_neto: float
    precio_unitario: Optional[float]
    subtotal: Optional[float]
    class Config:
        from_attributes = True


class SubpesajeOut(BaseModel):
    id: int
    peso_kg: float
    foto_url: str | None
    class Config:
        from_attributes = True


class NotaOut(BaseModel):
    id: int
    sucursal_id: int
    trabajador_id: int
    admin_id: int | None
    proveedor_id: int | None
    cliente_id: int | None
    tipo_operacion: TipoOperacion
    estado: NotaEstado
    total_kg_bruto: float
    total_kg_descuento: float
    total_kg_neto: float
    total_monto: float
    metodo_pago: str | None
    cuenta_financiera_id: int | None
    fecha_caducidad_pago: date | None
    comentarios_trabajador: str | None
    comentarios_admin: str | None

    materiales: List[NotaOutMaterial]

    class Config:
        from_attributes = True


@router.post("/", response_model=NotaOut, status_code=201)
def create_note(data: NotaCreate, db: Session = Depends(get_db)):
    if not data.materiales:
        raise HTTPException(status_code=400, detail="Debe incluir al menos un material.")
    try:
        nota = note_service.create_draft_note(
            db,
            sucursal_id=data.sucursal_id,
            trabajador_id=data.trabajador_id,
            tipo_operacion=data.tipo_operacion,
            materiales_payload=[m.dict() for m in data.materiales],
            comentarios_trabajador=data.comentarios_trabajador,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return nota


@router.get("/", response_model=List[NotaOut])
def list_notes(
    estado: NotaEstado | None = Query(None),
    sucursal_id: int | None = Query(None),
    db: Session = Depends(get_db),
):
    query = db.query(Nota)
    if estado:
        query = query.filter(Nota.estado == estado)
    if sucursal_id:
        query = query.filter(Nota.sucursal_id == sucursal_id)
    return query.order_by(Nota.id.desc()).all()


@router.get("/{nota_id}", response_model=NotaOut)
def get_note(nota_id: int, db: Session = Depends(get_db)):
    nota = db.query(Nota).get(nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    return nota


class EstadoUpdate(BaseModel):
    estado: NotaEstado
    admin_id: int | None = None
    comentarios_admin: str | None = None
    fecha_caducidad_pago: date | None = None


@router.put("/{nota_id}/estado", response_model=NotaOut)
def update_note_state(
    nota_id: int,
    data: EstadoUpdate,
    db: Session = Depends(get_db),
):
    nota = db.query(Nota).get(nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if nota.estado == NotaEstado.cancelada:
        raise HTTPException(status_code=400, detail="La nota cancelada no puede cambiar de estado.")

    if data.estado == NotaEstado.en_revision:
        if nota.estado != NotaEstado.borrador:
            raise HTTPException(status_code=400, detail="Solo borradores pueden enviarse a revisión.")
        try:
            return note_service.send_to_revision(db, nota)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    if data.estado == NotaEstado.aprobada:
        raise HTTPException(
            status_code=400,
            detail="Usa el flujo de aprobación para registrar inventario y contabilidad.",
        )

    if data.estado == NotaEstado.cancelada and nota.estado == NotaEstado.aprobada:
        raise HTTPException(status_code=400, detail="No puedes cancelar una nota aprobada por esta vía.")

    if data.estado == NotaEstado.borrador and nota.estado not in (NotaEstado.en_revision, NotaEstado.borrador):
        raise HTTPException(status_code=400, detail="Solo puedes devolver a borrador desde revisión.")

    nota = note_service.update_state(
        db,
        nota,
        new_state=data.estado,
        admin_id=data.admin_id,
        comentarios_admin=data.comentarios_admin,
        fecha_caducidad_pago=data.fecha_caducidad_pago,
    )
    return nota


class PartnerUpdate(BaseModel):
    proveedor_id: int | None = None
    cliente_id: int | None = None


@router.put("/{nota_id}/partner", response_model=NotaOut)
def update_note_partner(
    nota_id: int,
    data: PartnerUpdate,
    db: Session = Depends(get_db),
):
    nota = db.query(Nota).get(nota_id)
    if not nota:
        raise HTTPException(status_code=404, detail="Nota no encontrada")
    if nota.tipo_operacion == TipoOperacion.compra and not data.proveedor_id:
        raise HTTPException(status_code=400, detail="Proveedor requerido para compras.")
    if nota.tipo_operacion == TipoOperacion.venta and not data.cliente_id:
        raise HTTPException(status_code=400, detail="Cliente requerido para ventas.")

    nota = note_service.attach_partner(
        db,
        nota,
        proveedor_id=data.proveedor_id,
        cliente_id=data.cliente_id,
    )
    return nota
