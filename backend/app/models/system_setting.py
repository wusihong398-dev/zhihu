from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SystemSetting(Base):
    __tablename__ = "system_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    log_retention_days: Mapped[int] = mapped_column(Integer, default=90)
    publish_interval_min: Mapped[int] = mapped_column(Integer, default=5)
    publish_interval_max: Mapped[int] = mapped_column(Integer, default=12)
    browser_timeout_seconds: Mapped[int] = mapped_column(Integer, default=30)
    default_timezone: Mapped[str] = mapped_column(String(64), default="Asia/Shanghai")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
