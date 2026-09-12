from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.security import (
    create_access_token,
    require_active_user,
    user_is_expired,
    verify_password,
)
from app.db.session import get_db
from app.models.user import User
from app.schemas.auth import LoginRequest, LoginResponse, UserRead

router = APIRouter(prefix="/auth", tags=["authentication"])


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest, db: AsyncSession = Depends(get_db)
) -> LoginResponse:
    result = await db.execute(select(User).where(User.username == payload.username))
    user = result.scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="账号或密码错误",
        )
    if not user.is_active:
        raise HTTPException(status_code=403, detail="账号已停用，请联系管理员")
    if user_is_expired(user):
        raise HTTPException(
            status_code=403, detail="账号使用期限已到期，请联系管理员续期"
        )

    user.last_login_at = datetime.now(UTC)
    await db.commit()
    await db.refresh(user)
    token, expires_in = create_access_token(user)
    return LoginResponse(access_token=token, expires_in=expires_in, user=user)


@router.get("/me", response_model=UserRead)
async def me(user: User = Depends(require_active_user)) -> User:
    return user
