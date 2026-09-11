from fastapi import APIRouter
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal

router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict[str, str]:
    database = "ok"
    redis_status = "ok"

    try:
        async with SessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        database = "error"

    redis_client = Redis.from_url(settings.redis_url)
    try:
        await redis_client.ping()
    except Exception:
        redis_status = "error"
    finally:
        await redis_client.aclose()

    overall = "ok" if database == redis_status == "ok" else "degraded"
    return {"status": overall, "database": database, "redis": redis_status}


@router.get("/version")
async def version() -> dict[str, str]:
    return {"name": settings.app_name, "version": "0.1.0"}

