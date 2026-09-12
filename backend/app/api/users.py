import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import hash_password, require_admin
from app.db.session import get_db
from app.models.account import ZhihuAccount
from app.models.user import User, UserRole
from app.schemas.user import (
    ManagedUserCreate,
    ManagedUserPasswordReset,
    ManagedUserRead,
    ManagedUserUpdate,
)

router = APIRouter(
    prefix="/users",
    tags=["users"],
    dependencies=[Depends(require_admin)],
)


async def _require_managed_user(user_id: uuid.UUID, db: AsyncSession) -> User:
    user = await db.get(User, user_id)
    if user is None or user.role != UserRole.user:
        raise HTTPException(status_code=404, detail="用户不存在")
    return user


def _to_read(user: User, account_count: int = 0) -> ManagedUserRead:
    return ManagedUserRead(
        id=user.id,
        username=user.username,
        role=user.role,
        is_active=user.is_active,
        expires_at=user.expires_at,
        account_count=account_count,
        created_at=user.created_at,
        last_login_at=user.last_login_at,
    )


@router.get("", response_model=list[ManagedUserRead])
async def list_users(db: AsyncSession = Depends(get_db)) -> list[ManagedUserRead]:
    result = await db.execute(
        select(User, func.count(ZhihuAccount.id))
        .outerjoin(ZhihuAccount, ZhihuAccount.owner_user_id == User.id)
        .where(User.role == UserRole.user)
        .group_by(User.id)
        .order_by(User.created_at.desc())
    )
    return [_to_read(user, count) for user, count in result.all()]


@router.post("", response_model=ManagedUserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: ManagedUserCreate, db: AsyncSession = Depends(get_db)
) -> ManagedUserRead:
    duplicate = await db.scalar(
        select(func.count(User.id)).where(
            func.lower(User.username) == payload.username.lower()
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail="用户名已经存在")
    user = User(
        username=payload.username,
        password_hash=hash_password(payload.password),
        role=UserRole.user,
        is_active=payload.is_active,
        expires_at=payload.expires_at,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return _to_read(user)


@router.patch("/{user_id}", response_model=ManagedUserRead)
async def update_user(
    user_id: uuid.UUID,
    payload: ManagedUserUpdate,
    db: AsyncSession = Depends(get_db),
) -> ManagedUserRead:
    user = await _require_managed_user(user_id, db)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    await db.commit()
    await db.refresh(user)
    count = await db.scalar(
        select(func.count(ZhihuAccount.id)).where(ZhihuAccount.owner_user_id == user.id)
    )
    return _to_read(user, count or 0)


@router.post("/{user_id}/reset-password", status_code=status.HTTP_204_NO_CONTENT)
async def reset_user_password(
    user_id: uuid.UUID,
    payload: ManagedUserPasswordReset,
    db: AsyncSession = Depends(get_db),
) -> None:
    user = await _require_managed_user(user_id, db)
    user.password_hash = hash_password(payload.password)
    await db.commit()
