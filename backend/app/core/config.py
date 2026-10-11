from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(value: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in value.split(",") if part.strip())


class Settings(BaseSettings):
    """Application settings loaded from environment (and optional `.env`)."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_prefix="",
    )

    app_name: str = "Stock Ticker API"
    api_prefix: str = "/api"

    # Postgres (required in cluster / local DB mode). Maps env DATABASE_URL.
    database_url: str = Field(default="")
    # Redis for optional quote caching. Maps env REDIS_URL.
    redis_url: str = Field(default="")
    # Soft TTL: cached quotes younger than this are considered fresh.
    quote_cache_ttl_seconds: int = Field(default=60, ge=1)
    # Hard TTL: Redis/memory retention for stale-while-revalidate (default 1h so
    # outages longer than the soft window still serve last-known quotes).
    quote_cache_hard_ttl_seconds: int = Field(default=3600, ge=1)
    # Base cooldown before retrying Redis after a connect/runtime failure.
    quote_redis_retry_base_seconds: float = Field(default=5.0, ge=1.0, le=120.0)
    # Cap for exponential Redis reconnect backoff.
    quote_redis_retry_max_seconds: float = Field(default=60.0, ge=5.0, le=600.0)
    # Random jitter added to Redis reconnect cooldown.
    quote_redis_retry_jitter_seconds: float = Field(default=2.0, ge=0.0, le=30.0)
    # Dedup window for async quote refresh locks.
    quote_refresh_lock_seconds: int = Field(default=30, ge=1, le=300)
    # Background refresher keeps watchlist/portfolio quotes warm in Redis.
    quote_refresher_enabled: bool = Field(default=True)
    # Open-market poll interval between refresher cycles (seconds).
    quote_refresher_open_poll_seconds: int = Field(default=45, ge=15, le=300)
    # Closed-market poll interval (seconds).
    quote_refresher_closed_poll_seconds: int = Field(default=900, ge=60, le=7200)
    # Max concurrent upstream quote fetches in the refresher.
    quote_refresher_max_concurrency: int = Field(default=3, ge=1, le=8)
    # Minimum spacing between upstream requests per provider (seconds).
    quote_refresher_min_interval_seconds: float = Field(default=0.35, ge=0.0, le=5.0)
    # Hard deadline for one refresher cycle (seconds).
    quote_refresher_cycle_timeout_seconds: int = Field(default=40, ge=10, le=300)
    # Random jitter applied once at the start of each refresher cycle (seconds).
    quote_refresher_jitter_seconds: float = Field(default=2.0, ge=0.0, le=30.0)
    # Cap on queued immediate (read-triggered) refreshes; extras are dropped (#271).
    quote_refresher_immediate_queue_max: int = Field(default=64, ge=8, le=500)
    # Separate small pool so immediate work cannot starve the scheduled cycle (#271).
    quote_refresher_immediate_concurrency: int = Field(default=1, ge=1, le=4)
    # When true, UI read paths do not call providers while the sampler is alive.
    quote_refresher_owns_upstream: bool = Field(default=True)
    # Circuit breaker: consecutive provider failures before pausing that market.
    quote_refresher_circuit_failure_threshold: int = Field(default=5, ge=2, le=50)
    # Circuit breaker open duration (seconds) after threshold is hit.
    quote_refresher_circuit_open_seconds: int = Field(default=120, ge=30, le=3600)
    # Wait before retrying a cycle skipped for leadership, not a full poll (#291).
    quote_refresher_leader_retry_seconds: float = Field(default=5.0, ge=1.0, le=60.0)
    # Max ids accepted by GET /api/quotes and /api/stream (#272).
    quote_batch_ids_max: int = Field(default=100, ge=1, le=500)
    # TTL for Redis-cached stock AI narrative (metrics are always recomputed).
    stock_analysis_cache_ttl_seconds: int = Field(default=1_209_600, ge=60, le=3_153_600)
    # TTL for Yahoo / TW pre-ranked rank pages (Redis + in-memory).
    # Higher values mean fewer upstream calls (env RANKINGS_EXTERNAL_CACHE_TTL_SECONDS).
    rankings_external_cache_ttl_seconds: int = Field(default=600, ge=60, le=7200)
    # Background refresh for catalogs, the holiday calendar, and the current
    # daily-price month. Request paths read the stored copies (#264, #269).
    reference_refresh_enabled: bool = Field(default=True)
    reference_refresh_poll_seconds: int = Field(default=900, ge=60, le=7200)
    # Background sampler for portfolio history snapshots.
    portfolio_history_sampler_enabled: bool = Field(default=True)
    portfolio_history_poll_seconds: int = Field(default=300, ge=30)
    # Shared secret header token for internal snapshot-capture endpoint.
    portfolio_history_capture_token: str = Field(default="")
    # Background snapshot capture for free-only rankings persistence.
    rankings_snapshot_enabled: bool = Field(default=True)
    rankings_refresh_seconds: int = Field(default=1800, ge=60)
    # In-app ranking history. Older rows are deleted by the snapshot sampler.
    # The Azure archive keeps the long-term copy (#274).
    ranking_snapshot_retention_days: int = Field(default=400, ge=30, le=3650)
    # Manual POST /rankings/refresh cooldown per market+universe+type.
    rankings_manual_refresh_cooldown_seconds: int = Field(default=15, ge=1, le=120)
    rankings_universe_us: str = Field(
        default="AAPL,MSFT,NVDA,MU,TSLA,AMD,META,AMZN,GOOGL",
    )
    rankings_universe_tw: str = Field(
        default="0050,2330,2317,2454,2303,2881,2882,6488,8299,8271,6215,6643,6562,6451,6462",
    )
    rankings_capture_token: str = Field(default="")
    # Optional Azure SQL (or other) archive DB for public EOD ranking history (#165–#167).
    # Never reuse DATABASE_URL. Empty/unreachable archive must not affect FastAPI or GET rankings.
    rankings_archive_database_url: str = Field(default="")
    rankings_archive_eod_enabled: bool = Field(default=False)
    # Interrupt granularity while the market-aware sampler is idle (#187).
    # Not a fixed Azure SQL wake interval — the thread sleeps until the next settle.
    rankings_archive_eod_poll_seconds: int = Field(default=900, ge=60, le=7200)
    rankings_archive_capture_token: str = Field(default="")
    # Archive snapshot retention (#188). Purge runs opportunistically after an
    # already-awake capture cycle — never as a standalone Azure wake-up.
    # 730 ≈ 2 years. 0 disables retention purge explicitly.
    rankings_archive_retention_days: int = Field(default=730, ge=0, le=3650)
    # Taiwan detail-page market data refresh (institutional + margin-short write path).
    taiwan_market_data_refresh_enabled: bool = Field(default=True)
    taiwan_market_data_refresh_days: int = Field(default=120, ge=1, le=360)
    taiwan_market_data_refresh_recent_days: int = Field(default=7, ge=1, le=30)
    taiwan_market_data_refresh_poll_seconds: int = Field(default=3600, ge=300)
    taiwan_market_data_refresh_hour: int = Field(default=18, ge=0, le=23)
    taiwan_market_data_timezone: str = Field(default="Asia/Taipei")
    taiwan_market_data_capture_token: str = Field(default="")
    # Backend logging controls.
    log_level: str = Field(default="INFO")
    log_format: str = Field(default="console")
    request_id_header: str = Field(default="X-Request-ID")
    # Background evaluator for user alert rules.
    alert_evaluator_enabled: bool = Field(default=True)
    alert_evaluator_poll_seconds: int = Field(default=60, ge=30)

    # When false, FastAPI serves requests only; jobs run in the worker process (#276).
    # Default true keeps single-process local/k3s behaviour.
    run_background_jobs: bool = Field(default=True)
    # Worker health bind address. Loopback unless WORKER_HEALTH_HOST is set.
    # The worker Deployment sets that so kubelet can probe the pod.
    worker_health_host: str = Field(default="127.0.0.1")

    # Multi-user auth. When false, API uses default-user only.
    auth_enabled: bool = Field(default=False)
    # Local username/password auth flow (no external providers).
    local_auth_enabled: bool = Field(default=False)
    local_auth_username: str = Field(default="")
    local_auth_password: str = Field(default="")
    # When true, bootstrap may overwrite an existing user's password. Off by default (#286).
    local_auth_reset_password: bool = Field(default=False)
    # Microsoft Entra External ID (OIDC) as an additional provider (#240).
    entra_external_id_enabled: bool = Field(default=False)
    entra_tenant_id: str = Field(default="")
    entra_client_id: str = Field(default="")
    entra_client_secret: str = Field(default="")
    # e.g. https://<tenant>.ciamlogin.com/<tenant_id>/v2.0
    entra_authority: str = Field(default="")
    entra_redirect_uri: str = Field(default="")
    entra_post_logout_redirect_uri: str = Field(default="")
    # Starlette session signing; use a long random value in production when auth_enabled.
    session_secret: str = Field(default="dev-session-secret-change-me")
    # Secure session cookie. Keep false for plain HTTP (local / LAN Ingress);
    # set true when serving over HTTPS (#267).
    session_cookie_secure: bool = Field(default=False)

    # Per-user resource caps (#273). The quote refresher tracks the union of
    # watchlist, positions, and alert rules. These defaults stay usable for one
    # person and keep a single account from putting a few hundred symbols on
    # the shared upstream budget.
    user_watchlist_max_items: int = Field(default=100, ge=1, le=10_000)
    user_portfolio_max_positions: int = Field(default=80, ge=1, le=10_000)
    user_portfolio_max_assets: int = Field(default=50, ge=1, le=10_000)
    user_alert_max_rules: int = Field(default=80, ge=1, le=10_000)
    # Daily budget for stock-analysis calls that burn the server AI key (#273).
    user_ai_server_key_daily_limit: int = Field(default=50, ge=0, le=10_000)
    # How many reverse proxies append to X-Forwarded-For. The client address is
    # that many hops from the right, so a caller cannot rotate the leftmost value
    # into a fresh login bucket (#273). 0 ignores the header and uses the TCP peer.
    trusted_proxy_hops: int = Field(default=1, ge=0, le=10)
    # Auth route rate limits (per IP). Failed logins share the login bucket.
    auth_login_rate_limit_per_minute: int = Field(default=20, ge=1, le=1_000)
    auth_register_rate_limit_per_hour: int = Field(default=10, ge=1, le=1_000)
    # General authenticated API soft limit (per user). 0 disables.
    api_user_rate_limit_per_minute: int = Field(default=300, ge=0, le=100_000)
    # Comma-separated CORS origins (different-origin UIs). Same-origin `/` + `/api` does not
    # require CORS; defaults cover dev (direct API + separate UI port).
    cors_origins: str = Field(
        default=(
            "http://localhost:3000,"
            "http://127.0.0.1:3000,"
            "http://localhost:5173,"
            "http://127.0.0.1:5173"
        ),
    )
    # Provider-agnostic stock-analysis AI configuration.
    ai_provider: str = Field(default="")
    ai_model: str = Field(default="")
    openai_api_key: str = Field(default="")
    anthropic_api_key: str = Field(default="")
    gemini_api_key: str = Field(default="")
    perplexity_api_key: str = Field(default="")
    ollama_base_url: str = Field(default="http://127.0.0.1:11434")

    @property
    def allowed_origins(self) -> list[str]:
        if not self.cors_origins.strip():
            return []
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def sqlalchemy_database_url(self) -> str:
        """SQLAlchemy/Alembic URL (same as ``database_url``, trimmed)."""
        return self.database_url.strip()

    @property
    def parsed_rankings_universe_us(self) -> tuple[str, ...]:
        return _split_csv(self.rankings_universe_us)

    @property
    def parsed_rankings_universe_tw(self) -> tuple[str, ...]:
        return _split_csv(self.rankings_universe_tw)


settings = Settings()
