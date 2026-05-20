from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.services.database import Base


class PromptAssemblyCache(Base):
    __tablename__ = "prompt_assembly_cache"

    cache_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    messages_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
