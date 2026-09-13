import logging
import uuid
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import require_active_user
from app.db.session import get_db
from app.models.account import AccountStatus, ZhihuAccount
from app.models.system_setting import SystemSetting
from app.models.user import User, UserRole
from app.schemas.account import (
    AccountCreate,
    AccountRead,
    AccountUpdate,
    ZhihuBrowserAction,
    ZhihuLoginSessionRead,
)
from app.services.access_control import get_account_for_user
from app.services.account_storage import delete_account_storage, initialize_account_storage
from app.services.browser_lock import (
    get_account_browser_lock,
    remove_account_browser_lock,
)
from app.services.zhihu_login import (
    ZhihuLoginError,
    cancel_login_session,
    close_account_login_sessions,
    get_login_session,
    perform_website_action,
    poll_login_session,
    start_login_session,
    start_website_session,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/accounts",
    tags=["accounts"],
    dependencies=[Depends(require_active_user)],
)


def _validate_timezone(value: str | None) -> str:
    if not value:
        raise HTTPException(status_code=422, detail="账号时区不能为空")
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="账号时区无效") from exc
    return value


@router.post("", response_model=AccountRead, status_code=status.HTTP_201_CREATED)
async def create_account(
    payload: AccountCreate,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ZhihuAccount:
    owner_user_id = None if user.role == UserRole.admin else user.id
    data = payload.model_dump()
    if not data["timezone"]:
        system_setting = await db.get(SystemSetting, 1)
        data["timezone"] = (
            system_setting.default_timezone if system_setting else "Asia/Shanghai"
        )
    data["timezone"] = _validate_timezone(data["timezone"])
    account = ZhihuAccount(owner_user_id=owner_user_id, **data)
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
    data = payload.model_dump(exclude_unset=True)
    if "timezone" in data:
        data["timezone"] = _validate_timezone(data["timezone"])
    for field, value in data.items():
        setattr(account, field, value)
    await db.commit()
    await db.refresh(account)
    return account


@router.delete("/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_account(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> Response:
    account = await get_account_for_user(account_id, user, db)
    await close_account_login_sessions(account.id)
    browser_lock = get_account_browser_lock(account.id)
    if browser_lock.locked():
        raise HTTPException(
            status_code=409,
            detail="该账号正在发布文章，请先停止任务或等待当前文章处理完成",
        )
    profile_key = account.profile_key
    await db.delete(account)
    await db.commit()
    try:
        delete_account_storage(account_id, profile_key)
    except (OSError, ValueError):
        logger.exception("Failed to remove account storage for %s", account_id)
    remove_account_browser_lock(account_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _login_session_response(session) -> ZhihuLoginSessionRead:
    return ZhihuLoginSessionRead(
        session_id=session.id,
        account_id=session.account_id,
        mode=session.mode,
        status=session.status,
        message=session.message,
        page_url=session.page_url,
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


@router.post(
    "/{account_id}/website-session",
    response_model=ZhihuLoginSessionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_website_session(
    account_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ZhihuLoginSessionRead:
    account = await get_account_for_user(account_id, user, db)
    try:
        session = await start_website_session(account)
    except ZhihuLoginError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if account.status != session.status:
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


@router.post(
    "/{account_id}/login-session/{session_id}/browser-action",
    response_model=ZhihuLoginSessionRead,
)
async def website_browser_action(
    account_id: uuid.UUID,
    session_id: uuid.UUID,
    payload: ZhihuBrowserAction,
    db: AsyncSession = Depends(get_db),
    user: User = Depends(require_active_user),
) -> ZhihuLoginSessionRead:
    account = await get_account_for_user(account_id, user, db)
    try:
        session = await perform_website_action(
            account.id,
            session_id,
            action=payload.action,
            x=payload.x,
            y=payload.y,
            text=payload.text,
            key=payload.key,
            delta_y=payload.delta_y,
        )
    except ZhihuLoginError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if account.status != session.status:
        account.status = session.status
        await db.commit()
    return _login_session_response(session)


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
