"""Database engine, session, and table creation."""

import logging

from sqlalchemy import create_engine, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

logger = logging.getLogger(__name__)

engine = create_engine(settings.database_url, pool_pre_ping=True)
admin_engine = create_engine(
    settings.admin_database_url or settings.database_url,
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


def init_extensions() -> None:
    with admin_engine.connect() as conn:
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        conn.commit()


def migrate_schema() -> None:
    """Idempotent column additions for Phase 2 (existing deployments)."""
    dim = settings.gemini_embedding_dimensions
    ddl = [
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS episode_kind VARCHAR(32) NOT NULL DEFAULT 'message'",
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS is_offloaded BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS summary TEXT",
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS offloaded_at TIMESTAMPTZ",
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS metadata_json JSONB",
    ]
    try:
        with admin_engine.begin() as conn:
            _ensure_app_role(conn)
            for stmt in ddl:
                conn.execute(text(stmt))
            row = conn.execute(
                text(
                    """
                    SELECT 1 FROM information_schema.columns
                    WHERE table_schema = 'public' AND table_name = 'episodes'
                      AND column_name = 'embedding'
                    """
                )
            ).fetchone()
            if row is None:
                conn.execute(text(f"ALTER TABLE episodes ADD COLUMN embedding vector({dim})"))
            conn.execute(
                text(
                    "UPDATE episodes SET episode_kind = 'message' "
                    "WHERE episode_kind IS NULL"
                )
            )
            conn.execute(
                text(
                    "UPDATE episodes SET is_offloaded = FALSE WHERE is_offloaded IS NULL"
                )
            )
            # Phase 3
            conn.execute(
                text(
                    "ALTER TABLE users ADD COLUMN IF NOT EXISTS user_message_count "
                    "INTEGER NOT NULL DEFAULT 0"
                )
            )
            conn.execute(
                text(
                    """
                    CREATE TABLE IF NOT EXISTS user_facts (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        fact_key VARCHAR(128) NOT NULL,
                        fact_value TEXT NOT NULL,
                        confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                        source_session_id VARCHAR(36),
                        created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                        CONSTRAINT uq_user_facts_user_key UNIQUE (user_id, fact_key)
                    )
                    """
                )
            )
            conn.execute(
                text(
                    "CREATE INDEX IF NOT EXISTS ix_user_facts_user_id ON user_facts (user_id)"
                )
            )
            conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS idx_episodes_memory_retrieval
                    ON episodes (user_id)
                    WHERE episode_kind = 'memory' AND embedding IS NOT NULL
                    """
                )
            )
            conn.execute(
                text(
                    "ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS pinned "
                    "BOOLEAN NOT NULL DEFAULT FALSE"
                )
            )
            conn.execute(
                text(
                    """
                    CREATE INDEX IF NOT EXISTS ix_user_facts_user_pinned
                    ON user_facts (user_id, pinned)
                    WHERE pinned = TRUE
                    """
                )
            )
            _migrate_fact_v2_and_caches(conn, dim)
            _migrate_user_roles_and_admin_seed(conn)
            _migrate_token_quotas(conn)
            _migrate_phase5_selective_context(conn)
            from app.services.memory_keyword_search import (
                ensure_active_turns_fts_index,
                ensure_memory_fts_index,
            )

            ensure_memory_fts_index(conn)
            ensure_active_turns_fts_index(conn)
            _migrate_ann_index(conn)
            _migrate_rls(conn)
            _migrate_cache_rls(conn)
    except Exception:
        logger.exception("Schema migration failed")
        raise


def _migrate_user_roles_and_admin_seed(conn) -> None:
    conn.execute(
        text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS role VARCHAR(16) "
            "NOT NULL DEFAULT 'user'"
        )
    )
    conn.execute(
        text(
            """
            ALTER TABLE users ADD COLUMN IF NOT EXISTS expert_preview_enabled
            BOOLEAN NOT NULL DEFAULT FALSE
            """
        )
    )
    from app.core.config import settings
    from app.core.security import hash_password

    email = (settings.admin_email or "").strip().lower()
    password = settings.admin_password or ""
    if not email or not password:
        return
    row = conn.execute(
        text("SELECT id FROM users WHERE email = :email"),
        {"email": email},
    ).fetchone()
    hashed = hash_password(password)
    if row is None:
        conn.execute(
            text(
                """
                INSERT INTO users (
                    email, hashed_password, role, expert_preview_enabled, token_unlimited
                )
                VALUES (:email, :hashed, 'admin', TRUE, TRUE)
                """
            ),
            {"email": email, "hashed": hashed},
        )
    else:
        conn.execute(
            text(
                """
                UPDATE users SET role = 'admin', expert_preview_enabled = TRUE,
                token_unlimited = TRUE, hashed_password = :hashed
                WHERE email = :email
                """
            ),
            {"email": email, "hashed": hashed},
        )


def _migrate_token_quotas(conn) -> None:
    """Daily token quotas and usage ledger."""
    ddl = [
        """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS token_unlimited
        BOOLEAN NOT NULL DEFAULT FALSE
        """,
        """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_primary_daily
        BIGINT NOT NULL DEFAULT 1000000
        """,
        """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS quota_fallback_daily
        BIGINT NOT NULL DEFAULT 1000000
        """,
        """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS tokens_primary_today
        BIGINT NOT NULL DEFAULT 0
        """,
        """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS tokens_fallback_today
        BIGINT NOT NULL DEFAULT 0
        """,
        """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS tokens_primary_lifetime
        BIGINT NOT NULL DEFAULT 0
        """,
        """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS tokens_fallback_lifetime
        BIGINT NOT NULL DEFAULT 0
        """,
        """
        ALTER TABLE users ADD COLUMN IF NOT EXISTS usage_period_date DATE
        """,
        """
        CREATE TABLE IF NOT EXISTS token_usage_events (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            tier VARCHAR(16) NOT NULL,
            model VARCHAR(64) NOT NULL,
            prompt_tokens BIGINT NOT NULL DEFAULT 0,
            completion_tokens BIGINT NOT NULL DEFAULT 0,
            total_tokens BIGINT NOT NULL DEFAULT 0,
            session_id VARCHAR(36),
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """,
        """
        CREATE INDEX IF NOT EXISTS ix_token_usage_events_user_created
        ON token_usage_events (user_id, created_at DESC)
        """,
    ]
    for stmt in ddl:
        conn.execute(text(stmt))

    conn.execute(text("ALTER TABLE token_usage_events ENABLE ROW LEVEL SECURITY"))
    conn.execute(text("ALTER TABLE token_usage_events FORCE ROW LEVEL SECURITY"))


def _migrate_phase5_selective_context(conn) -> None:
    """Per-turn embeddings and selective active-context retrieval."""
    ddl = [
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS token_count INTEGER",
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS embed_status VARCHAR(16) NOT NULL DEFAULT 'pending'",
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS parent_episode_id INTEGER REFERENCES episodes(id) ON DELETE CASCADE",
        "ALTER TABLE episodes ADD COLUMN IF NOT EXISTS chunk_index INTEGER",
        """
        CREATE INDEX IF NOT EXISTS idx_episodes_active_tail
        ON episodes (user_id, session_id, episode_kind, is_offloaded)
        WHERE episode_kind IN ('message', 'message_chunk') AND is_offloaded = FALSE
        """,
    ]
    for stmt in ddl:
        conn.execute(text(stmt))
    conn.execute(
        text(
            "UPDATE episodes SET embed_status = 'ready' "
            "WHERE episode_kind = 'memory' AND embedding IS NOT NULL "
            "AND embed_status = 'pending'"
        )
    )
    conn.execute(
        text(
            "UPDATE episodes SET embed_status = 'skipped' "
            "WHERE episode_kind = 'message' AND is_offloaded = TRUE "
            "AND embed_status = 'pending'"
        )
    )


def _migrate_fact_v2_and_caches(conn, dim: int) -> None:
    """Fact extraction v2 lifecycle, jobs, chat_sessions, Postgres caches."""
    conn.execute(
        text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_fact_extraction_at TIMESTAMPTZ"
        )
    )
    conn.execute(
        text(
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS fact_extraction_last_error TEXT"
        )
    )
    conn.execute(
        text(
            """
            ALTER TABLE users ADD COLUMN IF NOT EXISTS
            fact_extraction_consecutive_failures INTEGER NOT NULL DEFAULT 0
            """
        )
    )
    for col, typedef in (
        ("status", "VARCHAR(16) NOT NULL DEFAULT 'active'"),
        ("canonical_key", "VARCHAR(128)"),
        ("deprecated_at", "TIMESTAMPTZ"),
        ("deleted_at", "TIMESTAMPTZ"),
    ):
        conn.execute(
            text(f"ALTER TABLE user_facts ADD COLUMN IF NOT EXISTS {col} {typedef}")
        )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS fact_extraction_runs (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                session_id VARCHAR(36),
                scope VARCHAR(16) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 5,
                next_retry_at TIMESTAMPTZ,
                last_error TEXT,
                payload_json JSONB,
                result_json JSONB,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_fact_extraction_runs_pending
            ON fact_extraction_runs (status, next_retry_at)
            WHERE status IN ('pending', 'failed')
            """
        )
    )
    conn.execute(
        text(
            """
            ALTER TABLE user_facts
            ADD COLUMN IF NOT EXISTS last_extraction_run_id INTEGER
            REFERENCES fact_extraction_runs(id) ON DELETE SET NULL
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_user_facts_user_status
            ON user_facts (user_id, status)
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_user_facts_user_canonical
            ON user_facts (user_id, canonical_key)
            WHERE status = 'active' AND canonical_key IS NOT NULL
            """
        )
    )
    conn.execute(
        text("UPDATE user_facts SET status = 'active' WHERE status IS NULL")
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS chat_sessions (
                id SERIAL PRIMARY KEY,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                session_id VARCHAR(36) NOT NULL,
                user_message_count INTEGER NOT NULL DEFAULT 0,
                last_activity_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                CONSTRAINT uq_chat_sessions_user_session UNIQUE (user_id, session_id)
            )
            """
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_chat_sessions_user ON chat_sessions (user_id)"
        )
    )
    conn.execute(
        text(
            "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS title VARCHAR(256) "
            "NOT NULL DEFAULT 'New conversation'"
        )
    )
    conn.execute(
        text(
            "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS preview_text VARCHAR(512)"
        )
    )
    conn.execute(
        text(
            "ALTER TABLE chat_sessions ADD COLUMN IF NOT EXISTS title_generated_at TIMESTAMPTZ"
        )
    )
    conn.execute(
        text(
            """
            INSERT INTO chat_sessions (user_id, session_id, user_message_count, title, preview_text, last_activity_at)
            SELECT
                e.user_id,
                e.session_id,
                COUNT(*) FILTER (
                    WHERE e.role = 'user'
                    AND (e.episode_kind = 'message' OR e.episode_kind IS NULL)
                ),
                'New conversation',
                LEFT(
                    (
                        SELECT e2.content
                        FROM episodes e2
                        WHERE e2.user_id = e.user_id
                          AND e2.session_id = e.session_id
                          AND (e2.episode_kind = 'message' OR e2.episode_kind IS NULL)
                          AND e2.is_offloaded IS NOT TRUE
                        ORDER BY e2.created_at DESC
                        LIMIT 1
                    ),
                    120
                ),
                MAX(e.created_at)
            FROM episodes e
            WHERE NOT EXISTS (
                SELECT 1 FROM chat_sessions cs
                WHERE cs.user_id = e.user_id AND cs.session_id = e.session_id
            )
            GROUP BY e.user_id, e.session_id
            """
        )
    )
    conn.execute(
        text(
            f"""
            CREATE TABLE IF NOT EXISTS embedding_cache (
                cache_key VARCHAR(128) PRIMARY KEY,
                embedding vector({dim}) NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                hit_count INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    conn.execute(
        text(
            "CREATE INDEX IF NOT EXISTS ix_embedding_cache_expires ON embedding_cache (expires_at)"
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS retrieval_bundle_cache (
                cache_key VARCHAR(512) PRIMARY KEY,
                payload_json JSONB NOT NULL,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_retrieval_bundle_cache_expires
            ON retrieval_bundle_cache (expires_at)
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS prompt_assembly_cache (
                cache_key VARCHAR(512) PRIMARY KEY,
                messages_json JSONB NOT NULL,
                token_count INTEGER NOT NULL DEFAULT 0,
                expires_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE INDEX IF NOT EXISTS ix_prompt_assembly_cache_expires
            ON prompt_assembly_cache (expires_at)
            """
        )
    )


def _migrate_cache_rls(conn) -> None:
    """RLS for new tenant-scoped tables (idempotent)."""
    for table in (
        "chat_sessions",
        "fact_extraction_runs",
        "embedding_cache",
        "retrieval_bundle_cache",
        "prompt_assembly_cache",
    ):
        conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))

    tenant_policies = [
        (
            "chat_sessions",
            "chat_sessions_tenant",
            """
            CREATE POLICY chat_sessions_tenant ON chat_sessions
            USING (rls_bypass() OR user_id = current_user_id())
            WITH CHECK (rls_bypass() OR user_id = current_user_id())
            """,
        ),
        (
            "fact_extraction_runs",
            "fact_extraction_runs_tenant",
            """
            CREATE POLICY fact_extraction_runs_tenant ON fact_extraction_runs
            USING (rls_bypass() OR user_id = current_user_id())
            WITH CHECK (rls_bypass() OR user_id = current_user_id())
            """,
        ),
        (
            "token_usage_events",
            "token_usage_events_tenant",
            """
            CREATE POLICY token_usage_events_tenant ON token_usage_events
            USING (rls_bypass() OR user_id = current_user_id())
            WITH CHECK (rls_bypass() OR user_id = current_user_id())
            """,
        ),
    ]
    for _table, policy_name, create_sql in tenant_policies:
        exists = conn.execute(
            text(
                """
                SELECT 1 FROM pg_policies
                WHERE schemaname = 'public' AND policyname = :name
                """
            ),
            {"name": policy_name},
        ).fetchone()
        if exists is None:
            conn.execute(text(create_sql))

    # Content-addressed / session-scoped caches: bypass-only (no user_id column)
    for table, policy_name in (
        ("embedding_cache", "embedding_cache_bypass"),
        ("retrieval_bundle_cache", "retrieval_bundle_cache_bypass"),
        ("prompt_assembly_cache", "prompt_assembly_cache_bypass"),
    ):
        exists = conn.execute(
            text(
                """
                SELECT 1 FROM pg_policies
                WHERE schemaname = 'public' AND policyname = :name
                """
            ),
            {"name": policy_name},
        ).fetchone()
        if exists is None:
            conn.execute(
                text(
                    f"""
                    CREATE POLICY {policy_name} ON {table}
                    USING (rls_bypass())
                    WITH CHECK (rls_bypass())
                    """
                )
            )


def _migrate_ann_index(conn) -> None:
    """pgvector ANN index for memory episode embeddings (idempotent)."""
    mode = (settings.memory_ann_index or "hnsw").lower()
    if mode == "none":
        return
    hnsw_exists = conn.execute(
        text(
            """
            SELECT 1 FROM pg_indexes
            WHERE schemaname = 'public'
              AND indexname = 'idx_episodes_memory_embedding_hnsw'
            """
        )
    ).fetchone()
    ivf_exists = conn.execute(
        text(
            """
            SELECT 1 FROM pg_indexes
            WHERE schemaname = 'public'
              AND indexname = 'idx_episodes_memory_embedding_ivfflat'
            """
        )
    ).fetchone()
    if mode == "ivfflat":
        if ivf_exists is not None:
            return
        lists = max(1, settings.ivfflat_lists)
        conn.execute(
            text(
                f"""
                CREATE INDEX IF NOT EXISTS idx_episodes_memory_embedding_ivfflat
                ON episodes USING ivfflat (embedding vector_cosine_ops)
                WITH (lists = {lists})
                WHERE episode_kind = 'memory' AND embedding IS NOT NULL
                """
            )
        )
    else:
        if hnsw_exists is not None:
            return
        m = settings.hnsw_m
        ef = settings.hnsw_ef_construction
        conn.execute(
            text(
                f"""
                CREATE INDEX IF NOT EXISTS idx_episodes_memory_embedding_hnsw
                ON episodes USING hnsw (embedding vector_cosine_ops)
                WITH (m = {m}, ef_construction = {ef})
                WHERE episode_kind = 'memory' AND embedding IS NOT NULL
                """
            )
        )


def _admin_database_name() -> str:
    """Database name from ADMIN_DATABASE_URL (e.g. maestro locally, railway on Railway)."""
    url = make_url(settings.admin_database_url or settings.database_url)
    name = url.database
    if not name:
        msg = "ADMIN_DATABASE_URL must include a database name (path after host/port)."
        raise ValueError(msg)
    if not name.replace("_", "").isalnum():
        raise ValueError(f"Unsafe database name in connection URL: {name!r}")
    return name


# Serialize role/grant bootstrap — concurrent Railway replicas otherwise race on
# GRANT CONNECT (pg_database "tuple concurrently updated").
_BOOTSTRAP_ADVISORY_LOCK_KEY = 87234921


def _ensure_app_role(conn) -> None:
    """Create non-superuser app role so FORCE RLS applies to API connections."""
    conn.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": _BOOTSTRAP_ADVISORY_LOCK_KEY},
    )
    exists = conn.execute(
        text("SELECT 1 FROM pg_roles WHERE rolname = 'contextual_app'")
    ).fetchone()

    # CONNECT updates pg_database and races when several Railway containers start together.
    # Run it only once, inside the role-creation block; never on redeploy.
    if not exists:
        pwd = settings.app_db_password.replace("'", "''")
        conn.execute(
            text(
                f"""
                DO $$
                BEGIN
                  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'contextual_app') THEN
                    CREATE ROLE contextual_app WITH LOGIN PASSWORD '{pwd}'
                      NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOBYPASSRLS;
                    EXECUTE format(
                      'GRANT CONNECT ON DATABASE %I TO contextual_app',
                      current_database()
                    );
                  END IF;
                END
                $$;
                """
            )
        )

    schema_grants = (
        "GRANT USAGE, CREATE ON SCHEMA public TO contextual_app",
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO contextual_app",
        "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO contextual_app",
        (
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO contextual_app"
        ),
        (
            "ALTER DEFAULT PRIVILEGES IN SCHEMA public "
            "GRANT USAGE, SELECT ON SEQUENCES TO contextual_app"
        ),
    )
    for stmt in schema_grants:
        conn.execute(text(stmt))


def _migrate_rls(conn) -> None:
    """Phase 4: row-level security (idempotent)."""
    conn.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION current_user_id() RETURNS INTEGER AS $$
            BEGIN
                RETURN NULLIF(current_setting('app.current_user_id', true), '')::INTEGER;
            EXCEPTION WHEN OTHERS THEN
                RETURN NULL;
            END;
            $$ LANGUAGE plpgsql STABLE;
            """
        )
    )
    conn.execute(
        text(
            """
            CREATE OR REPLACE FUNCTION rls_bypass() RETURNS BOOLEAN AS $$
            BEGIN
                RETURN current_setting('app.bypass_rls', true) = 'on';
            EXCEPTION WHEN OTHERS THEN
                RETURN FALSE;
            END;
            $$ LANGUAGE plpgsql STABLE;
            """
        )
    )
    for table in ("users", "user_facts", "episodes"):
        conn.execute(text(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY"))
        conn.execute(text(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY"))

    policies = [
        (
            "users",
            "users_tenant",
            """
            CREATE POLICY users_tenant ON users
            USING (rls_bypass() OR id = current_user_id())
            WITH CHECK (rls_bypass() OR id = current_user_id())
            """,
        ),
        (
            "user_facts",
            "user_facts_tenant",
            """
            CREATE POLICY user_facts_tenant ON user_facts
            USING (rls_bypass() OR user_id = current_user_id())
            WITH CHECK (rls_bypass() OR user_id = current_user_id())
            """,
        ),
        (
            "episodes",
            "episodes_tenant",
            """
            CREATE POLICY episodes_tenant ON episodes
            USING (rls_bypass() OR user_id = current_user_id())
            WITH CHECK (rls_bypass() OR user_id = current_user_id())
            """,
        ),
    ]
    for _table, policy_name, create_sql in policies:
        exists = conn.execute(
            text(
                """
                SELECT 1 FROM pg_policies
                WHERE schemaname = 'public' AND policyname = :name
                """
            ),
            {"name": policy_name},
        ).fetchone()
        if exists is None:
            conn.execute(text(create_sql))


def create_db_and_tables() -> None:
    # Import models so metadata is populated
    from app.models import chat_session  # noqa: F401
    from app.models import embedding_cache  # noqa: F401
    from app.models import episode  # noqa: F401
    from app.models import fact_extraction_run  # noqa: F401
    from app.models import prompt_assembly_cache  # noqa: F401
    from app.models import retrieval_bundle_cache  # noqa: F401
    from app.models import user  # noqa: F401
    from app.models import user_fact  # noqa: F401

    Base.metadata.create_all(bind=admin_engine)


def get_db():
    """Unscoped session (auth routes); call set_bypass_rls or set_tenant_context as needed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def open_tenant_session(user_id: int) -> Session:
    """Return a session with RLS tenant context (caller must close)."""
    from app.services.rls import set_tenant_context

    db = SessionLocal()
    set_tenant_context(db, user_id)
    return db
