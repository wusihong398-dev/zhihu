import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.keyword import KeywordSource
from app.models.keyword_job import KeywordJobSource, KeywordJobStatus


class KeywordJobCreate(BaseModel):
    seed_keyword: str = Field(min_length=1, max_length=255)
    source: KeywordJobSource = KeywordJobSource.both
    target_count: int = Field(default=50, ge=1, le=500)
    folder_id: uuid.UUID | None = None


class KeywordJobRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    seed_keyword: str
    source: KeywordJobSource
    status: KeywordJobStatus
    target_count: int
    collected_count: int
    searched_count: int
    current_keyword: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class KeywordRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    account_id: uuid.UUID
    keyword: str
    source: KeywordSource
    seed_keyword: str
    parent_keyword: str | None
    depth: int
    folder_id: uuid.UUID | None = None
    created_at: datetime


class KeywordListResponse(BaseModel):
    items: list[KeywordRead]
    total: int


class KeywordFolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class KeywordFolderUpdate(KeywordFolderCreate):
    pass


class KeywordFolderRead(BaseModel):
    id: uuid.UUID
    account_id: uuid.UUID
    name: str
    keyword_count: int
    created_at: datetime
    updated_at: datetime


class KeywordMoveRequest(BaseModel):
    keyword_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    folder_id: uuid.UUID | None = None


class KeywordDeleteRequest(BaseModel):
    keyword_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class KeywordBulkResult(BaseModel):
    affected_count: int
