# scripts/seed_super_admin_env.py
import os

from sqlalchemy.orm import Session

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models import User, UserRole, UserStatus


def _env_value(name: str, default: str) -> str:
    value = (os.getenv(name) or "").strip()
    return value or default


def main() -> None:
    username = _env_value("SUPERADMIN_USERNAME", "AVRC")
    password = os.getenv("SUPERADMIN_PASSWORD") or "scrap360$1123"
    nombre_completo = _env_value("SUPERADMIN_NAME", "Super Admin")

    if not username or not password:
        raise SystemExit("Faltan SUPERADMIN_USERNAME o SUPERADMIN_PASSWORD.")

    db: Session = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user:
            user.nombre_completo = nombre_completo
            user.password_hash = hash_password(password)
            user.rol = UserRole.super_admin
            user.estado = UserStatus.activo
            user.sucursal_id = None
            user.super_admin_original = True
            db.commit()
            print(f"[OK] Super admin actualizado: {user.username}")
            return

        user = User(
            username=username,
            nombre_completo=nombre_completo,
            password_hash=hash_password(password),
            rol=UserRole.super_admin,
            estado=UserStatus.activo,
            sucursal_id=None,
            super_admin_original=True,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        print(f"[OK] Super admin creado: {user.username} (id={user.id})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
