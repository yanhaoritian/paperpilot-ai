from fastapi import APIRouter
from sqlalchemy import text

from app.config import get_settings
from app.db import engine
from app.schemas import HealthResponse

router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    db_ok = False
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            db_ok = True
    except Exception:  # noqa: BLE001
        db_ok = False

    detail = None
    if settings.health_verbose:
        detail = {
            "base_url": settings.openai_base_url,
            "default_model": settings.default_model,
            "embedding_model": settings.embedding_model,
        }

    return HealthResponse(
        status="ok" if db_ok else "degraded",
        database=db_ok,
        has_api_key=bool(settings.openai_api_key),
        detail=detail,
    )
