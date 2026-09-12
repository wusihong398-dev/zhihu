import uuid

from fastapi import APIRouter, Depends, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_active_user
from app.db.session import get_db
from app.models.account import ZhihuAccount
from app.models.user import User, UserRole
from app.schemas.account import AccountCreate, AccountRead, AccountUpdate
from app.services.access_control import get_account_for_user
from app.services.account_storage import initialize_account_storage

router = APIRouter(
    prefix="/accounts",
    tags=["accounts"],
    dependencies=[Depends(require_active_user)],
)


@router.post("", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: AccountCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ZhihuAccount:
    owner_user_id = None if user.role == UserRole.admin else user.id
    account = ZhihuAccount(owner_user_id=owner_user_id, **payload.model_dump())
    db.add(account)
    await db.flush()
    try:
        initialize_account_storage(account.id, account.profile_key)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    await db.refresh(account)
    return account


@router.get("", response_model=list[AccountRead])
async def list_accounts(
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> list[ZhihuAccount]:
    query = select(ZhihuAccount)
    if user.role != UserRole.admin:
        query = query.where(ZhihuAccount.owner_user_id == user.id)
    result = await db.execute(query.order_by(ZhihuAccount.created_at))
    return list(result.scalars())


@router.get("/{account_id}", response_model=AccountRead)
async def get_account(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ZhihuAccount:
    return await get_account_for_user(account_id, user, db)


@router.patch("/{account_id}", response_model=AccountRead)
async def update_account(
    account_id: uuid.UUID,
    payload: AccountUpdate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ZhihuAccount:
    account = await get_account_for_user(account_id, user, db)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(account, field, value)
    await db.commit()
    await db.refresh(account)
    return account
