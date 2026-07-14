# Concepts — what is Docker, what is a container, what's all this stuff?

This page is for people who want to install URSA-OSCAR but don't have a Docker / containers / Linux background. You don't *have* to read this to install — the platform-specific guides give you exact commands to copy. But if you'd rather understand what you're doing than just type commands, this is the explainer.

We'll cover the concepts that come up in the install path, in the order you encounter them. Plain English, no assumed background.

---

## What is Docker?

**Docker is a program that runs other programs in isolated boxes.**

When you install Microsoft Word, Word installs *on your computer*. It uses your computer's libraries, your computer's settings, your version of Windows. If Word needs a different version of something than another program already installed, conflicts happen.

Docker takes a different approach. Instead of installing Word directly, you'd run Word inside a **container** — a self-contained bundle that has Word *plus everything Word depends on*, all packaged together. The container shares your computer's CPU and RAM, but it doesn't share libraries, settings, or other programs. It's like running Word inside a perfectly-sealed plastic bag that gets opened only at the bits you explicitly let through.

For URSA-OSCAR, this matters because:
- The four containers each bundle their own Python, FastAPI, DuckDB, etc.
- They can't accidentally conflict with anything else on your machine
- When a new version comes out, you replace the container — your computer's other software is untouched
- When you're done with URSA-OSCAR, you stop the containers and they're gone

Docker is one program (the "Docker daemon") that runs in the background. Anything you do with containers — start, stop, inspect, delete — you do by sending commands to the Docker daemon.

---

## What's a container vs. an image?

- An **image** is the recipe — the frozen bundle of "Python plus FastAPI plus URSA-OSCAR's code plus the right settings." Images don't run. They sit on disk.
- A **container** is a running instance of an image. You start a container *from* an image. You can run many containers from the same image at once (think: many Word documents open at the same time, all running the same Word.exe).

Practical example:

```bash
docker pull brain40/ursa-oscar-api:1.1.14      # Download the recipe (image)
docker run brain40/ursa-oscar-api:1.1.14       # Cook a meal (container)
```

When the URSA-OSCAR install guide says "pull the images" — you're downloading the recipes. When it says "start the stack" — you're starting containers from those recipes.

---

## What's docker compose?

URSA-OSCAR is **four** containers (api, web, watcher, plus the optional MCP). Starting them by hand, one at a time, with the right configuration is tedious.

**Docker Compose** is a tool that reads a single YAML file describing all your containers — what images to use, what folders to share, what ports to expose, how they're connected — and starts the whole set with one command. It's like a recipe for a dinner party: instead of cooking four dishes separately, you give Compose the menu and it handles all four in coordination.

The file Docker Compose reads is called `docker-compose.yml`. URSA-OSCAR's lives at `infra/docker-compose.production.yml` in the repository, and you copy it to your install location as `docker-compose.yml`.

Key commands:

```bash
docker compose pull            # Download all the images
docker compose up -d           # Start all the containers in the background
docker compose ps              # Show what's running
docker compose logs <service>  # Look at a service's output
docker compose down            # Stop and remove all containers
```

---

## What's a bind mount?

Containers are sealed bundles. They start with the image's contents, and when they stop, anything they wrote inside the container is **lost**.

That's a problem for data — you don't want your CPAP analytics to vanish when you restart the container.

A **bind mount** opens a small window between a folder on your computer and a folder inside the container. Both sides see the same files. If the container writes a file to its `/data` directory, that file actually lives at `C:\URSA-OSCAR\data` on your hard drive — so it survives restarts, upgrades, container replacement.

The compose file expresses this:

```yaml
    volumes:
      - C:\URSA-OSCAR\data:/data
      - C:\Users\YOU\Documents\OSCAR_Data:/cpap-import:ro
```

Left of the `:` is your host path (what you see in File Explorer). Right of the `:` is the container's path (what the container thinks it's looking at). The `:ro` at the end means **read-only** — the container can read files there but can't write or delete (perfect for your CPAP backup folder, which we don't want URSA-OSCAR touching).

In URSA-OSCAR, two folders get bind-mounted:
- `/data` — where URSA-OSCAR writes its database, secrets, processed analytics
- `/cpap-import:ro` — where it reads your raw CPAP data, read-only

---

## What's a port?

Containers run their own little server programs. The URSA-OSCAR **web** container, for example, runs an nginx server listening on port 80 *inside the container*. But your browser on your laptop can't reach inside the container directly — there's a wall.

A **port mapping** punches a specific hole through that wall. The compose file says:

```yaml
    ports:
      - "5063:80"
```

Which reads: "Take port 80 inside the web container, and expose it as port 5063 on the host." Now when you point a browser at `http://localhost:5063`, the request goes through to the container's port 80, which is the web UI.

If 5063 is already in use on your machine (another program grabbed it), change the **left** side (`5064:80`, `5065:80`, anything free). The right side must stay `80` — that's hardcoded inside the container.

---

## What's the difference between LAN, public URL, reverse proxy?

URSA-OSCAR's web container listens on a port. By default, that port is reachable in three increasing-difficulty scopes:

