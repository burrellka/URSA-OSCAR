"""Phase 6 Ticket 6.3 — PDF report regression tests.

Coverage:
  - Methodology registry: lookup, dedupe, strict mode raises on
    unknown method
  - Each data collector returns sane shape (success OR insufficient_data
    with `method` field for the registry collector)
  - Each template renders to HTML; methodology section present;
    every method used has a methodology card
  - INSUFFICIENT_DATA propagation: a template with mostly-empty data
    renders explicit "insufficient data" fragments
  - API endpoints: generate, preview-metadata, download-by-fingerprint
  - Cache hit/miss for generate (mock the WeasyPrint render so tests
    don't need GTK system libs)
  - Filename convention

The actual WeasyPrint render is mocked at the ``_html_to_pdf`` seam.
Tests verify the HTML the generator produces and the cache lifecycle;
the WeasyPrint binding is one library call we don't have to revalidate.
"""
from __future__ import annotations

import base64
from datetime import date as date_t
from datetime import timedelta

import numpy as np
import pytest
from fastapi.testclient import TestClient

from ursa_oscar.main import create_app
from ursa_oscar.reports.data_collectors import (
    DEFAULT_LAG_PAIRS,
    DEFAULT_MULTIVARIATE_PREDICTORS,
    DEFAULT_PAIRWISE_CORRELATIONS,
    DEFAULT_PREDICTION_PREDICTORS,
    assemble_context,
    collect_lag_analyses,
    collect_multivariate,
    collect_overview,
    collect_pairwise_correlations,
    collect_prediction,
    collect_trend,
)
from ursa_oscar.reports.generator import (
    known_templates,
    preview_report_metadata,
    render_report_html,
)
from ursa_oscar.reports.methodology_registry import (
    METHODOLOGY_REGISTRY,
    MissingMethodologyError,
    collect_methodology_descriptions,
    lookup_methodology,
)
from ursa_oscar.storage.db import DuckDBManager
from ursa_oscar.storage.migrations import apply_migrations


# ----------------------------------------------------------------------
# Methodology registry
# ----------------------------------------------------------------------


def test_methodology_registry_has_all_phase6_methods():
    """Every method literal returned by the Phase 6 analytical tools
    must be registered. This test will fail loudly if a new method
    ships without a registry entry — exactly the audit-trail discipline
    Decision 6.3-D requires."""
    required_methods = {
        "pairwise_correlation_pearson",          # 6.0 analyze_correlation
        "partial_correlation_pearson",           # 6.1 multivariate
        "cross_correlation_with_bootstrap_ci",   # 6.1 lag
        "ridge_regression_cv_with_quantile_intervals",  # 6.2 predict
        "linear_regression_least_squares",       # 6.0 trend
        "compare_periods_mean_difference",       # 6.0 compare_periods
    }
    missing = required_methods - set(METHODOLOGY_REGISTRY.keys())
    assert not missing, (
        f"These methods are returned by analytical tools but have NO "
        f"registry entry: {missing}. Add them to "
        f"reports/methodology_registry.py — Decision 6.3-D requires "
        f"every method to be registered before the PDF can ship."
    )


def test_methodology_lookup_returns_entry_for_known_method():
    e = lookup_methodology("partial_correlation_pearson")
    assert e is not None
    assert "name" in e
    assert "description" in e
    assert "limitations" in e
    assert "sample_size_note" in e


def test_methodology_lookup_returns_none_for_unknown():
    assert lookup_methodology("not_a_method") is None


def test_collect_methodology_deduplicates_and_preserves_order():
    keys = [
        "pairwise_correlation_pearson",
        "linear_regression_least_squares",
        "pairwise_correlation_pearson",  # dup
        "partial_correlation_pearson",
    ]
    entries = collect_methodology_descriptions(keys)
    assert [e["name"] for e in entries] == [
        "Pearson Correlation",
        "Linear Trend (Least-Squares Regression)",
        "Partial Correlation (multivariate)",
    ]


def test_collect_methodology_strict_mode_raises_on_unknown():
    with pytest.raises(MissingMethodologyError):
        collect_methodology_descriptions(["totally_made_up_method"])


def test_collect_methodology_lenient_mode_skips_unknown():
    out = collect_methodology_descriptions(
        ["totally_made_up_method", "pairwise_correlation_pearson"],
        strict=False,
    )
    assert [e["name"] for e in out] == ["Pearson Correlation"]


# ----------------------------------------------------------------------
# Data collectors — against a seeded DB
# ----------------------------------------------------------------------


