# app/services/auth.py
import logging
import unicodedata
from enum import Enum

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.core.security import verify_password, hash_password
from app.models import User, UserStatus

logger = logging.getLogger(__name__)

# Lo que un teclado de celular o un mensaje copiado meten sin que se vea.
ESPACIO_DURO = " "
ESPACIO_INVISIBLE = "​"


class MotivoRechazo(str, Enum):
    """Por qué se rechazó un intento de acceso. Alimenta el mensaje que ve la
    persona y la bitácora del servidor; nunca se guarda en base de datos."""

    usuario_inexistente = "usuario_inexistente"
    usuario_ambiguo = "usuario_ambiguo"
    usuario_inactivo = "usuario_inactivo"
    password_incorrecta = "password_incorrecta"


def normalizar_username(valor: str | None) -> str:
    """Deja el usuario como quedó guardado al darlo de alta.

    Las credenciales se reparten por WhatsApp y se escriben desde el celular:
    llegan con un espacio al final, con espacio duro pegado o con la primera
    letra en mayúscula. Nada de eso debe impedir entrar.
    """
    if not valor:
        return ""
    limpio = unicodedata.normalize("NFKC", valor)
    limpio = limpio.replace(ESPACIO_DURO, " ").replace(ESPACIO_INVISIBLE, "")
    return limpio.strip()


def normalizar_password(valor: str | None) -> str:
    """Quita solo los espacios de los extremos, que son los que agrega el
    teclado o el copiado, sin tocar el contenido real de la contraseña."""
    if not valor:
        return ""
    return valor.replace(ESPACIO_DURO, " ").replace(ESPACIO_INVISIBLE, "").strip()


def _buscar_usuario(db: Session, username: str) -> tuple[User | None, MotivoRechazo | None]:
    user: User | None = (
        db.query(User)
        .filter(User.username == username)
        .first()
    )
    if user is not None:
        return user, None

    # El alta pudo quedar como "Visor Norte" y la persona escribe "visor norte".
    # Aceptamos la variante solo si no hay dos cuentas que compitan por ella.
    candidatos = (
        db.query(User)
        .filter(func.lower(User.username) == username.lower())
        .limit(2)
        .all()
    )
    if len(candidatos) == 1:
        return candidatos[0], None
    if len(candidatos) > 1:
        return None, MotivoRechazo.usuario_ambiguo
    return None, MotivoRechazo.usuario_inexistente


def _password_coincide(password: str, password_hash: str) -> bool:
    """Verifica la contraseña tal cual se escribió y, si falla, sin los espacios
    de los extremos.

    Las altas guardaron el hash de la contraseña sin recortar y las ediciones lo
    guardaron recortado, así que ambas variantes conviven en la base. Probar las
    dos evita dejar fuera a quien sí tiene la clave correcta.
    """
    if not password_hash:
        return False
    try:
        if verify_password(password, password_hash):
            return True
        recortada = normalizar_password(password)
        if recortada and recortada != password:
            return verify_password(recortada, password_hash)
    except ValueError:
        # Hash con formato desconocido: se trata como credencial inválida.
        logger.warning("Hash de contraseña ilegible al validar el acceso.")
    return False


def autenticar(
    db: Session,
    username: str,
    password: str,
) -> tuple[User | None, MotivoRechazo | None]:
    """Valida credenciales y explica el rechazo.

    Devuelve `(usuario, None)` si puede entrar, o `(None, motivo)` si no.
    """
    username = normalizar_username(username)
    if not username or not password:
        return None, MotivoRechazo.usuario_inexistente

    user, motivo = _buscar_usuario(db, username)
    if user is None:
        return None, motivo

    if not _password_coincide(password, user.password_hash):
        return None, MotivoRechazo.password_incorrecta

    # La contraseña es correcta: recién aquí podemos decirle que su cuenta está
    # dada de baja sin revelarle nada a quien ande probando usuarios ajenos.
    if user.estado != UserStatus.activo:
        return None, MotivoRechazo.usuario_inactivo

    return user, None


def authenticate_user(
    db: Session,
    username: str,
    password: str,
) -> User | None:
    """Retorna el User si es válido y está activo; de lo contrario, None."""
    user, _ = autenticar(db=db, username=username, password=password)
    return user


def set_user_password(user: User, plain_password: str) -> None:
    """
    Asigna un hash de contraseña a un usuario existente (por ejemplo al crearlo o cambiar password).
    """
    user.password_hash = hash_password(normalizar_password(plain_password))
