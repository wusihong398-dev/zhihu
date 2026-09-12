import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_active_user
from app.db.session import get_db
from app.models.account import AccountStatus, ZhihuAccount
from app.models.user import User, UserRole
from app.schemas.account import (
    AccountCreate,
    AccountRead,
    AccountUpdate,
    ZhihuLoginSessionRead,
)
from app.services.access_control import get_account_for_user
from app.services.account_storage import initialize_account_storage
from app.services.zhihu_login import (
    ZhihuLoginError,
    cancel_login_session,
    get_login_session,
    poll_login_session,
    start_login_session,
)

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


def _login_session_response(session) -> ZhihuLoginSessionRead:
    return ZhihuLoginSessionRead(
        session_id=session.id,
        account_id=session.account_id,
        status=session.status,
        message=session.message,
        screenshot_version=session.screenshot_version,
        created_at=session.created_at,
        expires_at=session.expires_at,
    )


@router.post(
    "/{account_id}/login-session",
    response_model=ZhihuLoginSessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_login_session(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ZhihuLoginSessionRead:
    account = await get_account_for_user(account_id, user, db)
    try:
        session = await start_login_session(account)
    except ZhihuLoginError as exc:
        account.status = AccountStatus.offline
        await db.commit()
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    account.status = session.status
    await db.commit()
    return _login_session_response(session)


@router.get(
    "/{account_id}/login-session/{session_id}",
    response_model=ZhihuLoginSessionRead,
)
async def read_login_session(
    account_id: uuid.UUID,
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ZhihuLoginSessionRead:
    account = await get_account_for_user(account_id, user, db)
    try:
        session = await poll_login_session(account.id, session_id)
    except ZhihuLoginError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if account.status != session.status:
        account.status = session.status
        await db.commit()
    return _login_session_response(session)


@router.get("/{account_id}/login-session/{session_id}/screenshot")
async def read_login_screenshot(
    account_id: uuid.UUID,
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> FileResponse:
    account = await get_account_for_user(account_id, user, db)
    try:
        session = get_login_session(account.id, session_id)
    except ZhihuLoginError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not session.screenshot_path.is_file():
        raise HTTPException(status_code=404, detail="登录页面截图尚未生成")
    return FileResponse(
        session.screenshot_path,
        media_type="image/png",
        headers={"Cache-Control": "no-store, private"},
    )


@router.delete(
    "/{account_id}/login-session/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_login_session(
    account_id: uuid.UUID,
    session_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> Response:
    account = await get_account_for_user(account_id, user, db)
    try:
        await cancel_login_session(account.id, session_id)
    except ZhihuLoginError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if account.status != AccountStatus.online:
        account.status = AccountStatus.offline
        await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
