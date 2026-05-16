"""Analytical-output cache — Phase 6 Ticket 6.1.

Expensive analytical computations (multivariate correlation, lag
analysis, predictive models in 6.2, etc.) cache their results in the
``analytical_cache`` table. A cache hit returns the stored result
unchanged plus a ``cache_age_seconds`` annotation; cache misses run
the actual computation and persist.

Cache key (the "fingerprint") is a SHA-256 over three things:

  1. ``tool_name`` — distinguishes outputs from different analytical
     methods even when their other params happen to look similar.
  2. ``params`` — the user's query (target metric, predictors, date
     range, etc.). Serialized with sorted keys so order doesn't
     matter; same query from different callers hits the same entry.
  3. ``data_version_hash`` — captures the state of the underlying
     data at compute time. Derived from the latest ``last_updated``
     timestamps in ``nightly_summary`` and ``manual_logs`` for rows
     in the query date range. When ANY relevant row updates, the
     hash changes, the fingerprint changes, the cache misses, fresh
     computation runs.

Three invalidation paths complement the fingerprint discipline:

  - **By date range** (importer, manual_logs CRUD) — clears entries
    whose ``params_json`` indicates an overlapping date range. Fast
    safety net for the common case where one tool updates data
    another tool already cached.
  - **By schema bump** (apply_migrations) — clears every entry on a
    schema version transition. Cached results were computed against
    an older shape.
  - **User explicit recompute** — ``?recompute=true`` on the
    analytical endpoint forces a miss and overwrites the entry.

The cache is operator-visible via ``GET /api/v1/analytics/cache/stats``
and the Data Management page's "Analytical cache" section.
"""
from __future__ import annotations

import hashlib
import json
import logging
import time
from datetime import date as date_t
from typing import Any

from ..storage.db import DuckDBManager

logger = logging.getLogger(__name__)