1. **localhost only** — Only the machine running URSA-OSCAR can reach it. Browser on the host: works. Phone on the same Wi-Fi: doesn't work.

2. **LAN (local network)** — Anything on your home Wi-Fi or wired network can reach it. Browser on your laptop, phone, tablet at home: all work. From a coffee shop: doesn't work. This is what most home deployments use, accessing the web UI from any device in the house.

3. **Public internet** — Anyone, anywhere can reach it. You can use URSA-OSCAR from your phone at the office. This requires either opening a port on your router (risky) or using a **reverse proxy** with TLS encryption (the safe way).

A **reverse proxy** sits in front of URSA-OSCAR's web container and handles TLS (HTTPS), the domain name, and routing. Common reverse proxies: Cloudflare Tunnel (free, easy), nginx + Let's Encrypt (manual TLS setup), Tailscale (private VPN-like access).

Most users don't need a public URL for URSA-OSCAR itself — they access the web UI from inside their home network. The optional MCP connector for claude.ai *does* need a public URL, because claude.ai's servers reach out to your MCP container from the internet.

---

## What's WSL2 (Windows only)?

**Windows Subsystem for Linux, version 2**. It's a Linux kernel that Microsoft ships with Windows. Docker Desktop on Windows uses WSL2 to actually run the containers — even though you launch Docker Desktop from a Windows shortcut, the containers themselves run inside a tiny Linux VM that WSL2 manages.

You don't have to interact with WSL2 directly. You just have to make sure it's installed and at version 2 (not 1, which has different limitations). The Windows install guide's step 1 handles that.

---

## Why are there four URSA-OSCAR containers?

Could URSA-OSCAR be one container? Yes, but four separates concerns:

| Container | Job |
|---|---|
| **api** | The brain. Reads/writes the database (DuckDB), runs the analytics, generates PDFs, proxies AI calls. The only container that touches the database directly. |
| **web** | The face. An nginx web server delivering the React app to your browser. Doesn't know anything about CPAP data; just serves files and proxies API calls to the api container. |
| **watcher** | The janitor. Polls your bind-mounted CPAP folder once a minute. When new files appear, it calls the api container to import them. |
| **mcp** (optional) | The connector. Exposes URSA-OSCAR's analytics to external AI clients like claude.ai over MCP + OAuth. |

This separation means:
- You can scale the web container without affecting the database
- You can stop the watcher temporarily without affecting the web UI
- The api is the only container with database access, so concurrency questions are simple
- The MCP container can be completely absent if you don't need external AI integration

---

## Why does URSA-OSCAR need a password?

Because URSA-OSCAR has access to your nightly CPAP data — the kind of thing you'd rather not be readable by anyone who walks up to your LAN. The web UI requires an operator password.

There's **no password recovery** — URSA-OSCAR doesn't store an email, recovery question, or anything else that could be used to socially-engineer access. If you lose the password, you reset by deleting `<data>/auth.json` and going through first-run setup again. Your analytical data persists; you just pick a new password.

Single-tenant by design. One operator, one password, one instance. If your household has multiple CPAP users, you run multiple URSA-OSCAR instances (one each), not multiple accounts in one instance.

---

## What's an MCP server?

**Model Context Protocol**. A standardized way for AI assistants (like Claude) to call tools and read data from external systems. URSA-OSCAR's MCP container exposes ~17 tools that an AI client can call to read your nightly data, generate trends, produce reports.

Setup is more involved than the rest of URSA-OSCAR — it requires OAuth credentials, a public URL, and registration with the AI client (claude.ai's Custom Connector flow). If you only want the **in-app AI assistant** (web UI → chat panel, bring your own API key), you can skip MCP entirely.

The MCP setup is opt-in by design, separately documented at [mcp-optional-addon.md](mcp-optional-addon.md), and explicitly commented-out in the default compose file. Get the analytics stack working first; add MCP later if you decide you want it.

---

## Glossary

| Term | Meaning |
|---|---|
| **Bind mount** | A folder shared between host and container |
| **Compose** | A tool that reads a YAML file and starts a set of related containers |
| **Container** | A running instance of a Docker image |
| **DuckDB** | The analytical database URSA-OSCAR uses to store CPAP data |
| **Docker Desktop** | The Windows/macOS GUI for Docker |
| **Docker Engine** | The Linux version of Docker (no GUI) |
| **Docker Hub** | The default place Docker images live (similar to GitHub for code) |
| **Fernet** | The encryption format URSA-OSCAR uses for stored secrets |
| **Image** | A frozen recipe for a container |
| **MCP** | Model Context Protocol — how AI assistants call external tools |
| **OAuth** | The authentication flow URSA-OSCAR's MCP container uses |
| **Port** | A numbered communication channel; URSA-OSCAR's web UI uses 5063 by default |
| **TLS** / **HTTPS** | Encryption-in-transit for web traffic |
| **WSL2** | Windows Subsystem for Linux — required for Docker Desktop on Windows |
| **YAML** | The text format compose files are written in; indentation matters |

---

OK, now go install: [windows.md](windows.md) | [linux.md](linux.md) | [macos.md](macos.md) | [truenas.md](truenas.md).
