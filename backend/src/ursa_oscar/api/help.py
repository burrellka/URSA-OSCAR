"""Help topic API endpoints — Phase 7.3.

Two endpoints power the in-app Help system + the get_help_topic MCP
tool:

  GET /api/v1/help/topics
       Returns the registry listing (slug, title, section, keywords)
       without bodies. Cheap directory.

  GET /api/v1/help/topics/{slug}
       Returns one topic with its body.

  GET /api/v1/help/search?q=<query>
       Substring search across title + keywords + body. Returns
       topics in the three-tier ranking.

These endpoints are authenticated (require_auth) like the rest of the
API — the help content is operator-facing but not public. The MCP
container reads them via its service token like every other tool.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..help import HelpTopic, list_topics, search_topics, topic_by_slug

router = APIRouter(prefix="/api/v1/help", tags=["help"])


class HelpTopicSummary(BaseModel):
    slug: str
    title: str
    section: str
    keywords: list[str]


class HelpTopicFull(HelpTopicSummary):
    body: str


def _to_full(t: HelpTopic) -> HelpTopicFull:
    return HelpTopicFull(
        slug=t.slug,
        title=t.title,
        section=t.section,
        keywords=t.keywords,
        body=t.body,
    )


@router.get("/topics", response_model=list[HelpTopicSummary])
def list_help_topics() -> list[dict]:
    """List every Help topic (summary shape, no body). Cheap to call
    for directory rendering."""
    return list_topics(include_body=False)


@router.get("/topics/{slug}", response_model=HelpTopicFull)
def get_help_topic(slug: str) -> HelpTopicFull:
    """Get one topic with its markdown body."""
    t = topic_by_slug(slug)
    if t is None:
        raise HTTPException(
            status_code=404,
            detail=f"Help topic '{slug}' not found",
        )
    return _to_full(t)


@router.get("/search", response_model=list[HelpTopicFull])
def search_help_topics(
    q: str = Query(..., min_length=1, description="Substring to search for"),
) -> list[HelpTopicFull]:
    """Search across title, keywords, body. Returns ranked results
    (title matches first, then keyword matches, then body matches).
    Empty list when nothing matches."""
    return [_to_full(t) for t in search_topics(q)]
