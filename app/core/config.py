# app/core/config.py
from functools import lru_cache
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Entorno
    ENV: str = "dev"  # dev | prod
    DEBUG: bool = True

    # App
    PROJECT_NAME: str = "Metalería MVP"
    BACKEND_CORS_ORIGINS: List[str] = ["*"]

    # Base de datos (ajustaremos en Paso 3)
    DATABASE_URL: str = "sqlite:///./metalleria.db"

    # Seguridad
    SECRET_KEY: str

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
