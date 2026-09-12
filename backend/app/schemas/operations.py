import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.models.schedule import ScheduleRunStatus, ScheduleTaskType


class ScheduleCreate(BaseModel):
    account_id: uuid.UUID
    name: str = Field(min_length=1, max_length=120)
    task_type: ScheduleTaskType
    enabled: bool = True
    hour: int = Field(default=9, ge=0, le=23)
    minute: int = Field(default=0, ge=0, le=59)
    weekdays: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)
    config: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_weekdays(self):
        values = sorted(set(self.weekdays))
        if any(value < 0 or value > 6 for value in values):
            raise ValueError("星期必须是 0 到 6")
        self.weekdays = values
        return self


class ScheduleUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    enabled: bool | None = None
    hour: int | None = Field(default=None, ge=0, le=23)
    minute: int | None = Field(default=None, ge=0, le=59)
    weekdays: list[int] | None = Field(default=None, min_length=1, max_length=7)
    config: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_weekdays(self):
        if self.weekdays is not None:
            values = sorted(set(self.weekdays))
            if any(value < 0 or value > 6 for value in values):
                raise ValueError("星期必须是 0 到 6")
            self.weekdays = values
        return self


class ScheduleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    account_id: uuid.UUID
    account_name: str = ""
    name: str
    task_type: ScheduleTaskType
    enabled: bool
    hour: int
    minute: int
    weekdays: list[int]
    config: dict[str, Any]
    next_run_at: datetime | None
    last_started_at: datetime | None
    last_completed_at: datetime | None
    last_status: ScheduleRunStatus
    last_message: str | None
    last_reference_id: str | None
    created_at: datetime
    updated_at: datetime


class OperationLogRead(BaseModel):
    id: str
    account_id: uuid.UUID | None
    account_name: str
    category: str
    task_name: str
    status: str
    total_count: int
    success_count: int
    failed_count: int
    message: str | None
    started_at: datetime
    completed_at: datetime | None


class OperationLogList(BaseModel):
    items: list[OperationLogRead]
    total: int


class SystemSettingRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    log_retention_days: int
    publish_interval_min: int
    publish_interval_max: int
    browser_timeout_seconds: int
    default_timezone: str
    updated_at: datetime


class SystemSettingUpdate(BaseModel):
    log_retention_days: int = Field(ge=7, le=3650)
    publish_interval_min: int = Field(ge=0, le=3600)
    publish_interval_max: int = Field(ge=0, le=3600)
    browser_timeout_seconds: int = Field(ge=10, le=180)
    default_timezone: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def validate_interval(self):
        if self.publish_interval_max < self.publish_interval_min:
            raise ValueError("最大发布间隔不能小于最小发布间隔")
        return self
