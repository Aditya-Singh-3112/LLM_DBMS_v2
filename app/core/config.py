from functools import lru_cache
from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_JWT_SECRET = "change-me-in-production"


class Settings(BaseSettings):
    app_name: str = "LLM-Powered DBMS"
    app_env: str = "development"
    debug: bool = False

    mongo_uri: str = "mongodb://localhost:27017"
    mongo_database: str = "llm_dbms"

    redis_uri: str = "redis://localhost:6379/0"

    postgres_uri: str = "postgresql://postgres:postgres@localhost:5432/llm_dbms"
    postgres_min_pool_size: int = Field(default = 5, ge = 1)
    postgres_max_pool_size: int = Field(default = 20, ge = 1)

    jwt_secret: str = DEFAULT_JWT_SECRET
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default = 30, ge = 1)
    refresh_token_expire_days: int = Field(default = 7, ge = 1)

    google_api_key: str | None = None
    # Set false to skip the vector store / embedding model (tests, CI).
    rag_enabled: bool = True

    # Overall deadline for one /ask request (LLM + tools).
    ask_timeout_seconds: int = Field(default = 90, ge = 5)

    # Comma-separated in the environment, e.g. CORS_ORIGINS=http://localhost:3000,http://10.0.0.5:3000
    cors_origins: str = "http://localhost:3000"

    # Refresh-token cookie. Secure must be true in production (HTTPS);
    # samesite "lax" requires the frontend and API to share a site
    # (e.g. app.example.com + api.example.com); use "none" only for a
    # genuinely cross-site deployment (Secure is then mandatory).
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    cookie_domain: str | None = None

    # Per-user daily cap on /ask requests (LLM cost control). 0 disables.
    ask_daily_limit: int = Field(default = 200, ge = 0)

    model_config = SettingsConfigDict(
        env_file = ".env",
        env_file_encoding = "utf-8",
        case_sensitive = False,
        extra = "ignore"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @model_validator(mode="after")
    def _production_guards(self) -> "Settings":
        if self.app_env != "development":
            if self.jwt_secret == DEFAULT_JWT_SECRET:
                raise ValueError(
                    "JWT_SECRET must be set to a non-default value when APP_ENV is not 'development'"
                )
            if not self.cookie_secure:
                raise ValueError("COOKIE_SECURE must be true when APP_ENV is not 'development'")
        if self.cookie_samesite not in ("lax", "strict", "none"):
            raise ValueError("COOKIE_SAMESITE must be lax, strict or none")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("COOKIE_SAMESITE=none requires COOKIE_SECURE=true")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
