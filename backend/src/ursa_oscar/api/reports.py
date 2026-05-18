"""Reports API — Phase 6 Ticket 6.3.

Three endpoints:

    POST /api/v1/reports/generate
        Generate (or cache-hit) a PDF for a template + date range.
        Returns the PDF binary as application/pdf with Content-Disposition.
        Cached for free on subsequent identical calls.

    GET  /api/v1/reports/preview-metadata
        Cheap preview: collects data, returns sections/page-count/methodology
        without WeasyPrint render. Lets the UI show what's in the PDF
        before triggering the full render.

    GET  /api/v1/reports/download/{fingerprint}
        Look up a cached PDF by fingerprint and stream it as
        application/pdf. Used by the MCP tool's returned URL.

Per Decision 6.3-B the PDFs cache via the same analytical_cache table
as 6.1's correlation and 6.2's prediction. result_json stores a JSON
envelope where the actual PDF binary is base64-encoded.
"""
from __future__ import annotations

import base64
import io
import logging
from datetime import date as date_t
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from ..analytics.cache import AnalyticalCache, cached_compute
from ..reports.generator import (
    known_templates,
    preview_report_metadata,
    render_report_pdf,
)
from ..reports.methodology_registry import MissingMethodologyError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])


TemplateKey = Literal[
    "full_clinical_report", "summary_report", "analytical_report",
]


class GenerateReportRequest(BaseModel):
    template: TemplateKey
    start_date: date_t
    end_date: date_t
    recompute: bool = Field(
        default=False,
        description="Bypass cache and force a fresh render.",
    )


@router.post("/generate")
def generate_report_endpoint(
    body: GenerateReportRequest, request: Request,
):
    """Generate or fetch from cache. Returns the PDF binary."""
    if body.end_date < body.start_date:
        raise HTTPException(
            status_code=400,
            detail="end_date must be on or after start_date.",
        )
    if body.template not in known_templates():
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown template '{body.template}'. Known: "
                f"{known_templates()}"
            ),
        )

    db = request.app.state.db
    user_profile = _load_profile(request)
    cache = AnalyticalCache(db)
    params = {
        "template": body.template,
        "start_date": body.start_date.isoformat(),
        "end_date": body.end_date.isoformat(),
    }

    with cached_compute(
        cache,
        tool_name="generate_report",
        params=params,
        start_date=body.start_date,
        end_date=body.end_date,
        recompute=body.recompute,
    ) as ctx:
        if ctx.hit:
            envelope = ctx.cached_result
            pdf_b64 = envelope["data"].get("pdf_base64")
            if pdf_b64:
                pdf_bytes = base64.b64decode(pdf_b64)
                filename = envelope["data"].get("filename") or _build_filename(
                    body.template, body.start_date, body.end_date,
                )
                return _pdf_response(pdf_bytes, filename)
            # Cached entry has metadata but no PDF binary (test scaffolding
            # or a partial-cache bug). Fall through to re-render.
            logger.warning(
                "analytical_cache hit for generate_report but no pdf_base64 "
                "in payload — falling through to fresh render"
            )

        try:
            pdf_bytes, metadata = render_report_pdf(
                db, body.template, body.start_date, body.end_date, user_profile,
            )
        except MissingMethodologyError as e:
            # Decision 6.3-D: missing methodology entry is a hard failure.
            # Surface as 500 so it's loud — operator should never see
            # this in production; means a developer shipped a new
            # analytical method without registering it.
            logger.exception(
                "Report generation failed: missing methodology entry",
            )
            raise HTTPException(
                status_code=500,
                detail=(
                    f"Report generation failed: {e}. This is a "
                    f"server-side bug — every analytical method MUST "
                    f"have a methodology registry entry."
                ),
            )
        except Exception as e:
            logger.exception("Report generation failed")
            raise HTTPException(
                status_code=500,
                detail=f"Report generation failed: {type(e).__name__}: {e}",
            )

        filename = _build_filename(body.template, body.start_date, body.end_date)
        # Cache the binary as base64 in the analytical_cache envelope.
        # The metadata block becomes the cache-visible data; the binary
        # is opaque to other cache consumers.
        envelope = {
            "ok": True,
            "data": {
                **metadata,
                "filename": filename,
                "pdf_base64": base64.b64encode(pdf_bytes).decode("ascii"),
            },
        }
        ctx.store(envelope)
        return _pdf_response(pdf_bytes, filename)


@router.get("/preview-metadata")
def preview_metadata_endpoint(
    request: Request,
    template: TemplateKey = Query(...),
    start_date: date_t = Query(...),
    end_date: date_t = Query(...),
) -> dict[str, Any]:
    """Run data collection only; skip WeasyPrint. Returns the metadata
    that lets the UI show 'what's in the PDF' before generating."""
    if end_date < start_date:
        raise HTTPException(
            status_code=400,
            detail="end_date must be on or after start_date.",
        )
    if template not in known_templates():
        raise HTTPException(status_code=400, detail=f"Unknown template '{template}'.")

    db = request.app.state.db
    user_profile = _load_profile(request)
    try:
        return preview_report_metadata(
            db, template, start_date, end_date, user_profile,
        )
    except MissingMethodologyError as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/download/{fingerprint}")
def download_report_by_fingerprint(fingerprint: str, request: Request):
    """Look up a cached PDF by its analytical_cache fingerprint and
    stream it as application/pdf. Used by the MCP tool's returned URL —
    Claude.ai gets a clickable URL the user can open in a browser tab.

    No cache fall-through; if the fingerprint doesn't match a cached
    entry, return 404. Operator can regenerate via the UI."""
    cache = AnalyticalCache(request.app.state.db)
    envelope = cache.get(fingerprint)
    if envelope is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No cached report with fingerprint {fingerprint[:12]}…. "
                f"The cache may have been invalidated by a data change "
                f"or cleared. Generate the report again from the Reports "
                f"page."
            ),
        )
    data = envelope.get("data") or {}
    pdf_b64 = data.get("pdf_base64")
    if not pdf_b64:
        # Cached entry exists but isn't a report (could be a correlation
        # or prediction entry with the same fingerprint shape — defensive).
        raise HTTPException(
            status_code=404,
            detail="Cached entry is not a PDF report.",
        )
    try:
        pdf_bytes = base64.b64decode(pdf_b64)
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Cached PDF blob is not valid base64: {e}",
        )
    filename = data.get("filename") or "URSA-OSCAR-report.pdf"
    return _pdf_response(pdf_bytes, filename)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _pdf_response(pdf_bytes: bytes, filename: str) -> StreamingResponse:
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Length": str(len(pdf_bytes)),
        },
    )


def _build_filename(template: str, start: date_t, end: date_t) -> str:
    short = template.replace("_report", "")
    return f"URSA-OSCAR_{short}_{start.isoformat()}_{end.isoformat()}.pdf"


def _load_profile(request: Request) -> dict[str, Any] | None:
    """Best-effort profile lookup — same pattern as the AI proxy's
    profile loader. Reports without a profile still render."""
    try:
        from ..config import get_settings
        from ..storage import profile_store

        settings = get_settings()
        profile_path = settings.db_path.parent / "profile.json"
        return profile_store.read_raw(profile_path)
    except Exception:
        logger.exception("reports: failed to load profile; continuing without")
        return None
