# The AI chat panel

A conversational interface to your CPAP data, built on whichever AI provider you configured. Bring your own API key; URSA-OSCAR proxies the requests, but the inference happens at the provider.

## How to enable it

**Settings → AI Assistant** → pick a provider preset → paste your API key → save.

URSA-OSCAR encrypts the key at rest with the master.key generated on first boot. The key never leaves your hardware in plaintext outside the moment-by-moment outbound HTTPS request to the provider's API.

Skip this step entirely if you don't want AI features. The chat panel hides itself when no provider is configured; everything else in URSA-OSCAR still works.

## Supported providers

| Provider | API | Notes |
|---|---|---|
| Claude API (Anthropic) | Native Anthropic Messages | Best tool-calling reliability; recommended |
| OpenAI | OpenAI Chat Completions | GPT-4o family |
| Gemini | OpenAI-compat shim | Google's Gemini via the OpenAI-compatible endpoint |
| OpenRouter | OpenAI-compat | Multi-provider routing through one key |
| Groq | OpenAI-compat | Hosted llama / mixtral with very fast inference |
| Local LLM | OpenAI-compat | Anything with an OpenAI-compatible endpoint (LM Studio, Ollama, vLLM, etc.) |

The first provider (Claude) uses URSA-OSCAR's native Anthropic adapter with prompt caching (Phase 6.5). The rest use the OpenAI-compatibility shim. Caching behavior varies — Claude caches the system prompt + tools list explicitly; the OpenAI-compat path doesn't currently do explicit caching.

## What the AI can do

The system prompt sets up the AI as **URSA**, your CPAP-aware assistant. It has tools that query your actual data:

- `get_nightly_summary` — one night or a range
- `get_ahi_breakdown` — per-event-type detail
- `get_event_distribution_by_hour` — events filtered/grouped
- `get_pressure_profile`, `get_leak_profile` — per-night pressure/leak detail
- `get_session_breakdown` — per-session for one night
- `list_available_nights` — what dates are imported
- `compare_periods`, `get_trend`, `analyze_correlation`, `analyze_multivariate_correlation`, `analyze_lag_correlation`, `analyze_prediction` — the full statistical surface
- `get_user_profile` — your diagnoses, medications, providers, treatment goals
- `get_manual_log_summary` — what you've logged subjectively
- `trigger_import` — kick off a CPAP data import
- `generate_report` — produce a PDF report on demand
- `get_help_topic` *(new in 1.1)* — read these Help pages directly

When you ask a question, the AI picks which tools to call, calls them, reads the results, and responds. You see the tool calls happening in real time on the chat panel.

## What the AI is not

The system prompt is explicit:

- Not a doctor, sleep medicine specialist, or licensed clinician
- Cannot diagnose, prescribe, adjust medications, or change CPAP settings
- Will redirect you to human care for emergency symptoms

You'll see a disclaimer in the AI's first response of each conversation: "I'm URSA, an AI assistant. I'm not a doctor. I can help you read your data, spot patterns, and prepare better questions for your sleep medicine provider."

The AI is honest about uncertainty. It will say "I don't have enough data to answer that reliably" instead of fabricating numbers. It will surface the confidence level of statistical results. It will redirect you when the question requires clinical judgment.

## Conversation model

- **Conversations are scoped to a date.** Open the Daily View for 2026-05-13 and the chat is "about that night." Switch dates → new conversation. This keeps each conversation focused.
- **History lives in the browser.** Conversations are stored in localStorage per-date. There's no server-side conversation memory. If you clear browser data or switch devices, the history is gone.
- **No cross-conversation memory.** The AI doesn't "remember" what you discussed yesterday. If you want context to carry across conversations, fill in your Profile — that's loaded into the system prompt automatically.

## Costs

The provider bills you per token. URSA-OSCAR has no markup, no usage limit, no per-message charge — your provider's bill is your bill.

Prompt caching (Claude only) reduces the cost of repeated questions by ~90% on the cached prefix. The system prompt + tools list (~3-5 KB of tokens) only get billed at full rate the first time per cache window.

Future patches will surface token usage and cost estimates in the UI; for now you can check your provider's console for the running total.

## System prompt template

The system prompt is editable. **Settings → AI Assistant → System Prompt Template** shows you the default; you can save your own variant. Useful when:

- You want to adjust the AI's voice (more formal, more casual)
- You want to add domain context (e.g., "I'm a side sleeper" or "I have AFib")
- You want to constrain the AI ("don't suggest mask changes; my provider handles that")

The template supports a few placeholders: `{user_profile_summary}` and `{device_clock_description}` get filled in at runtime.

If you mess up the template and want to start over, the **Reset to factory default** button replaces your custom template with whatever the running image's `DEFAULT_TEMPLATE` is.

## Common chat workflows

- **"How was last night?"** — opens with `get_nightly_summary`, reads the AHI breakdown, comments on whether it's typical for you.
- **"Compare this week to last week."** — calls `compare_periods` with two adjacent 7-day windows, reads the deltas, calls out the most-changed metrics.
- **"Why was my AHI bad last Tuesday?"** — pulls the Daily View data for Tuesday, looks at events, checks leak %, looks at pressure response, hypothesizes.
- **"Is my AHI improving over the last 90 days?"** — calls `get_trend` for total_ahi, reports slope + R² + interpretation, warns if R² is low.
- **"What should I ask my doctor at my next visit?"** — pulls your recent data, identifies anomalies, suggests questions to bring (NOT recommendations to make).

## What chat is not for

- **Emergency symptoms.** "Chest pain" or "I think I'm having a stroke" — call emergency services. The chat is built to escalate, but don't put it in the loop.
- **Prescription decisions.** "Should I stop my CPAP for a week?" — that's your provider.
- **Diagnosis.** "Do I have central apnea?" — your provider tests for and diagnoses that.
- **Replacing the provider relationship.** The chat helps you talk to your provider better, not avoid your provider.
