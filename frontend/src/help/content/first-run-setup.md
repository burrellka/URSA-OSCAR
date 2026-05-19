# First-run setup

URSA-OSCAR's containers are designed to come up clean on a fresh `/data` directory and ask you for exactly two things: an operator password and your SD-card data. Everything else is auto-managed.

## What happens on first boot

When the api container starts against an empty `/data` directory, it:

1. Generates a Fernet master key (`/data/master.key`, mode 0600). This encrypts your AI provider API keys when you save them later.
2. Generates a JWT signing secret (`/data/jwt_secret`, mode 0600). Used to mint and verify session cookies and service tokens.
3. Mints two service tokens (`/data/service_tokens/mcp.jwt` and `/data/service_tokens/watcher.jwt`, both mode 0600). The MCP server and watcher daemon pick these up automatically so they can call the API.

None of these files require operator action. They persist across restarts and rotate automatically when they're close to expiring.

## Step 1 — pick the operator password

Visit the web UI in a browser. On a fresh install you'll land on `/setup` — a single-screen form asking for:

- A password (minimum 12 characters)
- Confirmation of the same password

There is **no email recovery, no password reset link, no security questions**. If you forget the password, the only way back in is to SSH to the docker host, delete `/data/auth.json`, restart the api container, and visit `/setup` again. So:

- **Pick a strong password.** A 4-word passphrase from a password manager (`correct-horse-battery-staple` style) is fine. Random 16 characters from a password manager is fine. Just don't reuse it from another service.
- **Store it in your password manager.** Not on a sticky note, not in a text file on the docker host.

Click "Set password." You'll land on the Overview page, signed in. The sidebar footer will show `operator | sign out` confirming you're authenticated.

## Step 2 — verify the stack is healthy

Before importing data, do a quick health check:

- **Settings → Configuration**: confirm all four image versions are filled in (api, mcp, web, watcher).
- **Settings → MCP Health Check**: click "Verify MCP Connectivity." You should see four green checkmarks. If any fail, check the MCP container's env vars per `Docs/17-oauth-setup.md`.
- **Docker logs**: `docker logs ursa-oscar-watcher` should show "api_client: operator JWT configured; auth header active." That confirms the auto-managed service token is working.

If any of these are wrong, fix them before importing. Importing into a half-configured stack creates support work later.

## Step 3 — set your profile (optional but useful)

Visit **Profile** in the sidebar. Fill in:

- Your active diagnoses (sleep apnea OSA / central / both; comorbidities)
- Active medications
- Sleep medicine provider's name (so the AI assistant doesn't have to ask)
- Treatment goals (target AHI, target mask-on hours)
- Equipment details (machine model, mask type, ramp/EPR settings)

This is read by the AI assistant via the `get_user_profile` tool. Filling it in is what differentiates "AI guessing your context" from "AI knowing your context."

## Step 4 — connect an AI provider (also optional)

Visit **Settings → AI Assistant**. Pick a provider preset (Claude, OpenAI, Gemini, OpenRouter, Groq, local LLM). Paste your API key. The key is encrypted at rest with the master.key generated in step 0 above.

If you skip this, the AI chat panel is hidden. Everything else still works.

## Step 5 — import your first SD card

See the **Importing your first SD card** topic for the three import paths (folder upload, bind-mount drop, path-based UI import).

## What's deliberately not in the first-run flow

A few things URSA-OSCAR could ask you for during /setup but doesn't:

- **Display name / preferred name.** The single-user system uses the literal string "operator." Personalization is a Phase 9+ consideration.
- **Default date range / chart preferences.** The defaults are picked to be sensible across the spectrum of users. Customization adds setup friction without enough payoff.
- **Time zone.** URSA-OSCAR uses device-local time (the CPAP's wall clock) end-to-end. Time zone reasoning is something the UI does on render, not something you configure once.
