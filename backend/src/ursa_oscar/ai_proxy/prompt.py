"""System prompt template + context injection — Phase 5 Decision 8.

The AI proxy renders a server-side system prompt at chat session start.
The prompt includes the user's clinical profile (diagnoses, active
medications, treatment goals) and the device-clock context (so the LLM
knows that "last night" in user-frame may need shifting to device-clock
when querying tools).

Custom-prompt support: the operator can override the entire template
via Settings → AI Assistant → Custom system prompt. When unset, the
default template below is used.
"""
from __future__ import annotations

from datetime import date as date_t
from datetime import datetime
from typing import Any


DEFAULT_TEMPLATE = """You are URSA, the user's dedicated sleep and health agent embedded in URSA-OSCAR. You have access to the user's CPAP analytics data via the tools below.

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
"""


def render_system_prompt(
    *,
    user_profile: dict | None,
    device_clock: dict | None,
    today_date: date_t | None = None,
    current_view: str | None = None,
    custom_template: str | None = None,
) -> str:
    """Fill the prompt template with runtime context.

    ``user_profile`` and ``device_clock`` are pulled from the API's
    profile store at chat-session start. Both are optional — when
    missing, the prompt notes the absence so the LLM doesn't
    hallucinate context.

    ``custom_template`` overrides the default if the operator
    customized the prompt in Settings. The same placeholders are
    available; missing placeholders just render as empty.
    """
    template = (custom_template or DEFAULT_TEMPLATE).strip()
    today = today_date or datetime.utcnow().date()

    context = {
        "user_profile_summary": _summarize_user_profile(user_profile),
        "device_clock_description": _describe_device_clock(device_clock),
        "today_date": today.isoformat(),
        "current_view_context": current_view or "URSA-OSCAR dashboard (no specific night)",
    }
    # Safe substitution — KeyError on unknown placeholders is annoying for
    # users editing the template, so use a forgiving formatter that leaves
    # unknown {tokens} in place.
    return _format_lenient(template, context)


# -------------------------------------------------------------------------
# Subsection renderers.
# -------------------------------------------------------------------------


def _summarize_user_profile(profile: dict | None) -> str:
    """Render the clinical bits of UserProfile as a compact bullet list.
    Skips empty fields entirely so the LLM doesn't see "Diagnoses: (none)"
    cluttering the prompt."""
    if not profile:
        return "No user profile configured yet."

    bits: list[str] = []
    clinical = profile.get("clinical") or {}

    diagnoses = clinical.get("diagnoses") or []
    if diagnoses:
        bits.append(f"- **Diagnoses:** {', '.join(diagnoses)}")

    meds = clinical.get("medications") or []
    active_meds = [m for m in meds if m.get("active") is not False]
    if active_meds:
        med_names = [m["name"] for m in active_meds if m.get("name")]
        if med_names:
            bits.append(f"- **Active medications:** {', '.join(med_names)}")

    goals = clinical.get("treatment_goals") or []
    if goals:
        bits.append(f"- **Treatment goals:** {', '.join(goals)}")

    allergies = clinical.get("allergies") or []
    if allergies:
        bits.append(f"- **Allergies:** {', '.join(allergies)}")

    notes = clinical.get("notes")
    if notes:
        bits.append(f"- **Notes:** {notes}")

    return "\n".join(bits) if bits else "No clinical context configured."


def _describe_device_clock(device_clock: dict | None) -> str:
    """Translate the DeviceClock config into a sentence the LLM can use
    to reason about timestamp shifts."""
    if not device_clock:
        return (
            "The user has not configured a device-clock offset. Assume "
            "the CPAP device records timestamps in the user's local "
            "time zone, no shift needed."
        )

    mode = device_clock.get("mode") or "matches_local"
    country = device_clock.get("country")
    country_str = f" (user country: {country})" if country else ""

    if mode == "matches_local":
        return (
            f"The user's CPAP device's clock matches their local wall-"
            f"clock time{country_str}. No timestamp shift is needed."
        )
    if mode == "static_offset":
        offset = device_clock.get("static_offset_minutes")
        auto_dst = device_clock.get("auto_dst", True)
        if offset is None:
            return (
                f"The user's device is on a fixed offset{country_str}, "
                f"but the offset value isn't configured."
            )
        hours = offset / 60.0
        dst_str = " The UI auto-adjusts for DST." if auto_dst else " DST is NOT auto-adjusted."
        return (
            f"The user's CPAP device records timestamps in a fixed UTC "
            f"offset of {hours:+.1f} hours{country_str}.{dst_str} "
            f"When the user says 'last night', that's in their browser's "
            f"local time; URSA-OSCAR applies the offset to render. "
            f"Tool queries that take a date should use the date the "
            f"DEVICE wrote (which is what's in the DB)."
        )
    if mode == "manual":
        manual = device_clock.get("manual_offset_minutes") or 0
        return (
            f"The user has set a manual {manual:+d}-minute display "
            f"offset{country_str}. Stored timestamps are device-clock; "
            f"the UI shifts on display."
        )
    return f"Device-clock mode '{mode}'{country_str} — unknown configuration."


def _format_lenient(template: str, context: dict[str, Any]) -> str:
    """Like str.format(**context) but leaves unknown {tokens} intact
    instead of raising KeyError. Useful when operators write custom
    templates that use a subset of placeholders."""
    import string

    class _DefaultDict(dict):
        def __missing__(self, k: str) -> str:
            return "{" + k + "}"

    return string.Formatter().vformat(template, (), _DefaultDict(context))
