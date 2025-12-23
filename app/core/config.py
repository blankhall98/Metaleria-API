# app/core/config.py
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Entorno
    ENV: str = "dev"  # dev | prod
    DEBUG: bool = True

    # App
    PROJECT_NAME: str = "Scrap360 MVP"
    BACKEND_CORS_ORIGINS: List[str] = ["*"]

    # Base de datos (ajustaremos en Paso 3)
    DATABASE_URL: str = "sqlite:///./metalleria.db"

    # Seguridad
    SECRET_KEY: str

    # Firebase Storage
    FIREBASE_BUCKET: str = "metaleria-api-z2h.firebasestorage.app"
    FIREBASE_CREDENTIALS_FILE: str = "secrets/metaleria-api-z2h-firebase-adminsdk-fbsvc-18da50717b.json"
    FIREBASE_CREDENTIALS_JSON: str | None = None
    FIREBASE_MAX_MB: int = 8

    # Notas: alerta de vencimiento (dias)
    NOTA_VENCIMIENTO_ALERTA_DIAS: int = 5

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
