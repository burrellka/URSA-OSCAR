"""Progressive tool disclosure — the AVAILABLE TOOLS index + resolver
for the ``load_tools`` discovery flow (1.1.12).

URSA sends only a small always-on core catalog on every turn. Everything
else lives behind a compact text index injected into the system prompt;
the model activates the tools it needs this turn by calling ``load_tools``.
This module builds that index and resolves a ``load_tools`` request back
into full tool descriptors that the chat loop can splice into the active
catalog for the next iteration.

Pattern lifted wholesale from KAIROS `proxy/src/core/tool_index.py`
(see docs/progressive-tool-disclosure-spec.md there). URSA's version is
simpler because URSA has a flat, small tool surface (~15 native tools,
no MCP prefixes, no Muses) — no group-key parsing from name prefixes is
needed; groups come from the operator-tagged TOOL_META. Semantics of
:class:`ResolveResult` and :func:`build_tool_index` match KAIROS's so the
pattern stays portable across the sibling projects.

Deliberately dependency-free (no motor / no heavy imports) so it can be
unit-tested standalone.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


# Index display tuning — mirrors KAIROS's constants.
_MAX_SHORT_PER_GROUP = 10


@dataclass
class ResolveResult:
    """Outcome of a ``load_tools`` request.

    - ``descriptors`` — the full OpenAI-shape entries to splice into the
      chat loop's active tool catalog on the next iteration.
    - ``loaded_names`` — the LLM tool names now active (for the
      confirmation message shown to the model as a tool result).
    - ``unknown`` — anything the model asked for that we couldn't map to
      a real tool or group. Fed back so the model doesn't repeat itself.
    """
    descriptors: list[dict[str, Any]]
    loaded_names: list[str]
    unknown: list[str]


@dataclass
class DeferredCatalog:
    """The pool of tools held back from the every-turn catalog, plus the
    rendered index text and the metadata needed to resolve ``load_tools``
    requests.

    The chat endpoint builds one of these at the start of a chat session
    and uses it for the whole conversation — the catalog itself is
    static per URSA-OSCAR release (tools don't hot-swap at runtime).
    """
    descriptors_by_name: dict[str, dict[str, Any]] = field(default_factory=dict)
    groups: dict[str, list[str]] = field(default_factory=dict)       # key -> [names]
    group_labels: dict[str, str] = field(default_factory=dict)        # key -> human label
    index_text: str = ""

    @property
    def is_empty(self) -> bool:
        return not self.descriptors_by_name

    def resolve(
        self,
        *,
        names: list[str] | None = None,
        groups: list[str] | None = None,
    ) -> ResolveResult:
        """Map a ``load_tools`` request to concrete descriptors.

        - ``groups`` matched against group keys (case-insensitive).
        - ``names`` matched exactly.

        Deduped; anything unmatched is reported in ``unknown`` so the chat
        loop can tell the model what didn't resolve. Semantics match
        KAIROS's resolver so the two projects behave alike from the
        model's perspective.
        """
        selected: dict[str, dict[str, Any]] = {}
        unknown: list[str] = []

        for g in groups or []:
            key = str(g).strip()
            members = self.groups.get(key)
            if members is None:
                members = self.groups.get(key.lower())
            if members:
                for nm in members:
                    selected[nm] = self.descriptors_by_name[nm]
            else:
                unknown.append(f"group:{g}")

        for n in names or []:
            nm = str(n).strip()
            if not nm:
                continue
            if nm in self.descriptors_by_name:
                selected[nm] = self.descriptors_by_name[nm]
            else:
                unknown.append(n)

        return ResolveResult(
            descriptors=list(selected.values()),
            loaded_names=list(selected.keys()),
            unknown=unknown,
        )


def build_tool_index(
    deferred_descriptors_by_group: dict[str, list[dict[str, Any]]],
    group_labels: dict[str, str],
) -> DeferredCatalog:
    """Assemble the ``DeferredCatalog`` from URSA's tagged tool metadata.

    Callers pass in the output of
    :func:`ursa_oscar.ai_proxy.tools.descriptors_by_group` plus the
    ``GROUP_LABELS`` map so this module doesn't have to import from
    :mod:`tools` and can be unit-tested with hand-crafted inputs.

    The rendered ``index_text`` is designed to be appended verbatim to
    the system prompt when the catalog is non-empty. If every tool is
    core, the returned catalog is empty and callers should NOT append
    an empty index block.
    """
    cat = DeferredCatalog(group_labels=dict(group_labels))
    for key, descs in deferred_descriptors_by_group.items():
        if not descs:
            continue
        cat.groups[key] = []
        for d in descs:
            name = ((d.get("function") or {}).get("name") or "").strip()
            if not name:
                continue
            cat.descriptors_by_name[name] = d
            cat.groups[key].append(name)

    if cat.is_empty:
        return cat

    lines = [
        "## AVAILABLE TOOLS (inactive — load before use)",
        "",
        "These capabilities exist but are not yet active. To use any, "
        "first call `load_tools` with its group key (preferred) or "
        "specific tool names; they become callable on your next step. "
        "Never tell the user a capability listed here is unavailable — "
        "load it and use it. Do NOT load groups you don't need — every "
        "loaded tool spends context tokens on this turn and every "
        "subsequent turn.",
        "",
    ]
    # Preserve group_labels' iteration order for a stable, readable index.
    for key in cat.groups:
        label = cat.group_labels.get(key, key)
        names = cat.groups[key]
        shorts = list(names)
        shown = shorts[:_MAX_SHORT_PER_GROUP]
        suffix = ""
        if len(shorts) > _MAX_SHORT_PER_GROUP:
            suffix = f", … (+{len(shorts) - _MAX_SHORT_PER_GROUP} more)"
        lines.append(
            f"- {label} [group: {key}]: {', '.join(shown)}{suffix}"
        )
    lines.append("")
    cat.index_text = "\n".join(lines)
    return cat


def format_load_result(result: ResolveResult) -> str:
    """The role=tool content the model reads back after a ``load_tools``
    call. Kept short (the tool result rides the context of every
    subsequent turn until the conversation ends)."""
    if not result.loaded_names and result.unknown:
        return (
            "No tools matched your request: "
            f"{', '.join(result.unknown)}. Check the AVAILABLE TOOLS "
            "index in the system prompt for the exact group keys and "
            "tool names."
        )
    parts: list[str] = []
    if result.loaded_names:
        parts.append(
            "Activated "
            f"{len(result.loaded_names)} tool(s); you can call them now: "
            + ", ".join(result.loaded_names)
            + "."
        )
    if result.unknown:
        parts.append(
            "Could not match: "
            + ", ".join(result.unknown)
            + " (ignored)."
        )
    return " ".join(parts)


# Constant name for the discovery tool — imported by the chat loop
# and by the tool descriptor definition in ai_proxy/tools.py so both
# sides agree on the exact string.
LOAD_TOOLS_NAME = "load_tools"
