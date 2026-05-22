"""Provider preset registry — Phase 5 Decision 2 (v2).

Seven user-facing presets, all routing through one of two adapters
(``claude`` or ``openai_compat``). The Settings UI loads this registry
via ``GET /api/v1/ai/providers`` and populates the dropdown + auto-
populates the endpoint + auth-header format when the operator picks one.

Adding a new provider later is a config-only change — append a
ProviderPreset to the list. The OpenAI-compat adapter handles any
new provider that speaks ``/v1/chat/completions``.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class ProviderPreset(BaseModel):
    """One entry in the dropdown. Endpoint + models come pre-populated;
    operator can override the endpoint (e.g., for Azure OpenAI deployments
    or self-hosted forks) but the dropdown's default works out of the box
    for the named provider."""

    id: str
    label: str
    adapter: Literal["claude", "openai_compat"]
    default_endpoint: str
    default_models: list[str]
    # The provider's expected auth header. Two shapes cover everything in
    # the registry today:
    #   ("x-api-key", "{key}")          — Anthropic
    #   ("Authorization", "Bearer {key}") — everyone else
    auth_header_name: str
    auth_header_format: str
    notes: str
    # When true, the Settings UI shows the routing-mode radio (Direct /
    # Through Proxy) and the proxy URL field. Currently only Local LLM
    # has this — but Custom could opt in later for users routing through
    # their own RAG layer.
    supports_local_routing: bool = False


PRESETS: list[ProviderPreset] = [
    ProviderPreset(
        id="claude",
        label="Claude API (Anthropic)",
        adapter="claude",
        default_endpoint="https://api.anthropic.com",
        default_models=[
            "claude-sonnet-4-5-20250929",
            "claude-opus-4-5-20250929",
            "claude-haiku-4-5-20250929",
        ],
        auth_header_name="x-api-key",
        auth_header_format="{key}",
        notes=(
            "Native Anthropic API. Best tool-calling reliability. "
            "Requires an API key from console.anthropic.com."
        ),
    ),
    ProviderPreset(
        id="openai",
        label="OpenAI",
        adapter="openai_compat",
        default_endpoint="https://api.openai.com/v1",
        default_models=["gpt-4o", "gpt-4o-mini", "gpt-4-turbo"],
        auth_header_name="Authorization",
        auth_header_format="Bearer {key}",
        notes=(
            "Native OpenAI function-calling. "
            "Requires an API key from platform.openai.com."
        ),
    ),
    ProviderPreset(
        id="gemini",
        label="Google Gemini (OpenAI-compat)",
        adapter="openai_compat",
        default_endpoint="https://generativelanguage.googleapis.com/v1beta/openai",
        # 1.1.2 — gemini-2.0-flash-exp and gemini-1.5-pro are deprecated
        # as of May 2026. gemini-1.5-flash is on the deprecation track too.
        # Default to gemini-3.5-flash; the freeform model field accepts any
        # other model string for operators who want a specific snapshot.
        default_models=[
            "gemini-3.5-flash",
        ],
        auth_header_name="Authorization",
        auth_header_format="Bearer {key}",
        notes=(
            "Google's OpenAI compatibility layer. Tool calling reliability "
            "varies by model. Requires an API key from aistudio.google.com."
        ),
    ),
    ProviderPreset(
        id="openrouter",
        label="OpenRouter",
        adapter="openai_compat",
        default_endpoint="https://openrouter.ai/api/v1",
        # 1.1.2 — drop google/gemini-2.0-flash (deprecated upstream).
        default_models=[
            "anthropic/claude-sonnet-4.5",
            "openai/gpt-4o",
            "google/gemini-3.5-flash",
        ],
        auth_header_name="Authorization",
        auth_header_format="Bearer {key}",
        notes=(
            "Multi-model proxy. Try many models with one API key. "
            "Requires a key from openrouter.ai."
        ),
    ),
    ProviderPreset(
        id="groq",
        label="Groq",
        adapter="openai_compat",
        default_endpoint="https://api.groq.com/openai/v1",
        default_models=[
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "mixtral-8x7b-32768",
        ],
        auth_header_name="Authorization",
        auth_header_format="Bearer {key}",
        notes="Fast inference. Tool-calling support varies by model.",
    ),
    ProviderPreset(
        id="local",
        label="Local LLM",
        adapter="openai_compat",
        default_endpoint="",     # operator-supplied; UI shows common examples
        default_models=[],       # operator-supplied
        auth_header_name="Authorization",
        auth_header_format="Bearer {key}",  # optional; many local servers accept empty
        notes=(
            "LocalAI, Ollama, llama.cpp server, vLLM, LM Studio, etc. "
            "Data stays on your local network. Routing mode (Direct vs "
            "Through Proxy) configures whether requests flow through a "
            "RAG layer like LocalRecall."
        ),
        supports_local_routing=True,
    ),
    ProviderPreset(
        id="custom",
        label="Custom (OpenAI-compatible)",
        adapter="openai_compat",
        default_endpoint="",
        default_models=[],
        auth_header_name="Authorization",
        auth_header_format="Bearer {key}",
        notes=(
            "Any OpenAI-compatible endpoint. Specify URL and model "
            "manually. Use this for Azure OpenAI deployments, "
            "self-hosted forks, or new providers not yet in the dropdown."
        ),
    ),
]


def get_preset(provider_id: str) -> ProviderPreset | None:
    """Look up a preset by id. Returns None on miss — callers handle the
    invalid-config case at the API endpoint boundary."""
    for p in PRESETS:
        if p.id == provider_id:
            return p
    return None


def build_auth_header(preset: ProviderPreset, api_key: str | None) -> dict[str, str]:
    """Construct the auth header dict from preset format + key. Returns
    an empty dict when api_key is None or empty (some local LLM
    deployments don't require auth — passing an empty header is the
    right move there)."""
    if not api_key:
        return {}
    formatted = preset.auth_header_format.format(key=api_key)
    return {preset.auth_header_name: formatted}
