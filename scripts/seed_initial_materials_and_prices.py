# scripts/seed_initial_materials_and_prices.py
from decimal import Decimal

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import Material, TablaPrecio, TipoOperacion, TipoCliente
from app.services.pricing_service import create_price_version

"""
Seed genérico de materiales y precios.

Puedes editar fácilmente la lista MATERIALS_CONFIG para ajustar:
- nombres y descripciones de materiales
- unidad de medida
- precios por tipo de operación y tipo de cliente
"""

MATERIALS_CONFIG = [
    {
        "nombre": 'Varilla 3/8"',
        "descripcion": "Varilla corrugada 3/8\" para construcción.",
        "unidad_medida": "kg",
        "precios": [
            # Ejemplos: compra / venta para cliente regular
            {"tipo_operacion": "compra", "tipo_cliente": "regular", "precio": "20.00"},
            {"tipo_operacion": "venta", "tipo_cliente": "regular", "precio": "25.00"},
        ],
    },
    {
        "nombre": "Cemento gris 50kg",
        "descripcion": "Saco de cemento gris de 50 kg.",
        "unidad_medida": "saco",
        "precios": [
            {"tipo_operacion": "compra", "tipo_cliente": "regular", "precio": "150.00"},
            {"tipo_operacion": "venta", "tipo_cliente": "regular", "precio": "190.00"},
        ],
    },
    {
        "nombre": "Arena",
        "descripcion": "Arena para construcción.",
        "unidad_medida": "m3",
        "precios": [
            {"tipo_operacion": "compra", "tipo_cliente": "regular", "precio": "220.00"},
            {"tipo_operacion": "venta", "tipo_cliente": "regular", "precio": "260.00"},
        ],
    },
    {
        "nombre": "Grava",
        "descripcion": "Grava para construcción.",
        "unidad_medida": "m3",
        "precios": [
            {"tipo_operacion": "compra", "tipo_cliente": "regular", "precio": "230.00"},
            {"tipo_operacion": "venta", "tipo_cliente": "regular", "precio": "270.00"},
        ],
    },
    {
        "nombre": "Alambre recocido",
        "descripcion": "Alambre recocido para amarres.",
        "unidad_medida": "kg",
        "precios": [
            {"tipo_operacion": "compra", "tipo_cliente": "regular", "precio": "30.00"},
            {"tipo_operacion": "venta", "tipo_cliente": "regular", "precio": "38.00"},
        ],
    },
]


def seed_materiales_y_precios(db: Session) -> None:
    print("=== Seed de materiales y precios iniciales ===")

    for cfg in MATERIALS_CONFIG:
        nombre = cfg["nombre"].strip()

        # 1) Material (idempotente por nombre)
        material = (
            db.query(Material)
            .filter(Material.nombre == nombre)
            .first()
        )

        if material:
            print(f"[INFO] Material ya existe: '{material.nombre}' (id={material.id}), se reutiliza.")
        else:
            material = Material(
                nombre=nombre,
                descripcion=(cfg.get("descripcion") or "").strip() or None,
                unidad_medida=(cfg.get("unidad_medida") or "kg").strip(),
                activo=True,
            )
            db.add(material)
            db.commit()
            db.refresh(material)
            print(f"[OK] Material creado: '{material.nombre}' (id={material.id})")

        # 2) Precios iniciales (idempotentes por combinación material+operación+cliente)
        for p in cfg.get("precios", []):
            tipo_operacion_str = p["tipo_operacion"]
            tipo_cliente_str = p["tipo_cliente"]
            precio_str = p["precio"]

            tipo_op = TipoOperacion(tipo_operacion_str)
            tipo_cli = TipoCliente(tipo_cliente_str)
            precio_dec = Decimal(precio_str)

            # ¿Ya existe alguna versión para esa combinación? Si sí, NO creamos otra (evitar duplicar versión en seed).
            existing = (
                db.query(TablaPrecio)
                .filter(
                    TablaPrecio.material_id == material.id,
                    TablaPrecio.tipo_operacion == tipo_op,
                    TablaPrecio.tipo_cliente == tipo_cli,
                )
                .first()
            )
            if existing:
                print(
                    f"  [INFO] Ya existe precio para {material.nombre} "
                    f"({tipo_op.value}, {tipo_cli.value}), se omite."
                )
                continue

            create_price_version(
                db,
                material_id=material.id,
                tipo_operacion=tipo_op,
                tipo_cliente=tipo_cli,
                precio=precio_dec,
                user_id=None,
                source="seed",
            )
            print(
                f"  [OK] Precio creado para {material.nombre} "
                f"({tipo_op.value}, {tipo_cli.value}) = {precio_dec}"
            )

    print("\nSeed de materiales y precios completado.\n")


def main() -> None:
    db: Session = SessionLocal()
    try:
        seed_materiales_y_precios(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
