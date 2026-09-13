import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.account import ZhihuAccount
from app.models.keyword import AccountKeyword
from app.models.keyword_folder import KeywordFolderItem


async def restore_keywords_to_threshold(
    account: ZhihuAccount,
    db: AsyncSession,
    *,
    folder_id: uuid.UUID | None = None,
    force: bool = False,
) -> int:
    """Restore the oldest recycled words until the configured active threshold."""
    if not force and not account.auto_restore_keywords:
        return 0

    active_filters = [
        AccountKeyword.account_id == account.id,
        AccountKeyword.is_recycled.is_(False),
    ]
    recycled_filters = [
        AccountKeyword.account_id == account.id,
        AccountKeyword.is_recycled.is_(True),
    ]
    active_query = select(func.count(AccountKeyword.id))
    recycled_query = select(AccountKeyword)
    if folder_id:
        active_query = active_query.join(
            KeywordFolderItem, KeywordFolderItem.keyword_id == AccountKeyword.id
        )
        recycled_query = recycled_query.join(
            KeywordFolderItem, KeywordFolderItem.keyword_id == AccountKeyword.id
        )
        active_filters.append(KeywordFolderItem.folder_id == folder_id)
        recycled_filters.append(KeywordFolderItem.folder_id == folder_id)

    active_count = int(await db.scalar(active_query.where(*active_filters)) or 0)
    needed = max(0, account.keyword_restore_threshold - active_count)
    if not needed:
        return 0
    result = await db.execute(
        recycled_query.where(*recycled_filters)
        .order_by(
            AccountKeyword.recycled_at.asc().nullsfirst(),
            AccountKeyword.created_at.asc(),
        )
        .limit(needed)
    )
    items = list(result.scalars())
    for item in items:
        item.is_recycled = False
        item.recycled_at = None
    if items:
        await db.flush()
    return len(items)


def mark_keyword_used(keyword: AccountKeyword, *, recycle: bool) -> None:
    now = datetime.now(UTC)
    keyword.used_count = (keyword.used_count or 0) + 1
    keyword.last_used_at = now
    if recycle:
        keyword.is_recycled = True
        keyword.recycled_at = now
