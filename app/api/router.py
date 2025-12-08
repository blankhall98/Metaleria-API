# app/api/router.py
from fastapi import APIRouter

from app.core.config import get_settings

api_router = APIRouter()
settings = get_settings()

@api_router.get("/health", tags=["health"])
async def health_check():
    return {
        "status": "ok",
        "env": settings.ENV,
        "version": "0.1.0",
        "service": settings.PROJECT_NAME,
    }
