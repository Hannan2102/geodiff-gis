"""Runtime configuration, read from environment variables."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore")

    database_url: str = Field(
        default="postgresql://geodiff:geodiff-local-dev@localhost:5433/geodiff",
        description="libpq connection string. Set via the host secret store in production.",
    )
    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    db_pool_min: int = 1
    db_pool_max: int = 4
    db_connect_timeout_s: int = 5
    log_level: str = "info"
    git_sha: str = "dev"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
