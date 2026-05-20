"""Generate launch-day visual assets for the README + Phase 8 announcement.

Two kinds of output:
  1. A real, polished SVG architecture diagram showing the four-container
     topology + data flow. GitHub renders SVG inline at high quality.
  2. Placeholder PNGs for the UI captures Kevin should take from his
     live stack with his real data. These render in the README so the
     layout doesn't break before Kevin substitutes the real captures.

Run from anywhere:
    python docs/screenshots/generate_assets.py

Outputs land in docs/screenshots/ alongside this script.
"""
from __future__ import annotations

from pathlib import Path

OUTPUT_DIR = Path(__file__).resolve().parent


# ---------------------------------------------------------------------------
# Architecture diagram (real, ship-able)
# ---------------------------------------------------------------------------

ARCHITECTURE_SVG = """<?xml version="1.0" encoding="UTF-8"?>
<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1100 680"
     font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Inter, sans-serif">

  <defs>
    <linearGradient id="apiGradient" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#2563eb"/>
      <stop offset="100%" stop-color="#1d4ed8"/>
    </linearGradient>
    <linearGradient id="serviceGradient" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#475569"/>
      <stop offset="100%" stop-color="#334155"/>
    </linearGradient>
    <linearGradient id="dataGradient" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#16a34a"/>
      <stop offset="100%" stop-color="#15803d"/>
    </linearGradient>
    <linearGradient id="externalGradient" x1="0%" y1="0%" x2="0%" y2="100%">
      <stop offset="0%" stop-color="#f1f5f9"/>
      <stop offset="100%" stop-color="#e2e8f0"/>
    </linearGradient>

    <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5"
            markerUnits="strokeWidth" markerWidth="7" markerHeight="7"
            orient="auto">
      <path d="M 0 0 L 10 5 L 0 10 z" fill="#64748b"/>
    </marker>
  </defs>

  <!-- Background -->
  <rect width="1100" height="680" fill="#f8fafc"/>

  <!-- Title -->
  <text x="550" y="40" text-anchor="middle"
        font-size="22" font-weight="700" fill="#0f172a">
    URSA-OSCAR architecture — 4 containers, one /data volume
  </text>
  <text x="550" y="62" text-anchor="middle"
        font-size="13" fill="#64748b">
    Single-tenant, self-hosted. API container is the sole writer of DuckDB (ADR-004).
  </text>

  <!-- kairos-net dashed boundary -->
  <rect x="60" y="100" width="980" height="380" rx="14"
        fill="none" stroke="#94a3b8" stroke-width="2"
        stroke-dasharray="6 4"/>
  <text x="78" y="124" font-size="12" font-weight="600" fill="#64748b"
        letter-spacing="0.04em">KAIROS-NET (DOCKER NETWORK)</text>

  <!-- ursa-oscar-api (center, large) -->
  <g>
    <rect x="380" y="160" width="340" height="180" rx="12"
          fill="url(#apiGradient)" stroke="#1e3a8a" stroke-width="1"/>
    <text x="550" y="195" text-anchor="middle"
          font-size="18" font-weight="700" fill="#ffffff">
      ursa-oscar-api
    </text>
    <text x="550" y="215" text-anchor="middle"
          font-size="11" fill="#dbeafe" font-style="italic">
      FastAPI · DuckDB · WeasyPrint · AI proxy · auth
    </text>

    <!-- DuckDB box inside api -->
    <rect x="430" y="240" width="240" height="80" rx="8"
          fill="url(#dataGradient)" stroke="#14532d"/>
    <text x="550" y="265" text-anchor="middle"
          font-size="13" font-weight="600" fill="#ffffff">
      /data
    </text>
    <text x="550" y="285" text-anchor="middle"
          font-size="10" fill="#dcfce7">
      ursa-oscar.duckdb · master.key · jwt_secret
    </text>
    <text x="550" y="300" text-anchor="middle"
          font-size="10" fill="#dcfce7">
      auth.json · service_tokens/ · secrets.enc · profile.json
    </text>
    <text x="550" y="315" text-anchor="middle"
          font-size="10" fill="#bbf7d0" font-style="italic">
      sole writer
    </text>
  </g>

  <!-- ursa-oscar-mcp (left) -->
  <g>
    <rect x="100" y="200" width="200" height="110" rx="12"
          fill="url(#serviceGradient)" stroke="#1e293b"/>
    <text x="200" y="232" text-anchor="middle"
          font-size="15" font-weight="700" fill="#ffffff">
      ursa-oscar-mcp
    </text>
    <text x="200" y="252" text-anchor="middle"
          font-size="10" fill="#cbd5e1">FastMCP · SSE · OAuth 2.1</text>
    <text x="200" y="268" text-anchor="middle"
          font-size="10" fill="#cbd5e1">17 analytical tools</text>
    <text x="200" y="290" text-anchor="middle"
          font-size="10" fill="#94a3b8" font-style="italic">
      thin proxy to api
    </text>
  </g>

  <!-- ursa-oscar-web (right) -->
  <g>
    <rect x="800" y="200" width="200" height="110" rx="12"
          fill="url(#serviceGradient)" stroke="#1e293b"/>
    <text x="900" y="232" text-anchor="middle"
          font-size="15" font-weight="700" fill="#ffffff">
      ursa-oscar-web
    </text>
    <text x="900" y="252" text-anchor="middle"
          font-size="10" fill="#cbd5e1">nginx · React 18 · uPlot</text>
    <text x="900" y="268" text-anchor="middle"
          font-size="10" fill="#cbd5e1">/api/* → api proxy</text>
    <text x="900" y="290" text-anchor="middle"
          font-size="10" fill="#94a3b8" font-style="italic">
      static + reverse proxy
    </text>
  </g>

  <!-- ursa-oscar-watcher (bottom) -->
  <g>
    <rect x="430" y="380" width="240" height="90" rx="12"
          fill="url(#serviceGradient)" stroke="#1e293b"/>
    <text x="550" y="410" text-anchor="middle"
          font-size="15" font-weight="700" fill="#ffffff">
      ursa-oscar-watcher
    </text>
    <text x="550" y="430" text-anchor="middle"
          font-size="10" fill="#cbd5e1">
      poll · quiescence · trigger-import
    </text>
    <text x="550" y="448" text-anchor="middle"
          font-size="10" fill="#94a3b8" font-style="italic">
      auto-ingest daemon
    </text>
  </g>

  <!-- Connections inside kairos-net -->
  <!-- MCP → API -->
  <line x1="300" y1="255" x2="380" y2="240" stroke="#64748b"
        stroke-width="2" marker-end="url(#arrow)"/>
  <text x="340" y="240" text-anchor="middle" font-size="9"
        fill="#64748b">HTTP</text>

  <!-- WEB → API -->
  <line x1="800" y1="255" x2="720" y2="240" stroke="#64748b"
        stroke-width="2" marker-end="url(#arrow)"/>
  <text x="760" y="240" text-anchor="middle" font-size="9"
        fill="#64748b">HTTP</text>

  <!-- WATCHER → API -->
  <line x1="550" y1="380" x2="550" y2="340" stroke="#64748b"
        stroke-width="2" marker-end="url(#arrow)"/>
  <text x="566" y="365" font-size="9" fill="#64748b">HTTP</text>

  <!-- External clients (below kairos-net) -->
  <g>
    <rect x="80" y="520" width="220" height="110" rx="12"
          fill="url(#externalGradient)" stroke="#94a3b8"/>
    <text x="190" y="552" text-anchor="middle"
          font-size="15" font-weight="700" fill="#0f172a">
      claude.ai connector
    </text>
    <text x="190" y="570" text-anchor="middle"
          font-size="10" fill="#475569">OAuth + PKCE</text>
    <text x="190" y="586" text-anchor="middle"
          font-size="10" fill="#475569">via Cloudflare tunnel</text>
    <text x="190" y="610" text-anchor="middle"
          font-size="10" fill="#64748b" font-style="italic">
      external SSE client
    </text>
  </g>

  <g>
    <rect x="440" y="520" width="220" height="110" rx="12"
          fill="url(#externalGradient)" stroke="#94a3b8"/>
    <text x="550" y="552" text-anchor="middle"
          font-size="15" font-weight="700" fill="#0f172a">
      Browser
    </text>
    <text x="550" y="570" text-anchor="middle"
          font-size="10" fill="#475569">Daily View · Trends · Help</text>
    <text x="550" y="586" text-anchor="middle"
          font-size="10" fill="#475569">in-app AI chat</text>
    <text x="550" y="610" text-anchor="middle"
          font-size="10" fill="#64748b" font-style="italic">
      operator
    </text>
  </g>

  <g>
    <rect x="800" y="520" width="220" height="110" rx="12"
          fill="url(#externalGradient)" stroke="#94a3b8"/>
    <text x="910" y="552" text-anchor="middle"
          font-size="15" font-weight="700" fill="#0f172a">
      SD card / bind-mount
    </text>
    <text x="910" y="570" text-anchor="middle"
          font-size="10" fill="#475569">DATALOG/YYYYMMDD/*.edf</text>
    <text x="910" y="586" text-anchor="middle"
          font-size="10" fill="#475569">ResMed AirSense 10/11</text>
    <text x="910" y="610" text-anchor="middle"
          font-size="10" fill="#64748b" font-style="italic">
      filesystem source
    </text>
  </g>

  <!-- External → service connections -->
  <line x1="190" y1="520" x2="190" y2="313" stroke="#64748b"
        stroke-width="2" marker-end="url(#arrow)"/>
  <text x="170" y="450" font-size="9" fill="#64748b" transform="rotate(-90 170 450)">HTTPS / SSE</text>

  <line x1="550" y1="520" x2="900" y2="313" stroke="#64748b"
        stroke-width="2" marker-end="url(#arrow)"/>

  <line x1="910" y1="520" x2="670" y2="425" stroke="#64748b"
        stroke-width="2" stroke-dasharray="4 3" marker-end="url(#arrow)"/>
  <text x="800" y="475" font-size="9" fill="#64748b">file system</text>

</svg>
"""


