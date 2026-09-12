from app.db.session import SessionLocal
from app.models.system_setting import SystemSetting


async def browser_timeout_ms() -> int:
    """Return the administrator-configured Playwright timeout in milliseconds."""
    async with SessionLocal() as db:
        value = await db.get(SystemSetting, 1)
        seconds = value.browser_timeout_seconds if value else 30
    return max(10, min(180, seconds)) * 1000
