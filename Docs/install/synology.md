# Installing URSA-OSCAR on Synology / QNAP

URSA-OSCAR runs fine on Synology DSM 7+ with Container Manager, and on QNAP with Container Station. The general shape is the same as the [Linux guide](linux.md), but you'll use the NAS's GUI for compose management instead of the docker CLI.

This guide is shorter than the others because (a) the docker-compose file is identical and (b) Synology/QNAP UIs change frequently — pointing at exact menu paths now would go stale within a year.

---

## Prerequisites

- **Synology**: DSM 7.0 or later. Install **Container Manager** from Package Center.
- **QNAP**: QTS 5.0 or later. Install **Container Station** from App Center.

Make sure your NAS model actually supports Docker. A few low-end Synology and QNAP units don't — check the spec sheet.

---

## Step 1 — Create the URSA-OSCAR shared folder

On Synology:
- **Control Panel → Shared Folder → Create** → name it `ursa-oscar`
- Inside it (via File Station), create subdirectories: `data` and `cpap-import`

On QNAP:
- **Control Panel → Shared Folders → Create a Shared Folder** → `ursa-oscar`
- Same subdirectory structure

The data subdirectory needs to be writable. The cpap-import subdirectory is where you'll copy your CPAP backup (or sync from a file share).

Typical paths:
- Synology: `/volume1/ursa-oscar/data` and `/volume1/ursa-oscar/cpap-import`
- QNAP: `/share/ursa-oscar/data` and `/share/ursa-oscar/cpap-import`

(Replace `volume1` / `share` with your actual volume name.)

---

## Step 2 — Create the compose project

In **Container Manager** (Synology) or **Container Station** (QNAP), create a new compose project / application:

- **Name:** `ursa-oscar`
- **Source:** Paste the contents of [infra/docker-compose.production.yml](../../infra/docker-compose.production.yml) from this repo

Edit the bind-mount paths in the `volumes:` blocks to your NAS paths:

```yaml
    volumes:
      - /volume1/ursa-oscar/data:/data
      - /volume1/ursa-oscar/cpap-import:/cpap-import:ro
```

Both `volumes:` blocks (api and watcher) need the same paths.

Validate, save, and start.

---

## Step 3 — First-run setup

Visit `http://<nas-ip>:5063` from any browser on your LAN. Pick an operator password (≥12 chars, no recovery, password manager).

If you've already populated the `cpap-import` directory with OSCAR data, the watcher imports it within a minute.

---

## Off-LAN access

DSM 7's **Application Portal** can serve as a reverse proxy with TLS, terminating at port 5063 of your URSA-OSCAR web container. QNAP's equivalent is the **App Center** reverse-proxy module.

For both NAS platforms, add a custom hostname for URSA-OSCAR and route it through the NAS's TLS termination. The NAS handles certificate issuance via Let's Encrypt.

---

## Backup

Synology Hyper Backup or QNAP HBS — point at the `ursa-oscar/data` shared folder. The data subdirectory is what URSA-OSCAR needs to recover; the cpap-import subdirectory is just a mirror of source data you have elsewhere.

---

## Getting help

- [Docs/install/troubleshooting.md](troubleshooting.md) — common errors
- [GitHub Issues](https://github.com/burrellka/URSA-OSCAR/issues) — anything URSA-OSCAR-specific

The Linux guide is closer to what you're doing under the hood — if a step on your NAS UI is confusing, the [linux.md](linux.md) guide describes the equivalent CLI commands the NAS GUI is running on your behalf.
