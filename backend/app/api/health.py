from fastapi import APIRouter
from fastapi.responses import JSONResponse
from redis.asyncio import Redis
from sqlalchemy import text

from app.core.config import settings
from app.db.session import SessionLocal

router = APIRouter(tags=["system"])


@router.get("/health", response_model=None)
async def health():
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
    payload = {"status": overall, "database": database, "redis": redis_status}
    status_code = 200 if overall == "ok" else 503
    return JSONResponse(status_code=status_code, content=payload)


@router.get("/version")
async def version() -> dict[str, str]:
    return {"name": settings.app_name, "version": "0.16.2"}
