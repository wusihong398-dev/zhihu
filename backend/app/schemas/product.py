import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _validate_promotion_url(value: str) -> str:
    value = value.strip()
    if value and not value.lower().startswith(("http://", "https://")):
        raise ValueError("推广链接必须以 http:// 或 https:// 开头")
    return value


class ProductCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="", max_length=100)
    description: str = Field(default="", max_length=10000)
    selling_points: str = Field(default="", max_length=10000)
    target_audience: str = Field(default="", max_length=10000)
    promotion_url: str = Field(default="", max_length=2048)
    content_requirements: str = Field(default="", max_length=10000)
    forbidden_terms: str = Field(default="", max_length=10000)
    enabled: bool = True

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("商品名称不能为空")
        return value

    @field_validator("category")
    @classmethod
    def strip_category(cls, value: str) -> str:
        return value.strip()

    @field_validator("promotion_url")
    @classmethod
    def validate_promotion_url(cls, value: str) -> str:
        return _validate_promotion_url(value)


class ProductUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=100)
    description: str | None = Field(default=None, max_length=10000)
    selling_points: str | None = Field(default=None, max_length=10000)
    target_audience: str | None = Field(default=None, max_length=10000)
    promotion_url: str | None = Field(default=None, max_length=2048)
    content_requirements: str | None = Field(default=None, max_length=10000)
    forbidden_terms: str | None = Field(default=None, max_length=10000)
    enabled: bool | None = None

    @field_validator("name")
    @classmethod
    def validate_optional_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("商品名称不能为空")
        return value

    @field_validator("category")
    @classmethod
    def strip_optional_category(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("promotion_url")
    @classmethod
    def validate_optional_promotion_url(cls, value: str | None) -> str | None:
        return _validate_promotion_url(value) if value is not None else None


class ProductRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    name: str
    category: str
    description: str
    selling_points: str
    target_audience: str
    promotion_url: str
    content_requirements: str
    forbidden_terms: str
    enabled: bool
    created_at: datetime
    updated_at: datetime


class ProductListResponse(BaseModel):
    items: list[ProductRead]
    total: int
