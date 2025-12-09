# app/api/materials.py
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.deps import get_db
from app.models import Material

router = APIRouter(prefix="/materials", tags=["materials"])


class MaterialBase(BaseModel):
    nombre: str
    descripcion: str | None = None
    unidad_medida: str = "kg"


class MaterialCreate(MaterialBase):
    pass


class MaterialUpdate(BaseModel):
    nombre: str | None = None
    descripcion: str | None = None
    unidad_medida: str | None = None
    activo: bool | None = None


class MaterialOut(MaterialBase):
    id: int
    activo: bool

    class Config:
        from_attributes = True  # pydantic v2 (equiv. a orm_mode=True)


@router.get("/", response_model=List[MaterialOut])
def list_materials(db: Session = Depends(get_db)):
    materiales = db.query(Material).order_by(Material.nombre).all()
    return materiales


@router.post("/", response_model=MaterialOut, status_code=201)
def create_material(material_in: MaterialCreate, db: Session = Depends(get_db)):
    existing = (
        db.query(Material)
        .filter(Material.nombre == material_in.nombre)
        .first()
    )
    if existing:
        raise HTTPException(status_code=400, detail="Ya existe un material con ese nombre.")

    material = Material(
        nombre=material_in.nombre.strip(),
        descripcion=(material_in.descripcion or "").strip() or None,
        unidad_medida=material_in.unidad_medida.strip() or "kg",
        activo=True,
    )
    db.add(material)
    db.commit()
    db.refresh(material)
    return material


@router.get("/{material_id}", response_model=MaterialOut)
def get_material(material_id: int, db: Session = Depends(get_db)):
    material = db.query(Material).get(material_id)
    if not material:
        raise HTTPException(status_code=404, detail="Material no encontrado.")
    return material


@router.put("/{material_id}", response_model=MaterialOut)
def update_material(
    material_id: int,
    material_in: MaterialUpdate,
    db: Session = Depends(get_db),
):
    material = db.query(Material).get(material_id)
    if not material:
        raise HTTPException(status_code=404, detail="Material no encontrado.")

    data = material_in.model_dump(exclude_unset=True)

    if "nombre" in data and data["nombre"]:
        # validar unicidad
        existing = (
            db.query(Material)
            .filter(Material.nombre == data["nombre"], Material.id != material.id)
            .first()
        )
        if existing:
            raise HTTPException(status_code=400, detail="Ya existe un material con ese nombre.")
        material.nombre = data["nombre"].strip()

    if "descripcion" in data:
        desc = data["descripcion"] or ""
        material.descripcion = desc.strip() or None

    if "unidad_medida" in data and data["unidad_medida"]:
        material.unidad_medida = data["unidad_medida"].strip()

    if "activo" in data and data["activo"] is not None:
        material.activo = bool(data["activo"])

    db.add(material)
    db.commit()
    db.refresh(material)
    return material
