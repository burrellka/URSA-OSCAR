"""Usage-rate breakdown — Phase 0.13.4.

The operator's CPAP usage is not continuous. There are stretches of
nights where the mask was never worn (travel, illness, equipment swap,
or just an off night). Those nights produce no DATALOG directory or
an empty one, so no row gets written to ``nightly_summary``.

Analytical endpoints (compare-periods, trend, etc.) compute their
clinical metrics over the rows that DO exist — naturally dropping the
no-session nights, which matches AASM convention. But the operator
still benefits from seeing, alongside the metric:

  - How many nights were in the requested date range
  - How many of those had a therapy session (= rows in nightly_summary)
  - How many were skipped
  - Usage rate as a percentage

This module is the single source of truth for that breakdown so all
endpoints surface it consistently.

Definition of "night with therapy": a row exists in
``nightly_summary`` for that date. This intentionally INCLUDES nights
where the operator later excluded every session via the UI's session-
exclusion toggle — that operator action affects clinical metrics, not
the "did you turn the machine on" answer.
"""
from __future__ import annotations

from datetime import date as date_t
from typing import TypedDict

from ..storage.db import DuckDBManager


class UsageBreakdown(TypedDict):
    n_nights_in_range: int
    n_nights_with_therapy: int
    n_nights_skipped: int
    usage_rate_pct: float


def compute_usage_breakdown(
    db: DuckDBManager,
    start: date_t,
    end: date_t,
) -> UsageBreakdown:
    """Count nights with therapy data in [start, end] inclusive and
    return the standard 4-field breakdown. Pure read; no writes.

    ``n_nights_in_range`` is calendar days (always >= 1 when end >= start).
    ``n_nights_with_therapy`` is distinct date count in nightly_summary.
    ``n_nights_skipped`` is the difference (clamped at 0 to defend
    against the edge case where someone manually inserts a row outside
    the requested range — shouldn't happen, but defensive).
    """
    if end < start:
        return UsageBreakdown(
            n_nights_in_range=0,
            n_nights_with_therapy=0,
            n_nights_skipped=0,
            usage_rate_pct=0.0,
        )

    days_in_range = (end - start).days + 1
    with db.serialized() as conn:
        n_with_data = conn.execute(
            """
            SELECT COUNT(DISTINCT date)
              FROM nightly_summary
             WHERE date BETWEEN ? AND ?
            """,
            (start, end),
        ).fetchone()[0]

    n_with_data = int(n_with_data or 0)
    skipped = max(0, days_in_range - n_with_data)
    usage_pct = (
        round(n_with_data / days_in_range * 100, 1)
        if days_in_range > 0 else 0.0
    )
    return UsageBreakdown(
        n_nights_in_range=days_in_range,
        n_nights_with_therapy=n_with_data,
        n_nights_skipped=skipped,
        usage_rate_pct=usage_pct,
    )
