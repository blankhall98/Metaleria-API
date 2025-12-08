# scripts/seed_initial_sucursales.py
from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import Sucursal, SucursalStatus

DEFAULT_SUCURSALES = [
    ("Sucursal Centro", "Calle Ficticia 123, Col. Centro"),
    ("Sucursal Norte", "Av. Imaginaria 456, Col. Norte"),
    ("Sucursal Sur", "Blvd. Inventado 789, Col. Sur"),
]


def main() -> None:
    print("=== Seed de sucursales iniciales ===")
    db: Session = SessionLocal()
    try:
        for nombre, direccion in DEFAULT_SUCURSALES:
            existing = (
                db.query(Sucursal)
                .filter(Sucursal.nombre == nombre)
                .first()
            )
            if existing:
                print(f"[INFO] Ya existe la sucursal '{nombre}' (id={existing.id}), se omite.")
                continue

            sucursal = Sucursal(
                nombre=nombre,
                direccion=direccion,
                estado=SucursalStatus.activa,
            )
            db.add(sucursal)
            print(f"[OK] Creada sucursal '{nombre}'.")

        db.commit()
        print("\nSeed de sucursales completado.\n")
    finally:
        db.close()


if __name__ == "__main__":
    main()
