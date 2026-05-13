# ADR-004 — Serialize all DuckDB access through a process-wide RLock

**Status:** Accepted (deployed in image `0.3.1`)
**Date:** 2026-05-13
**Decider:** Claude Code (URSA-OSCAR build agent), validated by Kevin's production repro
**Supersedes:** nothing
**Amends:** Design v1.1 Decision 2 (clarifies the in-process concurrency model that Decision 2 left implicit)

---

## Context

Phase 2 of URSA-OSCAR ships a real React frontend whose Daily View fires three concurrent requests on every navigation:

1. `GET /api/v1/night/{date}` — ~800 byte JSON, single row
2. `GET /api/v1/events?date={date}` — ~17 KB JSON, ~80 rows
3. `GET /api/v1/timeseries/{date}?series=...` — **~1.7 MB JSON, 7 channels × 12k samples**

In production (image `0.3.0`), the first cold load rendered correctly. But Kevin reported a deterministic failure: tab-switch away from Daily View, then tab back — `GET /api/v1/night/{date}` returns `404` on a row that exists, and the next request gets `502 Bad Gateway` from nginx. Date-arrow navigation also showed visible lag before failing.

Statistics / Events / Trends never hit the bug. Each issues one small query at a time. Daily View was the only screen with three in-flight queries during the same TCP-multiplexed render.

---

## Root cause

`backend/src/ursa_oscar/storage/db.py` held a single `duckdb.DuckDBPyConnection` for the API process's entire lifetime, lazily opened on first use. Every API endpoint reached it through `db.execute(sql, params).fetchone()` / `.fetchall()`.

Two facts collide:

1. **DuckDB's Python wrapper is not thread-safe.** The connection holds an active-result cursor as internal state. A second `conn.execute()` call from another thread overwrites it.
2. **FastAPI runs `def` (sync) handlers on AnyIO's thread pool.** Concurrent requests dispatch concurrent threads against the same connection.

Symptom-wise:

```
thread A: cursor = conn.execute("SELECT ... WHERE date = ?", (target,))
thread B (interleaves): cursor' = conn.execute(SOME_OTHER_SQL)  # overwrites A's cursor
thread A: cursor.fetchone() -> returns None or partial row from B's query
endpoint: raise HTTPException(404)
```

The 7-channel timeseries call (~1.7 MB) keeps the connection busy long enough that any sibling fetch reliably collides. Chrome's bfcache-restore on tab-return re-fires the Daily View's three useEffect mounts simultaneously, deterministically reproducing the race.

The `502` is the same bug at a worse phase — DuckDB raises into uvicorn, the worker enters a bad state, nginx times out the upstream.

The `404` is the more dangerous symptom because the route returned a plausible HTTP code on data that absolutely exists.

---

## Decision

**Wrap all DuckDB access from the API process in a single `threading.RLock` owned by `DuckDBManager`.** Throughput drops to "one DB query at a time"; correctness is restored.

Implementation pattern:

```python
class _MaterializedResult:
    """List-backed stand-in for DuckDBPyConnection's result. Drained inside
    the lock, returned to the caller for safe fetch outside the lock."""
    def __init__(self, rows): self._rows = rows; self._idx = 0
    def fetchone(self): ...    # pops from list
    def fetchall(self): ...    # returns remaining list


class DuckDBManager:
    def __init__(...):
        self._conn = None
        self._lock = threading.RLock()    # RLock so a single thread can nest

    def execute(self, sql, params=None) -> _MaterializedResult:
        with self._lock:
            cursor = self.connect().execute(sql, params) if params else self.connect().execute(sql)
            try:
                rows = cursor.fetchall()
            except duckdb.InvalidInputException:
                rows = []
            return _MaterializedResult(rows)

    @contextmanager
    def serialized(self):
        """Hold the lock across a multi-statement block: transactions,
        nextval+INSERT loops, schema migrations."""
        with self._lock:
            yield self.connect()
```