@pytest.fixture
def seeded_db(tmp_path):
    """Seeded DuckDB with 60 nights of synthetic data — enough for every
    analytical floor (n>=30 for prediction, n>=15 for multivariate, etc.)."""
    db = DuckDBManager(tmp_path / "reports.duckdb", read_only=False)
    apply_migrations(db)
    rng = np.random.default_rng(42)
    base_date = date_t(2026, 1, 1)
    with db.serialized() as conn:
        for i in range(60):
            d = base_date + timedelta(days=i)
            p_pressure = float(rng.normal(9.0, 1.5))
            p_leak = float(rng.normal(20.0, 5.0))
            noise = float(rng.normal(0, 0.5))
            ahi = -0.4 * p_pressure + 0.1 * p_leak + 6.0 + noise
            central = max(0.0, float(rng.normal(2.0, 0.7)))
            obstructive = max(0.0, float(rng.normal(1.5, 0.5)))
            conn.execute(
                """
                INSERT INTO nightly_summary (
                    date, total_ahi, central_ahi, obstructive_ahi,
                    p95_pressure, p95_leak,
                    total_time_minutes, session_count, last_updated
                ) VALUES (?, ?, ?, ?, ?, ?, 420, 1, CURRENT_TIMESTAMP)
                """,
                (d, ahi, central, obstructive, p_pressure, p_leak),
            )
    yield db, base_date, base_date + timedelta(days=59)
    db.close()


def test_collect_overview_returns_metrics(seeded_db):
    db, start, end = seeded_db
    out = collect_overview(db, start, end)
    assert "insufficient_data" not in out
    assert out["n_nights"] == 60
    assert out["mean_total_ahi"] > 0


def test_collect_overview_empty_range(seeded_db):
    db, start, end = seeded_db
    out = collect_overview(db, date_t(2030, 1, 1), date_t(2030, 1, 31))
    assert out.get("insufficient_data") is True


def test_collect_trend_returns_method_field(seeded_db):
    db, start, end = seeded_db
    out = collect_trend(db, "total_ahi", start, end)
    assert out["method"] == "linear_regression_least_squares"


def test_collect_pairwise_correlations_all_have_method(seeded_db):
    db, start, end = seeded_db
    results = collect_pairwise_correlations(db, DEFAULT_PAIRWISE_CORRELATIONS, start, end)
    assert len(results) == len(DEFAULT_PAIRWISE_CORRELATIONS)
    for r in results:
        assert r["method"] == "pairwise_correlation_pearson"


def test_collect_multivariate_success_shape(seeded_db):
    db, start, end = seeded_db
    out = collect_multivariate(
        db, "total_ahi", DEFAULT_MULTIVARIATE_PREDICTORS, start, end,
    )
    assert out["method"] == "partial_correlation_pearson"
    assert "predictors" in out


def test_collect_prediction_success_shape(seeded_db):
    db, start, end = seeded_db
    out = collect_prediction(
        db, "total_ahi", DEFAULT_PREDICTION_PREDICTORS, start, end,
    )
    assert out["method"] == "ridge_regression_cv_with_quantile_intervals"
    assert "prediction" in out
    assert out["prediction"]["point_estimate"] is not None


def test_collect_prediction_insufficient_data_path():
    """A 10-night window can't fit a predictive model (n<30 floor)."""
    db = DuckDBManager(":memory:", read_only=False)
    apply_migrations(db)
    rng = np.random.default_rng(7)
    base = date_t(2026, 1, 1)
    with db.serialized() as conn:
        for i in range(10):
            d = base + timedelta(days=i)
            conn.execute(
                """
                INSERT INTO nightly_summary (
                    date, total_ahi, p95_pressure, p95_leak, last_updated
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """,
                (d, float(rng.normal(5, 1)), float(rng.normal(9, 1.5)), float(rng.normal(20, 5))),
            )
    out = collect_prediction(
        db, "total_ahi", DEFAULT_PREDICTION_PREDICTORS,
        base, base + timedelta(days=9),
    )
    assert out["insufficient_data"] is True
    assert out["method"] == "ridge_regression_cv_with_quantile_intervals"
    assert out["n_training_nights"] == 10
    db.close()


# ----------------------------------------------------------------------
# Generator + templates
# ----------------------------------------------------------------------


def test_known_templates_returns_three():
    assert set(known_templates()) == {
        "full_clinical_report", "summary_report", "analytical_report",
    }


@pytest.mark.parametrize("template_key", [
    "full_clinical_report", "summary_report", "analytical_report",
])
def test_render_report_html_succeeds_for_each_template(seeded_db, template_key):
    db, start, end = seeded_db
    html, ctx = render_report_html(db, template_key, start, end)
    assert isinstance(html, str)
    assert len(html) > 500
    assert ctx.template_key == template_key
    # Methodology section is non-optional (Decision 6.3-D).
    assert "Methodology" in html
    # Every method that appeared has a methodology card.
    for method_name_pretty in [m["name"] for m in ctx.methodology]:
        assert method_name_pretty in html


