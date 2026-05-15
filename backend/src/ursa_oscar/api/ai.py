"""AI proxy endpoints — Phase 5 Ticket 1H.

Mounted under /api/v1/ai/*. Five endpoints:

  GET  /api/v1/ai/providers      — provider preset registry
  GET  /api/v1/ai/config          — current config (masked secrets)
  POST /api/v1/ai/config          — PATCH-semantics update
  POST /api/v1/ai/test            — connection probe for a provider
  POST /api/v1/ai/chat            — SSE-stream chat completion + tools

The chat endpoint owns the tool-execution loop: stream from the
adapter, execute tool calls as they complete, send tool results back
into the conversation, continue streaming until the adapter signals
``complete`` with a non-tool stop reason.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date as date_t

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..ai_proxy import (
    AiMessage,
    AiToolCall,
    build_adapter,
    get_preset,
)
from ..ai_proxy.config_store import AiProxyConfig
from ..ai_proxy.prompt import render_system_prompt
from ..ai_proxy.providers.base import AiStreamEvent
from ..ai_proxy.providers.presets import PRESETS
from ..ai_proxy.tools import TOOL_DESCRIPTORS, execute_tool

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/ai", tags=["ai"])


# -------------------------------------------------------------------------
# Request / response models.
# -------------------------------------------------------------------------


class ChatRequestContext(BaseModel):
    """Optional context the UI passes through. ``current_date`` tells
    the LLM what Daily View is currently showing (for "this night"
    references). ``include_profile`` defaults to true — the system
    prompt always includes clinical context unless the operator opts
    out via a profile setting."""
    current_date: str | None = None
    include_profile: bool = True


class ChatRequest(BaseModel):
    messages: list[AiMessage]
    context: ChatRequestContext = Field(default_factory=ChatRequestContext)


class ConfigUpdate(BaseModel):
    """PATCH body for /ai/config. All fields optional — any present
    field updates the corresponding stored config value. ``api_key``
    is the only field that goes through the SecretStore; everything
    else is plain-JSON config."""
    enabled: bool | None = None
    provider_id: str | None = None
    model: str | None = None
    endpoint_url: str | None = None
    routing_mode: str | None = None
    proxy_endpoint_url: str | None = None
    custom_system_prompt: str | None = None
    api_key: str | None = None


class TestRequest(BaseModel):
    provider_id: str


class MaskedConfig(BaseModel):
    """Shape returned by GET /ai/config. Never includes raw secret
    values — only an ``api_key_set: bool`` flag per provider."""
    enabled: bool
    provider_id: str | None
    model: str
    endpoint_url: str
    routing_mode: str
    proxy_endpoint_url: str | None
    custom_system_prompt: str | None
    api_key_set: bool
    # Per-provider api_key_set map so the Settings UI can show which
    # providers have keys stored without revealing which is selected.
    api_keys_set: dict[str, bool]


# -------------------------------------------------------------------------
# /ai/providers — preset registry.
# -------------------------------------------------------------------------


@router.get("/providers")
def list_providers() -> dict:
    """Return the seven user-facing presets. Settings UI populates the
    dropdown from this. Pure config; no auth required (the registry
    itself is not sensitive)."""
    return {"providers": [p.model_dump() for p in PRESETS]}


# -------------------------------------------------------------------------
# /ai/config — operator-facing config CRUD.
# -------------------------------------------------------------------------


@router.get("/config", response_model=MaskedConfig)
def get_config(request: Request) -> MaskedConfig:
    """Current config with secrets masked. Returns a per-provider
    ``api_keys_set`` map so the UI can render "Replace key" vs
    "Add key" buttons without leaking values."""
    config_store = request.app.state.ai_config_store
    secrets = request.app.state.ai_secrets
    cfg: AiProxyConfig = config_store.load()

    # api_keys_set: one entry per preset; True iff a key is stored.
    keys_set = {
        p.id: secrets.has(f"{p.id}_api_key")
        for p in PRESETS
    }
    selected_key_set = (
        keys_set.get(cfg.provider_id, False)
        if cfg.provider_id else False
    )
    return MaskedConfig(
        enabled=cfg.enabled,
        provider_id=cfg.provider_id,
        model=cfg.model,
        endpoint_url=cfg.endpoint_url,
        routing_mode=cfg.routing_mode,
        proxy_endpoint_url=cfg.proxy_endpoint_url,
        custom_system_prompt=cfg.custom_system_prompt,
        api_key_set=selected_key_set,
        api_keys_set=keys_set,
    )


@router.post("/config", response_model=MaskedConfig)
def patch_config(req: ConfigUpdate, request: Request) -> MaskedConfig:
    """PATCH-semantics: any field present in the body is applied; absent
    fields are left as-is. The ``api_key`` field is special — it goes
    through the SecretStore keyed by provider id, NOT into the JSON
    config. Set ``api_key=""`` to clear a stored key.

    Returns the new masked config (same shape as GET)."""
    config_store = request.app.state.ai_config_store
    secrets = request.app.state.ai_secrets

    # Pull out api_key — it never lands in plain-JSON config.
    api_key = req.api_key
    fields = req.model_dump(exclude={"api_key"}, exclude_none=True)

    # Validate provider_id if set — must be a known preset.
    if "provider_id" in fields:
        pid = fields["provider_id"]
        if pid is not None and get_preset(pid) is None:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown provider_id: {pid}",
            )

    new_cfg = config_store.patch(**fields)

    # If api_key was sent, store it under the appropriate provider's key
    # name. Prefer req.provider_id (the operator may be configuring a
    # different provider than the currently-selected one); fall back to
    # the now-saved provider_id.
    if api_key is not None:
        target_provider = req.provider_id or new_cfg.provider_id
        if target_provider is None:
            raise HTTPException(
                status_code=400,
                detail=(
                    "api_key provided but no provider_id set — specify "
                    "provider_id in this request or pre-set it."
                ),
            )
        secrets.set(f"{target_provider}_api_key", api_key)

    return get_config(request)


# -------------------------------------------------------------------------
# /ai/test — connection probe.
# -------------------------------------------------------------------------


@router.post("/test")
async def test_connection(req: TestRequest, request: Request) -> dict:
    """Cheap probe — usually a 1-token completion. Used by the
    Settings UI's Test connection button. Does NOT consult the
    currently-active config; uses the per-provider stored key + the
    config currently saved (so it tests what the operator just
    saved)."""
    preset = get_preset(req.provider_id)
    if preset is None:
        return {"ok": False, "error": f"Unknown provider_id: {req.provider_id}"}

    config_store = request.app.state.ai_config_store
    secrets = request.app.state.ai_secrets
    cfg = config_store.load()
    api_key = secrets.get(f"{req.provider_id}_api_key")

    # Build adapter targeting THIS provider (not necessarily the
    # currently-selected one). Endpoint + model come from the saved
    # config if the provider matches the saved provider, otherwise
    # the preset defaults.
    if cfg.provider_id == req.provider_id:
        config_dict = cfg.model_dump()
    else:
        config_dict = {
            "endpoint_url": preset.default_endpoint,
            "model": preset.default_models[0] if preset.default_models else "",
        }

    adapter = build_adapter(req.provider_id, config_dict, api_key)
    if adapter is None:
        return {"ok": False, "error": "Failed to construct adapter."}

    result = await adapter.test_connection()
    return result.model_dump()


# -------------------------------------------------------------------------
# /ai/chat — SSE-streamed chat completion with tool execution.
# -------------------------------------------------------------------------


@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    """Stream a chat completion. SSE response:

      data: {"event_type": "text", "payload": {"text": "..."}}\\n\\n
      data: {"event_type": "tool_call_start", "payload": {...}}\\n\\n
      ...
      data: {"event_type": "complete", "payload": {...}}\\n\\n

    Server-side tool execution: when the adapter emits
    ``tool_call_complete``, we run the tool via
    ``execute_tool()``, append the tool result as an
    ``AiMessage(role="tool")`` into the conversation, and call the
    adapter again to continue the LLM's response. Loops until the
    adapter completes without requesting more tools (or hits a safety
    cap to avoid runaway tool loops).
    """
    config_store = request.app.state.ai_config_store
    secrets = request.app.state.ai_secrets
    cfg: AiProxyConfig = config_store.load()

    if not cfg.enabled:
        raise HTTPException(
            status_code=400,
            detail="AI assistant is disabled. Enable it in Settings → AI Assistant.",
        )
    if not cfg.provider_id:
        raise HTTPException(
            status_code=400,
            detail="No AI provider configured. Pick one in Settings → AI Assistant.",
        )

    api_key = secrets.get(f"{cfg.provider_id}_api_key")
    preset = get_preset(cfg.provider_id)
    if preset is None:
        raise HTTPException(
            status_code=400,
            detail=f"Configured provider_id '{cfg.provider_id}' is not in the registry.",
        )

    adapter = build_adapter(cfg.provider_id, cfg.model_dump(), api_key)
    if adapter is None:
        raise HTTPException(status_code=400, detail="Failed to construct adapter.")

    # Build the system prompt with current context.
    profile = _load_profile(request)
    today = date_t.today()
    current_view = (
        f"Daily View for {req.context.current_date}"
        if req.context.current_date else None
    )
    system_prompt = render_system_prompt(
        user_profile=profile if req.context.include_profile else None,
        device_clock=(profile or {}).get("display", {}).get("device_clock"),
        today_date=today,
        current_view=current_view,
        custom_template=cfg.custom_system_prompt,
    )

    # API base URL for in-process tool execution. The chat endpoint
    # runs inside the API process; we want loopback (don't round-trip
    # through the public URL / web proxy). Build the URL from the
    # ASGI server scope so we hit whatever port uvicorn actually
    # bound to — works in production (8000) AND in tests (random
    # free port). Falls back to 8000 if the scope is missing the
    # server key (defensive).
    server = request.scope.get("server") or ("127.0.0.1", 8000)
    api_base_url = f"http://{server[0]}:{server[1]}"

    async def event_generator():
        messages = list(req.messages)
        # Safety cap on tool loops. Each loop = one adapter.chat() call +
        # possible tool executions. Typical use: 1-3 loops for a multi-
        # tool query. Cap at 8 to prevent runaway loops on a misbehaving
        # model (Llama 3.2 3B has been known to chain-call indefinitely).
        for loop_n in range(8):
            pending_tool_calls: list[AiToolCall] = []
            saw_text = False
            stop_reason: str | None = None

            try:
                async for event in adapter.chat(
                    messages=messages,
                    tools=TOOL_DESCRIPTORS,
                    system_prompt=system_prompt,
                ):
                    yield _sse_pack(event)

                    if event.event_type == "text":
                        saw_text = True
                    elif event.event_type == "tool_call_complete":
                        pending_tool_calls.append(
                            AiToolCall(
                                id=event.payload["id"],
                                name=event.payload["name"],
                                arguments=event.payload["arguments"],
                            ),
                        )
                    elif event.event_type == "complete":
                        stop_reason = event.payload.get("stop_reason")
                    elif event.event_type == "error":
                        # Adapter errored — surface and stop.
                        return
            except asyncio.CancelledError:
                logger.info("ai/chat: client disconnected; aborting stream")
                raise

            if not pending_tool_calls:
                # No more tools requested — the conversation is done.
                return

            # Insert the assistant's tool-call turn into the conversation
            # so the next adapter.chat() call sees the prior context.
            messages.append(AiMessage(
                role="assistant",
                content="",
                tool_calls=pending_tool_calls,
            ))

            # Execute each requested tool and append the result.
            for tc in pending_tool_calls:
                result = await execute_tool(tc.name, tc.arguments, api_base_url)
                yield _sse_pack(AiStreamEvent(
                    event_type="tool_result",
                    payload={"id": tc.id, "result": result},
                ))
                messages.append(AiMessage(
                    role="tool",
                    tool_call_id=tc.id,
                    content=json.dumps(result),
                ))

            # Continue the loop — adapter sees the tool results on next call.
        else:
            # Hit the loop cap. Emit a synthetic final event so the
            # client knows to stop.
            yield _sse_pack(AiStreamEvent(
                event_type="error",
                payload={
                    "message": (
                        "Tool-call loop limit reached (8 iterations). "
                        "Try again with a more specific question, or "
                        "switch to a stronger model."
                    ),
                    "code": "tool_loop_limit",
                },
            ))

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # tell nginx (web container) not to buffer
        },
    )


# -------------------------------------------------------------------------
# Helpers.
# -------------------------------------------------------------------------


def _sse_pack(event: AiStreamEvent) -> str:
    """Encode an AiStreamEvent as a single SSE frame."""
    return f"data: {event.model_dump_json()}\n\n"


def _load_profile(request: Request) -> dict | None:
    """Pull the user profile from the on-disk store. Best-effort —
    returns None if the file doesn't exist or fails to parse.
    Importing the profile store here rather than as a module-level
    import keeps the ai_proxy package decoupled from the storage
    layout."""
    try:
        from ..config import get_settings
        from ..storage import profile_store

        settings = get_settings()
        profile_path = settings.db_path.parent / "profile.json"
        return profile_store.read_raw(profile_path)
    except Exception:
        logger.exception("ai/chat: failed to load profile; using empty context")
        return None
