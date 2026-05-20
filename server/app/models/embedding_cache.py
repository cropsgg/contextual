from datetime import datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.services.database import Base

_EMBED_DIM = settings.gemini_embedding_dimensions


class EmbeddingCache(Base):
    __tablename__ = "embedding_cache"

    cache_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    embedding: Mapped[list[float]] = mapped_column(Vector(_EMBED_DIM))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    hit_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
