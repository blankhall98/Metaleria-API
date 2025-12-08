# app/db/base.py
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base declarativa para todos los modelos ORM."""
    pass

# IMPORTANTE: importar modelos para que Base.metadata los registre
from app import models  # noqa: F401