"""Phase 6.5 — Claude adapter prompt-caching tests.

Pure-unit tests against ``ClaudeAdapter._build_request()`` — no live
API calls, no anthropic SDK install required (the SDK is imported
lazily inside ``chat()`` only). Verifies the cache_control markers
are placed correctly per Anthropic's prompt-caching API contract:

  - system prompt → wrapped as a content-block list with cache_control
  - tools list → ONLY the last block gets cache_control (Anthropic
    treats everything up to that marker as the cache prefix)
  - messages → unmarked (they grow per turn; mustn't be in the prefix)

The live-cache behavior (cache_read_input_tokens > 0 on the 2nd
identical request) is exercised by the operator-only smoke test in
test_claude_live_smoke.py — it requires a real API key + budget and
isn't part of the regular test suite.
"""
from __future__ import annotations

import pytest

from ursa_oscar.ai_proxy.providers.base import AiMessage, AiToolCall
from ursa_oscar.ai_proxy.providers.claude import ClaudeAdapter


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _adapter() -> ClaudeAdapter:
    return ClaudeAdapter(
        api_key="test-key-not-real",
        model="claude-sonnet-4-5-20250929",
        endpoint=None,
    )


def _sample_tools() -> list[dict]:
    """Two-tool list in the OpenAI-style envelope our adapter accepts."""
    return [
        {
            "type": "function",
            "function": {
                "name": "get_nightly_summary",
                "description": "Return the nightly summary for one date or range.",
                "parameters": {
                    "type": "object",
                    "properties": {"date": {"type": "string"}},
                    "required": ["date"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "list_available_nights",
                "description": "List dates with CPAP data.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                },
            },
        },
    ]


# ---------------------------------------------------------------------------
# System prompt — cache_control attached
# ---------------------------------------------------------------------------


def test_system_prompt_is_content_block_list_with_cache_control():
    """The Anthropic ``system`` parameter must be a list of content
    blocks (not a plain string) so we can attach cache_control."""
    req = _adapter()._build_request(
        messages=[AiMessage(role="user", content="hi")],
        tools=_sample_tools(),
        system_prompt="You are URSA-OSCAR's analytical assistant.",
    )
    sys = req["system"]
    assert isinstance(sys, list)
    assert len(sys) == 1
    assert sys[0]["type"] == "text"
    assert sys[0]["text"] == "You are URSA-OSCAR's analytical assistant."
    assert sys[0]["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# Tools list — only the LAST block carries cache_control
# ---------------------------------------------------------------------------


def test_only_last_tool_block_has_cache_control():
    """Anthropic's caching rule: everything up to and including the
    block with cache_control is the cache prefix. So we set the marker
    on the LAST tool, never on intermediate ones."""
    tools = _sample_tools()
    req = _adapter()._build_request(
        messages=[AiMessage(role="user", content="hi")],
        tools=tools,
        system_prompt="sys",
    )
    anthropic_tools = req["tools"]
    assert len(anthropic_tools) == 2

    # First tool: no marker
    assert "cache_control" not in anthropic_tools[0]

    # Last tool: marker present
    assert anthropic_tools[-1]["cache_control"] == {"type": "ephemeral"}


def test_single_tool_gets_cache_control():
    """Edge case: a one-tool list — the lone tool IS the last, so it
    carries the marker."""
    tools = [_sample_tools()[0]]
    req = _adapter()._build_request(
        messages=[AiMessage(role="user", content="hi")],
        tools=tools,
        system_prompt="sys",
    )
    assert len(req["tools"]) == 1
    assert req["tools"][0]["cache_control"] == {"type": "ephemeral"}


def test_empty_tool_list_does_not_break_request():
    """Edge case: zero tools — request still builds; no cache_control
    to attach. The system-prompt cache marker is independent."""
    req = _adapter()._build_request(
        messages=[AiMessage(role="user", content="hi")],
        tools=[],
        system_prompt="sys",
    )
    assert req["tools"] == []
    # System prompt cache still works.
    assert req["system"][0]["cache_control"] == {"type": "ephemeral"}


# ---------------------------------------------------------------------------
# Messages — NOT marked for caching (they grow per turn)
# ---------------------------------------------------------------------------


def test_messages_do_not_carry_cache_control():
    """User/assistant messages must NOT be in the cache prefix —
    they change every turn, so caching them would defeat the point."""
    msgs = [
        AiMessage(role="user", content="hi"),
        AiMessage(role="assistant", content="hello"),
        AiMessage(role="user", content="what's my AHI?"),
    ]
    req = _adapter()._build_request(
        messages=msgs,
        tools=_sample_tools(),
        system_prompt="sys",
    )
    for m in req["messages"]:
        # No top-level cache_control on the message dict
        assert "cache_control" not in m
        # Also not nested into any string content
        if isinstance(m["content"], list):
            for block in m["content"]:
                assert "cache_control" not in block


def test_tool_result_messages_do_not_carry_cache_control():
    """Tool-result messages also grow per turn — they're echoes of
    function calls and must stay out of the cache prefix."""
    msgs = [
        AiMessage(role="user", content="show me last night"),
        AiMessage(
            role="assistant", content="",
            tool_calls=[AiToolCall(
                id="tu_01abc",
                name="get_nightly_summary",
                arguments={"date": "2026-05-13"},
            )],
        ),
        AiMessage(
            role="tool", content='{"ahi": 4.2}',
            tool_call_id="tu_01abc",
        ),
    ]
    req = _adapter()._build_request(
        messages=msgs, tools=_sample_tools(), system_prompt="sys",
    )
    # Walk every block in every message — none may have cache_control
    for m in req["messages"]:
        if isinstance(m["content"], list):
            for block in m["content"]:
                assert "cache_control" not in block, (
                    f"Found cache_control on a message content block, "
                    f"which would pollute the cache prefix: {block}"
                )


# ---------------------------------------------------------------------------
# Request envelope integrity (regression: don't break pre-6.5 callers)
# ---------------------------------------------------------------------------


def test_request_envelope_still_has_all_required_fields():
    """Belt-and-suspenders: the cache markers shouldn't change what
    the request dict carries to anthropic.AsyncAnthropic. Pre-6.5
    callers that introspect the request shape (the streaming test
    helpers in test_ai_proxy.py) should still see model, max_tokens,
    messages, tools, system."""
    req = _adapter()._build_request(
        messages=[AiMessage(role="user", content="hi")],
        tools=_sample_tools(),
        system_prompt="sys",
    )
    assert set(req.keys()) == {"model", "max_tokens", "system", "messages", "tools"}
    assert req["model"] == "claude-sonnet-4-5-20250929"
    assert req["max_tokens"] == 4096


def test_usage_dict_surfaces_cache_token_counts():
    """The Anthropic SDK's Usage object has cache_creation_input_tokens
    and cache_read_input_tokens fields. _usage_dict must surface those
    so the operator can verify caching is working via the DEBUG log
    line + the AiStreamEvent('complete') payload."""
    from ursa_oscar.ai_proxy.providers.claude import _usage_dict

    class _FakeUsage:
        input_tokens = 12
        output_tokens = 45
        cache_creation_input_tokens = 1100  # first call wrote to cache
        cache_read_input_tokens = 0

    d = _usage_dict(_FakeUsage())
    assert d["input_tokens"] == 12
    assert d["output_tokens"] == 45
    assert d["cache_creation_input_tokens"] == 1100
    assert d["cache_read_input_tokens"] == 0

    class _CachedUsage:
        input_tokens = 6
        output_tokens = 30
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 1100  # second call hit the cache

    d2 = _usage_dict(_CachedUsage())
    assert d2["cache_read_input_tokens"] == 1100
    assert d2["cache_creation_input_tokens"] == 0


# ---------------------------------------------------------------------------
# Per-message cache_control absence — exhaustive sweep
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("n_messages", [1, 5, 20])
def test_no_message_count_introduces_cache_markers(n_messages):
    """Defensive parametrization: regardless of how many messages
    are in the history, none of them ever get cache_control set."""
    msgs = [
        AiMessage(role="user" if i % 2 == 0 else "assistant", content=f"turn {i}")
        for i in range(n_messages)
    ]
    req = _adapter()._build_request(
        messages=msgs, tools=_sample_tools(), system_prompt="sys",
    )
    for m in req["messages"]:
        assert "cache_control" not in m
        if isinstance(m["content"], list):
            for b in m["content"]:
                assert "cache_control" not in b
