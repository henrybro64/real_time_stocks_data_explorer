from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    # TTL for cached quote payloads when Redis is enabled.
    quote_cache_ttl_seconds: int = Field(default=60, ge=1)
    # TTL for Redis-cached stock AI narrative (metrics are always recomputed).
    stock_analysis_cache_ttl_seconds: int = Field(default=1_209_600, ge=60, le=3_153_600)
    # TTL for Yahoo / TW pre-ranked rank pages (Redis + in-memory).
    # Higher values mean fewer upstream calls (env RANKINGS_EXTERNAL_CACHE_TTL_SECONDS).
    rankings_external_cache_ttl_seconds: int = Field(default=600, ge=60, le=7200)
    # Background sampler for portfolio history snapshots.
    portfolio_history_sampler_enabled: bool = Field(default=True)
    portfolio_history_poll_seconds: int = Field(default=300, ge=30)
    # Shared secret header token for internal snapshot-capture endpoint.
    portfolio_history_capture_token: str = Field(default="")

    # Multi-user auth. When false, API uses default-user only.
    auth_enabled: bool = Field(default=False)
    # Local username/password auth flow (no external providers).
    local_auth_enabled: bool = Field(default=False)
    local_auth_username: str = Field(default="")
    local_auth_password: str = Field(default="")
    # Starlette session signing; use a long random value in production when auth_enabled.
    session_secret: str = Field(default="dev-session-secret-change-me")
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


settings = Settings()
