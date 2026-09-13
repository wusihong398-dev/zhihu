from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import settings


class Base(DeclarativeBase):
    pass


engine = create_async_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session


async def create_schema() -> None:
    if engine.dialect.name == "postgresql":
        async with engine.begin() as connection:
            user_role_exists = await connection.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'user_role')"
                )
            )
            if user_role_exists:
                await connection.execute(
                    text("ALTER TYPE user_role ADD VALUE IF NOT EXISTS 'user'")
                )
            users_exists = await connection.scalar(
                text("SELECT to_regclass('public.users') IS NOT NULL")
            )
            if users_exists:
                await connection.execute(
                    text(
                        "ALTER TABLE users ADD COLUMN IF NOT EXISTS "
                        "expires_at TIMESTAMPTZ NULL"
                    )
                )
            accounts_exist = await connection.scalar(
                text("SELECT to_regclass('public.zhihu_accounts') IS NOT NULL")
            )
            if accounts_exist:
                await connection.execute(
                    text(
                        "ALTER TABLE zhihu_accounts ADD COLUMN IF NOT EXISTS "
                        "owner_user_id UUID NULL REFERENCES users(id) ON DELETE RESTRICT"
                    )
                )
                await connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_zhihu_accounts_owner_user_id "
                        "ON zhihu_accounts (owner_user_id)"
                    )
                )
                await connection.execute(
                    text(
                        "ALTER TABLE zhihu_accounts ADD COLUMN IF NOT EXISTS "
                        "recycle_keywords_after_use BOOLEAN NOT NULL DEFAULT TRUE"
                    )
                )
                await connection.execute(
                    text(
                        "ALTER TABLE zhihu_accounts ADD COLUMN IF NOT EXISTS "
                        "auto_restore_keywords BOOLEAN NOT NULL DEFAULT FALSE"
                    )
                )
                await connection.execute(
                    text(
                        "ALTER TABLE zhihu_accounts ADD COLUMN IF NOT EXISTS "
                        "keyword_restore_threshold INTEGER NOT NULL DEFAULT 20"
                    )
                )
            keywords_exist = await connection.scalar(
                text("SELECT to_regclass('public.account_keywords') IS NOT NULL")
            )
            if keywords_exist:
                await connection.execute(
                    text(
                        "ALTER TABLE account_keywords ADD COLUMN IF NOT EXISTS "
                        "is_recycled BOOLEAN NOT NULL DEFAULT FALSE"
                    )
                )
                await connection.execute(
                    text(
                        "ALTER TABLE account_keywords ADD COLUMN IF NOT EXISTS "
                        "used_count INTEGER NOT NULL DEFAULT 0"
                    )
                )
                await connection.execute(
                    text(
                        "ALTER TABLE account_keywords ADD COLUMN IF NOT EXISTS "
                        "last_used_at TIMESTAMPTZ NULL"
                    )
                )
                await connection.execute(
                    text(
                        "ALTER TABLE account_keywords ADD COLUMN IF NOT EXISTS "
                        "recycled_at TIMESTAMPTZ NULL"
                    )
                )
                await connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS ix_account_keywords_is_recycled "
                        "ON account_keywords (is_recycled)"
                    )
                )
            articles_exist = await connection.scalar(
                text("SELECT to_regclass('public.articles') IS NOT NULL")
            )
            if articles_exist:
                await connection.execute(
                    text(
                        "ALTER TABLE articles ADD COLUMN IF NOT EXISTS "
                        "publish_attempted_at TIMESTAMPTZ NULL"
                    )
                )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        # Older versions accepted /p/<id>/edit as a successful publication.
        # Such a URL only proves that an editor draft exists, so make the
        # historical record honest and allow the user to publish it again.
        await connection.execute(
            text(
                "UPDATE articles SET status = 'failed', published_url = NULL, "
                "published_at = NULL, "
                "error_message = '历史记录保存的是知乎编辑页地址，未确认公开发布，请重新发布' "
                "WHERE status = 'published' AND published_url LIKE '%/edit%'"
            )
        )
        # Preserve a useful display time for publication records created before
        # publish_attempted_at was introduced. For historical failures,
        # updated_at is the closest available timestamp to the failed attempt.
        await connection.execute(
            text(
                "UPDATE articles SET publish_attempted_at = "
                "COALESCE(published_at, updated_at) "
                "WHERE publish_attempted_at IS NULL "
                "AND status IN ('published', 'failed')"
            )
        )