def test_full_clinical_template_includes_all_sections(seeded_db):
    db, start, end = seeded_db
    html, ctx = render_report_html(db, "full_clinical_report", start, end)
    # Top-level section headings.
    for heading in (
        "Overview",
        "Trends",
        "Pairwise correlations",
        "Multivariate analysis",
        "Time-shifted lag analysis",
        "Tonight's prediction",
        "Methodology",
    ):
        assert heading in html, f"missing section heading: {heading}"


def test_summary_template_skips_multivariate_body(seeded_db):
    """Summary template intentionally omits the multivariate / lag /
    prediction body sections — those are reserved for full_clinical
    and analytical templates."""
    db, start, end = seeded_db
    html, ctx = render_report_html(db, "summary_report", start, end)
    assert "Overview" in html
    assert "Key trends" in html
    assert "Top correlations" in html
    # Summary intentionally lacks these body section headings.
    # The Methodology section still names the methods even if the
    # body sections don't render them.
    assert "Multivariate analysis</h2>" not in html


def test_analytical_template_skips_overview(seeded_db):
    db, start, end = seeded_db
    html, _ = render_report_html(db, "analytical_report", start, end)
    assert "Multivariate analysis" in html
    assert "Tonight's prediction" in html
    assert "Time-shifted lag analysis" in html
    # No "Overview" body section in analytical (cover-page metadata
    # only).
    assert ">Overview</h2>" not in html


def test_insufficient_data_renders_explicit_fragments():
    """With a small DB (10 nights), the predict + multivariate sections
    refuse with INSUFFICIENT_DATA. The PDF should render explicit
    'insufficient data' fragments (Decision 6.3-E), not omit the
    sections."""
    db = DuckDBManager(":memory:", read_only=False)
    apply_migrations(db)
    rng = np.random.default_rng(11)
    base = date_t(2026, 1, 1)
    with db.serialized() as conn:
        for i in range(10):
            d = base + timedelta(days=i)
            conn.execute(
                """
                INSERT INTO nightly_summary (
                    date, total_ahi, p95_pressure, p95_leak,
                    total_time_minutes, session_count, last_updated
                ) VALUES (?, ?, ?, ?, 420, 1, CURRENT_TIMESTAMP)
                """,
                (
                    d,
                    float(rng.normal(5, 1)),
                    float(rng.normal(9, 1.5)),
                    float(rng.normal(20, 5)),
                ),
            )
    html, ctx = render_report_html(
        db, "full_clinical_report", base, base + timedelta(days=9),
    )
    # Section heading still rendered.
    assert "Tonight's prediction" in html
    # "Insufficient data" callout text present.
    assert "Insufficient data for predictive modeling" in html
    db.close()


def test_preview_report_metadata_returns_expected_shape(seeded_db):
    db, start, end = seeded_db
    meta = preview_report_metadata(db, "full_clinical_report", start, end)
    assert meta["template"] == "full_clinical_report"
    assert meta["estimated_page_count"] > 0
    assert "methodology" in meta["sections_included"]
    assert meta["n_nights_in_range"] == 60


# ----------------------------------------------------------------------
# API endpoints — mock WeasyPrint so tests run without GTK
# ----------------------------------------------------------------------


_FAKE_PDF_PREFIX = b"%PDF-1.4 fake-test-pdf "


@pytest.fixture
def api_client(tmp_path, monkeypatch):
    """TestClient with the seeded DB + a mocked WeasyPrint binding."""
    import ursa_oscar.config as _config_mod
    db_file = tmp_path / "reports_api.duckdb"
    monkeypatch.setenv("URSA_OSCAR_DB_PATH", str(db_file))
    _config_mod._settings = None

    seeder = DuckDBManager(db_file, read_only=False)
    apply_migrations(seeder)
    rng = np.random.default_rng(42)
    base = date_t(2026, 1, 1)
    with seeder.serialized() as conn:
        for i in range(60):
            d = base + timedelta(days=i)
            p_pressure = float(rng.normal(9.0, 1.5))
            p_leak = float(rng.normal(20.0, 5.0))
            ahi = -0.4 * p_pressure + 0.1 * p_leak + 6.0 + float(rng.normal(0, 0.5))
            conn.execute(
                """
                INSERT INTO nightly_summary (
                    date, total_ahi, central_ahi, obstructive_ahi,
                    p95_pressure, p95_leak,
                    total_time_minutes, session_count, last_updated
                ) VALUES (?, ?, 2.0, 1.5, ?, ?, 420, 1, CURRENT_TIMESTAMP)
                """,
                (d, ahi, p_pressure, p_leak),
            )
    seeder.close()

    # Mock the WeasyPrint binding at its seam. Tests don't need the
    # real binary; they just verify the wire shape + cache lifecycle.
    import ursa_oscar.reports.generator as _gen
    monkeypatch.setattr(
        _gen, "_html_to_pdf",
        lambda html: _FAKE_PDF_PREFIX + html[:50].encode("utf-8"),
    )

    app = create_app()
    with TestClient(app) as client:
        yield client
    _config_mod._settings = None


