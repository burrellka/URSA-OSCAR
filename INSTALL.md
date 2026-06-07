# Installing URSA-OSCAR

This guide is for people who want to actually run URSA-OSCAR, not just look at the code. It's written assuming you have **no prior Docker experience**. If you do, you can skip straight to your platform's guide and skim.

If you've used Docker before and want the 30-second version, jump to the [README Quick Start](README.md#quick-start). Everything else here is for people who'd rather walk than run.

---

## What this is, in plain English

URSA-OSCAR is a set of small programs that run on your computer (or homelab, or NAS). It reads your CPAP machine's nightly data, stores it in a database, and shows you what your sleep is doing in a web browser. You point a web browser at it. That's the experience.

It's distributed as **four Docker containers**. Don't worry about what that means yet — for now, think of them as four small apps that come pre-packaged, talk to each other on a private channel inside your computer, and write their data to a folder on your hard drive that you control.

You'll need:

- **A computer that can run Docker.** Windows 11, macOS, Linux, TrueNAS, Synology, QNAP — all work. A Raspberry Pi 4 or 5 also works.
- **A folder where URSA-OSCAR can store its data** (a few MB to a few GB over time).
- **A folder where your CPAP backup lives** — the directory your SD card copies to, or the OSCAR backup folder.

That's it. No accounts to register, no cloud service to sign up for, nothing leaves your machine unless you explicitly turn on the optional AI assistant.

---

## Pick your path

| Your situation | Read this |
|---|---|
| **Windows 10 / 11** with Docker Desktop | [Docs/install/windows.md](Docs/install/windows.md) |
| **macOS** with Docker Desktop | [Docs/install/macos.md](Docs/install/macos.md) |
| **Linux** (Ubuntu, Debian, Fedora, etc.) with Docker Engine | [Docs/install/linux.md](Docs/install/linux.md) |
| **TrueNAS SCALE** with Dockge or Portainer | [Docs/install/truenas.md](Docs/install/truenas.md) |
| **Synology** or **QNAP** with Container Manager | [Docs/install/synology.md](Docs/install/synology.md) |
| **I don't even know what Docker is** | [Docs/install/concepts.md](Docs/install/concepts.md), then come back here |
| **Something broke** | [Docs/install/troubleshooting.md](Docs/install/troubleshooting.md) |

---

## Optional, do this later

After the analytics layer is running and you've imported some data, you might want to add the **external AI connector** — this is what lets claude.ai (or other MCP clients) reach your URSA-OSCAR data through a custom connector. It involves generating some secrets, exposing a public URL, and is genuinely the most complicated piece of the system. Save it for a second sitting.

When you're ready: [Docs/install/mcp-optional-addon.md](Docs/install/mcp-optional-addon.md).

---

## If you get stuck

GitHub Issues is the right place to ask: [github.com/burrellka/URSA-OSCAR/issues](https://github.com/burrellka/URSA-OSCAR/issues). When you open an issue, include:

1. **Your OS and Docker version** (`docker --version`)
2. **The exact error message** you're seeing
3. **What step in the install guide you were on** when it broke

Don't be shy about asking. Every confused install path that gets surfaced makes the next person's life easier — the docs you're reading right now exist because someone else got stuck first.
