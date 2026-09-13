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
        if engine.dialect.name == "postgresql":
            # create_all creates media tables for new installations but does not
            # add columns to an existing articles table.
            await connection.execute(
                text(
                    "ALTER TABLE articles ADD COLUMN IF NOT EXISTS "
                    "local_image_id UUID NULL"
                )
            )
            await connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_articles_local_image_id "
                    "ON articles (local_image_id)"
                )
            )
        if engine.dialect.name == "postgresql":
            await connection.execute(
                text(
                    "ALTER TABLE answer_prompt_templates ADD COLUMN IF NOT EXISTS "
                    "folder_id UUID NULL"
                )
            )
            # v0.16.0 temporarily shared article folders with answer templates.
            # Copy every referenced folder with the same UUID before moving the
            # foreign key, so existing answer template organization is preserved.
            await connection.execute(
                text(
                    "INSERT INTO answer_prompt_folders "
                    "(id, user_id, name, normalized_name, created_at, updated_at) "
                    "SELECT DISTINCT f.id, f.user_id, f.name, f.normalized_name, "
                    "f.created_at, f.updated_at "
                    "FROM article_prompt_folders f "
                    "JOIN answer_prompt_templates t ON t.folder_id = f.id "
                    "ON CONFLICT (id) DO NOTHING"
                )
            )
            await connection.execute(
                text(
                    "DO $$ DECLARE old_constraint TEXT; BEGIN "
                    "SELECT c.conname INTO old_constraint "
                    "FROM pg_constraint c "
                    "JOIN pg_class target ON target.oid = c.conrelid "
                    "JOIN pg_class referenced ON referenced.oid = c.confrelid "
                    "WHERE target.relname = 'answer_prompt_templates' "
                    "AND referenced.relname = 'article_prompt_folders' "
                    "AND c.contype = 'f' "
                    "AND pg_get_constraintdef(c.oid) LIKE "
                    "'FOREIGN KEY (folder_id)%' LIMIT 1; "
                    "IF old_constraint IS NOT NULL THEN "
                    "EXECUTE format('ALTER TABLE answer_prompt_templates "
                    "DROP CONSTRAINT %I', old_constraint); "
                    "END IF; "
                    "IF NOT EXISTS (SELECT 1 FROM pg_constraint c "
                    "JOIN pg_class target ON target.oid = c.conrelid "
                    "JOIN pg_class referenced ON referenced.oid = c.confrelid "
                    "WHERE target.relname = 'answer_prompt_templates' "
                    "AND referenced.relname = 'answer_prompt_folders' "
                    "AND c.contype = 'f' "
                    "AND pg_get_constraintdef(c.oid) LIKE "
                    "'FOREIGN KEY (folder_id)%') THEN "
                    "ALTER TABLE answer_prompt_templates ADD CONSTRAINT "
                    "fk_answer_prompt_templates_folder_id FOREIGN KEY (folder_id) "
                    "REFERENCES answer_prompt_folders(id) ON DELETE SET NULL; "
                    "END IF; END $$"
                )
            )
            await connection.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_answer_prompt_templates_folder_id "
                    "ON answer_prompt_templates (folder_id)"
                )
            )
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
