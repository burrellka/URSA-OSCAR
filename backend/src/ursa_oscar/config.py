"""URSA-OSCAR backend configuration.

Stricter than APEX's plain os.environ.get pattern — Pydantic Settings reads
.env or environment variables, validates types at startup, and fails fast on
misconfiguration. Per Design v1.1 § Tech Stack row "Config management".
"""
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Backend service settings.

    Read from environment variables prefixed with URSA_OSCAR_, with a .env
    file fallback for local dev. Production reads from container env (Dockge).
    """

    model_config = SettingsConfigDict(
        env_prefix="URSA_OSCAR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # DuckDB file path. Default points at the docker volume mount.
    db_path: Path = Path("/data/ursa-oscar.duckdb")

    # SD-card / watched-folder import root (Phase 4).
    import_watch_path: Path = Path("/cpap-import")

    # Generated exports landing zone.
    exports_path: Path = Path("/data/exports")

    # When True, the API exposes the unauthenticated read-only LAN-bypass surface
    # (Design v1.1 Decision 13). Default off; dev compose sets it on.
    dev_bypass_enabled: bool = Field(default=False, alias="URSA_OSCAR_DEV_BYPASS_ENABLED")

    # ---- Phase-2-polish: Settings-page surface (Item 5) ---------------------
    # The API exposes a masked view of these at GET /api/v1/system/config so
    # the web UI's Settings page can show operational state without granting
    # the browser any raw secret values. The variables are also consumed by
    # POST /api/v1/system/verify-mcp to talk to the MCP container.
    #
    # Container-internal URL used by /verify-mcp. Defaults to the kairos-net
    # service name; dev compose can override to localhost.
    mcp_internal_url: str = Field(default="http://ursa-oscar-mcp:8000", alias="URSA_OSCAR_MCP_INTERNAL_URL")
    # Public hostname surfaced to users + claude.ai. Mirrored from the MCP
    # container so the API can show it in the Settings page.
    mcp_base_url: str | None = Field(default=None, alias="URSA_OSCAR_MCP_BASE_URL")
    # Mirrored secrets — never returned in full over the wire; masked
    # server-side before the /api/v1/system/config response.
    mcp_bearer_token: str | None = Field(default=None, alias="URSA_OSCAR_MCP_BEARER_TOKEN")
    mcp_oauth_client_id: str | None = Field(default=None, alias="URSA_OSCAR_MCP_OAUTH_CLIENT_ID")
    mcp_oauth_client_secret: str | None = Field(default=None, alias="URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET")
    # Image versions baked at build time via Docker ARG. Default "dev" if
    # the image wasn't built by build_and_push.ps1.
    api_image_version: str = Field(default="dev", alias="URSA_OSCAR_IMAGE_VERSION")
    mcp_image_version: str | None = Field(default=None, alias="URSA_OSCAR_MCP_IMAGE_VERSION")
    web_image_version: str | None = Field(default=None, alias="URSA_OSCAR_WEB_IMAGE_VERSION")
    watcher_image_version: str | None = Field(default=None, alias="URSA_OSCAR_WATCHER_IMAGE_VERSION")


_settings: Settings | None = None


def get_settings() -> Settings:
    """Return a cached Settings singleton. Created on first call."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