def test_generate_endpoint_returns_pdf_application(api_client):
    r = api_client.post("/api/v1/reports/generate", json={
        "template": "summary_report",
        "start_date": "2026-01-01",
        "end_date": "2026-03-01",
    })
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/pdf")
    assert r.content.startswith(_FAKE_PDF_PREFIX)
    cd = r.headers["content-disposition"]
    assert "URSA-OSCAR_summary_2026-01-01_2026-03-01.pdf" in cd


def test_generate_endpoint_rejects_inverted_range(api_client):
    r = api_client.post("/api/v1/reports/generate", json={
        "template": "summary_report",
        "start_date": "2026-03-01",
        "end_date": "2026-01-01",
    })
    assert r.status_code == 400


def test_generate_endpoint_rejects_unknown_template(api_client):
    r = api_client.post("/api/v1/reports/generate", json={
        "template": "not_a_real_template",
        "start_date": "2026-01-01",
        "end_date": "2026-03-01",
    })
    # Pydantic Literal rejection -> 422.
    assert r.status_code in (400, 422)


def test_preview_metadata_endpoint(api_client):
    r = api_client.get(
        "/api/v1/reports/preview-metadata",
        params={
            "template": "analytical_report",
            "start_date": "2026-01-01",
            "end_date": "2026-03-01",
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["template"] == "analytical_report"
    assert body["estimated_page_count"] > 0
    assert "methodology" in body["sections_included"]


def test_generate_endpoint_caches_on_second_call(api_client):
    """Second identical call should return the cached PDF bytes from
    the analytical_cache table — Decision 6.3-B."""
    params = {
        "template": "summary_report",
        "start_date": "2026-01-01",
        "end_date": "2026-03-01",
    }
    r1 = api_client.post("/api/v1/reports/generate", json=params)
    r2 = api_client.post("/api/v1/reports/generate", json=params)
    assert r1.status_code == 200 and r2.status_code == 200
    # Same bytes — cache hit returns the stored blob.
    assert r1.content == r2.content


def test_download_by_fingerprint_round_trip(api_client):
    """Generate → look up cache → download by fingerprint → same bytes."""
    params = {
        "template": "summary_report",
        "start_date": "2026-01-01",
        "end_date": "2026-03-01",
    }
    r_gen = api_client.post("/api/v1/reports/generate", json=params)
    assert r_gen.status_code == 200

    # Find the fingerprint in the analytical_cache.
    db = api_client.app.state.db
    with db.serialized() as conn:
        row = conn.execute(
            "SELECT fingerprint FROM analytical_cache WHERE tool_name = ? LIMIT 1",
            ("generate_report",),
        ).fetchone()
    assert row is not None, "expected analytical_cache entry from generate"
    fp = row[0]

    r_dl = api_client.get(f"/api/v1/reports/download/{fp}")
    assert r_dl.status_code == 200
    assert r_dl.headers["content-type"].startswith("application/pdf")
    assert r_dl.content == r_gen.content


def test_download_by_unknown_fingerprint_404(api_client):
    r = api_client.get("/api/v1/reports/download/nonexistent-fingerprint-1234")
    assert r.status_code == 404


def test_recompute_flag_bypasses_cache(api_client):
    """recompute=True forces a fresh render even if the entry exists."""
    params = {
        "template": "summary_report",
        "start_date": "2026-01-01",
        "end_date": "2026-03-01",
    }
    api_client.post("/api/v1/reports/generate", json=params)
    # Use a different param to force a different rendered HTML
    # snapshot inside the mock; that way we can tell whether
    # ctx.store actually overwrote the cache.
    import ursa_oscar.reports.generator as _gen
    _gen._html_to_pdf = lambda html: _FAKE_PDF_PREFIX + b"FRESH-RENDER"  # type: ignore[attr-defined]

    r = api_client.post(
        "/api/v1/reports/generate",
        json={**params, "recompute": True},
    )
    assert r.status_code == 200
    assert b"FRESH-RENDER" in r.content
