from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api import (
    accounts_router,
    ai_router,
    article_prompts_router,
    articles_router,
    auth_router,
    health_router,
    keywords_router,
    local_media_router,
    local_publisher_router,
    operations_router,
    products_router,
    qa_router,
    users_router,
)
from app.core.config import settings
from app.db.session import create_schema, engine
from app.services.article_jobs import close_article_jobs, recover_article_jobs
from app.services.answer_jobs import close_answer_jobs, recover_answer_jobs
from app.services.schedule_runner import close_scheduler, start_scheduler
from app.services.zhihu_login import close_all_login_sessions
import app.models  # noqa: F401 - registers database models


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings.account_data_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    settings.media_data_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    await create_schema()
    await recover_article_jobs()
    await recover_answer_jobs()
    await start_scheduler()
    try:
        yield
    finally:
        await close_scheduler()
        await close_article_jobs()
        await close_answer_jobs()
        await close_all_login_sessions()
        await engine.dispose()


app = FastAPI(
    title=settings.app_name,
    version="0.18.2",
    lifespan=lifespan,
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)
app.include_router(health_router, prefix="/api")
app.include_router(auth_router, prefix="/api")
app.include_router(accounts_router, prefix="/api")
app.include_router(ai_router, prefix="/api")
app.include_router(articles_router, prefix="/api")
app.include_router(article_prompts_router, prefix="/api")
app.include_router(keywords_router, prefix="/api")
app.include_router(local_media_router, prefix="/api")
app.include_router(local_publisher_router, prefix="/api")
app.include_router(products_router, prefix="/api")
app.include_router(qa_router, prefix="/api")
app.include_router(operations_router, prefix="/api")
app.include_router(users_router, prefix="/api")
