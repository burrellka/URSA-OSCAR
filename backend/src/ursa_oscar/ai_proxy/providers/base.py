"""Provider adapter interface.

Phase 5 Ticket 1 — every concrete provider (Claude, OpenAI, Gemini, etc.)
implements this interface. The chat panel + Settings UI route through a
single ``ProviderAdapter`` instance picked at request time based on the
operator's configured ``provider_id``.

Wire shape:
  ``chat(...)`` is an async generator of ``AiStreamEvent`` objects. The
  endpoint forwards these to the browser as SSE frames. Each adapter
  internally translates the provider's native streaming format (Anthropic
  SDK events, OpenAI chunked completions) into this normalized event
  stream so the frontend doesn't have to know which provider is on the
  other side.

Tool calling is normalized to OpenAI-style ``tool_calls`` arrays in the
``AiMessage`` shape. Claude's native ``tool_use`` blocks are mapped at
the adapter boundary.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, AsyncIterator, Literal

from pydantic import BaseModel


# -------------------------------------------------------------------------
# Message + event shapes — shared across both adapters.
# -------------------------------------------------------------------------


class AiToolCall(BaseModel):
    """One tool invocation requested by the LLM. ``id`` is the
    provider-emitted handle that pairs the call with its eventual result;
    the adapter constructs this and the tool executor echoes it back."""
    id: str
    name: str
    arguments: dict


class AiMessage(BaseModel):
    """One message in a conversation. ``content`` is plain text for the
    common case; tool-call messages carry ``tool_calls`` instead and the
    response messages from the tool executor carry ``tool_call_id`` +
    string content."""
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[AiToolCall] | None = None
    tool_call_id: str | None = None


class AiStreamEvent(BaseModel):
    """One frame in the SSE stream surfaced to the browser.

    Event types:
      text            — append ``payload.text`` to the assistant message
      tool_call_start — assistant has begun emitting a tool call;
                        ``payload.id`` + ``payload.name`` known
      tool_call_input — partial JSON arguments for a tool call —
                        adapters that stream incremental tool input
                        emit this; others only emit ``tool_call_complete``
      tool_call_complete — full tool call ready to execute;
                          ``payload.id`` + ``payload.name`` + ``payload.arguments``
      tool_result     — server-side: a tool just ran;
                        ``payload.id`` + ``payload.result`` (envelope shape)
      complete        — the LLM finished this turn; ``payload.stop_reason``
                        + ``payload.usage`` (input/output token counts when known)
      error           — adapter or upstream error; ``payload.message``
                        + ``payload.code`` (e.g., "unauthorized", "rate_limit")
    """
    event_type: Literal[
        "text",
        "tool_call_start",
        "tool_call_input",
        "tool_call_complete",
        "tool_result",
        "complete",
        "error",
    ]
    payload: dict


class ProviderTestResult(BaseModel):
    """Returned by ``test_connection()``. Used by Settings → Test."""
    ok: bool
    error: str | None = None
    model_info: dict | None = None


# -------------------------------------------------------------------------
# Abstract base.
# -------------------------------------------------------------------------


class ProviderAdapter(ABC):
    """One LLM provider implementation.

    Concrete classes:
      ``ClaudeAdapter``       — Anthropic Messages API via the SDK
      ``OpenAiCompatAdapter`` — OpenAI ``/v1/chat/completions`` (also
                                covers Gemini's compat layer, OpenRouter,
                                Groq, LocalAI/Ollama/LM Studio, generic)

    Construction params are pulled from the per-provider config stored
    in ``ai_config.json`` (non-secret) + ``secrets.enc`` (encrypted).
    Each adapter validates its required params at __init__ and raises a
    clear error if anything's missing — the API endpoint turns that into
    a 400 with the operator-friendly diagnostic.
    """

    def __init__(
        self,
        *,
        api_key: str | None,
        endpoint: str,
        model: str,
        extra_headers: dict | None = None,
        **kwargs: Any,
    ) -> None:
        self.api_key = api_key
        self.endpoint = endpoint
        self.model = model
        self.extra_headers = extra_headers or {}

    @abstractmethod
    async def chat(
        self,
        messages: list[AiMessage],
        tools: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[AiStreamEvent]:
        """Stream a chat completion with tool-calling.

        ``tools`` is a list of OpenAI-style tool descriptors:
            [{"type": "function", "function": {"name": ..., "description": ..., "parameters": <JSON schema>}}]
        Claude adapter translates this to Anthropic's ``tools=[...]`` format internally.
        """
        raise NotImplementedError
        yield  # pragma: no cover — satisfies async-generator typing

    @abstractmethod
    async def test_connection(self) -> ProviderTestResult:
        """Cheap probe — usually a 1-token completion or a list-models call.
        Used by Settings → Test connection button."""
        raise NotImplementedError
