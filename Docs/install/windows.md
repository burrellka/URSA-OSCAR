# Installing URSA-OSCAR on Windows 11

Tested on Windows 11 with Docker Desktop. Should also work on Windows 10 with WSL2, but the screenshots here are from Windows 11.

This is the **full walkthrough**. If you already use Docker daily, you can skip to [step 5](#step-5--get-the-urs-aoscar-files). For everyone else, work through the steps in order. Each step says **what to do**, **what you should see when it worked**, and **what to do if it didn't**.

Time budget: **30-60 minutes** for a first-time Docker install, **10 minutes** if Docker is already running.

---

## What you'll have at the end

- The URSA-OSCAR web UI running at `http://localhost:5063`
- A folder on your hard drive (`C:\URSA-OSCAR\data`) where the database lives
- A read-only window into your existing OSCAR data folder
- Automatic nightly imports happening in the background

The **optional AI connector** for claude.ai is covered separately at the end. Skip it for now if you're new — get the analytics working first.

---

## Step 1 — Enable WSL2

Docker Desktop on Windows 11 requires **WSL2** (Windows Subsystem for Linux, version 2). Docker Desktop installs but doesn't run without it, and the installer doesn't always make this prerequisite obvious.

### Do this

1. Open **PowerShell as Administrator** (right-click the Start menu → "Terminal (Admin)" or "Windows PowerShell (Admin)").
2. Run:

   ```powershell
   wsl --install
   ```

3. Restart your computer when prompted.
4. After restart, open PowerShell again (regular, not Admin) and run:

   ```powershell
   wsl --status
   ```

### What success looks like

```
Default Distribution: Ubuntu
Default Version: 2
```

The "Default Version: 2" line is the important one. If you see "Default Version: 1" or no default distribution, WSL2 isn't set up correctly.

### If it didn't work

- **"WSL is not recognized"** → Windows version too old (need Win10 build 19041+ or Win11). Run `winver` to check.
- **Got version 1 instead of 2** → Run `wsl --set-default-version 2` then `wsl --install -d Ubuntu`.
- **BIOS virtualization disabled** → Reboot into BIOS, find "Virtualization Technology" or "VT-x" or "SVM", enable it, save and exit. This varies by motherboard manufacturer; search "[your computer model] enable virtualization BIOS" if you can't find it.

---

## Step 2 — Install Docker Desktop

### Do this

1. Download Docker Desktop from [docker.com/products/docker-desktop](https://www.docker.com/products/docker-desktop/).
2. Run the installer. When asked, **leave the "Use WSL 2 instead of Hyper-V" option checked**.
3. After install, restart your computer if asked.
4. Launch Docker Desktop from the Start menu.
5. **Wait for the green "Engine running" indicator** at the bottom-left. This can take 30-60 seconds on first launch.

### What success looks like

Open PowerShell and run:

```powershell
docker --version
docker run hello-world
```

You should see something like:

```
Docker version 27.x.x, build ...
Hello from Docker!
This message shows that your installation appears to be working correctly.
```

### If it didn't work

- **"500 Internal Server Error" or "request returned 500 ... check if the server supports the requested API version"** → Docker Desktop isn't running. Open it from the Start menu and wait for the green indicator. This is the single most common Windows install error.
- **"Hardware assisted virtualization and data execution protection must be enabled in the BIOS"** → Same fix as step 1's virtualization issue.
- **"Docker Desktop requires WSL 2"** → Step 1 didn't complete successfully. Re-run `wsl --status` and confirm "Default Version: 2".
- **Docker Desktop installs but refuses to start** → Try **Settings → Resources → WSL Integration** and toggle it. Reset Docker Desktop to factory defaults from the troubleshoot menu if needed.

---

## Step 3 — Decide where files will live

URSA-OSCAR needs two folders on your Windows drive:

| What | Where (suggested) | Why |
|---|---|---|
| **URSA-OSCAR data folder** | `C:\URSA-OSCAR\data` | The DuckDB database, secrets, processed analytics. URSA-OSCAR creates and owns the contents. Should be on a drive with at least 5 GB free. |
| **CPAP backup folder** | `C:\Users\<your-username>\Documents\OSCAR_Data` (default OSCAR location) | The raw nightly data from your CPAP machine. URSA-OSCAR reads from here, never writes. This is whatever directory your SD card backs up to. |

If your OSCAR data is in a different location, use that path instead — you just need to know where it is. Common alternatives: `D:\OSCAR_Data`, `C:\CPAP`, or your OneDrive folder.

### Do this

1. Create the URSA-OSCAR data folder. Open PowerShell and run:

   ```powershell
   New-Item -ItemType Directory -Force -Path "C:\URSA-OSCAR\data"
   ```

2. Confirm where your CPAP backup lives. Open OSCAR → Help → About → "Data folder" tells you where OSCAR is reading from. That's the path URSA-OSCAR will also read from.

3. Write both paths down — you'll need them in step 5.

### What success looks like

```powershell
Test-Path C:\URSA-OSCAR\data
# Should print: True
```

---

## Step 4 — Share the drives with Docker Desktop

By default, Docker Desktop on Windows can access most paths under `C:\` automatically through WSL2. But if your CPAP data is on a different drive (D:, E:), or if you see "permission denied" later, you need to explicitly share the drive.

### Do this

1. Open Docker Desktop.
2. Go to **Settings** (gear icon, top-right).
3. Go to **Resources → File Sharing**.
4. If you don't see the drive that holds your CPAP data, click **+** and add it.
5. Click **Apply & Restart**.

### What success looks like

The drives you need (typically `C:\`, sometimes also `D:\` or `E:\` if your CPAP data lives there) appear in the File Sharing list with a green check.

---

## Step 5 — Get the URSA-OSCAR files

You need two files: the **compose file** (tells Docker what to run) and your **local copy of it** (which you'll edit).

### Do this

The easiest way: download the compose file directly.

1. Open PowerShell and create a working directory:

   ```powershell
   New-Item -ItemType Directory -Force -Path "C:\URSA-OSCAR"
   Set-Location C:\URSA-OSCAR
   ```

2. Download the compose file:

   ```powershell
   Invoke-WebRequest -Uri "https://raw.githubusercontent.com/burrellka/URSA-OSCAR/main/infra/docker-compose.production.yml" -OutFile "docker-compose.yml"
   ```

### Alternative: clone the whole repo

If you'd rather have the full source (helpful if you want to read docs or contribute):

```powershell
Set-Location C:\
git clone https://github.com/burrellka/URSA-OSCAR.git
Set-Location URSA-OSCAR
Copy-Item infra\docker-compose.production.yml docker-compose.yml
```

### What success looks like

```powershell
Get-Item C:\URSA-OSCAR\docker-compose.yml
# Should print the file info; LastWriteTime should be today.
```

Open the file in Notepad (`notepad C:\URSA-OSCAR\docker-compose.yml`) and confirm it starts with `# URSA-OSCAR — public reference compose`.

---

## Step 6 — Edit the bind-mount paths

The compose file uses Linux-style paths by default (`/srv/ursa-oscar/data:/data`). You need to swap those for your Windows paths.

Open `C:\URSA-OSCAR\docker-compose.yml` in Notepad (or VS Code, or any text editor). Find the two `volumes:` sections — one under `ursa-oscar-api`, one under `ursa-oscar-watcher`. They look like this:

```yaml
    volumes:
      # EDIT THESE TWO LINES for your platform (see comment block above).
      - /srv/ursa-oscar/data:/data
      - /srv/ursa-oscar/cpap-import:/cpap-import:ro
```

### Do this

Change each `volumes:` block to use your Windows paths from step 3. For most users that will be:

```yaml
    volumes:
      - C:\URSA-OSCAR\data:/data
      - C:\Users\YOUR-USERNAME\Documents\OSCAR_Data:/cpap-import:ro
```

Replace `YOUR-USERNAME` with your actual Windows username (the one you see in `C:\Users\` — open File Explorer and look). Keep the `:/data` and `:/cpap-import:ro` parts exactly as shown — those are the paths *inside* the container, which don't change.

**Important Windows gotchas:**

- Use **backslashes** (`\`) on the left side, **forward slashes** (`/`) on the right side. That asymmetry is correct.
- Indent with **exactly 4 spaces** for the `volumes:` line and **exactly 6 spaces** for each `- ...` list item. Tabs or mismatched spaces break the YAML parser. (If you've ever pasted text from a PDF, double-check the indentation — PDFs sometimes lose spacing.)
- Make sure both `volumes:` sections (api and watcher) get the same paths.

### What success looks like

After saving the file, validate it with:

```powershell
Set-Location C:\URSA-OSCAR
docker compose config --quiet
```

If you get no output, the YAML is valid. If you see `yaml: while parsing a block mapping ... did not find expected key`, you've got an indentation problem — go back and check that all your `volumes:` lines use 4 spaces, and all the `- ...` items under them use 6 spaces.

---

## Step 7 — Pull the images

Docker needs to download the four URSA-OSCAR container images from Docker Hub before it can run them. This is a one-time download (~500 MB total) — subsequent starts use the cached copies.

### Do this

```powershell
Set-Location C:\URSA-OSCAR
docker compose pull
```

### What success looks like

```
[+] Pulling 4/4
 ✔ ursa-oscar-api Pulled
 ✔ ursa-oscar-web Pulled
 ✔ ursa-oscar-watcher Pulled
```

(Three pulls is correct — the optional MCP service is commented out by default.)

### If it didn't work

- **"image not found" or "manifest unknown"** → You may have an old or mistyped image tag in the compose file. Make sure all four image lines say `:1.1.12` (or whatever version is current; check the [latest release](https://github.com/burrellka/URSA-OSCAR/releases) if unsure).
- **"too many requests" or "rate limit"** → Docker Hub's anonymous pull rate-limit. Wait 10 minutes and try again, or create a free Docker Hub account and `docker login`.
- **"500 Internal Server Error"** → Docker Desktop isn't running. Back to step 2.

---

## Step 8 — Bring up the stack

### Do this

```powershell
docker compose up -d
```

The `-d` flag means "detached" — the containers run in the background and you get your prompt back.

### What success looks like

```
[+] Running 4/4
 ✔ Network ursa-oscar_default       Created
 ✔ Container ursa-oscar-api          Started
 ✔ Container ursa-oscar-web          Started
 ✔ Container ursa-oscar-watcher      Started
```

Confirm the containers are actually running:

```powershell
docker compose ps
```

Should show `running` next to all three services.

### If it didn't work

- **"port is already allocated"** → Something else on your machine is using port 5063. Either stop that thing, or change `5063:80` to `5064:80` (or any free port) in the compose file.
- **A container shows `restarting` or `exited`** → Get its logs to see why:

   ```powershell
   docker compose logs ursa-oscar-api
   ```

  Common causes: bind-mount path doesn't exist on your host, or the path isn't shared with Docker Desktop (back to step 4).

---

## Step 9 — Open the web UI

### Do this

In your browser, go to: **[http://localhost:5063](http://localhost:5063)**

You'll land on the **first-run setup page**. Pick an operator password — minimum 12 characters, **no recovery** (we don't store an email or recovery question; if you lose the password you reset by deleting `C:\URSA-OSCAR\data\auth.json` and going through setup again). Store it in a password manager.

### What success looks like

After picking a password, you're on the URSA-OSCAR home page. The web UI shows "0 nights imported" because we haven't pointed the watcher at anything yet.

### If it didn't work

- **Browser shows "connection refused" or "this site can't be reached"** → Either the web container isn't running (`docker compose ps`) or it's listening on a different port than you expect. Check the compose file's `ports:` line.
- **"Setup already complete" but you don't have the password** → Delete `C:\URSA-OSCAR\data\auth.json` and restart the API container: `docker compose restart ursa-oscar-api`.

---

## Step 10 — Watch the first import happen

The watcher container looks at your bind-mounted CPAP folder once a minute. If it sees new files, it imports them automatically.

### Do this

Either:
- **If your OSCAR data was already in the folder you bind-mounted**, the watcher should already be importing in the background. Check the **Daily View** page in the web UI — within a few minutes you should see nights appearing.
- **Manually trigger an import** to test: in the web UI, go to **Settings → Maintenance → Trigger import**.

### What success looks like

The **Daily View** page lists nights, each with AHI and pressure data. Click any night to see the detailed breakdown.

### If it didn't work

- **No nights show up** → Get the watcher logs:

   ```powershell
   docker compose logs ursa-oscar-watcher
   ```

   Most common cause: the bind-mount path is wrong. The watcher logs say what directory it's watching — make sure that directory exists *inside the container*, which means the host path you specified in step 6 must exist.
- **"Permission denied" reading CPAP files** → Docker Desktop's WSL2 backend sometimes has trouble with files synced from cloud storage (OneDrive, Dropbox). Try moving a copy of your OSCAR data to a regular local folder and pointing the watcher at that.

---

## What's next

You now have URSA-OSCAR's analytics layer running. Things to try:

- **Daily View** — pick a recent night, look at the EventRug, pressure traces, and event timeline
- **Trends** — pick a metric (AHI, pressure, leak rate) and see the regression line, lag analysis, predictions
- **Reports** — generate a provider-ready PDF with statistical methodology disclosure
- **AI Assistant (in-app)** — go to Settings → AI Assistant, add your provider API key (Anthropic, OpenAI, Gemini, OpenRouter, Groq, or a local LLM), and the chat panel becomes a conversational interface to your data
- **Help** — the in-app `/help` page has 37 topics covering every feature in detail

If you want to add the **external AI connector** (claude.ai Custom Connector, Claude Code MCP, etc.), see [Docs/install/mcp-optional-addon.md](mcp-optional-addon.md). That's the advanced second-day setup; not needed for the in-app AI assistant.

---

## Updating to a new version

When a new URSA-OSCAR release is published:

```powershell
Set-Location C:\URSA-OSCAR
# Edit docker-compose.yml — change the four "image: brain40/ursa-oscar-*:1.1.12"
# lines to the new version tag.
docker compose pull
docker compose up -d --force-recreate
```

That's it. Your data persists in the bind-mounted `C:\URSA-OSCAR\data` folder — restarts and upgrades don't touch it.

---

## Stopping URSA-OSCAR

```powershell
Set-Location C:\URSA-OSCAR
docker compose down
```

This stops and removes the containers but leaves your data in `C:\URSA-OSCAR\data`. Running `docker compose up -d` brings the same stack back with all your imported data intact.

---

## Uninstalling

```powershell
Set-Location C:\URSA-OSCAR
docker compose down
Remove-Item -Recurse -Force C:\URSA-OSCAR\data
```

That removes the containers and the URSA-OSCAR data folder. Your original CPAP backup folder is untouched (URSA-OSCAR only ever reads from it). To free disk space from the downloaded container images:

```powershell
docker image rm brain40/ursa-oscar-api:1.1.12 brain40/ursa-oscar-web:1.1.12 brain40/ursa-oscar-watcher:1.1.12
```

---

## Getting help

- **Common error messages** → [Docs/install/troubleshooting.md](troubleshooting.md)
- **What is Docker / a container / a bind mount** → [Docs/install/concepts.md](concepts.md)
- **Something specific to URSA-OSCAR is broken** → [github.com/burrellka/URSA-OSCAR/issues](https://github.com/burrellka/URSA-OSCAR/issues)

When opening a GitHub issue, please include: your Windows version, your Docker Desktop version (`docker --version`), the exact error message, and which step you were on. That triple makes the issue actionable.
