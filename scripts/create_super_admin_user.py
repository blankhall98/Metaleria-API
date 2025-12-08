# scripts/create_super_admin_user.py
import getpass

from sqlalchemy.orm import Session

from app.db.session import SessionLocal
from app.models import User, UserRole, UserStatus
from app.core.security import hash_password


def prompt_non_empty(label: str, default: str | None = None) -> str:
    while True:
        value = input(f"{label}{f' [{default}]' if default else ''}: ").strip()
        if not value and default is not None:
            return default
        if value:
            return value
        print("  -> Este campo no puede estar vacío.")


def main() -> None:
    print("=== Crear super admin (extra) ===")

    db: Session = SessionLocal()
    try:
        username = prompt_non_empty("Username", default="superadmin2")

        existing = db.query(User).filter(User.username == username).first()
        if existing:
            print(f"\n[INFO] Ya existe un usuario con username '{username}' (id={existing.id}).")
            print("No se creará un nuevo usuario.\n")
            return

        nombre_completo = prompt_non_empty("Nombre completo", default="Super Admin Extra")

        while True:
            password = getpass.getpass("Contraseña: ")
            password_confirm = getpass.getpass("Confirmar contraseña: ")

            if not password:
                print("  -> La contraseña no puede estar vacía.")
                continue
            if password != password_confirm:
                print("  -> Las contraseñas no coinciden, intenta de nuevo.\n")
                continue
            break

        user = User(
            username=username,
            nombre_completo=nombre_completo,
            password_hash=hash_password(password),
            rol=UserRole.super_admin,
            estado=UserStatus.activo,
            sucursal_id=None,
            super_admin_original=False,  # 👈 ojo: este NO es el original
        )

        db.add(user)
        db.commit()
        db.refresh(user)

        print("\n[OK] Super admin extra creado:")
        print(f"  id={user.id}")
        print(f"  username={user.username}")
        print(f"  nombre={user.nombre_completo}")
        print(f"  rol={user.rol}")
        print(f"  super_admin_original={user.super_admin_original}\n")

    finally:
        db.close()


if __name__ == "__main__":
    main()