class AnalyticalCache:
    """Read/write surface for the ``analytical_cache`` table.

    Constructed with a ``DuckDBManager`` (not a raw connection) so
    every operation acquires the manager's RLock; same RLock the rest
    of URSA-OSCAR uses for DuckDB writes. Safe under the API's
    FastAPI thread pool (one writer at a time).

    Not held as long-lived state — instantiate per-request with the
    app's DB manager. Cheap; just a couple of references.
    """

    def __init__(self, db: DuckDBManager) -> None:
        self._db = db

    # ------------------------------------------------------------------
    # Public surface — keyed-read, keyed-write, invalidation, stats.
    # ------------------------------------------------------------------

    def compute_fingerprint(
        self,
        tool_name: str,
        params: dict,
        data_version_hash: str,
    ) -> str:
        """Return the SHA-256 fingerprint for ``(tool_name, params, data_version_hash)``.

        ``params`` is serialized with ``sort_keys=True`` so call order
        and Python dict insertion order don't change the result. The
        same query from the AI assistant, the web UI, and an MCP tool
        all hit the same cache entry.
        """
        params_json = json.dumps(params, sort_keys=True, default=str)
        payload = f"{tool_name}|{params_json}|{data_version_hash}".encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def compute_data_version_hash(
        self,
        start_date: date_t,
        end_date: date_t,
    ) -> str:
        """Capture the state of the input data at this moment for a
        given date range. Hash is the SHA-256 of:

          - latest ``nightly_summary.last_updated`` in [start, end]
          - latest ``manual_logs.last_updated`` in [start, end]
          - count of rows in each table in the range (so deletions
            shift the hash, not just updates)

        A query computed against this hash is valid as long as nothing
        in either table for the range has changed.
        """
        with self._db.serialized() as conn:
            nights_row = conn.execute(
                """
                SELECT COALESCE(MAX(last_updated)::VARCHAR, ''),
                       COUNT(*)
                  FROM nightly_summary
                 WHERE date BETWEEN ? AND ?
                """,
                (start_date, end_date),
            ).fetchone()
            logs_row = conn.execute(
                """
                SELECT COALESCE(MAX(last_updated)::VARCHAR, ''),
                       COUNT(*)
                  FROM manual_logs
                 WHERE date BETWEEN ? AND ?
                """,
                (start_date, end_date),
            ).fetchone()

        nights_max, nights_count = nights_row or ("", 0)
        logs_max, logs_count = logs_row or ("", 0)
        payload = (
            f"nights:{nights_max}:{nights_count}|"
            f"logs:{logs_max}:{logs_count}"
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    def get(self, fingerprint: str) -> dict[str, Any] | None:
        """Look up a cached entry. On hit:

          - Bumps ``cache_hits`` and ``last_accessed_at``
          - Returns the deserialized result dict with a
            ``cache_age_seconds`` field merged in
          - Returns ``None`` on miss
        """
        with self._db.serialized() as conn:
            row = conn.execute(
                """
                SELECT result_json,
                       computed_at,
                       EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - computed_at))
                         AS age_seconds
                  FROM analytical_cache
                 WHERE fingerprint = ?
                """,
                (fingerprint,),
            ).fetchone()
            if row is None:
                return None
            result_json, computed_at, age_seconds = row
            # Bump hit counter + last_accessed_at.
            conn.execute(
                """
                UPDATE analytical_cache
                   SET cache_hits = cache_hits + 1,
                       last_accessed_at = CURRENT_TIMESTAMP
                 WHERE fingerprint = ?
                """,
                (fingerprint,),
            )

        result = json.loads(result_json)
        # Wire-shape contract: the cached envelope always wraps its
        # payload in {ok, data}; add cache_age_seconds + computed_at
        # to the data block so the UI/AI can show "computed N hours
        # ago" labels.
        if isinstance(result, dict) and "data" in result:
            result["data"] = dict(result["data"])
            result["data"]["cache_age_seconds"] = int(age_seconds or 0)
            result["data"]["computed_at"] = (
                computed_at.isoformat() if hasattr(computed_at, "isoformat")
                else str(computed_at)
            )
        return result

    def set(
        self,
        fingerprint: str,
        tool_name: str,
        params: dict,
        result: dict,
        data_version_hash: str,
        compute_duration_ms: float,
    ) -> None:
        """Persist a freshly-computed result. Upsert keyed on
        fingerprint — re-computing the same query overwrites the
        prior entry rather than failing."""
        params_json = json.dumps(params, sort_keys=True, default=str)
        result_json = json.dumps(result, default=str)
        with self._db.serialized() as conn:
            conn.execute(
                "DELETE FROM analytical_cache WHERE fingerprint = ?",
                (fingerprint,),
            )
            conn.execute(
                """
                INSERT INTO analytical_cache (
                    fingerprint, tool_name, params_json, result_json,
                    data_version_hash, compute_duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    fingerprint, tool_name, params_json, result_json,
                    data_version_hash, float(compute_duration_ms),
                ),
            )
        logger.debug(
            "analytical_cache.set: %s fingerprint=%s compute_ms=%.1f",
            tool_name, fingerprint[:12], compute_duration_ms,
        )

    def invalidate_by_date_range(
        self,
        start_date: date_t,
        end_date: date_t,
    ) -> int:
        """Clear cached entries whose params indicate an overlapping
        date range. Returns the count invalidated.

        We can't perfectly target the right entries (different tools
        encode date ranges differently in their params), so the rule
        is conservative: any entry whose params_json contains either
        ``start_date`` or ``end_date`` (or any date in the range)
        gets cleared. The next call will recompute against the new
        data and re-cache.

        Implemented as: parse params_json per entry, extract any
        plausible date-range fields, intersect with the invalidation
        range. Performance note: cache table is small (curated
        analytical outputs, not bulk records), so per-row JSON parse
        is fine even at high CRUD volume. If this becomes a hotspot
        in Phase 7+, switch to extracting date columns at insert time.
        """
        with self._db.serialized() as conn:
            entries = conn.execute(
                "SELECT fingerprint, params_json FROM analytical_cache"
            ).fetchall()

        to_clear: list[str] = []
        for fingerprint, params_json in entries:
            try:
                params = json.loads(params_json)
            except (TypeError, json.JSONDecodeError):
                # Corrupted entry — drop it defensively.
                to_clear.append(fingerprint)
                continue
            if _params_overlap_range(params, start_date, end_date):
                to_clear.append(fingerprint)

        if not to_clear:
            return 0

        with self._db.serialized() as conn:
            placeholders = ",".join(["?"] * len(to_clear))
            conn.execute(
                f"DELETE FROM analytical_cache WHERE fingerprint IN ({placeholders})",
                to_clear,
            )
        logger.info(
            "analytical_cache.invalidate_by_date_range: cleared %d entries "
            "for [%s, %s]",
            len(to_clear), start_date, end_date,
        )
        return len(to_clear)

    def clear_all(self) -> int:
        """Drop every entry. Called by ``apply_migrations`` after any
        v7+ schema transition. Returns the count cleared."""
        with self._db.serialized() as conn:
            row = conn.execute("SELECT COUNT(*) FROM analytical_cache").fetchone()
            n = int(row[0]) if row else 0
            conn.execute("DELETE FROM analytical_cache")
        return n

    def stats(self) -> dict[str, Any]:
        """Aggregate counts for the cache-stats endpoint + UI.

        Returns:
          total_entries, total_hits, cache_hit_rate,
          oldest_entry_age_seconds, largest_entry_bytes,
          by_tool: { tool_name: {entries, hits, avg_compute_ms} }
        """
        with self._db.serialized() as conn:
            top = conn.execute(
                """
                SELECT COUNT(*),
                       COALESCE(SUM(cache_hits), 0),
                       MAX(EXTRACT(EPOCH FROM (CURRENT_TIMESTAMP - computed_at))),
                       MAX(LENGTH(result_json))
                  FROM analytical_cache
                """
            ).fetchone()
            by_tool_rows = conn.execute(
                """
                SELECT tool_name,
                       COUNT(*) AS entries,
                       COALESCE(SUM(cache_hits), 0) AS hits,
                       COALESCE(AVG(compute_duration_ms), 0.0) AS avg_compute_ms
                  FROM analytical_cache
                 GROUP BY tool_name
                 ORDER BY tool_name
                """
            ).fetchall()

        total_entries = int(top[0] or 0)
        total_hits = int(top[1] or 0)
        # The hit-rate is hits / (hits + entries — i.e., one "miss" per
        # entry-creation). Approximation that's stable across cache
        # churn and gives operators something sensible to read.
        denom = total_hits + total_entries
        hit_rate = (total_hits / denom) if denom > 0 else 0.0
        oldest = int(top[2] or 0)
        largest = int(top[3] or 0)

        by_tool = {
            tool: {
                "entries": int(entries),
                "hits": int(hits),
                "avg_compute_ms": float(avg_ms),
            }
            for tool, entries, hits, avg_ms in by_tool_rows
        }

        return {
            "total_entries": total_entries,
            "total_hits": total_hits,
            "cache_hit_rate": round(hit_rate, 4),
            "oldest_entry_age_seconds": oldest,
            "largest_entry_bytes": largest,
            "by_tool": by_tool,
        }


# ----------------------------------------------------------------------
# Helpers.
# ----------------------------------------------------------------------


def _params_overlap_range(
    params: dict, start_date: date_t, end_date: date_t,
) -> bool:
    """Heuristic: does ``params`` indicate a date range that overlaps
    ``[start_date, end_date]``?

    Looks for ``start_date`` and ``end_date`` fields directly. Falls
    back to ``True`` (conservative — invalidate) when no recognizable
    range is present, since unrecognized analytical tools shouldn't
    keep stale entries through a data update.
    """
    p_start = _coerce_date(params.get("start_date"))
    p_end = _coerce_date(params.get("end_date"))
    if p_start is None or p_end is None:
        # No usable range info → conservative.
        return True
    # Two ranges overlap iff start_a <= end_b and start_b <= end_a.
    return p_start <= end_date and start_date <= p_end


def _coerce_date(value) -> date_t | None:
    if value is None:
        return None
    if isinstance(value, date_t):
        return value
    if isinstance(value, str):
        try:
            return date_t.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


# ----------------------------------------------------------------------
# Decorator helper — wraps a compute function with cache lookup.
# ----------------------------------------------------------------------


def cached_compute(
    cache: AnalyticalCache,
    *,
    tool_name: str,
    params: dict,
    start_date: date_t,
    end_date: date_t,
    recompute: bool = False,
):
    """Context-manager-like helper for analytical endpoints. Used as:

        with cached_compute(cache, tool_name="...", params={...},
                            start_date=s, end_date=e) as ctx:
            if ctx.hit:
                return ctx.cached_result
            # ... do expensive work, build `result_envelope` ...
            ctx.store(result_envelope)
            return result_envelope

    Centralizes the fingerprint + data_version_hash + compute-timer
    boilerplate so endpoint code stays focused on the actual math.
    """
    return _CachedComputeContext(
        cache, tool_name=tool_name, params=params,
        start_date=start_date, end_date=end_date, recompute=recompute,
    )


class _CachedComputeContext:
    def __init__(
        self,
        cache: AnalyticalCache,
        *,
        tool_name: str,
        params: dict,
        start_date: date_t,
        end_date: date_t,
        recompute: bool,
    ) -> None:
        self._cache = cache
        self._tool_name = tool_name
        self._params = params
        self._start_date = start_date
        self._end_date = end_date
        self._recompute = recompute
        self._t0: float | None = None
        self._data_version_hash: str | None = None
        self._fingerprint: str | None = None
        self.hit: bool = False
        self.cached_result: dict | None = None

    def __enter__(self):
        self._data_version_hash = self._cache.compute_data_version_hash(
            self._start_date, self._end_date,
        )
        self._fingerprint = self._cache.compute_fingerprint(
            self._tool_name, self._params, self._data_version_hash,
        )
        if not self._recompute:
            cached = self._cache.get(self._fingerprint)
            if cached is not None:
                self.hit = True
                self.cached_result = cached
                return self
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def store(self, result_envelope: dict) -> None:
        """Persist the freshly-computed envelope. Caller passes the
        full ``{ok, data}`` shape; we merge in metadata and store."""
        if self._t0 is None or self._fingerprint is None:
            return
        elapsed_ms = (time.perf_counter() - self._t0) * 1000.0
        # Stamp the envelope's data block with cache metadata BEFORE
        # storage so subsequent hits return the same shape.
        if isinstance(result_envelope, dict) and "data" in result_envelope:
            result_envelope["data"] = dict(result_envelope["data"])
            result_envelope["data"].setdefault("cache_age_seconds", 0)
        self._cache.set(
            fingerprint=self._fingerprint,
            tool_name=self._tool_name,
            params=self._params,
            result=result_envelope,
            data_version_hash=self._data_version_hash or "",
            compute_duration_ms=elapsed_ms,
        )
