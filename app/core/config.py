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

    # The MCP server the agent calls its tools through (app/mcp_server).
    mcp_server_url: str = "http://localhost:8001/mcp"
    # Who issues the bearer tokens the MCP server accepts (this API). The MCP
    # SDK requires it for its auth settings.
    mcp_issuer_url: str = "http://localhost:8000"

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
    # Per-user daily cap on LLM tokens (input + output). 0 disables.
    ask_daily_token_limit: int = Field(default = 0, ge = 0)

    # Storage per database (tables + indexes). Writes and imports that would
    # grow a database past it are rolled back. 0 disables.
    database_max_mb: int = Field(default = 500, ge = 0)
    # Largest set of tables a write may touch and still be undoable. 0 disables undo.
    undo_max_mb: int = Field(default = 50, ge = 0)

    # Where links in emails point (verification, password reset).
    frontend_url: str = "http://localhost:3000"
    # "console" logs emails (development), "smtp" sends them, "memory" keeps
    # them for tests.
    email_backend: str = "console"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_starttls: bool = True
    email_from: str = "LLM-DBMS <no-reply@localhost>"

    # Failed logins per email before it is locked out for the window.
    login_max_failures: int = Field(default = 5, ge = 1)
    # Failed logins per client IP (any email) before that IP is locked out.
    login_max_failures_per_ip: int = Field(default = 50, ge = 1)
    login_lockout_minutes: int = Field(default = 15, ge = 1)

    # Prometheus metrics at /metrics. Keep it off the public internet.
    metrics_enabled: bool = True

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
        if self.email_backend not in ("console", "smtp", "memory"):
            raise ValueError("EMAIL_BACKEND must be console, smtp or memory")
        if self.email_backend == "smtp" and not self.smtp_host:
            raise ValueError("EMAIL_BACKEND=smtp requires SMTP_HOST")
        if self.cookie_samesite not in ("lax", "strict", "none"):
            raise ValueError("COOKIE_SAMESITE must be lax, strict or none")
        if self.cookie_samesite == "none" and not self.cookie_secure:
            raise ValueError("COOKIE_SAMESITE=none requires COOKIE_SECURE=true")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
