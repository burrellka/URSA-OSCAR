# Recovering from a lost password

URSA-OSCAR has no email-based password reset. This is a deliberate architectural choice — see the **Single-tenant trust boundary** page for the reasoning. The recovery flow requires host access.

## The fact

If you forget your operator password, the only way back in is:

1. SSH (or otherwise gain shell access) to the docker host
2. Delete `/data/auth.json`
3. Restart the api container
4. Visit `/setup` and pick a new password

Your CPAP data, profile, manual logs, AI provider configurations, and analytical state are all preserved. Only the auth state is reset.

## The actual procedure

### Step 1 — gain host shell access

You need to be able to run commands on the docker host. SSH if the host is networked; physical console if it's a local NAS; the docker host's web shell (DSM Terminal, TrueNAS Shell) if you have a NAS UI.

### Step 2 — locate the `/data` mount on the host

The bind-mount path varies per deployment. Common locations:

- **TrueNAS reference setup**: `/mnt/nvme-apps/apps/ursa-oscar/data/`
- **Plain Docker on Linux**: `/opt/ursa-oscar/data/` or `/srv/ursa-oscar/data/`
- **Synology**: `/volume1/docker/ursa-oscar/data/`
- **QNAP**: `/share/Container/ursa-oscar/data/`

If you don't know yours, check the compose file:

```bash
grep -A 3 "ursa-oscar-api:" /path/to/docker-compose.yml | grep "volumes:" -A 5
```

The bind-mount line looks like `- /some/host/path:/data`. The left side is what you want.

### Step 3 — delete auth.json

```bash
sudo rm /opt/ursa-oscar/data/auth.json
```

Confirm it's gone:

```bash
ls -la /opt/ursa-oscar/data/auth.json
# Should show: No such file or directory
```

### Step 4 — restart the api container

```bash
docker compose restart ursa-oscar-api
# OR if you're in a Dockge/Container Manager UI: click "Restart" on ursa-oscar-api
```

The api container detects auth.json is missing and treats the deployment as "not bootstrapped." First request to the web UI now redirects to /setup.

### Step 5 — pick a new password via /setup

Open the web UI. You land on `/setup` automatically.

- Pick a password (≥12 chars)
- Confirm it
- Click "Set password"

You're signed in with the new password. The session cookie issues immediately.

### Step 6 — store the new password securely

This time, put it in your password manager BEFORE you write it on a sticky note that gets thrown out. The same problem that produced step 1 will produce step 1 again unless you store it durably.

## What gets preserved

After the recovery:

| Preserved | Reset |
|---|---|
| All CPAP data in DuckDB | Operator password |
| Profile, manual logs, vocabulary | Session cookies (you're signed out everywhere) |
| AI provider keys (if `master.key` survived) | — |
| MCP OAuth setup | — |
| Service tokens for MCP / watcher | — |
| Image versions, compose configuration | — |
| Manual session exclusions | — |
| Analytical cache | — |
| User profile and goals | — |

## What if you've also lost the master.key?

If you've lost `/data/master.key` in addition to `auth.json`, your AI provider API keys (encrypted in `/data/secrets.enc`) become unrecoverable. The decryption key is gone.

The recovery:

1. Delete both `auth.json` and `secrets.enc` (the encrypted file is useless without the key)
2. Restart api — it will mint a fresh `master.key` on next boot
3. Re-bootstrap via `/setup`
4. Re-enter your AI provider API keys via Settings → AI Assistant

Everything else (CPAP data, profile, manual logs) is unaffected because none of it depends on `master.key`.

## What if you've lost `/data` entirely?

That's a much more serious problem — see the **Multi-instance deployments** page's backup discussion. The recovery is:

1. Restore `/data` from your most recent backup
2. The auth.json from the backup has the *old* password hash; if you remember that password, you're back in
3. If you don't remember the old password either, delete auth.json again and re-bootstrap (the rest of the restored data is intact)

## What you cannot do

- **Recover the OLD password.** It's stored as an Argon2id hash, not as ciphertext. There's no decryption — Argon2id is one-way. You cannot retrieve the original password from auth.json.
- **Reset via email.** No email surface. Out of scope (see **Single-tenant trust boundary**).
- **Reset via a security question.** None configured. Same reason.
- **Reset via a recovery key.** None generated at bootstrap. (A future feature might generate a one-time recovery code at /setup; not currently implemented.)

## Why this design

The trust boundary is **anyone with host file access**. If we provided email reset, that would mean:

- URSA-OSCAR needs SMTP configuration (more env vars, more failure modes)
- An attacker with access to your inbox could reset your URSA-OSCAR password
- The reset flow needs its own auth (token in the email, single-use, expiration, etc.) — significant complexity

Skipping email reset means: the path from "forgot password" to "back in" requires host access. Anyone who can do step 2 (delete auth.json) was already trusted; the recovery procedure doesn't grant any additional privilege beyond what host access already implies. So the procedure is operationally clean and doesn't expand the trust boundary.

## Preventive measures

- **Store the password in a password manager.** This is the single highest-leverage preventive step.
- **Pick a memorable-but-strong password.** A 4-word passphrase from a password manager (`correct-horse-battery-staple` style) is easier to remember than 16 random characters.
- **Document where `/data` is mounted on your host.** Future-you SSHing in at 2 AM to recover will appreciate it. A `README.md` in `/opt/ursa-oscar/` is fine.
- **Back up `/data` regularly.** Your old auth.json (if you remember the password it corresponds to) gets you back in via restore without needing to re-bootstrap.