def write_architecture_diagram() -> None:
    out = OUTPUT_DIR / "architecture.svg"
    out.write_text(ARCHITECTURE_SVG, encoding="utf-8")
    print(f"wrote {out}")


# ---------------------------------------------------------------------------
# Placeholder PNGs for the UI captures Kevin should fill in
# ---------------------------------------------------------------------------

# What we want: each placeholder renders cleanly in the README so the
# layout doesn't break, but makes it visually obvious to a reader that
# this is a "real screenshot goes here" slot — not finished art. The
# PNG generator uses Pillow because it's already a backend dep.

PLACEHOLDER_VIEWS = [
    ("daily-view.png", "Daily View", "Per-night detail · EventRug · time-series charts"),
    ("trends.png", "Trends page", "Single-metric trend · correlation · lag · prediction"),
    ("ai-chat.png", "AI assistant chat panel", "Conversational query · tool calls visible · confidence levels surfaced"),
    ("reports.png", "Reports page", "PDF template selection · preview metadata"),
    ("pdf-report-cover.png", "Sample PDF — page 1", "Cover · headline numbers · date range"),
    ("pdf-report-methodology.png", "Sample PDF — methodology", "Every statistical method · plain-language description · limitations"),
    ("settings-ai.png", "Settings → AI Assistant", "Provider preset · API key · system-prompt template"),
    ("help-system.png", "Help system", "37 topics · 7 sections · markdown rendering · search"),
]


