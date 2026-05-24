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
# /ai/system-prompt/template — operator-editable template (0.9.10).
# -------------------------------------------------------------------------


class TemplateResponse(BaseModel):
    """Shape returned by GET /ai/system-prompt/template."""
    template: str
    source: str = Field(
        description=(
            "Where the returned text came from. 'file' = an operator has "
            "written a template to /data/system_prompt_template.txt. "
            "'default' = the file doesn't exist; we returned the in-code "
            "DEFAULT_TEMPLATE constant. The Settings UI uses this to show "
            "a 'Using saved template' vs 'Using built-in default' badge."
        ),
    )


class TemplateUpdate(BaseModel):
    template: str = Field(
        description=(
            "The new template content. Replaces whatever's currently in "
            "/data/system_prompt_template.txt. Atomic — a crash mid-write "
            "won't leave a half-written file."
        ),
    )


@router.get("/system-prompt/template", response_model=TemplateResponse)
def get_system_prompt_template(request: Request) -> TemplateResponse:
    """Return the current system-prompt template. On first read after a
    fresh install (no file written yet), returns the in-code default
    with source='default'. After the operator clicks "Save to template"
    once, returns the file's content with source='file'."""
    store = request.app.state.ai_template_store
    text, source = store.get_template()
    return TemplateResponse(template=text, source=source)


@router.put("/system-prompt/template", response_model=TemplateResponse)
def put_system_prompt_template(
    body: TemplateUpdate, request: Request,
) -> TemplateResponse:
    """Replace the stored template. After this returns, every subsequent
    chat session that doesn't have a per-provider custom_system_prompt
    override will use the new template."""
    store = request.app.state.ai_template_store
    store.set_template(body.template)
    text, source = store.get_template()
    return TemplateResponse(template=text, source=source)


