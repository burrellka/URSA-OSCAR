"""Manual-log aggregation — Phase 3 Item 5D.

Group manual_logs by type within a date window and produce a structured
summary the URSA agent + Trends UI can read. Per-type roll-ups:

  medication        per-name count + mean dose
  symptom           per-name count + mean severity
  alertness         scalar mean / median + count, plus morning/midday/evening
                    bucket means when the data lands across the day
  sleep_environment count + mean temperature_c + per-noise/light bucket counts
  freeform          count + sample of titles

Returns the structured dict for ``/api/v1/analytics/manual-log-summary``.
"""
from __future__ import annotations

import json
from collections import Counter
from datetime import date as date_t
from typing import Any

import pandas as pd

from ..storage.db import DuckDBManager


def summarize_manual_logs(
    db: DuckDBManager,
    start: date_t,
    end: date_t,
    log_type: str | None = None,
) -> dict[str, Any]:
    """Aggregate manual_logs in [start, end]. If log_type is given, only
    that type is rolled up; otherwise all five."""
    where = ["date >= ?", "date <= ?"]
    params: list[Any] = [start, end]
    if log_type:
        where.append("log_type = ?")
        params.append(log_type)

    rows = db.execute(
        "SELECT id, date, log_type, timestamp, value_text, value_numeric, "
        "unit, category, notes "
        f"FROM manual_logs WHERE {' AND '.join(where)} "
        "ORDER BY date ASC, timestamp ASC",
        params,
    ).fetchall()

    columns = ["id", "date", "log_type", "timestamp", "value_text",
               "value_numeric", "unit", "category", "notes"]
    df = pd.DataFrame(rows, columns=columns)

    by_type: dict[str, Any] = {}
    types_to_summarize = ([log_type] if log_type else
                          ["medication", "symptom", "alertness",
                           "sleep_environment", "freeform"])

    for t in types_to_summarize:
        sub = df[df["log_type"] == t] if not df.empty else df.iloc[0:0]
        by_type[t] = _summarize_for_type(t, sub)

    return {
        "date_range": {"start": start.isoformat(), "end": end.isoformat()},
        "total_entries": int(len(df)),
        "by_type": by_type,
    }


def _summarize_for_type(log_type: str, sub: pd.DataFrame) -> dict[str, Any]:
    n = int(len(sub))
    if n == 0:
        return {"count": 0}

    if log_type == "medication":
        names = sub["value_text"].astype(str)
        by_name = Counter(names.dropna())
        avg_dose: dict[str, float] = {}
        for name in by_name:
            sub_name = sub[sub["value_text"] == name]
            doses = pd.to_numeric(sub_name["value_numeric"], errors="coerce").dropna()
            if len(doses) > 0:
                avg_dose[name] = float(doses.mean())
        return {
            "count": n,
            "by_name": dict(by_name),
            "avg_dose_per_med": avg_dose,
        }

    if log_type == "symptom":
        names = sub["value_text"].astype(str)
        by_name = Counter(names.dropna())
        avg_sev: dict[str, float] = {}
        for name in by_name:
            sub_name = sub[sub["value_text"] == name]
            sevs = pd.to_numeric(sub_name["value_numeric"], errors="coerce").dropna()
            if len(sevs) > 0:
                avg_sev[name] = float(sevs.mean())
        return {
            "count": n,
            "by_name": dict(by_name),
            "avg_severity_per_symptom": avg_sev,
        }

    if log_type == "alertness":
        scores = pd.to_numeric(sub["value_numeric"], errors="coerce").dropna()
        out: dict[str, Any] = {"count": n}
        if len(scores) > 0:
            out["mean_score"] = float(scores.mean())
            out["median_score"] = float(scores.median())
            # Time-of-day buckets
            hours = pd.to_datetime(sub["timestamp"]).dt.hour
            morning = scores[hours < 12]
            evening = scores[hours >= 18]
            midday  = scores[(hours >= 12) & (hours < 18)]
            for label, bucket in [("morning", morning), ("midday", midday), ("evening", evening)]:
                if len(bucket) > 0:
                    out[f"mean_score_{label}"] = float(bucket.mean())
                    out[f"count_{label}"] = int(len(bucket))
        return out

    if log_type == "sleep_environment":
        temps: list[float] = []
        noise = Counter()
        light = Counter()
        for raw in sub["value_text"].dropna():
            try:
                obj = json.loads(raw)
            except Exception:
                continue
            if "temperature_c" in obj and isinstance(obj["temperature_c"], (int, float)):
                temps.append(float(obj["temperature_c"]))
            if obj.get("noise_level"):
                noise[obj["noise_level"]] += 1
            if obj.get("light_level"):
                light[obj["light_level"]] += 1
        out = {"count": n}
        if temps:
            out["avg_temperature_c"] = float(sum(temps) / len(temps))
            out["min_temperature_c"] = float(min(temps))
            out["max_temperature_c"] = float(max(temps))
        if noise:
            out["noise_level_counts"] = dict(noise)
        if light:
            out["light_level_counts"] = dict(light)
        return out

    # freeform
    titles = sub["category"].dropna().astype(str).tolist()
    return {
        "count": n,
        "sample_titles": titles[:5],
    }
