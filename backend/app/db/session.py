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
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
