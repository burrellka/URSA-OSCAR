# Operator Setup Guide

End-to-end deployment walkthrough for someone who's not the original maintainer and wants to run URSA-OSCAR on their own homelab. Targets the typical TrueNAS + Dockge setup but works on any Docker Compose host.

---

## Table of contents

1. [Prerequisites](#1-prerequisites)
2. [Provisioning storage](#2-provisioning-storage)
3. [Network setup](#3-network-setup)
4. [Generating secrets](#4-generating-secrets)
5. [Initial deployment](#5-initial-deployment)
6. [First-start operator action (master key)](#6-first-start-operator-action-master-key)
7. [Connecting your AI provider(s)](#7-connecting-your-ai-providers)
8. [Optional — Claude.ai MCP connector](#8-optional--claudeai-mcp-connector)
9. [Optional — file-watcher webhook](#9-optional--file-watcher-webhook)
10. [Importing your first SD card](#10-importing-your-first-sd-card)
11. [Backup strategy](#11-backup-strategy)
12. [Version management](#12-version-management)
13. [Upgrade procedure](#13-upgrade-procedure)
14. [Operator FAQ](#14-operator-faq)

---

## 1. Prerequisites

You need:

- **A Docker host.** TrueNAS SCALE, Synology Container Manager, plain Docker on a Raspberry Pi, etc. URSA-OSCAR runs on amd64 and arm64.
- **About 5 GB of disk** for the four container images, plus however much your CPAP data needs (~50 MB per 100 nights with full waveforms, plus growth headroom).
- **2 GB of RAM** budgeted for the stack (the API container is the heaviest under import load).
- **Network access to Docker Hub** for the initial image pull and updates.
- **One operator who can edit Docker Compose env blocks.** That's it. URSA-OSCAR doesn't have a web-based admin UI for secrets rotation; everything sensitive happens via the operator's env editor.

Optional, but recommended:
- A reverse proxy with TLS (Cloudflare Tunnel, Caddy, Traefik, nginx-proxy) for the web UI and especially for the MCP server if you'll connect Claude.ai to it
- API credits from at least one LLM provider if you want the in-app chat (~$5 of Claude API credits covers a few months of moderate use)

---

## 2. Provisioning storage

URSA-OSCAR needs **one bind-mount** that persists across container restarts — the data directory. It holds:

- `ursa-oscar.duckdb` — the DuckDB file (your CPAP data, manual logs, AI config, import history)
- `ursa-oscar.duckdb.wal` — DuckDB write-ahead log
- `profile.json` — your clinical profile + display preferences
- `vocab.json` — autocomplete vocabularies
- `ai_config.json` — non-secret AI proxy config (provider, model, endpoint)
- `secrets.enc` — encrypted API keys
- `secret_key.gen` — first-boot generated master key (you delete this after copying to compose env)

Plus a **second** bind-mount for the SD-card import path (read-only inside the container):

- `<host>/cpap-import` → `/cpap-import` in the API + watcher containers

This is where you'll drop the contents of your CPAP SD card. The watcher daemon monitors this path; new `DATALOG/YYYYMMDD/` directories trigger auto-imports.

### TrueNAS SCALE

Create two datasets:
- `/mnt/<pool>/apps/ursa-oscar/data` — mode 0700, owned by the container runtime user
- `/mnt/<pool>/apps/ursa-oscar/cpap-import` — mode 0755

Mount the SD card to `/mnt/<pool>/apps/ursa-oscar/cpap-import` via your CPAP-data sync workflow (rsync, SMB share, manual copy — whatever you already do).

### Plain Docker host

```bash
mkdir -p /opt/ursa-oscar/{data,cpap-import}
chmod 700 /opt/ursa-oscar/data
chmod 755 /opt/ursa-oscar/cpap-import
```

---

## 3. Network setup

URSA-OSCAR uses an external Docker network. The default name is `kairos-net`; you can rename it but the references in `infra/docker-compose.yml` need to match.

```bash
docker network create kairos-net
```

Service-to-service traffic happens entirely on this network via Docker's embedded DNS — the API container is reachable at `ursa-oscar-api:8000` from the MCP / web / watcher containers. None of the inter-container traffic touches the host network.

### Public exposure

You'll typically expose two ports:
- **Web UI** (default `5063:80` mapped from `ursa-oscar-web`) — your daily review interface
- **MCP server** (default `8085:8000` mapped from `ursa-oscar-mcp`) — only needed if you'll connect Claude.ai

Put a TLS-terminating proxy in front of both. The MCP server **must** be HTTPS for Claude.ai's connector to work; the web UI is HTTPS-recommended for hygiene.

---

## 4. Generating secrets

You need three or four secret values before first deploy:

### MCP bearer token

```bash
openssl rand -base64 32 | tr -d '/+= \n'
```

This is the static bearer token. Both Claude.ai's MCP connector config AND your compose env need it.

### MCP OAuth client ID + secret

```bash
# Client ID (any random opaque string works)
openssl rand -base64 16 | tr -d '/+= \n'

# Client secret
openssl rand -base64 32 | tr -d '/+= \n'
```

These two get configured in Claude.ai's connector as well as your compose env. See [`Docs/17-oauth-setup.md`](17-oauth-setup.md) for the full Claude.ai connector setup.

### Fernet master key (first-boot)

You can either generate it now and set it in the env, or let URSA-OSCAR generate it on first boot (§6 below). Pre-generating:

```bash
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

If you set this in compose env from the start, URSA-OSCAR uses it immediately and no first-boot action is needed.

---

## 5. Initial deployment

Copy the compose file:

```bash
mkdir -p /opt/ursa-oscar
curl -L https://raw.githubusercontent.com/burrellka/URSA-OSCAR/main/infra/docker-compose.yml \
  > /opt/ursa-oscar/docker-compose.yml
cd /opt/ursa-oscar
```

Edit the env block. The relevant fields:

```yaml
services:
  ursa-oscar-api:
    image: brain40/ursa-oscar-api:0.9.1
    environment:
      URSA_OSCAR_DB_PATH: /data/ursa-oscar.duckdb
      URSA_OSCAR_IMPORT_WATCH_PATH: /cpap-import
      URSA_OSCAR_EXPORTS_PATH: /data/exports
      URSA_OSCAR_MCP_INTERNAL_URL: http://ursa-oscar-mcp:8000
      URSA_OSCAR_MCP_BASE_URL: https://mcp.your-domain.example
      URSA_OSCAR_MCP_BEARER_TOKEN: <paste from generation step>
      URSA_OSCAR_MCP_OAUTH_CLIENT_ID: <paste from generation step>
      URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET: <paste from generation step>
      URSA_OSCAR_IMAGE_VERSION: 0.9.1
      URSA_OSCAR_MCP_IMAGE_VERSION: 0.7.0
      URSA_OSCAR_WEB_IMAGE_VERSION: 0.9.0
      URSA_OSCAR_WATCHER_IMAGE_VERSION: 0.9.0
      # Optional: pre-generate the Fernet key. Leave unset to let
      # URSA-OSCAR generate it on first boot (see §6 below).
      URSA_OSCAR_SECRET_KEY: <Fernet key or leave blank>
    volumes:
      - /opt/ursa-oscar/data:/data
      - /opt/ursa-oscar/cpap-import:/cpap-import:ro

  ursa-oscar-mcp:
    image: brain40/ursa-oscar-mcp:0.7.0
    environment:
      URSA_OSCAR_API_URL: http://ursa-oscar-api:8000
      URSA_OSCAR_MCP_BEARER_TOKEN: <same as above>
      URSA_OSCAR_MCP_BASE_URL: https://mcp.your-domain.example
      URSA_OSCAR_MCP_OAUTH_CLIENT_ID: <same as above>
      URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET: <same as above>
    ports:
      - 8085:8000

  ursa-oscar-web:
    image: brain40/ursa-oscar-web:0.9.0
    ports:
      - 5063:80

  ursa-oscar-watcher:
    image: brain40/ursa-oscar-watcher:0.9.0
    environment:
      URSA_OSCAR_API_URL: http://ursa-oscar-api:8000
      URSA_OSCAR_WATCH_PATH: /cpap-import
      URSA_OSCAR_POLL_INTERVAL: "30"
      URSA_OSCAR_QUIESCENCE_SECONDS: "30"
      URSA_OSCAR_IMPORT_WEBHOOK_URL: ""   # optional; see §9
      URSA_OSCAR_FORCE_REIMPORT: "false"
    volumes:
      - /opt/ursa-oscar/data:/data
      - /opt/ursa-oscar/cpap-import:/cpap-import:ro
```

Bring it up:

```bash
docker compose up -d
```

Verify the four containers are running:

```bash
docker ps --filter "name=ursa-oscar"
```

Hit the health endpoint:

```bash
curl http://localhost:5063/healthz
# {"ok":true,"service":"ursa-oscar-api"}
```

Open the web UI at `http://<host>:5063`. You should land on the Overview page with the empty-state message ("No CPAP data yet").

---

## 6. First-start operator action (master key)

If you left `URSA_OSCAR_SECRET_KEY` empty in §5, the API generates a key on first boot. Watch the logs:

```bash
docker logs ursa-oscar-api | grep "URSA_OSCAR_SECRET_KEY"
```

You'll see something like:

```
WARNING - URSA_OSCAR_SECRET_KEY is unset. Generated a fresh Fernet key and
wrote it to /data/secret_key.gen. Copy this value into your compose env as
URSA_OSCAR_SECRET_KEY=<value>, then delete /data/secret_key.gen.
```

Grab the key:

```bash
cat /opt/ursa-oscar/data/secret_key.gen
# gAAAAABl... (45-char urlsafe-base64 string)
```

Paste it into your compose env block as `URSA_OSCAR_SECRET_KEY`. Then:

```bash
rm /opt/ursa-oscar/data/secret_key.gen
docker compose up -d --force-recreate ursa-oscar-api
```

From now on, the API uses the persisted key from env on every boot. The encrypted secrets blob (`/data/secrets.enc`) is decryptable as long as the key stays the same. If you lose the key, you lose access to the stored API keys — but the rest of your data (CPAP nights, profile, logs) is unaffected.

**Back up your `URSA_OSCAR_SECRET_KEY` somewhere safe.** Treat it like a password manager master key.

---

## 7. Connecting your AI provider(s)

URSA-OSCAR ships seven provider presets. You can configure any one of them (or several over time and swap between them).

### Open Settings → AI Assistant

In the web UI: top-right gear icon → click **AI Assistant**, or go to `/settings/ai` directly.

### Pick a provider

Dropdown loads the seven presets:

- **Claude API (Anthropic)** — best tool-calling reliability. Get a key from [console.anthropic.com](https://console.anthropic.com). Recommended.
- **OpenAI** — `gpt-4o` and friends. Get a key from [platform.openai.com](https://platform.openai.com).
- **Google Gemini (OpenAI-compat)** — uses Google's compatibility layer. `gemini-1.5-flash` is more reliable for tool calling than `2.0-flash-exp`. Get a key from [aistudio.google.com](https://aistudio.google.com). Free tier exists.
- **OpenRouter** — multi-model proxy; try many models with one key. Get a key from [openrouter.ai](https://openrouter.ai).
- **Groq** — fast inference, free tier. Get a key from [groq.com](https://console.groq.com).
- **Local LLM** — LocalAI, Ollama, llama.cpp server, vLLM, LM Studio, etc. No key needed for most.
- **Custom (OpenAI-compatible)** — for Azure OpenAI deployments, self-hosted forks, etc.

### Fill the fields

After picking a provider, the endpoint URL and model dropdown auto-populate. You only need to:

1. Paste your API key (for cloud providers)
2. Optionally change the model from the default
3. For Local LLM: pick **Direct** routing if you're hitting LocalAI/Ollama directly, or **Through proxy** if you have a RAG layer like LocalRecall in the middle
4. Click **Test connection** — expect a green ✓ with model info
5. Click **Enable AI Assistant**
6. Click **Save**

### Verify

Go to any Daily View (e.g., the most recent night). Click the blue **Ask URSA** button in the header. The chat panel slides in from the right. Try a suggested prompt:

> "How was my sleep on YYYY-MM-DD?"

You should see:
1. The AI immediately starts streaming a response
2. A tool-call chip appears below the user message: `get_nightly_summary — date=...`
3. The chip's status changes from "running…" to a one-line summary: `AHI 3.94 · 2 sessions`
4. The AI's final answer streams in below, citing the real data

If the chat works, you're done with §7.

### Recommended local models

Local LLM support works with any OpenAI-compatible inference server (LocalAI, Ollama, llama.cpp server, vLLM, LM Studio). What matters more than the inference server is the model itself — specifically, its tool-calling reliability and clinical reasoning quality.

| Model | Min RAM/VRAM | Tool calling | Clinical reasoning | Recommended for |
|---|---|---|---|---|
| Qwen 2.5 32B Instruct (Q5_K_M) | ~24 GB | Excellent | Strong | Primary recommendation if you have the hardware |
| Llama 3.3 70B Instruct (Q4_K_M) | ~45 GB | Very good | Very strong | High-end homelab; best quality at this tier |
| Hermes 3 Llama 3.1 8B (Q5_K_M) | ~7 GB | Good | Moderate | Good single-GPU choice |
| Qwen 2.5 14B Instruct (Q5_K_M) | ~12 GB | Good | Moderate | Balance of speed and quality |
| Mistral Nemo 12B (Q4_K_M) | ~9 GB | Good | Moderate | Strong tool calling, fast |
| Llama 3.2 3B Instruct (Q4_K_M) | ~3 GB | Limited | Poor | Plumbing test only — NOT for clinical reading |

#### Why model size matters

URSA-OSCAR's AI assistant operates in a clinical context. Small models (under 7B parameters) regularly hallucinate clinical facts even when grounded with correct tool data. During URSA-OSCAR Phase 5 acceptance testing, Llama 3.2 3B confidently invented AASM threshold values and unit conversions ("events per minute" instead of "events per hour"), despite the `get_nightly_summary` tool returning the correct data.

For users who need URSA-OSCAR's AI assistant to be reliable for treatment reasoning, the recommendation is unambiguous: **use a 14B+ tool-capable model**. If your hardware can run a 32B model, do so. If you're constrained to <7B, use the Claude API instead — the model quality difference is significant enough to matter clinically, and Anthropic's free-tier pricing makes the trade-off real even for budget-conscious operators.

#### Verifying a model actually calls tools

After picking a model, send the verification query from above ("How was my sleep on YYYY-MM-DD?"). If you see no tool-call chip and the model answers from thin air — invented numbers, generic CPAP advice — the model is failing to call the tool. This is not a URSA-OSCAR bug; it's the model's tool-calling reliability falling short of what URSA-OSCAR's tool surface needs. Pick a stronger model from the table above.

If the tool chip appears but with `INVALID_ARGUMENT` or similar error, the model called the tool with malformed JSON. Some smaller models fail to format function-call JSON correctly under load — symptomatic of the same underlying weakness. Same recommendation: pick a stronger model.

---

## 8. Optional — Claude.ai MCP connector

URSA-OSCAR also exposes its tools via an MCP server for use directly inside the Claude.ai web app or API (not the in-app chat — the in-app chat is the AI proxy from §7). This lets you set up "URSA" as a Project in Claude.ai with the MCP connector, and Claude has access to your CPAP data + analytical tools in any conversation.

See [`Docs/17-oauth-setup.md`](17-oauth-setup.md) for the full Claude.ai connector configuration. Key points:

1. Your MCP server must be public-HTTPS (Cloudflare Tunnel works great for this)
2. Configure the OAuth client ID + secret + bearer token in Claude.ai's connector setup
3. Claude.ai walks the operator through an OAuth authorization flow on first connect
4. After authorization, Claude has access to all 11 tools

The two AI paths (in-app chat from §7, Claude.ai connector here) are **independent**. You can run both. The MCP server gates auth via static bearer + OAuth; the in-app chat is browser-only and trusts the LAN.

---

## 9. Optional — file-watcher webhook

Phase 4 added a watcher daemon that auto-imports new SD-card data. You can wire its completion event to any webhook receiver — ntfy, Slack, Home Assistant, Discord, custom service, anything that accepts a POST.

Set the env var:

```yaml
URSA_OSCAR_IMPORT_WEBHOOK_URL: https://ntfy.your-domain.example/ursa-imports
```

The payload shape:

```json
{
  "event": "import_completed",
  "job_id": 12,
  "status": "completed",
  "nights_imported": 1,
  "nights_skipped": 0,
  "nights_skipped_existing": 65,
  "earliest_date": "2026-05-14",
  "latest_date": "2026-05-14",
  "error_message": null,
  "source_path": "/cpap-import",
  "force_reimport": false
}
```

Use cases:
- **ntfy** push notification to your phone when a new night is imported
- **Home Assistant** automation: turn on a light, log to a sensor history, etc.
- **Slack** message to your sleep-tracking channel
- **Custom service** that pulls the day's full report and emails it to your sleep doc

The webhook fires AFTER the import completes server-side. If the import failed, the payload has `status: "failed"` and an `error_message`.

---

## 10. Importing your first SD card

URSA-OSCAR has three import paths. Pick whichever fits your workflow.

### Option A: Folder upload from your laptop

This is the easiest path if your CPAP setup doesn't include a NAS-attached card reader.

1. Eject the SD card from your CPAP machine
2. Insert into your laptop
3. Open the URSA-OSCAR web UI → Import page
4. Click **Choose folder...**
5. Pick the SD card root (the folder that contains `DATALOG/`)
6. The browser uploads to URSA-OSCAR; progress bar shows percentage
7. Once upload completes, an import job runs in the background; the result tile updates when it's done

### Option B: Drop into the bind-mount

If you have a NAS-attached card reader (or you sync the SD card via SMB / rsync):

1. Copy the SD card's `DATALOG/` contents into your bind-mounted import path (e.g., `/opt/ursa-oscar/cpap-import`)
2. The watcher daemon detects the new content within ~30 seconds
3. Waits 30 seconds for filesystem quiescence (so it doesn't fire mid-copy)
4. Auto-triggers an import via the API
5. Logs the result; fires webhook if configured

### Option C: Path-based import via the UI

If you've already manually copied your data into the bind-mount and don't want to wait for the watcher:

1. Web UI → Import page
2. **Source path** field defaults to `/cpap-import` (the bind-mount path inside the container)
3. Click **Start import**
4. Immediate response with a job ID; the page polls until completion

### Force re-import

Default behavior: nights already in the DB are skipped (fast, cheap). To re-parse everything (after an importer change, or to refresh a specific date range), check **Force re-parse nights already in the database** before triggering the import.

---

## 11. Backup strategy

### What to back up

The only directory that matters is your data bind-mount (e.g., `/opt/ursa-oscar/data`). Specifically:

- `ursa-oscar.duckdb` — irreplaceable; your CPAP data + analytics + manual logs + AI config
- `profile.json` — your clinical profile + DeviceClock + display preferences
- `vocab.json` — autocomplete state
- `secrets.enc` — encrypted API keys (useless without the Fernet master key; back up the env file separately)
- `ai_config.json` — non-secret AI proxy settings

You do NOT need to back up the SD-card import path (your CPAP machine has the source data) or the Docker images (re-pullable from Docker Hub).

### How

The simplest path is a daily `tar.gz` snapshot:

```bash
# On the Docker host
TS=$(date +%Y%m%d)
tar -czf /backups/ursa-oscar-data-$TS.tar.gz /opt/ursa-oscar/data
```

Make sure the DuckDB writer isn't mid-transaction when the tar runs. The lowest-risk approach is to stop the API container during the snapshot:

```bash
docker compose stop ursa-oscar-api
tar -czf /backups/ursa-oscar-data-$TS.tar.gz /opt/ursa-oscar/data
docker compose start ursa-oscar-api
```

Or use DuckDB's `CHECKPOINT` command first (via the Data Management page in the UI) and accept that backing up the WAL alongside the main file gets a consistent snapshot in most cases.

For TrueNAS users: ZFS snapshots on the dataset are even better — atomic + space-efficient.

### Separate: back up your master key

`URSA_OSCAR_SECRET_KEY` lives in your compose env block. Without it, the `secrets.enc` backup is useless (you'd need to re-enter your API keys on restore). Back up the env block separately — to a password manager is reasonable.

---

## 12. Version management

URSA-OSCAR follows a **strict version pinning policy** as of Phase 5.5. Every container in your stack pins to an explicit `X.Y.Z` image tag — never `:latest`. The Settings page version chips read the same env vars that drive the tag, so the chip you see always matches what's actually running. No drift between cosmetic and deployed.

### Why pinned, not `:latest`

`:latest` looked convenient during the 0.9.x patch cycle ("just `docker compose pull` and you get the new fix"), but it has three costs that compound as URSA-OSCAR moves into clinical-reasoning territory:

1. **Reproducibility.** If your AHI correlation jumps between yesterday and today, "what version of URSA-OSCAR were you running?" should have a deterministic answer.
2. **Rollback safety.** If a new push regresses something, `:latest` already pulled the bad bits to your local Docker cache. With pinning, you change one line + `docker compose up -d` and you're back on the known-good version.
3. **Audit trail.** Once Phase 6 brings predictive modeling + provider PDF reports, the version that produced any given output matters clinically. Pinning is what lets you say "this report was generated by URSA-OSCAR 0.10.2."

### Currently shipped versions

As of Phase 5.5:

| Container | Version |
|---|---|
| `ursa-oscar-api` | `0.9.8` |
| `ursa-oscar-web` | `0.9.4` |
| `ursa-oscar-mcp` | `0.7.0` |
| `ursa-oscar-watcher` | `0.9.0` |

The `infra/docker-compose.production.yml` reference in the repo always pins to the current shipped set, so a fresh checkout + `make up` brings up exactly these versions.

### Operator compose discipline

Your Dockge / local compose file should have explicit `image: brain40/ursa-oscar-api:X.Y.Z` lines, not `:latest`. Example:

```yaml
services:
  ursa-oscar-api:
    image: brain40/ursa-oscar-api:0.9.8       # explicit; bump deliberately
    container_name: ursa-oscar-api
    environment:
      URSA_OSCAR_IMAGE_VERSION: 0.9.8          # MUST match image: tag above
      URSA_OSCAR_MCP_IMAGE_VERSION: 0.7.0
      URSA_OSCAR_WEB_IMAGE_VERSION: 0.9.4
      URSA_OSCAR_WATCHER_IMAGE_VERSION: 0.9.0
      # ... rest of env ...
```

A useful sanity check: the version chips on `/settings` should display the same numbers as your `image:` tags. If they don't match, you've drifted.

## 13. Upgrade procedure

When new image versions ship:

1. Check the [public GitHub releases](https://github.com/burrellka/URSA-OSCAR/releases) or the `WIP/` build handover docs for the changelog
2. Bump **both** the `image:` tag and the matching `URSA_OSCAR_*_IMAGE_VERSION` env var in your compose file:
   ```yaml
   services:
     ursa-oscar-api:
       image: brain40/ursa-oscar-api:0.10.0   # was 0.9.8
       environment:
         URSA_OSCAR_IMAGE_VERSION: 0.10.0     # was 0.9.8 — keep paired with image: tag
   ```
   The env var alone won't change which image runs; the `image:` tag is the source of truth. Forgetting one or the other is the most common version-management mistake.
3. Bring the stack down + up:
   ```bash
   docker compose pull
   docker compose up -d --force-recreate
   ```
4. Watch logs for any migration errors:
   ```bash
   docker logs ursa-oscar-api 2>&1 | grep -i migration
   ```
5. Hit `/healthz` to confirm the API is back up
6. Hit `/settings` to confirm the version chips match what you set
7. Hit the Daily View to confirm the UI is back up

Migrations run automatically at API startup (the `apply_migrations()` lifespan hook). They're idempotent — running twice is a no-op.

### Rollback

If an upgrade breaks something:

1. Revert your compose env to the prior version
2. `docker compose up -d --force-recreate`
3. The DuckDB schema is forward-compatible within a major version, so rolling back the API container against an upgraded DB usually works fine. Cross-major-version rollbacks may need a schema downgrade, which we'll document if/when a non-additive migration ever ships.

---

## 14. Operator FAQ

### Q: My SD card has YYYY-MM-DD dates that don't look like real times.

URSA-OSCAR shows you the wall-clock time the device recorded. If your CPAP device isn't DST-adjusted, the displayed times are off by your DST offset (typically 1 hour). Phase 4 Ticket 4 added the DeviceClock display offset feature:

- Profile → **Device clock** section
- Country: your country (informational)
- Mode: **Device on fixed offset (DST-aware)**
- Device UTC offset: whatever your device's clock is set to (e.g., UTC-5 for US East Coast on standard time)
- The UI auto-computes the per-night display shift using your browser's timezone

Server-side data stays in device-clock; only the display shifts. Exports, MCP tool responses, and AI tool responses all use device-clock — that's intentional so the data has a single unambiguous frame.

### Q: I deleted a night by mistake. Can I get it back?

Re-import the SD card. Imports are idempotent — already-known nights get skipped; the deleted-then-re-imported night will land fresh. If you don't still have the SD card data, the night is gone (this is documented in the Data Management UI as "hard delete — no archive or restore").

### Q: How big does the DuckDB file get?

About 3-5 MB per night with full waveforms (pressure + leak + flow + tidal vol + minute vent + resp rate + snore at 0.5 Hz each, plus the high-res 25 Hz flow channel if you include it). A year of nightly data is roughly 1-2 GB. DuckDB compresses well, so this is much less than equivalent SQLite or Postgres would use.

### Q: My CHECKPOINT didn't shrink the DB file.

DuckDB's `CHECKPOINT` persists the WAL into the main file but doesn't reclaim allocator-tracked blocks. Disk-space reclamation happens over time as future inserts reuse freed blocks. The Data Management UI has an inline disclaimer about this. If you really need the disk back, the only path right now is export-everything → delete the DB file → re-import; we documented that as a deferred housekeeping item.

### Q: Is the in-app AI safe? Does it see my CPAP data?

The AI sees only what tools return. It cannot access your DuckDB directly. If you use a **cloud provider** (Claude, OpenAI, Gemini, OpenRouter, Groq), your conversations are processed per that provider's terms — they see the parts of your data that get returned from tool calls. If you use a **Local LLM**, conversations stay on your local network.

Settings → AI Assistant has a Privacy section that lists this in detail. Read it once.

### Q: The chat shows "Tool-call loop limit reached."

The model chain-called tools more than 8 times in one conversation turn without converging. Common with small models (Llama 3.2 3B, some lightly-trained quantized models). Try:

1. Restart the conversation (clear button in the chat panel header)
2. Ask a more focused question
3. Switch to a stronger model (Sonnet 4.5, GPT-4o, Gemini 1.5 Pro)

The 8-iteration cap is a deliberate safety against runaway loops that would burn tokens forever.

### Q: I'm getting nginx 413 errors on upload.

The web container's nginx is configured with `client_max_body_size 5000m` (5 GB). If you're hitting 413 anyway:

1. Make sure you're running web 0.6.1 or later (earlier versions had a 1 MB default)
2. Check the nginx logs in the web container

A full ResMed SD card with several months of data is typically 200-800 MB, so the 5 GB limit gives plenty of headroom.

### Q: How do I rotate an API key?

Settings → AI Assistant → click in the API key field → paste the new value → Save. The old key is overwritten in `secrets.enc`. Test connection to confirm the new key works.

To clear a key entirely without setting a new one: send an empty string via the API (`POST /api/v1/ai/config {api_key: ""}` with the relevant provider_id) or use a tool like curl. The UI doesn't have a "delete key" button right now — that's noted as housekeeping.

### Q: Does URSA-OSCAR work with non-ResMed CPAP machines?

The importer is built for ResMed AirSense 11. The DATALOG layout + EDF channel naming + EVE.edf event encoding are specific to ResMed. Other manufacturers (Philips, Apex, Fisher & Paykel) have different formats and aren't currently supported. Adding support would be a Phase 6+ feature — happy to discuss in a GitHub issue.
