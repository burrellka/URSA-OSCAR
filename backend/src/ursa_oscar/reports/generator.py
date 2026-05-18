"""PDF generator — Phase 6 Ticket 6.3.

Public surface:

    render_report_html(db, template, start, end, ...) -> tuple[str, list[str]]
        Render the report to its HTML string. Returns
        ``(html, methods_used)``. Used by tests + by render_report_pdf.

    render_report_pdf(db, template, start, end, ...) -> tuple[bytes, dict]
        Full pipeline: collect data, render HTML, convert to PDF.
        Returns ``(pdf_bytes, metadata_dict)`` where metadata_dict is
        the same shape the preview-metadata endpoint returns.

    preview_report_metadata(db, template, start, end) -> dict
        Cheap probe — collects data, returns metadata WITHOUT rendering.
        Lets the UI show what'll be in the PDF before triggering the
        expensive WeasyPrint step.

Per Decision 6.3-A the templating layer is Jinja2 + WeasyPrint, both
pure Python. WeasyPrint requires Pango/Cairo/fontconfig system libs;
installed in the API Dockerfile.

Tests should call ``render_report_html`` and assert on the HTML string
(deterministic) rather than ``render_report_pdf`` (which needs the
system libs to run). Production callers use ``render_report_pdf``.
"""
from __future__ import annotations

import logging
from dataclasses import asdict
from datetime import date as date_t
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jinja2 import ChainableUndefined, Environment, FileSystemLoader

from ..storage.db import DuckDBManager
from .data_collectors import ReportContext, assemble_context

logger = logging.getLogger(__name__)


# Template registry — maps the wire-shape ``template`` parameter to
# (filename, human-readable label).
_TEMPLATE_REGISTRY: dict[str, tuple[str, str]] = {
    "full_clinical_report": ("full_clinical.html", "Full Clinical Report"),
    "summary_report": ("summary.html", "Summary Report"),
    "analytical_report": ("analytical.html", "Analytical Report"),
}


def known_templates() -> list[str]:
    return list(_TEMPLATE_REGISTRY.keys())


# Estimated page counts per template. Used by preview-metadata so the
# UI can show "8-12 pages expected" before rendering. Derived from
# representative renders against the operator's data + the regression
# fixtures; refine these if the actual page counts drift.
_TEMPLATE_PAGE_ESTIMATES: dict[str, int] = {
    "full_clinical_report": 10,
    "summary_report": 3,
    "analytical_report": 5,
}


_TEMPLATES_DIR = Path(__file__).parent / "templates"
_STATIC_DIR = Path(__file__).parent / "static"


def _jinja_env() -> Environment:
    """Construct a Jinja2 env scoped to our templates dir.
    ChainableUndefined lets missing-attribute chains (e.g.,
    ``ctx.overview.insufficient_data`` on the success path where the
    key is absent) silently evaluate to Undefined / falsy. We rely on
    the rendering-tests as the typo-catcher rather than StrictUndefined
    because the templates legitimately probe optional attributes."""
    return Environment(
        loader=FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
        undefined=ChainableUndefined,
    )


def _load_shared_css() -> str:
    css_path = _TEMPLATES_DIR / "_style.css"
    try:
        return css_path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("shared style not loadable: %s", e)
        return ""


