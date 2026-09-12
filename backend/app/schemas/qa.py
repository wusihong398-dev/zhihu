import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.answer import AnswerStatus
from app.models.answer_job import AnswerJobStatus, AnswerJobType
from app.models.question import QuestionStatus


DEFAULT_ANSWER_PROMPT = (
    "请围绕问题给出专业、自然、有实际帮助的回答。结合商品真实资料进行适度推荐，"
    "先解决问题，再自然说明适用场景，不要生硬植入，不要虚构个人经历、检测数据、"
    "医疗效果、资质或保证性承诺。"
)


class QuestionCollectRequest(BaseModel):
    keywords: list[str] = Field(min_length=1, max_length=10)
    target_count: int = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def clean_keywords(self):
        cleaned = list(
            dict.fromkeys(" ".join(item.split()) for item in self.keywords if item.strip())
        )
        if not cleaned:
            raise ValueError("至少填写一个采集关键词")
        self.keywords = cleaned
        return self


class QuestionRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    zhihu_question_id: str
    title: str
    url: str
    keyword_text: str
    excerpt: str
    answer_count: int
    follower_count: int
    status: QuestionStatus
    discovered_at: datetime
    updated_at: datetime


class QuestionListResponse(BaseModel):
    items: list[QuestionRead]
    total: int


class QuestionCollectResponse(BaseModel):
    scanned_count: int
    added_count: int
    duplicate_count: int
    items: list[QuestionRead]


class QuestionBulkRequest(BaseModel):
    question_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class AnswerGenerateRequest(BaseModel):
    question_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    product_id: uuid.UUID
    provider: str = Field(min_length=1, max_length=32)
    model: str | None = Field(default=None, max_length=128)
    min_length: int = Field(default=300, ge=100, le=5000)
    max_length: int = Field(default=800, ge=100, le=10000)
    prompt: str = Field(default=DEFAULT_ANSWER_PROMPT, min_length=1, max_length=20000)
    ready_after_generate: bool = False

    @model_validator(mode="after")
    def validate_lengths(self):
        if self.max_length < self.min_length:
            raise ValueError("最大字数不能小于最小字数")
        return self


class AnswerCreate(BaseModel):
    question_id: uuid.UUID
    product_id: uuid.UUID | None = None
    content: str = Field(min_length=1, max_length=100000)
    status: AnswerStatus = AnswerStatus.draft


class AnswerUpdate(BaseModel):
    content: str | None = Field(default=None, min_length=1, max_length=100000)
    status: AnswerStatus | None = None


class AnswerRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    account_name: str = ""
    question_id: uuid.UUID
    product_id: uuid.UUID | None
    question_title: str
    question_url: str
    keyword_text: str
    product_name: str
    content: str
    content_length: int
    status: AnswerStatus
    provider: str
    model: str
    error_message: str | None
    published_url: str | None
    published_at: datetime | None
    publish_attempted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class AnswerListResponse(BaseModel):
    items: list[AnswerRead]
    total: int


class AnswerBulkRequest(BaseModel):
    answer_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    status: AnswerStatus | None = None


class AnswerBulkResult(BaseModel):
    affected_count: int


class AnswerPublishJobCreate(BaseModel):
    answer_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class AnswerJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    account_id: uuid.UUID
    job_type: AnswerJobType
    status: AnswerJobStatus
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


class AutoAnswerRunResponse(BaseModel):
    daily_limit: int
    attempted_today: int
    queued_count: int
    job: AnswerJobRead | None


class AutoAnswerSummary(BaseModel):
    daily_limit: int
    attempted_today: int
    remaining_today: int
    ready_count: int
    published_today: int
