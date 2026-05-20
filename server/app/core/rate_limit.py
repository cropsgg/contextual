"""Rate limiting helpers (Phase 4)."""

from fastapi import Request

from app.core.config import settings


def rate_limit_key(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return f"user:{auth[7:36]}"
    if request.client:
        return f"ip:{request.client.host}"
    return "ip:unknown"


def chat_rate_limit() -> str:
    return settings.chat_rate_limit


def auth_rate_limit() -> str:
    return settings.auth_rate_limit
