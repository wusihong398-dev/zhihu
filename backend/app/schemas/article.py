import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.article import ArticleStatus
from app.models.article_job import (
    ArticleJobStatus,
    ArticleJobType,
    ArticleOutputMode,
)


DEFAULT_TITLE_PROMPT = (
    "围绕{关键词}拟一个自然、有吸引力、适合知乎阅读的标题，不要夸大承诺。"
)
DEFAULT_CONTENT_PROMPT = (
    "围绕{关键词}写一篇专业、自然、有实际帮助的知乎文章。商品名称：{商品名称}。"
    "商品简介：{商品简介}。核心卖点：{商品卖点}。目标人群：{目标人群}。"
    "推广链接：{推广链接}。根据这些真实资料进行适度推荐，避免生硬广告和禁用表述。"
)


class ArticleCreate(BaseModel):
    keyword_id: uuid.UUID | None = None
    product_id: uuid.UUID | None = None
    keyword_text: str = Field(default="", max_length=255)
    product_name: str = Field(default="", max_length=120)
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(default="", max_length=100000)
    status: ArticleStatus = ArticleStatus.draft


class ArticleUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=300)
    content: str | None = Field(default=None, max_length=100000)
    status: ArticleStatus | None = None


class ArticleGenerateRequest(BaseModel):
    keyword_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    product_id: uuid.UUID
    provider: str = Field(min_length=1, max_length=32)
    model: str | None = Field(default=None, max_length=128)
    articles_per_keyword: int = Field(default=1, ge=1, le=5)
    min_length: int = Field(default=800, ge=100, le=10000)
    max_length: int = Field(default=1500, ge=100, le=20000)
    title_prompt: str = Field(
        default=DEFAULT_TITLE_PROMPT, min_length=1, max_length=10000
    )
    content_prompt: str = Field(
        default=DEFAULT_CONTENT_PROMPT, min_length=1, max_length=20000
    )
    output_mode: ArticleOutputMode = ArticleOutputMode.draft

    @model_validator(mode="after")
    def validate_batch(self):
        if self.max_length < self.min_length:
            raise ValueError("最大字数不能小于最小字数")
        if len(self.keyword_ids) * self.articles_per_keyword > 50:
            raise ValueError("单次最多生成 50 篇文章")
        return self


class ArticleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    account_name: str = ""
    keyword_id: uuid.UUID | None
    product_id: uuid.UUID | None
    keyword_text: str
    product_name: str
    title: str
    content: str
    status: ArticleStatus
    provider: str
    model: str
    content_length: int
    error_message: str | None
    published_url: str | None
    published_at: datetime | None
    created_at: datetime
    updated_at: datetime


class ArticleListResponse(BaseModel):
    items: list[ArticleRead]
    total: int


class ArticleGenerateResponse(BaseModel):
    items: list[ArticleRead]
    requested_count: int
    success_count: int
    failed_count: int


class ArticleBulkRequest(BaseModel):
    article_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    status: ArticleStatus | None = None


class ArticleBulkResult(BaseModel):
    affected_count: int


class ArticlePublishJobCreate(BaseModel):
    article_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class ArticleJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    account_id: uuid.UUID | None
    job_type: ArticleJobType
    status: ArticleJobStatus
    output_mode: ArticleOutputMode | None
    total_count: int
    completed_count: int
    success_count: int
    failed_count: int
    current_item: str | None
    error_message: str | None
    progress_percent: int = 0
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
