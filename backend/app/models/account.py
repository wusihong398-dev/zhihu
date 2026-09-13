import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class AccountStatus(str, enum.Enum):
    pending_login = "pending_login"
    online = "online"
    offline = "offline"
    verification_required = "verification_required"
    restricted = "restricted"
    paused = "paused"


class ZhihuAccount(Base):
    __tablename__ = "zhihu_accounts"
    __table_args__ = (UniqueConstraint("profile_key"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    display_name: Mapped[str] = mapped_column(String(100))
    remark: Mapped[str] = mapped_column(String(255), default="")
    profile_key: Mapped[str] = mapped_column(
        String(36), default=lambda: str(uuid.uuid4())
    )
    status: Mapped[AccountStatus] = mapped_column(
        Enum(AccountStatus, name="zhihu_account_status"),
        default=AccountStatus.pending_login,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    daily_article_limit: Mapped[int] = mapped_column(Integer, default=0)
    daily_answer_limit: Mapped[int] = mapped_column(Integer, default=0)
    recycle_keywords_after_use: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_restore_keywords: Mapped[bool] = mapped_column(Boolean, default=False)
    keyword_restore_threshold: Mapped[int] = mapped_column(Integer, default=20)
    timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
