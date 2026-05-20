from pydantic import BaseModel, EmailStr, Field


class UserRegister(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"


class QuotaStatusOut(BaseModel):
    token_unlimited: bool = False
    primary_limit: int
    fallback_limit: int
    primary_used_today: int
    fallback_used_today: int
    primary_remaining: int | None = None
    fallback_remaining: int | None = None
    primary_lifetime: int
    fallback_lifetime: int
    tier_in_use: str
    primary_model: str
    fallback_model: str
    resets_at: str
    usage_period_date: str | None = None


class UserOut(BaseModel):
    id: int
    email: str
    role: str = "user"
    expert_preview_enabled: bool = False
    quota: QuotaStatusOut | None = None

    model_config = {"from_attributes": True}


class AdminUserOut(BaseModel):
    id: int
    email: str
    role: str
    created_at: str
    expert_preview_enabled: bool
    token_unlimited: bool
    quota_primary_daily: int
    quota_fallback_daily: int
    tokens_primary_today: int
    tokens_fallback_today: int
    tokens_primary_lifetime: int
    tokens_fallback_lifetime: int
    usage_period_date: str | None = None


class AdminUserUpdate(BaseModel):
    quota_primary_daily: int | None = Field(default=None, ge=0)
    quota_fallback_daily: int | None = Field(default=None, ge=0)
    token_unlimited: bool | None = None
    expert_preview_enabled: bool | None = None


class PlatformStatsOut(BaseModel):
    total_users: int
    tokens_primary_today: int
    tokens_fallback_today: int
    tokens_primary_lifetime: int
    tokens_fallback_lifetime: int
    tokens_total_today: int
    tokens_total_lifetime: int


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str = Field(min_length=1, max_length=36)


class SessionSummary(BaseModel):
    session_id: str
    last_message_at: str
    title: str = "New conversation"
    preview_text: str | None = None


class SessionCreateOut(BaseModel):
    session_id: str
    title: str
    last_message_at: str


class SessionUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=256)


class SessionDeleteOut(BaseModel):
    session_id: str
    episodes_deleted: int


class MessageOut(BaseModel):
    id: int
    role: str
    content: str
    created_at: str

    model_config = {"from_attributes": True}


class ContextStatusOut(BaseModel):
    active_token_count: int
    context_threshold: int
    offloaded_message_count: int
    memory_chunk_count: int
    last_summary: str | None
    last_compressed_at: str | None
    latest_memory_episode_id: int | None = None
    offloaded_summary_label: str | None = None
    compression_in_progress: bool
    compression_attempted: bool = False
    compression_succeeded: bool = True
    failure_reason: str | None = None
    memory_paused: bool = False
    retrieval_mode: str | None = None
    cross_session_memory_available: bool | None = None
    retrieval_degraded: bool = False
    retrieval_failure_reason: str | None = None
    last_fact_extraction_at: str | None = None
    fact_extraction_last_error: str | None = None
    fact_extraction_consecutive_failures: int = 0
    embedding_cache_hit_rate: float | None = None
    retrieval_bundle_cache_hit: bool | None = None


class OffloadedMessageOut(BaseModel):
    id: int
    role: str
    snippet: str
    created_at: str
    offloaded_at: str | None

    model_config = {"from_attributes": True}


class OffloadedMessageListOut(BaseModel):
    items: list[OffloadedMessageOut]
    total: int
    page: int
    limit: int


class SessionCompressionSummaryOut(BaseModel):
    memory_episode_id: int
    summary: str
    created_at: str
    offloaded_message_count: int


class UserFactOut(BaseModel):
    id: int
    fact_key: str
    fact_value: str
    confidence: float
    pinned: bool = False
    status: str = "active"
    source_session_id: str | None
    created_at: str
    updated_at: str

    model_config = {"from_attributes": True}


class UserFactCreate(BaseModel):
    fact_key: str = Field(min_length=1, max_length=128)
    fact_value: str = Field(min_length=1, max_length=4000)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class UserFactUpdate(BaseModel):
    fact_key: str | None = Field(default=None, min_length=1, max_length=128)
    fact_value: str | None = Field(default=None, min_length=1, max_length=4000)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    pinned: bool | None = None


class AttributionFactItem(BaseModel):
    fact_key: str
    fact_value: str
    selection_reason: str | None = None
    pinned: bool = False


class AttributionMemoryItem(BaseModel):
    episode_id: int
    session_id: str
    snippet: str
    score: float | None = None
    scope: str = "cross_session"


class AttributionRetrievalOut(BaseModel):
    mode: str = "full"
    cross_session_memory_available: bool = True
    reranked: bool = False
    rerank_fallback: bool = False
    keyword_fallback_used: bool = False
    failure_reason: str | None = None


class AttributionOut(BaseModel):
    facts: list[AttributionFactItem]
    memories: list[AttributionMemoryItem]
    retrieval: AttributionRetrievalOut | None = None


class MemoryEpisodeOut(BaseModel):
    id: int
    session_id: str
    summary: str
    created_at: str


class MemoryEpisodeListOut(BaseModel):
    items: list[MemoryEpisodeOut]
    total: int
    page: int
    limit: int


class ClearMemoryRequest(BaseModel):
    confirm: str = Field(min_length=1, max_length=64)


class ClearMemoryResponse(BaseModel):
    facts_deleted: int
    episodes_deleted: int


class ChatPreviewFactItem(BaseModel):
    fact_key: str
    fact_value: str


class ChatPreviewMemoryItem(BaseModel):
    episode_id: int
    session_id: str
    snippet: str


class ChatPreviewEnhancedOut(BaseModel):
    facts: list[ChatPreviewFactItem]
    memories: list[ChatPreviewMemoryItem]


class ChatPreviewMessage(BaseModel):
    role: str
    content: str


class ChatPreviewOut(BaseModel):
    messages: list[ChatPreviewMessage]
    token_count: int
    model: str
    enhanced: ChatPreviewEnhancedOut
    would_compress: bool = False
    projected_offload_count: int = 0
