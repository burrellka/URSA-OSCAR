# URSA-OSCAR

> Self-hosted CPAP analytics with optional AI-assisted interpretation. A modernized workflow companion to OSCAR.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)
[![Docker](https://img.shields.io/badge/Docker-2496ED?logo=docker&logoColor=white)](https://hub.docker.com/u/brain40)
[![Release](https://img.shields.io/badge/release-1.1.1-16a34a.svg)](https://github.com/burrellka/URSA-OSCAR/releases/tag/v1.1.1)

URSA-OSCAR reads ResMed AirSense CPAP data and provides:

- Browser-based daily and trend analysis
- Multivariate statistical analysis (partial correlation, lag analysis)
- Predictive modeling with explicit confidence intervals
- Provider-ready PDF reports with methodology disclosure
- Optional AI assistant (Claude, OpenAI, Gemini, OpenRouter, Groq, local LLMs) grounded in your data via MCP tools

It does this as a self-hosted Docker stack. Your CPAP data never leaves your hardware unless you point an external AI provider at it.

---

## Why URSA-OSCAR exists

[OSCAR](https://www.sleepfiles.com/OSCAR/) is the gold standard for desktop CPAP analysis. URSA-OSCAR isn't a replacement — it addresses different workflow needs: 24/7 automated ingestion, longitudinal statistical analysis beyond OSCAR's scope, conversational query interface, and clinician-ready report generation.

If you're satisfied with OSCAR's desktop workflow, you don't need URSA-OSCAR. If you want web access, automation, AI integration, or analytical capabilities OSCAR doesn't provide, URSA-OSCAR is built for that.

---

## Screenshots

> All screenshots below are captured from a live URSA-OSCAR 1.1.1 stack against real CPAP data. The architecture diagram is a hand-authored SVG of the actual four-container topology.

![Architecture overview — four containers, one /data volume](Docs/screenshots/architecture.svg)

| | |
|---|---|
| ![Daily View](Docs/screenshots/daily-view.png) | ![Trends page](Docs/screenshots/trends.png) |
| **Daily View** — per-night detail with EventRug timeline + time-series charts | **Trends** — single-metric regression, correlations, lag analysis, predictions |
| ![AI assistant](Docs/screenshots/ai-chat.png) | ![Reports](Docs/screenshots/reports.png) |
| **AI chat panel** — conversational queries, grounded in your data via MCP tools | **Reports** — provider-ready PDF templates with methodology disclosure |
| ![Help system](Docs/screenshots/help-system.png) | ![Settings → AI](Docs/screenshots/settings-ai.png) |
| **Help system** — 37 in-app topics across 7 sections, AI-assistant accessible | **AI provider configuration** — bring your own key, encrypted at rest |

---

## Quick start

**Requirements**

- Docker + docker compose
- A bind-mountable directory for your CPAP data
- (Optional) An AI provider API key if you want the AI assistant
- (Optional) A reverse proxy if exposing beyond LAN

**Run the stack**

```bash
# Pull the four images
docker pull brain40/ursa-oscar-api:1.1.1
docker pull brain40/ursa-oscar-mcp:1.1.1
docker pull brain40/ursa-oscar-web:1.1.1
docker pull brain40/ursa-oscar-watcher:1.1.1

# Clone for the compose file (or copy infra/docker-compose.production.yml directly)
git clone https://github.com/burrellka/URSA-OSCAR.git
cd URSA-OSCAR
cp infra/docker-compose.production.yml docker-compose.yml
# Edit bind-mount paths and the MCP secrets for your environment

# Bring up the stack
docker compose up -d

# Visit http://<host>:5063 — first visit lands on /setup
# Pick an operator password (>=12 chars, no recovery, store in a password manager)
```

That's the whole onboarding. The api container generates its own Fernet master key, JWT signing secret, and service tokens for the MCP and watcher containers on first boot — no manual key-copying ceremony.

Detailed walkthrough lives in the in-app Help at `/help/first-run-setup` once you've signed in, or browse the source markdown at [frontend/src/help/content/first-run-setup.md](frontend/src/help/content/first-run-setup.md).

---

## Architecture

Four containers sharing a single `/data` volume:

- **api** — FastAPI backend, DuckDB analytics, AI proxy, PDF generation, sole writer of the database (per [ADR-004](Docs/architect-decisions/adr-004-duckdb-rlock.md))
- **web** — nginx + React 18 + uPlot UI
- **mcp** — Model Context Protocol server (FastMCP + SSE + OAuth 2.1 + PKCE), exposes 17 analytical tools to AI assistants
- **watcher** — auto-import daemon that polls a bind-mounted CPAP source and triggers async imports

Single-tenant by design. See [frontend/src/help/content/arch-single-tenant.md](frontend/src/help/content/arch-single-tenant.md) for the trust boundary discussion.

The full architecture deep-dive lives in [Docs/30-developer-guide.md](Docs/30-developer-guide.md) and the in-app Help → Architecture and deployment section.

---

## Documentation

Everything URSA-OSCAR does is documented in-app at `/help`. 37 topics across 7 sections:

| Section | Topics | Browse on GitHub |
|---|---|---|
| Getting started | 4 | [frontend/src/help/content/](frontend/src/help/content/) |
| Using URSA-OSCAR | 8 | feature-by-feature guides |
| Understanding the data | 5 | what AHI / pressure / leak metrics mean |
| Methodology | 6 | every statistical method, verbatim with PDF reports |
| Architecture and deployment | 5 | single-tenant, network security, multi-instance, deployment |
| Troubleshooting | 5 | import, watcher, AI chat, MCP, password recovery |
| About URSA-OSCAR | 4 | credits, license, version, future direction |

The same Help content is queryable by AI assistants via the `get_help_topic` MCP tool — they can read URSA-OSCAR's own documentation when answering questions about it.

Maintainer-facing docs:

- [Docs/30-developer-guide.md](Docs/30-developer-guide.md) — repo layout, container roles, request walkthroughs, schema, build / test / deploy
- [Docs/17-oauth-setup.md](Docs/17-oauth-setup.md) — connecting a claude.ai Custom Connector
- [Docs/35-migration-0.12-to-0.13.md](Docs/35-migration-0.12-to-0.13.md) — upgrade from pre-1.0 versions
- [Docs/architect-decisions/](Docs/architect-decisions/) — ADRs (no Tailwind, MCP-as-thin-proxy, DuckDB concurrency, MCP template adoption)

---

## What URSA-OSCAR isn't

- **A medical device.** Not FDA / CE / TGA-cleared. Not a substitute for clinical judgment. Not a diagnostic tool.
- **A multi-tenant system.** Single operator, single instance. Households with multiple CPAP users run multiple instances ([documented pattern](frontend/src/help/content/arch-multi-instance.md)).
- **A cloud service.** Self-hosted only. No cloud sync, no SaaS option.
- **A replacement for OSCAR.** Different workflow, different audience.

---

## Attribution

URSA-OSCAR is built on the file-format work of the [OSCAR project](https://www.sleepfiles.com/OSCAR/) — the open-source CPAP data viewer that figured out how to read ResMed's proprietary SD card format. Without OSCAR, this wouldn't exist. The "OSCAR" in URSA-OSCAR is that attribution.

URSA-OSCAR is independent of the OSCAR project — different codebase, different deployment model (self-hosted server vs. desktop application), different feature scope. URSA-OSCAR is downstream of OSCAR's file-format work, not a fork or replacement.

GPL-3.0 license matches OSCAR's, preserving the copyleft.

---

## License

URSA-OSCAR is licensed under [GNU GPL-3.0-or-later](LICENSE). The full license text is in `LICENSE`; the project copyright notice and OSCAR-project acknowledgement is in [COPYRIGHT](COPYRIGHT).

---

## Contributing

URSA-OSCAR is currently maintained by a single developer. Contributions are welcome but should align with the architectural posture documented in [frontend/src/help/content/arch-overview.md](frontend/src/help/content/arch-overview.md) and the ADRs.

- **Bugs and feature requests** — [GitHub Issues](https://github.com/burrellka/URSA-OSCAR/issues)
- **Security issues** — [SECURITY.md](SECURITY.md) (don't file public issues)
- **Contribution guide** — [CONTRIBUTING.md](CONTRIBUTING.md)

Pull requests should reference an issue first for anything beyond a typo or clear bug fix. The [developer guide](Docs/30-developer-guide.md) is the easiest entry point for understanding how a change spans the four containers.

---

## Acknowledgments

- The OSCAR community for showing what CPAP analytics could be, and for years of reverse-engineering ResMed's SD card format
- Anthropic for Claude, used heavily as a development collaborator during URSA-OSCAR's construction
- The open-source ecosystem URSA-OSCAR builds on: Python, FastAPI, DuckDB, pyedflib, MNE, NumPy / SciPy / pandas / scikit-learn, WeasyPrint, FastMCP, React 18, Vite, TypeScript, uPlot, lucide-react, react-markdown + KaTeX + highlight.js, passlib + python-jose, httpx, the Anthropic and OpenAI Python SDKs

Not affiliated with ResMed, the OSCAR project, Anthropic, OpenAI, Google, or any other entity referenced in URSA-OSCAR's code or documentation.
