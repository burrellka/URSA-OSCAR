# Deployment topologies

How operators actually run URSA-OSCAR. Three established patterns, in increasing order of operational sophistication.

## Pattern 1 — TrueNAS + Dockge

The maintainer's reference setup. The most common deployment pattern for URSA-OSCAR.

**Hardware:** any TrueNAS SCALE box. The reference deployment runs on an Intel NUC-class box with NVMe storage for the app data and HDD storage for backups.

**Stack:**

- **TrueNAS SCALE** as the host OS — handles ZFS storage, snapshots, the underlying container runtime.
- **Dockge** as the docker-compose UI — manage stacks, edit env vars, view logs, recreate containers. Dockge running on port 5001.
- **URSA-OSCAR** as one stack inside Dockge — the four containers come up together from a single compose file.

**Storage:**

- `/mnt/nvme-apps/apps/ursa-oscar/data` — bind-mounted into the api / watcher containers as `/data`
- `/mnt/nvme-apps/apps/ursa-oscar/cpap-import` — bind-mounted into the watcher container as `/cpap-import` (read-only)

**Backup:** ZFS snapshots on the dataset that contains `/data`, retained for two weeks. Snapshots are local but the dataset is also rsync'd to a separate backup box overnight.

**Access:**

- LAN access via `http://192.168.x.x:5063` (no TLS, scheme-aware cookies handle this correctly)
- Cloudflare tunnel for off-LAN access via `https://ursa-oscar.example.com`
- claude.ai's Custom Connector targets the Cloudflare tunnel's MCP subdomain

This is the deployment Phase 6.4 (auth) was tuned against. If you're starting from scratch and want the path of least resistance, copy this pattern.

## Pattern 2 — Plain Docker on a Linux host

Less infrastructure, more direct.

**Hardware:** any Linux box. A Raspberry Pi 4/5 (4+ GB RAM) handles a single-instance deployment fine; a NUC, Mac mini, or generic mini-PC handles it easily.

**Stack:**

- Docker Engine + docker compose plugin
- The URSA-OSCAR compose file at `/opt/ursa-oscar/docker-compose.yml`
- Bind-mounted data at `/opt/ursa-oscar/data`
- Watcher source at `/opt/ursa-oscar/cpap-import`

**Backup:** rsync or borgbackup of `/opt/ursa-oscar/data` to a separate device on a schedule.

**Access:**

- LAN via `http://<host-ip>:5063`
- Reverse proxy with TLS (nginx on the same host, Caddy, or Traefik) for off-LAN

**Differences from pattern 1:**

- No Dockge UI for stack management — operator uses docker compose commands directly
- No ZFS snapshots — relies on file-system level backups
- No web UI for editing env vars — operator edits compose file in a terminal

Suitable for operators comfortable with the docker compose CLI. Lower resource footprint than TrueNAS.

## Pattern 3 — Synology / QNAP Container Manager

NAS-based deployment without TrueNAS.

**Hardware:** any Synology DSM 7+ or QNAP NAS with Container Manager (Synology) or Container Station (QNAP) installed.

**Stack:**

- Container Manager loads the compose file as a "Project"
- Bind-mounts target NAS volume paths (e.g., `/volume1/docker/ursa-oscar/data`)
- Watcher source on `/volume1/cpap/cpap-import`

**Backup:** Hyper Backup (Synology) or HBS (QNAP) for the data directory.

**Access:**

- NAS reverse proxy (built into DSM 7's Application Portal) for TLS
- LAN access via NAS IP + 5063

**Differences:**

- Bind-mount paths follow the NAS's volume convention (`/volume1/...`)
- Container Manager's UI differs from Dockge but exposes equivalent functionality
- NAS-provided reverse proxy handles TLS without needing nginx/Caddy alongside

This pattern works well if you already have a Synology or QNAP for general home storage; URSA-OSCAR just becomes one more service running on it.

## Compose file structure (any pattern)

Two compose files ship in the repository's `infra/` directory:

- `docker-compose.yml` — dev-flavored, uses env vars for image versions and host paths
- `docker-compose.production.yml` — pinned to specific image versions and TrueNAS paths

Operators typically take `docker-compose.production.yml` as the starting point and edit the bind-mount paths + tunnel hostname to match their setup. The image tags are pinned (currently 1.1.15); pulling a new release means editing the file to bump the tags.

## Required environment variables

Per the production compose:

- `URSA_OSCAR_MCP_BEARER_TOKEN` — static bearer for MCP curl/Desktop/Code access
- `URSA_OSCAR_MCP_BASE_URL` — public URL where the MCP container is reachable (used in OAuth discovery)
- `URSA_OSCAR_MCP_OAUTH_CLIENT_ID` and `URSA_OSCAR_MCP_OAUTH_CLIENT_SECRET` — pre-registered OAuth client (DCR disabled, see Architecture overview)

Optional but commonly used:

- `URSA_OSCAR_JWT_SECRET` — explicit override for the JWT signing key. Default: auto-managed at `/data/jwt_secret`.
- `URSA_OSCAR_MCP_API_TOKEN` — explicit override for MCP's outgoing API bearer. Default: auto-managed at `/data/service_tokens/mcp.jwt`.
- `URSA_OSCAR_WATCHER_TOKEN` — same for watcher. Default: auto-managed.
- `URSA_OSCAR_IMPORT_WEBHOOK_URL` — POST destination on successful import (Home Assistant, ntfy, etc.)

The "auto-managed" defaults are the right answer for most operators. Set the env vars explicitly only if you have a reason to (separate rotation cadence, audit policy, etc.).

## First-boot vs. routine boot

First boot of an empty `/data` volume:

- api container generates `master.key`, `jwt_secret`, and the service tokens
- These persist; restarting the stack reuses them
- The operator visits `/setup` and picks a password

Routine boot (existing `/data` volume):

- api container reads existing secrets, no new generation
- The MCP and watcher containers pick up their service tokens from the volume
- If a service token is within 7 days of expiration, the api re-mints it on startup

Routine boot is silent — no operator action.

## Common operational tasks

| Task | Where to do it |
|---|---|
| Pull a new image release | `docker compose pull && docker compose up -d --force-recreate` |
| Add or rotate an AI provider API key | Web UI → Settings → AI Assistant |
| Change the operator password | Web UI → Settings → Account |
| Forgotten the password | SSH host, `rm /data/auth.json`, restart api, re-bootstrap |
| Inspect why MCP isn't connecting | `docker logs ursa-oscar-mcp` + the **Troubleshooting → MCP connector issues** page |
| Backup `/data` | Operator-managed (rsync, borg, ZFS snapshots, Hyper Backup, etc.) |
| Restore from backup | Stop stack, replace `/data`, start stack, sign in with the previously-set password |

## What to NOT do in production

- **Run with `URSA_OSCAR_DEV_MODE=true`** — that forces cookie Secure=False even on HTTPS. Lockout-proof but security-degraded. Only use for local development.
- **Skip the bind-mount on `/data`** — without a persistent bind-mount, the containers regenerate secrets on every recreate, locking out any existing data.
- **Run multiple instances against the same `/data`** — DuckDB doesn't tolerate it. The second container fails to acquire the writer lock and crashes.
- **Expose port 8000 (api container) to the LAN** — the web container's nginx proxy is the intended public path. Direct api exposure bypasses the web's static-content layer and exposes you to whatever future routing decisions are made there.
