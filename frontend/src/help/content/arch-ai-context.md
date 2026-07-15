# What URSA sends to the AI model on every turn

This page explains, exactly and honestly, what data URSA-OSCAR puts into the model's context window when you send a chat message. If you're running a local LLM with a limited context (Gemma-4, Qwen3, DeepSeek-R1, etc.), this is what you need to understand to reason about whether your context budget will fit — and where to trim if it won't.

Nothing described here is secret or hard-coded away from you. Everything on this page is derived from five files in the codebase: `backend/src/ursa_oscar/ai_proxy/prompt.py`, `backend/src/ursa_oscar/ai_proxy/tools.py`, `backend/src/ursa_oscar/ai_proxy/tool_index.py`, `backend/src/ursa_oscar/ai_proxy/tool_prepass.py`, and the two adapter files under `backend/src/ursa_oscar/ai_proxy/providers/`. Read them if you want the byte-level truth; this page is the human-readable summary.

## Progressive tool disclosure (as of 1.1.12)

Prior to 1.1.12, URSA shipped **all 15 tool schemas on every turn** — a fixed ~5,300-token tax against the model's context window regardless of what the user actually asked. From 1.1.12 forward, URSA tiers tools into a small always-on **core** set and larger **deferred** groups the model activates on demand. The per-turn tool tax drops from ~5,300 tokens to ~1,000-1,500 tokens for typical conversations.

Three mechanisms working together:

1. **Core tools** (always in the catalog every turn): `get_nightly_summary`, `get_user_profile`, and `load_tools` — 3 tools, ~1,000 tokens.
2. **Deferred groups** summarized as a compact `AVAILABLE TOOLS` index injected into the system prompt: one line per group listing member tool names, ~300-500 tokens total (much cheaper than shipping the full schemas). Groups today: `analytics` (5 tools), `trends` (2), `advanced-analysis` (4), `reports` (1), `logs` (1).
3. **`load_tools`** — a core discovery tool the model calls with either group keys (`groups: ["analytics"]`) or specific names (`names: ["get_ahi_breakdown"]`). URSA splices the requested schemas into the live catalog so they're callable on the next model step.

**Lexical pre-pass**: to avoid burning a round-trip on obvious intents, URSA runs a cheap deterministic keyword match against the user's message BEFORE the first model call. If the pre-pass finds a strong signal ("show me my AHI trend", "correlate pressure with leak"), it pre-activates up to 2 matching groups so the model just uses them. `load_tools` remains the fallback for everything the pre-pass misses.

Pattern lifted from KAIROS's progressive-tool-disclosure spec (see the KAIROS `proxy/src/core/tool_index.py` and `tool_prepass.py` for the sibling implementation). Vitals converged on the same pattern independently.

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

### 3. Tool descriptors + AVAILABLE TOOLS index (1.1.12 progressive disclosure)

Location: `backend/src/ursa_oscar/ai_proxy/tools.py` (`TOOL_DESCRIPTORS`, `TOOL_META`) + `backend/src/ursa_oscar/ai_proxy/tool_index.py` (index render + resolver).

Per-turn size (typical): **~1,000-1,500 tokens** — a huge improvement over the pre-1.1.12 ~5,300 tokens.

- **Core catalog (always sent)**: 3 tools, ~1,000 tokens
  - `get_nightly_summary` — the "how was last night" workhorse (grounds most CPAP questions)
  - `get_user_profile` — clinical context (diagnoses, medications, treatment goals)
  - `load_tools` — the discovery tool for activating deferred groups
- **AVAILABLE TOOLS index (system prompt block, always sent)**: ~300-500 tokens for 13 deferred tools across 5 groups. One line per group with the group key + friendly label + comma-separated tool names. Compare to shipping the 13 full schemas (~4,600 tokens): the index is ~10× cheaper.
- **Additional deferred schemas the model has activated this conversation**: grows as needed. Once a group is activated (via pre-pass OR the model calling `load_tools`), its schemas ride every subsequent turn until the chat panel is closed. Loading a small group (`logs`, `reports`) adds ~300-500 tokens; loading `advanced-analysis` (4 tools with dense schemas) adds ~1,500-2,000 tokens.

