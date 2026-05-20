"""generate_report — Phase 6 Ticket 6.3 MCP tool.

Proxy over POST /api/v1/reports/generate. Triggers server-side PDF
rendering against one of three templates, returns metadata about the
generated PDF + a download URL the user can open in their browser to
pick up the actual PDF.

Per Decision 6.3-A the LLM does NOT see / interpret the PDF contents.
The PDF is authoritative; this tool exists so the LLM can route the
user's "generate me a report" requests to the deterministic
server-side renderer, then point the user at the URL.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
from datetime import date as date_t

import httpx

from ..client import api_post, get_client
from ..envelope import _err
from ..server import mcp


@mcp.tool()
def generate_report(
    template: str,
    start_date: str,
    end_date: str,
    recompute: bool = False,
) -> dict:
    """Generate a multi-page PDF report combining the user's CPAP data,
    analytical findings, and methodology disclosures.

    Use this tool when the user asks to generate a PDF report for their
    sleep medicine provider, an upcoming appointment, or their own
    records. Examples:

        "Generate a report I can take to my sleep doctor"
        "Create a summary PDF for my appointment next week"
        "Make me a full clinical report for the last 90 days"
        "Analytical PDF for the past month"

    Templates:
      - "full_clinical_report" (8-12 pages): comprehensive overview,
        trends, pairwise + multivariate + lag correlations, predictions,
        methodology. For annual reviews or major treatment-change
        consultations.
      - "summary_report" (2-3 pages): condensed — key metrics, recent
        trends, top correlations, methodology. For routine follow-ups.
      - "analytical_report" (4-6 pages): skips OSA boilerplate, focuses
        on multivariate + lag + predictions + methodology. For
        established care where analytical updates are the conversation.

    Sample-size discipline: PDFs include explicit "insufficient data"
    sections rather than omitting them when an underlying analysis
    refuses (e.g., predictions below the n=30 floor). The methodology
    section is non-optional in every PDF and explains every analytical
    method actually used.

    When relaying to the user:
      - Tell them WHICH template you generated and WHAT date range
      - Tell them WHERE to download it (the download_url field)
      - Mention any sections that came back insufficient_data — they
        should know what's missing before bringing the PDF to a
        clinician
      - Do NOT summarize the PDF's contents verbatim. The PDF is
        authoritative; if they have follow-up questions about specific
        findings, use the underlying analytical tools (
        analyze_multivariate_correlation, analyze_prediction, etc.)
        to query the same data and explain conversationally
      - Recommend they discuss findings with their sleep medicine
        provider; never suggest the PDF replaces a clinical visit
      - Never interpret the PDF prescriptively ('you should change X')

    Args:
        template: one of "full_clinical_report", "summary_report",
            "analytical_report"
        start_date: YYYY-MM-DD inclusive
        end_date: YYYY-MM-DD inclusive
        recompute: bypass cache and re-render. Default false.

    Returns:
        {"ok": true, "data": {
            "template": "...",
            "template_label": "Summary Report",
            "estimated_page_count": 3,
            "n_nights_in_range": 47,
            "sections_included": [...],
            "sections_with_insufficient_data": [],
            "confidence_level_for_predictions": "moderate" | null,
            "methodology_section_includes": ["Pearson Correlation", ...],
            "filename": "URSA-OSCAR_summary_2026-04-01_2026-05-17.pdf",
            "download_url": "http://<host>/api/v1/reports/download/<fp>",
            "pdf_bytes": 145678
        }}
    """
    valid_templates = {
        "full_clinical_report", "summary_report", "analytical_report",
    }
    if template not in valid_templates:
        return _err(
            f"Unknown template '{template}'. Use one of: {sorted(valid_templates)}",
            code="INVALID_INPUT",
        )
    for label, value in [("start_date", start_date), ("end_date", end_date)]:
        try:
            date_t.fromisoformat(value)
        except ValueError:
            return _err(f"Invalid date '{value}' for {label}", code="INVALID_INPUT")

    # The /generate endpoint returns the PDF as application/pdf. The MCP
    # surface wants metadata + a download URL, not the binary. So we
    # call /preview-metadata for the structural info, then trigger
    # /generate with the same params to ensure the PDF is rendered + cached,
    # then compute the cache fingerprint to build the download URL.
    try:
        meta = api_post(
            "/api/v1/reports/preview-metadata",
            json_body=None,  # api_post is POST-only; use a sibling helper below
        )
    except Exception:
        # Fall through to GET below.
        meta = None

    # api_post hard-codes POST; the preview is GET. Use the auth-attaching
    # get_client() so the API's Phase 6.4 _AUTH_REQUIRED check passes.
    # 1.1.1 fix — previously used raw httpx.Client without bearer headers
    # and 401'd on every call.
    api_base = _api_base_url()
    try:
        with get_client(timeout=120.0) as client:
            preview = client.get(
                "/api/v1/reports/preview-metadata",
                params={
                    "template": template,
                    "start_date": start_date,
                    "end_date": end_date,
                },
            )
            preview.raise_for_status()
            preview_data = preview.json()

            # Trigger render so the cache has it.
            gen = client.post(
                "/api/v1/reports/generate",
                json={
                    "template": template,
                    "start_date": start_date,
                    "end_date": end_date,
                    "recompute": bool(recompute),
                },
            )
            gen.raise_for_status()
            pdf_bytes = len(gen.content)
            cd = gen.headers.get("content-disposition", "")
            filename = _filename_from_content_disposition(cd) or _build_filename(
                template, start_date, end_date,
            )
    except httpx.HTTPStatusError as e:
        return _err(
            f"Report generation failed: {e.response.status_code} {e.response.text[:200]}",
            code="ERROR",
        )
    except httpx.RequestError as e:
        return _err(f"Could not reach API: {e}", code="ERROR")

    # Compute the cache fingerprint the same way analytics/cache.py
    # does, so we can construct the download URL. The fingerprint over
    # ``(tool_name, sorted params, data_version_hash)`` would require a
    # round-trip to the cache stats endpoint — instead, ask the API
    # directly which fingerprint matches.
    try:
        with get_client(timeout=10.0) as client:
            stats = client.get("/api/v1/analytics/cache/stats")
            stats.raise_for_status()
            # The most recent entry under tool_name="generate_report"
            # is the one we just stored. We can't read fingerprints from
            # stats alone — for v1, return the download URL with a
            # special marker; the operator's UI is the authoritative
            # download path.
    except Exception:
        pass

    # The MCP-side download URL points at the API host (the operator's
    # LAN URL or whatever public mapping they've set up). The MCP
    # container's /reports/download/... proxy would be cleaner but
    # adds a layer; for v1 we return the API URL the operator's
    # exposing.
    public_url = os.environ.get("URSA_OSCAR_PUBLIC_API_URL")
    download_path = (
        f"/api/v1/reports/generate"
        # The actual cache-by-fingerprint download requires the
        # fingerprint, which the API hasn't surfaced. As a fallback,
        # surface a regenerate-and-download URL pattern the operator's
        # browser can open. The cache will hit on the second call so
        # the operator gets the same blob without an extra render.
    )
    download_url = (
        f"{public_url.rstrip('/')}{download_path}" if public_url
        else f"{api_base}{download_path}"
    )

    return {
        "ok": True,
        "data": {
            **preview_data,
            "filename": filename,
            "pdf_bytes": pdf_bytes,
            "download_url": download_url,
            "download_method": "POST",
            "download_body": {
                "template": template,
                "start_date": start_date,
                "end_date": end_date,
            },
            "download_note": (
                "The PDF is already rendered + cached server-side. Open "
                "the Reports page in URSA-OSCAR (/reports) to download "
                "with one click, or POST the download_body to the "
                "download_url to fetch the same cached PDF without "
                "re-rendering."
            ),
        },
    }


def _api_base_url() -> str:
    """Resolve the API base URL the MCP container talks to. The MCP
    container reads URSA_OSCAR_API_URL on startup; reuse that env."""
    return os.environ.get("URSA_OSCAR_API_URL", "http://ursa-oscar-api:8000")


def _build_filename(template: str, start: str, end: str) -> str:
    short = template.replace("_report", "")
    return f"URSA-OSCAR_{short}_{start}_{end}.pdf"


def _filename_from_content_disposition(cd: str) -> str | None:
    """Parse `filename="..."` out of a Content-Disposition header."""
    if not cd:
        return None
    parts = [p.strip() for p in cd.split(";")]
    for p in parts:
        if p.lower().startswith("filename="):
            name = p[len("filename="):].strip().strip('"')
            return name or None
    return None
