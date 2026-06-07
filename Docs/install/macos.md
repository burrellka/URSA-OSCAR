# Installing URSA-OSCAR on macOS

For macOS users with Docker Desktop. Tested on macOS 14 (Sonoma) and 15 (Sequoia) with Docker Desktop 4.x, both Apple Silicon and Intel.

Time budget: **30 minutes** for a first-time Docker install, **10 minutes** if Docker is already running.

---

## What you'll have at the end

- URSA-OSCAR web UI running at `http://localhost:5063`
- A folder under your home directory (e.g., `~/URSA-OSCAR/data`) where the database lives
- A read-only window into your existing OSCAR data folder
- Automatic nightly imports happening in the background

The optional AI connector for claude.ai is covered separately at the end. Get the analytics working first.

---

## Step 1 — Install Docker Desktop

### Do this

1. Download Docker Desktop for Mac from [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/).
2. **Pick the right architecture**: "Apple Chip" for M1/M2/M3/M4 Macs (the common case in 2026), "Intel Chip" for older Intel-based Macs. Look under Apple menu → About This Mac if you're unsure.
3. Open the downloaded `.dmg`, drag Docker.app into Applications, and launch it.
4. Grant the permissions Docker Desktop asks for (it needs to install a helper).
5. Wait for the green "Engine running" indicator at the bottom of the Docker Desktop window. First launch takes 30-60 seconds.

### What success looks like

Open Terminal and run:

```bash
docker --version
docker run hello-world
```

You should see Docker's version string and the "Hello from Docker!" message.

### If it didn't work

- **Docker Desktop won't launch on Apple Silicon** → Make sure you downloaded the Apple Chip version, not the Intel one. The two `.dmg` files have different names on docker.com.
- **"Cannot connect to the Docker daemon"** → Docker Desktop hasn't finished starting. Wait for the green indicator before running any `docker` command.
- **"Permission denied" or installer asks for sudo password repeatedly** → Normal on first install. Approve the helper installation in System Settings → Privacy & Security.

---

## Step 2 — Decide where files will live

URSA-OSCAR needs two folders:

| What | Where (suggested) | Why |
|---|---|---|
| **URSA-OSCAR data folder** | `~/URSA-OSCAR/data` (which is `/Users/<your-username>/URSA-OSCAR/data`) | DuckDB, secrets, processed analytics. ≥5 GB free. |
| **CPAP backup folder** | `~/Documents/OSCAR_Data` (default OSCAR location on macOS) | Raw nightly data from your CPAP machine. Read-only. |

If your OSCAR data is elsewhere — check OSCAR → Help → About → "Data folder" — use that path instead.

### Do this

```bash
mkdir -p ~/URSA-OSCAR/data
ls -la ~/Documents/OSCAR_Data 2>/dev/null || echo "OSCAR_Data folder not where I expected — find it and use the real path"
```

Note both full paths down — you'll plug them into the compose file in step 4.

---

## Step 3 — Share the folders with Docker Desktop

By default Docker Desktop on macOS can access most paths under `/Users/`. If your CPAP data is on an external drive (`/Volumes/...`), you may need to add it explicitly.

### Do this

1. Open Docker Desktop.
2. Go to **Settings → Resources → File sharing**.
3. Your home directory (`/Users`) should already be in the list. If your CPAP data is under `/Volumes` or another path, click `+` and add it.
4. Click **Apply & Restart**.

---

## Step 4 — Get the compose file and edit it

```bash
mkdir -p ~/URSA-OSCAR && cd ~/URSA-OSCAR
curl -fsSL https://raw.githubusercontent.com/burrellka/URSA-OSCAR/main/infra/docker-compose.production.yml -o docker-compose.yml
```

Open the file in any editor (`open -e docker-compose.yml` for TextEdit, or `code docker-compose.yml` if you have VS Code):

Find the two `volumes:` blocks — one under `ursa-oscar-api`, one under `ursa-oscar-watcher`. Change them to your macOS paths:

```yaml
    volumes:
      - /Users/YOUR-USERNAME/URSA-OSCAR/data:/data
      - /Users/YOUR-USERNAME/Documents/OSCAR_Data:/cpap-import:ro
```

Replace `YOUR-USERNAME` with your actual macOS username (`whoami` in Terminal shows it). Both `volumes:` blocks (api and watcher) need the same paths.

**macOS gotchas:**
- Use **full absolute paths** starting with `/Users/`. `~` doesn't expand inside YAML.
- Indent with exactly 4 spaces for `volumes:` and 6 spaces for each `- ...` item. Tabs break the YAML parser.

Validate before starting:

```bash
docker compose config --quiet && echo OK
```

---

## Step 5 — Bring up the stack

```bash
cd ~/URSA-OSCAR
docker compose pull
docker compose up -d
docker compose ps
```

All three services should show `running`. Open [http://localhost:5063](http://localhost:5063) and pick an operator password (≥12 chars, no recovery, use a password manager).

---

## Step 6 — Verify imports

Watch the watcher pick up files:

```bash
docker compose logs -f ursa-oscar-watcher
```

Press `Ctrl+C` when done watching. The web UI's **Daily View** page should show nights within a minute or two.

If nothing imports, the bind-mount path is likely wrong — see [troubleshooting.md](troubleshooting.md).

---

## Stopping, updating, uninstalling

```bash
# Stop the stack but keep your data:
docker compose down

# Restart it:
docker compose up -d

# Upgrade to a new release (edit image tags in docker-compose.yml first):
docker compose pull && docker compose up -d --force-recreate

# Uninstall and remove all data:
docker compose down
rm -rf ~/URSA-OSCAR/data
```

Your original CPAP backup folder is never touched by URSA-OSCAR.

---

## Getting help

- [Docs/install/troubleshooting.md](troubleshooting.md) — common errors
- [GitHub Issues](https://github.com/burrellka/URSA-OSCAR/issues) — anything URSA-OSCAR-specific
