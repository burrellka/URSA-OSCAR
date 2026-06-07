# Install troubleshooting

Find your error message in the table of contents, jump to that section. If you don't see your exact error here, open a [GitHub issue](https://github.com/burrellka/URSA-OSCAR/issues) with the full error text and which install step you were on — that's how this doc grows.

## Contents

- [Docker won't start / "500 Internal Server Error"](#docker-wont-start--500-internal-server-error)
- [YAML: "did not find expected key"](#yaml-did-not-find-expected-key)
- [Image not found / manifest unknown](#image-not-found--manifest-unknown)
- [Port already in use](#port-already-in-use)
- ["No such file or directory" on a bind mount](#no-such-file-or-directory-on-a-bind-mount)
- [Permission denied reading CPAP files](#permission-denied-reading-cpap-files)
- [Can't reach the web UI](#cant-reach-the-web-ui)
- [Watcher not importing](#watcher-not-importing)
- [Container keeps restarting](#container-keeps-restarting)
- [Lost the operator password](#lost-the-operator-password)
- [Docker Hub rate limit](#docker-hub-rate-limit)
- ["Mounts denied" (macOS)](#mounts-denied-macos)
- [WSL2 not working (Windows)](#wsl2-not-working-windows)
- [Where to find logs](#where-to-find-logs)

---

## Docker won't start / "500 Internal Server Error"

Exact text varies, e.g.:

```
request returned 500 Internal Server Error for API route and version
http://%2F%2F.%2Fpipe%2FdockerDesktopLinuxEngine/v1.54/images/create?...
check if the server supports the requested API version
```

**Diagnosis:** The Docker daemon isn't responding. Either Docker Desktop isn't running, or (on Windows) WSL2 isn't installed.

**Fix:**

1. Open Docker Desktop from your Start menu / Applications. Wait for the green "Engine running" indicator at the bottom-left before running any commands.
2. Confirm with `docker run hello-world` — should print the welcome message.
3. If hello-world fails the same way, on Windows run `wsl --status` and confirm "Default Version: 2". If it's not, follow [Windows install step 1](windows.md#step-1--enable-wsl2).
4. On macOS, check System Settings → Privacy & Security and approve any pending Docker helper installation.

---

## YAML: "did not find expected key"

Exact text:

```
yaml: while parsing a block mapping at line 7, column 3:
line 78, column 4: did not find expected key
```

**Diagnosis:** Almost always an **indentation error** in the compose file. YAML cares about exact whitespace; tabs and mismatched spaces break the parser.

**Fix:**

The line number the error mentions is approximate — open the file in an editor that shows whitespace (VS Code: View → Render Whitespace) and walk down looking for:

- A service-level key (`restart:`, `volumes:`, `ports:`, `environment:`, `depends_on:`) at any indentation other than **4 spaces from column 0**
- A list item (`- something`) at any indentation other than **6 spaces from column 0**
- A tab character anywhere (YAML doesn't accept tabs — all whitespace must be spaces)

Quick validate:

```bash
docker compose config --quiet
```

If that prints nothing, the file is valid. If it prints an error, the line/column numbers point at the problem.

**Common cause:** copy-pasting from a PDF or browser strips trailing spaces or converts tabs. Re-download the file fresh:

```bash
# Windows PowerShell:
Invoke-WebRequest -Uri "https://raw.githubusercontent.com/burrellka/URSA-OSCAR/main/infra/docker-compose.production.yml" -OutFile "docker-compose.yml"

# Linux/macOS:
curl -fsSL https://raw.githubusercontent.com/burrellka/URSA-OSCAR/main/infra/docker-compose.production.yml -o docker-compose.yml
```

Then re-edit just the bind-mount path lines.

---

## Image not found / manifest unknown

Exact text:

```
Error response from daemon: manifest for brain40/ursa-oscar-api:X.Y.Z not found
```

**Diagnosis:** The image tag in your compose file doesn't exist on Docker Hub. Usually because you copied an old version of the compose file or hand-edited the tags wrong.

**Fix:**

1. Check the [latest release](https://github.com/burrellka/URSA-OSCAR/releases) to see what the current version is.
2. In your `docker-compose.yml`, update every `image: brain40/ursa-oscar-*:X.Y.Z` line to that version.
3. All four images (api, web, watcher, mcp) ship together — they should all use the same tag.
4. Re-run `docker compose pull`.

---

## Port already in use

Exact text:

```
Error: bind: address already in use
or
Bind for 0.0.0.0:5063 failed: port is already allocated
```

**Diagnosis:** Something on your machine is already using port 5063 (the default URSA-OSCAR web port).

**Fix:**

Identify what's using the port:

```bash
# Linux/macOS:
sudo lsof -i :5063

# Windows PowerShell:
Get-NetTCPConnection -LocalPort 5063 | Select-Object OwningProcess
Get-Process -Id <PID-from-above>
```

Then either:
- **Stop the other thing** if you don't need it
- **Change URSA-OSCAR's port** in `docker-compose.yml` — find the `ports:` line under `ursa-oscar-web` and change `5063:80` to `5064:80` (or any free port). The right side stays `80`.

After editing, `docker compose up -d` re-creates the web container on the new port.

---

## "No such file or directory" on a bind mount

Exact text:

```
Error response from daemon: invalid mount config for type "bind":
bind source path does not exist: /path/you/specified
```

**Diagnosis:** The host path in your `volumes:` block doesn't exist.

**Fix:**

1. Check the exact path the error mentions. Is that the path you intended?
2. Create the directory if it should exist:

   ```bash
   # Linux/macOS:
   mkdir -p /path/you/specified

   # Windows PowerShell:
   New-Item -ItemType Directory -Force -Path "C:\path\you\specified"
   ```

3. Common Windows mistake: backslashes in the wrong places. The compose file expects backslashes on the host side (`C:\URSA-OSCAR\data`) and forward slashes on the container side (`:/data`). Don't escape the backslashes — `C:\URSA-OSCAR\data` is correct, `C:\\URSA-OSCAR\\data` is not.

---

## Permission denied reading CPAP files

Exact text (in `docker compose logs ursa-oscar-watcher`):

```
PermissionError: [Errno 13] Permission denied: '/cpap-import/...'
```

**Diagnosis:** Docker can't read your bind-mounted CPAP directory. Usually a permissions or sharing setting.

**Fix:**

- **Windows / Docker Desktop:** Settings → Resources → File Sharing. Make sure the drive that holds your CPAP data is in the list. Apply & Restart.
- **macOS / Docker Desktop:** Settings → Resources → File Sharing. Same as Windows.
- **macOS files synced from OneDrive/Dropbox/iCloud:** Cloud-sync clients sometimes lock files. Try moving a copy of your OSCAR data to a regular local folder and bind-mount that.
- **Linux:** Check the directory's permissions. The watcher runs as a non-root user inside the container. `chmod -R a+rX /path/to/cpap-import` (a+rX means "read for everyone, traverse on directories").

---

## Can't reach the web UI

You opened `http://localhost:5063` and got "connection refused" or "this site can't be reached".

**Diagnosis steps:**

1. Are the containers actually running?

   ```bash
   docker compose ps
   ```

   All three (api, web, watcher) should say `running`. If one says `restarting` or `exited`, jump to [Container keeps restarting](#container-keeps-restarting).

2. Is the web container listening on the port you expect?

   ```bash
   docker compose port ursa-oscar-web 80
   ```

   Should print `0.0.0.0:5063` (or whatever port you mapped). If it says nothing, the port mapping in your compose file is wrong.

3. Are you trying from the host or another device?
   - From the host: `http://localhost:5063` works.
   - From another LAN device: replace `localhost` with the host's LAN IP (e.g., `http://192.168.1.42:5063`).
   - From outside your LAN: doesn't work without a reverse proxy or VPN.

4. Firewall? Windows Defender Firewall or a third-party firewall may block the port. Temporarily allow it for testing.

---

## Watcher not importing

You have CPAP data in the bind-mounted folder but URSA-OSCAR's Daily View is empty.

**Diagnosis steps:**

1. Watcher logs:

   ```bash
   docker compose logs --tail=50 ursa-oscar-watcher
   ```

   Should show "scanning /cpap-import" lines periodically. If it says "directory not found" or similar, the bind-mount path is wrong.

2. Inside the container, what does the watcher see?

   ```bash
   docker compose exec ursa-oscar-watcher ls /cpap-import
   ```

   Should list your CPAP folders. If empty, the bind mount is pointing at the wrong host folder.

3. Manual trigger: web UI → Settings → Maintenance → Trigger import. The api logs (`docker compose logs ursa-oscar-api`) will show what happens.

4. **ResMed AirSense users:** URSA-OSCAR reads ResMed's DATALOG/ format. If you have a non-ResMed machine, the importer doesn't handle it — that's a feature, not a bug. URSA-OSCAR's analytical layer is ResMed-validated.

---

## Container keeps restarting

`docker compose ps` shows one of the containers in `restarting` state.

**Diagnosis steps:**

1. Get the logs to see why it's crashing:

   ```bash
   docker compose logs <service-name>
   # e.g. docker compose logs ursa-oscar-api
   ```

2. Common causes:

   | Log message | Cause | Fix |
   |---|---|---|
   | `URSA_OSCAR_MCP_BEARER_TOKEN is required` | MCP service uncommented but env var missing | Either set the var in a `.env` file next to the compose, or re-comment the MCP service block |
   | `Permission denied: /data/...` | Bind-mounted host folder not writable by container | See [Permission denied](#permission-denied-reading-cpap-files) |
   | `database is locked` | Multiple containers writing to the same database (shouldn't happen with the default compose) | Make sure you don't have two URSA-OSCAR stacks running with the same data folder |
   | `Connection refused: ursa-oscar-api:8000` | api container didn't come up; web/watcher are trying to reach it | Check api logs; usually a bind-mount or environment problem |

3. After fixing, `docker compose up -d --force-recreate` re-creates the broken container.

---

## Lost the operator password

There's no recovery flow by design. To reset:

```bash
# Stop the stack
docker compose down

# Delete the auth file from your data directory
# Windows:
Remove-Item C:\URSA-OSCAR\data\auth.json
# Linux/macOS:
rm /srv/ursa-oscar/data/auth.json
# (substitute your actual data path)

# Restart the stack
docker compose up -d
```

Visit the web UI — you'll land on `/setup` again and can pick a new password. Your imported data is preserved.

---

## Docker Hub rate limit

Exact text:

```
toomanyrequests: You have reached your pull rate limit.
```

**Diagnosis:** Docker Hub limits anonymous downloads to 100 pulls per 6 hours per IP. If you're testing repeatedly or your ISP shares an IP, you can hit this.

**Fix:**

1. Create a free Docker Hub account at [hub.docker.com](https://hub.docker.com).
2. `docker login` on your host — enter the username + password.
3. Logged-in pulls go up to 200 per 6 hours, which is plenty.
4. Alternatively, wait 6 hours and try again.

---

## "Mounts denied" (macOS)

Exact text:

```
Mounts denied: The path /Volumes/... is not shared from the host
and is not known to Docker.
```

**Fix:** Docker Desktop → Settings → Resources → File sharing → click `+` → add the path → Apply & Restart.

---

## WSL2 not working (Windows)

`wsl --status` shows version 1, or the command isn't recognized.

**Fix sequence:**

1. **Update Windows:** Settings → Windows Update → make sure you're on Win10 21H1 or later (`winver` to check; 19041 build minimum).
2. **Enable virtualization in BIOS:** This varies by motherboard manufacturer. Look for "Intel VT-x", "AMD-V", "SVM Mode", or "Virtualization Technology" in BIOS. Enable, save, exit.
3. **Install WSL:** `wsl --install` in admin PowerShell.
4. **Set version 2 as default:** `wsl --set-default-version 2`.
5. **Install a distribution:** `wsl --install -d Ubuntu` (Docker Desktop ships with a minimal distro; the default Ubuntu install is optional but lets you test that WSL2 works).
6. **Restart your computer** after each major step.

If `wsl --install` fails with permission errors, the Windows command is actually `wsl.exe --install` (the .exe matters in some PowerShell configurations).

---

## Where to find logs

URSA-OSCAR doesn't write log files to disk by default — everything goes to Docker's container log stream.

```bash
# Stream live logs from one service:
docker compose logs -f ursa-oscar-api

# Last 100 lines from one service:
docker compose logs --tail=100 ursa-oscar-watcher

# All services at once:
docker compose logs --tail=50

# Save logs to a file for sharing in a bug report:
docker compose logs > ursa-oscar-logs.txt
```

When opening a [GitHub issue](https://github.com/burrellka/URSA-OSCAR/issues), the most useful thing you can attach is the output of:

```bash
docker compose ps
docker compose logs --tail=200
docker --version
```

That triple lets us reproduce the issue without 20 rounds of back-and-forth.

---

## Still stuck?

[GitHub Issues](https://github.com/burrellka/URSA-OSCAR/issues) — open a new issue with:

1. **OS and version** — Windows 11, macOS 14.4, Ubuntu 24.04, etc.
2. **Docker version** — `docker --version`
3. **Step you were on** — "Step 7 of windows.md", "trying to start the stack", etc.
4. **Exact error text** — copy-paste, don't paraphrase
5. **Logs** — `docker compose logs --tail=200` if relevant

The more specific the report, the faster the fix gets back to you.
