# app/api/partners.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.models import Proveedor, Cliente

router = APIRouter(prefix="/partners", tags=["partners"])


# --------- Pydantic models compartidos ---------


class PartnerBase(BaseModel):
    nombre_completo: str
    telefono: str | None = None
    correo_electronico: str | None = None
    placas: str | None = None


class PartnerCreate(PartnerBase):
    pass


class PartnerUpdate(BaseModel):
    nombre_completo: str | None = None
    telefono: str | None = None
    correo_electronico: str | None = None
    placas: str | None = None
    activo: bool | None = None


class PartnerOut(PartnerBase):
    id: int
    activo: bool

    class Config:
        from_attributes = True  # pydantic v2


def _apply_search(query, model, q: str | None, only_active: bool):
    if q:
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(
                model.nombre_completo.ilike(term),
                model.telefono.ilike(term),
                model.correo_electronico.ilike(term),
                model.placas.ilike(term),
            )
        )
    if only_active:
        query = query.filter(model.activo.is_(True))
    return query


# --------- Proveedores ---------


@router.get("/proveedores", response_model=List[PartnerOut])
def list_proveedores(
    q: str | None = Query(None, description="Texto para buscar por nombre, teléfono, correo o placas"),
    only_active: bool = Query(True),
    db: Session = Depends(get_db),
):
    query = db.query(Proveedor)
    query = _apply_search(query, Proveedor, q, only_active)
    proveedores = query.order_by(Proveedor.nombre_completo).all()
    return proveedores


@router.post("/proveedores", response_model=PartnerOut, status_code=201)
def create_proveedor(
    partner_in: PartnerCreate,
    db: Session = Depends(get_db),
):
    # Unicidad simple por placas (si se envía)
    if partner_in.placas:
        existing = (
            db.query(Proveedor)
            .filter(Proveedor.placas == partner_in.placas)
            .first()
        )
        if existing:
            raise HTTPException(status_code=400, detail="Ya existe un proveedor con esas placas.")

    proveedor = Proveedor(
        nombre_completo=partner_in.nombre_completo.strip(),
        telefono=(partner_in.telefono or "").strip() or None,
        correo_electronico=(partner_in.correo_electronico or "").strip() or None,
        placas=(partner_in.placas or "").strip() or None,
        activo=True,
    )
    db.add(proveedor)
    db.commit()
    db.refresh(proveedor)
    return proveedor


@router.get("/proveedores/{proveedor_id}", response_model=PartnerOut)
def get_proveedor(
    proveedor_id: int,
    db: Session = Depends(get_db),
):
    proveedor = db.query(Proveedor).get(proveedor_id)
    if not proveedor:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado.")
    return proveedor


@router.put("/proveedores/{proveedor_id}", response_model=PartnerOut)
def update_proveedor(
    proveedor_id: int,
    partner_in: PartnerUpdate,
    db: Session = Depends(get_db),
):
    proveedor = db.query(Proveedor).get(proveedor_id)
    if not proveedor:
        raise HTTPException(status_code=404, detail="Proveedor no encontrado.")

    data = partner_in.model_dump(exclude_unset=True)

    if "nombre_completo" in data and data["nombre_completo"]:
        proveedor.nombre_completo = data["nombre_completo"].strip()

    if "telefono" in data:
        tel = data["telefono"] or ""
        proveedor.telefono = tel.strip() or None

    if "correo_electronico" in data:
        ce = data["correo_electronico"] or ""
        proveedor.correo_electronico = ce.strip() or None

    if "placas" in data:
        placas = (data["placas"] or "").strip() or None
        if placas:
            existing = (
                db.query(Proveedor)
                .filter(Proveedor.placas == placas, Proveedor.id != proveedor.id)
                .first()
            )
            if existing:
                raise HTTPException(status_code=400, detail="Ya existe otro proveedor con esas placas.")
        proveedor.placas = placas

    if "activo" in data and data["activo"] is not None:
        proveedor.activo = bool(data["activo"])

    db.add(proveedor)
    db.commit()
    db.refresh(proveedor)
    return proveedor


# --------- Clientes ---------


@router.get("/clientes", response_model=List[PartnerOut])
def list_clientes(
    q: str | None = Query(None, description="Texto para buscar por nombre, teléfono, correo o placas"),
    only_active: bool = Query(True),
    db: Session = Depends(get_db),
):
    query = db.query(Cliente)
    query = _apply_search(query, Cliente, q, only_active)
    clientes = query.order_by(Cliente.nombre_completo).all()
    return clientes


@router.post("/clientes", response_model=PartnerOut, status_code=201)
def create_cliente(
    partner_in: PartnerCreate,
    db: Session = Depends(get_db),
):
    if partner_in.placas:
        existing = (
            db.query(Cliente)
            .filter(Cliente.placas == partner_in.placas)
            .first()
        )
        if existing:
            raise HTTPException(status_code=400, detail="Ya existe un cliente con esas placas.")

    cliente = Cliente(
        nombre_completo=partner_in.nombre_completo.strip(),
        telefono=(partner_in.telefono or "").strip() or None,
        correo_electronico=(partner_in.correo_electronico or "").strip() or None,
        placas=(partner_in.placas or "").strip() or None,
        activo=True,
    )
    db.add(cliente)
    db.commit()
    db.refresh(cliente)
    return cliente


@router.get("/clientes/{cliente_id}", response_model=PartnerOut)
def get_cliente(
    cliente_id: int,
    db: Session = Depends(get_db),
):
    cliente = db.query(Cliente).get(cliente_id)
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")
    return cliente


@router.put("/clientes/{cliente_id}", response_model=PartnerOut)
def update_cliente(
    cliente_id: int,
    partner_in: PartnerUpdate,
    db: Session = Depends(get_db),
):
    cliente = db.query(Cliente).get(cliente_id)
    if not cliente:
        raise HTTPException(status_code=404, detail="Cliente no encontrado.")

    data = partner_in.model_dump(exclude_unset=True)

    if "nombre_completo" in data and data["nombre_completo"]:
        cliente.nombre_completo = data["nombre_completo"].strip()

    if "telefono" in data:
        tel = data["telefono"] or ""
        cliente.telefono = tel.strip() or None

    if "correo_electronico" in data:
        ce = data["correo_electronico"] or ""
        cliente.correo_electronico = ce.strip() or None

    if "placas" in data:
        placas = (data["placas"] or "").strip() or None
        if placas:
            existing = (
                db.query(Cliente)
                .filter(Cliente.placas == placas, Cliente.id != cliente.id)
                .first()
            )
            if existing:
                raise HTTPException(status_code=400, detail="Ya existe otro cliente con esas placas.")
        cliente.placas = placas

    if "activo" in data and data["activo"] is not None:
        cliente.activo = bool(data["activo"])

    db.add(cliente)
    db.commit()
    db.refresh(cliente)
    return cliente
