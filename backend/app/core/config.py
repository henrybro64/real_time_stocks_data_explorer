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
    database_url: str | None = Field(default=None)
    # Optional Redis: cache live quotes for faster portfolio / watchlist.
    redis_url: str | None = Field(default=None)
    quote_cache_ttl_seconds: int = Field(default=60, ge=10, le=600)
    # Comma-separated CORS origins for different-origin UIs.
    # Same-origin `/` + `/api` does not require CORS.
    # Defaults cover local dev (direct API + separate UI port).
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

    # When TWSE realtime JSON fails, Yahoo Finance is used as fallback.
    # Synthetic mock TW quotes are for local demos only — not real prices.
    tw_use_mock_quotes: bool = Field(default=False)

    @property
    def sqlalchemy_database_url(self) -> str | None:
        """Normalize DATABASE_URL for SQLAlchemy + psycopg driver."""
        if not self.database_url:
            return None
        if self.database_url.startswith("postgres://"):
            return self.database_url.replace(
                "postgres://",
                "postgresql+psycopg://",
                1,
            )
        if self.database_url.startswith("postgresql://"):
            return self.database_url.replace(
                "postgresql://",
                "postgresql+psycopg://",
                1,
            )
        return self.database_url


settings = Settings()
