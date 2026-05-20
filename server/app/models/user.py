from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.services.database import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    hashed_password: Mapped[str] = mapped_column(String(255))
    role: Mapped[str] = mapped_column(String(16), default="user", server_default="user")
    expert_preview_enabled: Mapped[bool] = mapped_column(
        default=False,
        server_default="false",
    )
    token_unlimited: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default="false",
    )
    quota_primary_daily: Mapped[int] = mapped_column(
        BigInteger, default=1_000_000, server_default="1000000"
    )
    quota_fallback_daily: Mapped[int] = mapped_column(
        BigInteger, default=1_000_000, server_default="1000000"
    )
    tokens_primary_today: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    tokens_fallback_today: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    tokens_primary_lifetime: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    tokens_fallback_lifetime: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default="0"
    )
    usage_period_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    user_message_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_fact_extraction_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    fact_extraction_last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    fact_extraction_consecutive_failures: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
