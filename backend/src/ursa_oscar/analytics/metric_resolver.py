"""Resolve metric-name strings into per-day pandas Series.

Phase 3 Item 5A-D analytics endpoints need a uniform way to pull
"give me a per-day series for metric X" regardless of whether X lives
in the nightly_summary table, the manual_logs table, or is computed.

Naming convention:

  Bare column name → a column of ``nightly_summary``. The authoritative
  list is ``_NIGHTLY_NUMERIC_COLUMNS`` below; call
  ``known_nightly_metrics()`` to read it. Do NOT re-type the list in a
  docstring, a tool schema, or a UI dropdown — 1.1.15 fixed a bug caused
  by exactly that (the AI's tool schema described ``metric`` as a bare
  string, the model guessed "ahi", and the resolver rejected it because
  the real name is "total_ahi"). Anything that needs to tell a human or a
  model what the valid names are must derive them from here.

  ``log_type:filter:field`` → a manual_logs aggregation. The string is
  parsed left-to-right:
      log_type — one of medication/symptom/alertness/sleep_environment/freeform
      filter   — narrows by name (medication name, symptom name) or
                 free-text. Empty filter = all rows of that log_type.
      field    — which numeric column to aggregate (dose/severity/score/
                 temperature_c). Defaults vary by log_type — see
                 ``_DEFAULT_FIELDS`` below.

  Examples:
      "medication:Melatonin:dose"          dose-per-day of Melatonin
      "medication:Melatonin"               same as above (default field=dose)
      "symptom:headache:severity"          headache severity-per-day
      "symptom:headache"                   same as above (default field=severity)
      "alertness::score"                   alertness score-per-day, all entries
      "alertness:morning:score"            alertness score, morning entries only
                                            (filter='morning' matches entries
                                            whose hour-of-timestamp <= 11)

Aggregation rule per (day × metric): mean of all matching rows.
Days with zero matching rows return NaN. Caller decides whether to
drop NaN or interpolate.
"""
from __future__ import annotations

import json
from datetime import date as date_t
from typing import Any

import pandas as pd

from ..storage.db import DuckDBManager


# nightly_summary columns we expose as metrics. Subset of the table — we
# don't expose ID-ish or string columns.
_NIGHTLY_NUMERIC_COLUMNS = frozenset({
    "session_count", "total_time_minutes",
    "total_ahi", "obstructive_ahi", "central_ahi", "hypopnea_index", "rera_index",
    "median_pressure", "p95_pressure", "p995_pressure",
    "median_epap", "p95_epap", "p995_epap",
    "median_leak", "p95_leak", "p995_leak",
    "minutes_in_apnea", "minutes_over_leak_redline",
    "cheyne_stokes_pct", "large_leak_pct",
    "min_pressure_setting", "max_pressure_setting",
    "epr_level", "ramp_time_minutes",
    "temperature_celsius",
})

# 1.1.15 — natural-language synonyms an LLM (or a human) is likely to
# reach for, mapped to the canonical column. Kept deliberately TINY and
# unambiguous: "ahi" means total AHI to every clinician and every model,
# so accepting it is correct API design, not sloppiness. Ambiguous words
# are NOT aliased on purpose — "leak" and "pressure" could each mean the
# median or the p95 variant, and silently picking one for the caller
# would be a data-integrity bug, not a convenience. The real fix for
# guessing is the derived vocabulary in the tool schema (see
# ai_proxy/tools.py); this map is the safety net for the one case where
# the guess is unambiguous.
_METRIC_ALIASES = {
    "ahi": "total_ahi",
}

# Default numeric field per log_type when the user omits :field.
_DEFAULT_FIELDS = {
    "medication": "dose",
    "symptom": "severity",
    "alertness": "score",
    "sleep_environment": "temperature_c",
    "freeform": None,  # freeform has no numeric column
}

# log_type → row-shape column carrying the numeric value, OR a marker
# 'json:key' meaning extract from value_text-as-JSON (sleep_environment).
_FIELD_TO_ROW_COLUMN = {
    ("medication", "dose"):              "value_numeric",
    ("symptom",    "severity"):          "value_numeric",
    ("alertness",  "score"):             "value_numeric",
    ("sleep_environment", "temperature_c"):       "json:temperature_c",
    ("sleep_environment", "bed_partner_present"): "json:bed_partner_present",
}


