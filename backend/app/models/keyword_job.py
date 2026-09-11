import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class KeywordJobSource(str, enum.Enum):
    baidu = "baidu"
    google = "google"
    both = "both"


class KeywordJobStatus(str, enum.Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    partial = "partial"
    failed = "failed"


class KeywordCollectionJob(Base):
    __tablename__ = "keyword_collection_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zhihu_accounts.id", ondelete="CASCADE"), index=True
    )
    seed_keyword: Mapped[str] = mapped_column(String(255))
    source: Mapped[KeywordJobSource] = mapped_column(
        Enum(KeywordJobSource, name="keyword_job_source")
    )
    status: Mapped[KeywordJobStatus] = mapped_column(
        Enum(KeywordJobStatus, name="keyword_job_status"),
        default=KeywordJobStatus.pending,
    )
    target_count: Mapped[int] = mapped_column(Integer)
    collected_count: Mapped[int] = mapped_column(Integer, default=0)
    searched_count: Mapped[int] = mapped_column(Integer, default=0)
    current_keyword: Mapped[str | None] = mapped_column(String(255), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
