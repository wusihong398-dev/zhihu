import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class LocalPublisherDeviceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class LocalPublisherDeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    enabled: bool
    last_seen_at: datetime | None
    created_at: datetime


class LocalPublisherDeviceCreated(LocalPublisherDeviceRead):
    token: str


class LocalPublisherAccountRead(BaseModel):
    id: uuid.UUID
    display_name: str
    remark: str
    timezone: str


class LocalPublisherTaskRead(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    answer_id: uuid.UUID
    account_id: uuid.UUID
    account_name: str
    question_title: str
    question_url: str
    content: str
    attempt_count: int
    lease_expires_at: datetime


class LocalPublisherTaskResult(BaseModel):
    success: bool
    published_url: str | None = Field(default=None, max_length=2048)
    error_message: str | None = Field(default=None, max_length=2000)


class LocalPublisherHeartbeatRead(BaseModel):
    lease_expires_at: datetime

