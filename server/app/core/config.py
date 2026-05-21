"""Typed configuration from environment."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = (
        "postgresql+psycopg2://contextual_app:maestro_dev@127.0.0.1:5433/maestro"
    )
    # Superuser URL for migrations / role bootstrap only (docker default: maestro)
    admin_database_url: str = (
        "postgresql+psycopg2://maestro:maestro_dev@127.0.0.1:5433/maestro"
    )
    app_db_password: str = "maestro_dev"
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60 * 24 * 7  # 7 days
    cors_origins: str = "http://localhost:3000"
    environment: str = "development"
    message_max_length: int = 8000
    chat_rate_limit: str = "30/minute"
    auth_rate_limit: str = "10/minute"

    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-chat"
    deepseek_model_tier_primary: str = "deepseek-v4-flash"
    deepseek_model_tier_fallback: str = "deepseek-chat"
    default_quota_primary_daily: int = 1_000_000
    default_quota_fallback_daily: int = 1_000_000
    # Reject chat when estimated turn cost exceeds remaining daily quota minus this buffer.
    quota_preflight_min_remaining: int = 256
    deepseek_summarize_model: str = "deepseek-chat"

    # Context engineering (Phase 2)
    context_threshold_tokens: int = 4000
    min_recent_messages_to_keep: int = 8
    compression_embed_max_retries: int = 3
    compression_embed_retry_base_seconds: float = 0.25
    chat_system_prompt: str = (
        "You are Contextual Maestro, a helpful assistant. Be concise and accurate."
    )
    token_encoding_model: str = "gpt-4"  # tiktoken encoding hint; approximate for DeepSeek

    gemini_api_key: str = ""
    gemini_embedding_model: str = "gemini-embedding-001"
    # MRL sizes supported: 768, 1536, 3072 — must match DB vector column and API request
    gemini_embedding_dimensions: int = 768

    # Phase 3: adaptive retrieval and persistent memory
    retrieval_top_k: int = 5
    retrieval_final_k: int = 2
    retrieval_min_score: float = 0.35
    retrieval_keyword_top_k: int = 5
    retrieval_embed_max_retries: int = 3
    retrieval_embed_retry_base_seconds: float = 0.25
    fact_injection_max: int = 8
    fact_injection_min_similarity: float = 0.25
    fact_injection_embed_cap: int = 20
    in_session_memory_final_k: int = 1
    memory_ann_index: str = "hnsw"  # hnsw | ivfflat | none
    hnsw_m: int = 16
    hnsw_ef_construction: int = 64
    ivfflat_lists: int = 100
    rerank_degraded_on_failure: bool = True
    fact_extraction_every_n_messages: int = 4  # legacy alias for global
    fact_extraction_session_every_n: int = 4
    fact_extraction_global_every_n: int = 8
    fact_extraction_lookback_messages: int = 12
    fact_extraction_memory_sessions_cap: int = 5
    fact_max_per_user: int = 50
    fact_dedup_similarity_threshold: float = 0.92
    fact_delete_min_confidence: float = 0.7
    fact_extraction_max_attempts: int = 5
    fact_extraction_max_consecutive_failures: int = 10
    fact_extraction_retry_base_seconds: float = 30.0
    deepseek_rerank_model: str = "deepseek-chat"

    # Memory gate agent (two-stage selective extraction)
    deepseek_memory_model: str = "deepseek-v4-flash"
    memory_gate_enabled: bool = True
    memory_gate_min_confidence: float = 0.75
    memory_gate_max_candidates: int = 10
    memory_extraction_skip_if_no_signals: bool = True

    # Postgres caches
    embedding_cache_ttl_seconds: int = 7 * 24 * 3600
    retrieval_bundle_cache_ttl_seconds: int = 60
    prompt_assembly_cache_ttl_seconds: int = 45
    prompt_assembly_cache_enabled: bool = False

    # Phase 5: selective active-turn retrieval
    selective_context_enabled: bool = True
    prompt_token_budget: int = 8000
    active_retrieval_floor_turns: int = 6
    active_retrieval_top_k: int = 8
    active_turn_chunk_threshold_tokens: int = 1000
    active_turn_chunk_size_tokens: int = 500
    active_turn_chunk_overlap_tokens: int = 50
    scoring_weight_vector: float = 0.55
    scoring_weight_bm25: float = 0.20
    scoring_weight_recency: float = 0.15
    scoring_weight_entity: float = 0.10
    recency_half_life_turns: int = 20
    mmr_lambda: float = 0.7

    # Seeded admin (same login URL as users; expert preview enabled)
    admin_email: str = ""
    admin_password: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
