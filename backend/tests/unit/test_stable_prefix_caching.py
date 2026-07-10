"""Byte-stability audit for the AI proxy's system prompt (KAIROS D74).

llama.cpp / LocalAI's cross-request prefix / KV cache reuses the KV state
of a shared BYTE-IDENTICAL leading token run. The cache matches from the
start and stops at the first differing byte, so any volatile content
at the front of the system prompt poisons everything behind it — the
model has to re-read every stable byte on every turn.

These tests prove that URSA's stable prefix is actually stable:

  - render_system_prompt_parts() returns (stable, volatile) where the
    stable half is byte-identical across two "turns" with different
    volatile inputs (current_view changes when the operator navigates).
  - The volatile suffix DOES differ across those two turns (proving the
    split really does isolate the variable content).
  - The chat endpoint's assembly order — stable + tool_index + volatile
    — puts every stable byte before the volatile tail.

If any of these fail, someone has re-introduced volatile content into
the stable half (a timestamp, a live clock, a per-turn UUID). The fix
is to move it into VOLATILE_SUFFIX_TEMPLATE in prompt.py, not to
loosen this test.

Companion reference: KAIROS's docs/stable-prefix-caching-for-sibling-devs.md
and its own proxy/tests/unit/test_stable_prefix_caching.py.
"""
from __future__ import annotations

import hashlib
from datetime import date

from ursa_oscar.ai_proxy.prompt import (
    VOLATILE_SUFFIX_TEMPLATE,
    render_system_prompt,
    render_system_prompt_parts,
)


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# Fixed inputs used across turns 1 and 2 — everything except current_view
# is byte-identical between the two calls. If the stable hashes diverge,
# something inside render_system_prompt_parts is still reading a volatile
# source (module-level cache, wall clock, etc.).

_TURN_ARGS = dict(
    user_profile={
        "clinical": {
            "diagnoses": ["OSA", "central sleep apnea"],
            "medications": [{"name": "Sunosi", "active": True}],
            "treatment_goals": ["reduce daytime sleepiness"],
        },
    },
    device_clock={
        "country": "USA",
        "mode": "matches_local",
    },
    today_date=date(2026, 7, 8),
)


def test_stable_prefix_is_byte_identical_across_turns():
    """Turn 1 (Daily View 2026-07-06) and turn 2 (Daily View 2026-07-07)
    of the same chat session must produce the SAME stable prefix.
    Anything else in the args is identical; only current_view differs.

    If this fails, look for volatile content that has crept into
    DEFAULT_TEMPLATE or the stable_context dict inside
    render_system_prompt_parts. The fix is to move it into the
    VOLATILE_SUFFIX_TEMPLATE, not to weaken this test.
    """
    stable_1, _volatile_1 = render_system_prompt_parts(
        current_view="Daily View 2026-07-06",
        **_TURN_ARGS,
    )
    stable_2, _volatile_2 = render_system_prompt_parts(
        current_view="Daily View 2026-07-07",
        **_TURN_ARGS,
    )
    assert _sha256(stable_1) == _sha256(stable_2), (
        "Stable prefix hash diverged across turns with identical stable "
        "inputs. Something in DEFAULT_TEMPLATE or stable_context is "
        "reading a volatile source (wall clock, uuid4, etc). Diff the "
        "two prompts to find the byte that changed:\n\n"
        f"turn 1 hash: {_sha256(stable_1)}\n"
        f"turn 2 hash: {_sha256(stable_2)}"
    )


def test_volatile_suffix_actually_differs_when_current_view_changes():
    """Sanity check the split: if the two suffixes were also equal, the
    reorder wouldn't be doing any work. The whole point is that this
    is the ONE part that changes between turns; everything else is
    in the stable prefix."""
    _stable_1, volatile_1 = render_system_prompt_parts(
        current_view="Daily View 2026-07-06",
        **_TURN_ARGS,
    )
    _stable_2, volatile_2 = render_system_prompt_parts(
        current_view="Daily View 2026-07-07",
        **_TURN_ARGS,
    )
    assert volatile_1 != volatile_2
    assert "2026-07-06" in volatile_1
    assert "2026-07-07" in volatile_2


def test_volatile_suffix_never_contains_stable_placeholders():
    """The volatile suffix should be short and only reference
    current_view_context — every other {placeholder} belongs in the
    stable half. Guards against future edits that quietly bloat the
    volatile tail (which defeats the cache's whole point)."""
    other_placeholders = [
        "{user_profile_summary}",
        "{device_clock_description}",
        "{today_date}",
    ]
    for ph in other_placeholders:
        assert ph not in VOLATILE_SUFFIX_TEMPLATE, (
            f"Volatile suffix contains {ph!r}. Only current_view_context "
            "should be in the volatile tail — everything else needs to "
            "ride the stable prefix so the cache can hit."
        )


def test_backward_compat_facade_still_returns_a_single_string():
    """The old render_system_prompt() function is retained as a compat
    façade — callers that don't care about caching still get the same
    concatenated string. Assert the result matches stable + '\\n\\n' +
    volatile so no caller sees a shape change."""
    stable, volatile = render_system_prompt_parts(
        current_view="Daily View 2026-07-06",
        **_TURN_ARGS,
    )
    full = render_system_prompt(
        current_view="Daily View 2026-07-06",
        **_TURN_ARGS,
    )
    assert full == stable + "\n\n" + volatile


def test_today_date_stays_in_stable_prefix_not_volatile():
    """Per KAIROS D74's nuance: a per-day date is byte-stable across a
    chat session (all turns happen on the same date). It can live in
    the stable prefix and re-caches once at midnight, which is fine.
    A per-turn CLOCK ('HH:MM') cannot — but we don't render one.

    Guard: today_date must appear in the stable half, not the volatile
    suffix, so it participates in the cached run."""
    stable, volatile = render_system_prompt_parts(
        current_view="Daily View 2026-07-06",
        **_TURN_ARGS,
    )
    assert "2026-07-08" in stable
    assert "2026-07-08" not in volatile


def test_assembly_order_stable_first_volatile_last():
    """Simulate the chat endpoint's assembly: STABLE + tool_index +
    VOLATILE. The volatile suffix must be the FINAL block in the final
    assembled prompt so llama.cpp's cache only invalidates on that
    tail, not on the tool index or on any earlier content."""
    stable, volatile = render_system_prompt_parts(
        current_view="Daily View 2026-07-06",
        **_TURN_ARGS,
    )
    fake_index = "## AVAILABLE TOOLS (inactive — load before use)\n\n- foo\n"
    assembled = stable + "\n\n" + fake_index + "\n\n" + volatile

    # Volatile is the tail.
    assert assembled.rstrip().endswith(volatile.rstrip())
    # Tool index is BEFORE the volatile suffix.
    assert assembled.index(fake_index) < assembled.index(volatile)
    # Stable persona is BEFORE the tool index (so the whole leading run
    # up to the tool index is stable).
    persona_line = "You are URSA"
    assert assembled.index(persona_line) < assembled.index(fake_index)
