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
    # When true, TW quotes use synthetic data (offline demos only).
    tw_use_mock_quotes: bool = Field(default=False)

    # Multi-user OAuth (Google, Microsoft, Apple). When false, API uses default-user only.
    auth_enabled: bool = Field(default=False)
    # Starlette session signing; use a long random value in production when auth_enabled.
    session_secret: str = Field(default="dev-session-secret-change-me")
    # Public origin of the Next.js app (OAuth redirect URIs), e.g. http://localhost:3000
    public_base_url: str = Field(default="http://localhost:3000")

    google_client_id: str = Field(default="")
    google_client_secret: str = Field(default="")

    microsoft_client_id: str = Field(default="")
    microsoft_client_secret: str = Field(default="")
    microsoft_tenant: str = Field(default="consumers")

    apple_client_id: str = Field(default="")
    apple_team_id: str = Field(default="")
    apple_key_id: str = Field(default="")
    apple_private_key: str = Field(default="")
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
