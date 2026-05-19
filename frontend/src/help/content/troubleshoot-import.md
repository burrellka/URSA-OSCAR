# Import not finding files

When an import doesn't pick up the data you expected. Most common causes, in rough order of frequency.

## Symptom 1 — "No DATALOG directory found"

You triggered an import and the response (or the watcher log) reads: `No DATALOG directory found in source path.`

**Cause:** URSA-OSCAR expects the source to be the *root* of the SD card (or a tree rooted at the SD-card level). Inside that root must be a `DATALOG/` subdirectory containing `YYYYMMDD/` per-night dirs.

**Fix:**

- If you uploaded via folder picker: re-pick at the level *containing* `DATALOG/`, not at `DATALOG/` itself or at a single night's subdirectory.
- If you used path-based import: check `docker exec ursa-oscar-api ls /path/to/source/DATALOG/`. If that returns an error, you've pointed at the wrong path.
- If watcher-triggered: check `URSA_OSCAR_WATCH_PATH` (default `/cpap-import`). The watcher walks the immediate bind-mounted directory; if your data is in `/cpap-import/some-sub-dir/DATALOG/`, the watcher won't find it. Move the DATALOG up one level.

## Symptom 2 — Watcher logs "tree quiescent" but no import fires

The watcher reports that it detected and waited for a quiescent tree, but no `POST /imports` follows.

**Cause:** the tree is reachable but empty. The watcher only triggers when fingerprint is **non-empty** (Phase 4 design choice — empty trees from just-mounted blank cards shouldn't fire imports).

**Fix:** confirm there are actually files in the source. `docker exec ursa-oscar-watcher ls -R /cpap-import/ | head -20` is the diagnostic.

## Symptom 3 — Import enqueues but the job stays in "queued" forever

`/api/v1/imports/jobs/{id}` returns `status: "queued"` indefinitely.

**Cause:** the import worker isn't running. This means either the api container crashed, the import worker task died inside the container, or the api process is hung on something else.

**Fix:**

- `docker ps` to confirm api is up
- `docker logs ursa-oscar-api 2>&1 | tail -50` to look for exceptions
- If the worker task died but the api is otherwise alive, restart: `docker compose restart ursa-oscar-api`
- The persisted job records survive restart; the new worker picks up where the old one left off

## Symptom 4 — Watcher 401s on `/imports`

The watcher logs an HTTPStatusError with `401 Unauthorized` on every attempt.

**Cause:** the watcher's service token isn't valid. Either it's not there, it's expired, or the JWT signing secret doesn't match.

**Fix:**

- Confirm the file: `docker exec ursa-oscar-watcher ls -la /data/service_tokens/watcher.jwt`
- If missing: restart the api container to re-mint
- If present but old: `rm /opt/ursa-oscar/data/service_tokens/watcher.jwt && docker compose restart ursa-oscar-api` to force a re-mint
- If you've manually set `URSA_OSCAR_WATCHER_TOKEN` in the watcher's compose env, confirm it's a valid JWT signed by the current `URSA_OSCAR_JWT_SECRET`

## Symptom 5 — Permission denied reading the bind-mount

The api or watcher container can't read the bind-mounted source.

**Cause:** the container's uid/gid doesn't match the host file permissions.

**Fix:**

- `chmod -R o+r /opt/ursa-oscar/cpap-import` to make it world-readable (simplest fix, fine for non-sensitive CPAP data)
- OR: set the `user:` field in the compose service to match the host owner's uid:gid
- OR: chown the source directory to the container's default uid (typically 1000)

## Symptom 6 — Some nights import, others don't

Import reports "imported 5 nights, skipped 2" and you expected 7.

**Cause:** the skipped nights either have no PLD.edf files (no actual therapy session — common pattern; see **Sessions vs nights**) or have files that fail the EDF header parse.

**Fix:**

- If those nights are dates you didn't use the CPAP: this is correct behavior, no fix needed
- If those nights should have data: check the dir contents — `docker exec ursa-oscar-api ls /cpap-import/DATALOG/YYYYMMDD/`. A complete session has 5 files: CSL, EVE, BRP, PLD, SA2. Missing PLD means no waveform data was recorded.
- The pressure_audit diagnostic tool (`docker exec ursa-oscar-api python -m ursa_oscar.diagnostics.pressure_audit --summary-only`) walks every night and reports which have PLD.edf files vs. which don't.

## Symptom 7 — Force re-import doesn't pick up changes

You triggered a re-import with `force=true` and expected updated data, but the existing nights look the same.

**Cause:** force re-import replaces existing rows with freshly-parsed values, but if your source SD card has the SAME data, the result is identical. Force re-import isn't a fix for incorrect parsing — it's a way to re-run the parser on the same source.

**Fix:**

- If you modified the source files (e.g., manually re-extracted from a CPAP backup), confirm the actual files changed
- If you're trying to fix a parsing bug, you need a code change + image rebuild, not a re-import

## Symptom 8 — Import succeeds but the night doesn't appear on the Overview

The import job's status is "completed" with `nights_imported: 1` but the Overview heatmap doesn't show the new night.

**Cause:** browser cache. The Overview calls `/api/v1/nights` to fetch the list, and that's cached in the React state — refresh the page.

**Fix:**

- Hard refresh (Ctrl+Shift+R / Cmd+Shift+R)
- Or navigate away (Daily View) and back (Overview)
- If the night still doesn't appear after refresh: `docker exec ursa-oscar-api python -c "from ursa_oscar.storage.db import DuckDBManager; db = DuckDBManager('/data/ursa-oscar.duckdb', read_only=True); print(db._read.execute('SELECT date FROM nightly_summary ORDER BY date DESC LIMIT 5').fetchall())"`. If the new night is in DuckDB, the issue is browser-side; if not, the import wrote events but skipped writing the summary row (rare, usually means a session_analyzer exception that the worker swallowed).

## When to ask for help

If none of the above fixes the issue:

- Capture `docker logs ursa-oscar-api 2>&1 | tail -100` and `docker logs ursa-oscar-watcher 2>&1 | tail -50`
- Run the pressure_audit diagnostic and capture its summary output
- File an issue on the repo with the logs and audit output. The diagnostic tool was built specifically so support questions about ingestion have a fast, structured answer.
