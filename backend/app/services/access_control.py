import uuid

from fastapi import Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_active_user
from app.db.session import get_db
from app.models.account import ZhihuAccount
from app.models.user import User, UserRole


async def get_account_for_user(
    account_id: uuid.UUID, user: User, db: AsyncSession
) -> ZhihuAccount:
    filters = [ZhihuAccount.id == account_id]
    if user.role != UserRole.admin:
        filters.append(ZhihuAccount.owner_user_id == user.id)
    result = await db.execute(select(ZhihuAccount).where(*filters))
    account = result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="知乎账号不存在")
    return account


async def require_account_access(
    account_id: uuid.UUID,
    user: User = Depends(require_active_user),
    db: AsyncSession = Depends(get_db),
) -> ZhihuAccount:
    return await get_account_for_user(account_id, user, db)
