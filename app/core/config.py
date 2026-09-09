from functools import lru_cache
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

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

    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = Field(default = 30, ge = 1)
    refresh_token_expire_days: int = Field(default = 7, ge = 1)

    google_api_key: str | None = None

    model_config = SettingsConfigDict(
        env_file = ".env",
        env_file_encoding = "utf-8",
        case_sensitive = False,
        extra = "ignore"
    )

@lru_cache
def get_settings() -> Settings:
    return Settings()