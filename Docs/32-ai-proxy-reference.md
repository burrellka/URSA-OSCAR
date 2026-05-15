# AI Proxy Reference

Comprehensive reference for the Phase 5 AI proxy module. Targets anyone modifying / extending the in-app chat experience, adding a new provider, or debugging an LLM that's misbehaving in the wild.

For the *systems-level* rationale (why two adapters, why server-side tool execution, why localStorage conversations), see [`31-architecture-deep-dive.md`](31-architecture-deep-dive.md). This document is the file-by-file walk through.

---

## Table of contents

1. [Module map](#1-module-map)
2. [Provider adapter contract](#2-provider-adapter-contract)
3. [The Claude adapter](#3-the-claude-adapter)
4. [The OpenAI-compat adapter](#4-the-openai-compat-adapter)
5. [Provider preset registry](#5-provider-preset-registry)
6. [Tool descriptors and executor](#6-tool-descriptors-and-executor)
7. [System prompt template](#7-system-prompt-template)
8. [Secret store and config store](#8-secret-store-and-config-store)
9. [The chat endpoint loop](#9-the-chat-endpoint-loop)
10. [Frontend chat panel](#10-frontend-chat-panel)
11. [Adding a new provider](#11-adding-a-new-provider)
12. [Adding a new tool](#12-adding-a-new-tool)
13. [Debugging](#13-debugging)

---

## 1. Module map

```
backend/src/ursa_oscar/ai_proxy/
├── __init__.py          build_adapter() — adapter factory
├── providers/
│   ├── base.py          ProviderAdapter ABC + AiMessage / AiStreamEvent
│   ├── claude.py        ClaudeAdapter — Anthropic Messages API via SDK
│   ├── openai_compat.py OpenAiCompatAdapter — 6 providers, raw httpx
│   └── presets.py       PRESETS list + get_preset + build_auth_header
├── tools.py             TOOL_DESCRIPTORS + execute_tool dispatcher
├── prompt.py            render_system_prompt + DeviceClock context
├── secrets.py           SecretStore (Fernet) + resolve_secret_key
└── config_store.py      ConfigStore (non-secret AI proxy config)

backend/src/ursa_oscar/api/ai.py
                         FastAPI routes: /providers /config /test /chat
                         Owns the multi-turn tool-call loop

frontend/src/components/AiChatPanel.tsx
                         Slide-in panel, SSE consumer, markdown render

frontend/src/pages/SettingsAi.tsx
                         /settings/ai — provider config UI
```

---

## 2. Provider adapter contract

`providers/base.py` defines:

### `AiMessage`

```python
class AiMessage(BaseModel):
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: list[AiToolCall] | None = None
    tool_call_id: str | None = None
```

Universal across providers. The adapter translates to/from this shape at the boundary. The `tool` role + `tool_call_id` carries a tool result back to the LLM.

### `AiStreamEvent`

Seven event types, all carrying a `payload: dict`:

| event_type | payload |
|---|---|
| `text` | `{"text": str}` — append to the in-flight assistant message |
| `tool_call_start` | `{"id": str, "name": str}` — assistant has begun a tool call |
| `tool_call_input` | `{"id": str, "partial_input": str}` — streaming partial JSON of tool args |
| `tool_call_complete` | `{"id": str, "name": str, "arguments": dict}` — full tool call ready to execute |
| `tool_result` | `{"id": str, "result": dict}` — server-side: tool executed, result attached |
| `complete` | `{"stop_reason": str, "usage": dict | None}` — LLM finished this turn |
| `error` | `{"message": str, "code": str}` — adapter or upstream error |

The browser-side `applyStreamEvent` reducer in `AiChatPanel.tsx` consumes these.

### `ProviderAdapter` ABC

Two methods:

```python
class ProviderAdapter(ABC):
    def __init__(self, *, api_key, endpoint, model, extra_headers): ...

    @abstractmethod
    async def chat(
        self,
        messages: list[AiMessage],
        tools: list[dict],
        system_prompt: str,
    ) -> AsyncIterator[AiStreamEvent]: ...

    @abstractmethod
    async def test_connection(self) -> ProviderTestResult: ...
```

`chat` is an async generator. `test_connection` is one-shot (used by Settings → Test).

---

## 3. The Claude adapter

`providers/claude.py` — uses the official `anthropic` Python SDK.

### Request shape

Claude's API differs from OpenAI's in three ways:

1. **System prompt is a top-level parameter**, not a role in `messages`. The adapter hoists `system_prompt` to the SDK's `system=` parameter.
2. **Tool definitions use `name` + `description` + `input_schema`** (not OpenAI's `{type: "function", function: {...}}` wrapper). The adapter translates at request time:
   ```python
   "tools": [
       {
           "name": t["function"]["name"],
           "description": t["function"]["description"],
           "input_schema": t["function"]["parameters"],
       }
       for t in tools
   ]
   ```
3. **Tool results live in `tool_result` content blocks inside user messages**, not separate role="tool" messages. The adapter assembles these blocks when serializing the conversation back to Anthropic's shape.

### Streaming events

Anthropic SDK emits typed events. The adapter pattern-matches on `event.type`:

| Anthropic event | Our normalized event |
|---|---|
| `content_block_start` with `block.type == "tool_use"` | `tool_call_start` |
| `content_block_delta` with `delta.type == "text_delta"` | `text` |
| `content_block_delta` with `delta.type == "input_json_delta"` | `tool_call_input` (also buffers for `tool_call_complete` at end of block) |
| `message_delta` with `stop_reason` | (captured for `complete` event at end of stream) |
| `message_stop` | `complete` |

### Lazy SDK import

The `anthropic` import is inside the `chat()` / `test_connection()` methods, not at module top. Reason: anywhere this module is imported in environments without the SDK installed (e.g., the MCP container's narrower deps), top-level imports would break the whole `ai_proxy` package. The lazy import lets non-Claude paths work in those environments. (In production the API container always has `anthropic` installed — it's in `pyproject.toml`.)

---

## 4. The OpenAI-compat adapter

`providers/openai_compat.py` — raw `httpx`. No SDK dependency.

Covers six provider presets through one implementation:
- OpenAI native
- Google Gemini's OpenAI-compat layer
- OpenRouter
- Groq
- Local LLM (LocalAI / Ollama / llama.cpp server / vLLM / LM Studio)
- Custom (anything else with a `/v1/chat/completions` endpoint)

### Request shape

OpenAI's `POST /v1/chat/completions` with:
- `model` — operator-supplied
- `messages` — translated from `AiMessage` (system as a role=system message, tool results as role=tool messages with `tool_call_id`, assistant tool-call messages with `tool_calls` array)
- `tools` — passed through as-is (we already use OpenAI's `{type: "function", function: {...}}` shape internally)
- `stream: true`
- `tool_choice: "auto"`

### Streaming events

Provider sends `data: {...}\n\n` SSE frames. The adapter parses each, extracts `choices[0].delta`, and emits:

| OpenAI delta | Our event |
|---|---|
| `delta.content` | `text` |
| `delta.tool_calls[i].id + function.name` (first seen) | `tool_call_start` |
| `delta.tool_calls[i].function.arguments` (each chunk) | `tool_call_input` (buffered for `tool_call_complete`) |
| `finish_reason` | (captured for `complete`) |

### Provider-specific quirks we accept

- **Gemini's compat layer** has known tool-calling reliability variance — `gemini-1.5-flash` is generally more reliable than `gemini-2.0-flash-exp`. We don't work around this in the adapter; we surface in the Settings UI notes.
- **Groq** sometimes returns usage as a separate "final" SSE chunk without `choices` — the adapter captures usage from any chunk that has it.
- **Local LLMs** sometimes omit the `id` on the first `tool_calls` delta — we set it when it arrives and emit `tool_call_start` lazily once we have both `id` and `name`.

### Tool-calling reliability across providers

The adapter contract is identical for every provider, but tool-calling *reliability* — the model's ability to route a user query to the right tool with correctly-formatted arguments — varies sharply. Approximate ranking from URSA-OSCAR Phase 5 acceptance testing:

| Provider / model class | Tool calling | Notes |
|---|---|---|
| Claude API (Sonnet 4.5+) | Excellent | Reference implementation. URSA-OSCAR's acceptance matrix uses it. |
| OpenAI GPT-4o / 4o-mini | Excellent | Equivalent to Claude in practice. |
| Anthropic Haiku family | Very good | A bit more prone to skipping a tool for chitchat queries. |
| OpenRouter (with strong backing model) | Inherits backing model's quality | Variable — pick the model carefully. |
| Groq (Llama 3.1 70B, Mixtral 8x7B) | Good | Fast, decent tool routing. |
| Local LLM — 32B+ open-weight tool-callers | Good–Very good | Qwen 2.5 32B, Llama 3.3 70B, etc. |
| Local LLM — 7-14B tool-callers | Moderate | Hermes 3 8B, Qwen 2.5 14B, Mistral Nemo 12B. |
| Local LLM — <7B models | Poor | Skip tools, hallucinate clinical facts. Plumbing test only. |
| Gemini 2.0 Flash Exp | Unreliable | Frequent malformed tool-call JSON. Prefer `1.5-flash`. |

For local-LLM selection in particular, see [`Docs/33-operator-setup-guide.md` → Recommended local models](33-operator-setup-guide.md#recommended-local-models) for a hardware-sized recommendations table and the rationale on why model size matters in a clinical context.

---

## 5. Provider preset registry

`providers/presets.py` — 7 hardcoded `ProviderPreset` entries.

```python
ProviderPreset(
    id="claude",
    label="Claude API (Anthropic)",
    adapter="claude",
    default_endpoint="https://api.anthropic.com",
    default_models=["claude-sonnet-4-5-20250929", ...],
    auth_header_name="x-api-key",
    auth_header_format="{key}",
    notes="Native Anthropic API. Best tool-calling reliability...",
    supports_local_routing=False,
)
```

The Settings UI's dropdown is populated from `GET /api/v1/ai/providers`, which returns this list verbatim. When the operator picks a provider, the UI auto-fills endpoint + first model.

`build_auth_header(preset, api_key)` returns the dict to add to outbound requests:
- `claude` → `{"x-api-key": "<key>"}`
- everyone else → `{"Authorization": "Bearer <key>"}`
- empty key → `{}` (Local LLM no-auth path)

---

## 6. Tool descriptors and executor

`tools.py` exports two things:

### `TOOL_DESCRIPTORS`

A list of 11 dicts in OpenAI function-calling shape:

```python
{
    "type": "function",
    "function": {
        "name": "get_nightly_summary",
        "description": "Return the nightly summary (AHI, pressure, leak, ...) for one date or a date range. Use when the user asks 'how was last night', ...",
        "parameters": {
            "type": "object",
            "properties": {
                "date": {"type": "string", "description": "Start date in YYYY-MM-DD."},
                "end_date": {"type": "string", "description": "Optional end date..."},
            },
            "required": ["date"],
        },
    },
}
```

**The description is critical.** LLMs choose tools by matching the user's intent against tool descriptions. The "Use when the user asks..." patterns dramatically improve routing accuracy. Don't shorten these.

### `execute_tool(name, arguments, api_base_url)`

Async function that runs a tool by name. Returns the `{ok, data, ...}` envelope.

```python
async def execute_tool(
    tool_name: str,
    arguments: dict,
    api_base_url: str = "http://127.0.0.1:8000",
) -> dict:
```

Routing is via `_TOOL_ROUTING`, a dict mapping tool name → either:
- A simple `path` + `builder` pair for GET-with-query-params shapes:
  ```python
  "list_available_nights": {
      "method": "GET",
      "path": "/api/v1/nights",
      "builder": _no_body,
  }
  ```
- A custom `router` function for shapes that need composing or argument transformation:
  ```python
  "get_nightly_summary": {
      "method": "GET",
      "router": "_route_nightly_summary",
  }
  ```

Custom routers compose multiple underlying API calls. For example, `_route_ahi_breakdown` calls `/night/{date}` + `/events?date=...` and synthesizes a response with TECSA-likely heuristic computation — that's not a standalone API endpoint (yet), so the composition lives in the tool router.

Failures wrap into envelope shape:

```python
{ok: false, code: "INVALID_INPUT", error: "..."}
{ok: false, code: "NOT_FOUND", error: "..."}
{ok: false, code: "NETWORK_ERROR", error: "..."}
{ok: false, code: "UPSTREAM_ERROR", error: "..."}
{ok: false, code: "INTERNAL_ERROR", error: "..."}
```

The LLM sees the envelope and can decide what to tell the user. No exceptions raise out of `execute_tool` to the chat-loop level.

---

## 7. System prompt template

`prompt.py:render_system_prompt(user_profile, device_clock, today_date, current_view, custom_template)`.

The default template:

```
You are URSA, the user's dedicated sleep and health agent embedded in URSA-OSCAR. You have access to the user's CPAP analytics data via the tools below.

## User context
{user_profile_summary}

## Device clock context
{device_clock_description}

When the user asks about "last night" or "this morning", resolve those references in the user's local time zone, then convert to device-clock time when querying tools that expect a date.

## Operating principles
- Be direct. BLUF format: bottom line first, reasoning second.
- Cite specific data from tool calls. Don't speculate when data is available.
- When uncertain, say so.
- The user is medically literate. Use clinical terminology.
- You are not a substitute for medical advice. Note this only when the user asks something that genuinely warrants a doctor's input (e.g., new symptom, prescription change).
- Tool calls are visible to the user, so use them confidently when they're the right answer — don't try to recall data from memory.

Today's date (user frame): {today_date}
Current viewing: {current_view_context}
```

### Sub-renderers

`_summarize_user_profile(profile)` reads `UserProfile.clinical` and emits a compact bullet list:

```
- **Diagnoses:** Obstructive Sleep Apnea, TECSA
- **Active medications:** Doxepin, Melatonin
- **Treatment goals:** AHI < 5
- **Allergies:** none on file
```

Empty fields are skipped — the LLM doesn't see "Diagnoses: (none)" clutter.

`_describe_device_clock(device_clock)` emits one of three sentences depending on `mode`:

- `matches_local`: "The user's CPAP device's clock matches their local wall-clock time. No timestamp shift is needed."
- `static_offset`: "The user's CPAP device records timestamps in a fixed UTC offset of -5.0 hours. The UI auto-adjusts for DST. When the user says 'last night', that's in their browser's local time; URSA-OSCAR applies the offset to render. Tool queries that take a date should use the date the DEVICE wrote (which is what's in the DB)."
- `manual`: "The user has set a manual +60-minute display offset. Stored timestamps are device-clock; the UI shifts on display."

### Custom template support

Operators can override the entire template via Settings → AI Assistant → System prompt. The same `{placeholder}` substitution works; unknown placeholders are left as literal text (via the `_format_lenient` helper) rather than raising — so a typo doesn't crash the chat endpoint.

---

## 8. Secret store and config store

### `SecretStore` (`secrets.py`)

Fernet-encrypted key/value store backed by a single JSON file (`/data/secrets.enc`).

```python
class SecretStore:
    def __init__(self, key: bytes, store_path: Path): ...
    def get(self, key: str) -> Optional[str]: ...
    def set(self, key: str, value: str) -> None: ...    # value="" deletes
    def delete(self, key: str) -> None: ...
    def list_keys(self) -> list[str]: ...               # never returns values
    def has(self, key: str) -> bool: ...
```

Keys are named `<provider_id>_api_key` (e.g., `claude_api_key`, `openai_api_key`). The Settings UI shows `api_key_set: bool` per provider in the masked config response; the raw value never round-trips to the browser.

### `resolve_secret_key(data_dir)`

The first-start flow lives here:

```python
def resolve_secret_key(data_dir: Path) -> bytes:
    raw = os.environ.get("URSA_OSCAR_SECRET_KEY", "").strip()
    if raw:
        # Validate by passing through Fernet()
        return raw.encode("ascii")
    
    # First-start: generate + log + write to /data/secret_key.gen
    key = Fernet.generate_key()
    (data_dir / "secret_key.gen").write_bytes(key)
    os.chmod(..., 0o600)
    logger.warning(
        "URSA_OSCAR_SECRET_KEY is unset. Generated a fresh Fernet key "
        "and wrote it to %s. Copy this value into your compose env as "
        "URSA_OSCAR_SECRET_KEY=<value>, then delete %s.", ..., ...
    )
    return key
```

Called once in the API's lifespan. Re-calling within the same process returns the same key.

### `ConfigStore` (`config_store.py`)

Plain JSON file backing for `AiProxyConfig`:

```python
class AiProxyConfig(BaseModel):
    enabled: bool = False
    provider_id: str | None = None
    model: str = ""
    endpoint_url: str = ""
    routing_mode: str = "direct"  # "direct" | "proxy"
    proxy_endpoint_url: str | None = None
    custom_system_prompt: str | None = None
```

Two stores — encrypted (`secrets.enc`) for keys, plain (`ai_config.json`) for config — because most config edits don't involve secrets, and roundtripping the whole encrypted blob for every field tweak is wasteful.

---

## 9. The chat endpoint loop

`api/ai.py:chat` — the most complex single function in URSA-OSCAR. Walks the multi-turn tool-call loop:

```python
@router.post("/chat")
async def chat(req: ChatRequest, request: Request):
    # 1. Load config + decrypt API key + build adapter
    config = config_store.load()
    if not config.enabled: raise HTTPException(400, ...)
    api_key = secrets.get(f"{config.provider_id}_api_key")
    adapter = build_adapter(config.provider_id, config.model_dump(), api_key)

    # 2. Render system prompt with profile + DeviceClock context
    profile = _load_profile(request)
    system_prompt = render_system_prompt(
        user_profile=profile if req.context.include_profile else None,
        device_clock=(profile or {}).get("display", {}).get("device_clock"),
        today_date=date.today(),
        current_view=...,
        custom_template=config.custom_system_prompt,
    )

    # 3. Loopback URL from request scope (CRITICAL: not hardcoded
    #    127.0.0.1:8000 — see 0.9.1 fix in handover)
    server = request.scope.get("server") or ("127.0.0.1", 8000)
    api_base_url = f"http://{server[0]}:{server[1]}"

    # 4. The actual loop
    async def event_generator():
        messages = list(req.messages)
        for loop_n in range(8):  # safety cap
            pending_tool_calls = []
            async for event in adapter.chat(messages, TOOL_DESCRIPTORS, system_prompt):
                yield _sse_pack(event)
                if event.event_type == "tool_call_complete":
                    pending_tool_calls.append(AiToolCall(...))
                # complete / error: fall through
            
            if not pending_tool_calls:
                return  # LLM finished without requesting tools
            
            # Append assistant's tool-call turn into conversation
            messages.append(AiMessage(role="assistant", content="", tool_calls=pending_tool_calls))
            
            # Execute each tool, append result, emit tool_result SSE event
            for tc in pending_tool_calls:
                result = await execute_tool(tc.name, tc.arguments, api_base_url)
                yield _sse_pack(AiStreamEvent(event_type="tool_result", payload={"id": tc.id, "result": result}))
                messages.append(AiMessage(role="tool", tool_call_id=tc.id, content=json.dumps(result)))
            
            # Loop back; adapter sees the tool results on next iteration
        else:
            yield _sse_pack(AiStreamEvent(event_type="error", payload={
                "message": "Tool-call loop limit reached (8 iterations)...",
                "code": "tool_loop_limit",
            }))

    return StreamingResponse(event_generator(), media_type="text/event-stream", headers={...})
```

### The 8-iteration cap

Some small models chain-call tools indefinitely (we've observed Llama 3.2 3B doing this). The cap prevents the chat from running forever (and burning tokens) — surfaces as a clean `error` event the UI can render.

### The `X-Accel-Buffering: no` header

Set on the `StreamingResponse` headers. Tells nginx (which proxies between web container and API container) to NOT buffer the response. Without it, the browser doesn't see anything until the LLM finishes. With it, chunks arrive in real-time.

---

## 10. Frontend chat panel

`frontend/src/components/AiChatPanel.tsx` is the consumer side.

### SSE consumer (`api/client.ts`)

The browser doesn't use `EventSource` (it doesn't support POST bodies). Instead:

```typescript
chatStream: async function* (messages, context, signal) {
    const resp = await fetch(`${BASE}/ai/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
        body: JSON.stringify({ messages, context }),
        signal,
    });
    const reader = resp.body.getReader();
    const decoder = new TextDecoder('utf-8');
    let buffer = '';
    while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let nl;
        while ((nl = buffer.indexOf('\n\n')) !== -1) {
            const frame = buffer.slice(0, nl);
            buffer = buffer.slice(nl + 2);
            if (!frame.startsWith('data: ')) continue;
            try {
                yield JSON.parse(frame.slice(6)) as AiStreamEvent;
            } catch {}
        }
    }
}
```

The buffer-and-split pattern handles SSE frames arriving in arbitrary network-packet chunks. Each `data: <json>\n\n` becomes one yielded event.

### Stream-event reducer (`applyStreamEvent`)

Each event updates the *last* message in the messages array (the in-flight assistant turn):

```typescript
case 'text':
    msg.content = msg.content + payload.text;
case 'tool_call_start':
    msg.tool_calls.push({ id, name, status: 'running' });
case 'tool_call_complete':
    // update tc with parsed arguments
case 'tool_result':
    // set tc.status = 'complete' or 'error', attach result_summary
```

The `result_summary` field is a one-line per-tool summary generated by `summarizeToolResult(name, args, result)` — keeps the visible chip compact while the expandable details show the full envelope.

### localStorage persistence

```typescript
function chatKey(date: string | null): string {
    return `ursa_oscar_chat_${date || 'overview'}`;
}

// On open / date change:
const raw = localStorage.getItem(chatKey(currentDate));
setMessages(JSON.parse(raw)?.messages || []);

// On every message update:
localStorage.setItem(chatKey(currentDate), JSON.stringify({
    date: currentDate,
    messages,
    updated_at: new Date().toISOString(),
}));
```

Per-date scoping means each Daily View has its own conversation thread. Switching to a different night clears the visible chat but the prior thread is retrievable by switching back. There's no UI to browse all conversations — the architect explicitly deferred that to Phase 6+ (see Phase 5 Decision 5).

---

## 11. Adding a new provider

If the new provider speaks OpenAI's `/v1/chat/completions` format (most do):

1. Append a `ProviderPreset(...)` to `PRESETS` in `providers/presets.py`. Specify:
   - `id` — short slug
   - `label` — Settings UI dropdown text
   - `adapter` — `"openai_compat"` (or `"claude"` for native Anthropic)
   - `default_endpoint` — what fills the field on dropdown change
   - `default_models` — datalist options
   - `auth_header_name` + `auth_header_format` — usually `"Authorization"` + `"Bearer {key}"`
   - `notes` — operator-facing description; explain quirks (tool-calling reliability, rate limits, etc.)
   - `supports_local_routing` — only true for the Local LLM preset
2. Update `test_seven_presets_registered` in `test_ai_proxy.py` to expect the new count.
3. Optional: if the provider has unusual SSE behavior (extra event types, weird usage chunks), special-case in the adapter's `_consume_sse`. Most OpenAI-compat providers don't need this.

If the provider uses a fundamentally different protocol (e.g., a future native Mistral API):

1. Create `providers/<name>.py` with a `<Name>Adapter(ProviderAdapter)` subclass.
2. Implement `chat()` and `test_connection()`.
3. Add to `build_adapter()` in `ai_proxy/__init__.py`:
   ```python
   if preset.adapter == "claude":
       cls = ClaudeAdapter
   elif preset.adapter == "your_adapter":
       cls = YourAdapter
   else:
       cls = OpenAiCompatAdapter
   ```
4. Tests in `test_ai_proxy.py`.

---

## 12. Adding a new tool

See [`30-developer-guide.md` §12](30-developer-guide.md#12-adding-a-new-feature) for the cross-container flow. Quick summary specific to the AI proxy side:

1. Append a descriptor to `TOOL_DESCRIPTORS` in `tools.py`:
   ```python
   {
       "type": "function",
       "function": {
           "name": "your_tool_name",
           "description": "What the tool does. Use when the user asks ...",
           "parameters": { ... JSON Schema ... },
       },
   }
   ```
2. Add a route entry to `_TOOL_ROUTING`:
   - For simple GETs: `{"method": "GET", "path": "/api/v1/your-endpoint", "builder": _no_body}`
   - For shapes that need composing: `{"method": "GET", "router": "_route_your_tool"}` + a custom router async function
3. Update tests:
   - `test_eleven_tool_descriptors` count
   - `test_tool_descriptors_have_descriptions` (length check)
   - Add a round-trip test against the live fixture if the dispatcher logic is non-trivial

---

## 13. Debugging

### "AI Assistant is disabled" 400

The chat endpoint guards against accidental traffic when AI isn't configured. Operator needs to:
1. Settings → AI Assistant
2. Pick a provider
3. Paste an API key
4. Check **Enable AI Assistant**
5. Save

### "Configured provider_id '...' is not in the registry"

The provider id stored in `ai_config.json` doesn't match any preset. This shouldn't happen in normal use (the Settings UI only writes ids from the registry). If it does, edit `/data/ai_config.json` directly to set `provider_id` to a valid value, or click "Disabled" in Settings and re-configure.

### Tool routes correctly but tool result is `{ok: false}`

Three common causes:
- The API endpoint behind the tool is down/erroring — check API container logs
- The tool's `_TOOL_ROUTING` entry has the wrong path — check `tools.py`
- For composed routers: one of the underlying API calls failed — the router logs the failure

The smoke test `test_claude_live_smoke.py` caught this exact class of bug pre-deploy. Pattern: when an end-to-end test fails on a tool result while the LLM picks the right tool with right args, the bug is almost always in the tool executor's path resolution.

### Stream ends without a `complete` event

Means the adapter raised partway through. Look for `error` events earlier in the stream (the browser-side consumer logs them to console). Most common cause:
- Upstream auth failure (401/403) → adapter emits `error` with `code: "unauthorized"`
- Rate limit (429) → `code: "rate_limit"`
- Bad model name (400) → `code: "bad_request"`

### Conversation looks weird after page refresh

`localStorage` quota in browsers is typically 5-10 MB per origin. Long conversations with large tool results can fill it. The chat panel doesn't enforce a quota; if you're hitting weird state, clear the conversation via the trash icon (which calls `localStorage.removeItem(chatKey(date))`).

### Tool loop hits the 8-iteration cap

The model is chain-calling tools without converging on an answer. Common with small local models (Llama 3.2 3B). The UI surfaces the `tool_loop_limit` error and the operator can retry with a stronger model.

### Live smoke test

Re-run any time you want to validate the full end-to-end pipeline:

```bash
cd backend
set CLAUDE_API_KEY_LIVE=sk-ant-...
pytest tests/smoke/test_claude_live_smoke.py -v -s
```

The `-s` flag shows the full SSE event stream as it arrives — useful for spotting tool-call routing issues.
