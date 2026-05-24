# Multi-instance deployments

URSA-OSCAR is single-tenant by design (see **Single-tenant trust boundary**). The right way to support multiple operators is multiple independent deployments. This page describes that pattern.

## When you need multiple instances

The most common reason: a household with two CPAP users. URSA-OSCAR doesn't have a "switch user" feature, so each person needs their own:

- DuckDB file
- AI provider keys (each person's own API key, billed separately)
- Operator password
- MCP configuration if they want claude.ai integration

A friends-and-family deployment (you running an instance for a parent or sibling) is the same pattern: one instance each.

## What gets duplicated, what stays single

Per instance (separate):

- `/data` volume — every file inside, including the DuckDB, secrets, auth state
- Operator password
- AI provider API keys
- MCP OAuth client (each instance is its own OAuth pre-registered client)
- Manual logs, profile, vocabulary

Shared safely:

- The docker host
- The Docker network (`kairos-net`)
- The Docker images themselves (each instance can reference the same `brain40/ursa-oscar-*:1.1.4` images)
- The reverse proxy (one nginx / Cloudflare tunnel can route to multiple instances)

## Compose layout for two instances

Use two compose files in two directories, each with its own `.env` and its own `/data` mount:

```
/srv/
├── ursa-oscar-alice/
│   ├── docker-compose.yml
│   ├── .env                  # ports 5063 / 8085 / 8082
│   ├── data/                 # alice's DuckDB, secrets, auth state
│   └── cpap-import/          # alice's bind-mount
└── ursa-oscar-bob/
    ├── docker-compose.yml
    ├── .env                  # ports 5064 / 8086 / 8083
    ├── data/                 # bob's DuckDB, etc.
    └── cpap-import/          # bob's bind-mount
```

In each instance's compose env block, override the four host port mappings and the four container names:

```yaml
ursa-oscar-api:
  container_name: ursa-oscar-api-${INSTANCE}   # api-alice or api-bob

ursa-oscar-web:
  container_name: ursa-oscar-web-${INSTANCE}
  ports:
    - "${WEB_HOST_PORT}:80"                    # 5063 for alice, 5064 for bob

ursa-oscar-mcp:
  container_name: ursa-oscar-mcp-${INSTANCE}
  ports:
    - "${MCP_HOST_PORT}:8000"                  # 8085 for alice, 8086 for bob

ursa-oscar-watcher:
  container_name: ursa-oscar-watcher-${INSTANCE}
```

The `kairos-net` network is shared — both instances can attach to it without conflict because each container has a unique name.

If the URSA-OSCAR containers refer to each other by service name (e.g., `URSA_OSCAR_API_URL=http://ursa-oscar-api:8000`), update those to the per-instance container name (`http://ursa-oscar-api-alice:8000`).

## Reverse proxy routing

If you're behind nginx / Caddy / Traefik / Cloudflare, route subdomains to per-instance ports:

```
alice.example.com → 192.168.1.10:5063   (alice web)
bob.example.com   → 192.168.1.10:5064   (bob web)
mcp-alice.example.com → 192.168.1.10:8085 (alice MCP)
mcp-bob.example.com   → 192.168.1.10:8086 (bob MCP)
```

Each subdomain gets its own TLS cert. Each instance's OAuth setup uses its own subdomain's MCP URL.

## Image lifecycle

Both instances can run the same image tag. Upgrade by:

```bash
cd /srv/ursa-oscar-alice && docker compose pull && docker compose up -d --force-recreate
cd /srv/ursa-oscar-bob && docker compose pull && docker compose up -d --force-recreate
```

You can stagger the upgrades if you want — there's no inter-instance compatibility concern, because the instances don't talk to each other.

## Backups

Each instance's backup is its own `/data` directory. Snapshot-based backup (ZFS, Btrfs, LVM, rsync to a NAS) covers everything URSA-OSCAR needs to recover.

Critical files in each `/data`:

- `*.duckdb` and `*.duckdb.wal` — analytical data
- `auth.json` — operator credentials (won't recover the actual password, but recovering this file means recovering the salt + hash so the existing password works)
- `master.key` — encrypted-secrets decryption key. Lose this and your AI provider API keys are unrecoverable from `secrets.enc` (re-enter them in Settings → AI Assistant after restore).
- `jwt_secret` — JWT signing secret. Lose this and all outstanding service tokens invalidate; new ones auto-mint on next api startup.

A daily snapshot to a separate device is the recommended minimum. Two-week retention is plenty (the data underneath rarely changes catastrophically).

## What you DON'T share between instances

- **API keys for AI providers.** Each instance has its own. Don't share an Anthropic key — bills get conflated, you can't tell which instance is using how many tokens.
- **JWT secrets.** Each instance has its own auto-generated one. Cross-instance service tokens would defeat the per-instance auth model.
- **OAuth clients.** Each instance pre-registers its own client_id + client_secret with claude.ai. If both instances try to register the same client, the second one is rejected.
- **Manual logs, profiles, conversations.** These are personal.

## When NOT to do multi-instance

If you actually want multiple users to **share** their CPAP data (e.g., a research study, a clinical practice supporting multiple patients), URSA-OSCAR is not the right tool. The single-tenant architecture is opinionated about not being multi-tenant. See the **Future direction** page for why.

If you just want your sleep medicine provider to have read access to your data, the right pattern is generating a PDF report from the Reports page and emailing or sharing it — not adding their account to your URSA-OSCAR.

## A note on shared docker hosts

You can absolutely run multiple URSA-OSCAR instances on the same docker host. The constraint is the operator running them needs trust in everyone else who has access to that host — because the host-file-access trust boundary applies to everyone.

For a true zero-trust multi-tenant scenario, run instances on separate VMs or physical hosts.