All repository functions that previously called `db.connect()` directly (`nights.upsert`, `events.bulk_insert`, `manual_logs.insert`, `timeseries.bulk_insert`, `migrations.apply_migrations`, `migrations.current_version`) now use `with db.serialized() as conn:` instead. Read-paths that already went through `db.execute()` need zero changes — the new materialized result has the same `.fetchone()` / `.fetchall()` API.

---

## Why an `RLock` and not per-thread cursors or a connection pool

Three alternatives were considered:

1. **`conn.cursor()` per thread.** DuckDB does expose a `.cursor()` that "creates a new lightweight connection sharing the same database." Promising in theory; in practice the cursor object's lifecycle is subtle (close ordering matters; the manager has to track them; concurrent transactions across cursors interact in non-obvious ways). The single-user homelab workload doesn't pay back the implementation complexity.

2. **A `duckdb.connect()` pool.** Multiple connections to the same file from the same process is well-tested but introduces transaction-isolation questions that Phase 1's single-writer assumption sidesteps. We'd need to declare which connection writes and which read, manage pool eviction, etc.

3. **Switch sync handlers to async + `run_in_threadpool` with a global lock.** Same correctness as the chosen fix but more code change for no behavioral win.

`RLock` is the smallest surgical change that produces a correctness guarantee. The cost is throughput: one DB query at a time per process. For URSA-OSCAR's traffic profile (Kevin, one human, plus the URSA agent's bursts of ≤8 sequential tool calls) this is invisible.

---

## Trade-offs

**What we gave up.** True parallel DB query throughput. If Phase 3+ ever needs to support multiple simultaneous human users or a background importer that runs *during* live UI traffic, we'll have to revisit. The likely target then is option 2 (small connection pool with one writer + N readers), and `db.serialized()` is the natural seam to make that change behind.

**What we gained.**
- 8–15 concurrent 1.7 MB timeseries requests now all return `200 OK` in `0.4–1.4s` each (queued). The previous behavior was 1 OK then worker hang → cascading 502s.
- Post-burst `/healthz` responds in ~6 ms — worker recovers cleanly.
- Tab-switch-and-return on Daily View renders identically to first load. No lag, no 404s, no 502s.

**Verification.**
- Stress: 10 concurrent timeseries + 5 concurrent night-summary requests against a freshly imported 5-night DB, all return 200.
- UI repro: Chrome navigates Daily → Statistics → Daily (the exact failure scenario), 9/9 API calls return 200.
- Write paths still work end-to-end: fresh import of 5 nights with all 7 PLD series in 8.8s; events table populated (78 / 63 / etc. rows per night); CSV export streams the correct column set.

---

## Surface area touched

```
backend/src/ursa_oscar/storage/db.py                              +106 / -40   (RLock, _MaterializedResult, serialized())
backend/src/ursa_oscar/storage/migrations.py                       +19 / -16
backend/src/ursa_oscar/storage/repositories/events.py              +20 / -17
backend/src/ursa_oscar/storage/repositories/manual_logs.py         +14 / -14
backend/src/ursa_oscar/storage/repositories/nights.py              +13 / -11
backend/src/ursa_oscar/storage/repositories/timeseries.py          +15 / -7
```

The `DuckDBManager.execute()` signature change (returning `_MaterializedResult` instead of a raw cursor) is wire-compatible for every existing call site because the wrapper exposes the same `.fetchone()` / `.fetchall()` shape.

---

## Open follow-ups for Phase 3

1. **Async handler migration.** Several read paths could be `async def` and call `await run_in_threadpool(db.execute, ...)`. Doesn't change behavior under the lock but lines up with FastAPI idioms.
2. **Sequence/table self-consistency check at startup.** During a failed-import retry we observed `nightly_events_id_seq` collide with surviving rows from a half-rolled-back partial commit. Adding a startup check (or moving auto-id allocation inside the same transaction as the insert) would be defensive — independent of this ADR but surfaced during the repro work.
3. **`importer.py` `try/raise/finally: return` pattern silently swallows exceptions** — the constraint violation that led to (2) came back to the caller via `ImportLogEntry.error_message` but never bubbled to logs. Worth fixing during Phase 3 ingestion hardening.
