import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class QuestionStatus(str, enum.Enum):
    collected = "collected"
    answered = "answered"
    ignored = "ignored"


class ZhihuQuestion(Base):
    __tablename__ = "zhihu_questions"
    __table_args__ = (
        UniqueConstraint("account_id", "zhihu_question_id", name="uq_question_account_remote"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zhihu_accounts.id", ondelete="CASCADE"), index=True
    )
    zhihu_question_id: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(2048))
    keyword_text: Mapped[str] = mapped_column(String(255), default="", index=True)
    excerpt: Mapped[str] = mapped_column(Text, default="")
    answer_count: Mapped[int] = mapped_column(Integer, default=0)
    follower_count: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[QuestionStatus] = mapped_column(
        Enum(QuestionStatus, name="question_status"),
        default=QuestionStatus.collected,
        index=True,
    )
    discovered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
