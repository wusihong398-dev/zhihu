from app.api.accounts import router as accounts_router
from app.api.ai import router as ai_router
from app.api.articles import router as articles_router
from app.api.article_prompts import router as article_prompts_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.keywords import router as keywords_router
from app.api.products import router as products_router
from app.api.qa import router as qa_router
from app.api.operations import router as operations_router
from app.api.users import router as users_router

__all__ = [
    "accounts_router",
    "ai_router",
    "articles_router",
    "article_prompts_router",
    "auth_router",
    "health_router",
    "keywords_router",
    "products_router",
    "qa_router",
    "operations_router",
    "users_router",
]
