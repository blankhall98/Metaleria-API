# app/services/auth.py
from sqlalchemy.orm import Session

from app.core.security import verify_password, hash_password
from app.models import User, UserStatus



def authenticate_user(
    db: Session,
    username: str,
    password: str,
) -> User | None:
    """
    Busca un usuario por username y verifica la contraseña.
    Retorna el User si es válido y está activo; de lo contrario, None.
    """
    user: User | None = (
        db.query(User)
        .filter(User.username == username)
        .first()
    )

    if user is None:
        return None

    # Solo permitimos login de usuarios activos
    if user.estado != UserStatus.activo:
        return None

    if not verify_password(password, user.password_hash):
        return None

    return user

def set_user_password(user: User, plain_password: str) -> None:
    """
    Asigna un hash de contraseña a un usuario existente (por ejemplo al crearlo o cambiar password).
    """
    user.password_hash = hash_password(plain_password)
