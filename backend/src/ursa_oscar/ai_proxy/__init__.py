"""AI proxy module — Phase 5.

The AI proxy lives inside the API container and brokers LLM calls
between the chat panel UI and a user-configured provider (Claude API
or any OpenAI-compatible endpoint). All API keys stay server-side
(Decision 1); conversations are client-state only (Decision 5);
secrets are Fernet-encrypted at rest (Decision 7).

Public surface for the rest of the API container:
  - ``build_adapter(provider_id, config_dict, api_key) -> ProviderAdapter``
  - ``execute_tool(name, args, api_base_url) -> dict`` (re-exported from tools)
  - ``render_system_prompt(...)`` (re-exported from prompt)
"""
from .providers.base import (  # noqa: F401
    AiMessage,
    AiStreamEvent,
    AiToolCall,
    ProviderAdapter,
    ProviderTestResult,
)
from .providers.claude import ClaudeAdapter  # noqa: F401
from .providers.openai_compat import OpenAiCompatAdapter  # noqa: F401
from .providers.presets import (  # noqa: F401
    PRESETS,
    ProviderPreset,
    build_auth_header,
    get_preset,
)


# 1.1.11 — provider-family defaults for the HTTP read timeout on
# streaming chat calls. Local LLMs get a generous 5-minute window
# because thinking-mode models (Qwen3, DeepSeek-R1, GPT-OSS, Gemma-4)
# on CPU can spend several minutes on the chain-of-thought before
# the first content token. Cloud APIs (Claude, OpenAI, Gemini, etc.)
# stream within a few seconds of connect; longer waits usually mean
# a real network problem worth surfacing rather than hiding.
DEFAULT_TIMEOUT_SECONDS_LOCAL = 300  # 5 minutes
DEFAULT_TIMEOUT_SECONDS_CLOUD = 120  # 2 minutes


def _effective_timeout(provider_id: str, configured: int | None) -> float:
    """Return the per-request HTTP read timeout in seconds. Operator
    override wins; otherwise pick the family default."""
    if configured is not None:
        return float(configured)
    if provider_id == "local":
        return float(DEFAULT_TIMEOUT_SECONDS_LOCAL)
    return float(DEFAULT_TIMEOUT_SECONDS_CLOUD)


def build_adapter(provider_id: str, config_dict: dict, api_key: str | None):
    """Pick the right adapter class for a provider id and wire it with
    the operator's stored config + decrypted API key. Returns ``None``
    when the provider isn't in the registry — caller turns that into a
    400 (better than silently using a default)."""
    preset = get_preset(provider_id)
    if preset is None:
        return None

    endpoint = (
        config_dict.get("endpoint_url")
        or preset.default_endpoint
    )
    model = config_dict.get("model") or (
        preset.default_models[0] if preset.default_models else ""
    )
    headers = build_auth_header(preset, api_key)
    timeout = _effective_timeout(
        provider_id, config_dict.get("timeout_seconds"),
    )

    cls = ClaudeAdapter if preset.adapter == "claude" else OpenAiCompatAdapter
    return cls(
        api_key=api_key,
        endpoint=endpoint,
        model=model,
        extra_headers=headers,
        timeout_seconds=timeout,
    )
