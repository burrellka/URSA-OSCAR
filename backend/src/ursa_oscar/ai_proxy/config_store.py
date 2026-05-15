"""Non-secret AI proxy configuration — Phase 5 Ticket 1G companion.

Lives next to ``secrets.enc`` on the operator's data volume but stored
in plain JSON because none of the fields here are sensitive:
  - provider_id, model, endpoint_url, routing_mode, proxy_endpoint_url
  - custom_system_prompt (operator's choice; not a secret)
  - enabled

The encrypted ``secrets.enc`` companion stores API keys keyed by
provider id (e.g., ``claude_api_key``, ``openai_api_key``).

Two files because:
  1. Config gets edited often; rewriting an encrypted blob each time is
     wasteful and complicates dev introspection.
  2. The Settings UI's masked-config response needs to read config
     fields freely; only the boolean ``api_key_set`` comes from the
     secret store.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AiProxyConfig(BaseModel):
    """Operator-tunable AI proxy settings."""

    enabled: bool = False
    provider_id: str | None = None
    model: str = ""
    endpoint_url: str = ""
    # Only meaningful when provider_id="local". Other providers ignore.
    routing_mode: str = "direct"  # "direct" | "proxy"
    proxy_endpoint_url: str | None = None
    # Optional custom system prompt — when None, the default template
    # from prompt.py is used.
    custom_system_prompt: str | None = None
    # Forward-compat: extra provider-specific config (e.g., temperature)
    # without bumping the schema. Currently unused.
    extra: dict[str, Any] = Field(default_factory=dict)


class ConfigStore:
    """Simple JSON-backed store. Same load → mutate → save pattern as
    SecretStore — no concurrent writes to worry about."""

    def __init__(self, store_path: Path) -> None:
        self._path = store_path
        self._cache: AiProxyConfig | None = None

    def load(self) -> AiProxyConfig:
        if self._cache is not None:
            return self._cache
        if not self._path.exists():
            self._cache = AiProxyConfig()
            return self._cache
        try:
            raw = json.loads(self._path.read_text("utf-8"))
            self._cache = AiProxyConfig.model_validate(raw)
        except (json.JSONDecodeError, OSError, ValueError) as e:
            logger.exception("ConfigStore.load: corrupted config; falling back to defaults: %s", e)
            self._cache = AiProxyConfig()
        return self._cache

    def patch(self, **fields: Any) -> AiProxyConfig:
        """Merge the given fields into the current config and save.
        Validates via Pydantic — bad input raises ValidationError."""
        current = self.load()
        merged = current.model_copy(update={k: v for k, v in fields.items() if v is not None})
        # Re-validate via construction so model-level checks fire even
        # though model_copy bypasses them.
        new = AiProxyConfig.model_validate(merged.model_dump())
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(new.model_dump(), indent=2),
            encoding="utf-8",
        )
        self._cache = new
        return new

    def replace(self, config: AiProxyConfig) -> None:
        """Wholesale replacement — used by tests that need to reset state."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(config.model_dump(), indent=2),
            encoding="utf-8",
        )
        self._cache = config