def write_placeholders() -> None:
    """Render PNG placeholders for each UI view Kevin will capture later."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        print("Pillow not installed; skipping PNG placeholders.")
        print("    pip install Pillow")
        return

    width, height = 1440, 900

    # Try to find a TrueType font; fall back to default if unavailable.
    def _font(size: int) -> ImageFont.ImageFont:
        for path in (
            "C:/Windows/Fonts/segoeui.ttf",
            "C:/Windows/Fonts/arial.ttf",
            "/System/Library/Fonts/SFNS.ttf",
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        ):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        return ImageFont.load_default()

    title_font = _font(48)
    subtitle_font = _font(22)
    badge_font = _font(20)
    footer_font = _font(18)

    for filename, label, sublabel in PLACEHOLDER_VIEWS:
        img = Image.new("RGB", (width, height), color=(247, 248, 250))
        draw = ImageDraw.Draw(img)

        # Faux sidebar
        draw.rectangle((0, 0, 240, height), fill=(255, 255, 255))
        draw.line((240, 0, 240, height), fill=(227, 230, 235), width=2)
        draw.text(
            (24, 32), "URSA-OSCAR",
            fill=(26, 29, 35), font=_font(20),
        )
        draw.text(
            (24, 58), "Unified Rest & Somatic Analytics",
            fill=(154, 160, 166), font=_font(11),
        )

        # Sidebar nav items (decorative)
        nav_items = [
            "Overview", "Daily View", "Statistics", "Events",
            "Import", "Export", "Trends", "Reports",
            "Manual Logs", "Profile", "Settings", "Help",
        ]
        for i, item in enumerate(nav_items):
            y = 110 + i * 36
            color = (37, 99, 235) if item.lower() in label.lower() else (107, 114, 128)
            draw.text((24, y), item, fill=color, font=_font(14))

        # Main content area: big "screenshot placeholder" message
        draw.text(
            (290, 80), label,
            fill=(26, 29, 35), font=title_font,
        )
        draw.text(
            (290, 145), sublabel,
            fill=(107, 114, 128), font=subtitle_font,
        )

        # Centered placeholder card
        card_x0, card_y0 = 290, 220
        card_x1, card_y1 = width - 60, height - 80
        draw.rounded_rectangle(
            (card_x0, card_y0, card_x1, card_y1),
            radius=14,
            fill=(255, 255, 255),
            outline=(227, 230, 235),
            width=2,
        )

        # "Placeholder" badge
        badge_x, badge_y = card_x0 + 32, card_y0 + 32
        draw.rounded_rectangle(
            (badge_x, badge_y, badge_x + 220, badge_y + 36),
            radius=8,
            fill=(254, 243, 199),
            outline=(217, 119, 6, 100),
        )
        draw.text(
            (badge_x + 14, badge_y + 6), "PLACEHOLDER",
            fill=(146, 64, 14), font=badge_font,
        )

        # Center text
        center_y = (card_y0 + card_y1) // 2
        draw.text(
            (card_x0 + 60, center_y - 60),
            "Real screenshot from operator's live stack",
            fill=(26, 29, 35), font=_font(28),
        )
        draw.text(
            (card_x0 + 60, center_y - 20),
            "goes here.",
            fill=(26, 29, 35), font=_font(28),
        )
        draw.text(
            (card_x0 + 60, center_y + 30),
            "Replace docs/screenshots/" + filename
            + " with a 1440×900 capture before launch.",
            fill=(107, 114, 128), font=footer_font,
        )

        out = OUTPUT_DIR / filename
        img.save(out, "PNG", optimize=True)
        print(f"wrote {out}")


def main() -> None:
    write_architecture_diagram()
    write_placeholders()


if __name__ == "__main__":
    main()
