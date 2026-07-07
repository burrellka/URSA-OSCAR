# What URSA sends to the AI model on every turn

This page explains, exactly and honestly, what data URSA-OSCAR puts into the model's context window when you send a chat message. If you're running a local LLM with a limited context (Gemma-4, Qwen3, DeepSeek-R1, etc.), this is what you need to understand to reason about whether your context budget will fit — and where to trim if it won't.

Nothing described here is secret or hard-coded away from you. Everything on this page is derived from three files in the codebase: `backend/src/ursa_oscar/ai_proxy/prompt.py`, `backend/src/ursa_oscar/ai_proxy/tools.py`, and the two adapter files under `backend/src/ursa_oscar/ai_proxy/providers/`. Read them if you want the byte-level truth; this page is the human-readable summary.

## What one chat request contains

Every message you send from the chat panel triggers exactly one HTTP request to your configured provider. That request carries five things:

1. **The system prompt** — instructions for the model about what URSA is, how to behave, what tools exist, what safety patterns apply.
2. **Runtime context blocks** — your profile and device-clock context, interpolated into the system prompt at chat-session start.
3. **The tool descriptors** — JSON Schema for all 15 tools the model can call, with per-tool descriptions telling it when to invoke each.
4. **The conversation history** — every prior turn in the current chat panel session (user + assistant + tool-call + tool-result messages), sent in order.
5. **Your new message** — the latest thing you typed.

The provider streams the response back. If the model calls a tool, URSA executes the tool locally and adds two more messages to the history (the assistant's tool-call message + the tool's result message) before making a *second* request with the same shape. Multi-tool-call conversations grow the history quickly for the same reason.

## Token accounting (approximate)

Rule of thumb: **1 token ≈ 4 characters** for English prose, less for JSON (~3.5). Numbers below use 4 chars/token; JSON blocks are slightly denser.

### 1. System prompt template

Location: `backend/src/ursa_oscar/ai_proxy/prompt.py` — the `DEFAULT_TEMPLATE` constant.

- Size: ~14 KB → **~3,500 tokens**
- Contents: role framing, communication style, safety patterns, statistical-confidence handling, prediction-and-counterfactual patterns, PDF-report guidance, help-system routing, safety guardrails, don't-do list.

The operator can replace this entirely via **Settings → AI Assistant → Custom system prompt** or save a new default at `/data/system_prompt_template.txt` via the "Save to template" button. If the operator's custom prompt is shorter, the whole context bill shrinks proportionally.

### 2. Runtime context blocks (interpolated into the system prompt)

Rendered once at chat-session start, then cached for the whole session:

| Block | Source | Typical size | When it's set |
|---|---|---|---|
| User profile summary | `Profile` page: diagnoses, active medications, treatment goals, allergies, notes | ~100-400 tokens | Whatever you put in Profile |
| Device clock description | Device-clock offset config | ~40-100 tokens | Static per user |
| Today's date + current viewing context | Client-injected: `{today_date}` and `{current_view_context}` | ~30 tokens | Every session |

Total for a typical user with a modest profile: **~200-500 tokens** added to the 3,500-token template.

### 3. Tool descriptors (JSON Schema, 15 tools)

Location: `backend/src/ursa_oscar/ai_proxy/tools.py` — the `TOOL_DESCRIPTORS` list.

- Size: **~5,300 tokens** at the moment (15 tools × ~350 tokens each including name, description, JSON parameter schema).
- Sent with **every** request in the conversation, not just the first.
- Cloud providers (Anthropic, OpenAI) apply prompt caching where the tools block is cached on their end for repeated conversation turns — no cost saving on your local LLM's context window though; the model still has to read the tokens.

The full list of tools: `get_nightly_summary`, `get_ahi_breakdown`, `get_pressure_profile`, `get_leak_profile`, `get_event_distribution_by_hour`, `list_available_nights`, `get_trend`, `compare_periods`, `analyze_correlation`, `analyze_lag_correlation`, `analyze_multivariate_correlation`, `analyze_prediction`, `generate_report`, `get_manual_log_summary`, `get_user_profile`, `get_help_topic`.

### 4. Conversation history

Every prior turn in the current chat session goes into the request, in order:

| Message type | Typical size | Notes |
|---|---|---|
| Your text message | 20-200 tokens | Whatever you typed |
| Assistant text reply | 200-1,000 tokens | Depends on model verbosity + your BLUF-style system-prompt tuning |
| Tool call (assistant emits) | 100-400 tokens | Function name + JSON arguments |
| Tool result (URSA feeds back) | 200-3,000+ tokens | Depends on the tool — trend data over 30 nights is bigger than a single-night summary |

