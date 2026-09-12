from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import (
    accounts_router,
    ai_router,
    auth_router,
    health_router,
    keywords_router,
)
from app.core.config import settings
from app.db.session import create_schema
import app.models  # noqa: F401 - registers database models


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.account_data_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    await create_schema()
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.5.0",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.include_router(health_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(accounts_router, prefix="/api")
app.include_router(ai_router, prefix="/api")
app.include_router(keywords_router, prefix="/api")