def render_report_html(
    db: DuckDBManager,
    template_key: str,
    start: date_t,
    end: date_t,
    user_profile: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> tuple[str, ReportContext]:
    """Collect data + render the report to its HTML string. Returns
    ``(html, ctx)`` so callers can introspect what got rendered for
    tests / metadata.

    Does NOT run WeasyPrint — this function is the unit-testable seam.
    """
    if template_key not in _TEMPLATE_REGISTRY:
        raise ValueError(
            f"Unknown template_key '{template_key}'. "
            f"Known: {sorted(_TEMPLATE_REGISTRY.keys())}"
        )
    template_filename, template_label = _TEMPLATE_REGISTRY[template_key]
    gen_at = (generated_at or datetime.now(timezone.utc)).replace(microsecond=0)

    ctx = assemble_context(
        db=db,
        template_key=template_key,
        template_label=template_label,
        start=start, end=end,
        user_profile=user_profile,
        generated_at_iso=gen_at.isoformat(),
    )

    env = _jinja_env()
    tmpl = env.get_template(template_filename)
    html = tmpl.render(
        ctx=ctx,
        shared_css=_load_shared_css(),
    )
    return html, ctx


def render_report_pdf(
    db: DuckDBManager,
    template_key: str,
    start: date_t,
    end: date_t,
    user_profile: dict[str, Any] | None = None,
    generated_at: datetime | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Full pipeline: collect data, render HTML, convert to PDF.

    Returns ``(pdf_bytes, metadata)`` where metadata is the same shape
    ``preview_report_metadata`` returns. Requires WeasyPrint + system
    libs — production-only path; tests use ``render_report_html``.
    """
    html, ctx = render_report_html(
        db, template_key, start, end, user_profile, generated_at,
    )
    pdf_bytes = _html_to_pdf(html)
    metadata = _metadata_from_context(ctx, len(pdf_bytes))
    return pdf_bytes, metadata


def _html_to_pdf(html: str) -> bytes:
    """Run WeasyPrint. Imported lazily so non-PDF code paths (tests,
    preview-only endpoints) don't pay the WeasyPrint import cost +
    don't fail to load on dev environments without the system libs."""
    from weasyprint import HTML  # type: ignore[import-not-found]
    return HTML(string=html, base_url=str(_STATIC_DIR)).write_pdf()


def _metadata_from_context(
    ctx: ReportContext, pdf_bytes_len: int = 0,
) -> dict[str, Any]:
    """Shape returned by preview-metadata + included alongside the PDF
    binary on generate. The UI uses this to render "what's in here"
    before / after rendering."""
    # Build the sections-included + sections-with-insufficient-data
    # lists by inspecting each section payload's `insufficient_data`
    # flag.
    sections_included: list[str] = []
    sections_with_insufficient_data: list[str] = []

    def _check(name: str, payload: dict[str, Any] | None) -> None:
        if not payload:
            return
        sections_included.append(name)
        if payload.get("insufficient_data"):
            sections_with_insufficient_data.append(name)

    _check("overview", ctx.overview)
    _check("trend_total_ahi", ctx.trend_total_ahi)
    _check("trend_p95_pressure", ctx.trend_p95_pressure)
    _check("trend_p95_leak", ctx.trend_p95_leak)
    # Pairwise correlations are a list; mark "included" if any present,
    # "insufficient" if every one refused.
    if ctx.pairwise_correlations:
        sections_included.append("pairwise_correlations")
        if all(c.get("insufficient_data") for c in ctx.pairwise_correlations):
            sections_with_insufficient_data.append("pairwise_correlations")
    _check("multivariate", ctx.multivariate)
    if ctx.lag_analyses:
        sections_included.append("lag_analyses")
        if all(la.get("insufficient_data") for la in ctx.lag_analyses):
            sections_with_insufficient_data.append("lag_analyses")
    _check("prediction", ctx.prediction)
    sections_included.append("methodology")  # always included

    confidence_for_predictions = None
    if not ctx.prediction.get("insufficient_data"):
        confidence_for_predictions = ctx.prediction.get("confidence_level")

    return {
        "template": ctx.template_key,
        "template_label": ctx.template_label,
        "estimated_page_count": _TEMPLATE_PAGE_ESTIMATES.get(ctx.template_key, 8),
        "sections_included": sections_included,
        "sections_with_insufficient_data": sections_with_insufficient_data,
        "n_nights_in_range": ctx.n_nights_in_range,
        "confidence_level_for_predictions": confidence_for_predictions,
        "methods_used": list(ctx.methods_used),
        "methodology_section_includes": [
            entry["name"] for entry in ctx.methodology
        ],
        "pdf_bytes": pdf_bytes_len,
        "generated_at": ctx.generated_at_iso,
        "date_range_start": ctx.date_range_start,
        "date_range_end": ctx.date_range_end,
    }


def preview_report_metadata(
    db: DuckDBManager,
    template_key: str,
    start: date_t,
    end: date_t,
    user_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Cheap preview — runs the data collection but NOT the WeasyPrint
    render. Lets the UI show "8-12 pages, 5 sections, 47 nights"
    before the user commits to the full render."""
    _html, ctx = render_report_html(
        db, template_key, start, end, user_profile,
    )
    return _metadata_from_context(ctx, pdf_bytes_len=0)


# Convenience for tests that need to introspect a ReportContext.
def context_as_dict(ctx: ReportContext) -> dict[str, Any]:
    return asdict(ctx)
