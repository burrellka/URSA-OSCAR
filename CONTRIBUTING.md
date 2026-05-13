# Contributing to URSA-OSCAR

Thank you for your interest in contributing. URSA-OSCAR is a small, focused project — issues and PRs are very welcome, but please read this guide first.

## Project goals (and non-goals)

**Goals:**
- A self-hosted CPAP analytics service that runs cleanly on a homelab NAS.
- Faithful OSCAR-equivalent analytics for ResMed AirSense devices (Phase 1/2 done; other ResMed/Philips devices welcome contributions).
- AI-assistant integration via the Model Context Protocol (MCP).
- A foundation for correlating subjective signals (medications, symptoms, sleep environment) with objective device data (Phase 3, in progress).

**Non-goals:**
- A clinical-grade medical device.
- A replacement for clinician oversight or interpretation.
- Multi-tenant SaaS — URSA-OSCAR is intentionally single-user-per-instance.

If your idea fits in or near the goals, open an issue first to discuss before sinking time into a PR.

## Before you open a PR

1. **Open an issue first** for anything bigger than a typo or a clear bug fix. We use issues to align on scope before implementation.
2. **No personal data in PRs.** This is non-negotiable. URSA-OSCAR ships with anonymized targets only — never check in real EDF files, real SD-card exports, real CPAP-recording fixtures, or real patient/user identifying data of any kind. If a regression target requires recorded data, propose a synthetic-fixture generator in your issue.
3. **No secrets in PRs.** No API keys, bearer tokens, OAuth client IDs/secrets, or any credential of any kind. The repo's `.gitignore` covers the common cases; PR review will reject anything that slips through.
4. **Keep PRs small.** One concept per PR. A feature + its tests + its docs is one concept. A refactor + a feature is two.

## Style

- **Backend (Python):** PEP 8 + the existing module patterns. Type hints on public functions. Docstrings on anything non-trivial — explain *why*, not what.
- **Frontend (TypeScript):** TypeScript strict mode (already configured). React hooks pattern. Inline styles or CSS-token-driven (per ADR-001 — no Tailwind, no shadcn).
- **Docs:** Markdown. Reference [`Docs/URSA-OSCAR_Design.md`](Docs/URSA-OSCAR_Design.md) for current architecture vocabulary.

## Tests

Backend has three suites:
- `backend/tests/unit/` — fast, no DB or filesystem.
- `backend/tests/integration/` — FastAPI smoke tests against an in-memory DuckDB.
- `backend/tests/regression/` — analytics parity tests. **The fixture set is gitignored** (it's the project author's personal CPAP data). Phase 4 will re-establish synthetic anonymized fixtures.

MCP server tests live in `mcp-server/tests/`.

PRs should add or update tests where they touch logic. Pure cosmetic/doc PRs don't need tests.

## Architectural decisions

We keep design rationale in `Docs/architect-decisions/`. Before proposing a meaningful architectural change (new transport, new storage backend, new auth approach), please read the existing ADRs to see what's been considered and decided. If your PR amends or supersedes an ADR, include the updated ADR in the PR.

## Submitting

1. Fork the repo, branch from `main`.
2. Make your changes, add tests, run the existing test suites locally.
3. Open a PR with a clear title and description. Link the issue you discussed in step 1.
4. Address review comments. Squash before merge.

## License

By contributing, you agree your contributions will be licensed under the project's GNU GPL-3 license (see [LICENSE](LICENSE) and [COPYRIGHT](COPYRIGHT)).

## Acknowledgements (project lineage)

URSA-OSCAR's analytics core is ported from the [OSCAR project](https://www.sleepfiles.com/OSCAR/). The original OSCAR team's work made this possible — see [COPYRIGHT](COPYRIGHT).
