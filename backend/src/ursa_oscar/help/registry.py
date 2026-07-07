"""Backend mirror of the Help topic registry.

Metadata (slug, title, section, keywords) is defined here in Python
so the api container doesn't need to parse TypeScript. The bodies
are loaded from .md files copied into the container at build time
from ``frontend/src/help/content/``.

The no-drift regression test verifies this metadata matches what
``frontend/src/help/topics.ts`` declares — both must have the same
37 entries with matching slugs / titles / sections.

Layout matches the architect's outline:
  - Getting started (4)
  - Using URSA-OSCAR (8 — Profile and AI chat are use topics)
  - Understanding the data (5)
  - Methodology (6 — verbatim from methodology_registry.py)
  - Architecture and deployment (5)
  - Troubleshooting (5)
  - About URSA-OSCAR (4)

Total: 37 topics.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Optional

logger = logging.getLogger(__name__)


SectionName = Literal[
    "Getting started",
    "Using URSA-OSCAR",
    "Understanding the data",
    "Methodology",
    "Architecture and deployment",
    "Troubleshooting",
    "About URSA-OSCAR",
]


@dataclass(frozen=True)
class HelpTopic:
    slug: str
    title: str
    section: SectionName
    keywords: list[str]
    body: str


# ---------------------------------------------------------------------------
# Metadata table — must match frontend/src/help/topics.ts
# ---------------------------------------------------------------------------

_METADATA: list[tuple[str, str, SectionName, list[str]]] = [
    # Getting started
    ("what-is-ursa-oscar", "What is URSA-OSCAR?", "Getting started",
        ["intro", "overview", "introduction"]),
    ("first-run-setup", "First-run setup", "Getting started",
        ["setup", "install", "bootstrap", "password", "first time"]),
    ("importing-sd-card", "Importing your first SD card", "Getting started",
        ["import", "sd card", "datalog", "upload", "edf"]),
    ("quick-tour", "Quick tour of the UI", "Getting started",
        ["ui", "navigation", "pages", "tour"]),

    # Using URSA-OSCAR
    ("using-overview", "The Overview page", "Using URSA-OSCAR",
        ["overview", "home", "heatmap", "calendar"]),
    ("using-daily-view", "The Daily View", "Using URSA-OSCAR",
        ["daily", "night", "detail", "eventrug", "sessions"]),
    ("using-statistics", "The Statistics page", "Using URSA-OSCAR",
        ["statistics", "aggregate", "histogram", "usage rate"]),
    ("using-trends", "The Trends page", "Using URSA-OSCAR",
        ["trends", "regression", "correlation", "prediction", "lag"]),
    ("using-reports", "Reports", "Using URSA-OSCAR",
        ["pdf", "report", "clinical", "provider"]),
    ("using-manual-logs", "Manual logs", "Using URSA-OSCAR",
        ["log", "medication", "symptom", "alertness", "subjective"]),
    ("using-profile", "Profile", "Using URSA-OSCAR",
        ["profile", "diagnosis", "medications", "goals", "equipment"]),
    ("using-ai-chat", "The AI chat panel", "Using URSA-OSCAR",
        ["ai", "chat", "assistant", "claude", "openai", "tool calling"]),

    # Understanding the data
    ("nightly-summary", "What's in a nightly summary", "Understanding the data",
        ["nightly summary", "fields", "schema", "data dictionary"]),
    ("ahi-and-subindices", "AHI and its sub-indices", "Understanding the data",
        ["ahi", "apnea", "hypopnea", "central", "obstructive", "rera"]),
    ("pressure-metrics", "Pressure metrics", "Understanding the data",
        ["pressure", "median", "p95", "epap", "ipap", "bipap", "epr"]),
    ("leak-metrics", "Leak metrics", "Understanding the data",
        ["leak", "mask", "redline", "large leak"]),
    ("sessions-vs-nights", "Sessions vs nights", "Understanding the data",
        ["session", "night", "datalog", "noon-split"]),

    # Methodology
    ("methodology-pearson-correlation", "Pearson Correlation", "Methodology",
        ["pearson", "correlation", "method", "pairwise_correlation_pearson"]),
    ("methodology-partial-correlation", "Partial Correlation (multivariate)", "Methodology",
        ["partial correlation", "multivariate", "method", "partial_correlation_pearson"]),
    ("methodology-lag-correlation", "Time-shifted Cross-Correlation", "Methodology",
        ["lag", "cross-correlation", "bootstrap", "method", "cross_correlation_with_bootstrap_ci"]),
    ("methodology-ridge-regression", "Ridge Regression with Prediction Intervals", "Methodology",
        ["ridge", "regression", "prediction", "counterfactual", "method", "ridge_regression_cv_with_quantile_intervals"]),
    ("methodology-linear-trend", "Linear Trend (Least-Squares)", "Methodology",
        ["trend", "linear", "least squares", "projection", "method", "linear_regression_least_squares"]),
    ("methodology-period-comparison", "Period Comparison", "Methodology",
        ["compare", "period", "method", "compare_periods_mean_difference"]),

    # Architecture and deployment
    ("arch-overview", "Architecture overview", "Architecture and deployment",
        ["architecture", "containers", "docker", "data flow", "mcp", "watcher"]),
    ("arch-single-tenant", "Single-tenant trust boundary", "Architecture and deployment",
        ["single-tenant", "trust", "security", "operator", "tenancy"]),
    ("arch-network-security", "Network security", "Architecture and deployment",
        ["network", "security", "tls", "https", "cookie", "jwt", "auth", "rate limit"]),
    ("arch-multi-instance", "Multi-instance deployments", "Architecture and deployment",
        ["multi-instance", "household", "multiple users", "separate"]),
    ("arch-deployment", "Deployment topologies", "Architecture and deployment",
        ["deployment", "truenas", "dockge", "synology", "qnap", "compose"]),
    # 1.1.11 — what URSA sends to the model per turn (context budget)
    ("arch-ai-context", "What URSA sends to the AI model",
        "Architecture and deployment",
        [
            "ai", "context", "system prompt", "tools", "tokens",
            "context window", "local llm", "gemma", "qwen", "deepseek",
            "context budget", "prompt caching",
        ]),

    # Troubleshooting
    ("troubleshoot-import", "Import not finding files", "Troubleshooting",
        ["import", "datalog", "sd card", "troubleshoot", "not finding"]),
    ("troubleshoot-watcher", "Watcher not auto-importing", "Troubleshooting",
        ["watcher", "auto-import", "quiescence", "webhook"]),
    ("troubleshoot-ai-chat", "AI assistant not responding", "Troubleshooting",
        ["ai", "chat", "not responding", "provider", "tool call"]),
    ("troubleshoot-mcp", "MCP connector issues", "Troubleshooting",
        ["mcp", "oauth", "connector", "claude.ai", "sse"]),
    ("troubleshoot-password-recovery", "Recovering from a lost password", "Troubleshooting",
        ["password", "recovery", "lost", "forgot", "bootstrap"]),

    # About URSA-OSCAR
    ("about-credits", "Credits and OSCAR attribution", "About URSA-OSCAR",
        ["credits", "oscar", "attribution", "thanks"]),
    ("about-license", "License", "About URSA-OSCAR",
        ["license", "gpl", "gpl-3.0", "open source", "copyleft"]),
    ("about-version", "Version and release notes", "About URSA-OSCAR",
        ["version", "release", "changelog", "history"]),
    ("about-future-direction", "Future direction", "About URSA-OSCAR",
        ["future", "direction", "roadmap", "planned", "deferred"]),
]


# ---------------------------------------------------------------------------
# Body loader
# ---------------------------------------------------------------------------

# Path inside the api container where the Dockerfile copies the
# markdown files. Override via env if the deployment lays things out
# differently (e.g., test fixtures pointing at the source tree).
_DEFAULT_CONTENT_DIR = Path("/app/help/content")


def _resolve_content_dir() -> Path:
    """Find the help content directory at module-import time.

    Checks the container-image path first, then falls back to the
    repository's frontend/src/help/content/ when running in dev
    (e.g., backend tests against an uninstalled checkout)."""
    if _DEFAULT_CONTENT_DIR.is_dir():
        return _DEFAULT_CONTENT_DIR
    # Dev fallback — walk up from this file to the repo root and look
    # for the frontend content dir.
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "frontend" / "src" / "help" / "content"
        if candidate.is_dir():
            return candidate
    # Last resort — return the default and let _load fail loudly.
    return _DEFAULT_CONTENT_DIR


_CONTENT_DIR = _resolve_content_dir()


def _load_body(slug: str) -> str:
    """Read one topic's markdown body from disk."""
    path = _CONTENT_DIR / f"{slug}.md"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        logger.warning(
            "Help topic '%s' has no markdown body at %s. The registry "
            "metadata references this slug; either the .md file is "
            "missing from the image build, or the metadata table needs "
            "the entry removed.",
            slug, path,
        )
        return ""


