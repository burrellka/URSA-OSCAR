# Watcher not auto-importing

The watcher daemon should trigger imports automatically when new files appear in the bind-mounted CPAP source. When it doesn't, this page is the diagnostic flow.

## Symptom: the watcher container is up but never triggers

`docker ps` shows `ursa-oscar-watcher` running. Files are in the bind-mount. Nothing happens.

### Step 1 — confirm the watcher is actually looking at the right path

```bash
docker exec ursa-oscar-watcher ls /cpap-import/
```

If this is empty or doesn't contain `DATALOG/`, your bind-mount is wrong. The host path in your compose file should be the directory that contains `DATALOG/` — not `DATALOG/` itself, not a parent containing multiple unrelated directories.

### Step 2 — confirm fingerprint scanning is happening

```bash
docker logs ursa-oscar-watcher 2>&1 | tail -20
```

You should see periodic lines like:

```
watcher: tick: fingerprint changed (4 entries); quiescence timer reset
watcher: tick: fingerprint stable, waiting 30s for quiescence
```

If you see no fingerprint-related lines, the watcher isn't running its tick loop. Check for crash exceptions further up in the log.

If you see fingerprint lines but they always say `fingerprint empty`, the watcher is looking at a path it CAN see but that has no files — usually means the bind-mount points at a parent of `DATALOG/` rather than at the SD card root.

### Step 3 — confirm quiescence is being reached

The watcher waits for the file tree to stop changing for `URSA_OSCAR_QUIESCENCE_SECONDS` (default 30) before triggering. If you're actively copying files in (rsync running, network mount syncing), every new file resets the timer.

Two options:

- Wait until the copy finishes. Watcher will fire ~30s after the last file lands.
- Shorten quiescence in compose env: `URSA_OSCAR_QUIESCENCE_SECONDS: "10"`. Useful for development; in production the longer window protects against firing in the middle of a copy.

### Step 4 — confirm the watcher's auth is configured

```bash
docker exec ursa-oscar-watcher ls -la /data/service_tokens/watcher.jwt
```

The file should exist with size > 0. If it doesn't:

- Restart the api container: `docker compose restart ursa-oscar-api`. The api mints service tokens on startup if they're missing.
- After restart, re-check the file exists.

If the file exists but the watcher still gets 401s on `/imports`, the JWT signing secret on the api might have changed since the file was minted. Force a re-mint:

```bash
rm /opt/ursa-oscar/data/service_tokens/watcher.jwt
docker compose restart ursa-oscar-api
```

### Step 5 — confirm api connectivity

```bash
docker exec ursa-oscar-watcher curl -s -o /dev/null -w "%{http_code}\n" http://ursa-oscar-api:8000/healthz
```

Should print `200`. If it prints something else (timeout, connection refused), the watcher can't reach the api container. Most common cause: the `kairos-net` network isn't shared between the two containers. Check the compose file's `networks:` block for both services.

## Symptom: watcher triggers but the import fails

Watcher log shows `POST /imports` being called, but the response is 401 or 500.

### 401 — auth issue

See **Import not finding files → Symptom 4** for the service-token recovery flow.

### 500 — api-side import failure

Look at the api container's log for the actual exception:

```bash
docker logs ursa-oscar-api 2>&1 | grep -A 20 "ImportWorker.*error"
```

Common causes:

- Source directory unreadable from inside the api container (permissions)
- Bind-mount path differs between watcher and api (watcher sees `/cpap-import`, api also sees `/cpap-import`, but if you mis-configured them, the path the watcher passes to api might not be reachable from api)
- A single EDF file with a corrupted header causes the parser to throw; current behavior is to fail the whole night

If a corrupted file is blocking the import, move it out of the DATALOG dir temporarily and re-trigger. URSA-OSCAR can usually handle a partial night, but a malformed header in PLD/BRP/EVE is sometimes fatal.

## Symptom: webhook doesn't fire

Watcher imports successfully, but your downstream webhook (Home Assistant, ntfy, Slack) doesn't receive the notification.

### Step 1 — confirm the env var is set

```bash
docker exec ursa-oscar-watcher env | grep WEBHOOK
```

Should print `URSA_OSCAR_IMPORT_WEBHOOK_URL=https://...`. If empty, the webhook is disabled.

### Step 2 — confirm reachability

```bash
docker exec ursa-oscar-watcher curl -X POST -H "Content-Type: application/json" -d '{"test": true}' "$URSA_OSCAR_IMPORT_WEBHOOK_URL"
```

If this returns an error, your webhook endpoint isn't reachable from inside the watcher container. Check DNS, check firewall rules, check that the webhook service is actually accepting requests.

### Step 3 — check the watcher log for the webhook attempt

```bash
docker logs ursa-oscar-watcher 2>&1 | grep -i webhook
```

You should see `webhook delivered: status=200` (or similar). If you see `webhook POST to ... failed`, the URL is wrong or the destination rejected the request. The Python exception is in the log lines above.

The watcher deliberately catches webhook errors and doesn't retry — a bad webhook URL shouldn't break the daemon. Fix the URL or the destination, then the next import's webhook will succeed.

## Symptom: a stuck job blocks subsequent imports

A previous import job is stuck in `running` status forever. New file detection happens but no new import fires.

### Cause

The watcher tracks one in-flight job at a time (the `_tracked_job_id` pattern). Until that job reaches a terminal status (completed / failed / orphaned), it doesn't enqueue a new one. If the worker crashed without updating the job's status, the tracker stays armed indefinitely.

### Fix

The watcher has a built-in timeout (`URSA_OSCAR_JOB_WAIT_TIMEOUT`, default 600 seconds). After that, it releases the tracker and accepts new triggers. So:

- If you can wait 10 minutes, the auto-release kicks in
- If you want immediate recovery: restart the watcher container (`docker compose restart ursa-oscar-watcher`). The tracker is in-memory; restart clears it. The orphaned job's status in DuckDB stays "running" but doesn't block further imports.

## When to check the auto-managed service tokens

The watcher (and MCP) service tokens auto-rotate at api startup when within 7 days of expiration. If your stack hasn't restarted in 90+ days, the tokens have expired. The api will re-mint on next restart; trigger one with `docker compose restart ursa-oscar-api`.

For long-running deployments, this is the only routine maintenance — restart the api container periodically (or after expirations) to ensure service tokens stay fresh.
