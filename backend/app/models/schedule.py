import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ScheduleTaskType(str, enum.Enum):
    keyword_collect = "keyword_collect"
    article_generate = "article_generate"
    article_publish = "article_publish"
    question_collect = "question_collect"
    answer_generate = "answer_generate"
    answer_publish = "answer_publish"


class ScheduleRunStatus(str, enum.Enum):
    never = "never"
    running = "running"
    success = "success"
    failed = "failed"
    skipped = "skipped"


class OperationSchedule(Base):
    __tablename__ = "operation_schedules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zhihu_accounts.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(120))
    task_type: Mapped[ScheduleTaskType] = mapped_column(
        Enum(ScheduleTaskType, name="schedule_task_type"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    hour: Mapped[int] = mapped_column(Integer, default=9)
    minute: Mapped[int] = mapped_column(Integer, default=0)
    weekdays: Mapped[str] = mapped_column(String(32), default="0,1,2,3,4,5,6")
    config_json: Mapped[str] = mapped_column(Text, default="{}")
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_status: Mapped[ScheduleRunStatus] = mapped_column(
        Enum(ScheduleRunStatus, name="schedule_run_status"), default=ScheduleRunStatus.never
    )
    last_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_reference_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
