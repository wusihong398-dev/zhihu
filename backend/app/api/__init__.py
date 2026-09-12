from app.api.accounts import router as accounts_router
from app.api.ai import router as ai_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router
from app.api.keywords import router as keywords_router
from app.api.products import router as products_router
from app.api.users import router as users_router

__all__ = [
    "accounts_router",
    "ai_router",
    "auth_router",
    "health_router",
    "keywords_router",
    "products_router",
    "users_router",
]
