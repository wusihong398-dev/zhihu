import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_admin
from app.db.session import get_db
from app.models.account import ZhihuAccount
from app.schemas.account import AccountCreate, AccountRead, AccountUpdate
from app.services.account_storage import initialize_account_storage

router = APIRouter(
    prefix="/accounts",
    tags=["accounts"],
    dependencies=[Depends(require_admin)],
)


@router.post("", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: AccountCreate, db: AsyncSession = Depends(get_db)
) -> ZhihuAccount:
    account = ZhihuAccount(**payload.model_dump())
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
async def list_accounts(db: AsyncSession = Depends(get_db)) -> list[ZhihuAccount]:
    result = await db.execute(select(ZhihuAccount).order_by(ZhihuAccount.created_at))
    return list(result.scalars())


@router.get("/{account_id}", response_model=AccountRead)
async def get_account(
    account_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> ZhihuAccount:
    account = await db.get(ZhihuAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    return account


@router.patch("/{account_id}", response_model=AccountRead)
async def update_account(
    account_id: uuid.UUID,
    payload: AccountUpdate,
    db: AsyncSession = Depends(get_db),
) -> ZhihuAccount:
    account = await db.get(ZhihuAccount, account_id)
    if account is None:
        raise HTTPException(status_code=404, detail="账号不存在")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(account, field, value)
    await db.commit()
    await db.refresh(account)
    return account
