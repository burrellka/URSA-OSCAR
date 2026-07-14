"""Per-turn context breakdown — 1.1.14 observability (KAIROS/Vitals note).

Cross-project observability pattern: *you can't cut what you can't see.*
Before optimizing a slow AI turn you want to know WHAT filled the model's
context on that turn, split into named buckets:

  - system        — the system prompt (persona + instructions + profile +
                    device-clock + today's date + the AVAILABLE TOOLS index)
  - tools         — the active tool SCHEMAS sent this turn (progressive
                    disclosure keeps this small; it's the surprise bucket
                    on apps that ship every schema every turn)
  - tool_results  — the JSON a tool returned, appended to the conversation
                    and RE-SENT on every later round of the tool loop
  - history       — the prior user/assistant turns (grows per message)

Estimation is a deliberately-cheap ``chars / 4`` — URSA has no per-provider
tokenizer, and the note is explicit that the *relative* breakdown (which
bucket is huge) is what diagnoses you, not absolute precision. Numbers are
surfaced with a "~" in the UI to signal they're estimates.

The real prompt/completion counts (when the provider returns a ``usage``
object) are reported separately and preferred; the estimate is the fallback
for local servers that don't emit usage even when asked.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass


def estimate_tokens(text: str) -> int:
    """~4-chars-per-token estimate, rounded up. Good enough for the
    relative breakdown; not a substitute for a real tokenizer."""
    if not text:
        return 0
    return (len(text) + 3) // 4


def _json_tokens(obj: object) -> int:
    """Estimate tokens for a JSON-serializable object (a tool schema, a
    tool-call arguments dict). Falls back to str() if it isn't
    serializable so a weird value can't crash the breakdown."""
    try:
        return estimate_tokens(json.dumps(obj))
    except (TypeError, ValueError):
        return estimate_tokens(str(obj))


@dataclass
class ContextBreakdown:
    """Estimated input-token cost of one request, split by bucket. All
    values are ``chars/4`` estimates over the exact payload sent."""

    system: int
    tools: int
    tool_results: int
    history: int

    @property
    def total(self) -> int:
        return self.system + self.tools + self.tool_results + self.history

    def as_dict(self) -> dict:
        d = asdict(self)
        d["total"] = self.total
        return d


def compute_breakdown(*, system_prompt: str, tools: list, messages: list) -> ContextBreakdown:
    """Tally the estimated token cost of the payload about to be (or just)
    sent to the model. ``messages`` are the normalized ``AiMessage``
    objects (system is NOT in this list — it's counted once from
    ``system_prompt``).

    Tool results (role="tool") get their own bucket; everything else
    (user/assistant, plus any assistant tool-call payloads) is history.
    """
    tools_tokens = sum(_json_tokens(t) for t in tools)

    tool_results = 0
    history = 0
    for m in messages:
        role = getattr(m, "role", None)
        content = getattr(m, "content", "") or ""
        if role == "tool":
            tool_results += estimate_tokens(content)
            continue
        history += estimate_tokens(content)
        # An assistant turn that requested tools carries the tool-call
        # payloads too — they're re-sent every later round, so count them.
        tool_calls = getattr(m, "tool_calls", None) or []
        for tc in tool_calls:
            history += estimate_tokens(getattr(tc, "name", "") or "")
            args = getattr(tc, "arguments", None)
            if args:
                history += _json_tokens(args)

    return ContextBreakdown(
        system=estimate_tokens(system_prompt),
        tools=tools_tokens,
        tool_results=tool_results,
        history=history,
    )


def normalize_usage(usage: dict | None) -> dict | None:
    """Map a provider ``usage`` object to a common
    ``{prompt, completion, total}`` shape.

    OpenAI-compat servers report ``prompt_tokens`` / ``completion_tokens``;
    the Claude adapter reports ``input_tokens`` / ``output_tokens`` (plus
    cache fields). Returns None if there's nothing usable so callers can
    fall back to the estimate.
    """
    if not usage:
        return None
    prompt = usage.get("prompt_tokens")
    if prompt is None:
        prompt = usage.get("input_tokens")
    completion = usage.get("completion_tokens")
    if completion is None:
        completion = usage.get("output_tokens")
    total = usage.get("total_tokens")
    if total is None and (prompt is not None or completion is not None):
        total = (prompt or 0) + (completion or 0)
    if prompt is None and completion is None and total is None:
        return None
    out: dict = {"prompt": prompt, "completion": completion, "total": total}
    # Carry Anthropic cache accounting through when present — a cache read
    # is the visible proof the stable-prefix / prompt cache is working.
    for k in ("cache_read_input_tokens", "cache_creation_input_tokens"):
        if usage.get(k) is not None:
            out[k] = usage[k]
    return out
