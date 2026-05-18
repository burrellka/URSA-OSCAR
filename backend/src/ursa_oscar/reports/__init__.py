"""Phase 6 Ticket 6.3 — provider PDF reports.

Module layout:
  generator.py            — pipeline orchestrator (collect → render → PDF)
  data_collectors.py      — per-section data fetchers
  methodology_registry.py — human descriptions for every analytical method
  templates/              — Jinja2 templates (full_clinical, summary, analytical)
  static/                 — assets (logo, etc.)

Per Decision 6.3-A: PDF content is server-side rendered (Jinja2 +
WeasyPrint), NOT LLM-generated. Same data the AI assistant sees; the
PDF stays authoritative.

Per Decision 6.3-D: methodology section is non-optional in every PDF.
Every analytical method whose output appears in the report must be
registered in methodology_registry.py — generation fails loudly if not.

Per Decision 6.3-E: sample-size refusals propagate to the PDF as
explicit "insufficient data" sections, not omitted. The template
fragment is the same language the AI assistant uses for INSUFFICIENT_DATA.
"""