**Tool results are the biggest single expense.** A `get_trend` result across 90 nights returns per-night data; a `generate_report` result is small (just a download URL + metadata); an `analyze_multivariate_correlation` result includes bootstrap CIs for every predictor. Look at `tools.py` result envelopes if you want exact numbers.

Assistant's chain-of-thought (the `reasoning` content stream from thinking-mode models) is **not** replayed on subsequent turns — URSA strips it from the conversation history precisely so it doesn't burn context on later requests. Only the final `text` content becomes part of the history.

### 5. Your new message

20-200 tokens typically. Ranges up to ~2,000 tokens if you paste a lot of clinical text.

## Total context budget by request number

Assuming a modest profile, standard tool descriptors, and typical assistant reply lengths:

| Turn | Approx. total input tokens sent to the model |
|---|---|
| 1st request (fresh chat) | 3,500 (prompt) + 500 (profile) + 5,300 (tools) + 100 (your message) = **~9,400** |
| 2nd request (after one round-trip) | ~9,400 + 1,000 (assistant's reply) + 100 (your reply) = **~10,500** |
| 5th request (typical steady-state) | ~13,000-15,000 depending on how many tool calls fired |
| 10th request with heavy tool use | 20,000-30,000 |

**For an operator running Gemma-4 26B B4 MoE on the homelab** — which typically ships with a 32K or 128K context window depending on the quantization — you have real headroom for casual chats. Extended sessions with multiple tool calls per turn can approach the 32K bound within a dozen turns.

## Where to cut context if you're running a smaller model

In descending order of savings:

1. **Shorten the system prompt.** Delete the sections you don't need — the safety-patterns block, the PDF-report section, the help-system routing section. Each section is ~200-500 tokens. Cutting three or four sections you don't use can drop the base prompt by 1,500-2,000 tokens. Do this via Settings → AI Assistant → Custom system prompt.
2. **Start fresh conversations.** URSA doesn't limit history length — every turn stays in until you close the chat panel. Start a new chat when a session gets long.
3. **Ask fewer questions per turn.** Multi-part questions ("what's my trend AND what happened last night AND run a correlation") trigger multiple tool calls in one round, each of which adds a call+result pair to the history.
4. **Prefer summary-level tools over dense ones.** `get_nightly_summary` for one night is 200-500 tokens; `get_trend` over 90 nights can be 2,000+ tokens. Ask for a specific window, not "everything."
5. **Cut tool descriptors you never use.** Requires a code change (comment out entries in `tools.py`). Not possible from Settings today; if you use only the summary + AHI tools, cutting the other 13 saves ~4,600 tokens per request. If this becomes routine we'll add an operator-configurable subset.

## What is NOT in the request

To calibrate expectations against fear:

- **Your entire nightly database.** URSA never sends raw imported EDF data or the DuckDB tables. Tool calls query specific slices on demand.
- **Prior chat sessions.** Each chat panel session is independent — closing the panel and reopening starts fresh.
- **Any URSA log, metric, or telemetry.** URSA has no telemetry; nothing about your operator-side activity leaves the box unless a tool call explicitly puts it into the model's context.
- **The URSA-OSCAR source code.** The help system content is queryable via `get_help_topic` — it lands in the response, not the request, and only when the model chose to call the tool.
- **Anthropic-style prompt-caching metadata for non-Claude providers.** OpenAI's cache markers are sent; local LLMs simply see the tokens.

## Timeouts

The AI proxy's HTTP read timeout is configurable per provider at **Settings → AI Assistant → Request timeout**. Defaults are 300 seconds (5 minutes) for Local LLM providers and 120 seconds (2 minutes) for cloud providers. Local defaults are longer because thinking-mode models on CPU can spend 60-180 seconds on the chain-of-thought before emitting the first content token; the timeout is measured from connect, not from the last byte received.

If you hit "network_error" or a hung request against a local LLM, first check whether the model is still generating (LocalAI logs, `nvidia-smi`, etc.) before increasing the timeout. A model that's still working can be given more time; a model that's stuck won't complete faster with a longer timeout.

## Reading the source

If you want to verify any claim on this page byte-for-byte:

- **System prompt template**: `backend/src/ursa_oscar/ai_proxy/prompt.py` → `DEFAULT_TEMPLATE`
- **What the profile injection looks like**: same file → `_summarize_user_profile`
- **Tool descriptors**: `backend/src/ursa_oscar/ai_proxy/tools.py` → `TOOL_DESCRIPTORS`
- **Adapter request shaping** (OpenAI-compat): `providers/openai_compat.py` → `_build_request`
- **Adapter request shaping** (Claude): `providers/claude.py` → `_build_request`

The template you actually get is what's in the source; there's no additional layer that adds hidden instructions. Custom-prompt overrides replace the template wholesale.
