"""Application entry and router wiring."""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from sqlalchemy import text
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded

from app.api import admin, auth, chat, chat_preview, history, memory
from app.core.config import settings
from app.core.limiter import limiter
from app.middleware.security_headers import SecurityHeadersMiddleware
from app.services.database import (
    create_db_and_tables,
    engine,
    init_extensions,
    migrate_schema,
)

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    if settings.jwt_secret == "change-me-in-production":
        logger.warning(
            "JWT_SECRET is still the default; set a strong unique secret before production."
        )
    init_extensions()
    create_db_and_tables()
    migrate_schema()
    yield


app = FastAPI(
    title="Contextual Maestro API",
    version="0.1.0",
    lifespan=lifespan,
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(admin.router, prefix="/api/admin", tags=["admin"])
app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(chat.router, prefix="/api/chat", tags=["chat"])
app.include_router(chat_preview.router, prefix="/api/chat", tags=["chat"])
app.include_router(history.router, prefix="/api/history", tags=["history"])
app.include_router(memory.router, prefix="/api/memory", tags=["memory"])


@app.get("/health")
def health() -> dict:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:
        logger.warning("Health check database probe failed: %s", exc)
        raise HTTPException(
            status_code=503,
            detail={"status": "degraded", "database": "unavailable"},
        ) from exc
    return {"status": "ok", "database": "connected"}
