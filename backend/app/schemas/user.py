import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.user import UserRole


class ManagedUserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=100, pattern=r"^[A-Za-z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=1024)
    expires_at: datetime | None = None
    is_active: bool = True

    @field_validator("username")
    @classmethod
    def normalize_username(cls, value: str) -> str:
        return value.strip()


class ManagedUserUpdate(BaseModel):
    expires_at: datetime | None = None
    is_active: bool | None = None


class ManagedUserPasswordReset(BaseModel):
    password: str = Field(min_length=12, max_length=1024)


class ManagedUserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    username: str
    role: UserRole
    is_active: bool
    expires_at: datetime | None
    account_count: int = 0
    created_at: datetime
    last_login_at: datetime | None
