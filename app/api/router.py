# app/api/router.py
from fastapi import APIRouter

from app.core.config import get_settings
from app.api import materials as materials_api
from app.api import pricing as pricing_api
from app.api import partners as partners_api
from app.api import notes as notes_api

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

api_router.include_router(materials_api.router)
api_router.include_router(pricing_api.router)
api_router.include_router(partners_api.router)
api_router.include_router(notes_api.router)
