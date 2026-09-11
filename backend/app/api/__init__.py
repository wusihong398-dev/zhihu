from app.api.accounts import router as accounts_router
from app.api.auth import router as auth_router
from app.api.health import router as health_router

__all__ = ["accounts_router", "auth_router", "health_router"]
