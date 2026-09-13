import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class KeywordSource(str, enum.Enum):
    baidu = "baidu"
    google = "google"
    import_file = "import_file"
    manual = "manual"


class AccountKeyword(Base):
    __tablename__ = "account_keywords"
    __table_args__ = (
        UniqueConstraint("account_id", "normalized_keyword", name="uq_account_keyword"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    account_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("zhihu_accounts.id", ondelete="CASCADE"), index=True
    )
    keyword: Mapped[str] = mapped_column(String(255))
    normalized_keyword: Mapped[str] = mapped_column(String(255))
    source: Mapped[KeywordSource] = mapped_column(
        Enum(KeywordSource, name="keyword_source")
    )
    seed_keyword: Mapped[str] = mapped_column(String(255))
    parent_keyword: Mapped[str | None] = mapped_column(String(255), nullable=True)
    depth: Mapped[int] = mapped_column(Integer, default=0)
    is_recycled: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    used_count: Mapped[int] = mapped_column(Integer, default=0)
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    recycled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