class UnknownMetricError(ValueError):
    """Raised when a metric name doesn't parse or doesn't have a known mapping."""


def parse_metric_name(name: str) -> tuple[str, str | None, str | None]:
    """Split ``"log_type:filter:field"`` or bare column name.

    Returns (source, filter, field) where source is either the
    nightly_summary column name or one of the log_type literals.

    Raises UnknownMetricError on malformed input.
    """
    if ":" not in name:
        # 1.1.15 — resolve the unambiguous synonym first ("ahi" -> "total_ahi").
        canonical = _METRIC_ALIASES.get(name.strip().lower(), name)
        if canonical not in _NIGHTLY_NUMERIC_COLUMNS:
            raise UnknownMetricError(
                f"Unknown nightly metric '{name}'. Valid: {sorted(_NIGHTLY_NUMERIC_COLUMNS)}"
            )
        return (canonical, None, None)

    parts = name.split(":", 2)
    log_type = parts[0]
    if log_type not in _DEFAULT_FIELDS:
        raise UnknownMetricError(
            f"Unknown log_type '{log_type}' in metric '{name}'. "
            f"Valid: {list(_DEFAULT_FIELDS)}"
        )
    filt = parts[1] if len(parts) >= 2 and parts[1] else None
    field = parts[2] if len(parts) == 3 and parts[2] else _DEFAULT_FIELDS[log_type]
    return (log_type, filt, field)


def resolve_metric(
    db: DuckDBManager,
    name: str,
    start: date_t,
    end: date_t,
) -> pd.Series:
    """Return a per-day pandas Series for the given metric, indexed by
    date in [start, end]. Days with no data are NaN.

    See module docstring for the naming convention.
    """
    source, filt, field = parse_metric_name(name)

    # All-days index so callers can join cleanly across metrics.
    days_index = pd.date_range(start=start, end=end, freq="D").date

    if source in _NIGHTLY_NUMERIC_COLUMNS:
        return _resolve_nightly_column(db, source, start, end, days_index)

    return _resolve_manual_log(db, source, filt, field, start, end, days_index)


def _resolve_nightly_column(
    db: DuckDBManager,
    column: str,
    start: date_t,
    end: date_t,
    days_index,
) -> pd.Series:
    rows = db.execute(
        f"SELECT date, {column} FROM nightly_summary "
        "WHERE date >= ? AND date <= ? ORDER BY date ASC",
        (start, end),
    ).fetchall()
    if not rows:
        return pd.Series(index=days_index, dtype="float64", name=column)
    df = pd.DataFrame(rows, columns=["date", column])
    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.set_index("date")[column]
    df = pd.to_numeric(df, errors="coerce")
    return df.reindex(days_index).rename(column)


def _resolve_manual_log(
    db: DuckDBManager,
    log_type: str,
    filt: str | None,
    field: str | None,
    start: date_t,
    end: date_t,
    days_index,
) -> pd.Series:
    metric_label = f"{log_type}:{filt or ''}:{field or ''}".rstrip(":")

    # Pull all rows of this log_type in range.
    where_clauses = ["log_type = ?", "date >= ?", "date <= ?"]
    params: list[Any] = [log_type, start, end]
    if filt:
        # Most filters are name-matches against value_text. The alertness
        # 'morning' / 'evening' filters are time-of-day buckets, handled
        # post-fetch in Python (DuckDB doesn't have HOUR() in all builds
        # of our pinned version and the data volume is small).
        if log_type == "alertness" and filt.lower() in {"morning", "evening", "midday"}:
            pass  # No SQL filter; apply Python-side below.
        else:
            where_clauses.append("value_text = ?")
            params.append(filt)
    sql = (
        "SELECT date, timestamp, value_text, value_numeric "
        f"FROM manual_logs WHERE {' AND '.join(where_clauses)} "
        "ORDER BY date ASC, timestamp ASC"
    )
    rows = db.execute(sql, params).fetchall()
    if not rows:
        return pd.Series(index=days_index, dtype="float64", name=metric_label)

    df = pd.DataFrame(rows, columns=["date", "timestamp", "value_text", "value_numeric"])
    df["date"] = pd.to_datetime(df["date"]).dt.date

    # Time-of-day filter for alertness.
    if log_type == "alertness" and filt:
        f = filt.lower()
        if f in {"morning", "evening", "midday"}:
            hours = pd.to_datetime(df["timestamp"]).dt.hour
            if f == "morning":
                df = df[hours < 12]
            elif f == "evening":
                df = df[hours >= 18]
            elif f == "midday":
                df = df[(hours >= 12) & (hours < 18)]

    if df.empty:
        return pd.Series(index=days_index, dtype="float64", name=metric_label)

    # Extract the numeric value.
    row_col = _FIELD_TO_ROW_COLUMN.get((log_type, field))
    if row_col is None:
        # freeform or an unsupported field — surface as NaN rather than raise.
        return pd.Series(index=days_index, dtype="float64", name=metric_label)
    if row_col.startswith("json:"):
        json_key = row_col.split(":", 1)[1]
        values = df["value_text"].apply(
            lambda raw: _json_get(raw, json_key)
        )
    else:
        values = df[row_col]

    df = df.assign(_value=pd.to_numeric(values, errors="coerce"))
    daily = df.groupby("date")["_value"].mean()
    return daily.reindex(days_index).rename(metric_label)


