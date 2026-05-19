"""Help-system backend surface — Phase 7.3.

The markdown content lives in the frontend repo at
``frontend/src/help/content/*.md``. At image-build time the
backend Dockerfile copies that directory into ``/app/help/content/``
inside the api container. This module reads from there at
import time.

The registry's METADATA (slug, title, section, keywords) is
duplicated between this module and ``frontend/src/help/topics.ts``.
The no-drift regression test in
``tests/integration/test_help_no_drift.py`` verifies the two
registries stay in sync — both have the same 37 entries with
matching slugs, titles, and sections.

The body is single-source-of-truth: this module reads the .md
files at import time so the backend and the frontend both render
the same prose without manual maintenance.
"""
from .registry import HelpTopic, list_topics, topic_by_slug, search_topics

__all__ = ["HelpTopic", "list_topics", "topic_by_slug", "search_topics"]
