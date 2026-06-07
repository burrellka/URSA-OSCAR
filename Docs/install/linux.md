# Installing URSA-OSCAR on Linux

For Linux operators with Docker Engine. If you're using a Linux distribution but with Docker Desktop (some Ubuntu users do this), the [Windows guide](windows.md)'s structure is closer — you can adapt from there.

Time budget: **15 minutes** if Docker is already installed, **30 minutes** for a clean install.

---

## What you'll have at the end

- URSA-OSCAR running at `http://<host-ip>:5063` (LAN-reachable)
- `/srv/ursa-oscar/data` holds the database, secrets, processed analytics
- `/srv/ursa-oscar/cpap-import` (or wherever your CPAP backup lives) is read-only mounted into the watcher
- Optional: reverse proxy with TLS for off-LAN access

---

## Prerequisites

### Install Docker Engine + Compose plugin

The convenience script handles 95% of Linux distributions:

```bash
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
sudo usermod -aG docker $USER
newgrp docker   # or log out and back in
```

### Verify it works

```bash
docker --version
docker compose version
docker run hello-world
```

If `hello-world` prints the welcome message, you're set.

If you don't want to use the convenience script, follow the distribution-specific instructions at [docs.docker.com/engine/install](https://docs.docker.com/engine/install/). On Ubuntu/Debian, install `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin`, and `docker-compose-plugin`.

---

## Decide where files will live

| What | Suggested path | Purpose |
|---|---|---|
| URSA-OSCAR data | `/srv/ursa-oscar/data` | DuckDB, secrets, processed analytics. Owned and managed by URSA-OSCAR. |
| CPAP backup source | `/srv/ursa-oscar/cpap-import` (or wherever your SD card backup script writes) | Raw data from your CPAP machine. URSA-OSCAR reads, never writes. |

```bash
sudo mkdir -p /srv/ursa-oscar/data /srv/ursa-oscar/cpap-import
sudo chown -R $USER:$USER /srv/ursa-oscar
```

If your CPAP source is already somewhere else (e.g., `/mnt/nvme/cpap`), use that path instead — you'll wire it up in the compose file.

---

## Get the compose file

```bash
mkdir -p /opt/ursa-oscar && cd /opt/ursa-oscar
curl -fsSL https://raw.githubusercontent.com/burrellka/URSA-OSCAR/main/infra/docker-compose.production.yml -o docker-compose.yml
```

The default compose already uses Linux-style paths (`/srv/ursa-oscar/data:/data` and `/srv/ursa-oscar/cpap-import:/cpap-import:ro`). If your CPAP backup lives somewhere else, edit the two `- /srv/ursa-oscar/cpap-import:/cpap-import:ro` lines (one under `ursa-oscar-api`, one under `ursa-oscar-watcher`) to point at the real path.

Validate before starting:

```bash
docker compose config --quiet && echo OK
```

---

## Start the stack

```bash
docker compose pull
docker compose up -d
docker compose ps
```

All three services should show `running`. Visit `http://<your-host-ip>:5063` from any machine on your LAN, or `http://localhost:5063` if you're on the host.

First visit lands on the setup page. Pick an operator password (≥12 characters, no recovery — use a password manager).

---

## Watching the first import

If your CPAP backup directory already has nightly data in it, the watcher should start importing within 60 seconds. Confirm:

```bash
docker compose logs -f ursa-oscar-watcher
```

You'll see "scanning /cpap-import" lines and per-file import events. Visit **Daily View** in the web UI to see the imported nights.

To trigger manually: web UI → Settings → Maintenance → Trigger import.

---

## Off-LAN access (optional)

If you want to reach URSA-OSCAR from outside your local network, you have three reasonable options:

| Option | When to use | Setup difficulty |
|---|---|---|
| **Cloudflare Tunnel** | You don't want to open ports on your router. Free for non-commercial use. | Low (use Cloudflare's `cloudflared` Docker image with a tunnel token; route your subdomain at the URSA-OSCAR web container's port 5063). |
| **nginx + Let's Encrypt** | You already run a reverse proxy and have a domain. | Medium (standard nginx `proxy_pass http://localhost:5063;` plus `certbot` for TLS). |
| **Tailscale or Wireguard** | You only want yourself (or a small group) to reach it. | Low (install on the host and your client devices; access via the Tailscale IP). |

Whatever you pick, set `X-Forwarded-Proto: https` on the proxy so URSA-OSCAR knows the original scheme — without this, the cookie's Secure flag has to fall back to Origin/Referer detection (still works, but less robust).

The full reverse-proxy walkthrough lives in the in-app help at `/help/arch-network-security` once you have the stack running.

---

## Stopping, restarting, uninstalling

```bash
# Stop, keep data:
docker compose down

# Restart same stack with same data:
docker compose up -d

# Upgrade to a new version (edit image tags in docker-compose.yml first):
docker compose pull && docker compose up -d --force-recreate

# Uninstall (REMOVES YOUR DATA):
docker compose down
sudo rm -rf /srv/ursa-oscar
```

---

## Getting help

- [Docs/install/troubleshooting.md](troubleshooting.md) — common errors
- [GitHub Issues](https://github.com/burrellka/URSA-OSCAR/issues) — for anything specific to URSA-OSCAR
- In-app `/help` — once the stack is running, 37 topics covering every feature