def _json_get(raw: Any, key: str) -> Any:
    if not isinstance(raw, str) or not raw:
        return None
    try:
        obj = json.loads(raw)
    except Exception:
        return None
    return obj.get(key) if isinstance(obj, dict) else None


def known_nightly_metrics() -> list[str]:
    """Return the sorted list of bare-name CPAP metrics for UI dropdowns.

    Also the source of truth the AI tool schemas derive their ``metric``
    parameter description from — see ``ai_proxy/tools.py``. Anything that
    enumerates valid metric names MUST call this rather than hand-copy the
    list, or it will drift (which is the 1.1.15 bug).
    """
    return sorted(_NIGHTLY_NUMERIC_COLUMNS)


def known_log_types() -> list[str]:
    """Return the manual-log types valid as the ``log_type`` segment of a
    composite ``log_type:filter:field`` metric. Same no-hand-copying rule
    as ``known_nightly_metrics``."""
    return list(_DEFAULT_FIELDS)


def known_metric_aliases() -> dict[str, str]:
    """Return the accepted synonym -> canonical-name map (e.g.
    ``{"ahi": "total_ahi"}``). Exposed so the tool-schema vocabulary can
    tell the model the canonical name for a word it would otherwise
    guess."""
    return dict(_METRIC_ALIASES)


def list_available_manual_metrics(db: DuckDBManager) -> list[str]:
    """Probe the DB for manual-log metric names that actually have data.

    Returns a list of "log_type:filter:field" strings for medications and
    symptoms the user has actually logged. Powers the Trends page metric
    dropdown so users see only the metrics with data behind them.
    """
    out: list[str] = []
    # Medications: distinct value_text for log_type=medication.
    rows = db.execute(
        "SELECT DISTINCT value_text FROM manual_logs "
        "WHERE log_type = 'medication' AND value_text IS NOT NULL "
        "ORDER BY value_text"
    ).fetchall()
    for (name,) in rows:
        out.append(f"medication:{name}:dose")
    # Symptoms.
    rows = db.execute(
        "SELECT DISTINCT value_text FROM manual_logs "
        "WHERE log_type = 'symptom' AND value_text IS NOT NULL "
        "ORDER BY value_text"
    ).fetchall()
    for (name,) in rows:
        out.append(f"symptom:{name}:severity")
    # Alertness — if any rows, expose the scalar + time-of-day buckets.
    n = db.execute(
        "SELECT COUNT(*) FROM manual_logs WHERE log_type = 'alertness'"
    ).fetchone()
    if n and n[0] > 0:
        out.append("alertness::score")
        out.append("alertness:morning:score")
        out.append("alertness:evening:score")
    # Sleep environment temperature.
    n = db.execute(
        "SELECT COUNT(*) FROM manual_logs WHERE log_type = 'sleep_environment'"
    ).fetchone()
    if n and n[0] > 0:
        out.append("sleep_environment::temperature_c")
    return out
