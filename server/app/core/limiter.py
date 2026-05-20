"""Shared SlowAPI limiter instance."""

from slowapi import Limiter

from app.core.rate_limit import rate_limit_key

limiter = Limiter(key_func=rate_limit_key)
