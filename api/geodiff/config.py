"""Runtime configuration, read from environment variables."""

from functools import lru_cache

from pydantic import AliasChoices, Field
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
    # GIT_SHA locally/CI; Render injects RENDER_GIT_COMMIT for the deployed commit.
    git_sha: str = Field(
        default="dev", validation_alias=AliasChoices("GIT_SHA", "RENDER_GIT_COMMIT")
    )

    # Public-demo bounds (see docs/API.md, "Limits").
    max_upload_bytes: int = 5 * 1024 * 1024
    max_features: int = 10_000
    max_datasets_per_workspace: int = 12
    max_comparisons_per_workspace: int = 30
    max_active_workspaces: int = 200
    workspace_ttl_hours: int = 72
    page_max: int = 200
    arcgis_enabled: bool = True
    map_max_features: int = 2_000
    bulk_max: int = 5_000
    rate_limit_writes_per_min: int = 120
    rate_limit_workspaces_per_hour: int = 20
    # Header that carries the real client IP, set (and overwritten) by a trusted edge proxy, e.g.
    # "cf-connecting-ip" on Render (Cloudflare in front). Empty: use the TCP peer address.
    # Never X-Forwarded-For: Render appends to it, so its first entry is client-controlled.
    client_ip_header: str = ""

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
