# ADR-001 — Frontend Stack

**Status:** Accepted
**Date:** 2026-05-11
**Resolves:** URSA-OSCAR Design v1.0 § Architect Decision 7 (Frontend framework — TBD pending APEX inspection)
**Inputs:** `phase-0-apex-findings.md`, `phase-0-synthesis.md` Conflict 1, APEX `docs/14-current-architecture-and-filelist.md` §8 + §12

---

## Context

Design Decision 7 deferred the frontend stack choice pending Phase 0 inspection of APEX, with a working hypothesis of "React + Vite + Tailwind." The kickoff doc made this an explicit escalation trigger: *"If APEX is not React/Vite/Tailwind, pause and escalate before continuing."*

Phase 0 inspection found the hypothesis is half-right and half-wrong:
- **Right:** APEX is React 18 + TypeScript + Vite.
- **Wrong:** APEX does **not** use Tailwind. The current stack uses hand-rolled CSS custom properties (`web/src/index.css`) and raw `fetch()` for API calls. Doc 14 §12 documents this as a deliberate decision, not an outstanding migration: *"Custom CSS reaches the same Jobscan aesthetic faster; rebuild not on the v1 critical path."*
- Also wrong: APEX `package.json` declares `@tanstack/react-query`, `react-hook-form`, and `zod`, but grep against `web/src/` finds zero imports of any of them. They are stale aspirational deps.

## Decision

URSA-OSCAR frontend stack:

| Layer | Choice | Reason |
|---|---|---|
| Framework | React 18 + TypeScript (strict) | Matches APEX |
| Build tool | Vite | Matches APEX |
| Routing | `react-router-dom` ^6 | Matches APEX (confirmed in use) |
| Styling | **Hand-rolled CSS custom properties**, no Tailwind / shadcn / Radix / MUI | Matches APEX deliberate choice; operational consistency between sibling homelab UIs |
| Design tokens | Start from `apex-system/web/src/index.css` verbatim; extend with URSA-OSCAR-specific additions for chart palette | Visual unity across Kevin's homelab |
| API calls | Raw `fetch()` initially | Matches APEX; revisit if Phase 2 charting benefits from a caching layer |
| Forms | Native form elements | Matches APEX; revisit at Phase 3 Manual Logging if needed |
| Icons | `lucide-react` | Matches APEX |
| Drag-drop | `@dnd-kit/*` only if needed | Matches APEX; Daily View doesn't need it; Manual Logging spreadsheet view might |
| Charting (time-series) | uPlot (per Design Decision 3) | High-volume points, 60fps pan/zoom |
| Charting (calendar heatmap) | D3 or custom canvas (per Design § Tech Stack) | Overview screen |

Typography: Inter (Google Fonts, 400/500/600/700). Accent color `#2563eb`. Status palette: good `#16a34a`, warn `#d97706`, bad `#dc2626`. Card grammar: white card, 10px radius, 1px border, `shadow-sm`. Sidebar: 240px fixed, padding `1.5rem 0.75rem`. Page title 1.75rem / 600.

## Consequences

**Positive:**
- Phase 2 frontend scaffolding starts from a known-working pattern, not a clean slate.
- Visual unity across Kevin's homelab (APEX and URSA-OSCAR share a design language).
- No Tailwind config + content-scan + JIT-compile build complexity.
- No utility-class soup in heavy custom-layout components (Daily View has 8-10 stacked synchronized charts; layout-level CSS is clearer than utility classes for this).
- uPlot doesn't benefit from Tailwind — it styles its canvas directly.

**Negative:**
- Two sibling projects (APEX, URSA-OSCAR) hand-maintain two parallel CSS token sheets. If APEX's tokens drift, URSA-OSCAR has to track manually. Acceptable for single-user homelab cadence.
- No type-safe form handling out of the box. URSA-OSCAR will hit this at Phase 3 Manual Logging. Revisit react-hook-form + zod adoption then.
- No client-side cache means repeated tab-switching re-fetches. Acceptable at URSA-OSCAR data volumes (one user, ~1 year of nights).

**Revisit triggers:**
- Phase 2 acceptance criterion 5 ("CSV export of any night produces a file with the same nightly_summary fields as OSCAR's CSV export") — if the chart rendering shows perceptible re-fetch lag, add a small `useEffect` + `useState` cache or pull in `swr`/`react-query`.
- Phase 3 Manual Logging — if native form handling proves brittle for the spreadsheet view, add `react-hook-form` + `zod`.

## Implementation note

For Phase 2 scaffolding, copy `apex-system/web/src/index.css` into `frontend/src/index.css` verbatim. Add URSA-OSCAR-specific tokens at the bottom (don't overwrite APEX's — keep them aligned for upstream-able fixes):

```css
/* URSA-OSCAR additions */
--event-oa:    #dc2626;   /* obstructive apnea */
--event-ca:    #7c3aed;   /* central apnea */
--event-h:     #d97706;   /* hypopnea */
--event-rera:  #2563eb;   /* respiratory effort arousal */
--event-leak:  #ea580c;   /* large leak */
--chart-axis:  #d1d5db;
--chart-grid:  #f3f4f6;
```

## References

- APEX `web/src/index.css` — token source-of-truth
- APEX `docs/14-current-architecture-and-filelist.md` §8 (Frontend stack), §12 (deferred rebuild)
- URSA-OSCAR `Docs/URSA-OSCAR_Design.md` Decision 7
- URSA-OSCAR `Docs/architect-decisions/phase-0-apex-findings.md` §1, §8
- URSA-OSCAR `Docs/architect-decisions/phase-0-synthesis.md` Conflict 1
