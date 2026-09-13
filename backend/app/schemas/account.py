import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.account import AccountStatus


class AccountCreate(BaseModel):
    display_name: str = Field(min_length=1, max_length=100)
    remark: str = Field(default="", max_length=255)
    daily_article_limit: int = Field(default=0, ge=0, le=100)
    daily_answer_limit: int = Field(default=0, ge=0, le=200)
    recycle_keywords_after_use: bool = True
    auto_restore_keywords: bool = False
    keyword_restore_threshold: int = Field(default=20, ge=1, le=500)
    timezone: str | None = Field(default=None, max_length=64)


class AccountUpdate(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=100)
    remark: str | None = Field(default=None, max_length=255)
    daily_article_limit: int | None = Field(default=None, ge=0, le=100)
    daily_answer_limit: int | None = Field(default=None, ge=0, le=200)
    recycle_keywords_after_use: bool | None = None
    auto_restore_keywords: bool | None = None
    keyword_restore_threshold: int | None = Field(default=None, ge=1, le=500)
    timezone: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None


class AccountRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_user_id: uuid.UUID | None
    display_name: str
    remark: str
    status: AccountStatus
    enabled: bool
    daily_article_limit: int
    daily_answer_limit: int
    recycle_keywords_after_use: bool
    auto_restore_keywords: bool
    keyword_restore_threshold: int
    timezone: str
    created_at: datetime
    updated_at: datetime


class ZhihuLoginSessionRead(BaseModel):
    session_id: uuid.UUID
    account_id: uuid.UUID
    mode: Literal["login", "website"]
    status: AccountStatus
    message: str
    page_url: str
    screenshot_version: int
    created_at: datetime
    expires_at: datetime


class ZhihuBrowserAction(BaseModel):
    action: Literal[
        "click", "type", "key", "scroll", "home", "back", "forward", "reload"
    ]
    x: float | None = Field(default=None, ge=0, le=5000)
    y: float | None = Field(default=None, ge=0, le=10000)
    text: str | None = Field(default=None, max_length=2000)
    key: str | None = Field(default=None, max_length=40)
    delta_y: int | None = Field(default=None, ge=-5000, le=5000)
