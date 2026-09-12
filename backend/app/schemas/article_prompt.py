import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PromptFolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class PromptFolderUpdate(PromptFolderCreate):
    pass


class PromptFolderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    template_count: int = 0
    created_at: datetime
    updated_at: datetime


class PromptTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    folder_id: uuid.UUID | None = None
    folder_name: str | None = Field(default=None, max_length=100)
    title_prompt: str = Field(min_length=1, max_length=10000)
    content_prompt: str = Field(min_length=1, max_length=20000)


class PromptTemplateUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    folder_id: uuid.UUID | None = None
    folder_name: str | None = Field(default=None, max_length=100)
    title_prompt: str | None = Field(default=None, min_length=1, max_length=10000)
    content_prompt: str | None = Field(default=None, min_length=1, max_length=20000)


class PromptTemplateRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    folder_id: uuid.UUID | None
    folder_name: str | None = None
    name: str
    title_prompt: str
    content_prompt: str
    created_at: datetime
    updated_at: datetime


class PromptTemplateListResponse(BaseModel):
    items: list[PromptTemplateRead]
    total: int
