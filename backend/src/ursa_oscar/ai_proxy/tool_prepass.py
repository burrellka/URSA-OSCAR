"""Lexical pre-pass tool selector (1.1.12 slice 3).

Progressive disclosure (slice 2) keeps the per-turn catalog lean but
costs an extra model round-trip whenever the model must call
``load_tools`` before it can act. This pre-pass kills that round-trip
for the obvious cases: a cheap, deterministic lexical match of the
user's latest message against the deferred catalog. When the intent is
clear ("show me the AHI trend", "compare last week to the week before"),
the matching group's tools are pre-activated BEFORE the first model
call, so the model just uses them. ``load_tools`` stays as the fallback
for everything the pre-pass doesn't catch.

Conservative by design: it only pre-loads on a strong signal and caps
how many groups it will add, so it never re-introduces the bloat slice 2
just removed. No embeddings, no extra model call, no heavy imports —
unit-testable standalone.

Pattern from KAIROS `proxy/src/core/tool_prepass.py`. URSA's stopword
list adapts for the CPAP-analytics domain: it keeps the KAIROS filler
words + generic verbs (get/list/search/…) and adds a few URSA-common
verbs (compare/analyze/show) so an intent noun like "correlation" or
"pressure" carries the signal instead of being drowned out.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # avoid runtime import cycle; both are pure modules.
    from .tool_index import DeferredCatalog


# Cap: never pre-load more than this many groups. Keeps the pre-pass
# from silently blowing up the per-turn tool tax on an ambiguous query.
# The model can still call load_tools for anything the pre-pass missed.
MAX_PREPASS_GROUPS = 2


# Stopwords: filler words + generic tool-verbs. Dropping the verbs is
# what stops "list my events" from spuriously matching every group whose
# tools happen to be named list_*/get_*. Intent nouns (correlation,
# pressure, sleep, ahi, leak) survive and carry the signal.
_STOPWORDS = frozenset({
    # Filler.
    "the", "and", "for", "you", "your", "can", "could", "would", "please",
    "with", "what", "whats", "that", "this", "have", "has", "are", "was",
    "but", "not", "from", "into", "out", "got", "let", "lets",
    "need", "want", "about", "any", "all", "how", "when", "where", "who",
    "why", "did", "does", "do", "is", "it", "its", "me", "my", "mine",
    "our", "us", "we", "i", "a", "an", "to", "of", "in", "on", "at", "by",
    "or", "so", "up", "as", "be", "if", "go", "now", "today", "see",
    "was", "were", "been", "being", "am",
    "ursa", "oscar",  # self-reference is noise
    # Generic tool-verbs (noise, not intent). URSA adds compare/analyze/
    # show alongside KAIROS's originals because our tools are literally
    # named analyze_* / compare_* — without the verbs on the stoplist,
    # a query like "show me AHI" would match those groups on the verb
    # alone, not on the "ahi" intent noun.
    "get", "list", "search", "create", "update", "delete", "manage",
    "send", "set", "find", "add", "remove", "fetch", "read", "write",
    "tell", "show", "give", "make", "help", "check", "look", "pull",
    "compare", "analyze", "run", "generate", "explain", "describe",
    "print", "return",
})


_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> set[str]:
    """Lower-case, tokenize, drop stopwords and short/numeric tokens."""
    if not text:
        return set()
    out: set[str] = set()
    for w in _WORD_RE.findall(text.lower()):
        if len(w) < 3 or w.isdigit() or w in _STOPWORDS:
            continue
        out.add(w)
    return out


def select_prepass_groups(
    message: str,
    catalog: "DeferredCatalog",
    *,
    max_groups: int = MAX_PREPASS_GROUPS,
) -> list[str]:
    """Return up to ``max_groups`` deferred group keys to pre-activate.

    A group qualifies if a query word hits its label/key/tool names
    (strong signal), or matches its tools' description words (weaker,
    weighted lower).

    - Direct hit (label / key / tool name) score: +3 per match
    - Description hit score: +1 per match

    Ranked by score desc, then key asc for determinism. Ties broken by
    key so the same query always picks the same groups (helpful for
    reproducibility in test transcripts and for operators reviewing
    conversation logs).
    """
    qwords = _tokens(message)
    if not qwords or catalog.is_empty:
        return []

    scored: list[tuple[int, str]] = []
    for key, names in catalog.groups.items():
        label_bag = _tokens(catalog.group_labels.get(key, "") + " " + key)
        name_bag: set[str] = set()
        desc_bag: set[str] = set()
        for n in names:
            fn = (catalog.descriptors_by_name.get(n) or {}).get("function") or {}
            name_bag |= _tokens(fn.get("name", ""))
            desc_bag |= _tokens(fn.get("description", ""))

        direct = qwords & (label_bag | name_bag)
        desc_hits = qwords & desc_bag
        if direct or desc_hits:
            score = len(direct) * 3 + len(desc_hits)
            scored.append((score, key))

    # Descending score, then ascending key for determinism on ties.
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [key for _, key in scored[:max_groups]]
