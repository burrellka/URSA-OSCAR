"""Tests for the per-turn context breakdown (1.1.14 observability).

The breakdown is what turns a slow AI turn from a black box into a
one-glance diagnosis (KAIROS/Vitals observability note). These tests
lock in the bucketing rules so a future refactor can't silently
mis-attribute tokens (e.g. counting a tool result as history, which
would hide the exact bloat the breakdown exists to reveal).
"""
from __future__ import annotations

from ursa_oscar.ai_proxy.context_budget import (
    ContextBreakdown,
    compute_breakdown,
    estimate_tokens,
    normalize_usage,
)
from ursa_oscar.ai_proxy.providers.base import AiMessage, AiToolCall


def test_estimate_tokens_is_ceil_chars_over_4():
    assert estimate_tokens("") == 0
    assert estimate_tokens("a") == 1        # ceil(1/4)
    assert estimate_tokens("abcd") == 1     # 4/4
    assert estimate_tokens("abcde") == 2    # ceil(5/4)
    assert estimate_tokens("a" * 400) == 100


def test_breakdown_separates_tool_results_from_history():
    """A tool result must land in tool_results, never history — the whole
    point is to show when re-sent tool payloads are the bloat."""
    messages = [
        AiMessage(role="user", content="how did I sleep on 2026-06-21?"),
        AiMessage(
            role="assistant", content="",
            tool_calls=[AiToolCall(
                id="c1", name="get_nightly_summary",
                arguments={"date": "2026-06-21"},
            )],
        ),
        AiMessage(
            role="tool", tool_call_id="c1",
            content="{" + "x" * 800 + "}",  # a fat ~200-token tool result
        ),
        AiMessage(role="assistant", content="Your AHI was 4.7."),
    ]
    tools = [{"type": "function", "function": {"name": "get_nightly_summary", "description": "d", "parameters": {}}}]
    b = compute_breakdown(system_prompt="You are URSA." * 50, tools=tools, messages=messages)

    assert b.tool_results > 150          # the fat tool payload shows up here
    assert b.tool_results < b.system + b.tool_results + b.history + b.tools + 1
    # The user + assistant text + the tool-call args are history, NOT the
    # tool result.
    assert b.history > 0
    # System prompt counted once from system_prompt, not from messages.
    assert b.system == estimate_tokens("You are URSA." * 50)
    # Tool schema estimated in the tools bucket.
    assert b.tools > 0
    # total is the sum.
    assert b.total == b.system + b.tools + b.tool_results + b.history


def test_breakdown_as_dict_carries_total():
    b = ContextBreakdown(system=10, tools=20, tool_results=30, history=40)
    d = b.as_dict()
    assert d == {"system": 10, "tools": 20, "tool_results": 30, "history": 40, "total": 100}


def test_normalize_usage_openai_shape():
    out = normalize_usage({"prompt_tokens": 5657, "completion_tokens": 775, "total_tokens": 6432})
    assert out == {"prompt": 5657, "completion": 775, "total": 6432}


def test_normalize_usage_claude_shape_and_cache():
    out = normalize_usage({
        "input_tokens": 100, "output_tokens": 50,
        "cache_read_input_tokens": 3400,
    })
    assert out["prompt"] == 100
    assert out["completion"] == 50
    assert out["total"] == 150             # derived when total absent
    assert out["cache_read_input_tokens"] == 3400  # proof the cache hit


def test_normalize_usage_none_when_empty():
    assert normalize_usage(None) is None
    assert normalize_usage({}) is None
    assert normalize_usage({"something_else": 1}) is None