@router.delete("/system-prompt/template", response_model=TemplateResponse)
def delete_system_prompt_template(request: Request) -> TemplateResponse:
    """0.11.1 — Reset to the in-code DEFAULT_TEMPLATE shipped with this
    image. Deletes the operator's saved ``/data/system_prompt_template.txt``
    file and returns the factory-default content with source='default'.

    Useful when a new image ships richer template content (added
    sections, refined guidance) and the operator wants to adopt the
    upstream default rather than stay forked on their saved file. The
    operator can then "Save to template" again to re-fork from the
    new baseline, or leave the file deleted to track DEFAULT_TEMPLATE
    going forward."""
    store = request.app.state.ai_template_store
    store.reset()
    text, source = store.get_template()
    return TemplateResponse(template=text, source=source)


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
    # 0.9.10 — template resolution:
    #   1. cfg.custom_system_prompt (per-provider override) — if non-empty
    #   2. TemplateStore.get_template_text() — file-backed editable template
    #   3. None → render_system_prompt falls through to DEFAULT_TEMPLATE
    template_store = request.app.state.ai_template_store
    template_text = (
        cfg.custom_system_prompt
        or template_store.get_template_text()
    )
    system_prompt = render_system_prompt(
        user_profile=profile if req.context.include_profile else None,
        device_clock=(profile or {}).get("display", {}).get("device_clock"),
        today_date=today,
        current_view=current_view,
        custom_template=template_text,
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

    # 1.1.1 fix — Phase 6.4 added _AUTH_REQUIRED to every router but
    # this loopback path didn't forward the operator JWT, so every
    # tool call landed as anonymous and 401'd. ``_AUTH_REQUIRED``
    # already validated the inbound JWT before we got here; we just
    # need to relay it. Browser sessions present the JWT via the
    # ``ursa_oscar_session`` cookie; MCP/CLI clients via Authorization
    # Bearer header. Forward whichever the operator presented, as a
    # Bearer header (the simpler of the two for httpx — and the
    # backend's middleware accepts either form).
    from ..auth.middleware import COOKIE_NAME as _SESSION_COOKIE
    _cookie_token = request.cookies.get(_SESSION_COOKIE)
    if _cookie_token:
        auth_header = f"Bearer {_cookie_token}"
    else:
        auth_header = request.headers.get("authorization")

    async def event_generator():
        # 0.9.4 — emit an immediate SSE comment (keepalive) before the
        # first LLM byte arrives. Two things this protects against:
        #   1. Intermediate proxies (Cloudflare, gateway nginx, etc.)
        #      that wait to see actual bytes before relaying response
        #      headers and the streaming body to the client.
        #   2. Browsers that may delay engaging the ReadableStream
        #      consumer until the first chunk arrives.
        # SSE comments (lines starting with ':') are required to be
        # ignored by clients — they're the canonical way to keep the
        # connection warm without adding semantic events.
        yield ": keepalive\n\n"

        messages = list(req.messages)
        # Safety cap on tool loops. Each loop = one adapter.chat() call +
        # possible tool executions. Typical use: 1-3 loops for a multi-
        # tool query. Cap at 8 to prevent runaway loops on a misbehaving
        # model (Llama 3.2 3B has been known to chain-call indefinitely).
        #
        # 0.9.6 fix — buffer the adapter's per-turn ``complete`` event
        # instead of forwarding it. Adapters emit ``complete`` at the end
        # of every chat() call, including the turn where
        # ``stop_reason='tool_use'`` (signaling "I want to call tools,
        # take over"). If we forwarded those intermediate completes the
        # client's for-await loop would break on the FIRST one — before
        # the tool ran, before the second adapter turn that produces the
        # assistant text. Tool chip ends up stuck on "running", no text
        # response, server keeps streaming into a closed consumer.
        #
        # The single visible ``complete`` event the client sees is the
        # one we emit at the very end of THIS function (or never — if the
        # body just ends after the final text events, the for-await loop
        # exits naturally, which the client also handles correctly).
        final_complete: AiStreamEvent | None = None
        for loop_n in range(8):
            pending_tool_calls: list[AiToolCall] = []
            saw_text = False
            # 1.1.4 — accumulate text content to detect the "malformed
            # tool-call as content" failure mode that under-capable local
            # models (e.g. Qwen3-4b on CPU + URSA's full 18-tool surface)
            # hit. The model emits a few characters of JSON (typically
            # `{"`) trying to write a tool-call as text, then stops. The
            # heuristic check after the loop turns this into a friendly
            # diagnostic instead of rendering the broken `{` to the user.
            accumulated_text = ""
            stop_reason: str | None = None
            errored = False

            try:
                # 0.9.4 — interleave keepalive comments between adapter
                # events so quiet stretches (the LLM thinking before its
                # first token, between tool result + next-turn response,
                # etc.) don't let intermediate proxies time out the SSE
                # connection. _with_keepalive yields an SSE-comment line
                # roughly every 5s when the adapter is quiet.
                async for line in _with_keepalive(
                    adapter.chat(
                        messages=messages,
                        tools=TOOL_DESCRIPTORS,
                        system_prompt=system_prompt,
                    ),
                    keepalive_seconds=5.0,
                ):
                    if isinstance(line, str):
                        # SSE comment line (keepalive) — pass through verbatim.
                        yield line
                        continue
                    event: AiStreamEvent = line

                    if event.event_type == "complete":
                        # Buffer — only the LAST loop's complete (or none)
                        # gets sent to the client.
                        stop_reason = event.payload.get("stop_reason")
                        final_complete = event
                        continue

                    if event.event_type == "error":
                        # Adapter errored — surface immediately and stop.
                        yield _sse_pack(event)
                        errored = True
                        return

                    yield _sse_pack(event)

                    if event.event_type == "text":
                        saw_text = True
                        accumulated_text += str(event.payload.get("text", ""))
                    elif event.event_type == "tool_call_complete":
                        pending_tool_calls.append(
                            AiToolCall(
                                id=event.payload["id"],
                                name=event.payload["name"],
                                arguments=event.payload["arguments"],
                            ),
                        )
            except asyncio.CancelledError:
                logger.info("ai/chat: client disconnected; aborting stream")
                raise

            if errored:
                return

            if not pending_tool_calls:
                # No more tools requested — the conversation is done.
                # 1.1.4 — diagnostic for the "model emitted a malformed
                # tool-call as content" failure mode. When an under-
                # capable local model (e.g. Qwen3-4b with URSA's full
                # 18-tool surface) tries to write a tool call as JSON
                # text instead of using the OpenAI tool-call format, it
                # typically emits just `{"` (or similar) and then
                # finishes with stop_reason="stop". The user sees a
                # confusing single `{` in the chat panel. Detect this
                # shape and surface a friendly diagnostic with concrete
                # next steps instead.
                content_stripped = accumulated_text.strip()
                if (
                    saw_text
                    and len(content_stripped) <= 10
                    and content_stripped.startswith("{")
                    and (stop_reason or "stop") == "stop"
                ):
                    logger.info(
                        "ai/chat: detected malformed-tool-call shape "
                        "(content=%r, stop_reason=%s); surfacing diagnostic",
                        accumulated_text, stop_reason,
                    )
                    yield _sse_pack(AiStreamEvent(
                        event_type="error",
                        payload={
                            "code": "MODEL_INCOMPLETE_RESPONSE",
                            "message": (
                                "The model returned only a partial response "
                                "(likely a failed tool-call attempt). The "
                                "configured model isn't capable enough for "
                                "URSA's tool surface. Recommended next steps: "
                                "switch to Claude API (Settings → AI "
                                "Assistant), use a larger local model "
                                "(Qwen3-30b-instruct, Llama-3.3-70b-instruct), "
                                "or run on GPU instead of CPU."
                            ),
                        },
                    ))
                    return

                # Emit the captured final complete now (if any) so the
                # client gets a clean end-of-stream signal.
                if final_complete is not None:
                    yield _sse_pack(final_complete)
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
                result = await execute_tool(
                    tc.name, tc.arguments, api_base_url, auth_header=auth_header,
                )
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


async def _with_keepalive(source, keepalive_seconds: float):
    """Yield items from an async iterator, interleaved with SSE-comment
    keepalive strings every ``keepalive_seconds`` seconds of silence.

    The yielded stream is heterogeneous:
      - ``str`` values are pre-formatted SSE keepalive comments
        (``": keepalive\\n\\n"``) — caller should yield them straight
        through without re-packing.
      - All other values are items from the source iterator (here:
        ``AiStreamEvent`` objects from a ProviderAdapter).

    Implementation: spawn the source iteration as a task that pushes
    into an asyncio.Queue. Pull from the queue with a timeout; on
    timeout, yield a keepalive instead.
    """
    queue: asyncio.Queue = asyncio.Queue()

    _SENTINEL = object()

    async def _pump():
        try:
            async for item in source:
                await queue.put(item)
        except Exception as e:
            # Propagate the error through the queue so the consumer
            # sees it in the right loop iteration.
            await queue.put(("__error__", e))
        finally:
            await queue.put(_SENTINEL)

    task = asyncio.create_task(_pump(), name="ai-chat-pump")
    try:
        while True:
            try:
                item = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
                continue
            if item is _SENTINEL:
                return
            if isinstance(item, tuple) and len(item) == 2 and item[0] == "__error__":
                raise item[1]
            yield item
    finally:
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):
                pass


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
