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

    cls = ClaudeAdapter if preset.adapter == "claude" else OpenAiCompatAdapter
    return cls(
        api_key=api_key,
        endpoint=endpoint,
        model=model,
        extra_headers=headers,
    )
