# Importing your first SD card

URSA-OSCAR ingests the EDF and JSON files your CPAP writes to its SD card. Three import paths cover three different operator workflows. Pick whichever fits your setup.

## What URSA-OSCAR expects to see

The SD card or directory you import should contain a `DATALOG/YYYYMMDD/` tree — one directory per night, with five files per session inside:

- `*_CSL.edf` — annotation channel
- `*_EVE.edf` — event annotations (apneas, hypopneas)
- `*_BRP.edf` — high-resolution flow + pressure (25 Hz)
- `*_PLD.edf` — lower-resolution physiologic data (Press.2s, Leak.2s, etc.)
- `*_SA2.edf` — secondary annotations

Plus a `SETTINGS/` directory at the SD root with the device configuration JSON files.

If your card has additional files (firmware backups, manufacturer logs), URSA-OSCAR ignores them.

## Path 1 — folder upload from a laptop

Best when: you grab the SD card occasionally, plug it into a laptop, and want to push the data without setting up file shares.

1. Plug the SD card into your laptop's reader.
2. Open URSA-OSCAR's web UI → **Import** page.
3. Click "Select folder." Your browser will open a folder picker.
4. Navigate to the root of the SD card (the directory containing `DATALOG/` and `SETTINGS/`).
5. Pick that directory. Your browser will warn that the site wants to upload the whole tree; confirm.
6. URSA-OSCAR uploads the files, creates a temp directory in the api container, and queues an import job.

The import is asynchronous. The page shows progress; you can navigate away and come back. New nights land in the database within seconds to a minute or two depending on how many you're importing.

## Path 2 — drop into the bind-mount

Best when: you have a fixed SD-card-reader setup, or you copy data from the card to a network share that URSA-OSCAR auto-watches.

1. Identify the host path URSA-OSCAR's watcher monitors. In the production compose this is `URSA_OSCAR_CPAP_IMPORT_PATH` — typically `/srv/ursa-oscar/cpap-import` or `/mnt/nvme-apps/apps/ursa-oscar/cpap-import` depending on your host layout.
2. Copy or rsync the `DATALOG/` tree from your SD card into that directory. The watcher detects the new files within ~30 seconds (the default poll interval) and waits another 30 seconds for the tree to be quiescent (no new files appearing) before triggering an import.
3. Watch `docker logs ursa-oscar-watcher` for the trigger:
   ```
   watcher: tree quiescent — POST /imports source_path=/cpap-import force=False
   ```

This is the most operationally clean path. Set up an rsync cron job from the SD card or a mounted CPAP source, and URSA-OSCAR ingests new nights automatically. Pair with the optional webhook (`URSA_OSCAR_IMPORT_WEBHOOK_URL`) to get notified when imports complete.

## Path 3 — path-based import via the UI

Best when: the data is already on the docker host but not in the watcher's bind-mount, and you want to trigger an import without copying it.

1. SSH to the docker host. Verify the path is readable from inside the api container (typically `docker exec ursa-oscar-api ls /cpap-import/...`).
2. Web UI → **Import** → "Import from a path" tab.
3. Type the path (as the api container sees it, not as the host sees it).
4. Click "Import." Same async queue as the other paths.

## Force re-import

By default, URSA-OSCAR skips nights it has already imported. If you re-upload an SD card with the same data, those nights aren't re-parsed.

If you've made changes that should re-aggregate (e.g., you fixed a manual session-exclusion, or you want to force re-computation after an upgrade), use the **Force re-import** toggle on the Import page. Every night in the source gets re-parsed and re-written.

## What happens after import

Nights with data show up immediately on **Overview** (calendar heatmap), **Daily View**, **Statistics**, and **Trends**. The AI assistant has access to them via its tools the moment the rows land in DuckDB.

Nights you did NOT use the CPAP — the operator left the mask off, traveled, was sick, etc. — don't get imported. URSA-OSCAR only stores data for nights with a recorded therapy session. The Daily View has a clear "No therapy session on this date" message if you navigate to a date the device didn't record. See **What's in a nightly summary** for more on what URSA-OSCAR stores per night.

## Common import problems

If the import fails:

- **"No DATALOG directory found"** — you picked a subdirectory instead of the SD card root. Re-pick at the level that contains `DATALOG/`.
- **Permission denied on the bind-mount** — the api container's uid/gid can't read your bind-mount. Either chmod the source to be world-readable, or set the docker-compose user directive to match.
- **Watcher doesn't trigger** — check `docker logs ursa-oscar-watcher`. The 30-second poll + 30-second quiescence means there's up to a minute of latency between the file appearing and the import firing.

For more on troubleshooting imports, see the **Troubleshooting** section.
