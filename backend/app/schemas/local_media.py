import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.local_media import LocalMediaKind


class LocalMediaFolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class LocalMediaFolderUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=100)


class LocalMediaFolderRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    image_count: int = 0
    archive_count: int = 0
    created_at: datetime
    updated_at: datetime


class LocalMediaAssetRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    folder_id: uuid.UUID | None
    folder_name: str | None = None
    kind: LocalMediaKind
    original_name: str
    mime_type: str
    size_bytes: int
    extracted: bool
    extracted_at: datetime | None
    public_url: str | None = None
    created_at: datetime


class LocalMediaListResponse(BaseModel):
    items: list[LocalMediaAssetRead]
    total: int


class LocalMediaUploadResponse(BaseModel):
    items: list[LocalMediaAssetRead]
    uploaded_count: int


class LocalMediaExtractResponse(BaseModel):
    extracted_count: int
    skipped_count: int


class LocalMediaBulkRequest(BaseModel):
    asset_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)


class LocalMediaBulkResult(BaseModel):
    affected_count: int
