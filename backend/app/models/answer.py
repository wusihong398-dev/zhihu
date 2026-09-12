import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AnswerStatus(str, enum.Enum):
    draft = "draft"
    ready = "ready"
    published = "published"
    failed = "failed"


class ZhihuAnswer(Base):
    __tablename__ = "zhihu_answers"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zhihu_accounts.id", ondelete="CASCADE"), index=True
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zhihu_questions.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("promoted_products.id", ondelete="SET NULL"), nullable=True
    )
    question_title: Mapped[str] = mapped_column(String(500))
    question_url: Mapped[str] = mapped_column(String(2048))
    keyword_text: Mapped[str] = mapped_column(String(255), default="")
    product_name: Mapped[str] = mapped_column(String(120), default="")
    content: Mapped[str] = mapped_column(Text, default="")
    content_length: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[AnswerStatus] = mapped_column(
        Enum(AnswerStatus, name="answer_status"), default=AnswerStatus.draft, index=True
    )
    provider: Mapped[str] = mapped_column(String(32), default="")
    model: Mapped[str] = mapped_column(String(128), default="")
    prompt: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    publish_attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
