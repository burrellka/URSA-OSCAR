# Installing URSA-OSCAR on TrueNAS SCALE

This is the maintainer's reference deployment — Kevin runs URSA-OSCAR on TrueNAS SCALE with the [Dockge](https://github.com/louislam/dockge) compose UI. If you have a TrueNAS box and want the path that's been tested most thoroughly, this is it.

If you're not on TrueNAS but on a NAS with Container Manager (Synology) or Container Station (QNAP), see [synology.md](synology.md) instead.

---

## Prerequisites

- TrueNAS SCALE 24.x or 25.x with the apps catalog enabled
- A dataset for URSA-OSCAR data — recommend a child dataset of your apps pool with snapshots enabled (the analytical data is worth preserving; ZFS snapshots make backup trivial)
- A directory with your CPAP backup data — could be another dataset, an SMB share you sync to from Windows, or a directory the watcher script writes to

This guide assumes you'll use Dockge as the compose UI. If you prefer Portainer or raw docker compose via SSH, the compose file is the same — only the deployment mechanism differs.

---

## Step 1 — Set up Dockge (if not already installed)

If you don't have Dockge running yet, install it as a TrueNAS app or as its own compose stack. Dockge gives you a clean UI for editing, starting, and inspecting compose stacks without dropping to SSH.

The Dockge container's data directory needs to contain a `stacks/` subdirectory — that's where URSA-OSCAR's compose file will live. A typical Dockge install puts stacks at `/mnt/<pool>/dockge/stacks/`.

---

## Step 2 — Create the URSA-OSCAR datasets

In the TrueNAS web UI:

1. **Datasets → Add Dataset** under your apps pool
2. Create `ursa-oscar` (parent)
3. Inside it, create `data` (where URSA-OSCAR writes)
4. The CPAP source dataset can be `cpap-import` under the same parent, or a separate location you already use for SD-card backups

Typical layout:

```
/mnt/nvme-apps/apps/ursa-oscar/data         ← URSA-OSCAR writes here
/mnt/nvme-apps/apps/ursa-oscar/cpap-import  ← URSA-OSCAR reads here
```

Set the datasets to ZFS snapshots — daily or hourly, retain at least 2 weeks. The data lives here; you want it covered by your snapshot policy.

---

## Step 3 — Create the URSA-OSCAR stack in Dockge

1. In Dockge, click **+ Compose** to create a new stack.
2. Name it `ursa-oscar`.
3. Paste the contents of [infra/docker-compose.production.yml](../../infra/docker-compose.production.yml) from this repo.
4. **Edit the bind-mount paths** to point at your TrueNAS datasets. For the example above:

```yaml
    volumes:
      - /mnt/nvme-apps/apps/ursa-oscar/data:/data
      - /mnt/nvme-apps/apps/ursa-oscar/cpap-import:/cpap-import:ro
```

Replace `nvme-apps` with your actual pool name. Both `volumes:` blocks (api and watcher) need the same paths.

5. Click **Save**.

---

## Step 4 — Start the stack

In Dockge, click the URSA-OSCAR stack and hit **Start**. The first start pulls the four images (~500 MB) and creates the containers.

Watch the logs in the Dockge UI — all three services should reach a steady state within 60 seconds.

---

## Step 5 — First-run setup

Visit `http://<truenas-ip>:5063` from any browser on your LAN. Pick an operator password (≥12 chars, no recovery — use a password manager).

The watcher will start scanning your CPAP import directory once a minute. If you already have data there, nights will appear in the Daily View within a minute or two.

---

## Off-LAN access — Cloudflare Tunnel pattern

This is what Kevin uses, and what the rest of the URSA-OSCAR docs assume. The pattern:

1. Run Cloudflare Tunnel as its own container (or directly via `cloudflared`).
2. Tunnel a subdomain (e.g., `ursa-oscar.yourdomain.com`) to `http://ursa-oscar-web:80` on the docker network.
3. Cloudflare terminates TLS at the edge; your container only ever sees HTTP traffic.
4. URSA-OSCAR's reverse-proxy detection picks up `X-Forwarded-Proto: https` and sets the cookie Secure flag correctly.

To put Cloudflare Tunnel on the same docker network as URSA-OSCAR so it can reach `ursa-oscar-web` by container name, either:

- Add a `networks:` block to both stacks pointing at a shared external network
- Or run Cloudflare Tunnel inside the URSA-OSCAR compose file as a fifth service

The reverse-proxy chapter in the in-app help (`/help/arch-network-security`) has the full walkthrough including the necessary `X-Forwarded-*` headers.

---

## Backup strategy

The `/data` dataset contains everything URSA-OSCAR needs to recover from scratch:

- `*.duckdb` and `*.duckdb.wal` — analytical data
- `auth.json` — operator credentials (salt + hash; restoring this lets the existing password keep working)
- `master.key` — Fernet master key for encrypted secrets. Lose this and your AI provider API keys are unrecoverable.
- `jwt_secret` — JWT signing key
- `service_tokens/` — auto-managed service credentials for MCP and watcher

ZFS snapshots on this dataset are the recommended backup. A daily snapshot + rsync replication to a separate device covers everything.

Your original CPAP backup directory is read-only mounted; URSA-OSCAR never writes to it.

---

## Updating

When a new URSA-OSCAR release is published:

1. In Dockge, edit the URSA-OSCAR stack
2. Change the four `image: brain40/ursa-oscar-*:1.1.14` lines to the new version tag
3. Save and hit **Update**

Dockge runs `docker compose pull && docker compose up -d --force-recreate` for you. Your data persists across the upgrade.

---

## Multi-user households

URSA-OSCAR is single-tenant by design. For multiple CPAP users in the same household, run multiple instances — one per user, on different host ports, with their own dataset.

The pattern is documented in the in-app help at `/help/arch-multi-instance` (and at [frontend/src/help/content/arch-multi-instance.md](../../frontend/src/help/content/arch-multi-instance.md) for the source).

---

## Getting help

- [Docs/install/troubleshooting.md](troubleshooting.md) — common errors
- [GitHub Issues](https://github.com/burrellka/URSA-OSCAR/issues) — anything URSA-OSCAR-specific
- In-app `/help` — 37 topics covering every feature