# ---------------------------------------------------------------------------
# Eager-loaded registry — built at module import time
# ---------------------------------------------------------------------------

TOPICS: list[HelpTopic] = [
    HelpTopic(
        slug=slug,
        title=title,
        section=section,
        keywords=list(keywords),
        body=_load_body(slug),
    )
    for (slug, title, section, keywords) in _METADATA
]

_BY_SLUG: dict[str, HelpTopic] = {t.slug: t for t in TOPICS}


# ---------------------------------------------------------------------------
# Public API — list / get / search
# ---------------------------------------------------------------------------


def list_topics(*, include_body: bool = False) -> list[dict]:
    """Return all topics in registry order. By default omits the body
    (for cheap directory listings). Set include_body=True to get the
    full markdown content per topic."""
    out: list[dict] = []
    for t in TOPICS:
        entry = {
            "slug": t.slug,
            "title": t.title,
            "section": t.section,
            "keywords": t.keywords,
        }
        if include_body:
            entry["body"] = t.body
        out.append(entry)
    return out


def topic_by_slug(slug: str) -> Optional[HelpTopic]:
    """Look up a single topic. Returns None when the slug isn't
    registered (caller should 404)."""
    return _BY_SLUG.get(slug)


def search_topics(query: str) -> list[HelpTopic]:
    """Case-insensitive substring search across title + keywords + body.
    Same three-tier ranking as the frontend implementation."""
    q = query.strip().lower()
    if not q:
        return []
    tier1: list[HelpTopic] = []
    tier2: list[HelpTopic] = []
    tier3: list[HelpTopic] = []
    for t in TOPICS:
        if q in t.title.lower():
            tier1.append(t)
            continue
        if any(q in k.lower() for k in t.keywords):
            tier2.append(t)
            continue
        if q in t.body.lower():
            tier3.append(t)
    return [*tier1, *tier2, *tier3]
