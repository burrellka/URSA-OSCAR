"""Packaged community defaults shipped inside the wheel.

Phase 3 — per-instance config lives on the mounted volume at
``/data/profile.json`` and ``/data/vocab.json``. The API container's
first-start path copies these defaults out of the package and into
``/data/`` if those files don't already exist, then logs an init message.

Defaults are intentionally empty/generic — no personal data ships in
the public repo. Operators populate via the Settings → Profile UI.
"""
