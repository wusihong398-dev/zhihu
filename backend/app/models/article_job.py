import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ArticleJobType(str, enum.Enum):
    generate = "generate"
    publish = "publish"


class ArticleJobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    paused = "paused"
    stopped = "stopped"
    completed = "completed"
    failed = "failed"


class ArticleOutputMode(str, enum.Enum):
    draft = "draft"
    immediate = "immediate"


class ArticleJob(Base):
    __tablename__ = "article_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("zhihu_accounts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    job_type: Mapped[ArticleJobType] = mapped_column(
        Enum(ArticleJobType, name="article_job_type"), index=True
    )
    status: Mapped[ArticleJobStatus] = mapped_column(
        Enum(ArticleJobStatus, name="article_job_status"),
        default=ArticleJobStatus.pending,
        index=True,
    )
    output_mode: Mapped[ArticleOutputMode | None] = mapped_column(
        Enum(ArticleOutputMode, name="article_output_mode"), nullable=True
    )
    total_count: Mapped[int] = mapped_column(Integer, default=0)
    completed_count: Mapped[int] = mapped_column(Integer, default=0)
    success_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    current_item: Mapped[str | None] = mapped_column(String(300), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
