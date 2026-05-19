# Credits and OSCAR attribution

URSA-OSCAR builds on a body of open-source CPAP work without which it could not exist.

## OSCAR (Open Source CPAP Analysis Reporter)

The single most important attribution. OSCAR is the open-source desktop application that figured out — through years of community reverse-engineering — how to read ResMed's proprietary SD card file format. Every URSA-OSCAR nightly summary, AHI breakdown, and pressure waveform comes from EDF and JSON files that OSCAR taught the world how to parse.

URSA-OSCAR's name is that attribution: **U**nified **R**est & **S**omatic **A**nalytics — **OSCAR**. The trailing "OSCAR" is permanent; it's on the /login and /setup pages, the About modal, and this Help system.

OSCAR project: https://www.sleepfiles.com/OSCAR/

URSA-OSCAR is independent of the OSCAR project — different codebase, different deployment model (self-hosted server vs. desktop application), different feature scope. URSA-OSCAR is downstream of OSCAR's file-format work, not a fork or replacement.

## ResMed

The hardware that produces the data URSA-OSCAR analyzes. URSA-OSCAR ingests output from the AirSense 10 and 11 product lines. ResMed itself has no relationship with URSA-OSCAR and provides no support for it. Your warranty, your provider relationships, your prescription — all of those are between you, your sleep medicine provider, and ResMed.

## Open-source dependencies

The major libraries URSA-OSCAR depends on:

- **FastAPI** — HTTP layer in the api container
- **DuckDB** — embedded analytical database
- **pyedflib** + **MNE** — EDF file parsing
- **NumPy / SciPy / pandas / scikit-learn** — analytical math
- **WeasyPrint** — PDF report generation
- **FastMCP** — MCP server framework
- **passlib + argon2-cffi** — password hashing
- **python-jose** — JWT verification
- **React 18** + **Vite 5** + **TypeScript** — web UI
- **uPlot** — time-series charts
- **react-markdown** + **KaTeX** + **highlight.js** — this Help system
- **httpx** — HTTP client used by the MCP server and watcher
- **Anthropic SDK** — Claude provider adapter
- **OpenAI Python SDK** — OpenAI-compatible provider adapter

All major dependencies are MIT, BSD, Apache-2, or LGPL-licensed. URSA-OSCAR itself is GPL-3.0; see the License page.

## AI assistance in development

This codebase was built by a single maintainer with substantial AI assistance — Claude (Anthropic) and ChatGPT (OpenAI) used as collaborators across architecture review, implementation, test authorship, and documentation. The maintainer remains responsible for every line; the AI assistance is acknowledged here rather than hidden.