**Deferred groups**:
- `analytics` (5): `get_ahi_breakdown`, `list_available_nights`, `get_event_distribution_by_hour`, `get_pressure_profile`, `get_leak_profile`
- `trends` (2): `compare_periods`, `get_trend`
- `advanced-analysis` (4): `analyze_correlation`, `analyze_multivariate_correlation`, `analyze_lag_correlation`, `analyze_prediction`
- `reports` (1): `generate_report`
- `logs` (1): `get_manual_log_summary`

Cloud providers (Anthropic, OpenAI) still apply prompt caching where the whole system prompt + tools block is cached on their end for repeated turns — that's a wall-clock and cost savings on their side. Local LLMs don't get that; the tokens are re-read every turn. Progressive disclosure benefits both, but the felt-latency win is much bigger on local.

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

Assuming a modest profile, 1.1.12's progressive disclosure, and typical assistant reply lengths:

| Turn | Approx. total input tokens sent to the model |
|---|---|
| 1st request (fresh chat, no pre-pass hit) | 3,500 (prompt) + 500 (profile) + 1,000 (core tools) + 400 (index) + 100 (your message) = **~5,500** |
| 1st request with pre-pass hit (one group activated) | ~5,500 + ~500 (one group's schemas) = **~6,000** |
| 2nd request (after one round-trip) | ~5,500 + 1,000 (assistant's reply) + 100 (your reply) = **~6,600** |
| 5th request (steady-state, `analytics` + `trends` loaded) | ~8,000-10,000 depending on how many tool calls fired |
| 10th request with all groups loaded + heavy tool use | 15,000-20,000 |

**Pre-1.1.12 numbers, for comparison**: 1st request was ~9,400 tokens (before progressive disclosure); 10th request was 20,000-30,000. The 1.1.12 architecture roughly cuts the fixed per-turn tax in half for typical conversations and much more for short ones.

**For an operator running Gemma-4 26B B4 MoE on the homelab** — which typically ships with a 32K or 128K context window depending on the quantization — you have generous headroom now. Extended sessions with multiple tool calls per turn no longer approach the 32K bound within a dozen turns; you can chat for much longer before context pressure matters.

## Where to cut context if you're running a smaller model

In descending order of savings:

1. **Shorten the system prompt.** Delete the sections you don't need — the safety-patterns block, the PDF-report section, the help-system routing section. Each section is ~200-500 tokens. Cutting three or four sections you don't use can drop the base prompt by 1,500-2,000 tokens. Do this via Settings → AI Assistant → Custom system prompt.
2. **Start fresh conversations.** URSA doesn't limit history length — every turn stays in until you close the chat panel. Start a new chat when a session gets long. This also clears any groups the pre-pass or `load_tools` activated during the prior session, so you return to the ~5,500-token baseline.
3. **Ask fewer questions per turn.** Multi-part questions ("what's my trend AND what happened last night AND run a correlation") trigger multiple tool calls in one round, each of which adds a call+result pair to the history AND typically activates multiple deferred groups at once.
4. **Prefer summary-level tools over dense ones.** `get_nightly_summary` for one night is 200-500 tokens; `get_trend` over 90 nights can be 2,000+ tokens. Ask for a specific window, not "everything."
5. **Be specific in your first message.** The pre-pass activates groups based on keyword hits. A vague opener ("tell me about my sleep") won't activate anything specific — the model has to call `load_tools` explicitly, costing a round-trip. A specific opener ("show me my AHI trend for the last month") lets the pre-pass activate `analytics` + `trends` before the first model call.
6. **Cut tool descriptors you never use.** Requires a code change (edit `TOOL_META` in `tools.py` to move a tool out of a group or delete its descriptor). If this becomes routine we could add operator-configurable disclosure to Settings.

## What is NOT in the request

To calibrate expectations against fear:

- **Your entire nightly database.** URSA never sends raw imported EDF data or the DuckDB tables. Tool calls query specific slices on demand.
- **Prior chat sessions.** Each chat panel session is independent — closing the panel and reopening starts fresh.
- **Any URSA log, metric, or telemetry.** URSA has no telemetry; nothing about your operator-side activity leaves the box unless a tool call explicitly puts it into the model's context.
- **The URSA-OSCAR source code.** The help system content is queryable via `get_help_topic` — it lands in the response, not the request, and only when the model chose to call the tool.
- **Anthropic-style prompt-caching metadata for non-Claude providers.** OpenAI's cache markers are sent; local LLMs simply see the tokens.

## Stable-prefix caching (as of 1.1.13)

llama.cpp / LocalAI (and Anthropic at the API level) reuse the KV / prefix cache across requests when the leading run of tokens is byte-identical. The cache matches from the start and stops at the first differing byte, so anything volatile at the front of the system prompt breaks the cache for everything behind it.

URSA's system prompt is now assembled as:

```
[stable persona + instructions + user profile + device clock + today's date]
+ [stable AVAILABLE TOOLS index]
+ [volatile: "Current viewing: ..."]
```

Everything before the volatile tail is byte-identical turn-to-turn within a session. On turn 2, llama.cpp reuses the entire cached prefix (~3,500-token DEFAULT_TEMPLATE + ~400-token AVAILABLE TOOLS index + profile + date) and only reprocesses the ~50-byte "Current viewing" tail plus your new message. On a Gemma-4 CPU box that's the difference between a 20-second first-token-latency and a sub-2-second one on turn 2.

**Two conditions have to hold for the win to land:**

1. **The prefix has to actually be byte-stable** — URSA's `render_system_prompt_parts()` guarantees this; the guarantee is locked in by `backend/tests/unit/test_stable_prefix_caching.py`. If a future PR reintroduces a per-turn timestamp (clock, uuid, live location) into the stable half, that test regresses.
2. **The inference engine has to have cross-request prefix reuse turned on.** For LocalAI this is a server-config concern, not a URSA concern. Confirm on the box — the flag is usually `--parallel` + slot cache enabled, or the `promptcache` option in LocalAI's config.

If you have the code side working but no latency improvement on turn 2, the engine's prefix cache is likely off.

**What is and isn't in the volatile suffix:**
- `Current viewing: Daily View 2026-07-06` — changes when the operator navigates. This IS in the volatile suffix.
- Today's date (`2026-07-08`) — a per-day value is byte-stable across all turns of a single chat session (it only recomputes at midnight, at which point a single re-cache event is fine). This is NOT in the volatile suffix; it lives in the stable prefix.
- Nothing else. If a future release adds a per-turn signal (e.g., live CPAP session status, a "generated at" timestamp), it must be threaded into `VOLATILE_SUFFIX_TEMPLATE`, not the DEFAULT_TEMPLATE body.

Pattern reference: KAIROS's `docs/stable-prefix-caching-for-sibling-devs.md` (D74). URSA's implementation is a straight adoption; Vitals is expected to converge.

## Timeouts

The AI proxy's HTTP read timeout is configurable per provider at **Settings → AI Assistant → Request timeout**. Defaults are 300 seconds (5 minutes) for Local LLM providers and 120 seconds (2 minutes) for cloud providers. Local defaults are longer because thinking-mode models on CPU can spend 60-180 seconds on the chain-of-thought before emitting the first content token; the timeout is measured from connect, not from the last byte received.

If you hit "network_error" or a hung request against a local LLM, first check whether the model is still generating (LocalAI logs, `nvidia-smi`, etc.) before increasing the timeout. A model that's still working can be given more time; a model that's stuck won't complete faster with a longer timeout.

## The output-token cap and the empty-answer trap (as of 1.1.14)

The counterpart to the read timeout is the **output-token cap** — the ceiling on how many tokens the model may generate for one answer. Configure it at **Settings → AI Assistant → Max output tokens**. Defaults: **4000 for Local LLM**; **the provider's own default for cloud** (blank — URSA doesn't impose a cap, so a legitimately long cloud answer isn't truncated). Claude's own default stays 4096, overridable.

Why local *must* have a generous cap: reasoning-mode models (Gemma-4, Qwen3, DeepSeek-R1) stream a hidden **reasoning channel** — chain-of-thought — that shares the *same* output budget as the answer, and the model spends it *before* the first answer token. If the cap is too small, the round hits the limit mid-thought (`finish_reason = length`) and **the answer never starts** — you get an HTTP 200 with a blank bubble. It looks random (a longer reasoning spike on one question, not another) but it's deterministic budget pressure, and a fat tool result makes it worse (more to reason about → longer reasoning). Before 1.1.14 URSA sent *no* `max_tokens` on local calls at all, so whatever small default your LocalAI/llama.cpp server applied was the ceiling.

Three layers guard against it now:

1. **A generous cap is always sent to local servers** (4000 by default) so reasoning + answer both fit. If you still see blank or cut-off answers, raise it.
2. **The per-turn line flags `⚠ truncated`** whenever `finish_reason = length`, so a truncation is never silent. If you see it, raise Max output tokens (or lower the model's reasoning effort at the LocalAI side — a reasoning-budget knob caps the thinking at the source, the highest-leverage fix if your endpoint exposes it).
3. **Reasoning-as-answer fallback**: if a turn finishes with only a reasoning trail and no answer text, URSA renders the reasoning as the answer instead of a blank bubble — partial thinking beats nothing.

## Per-turn observability (as of 1.1.14)

You can't cut what you can't see. Every completed assistant turn now shows a small **per-turn line** underneath it:

```
~5,657p + 775c  ·  26.3s  ·  gemma-4-26b-a4b   ⚠ truncated
```

- **tokens** — prompt (`p`) + completion (`c`). Real counts when the provider returns a `usage` object; a `~`-prefixed `chars/4` estimate when it doesn't (many local servers don't emit usage unless asked — URSA now asks, via `stream_options.include_usage`, but not all honor it).
- **elapsed** — wall-clock for the whole turn, including any tool loop.
- **model** — the model id that actually ran.
- **⚠ truncated** — only when the output cap was hit (see above).

Click the line to expand the **context breakdown** — the estimated token cost of what filled the model's context this turn, split into buckets:

```
used get_nightly_summary, get_trend
context (est.): system 3,980 · tools 1,020 · tool results 298 · history 240 · total 5,538
2 tool-loop rounds
prompt cache: 3,400 tokens reused
```

This is the single most useful artifact for a slow turn: it shows *which* bucket is the bloat — a fat `tool results`, a large `system` (a big custom prompt), a long `history` — instead of you inferring it from a slow spinner. The `tools used` line is the actual execution trace (grounding: a turn that answered but called zero tools is a red flag). `prompt cache` appears when the provider reports cache reuse — visible proof the stable-prefix / prompt cache (§ above) is working.

The breakdown is the measurement that drives tool-payload trimming: cut where it points, not where you guess. Pattern per the Vitals/KAIROS per-turn-observability note.

## Metric names the AI can use (as of 1.1.15)

Anything that takes a `metric` — `get_trend`, `compare_periods`, `analyze_correlation`, `analyze_lag_correlation`, `analyze_multivariate_correlation`, `analyze_prediction` — accepts either:

- **A bare nightly metric**: one of the 25 columns of `nightly_summary` (`total_ahi`, `central_ahi`, `obstructive_ahi`, `hypopnea_index`, `rera_index`, `median_pressure`, `p95_leak`, `cheyne_stokes_pct`, …). Overall AHI is **`total_ahi`**, not `ahi` — though `ahi` is accepted as an alias.
- **A manual-log composite**: `log_type:filter:field`, where log_type is one of `medication` / `symptom` / `alertness` / `sleep_environment` / `freeform`. Examples: `medication:melatonin:dose`, `symptom:headache:severity`, `alertness:morning:score`.

The exact list is **generated from `metric_resolver.known_nightly_metrics()` at import time** and injected into each tool's parameter description, so what the model is told and what the API validates cannot drift apart. This page deliberately does not re-type the full list — that hand-copying *was* the 1.1.15 bug. `Settings → AI Assistant` isn't where you change it; add a column to `_NIGHTLY_NUMERIC_COLUMNS` and every tool picks it up.

Why a description and not a JSON-Schema `enum`: an enum of the nightly columns would forbid the composite form, and "trend my melatonin intake" is a real question. The description steers without narrowing the contract.

Cost: ~175 tokens, carried once per tool and only when that tool's group is loaded. It replaces a wasted tool round-trip (~20s on a local reasoning model) every time the model previously guessed a metric name wrong.

## Reading the source

If you want to verify any claim on this page byte-for-byte:

- **System prompt template**: `backend/src/ursa_oscar/ai_proxy/prompt.py` → `DEFAULT_TEMPLATE`
- **What the profile injection looks like**: same file → `_summarize_user_profile`
- **Tool descriptors**: `backend/src/ursa_oscar/ai_proxy/tools.py` → `TOOL_DESCRIPTORS`
- **Adapter request shaping** (OpenAI-compat): `providers/openai_compat.py` → `_build_request`
- **Adapter request shaping** (Claude): `providers/claude.py` → `_build_request`

The template you actually get is what's in the source; there's no additional layer that adds hidden instructions. Custom-prompt overrides replace the template wholesale.
