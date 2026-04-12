"""
Saved keywords resource — read-only access to the user's stored keyword history.

Registered as both an MCP resource (for native clients) and auto-exposed
as a tool via ResourcesAsTools (for tool-only clients like most LLM
integrations).
"""

import asyncio
import json
import logging

from mcp_server.src import mcp
from mcp_server.src.django_bootstrap import bootstrap_django

logger = logging.getLogger(__name__)


@mcp.resource(
    "respectaso://keywords/saved{?app_id,country}",
    name="Saved Keywords",
    description=(
        "Read the user's saved keyword search history from the local database. "
        "Returns the latest search result per keyword+country pair — the same "
        "data shown on the RespectASO Dashboard. Optionally filter by app_id "
        "or country code."
    ),
    mime_type="application/json",
)
async def get_saved_keywords(app_id: str = "", country: str = "") -> str:
    """
    Fetch the latest SearchResult per keyword+country pair from the local DB.

    This is a read-only query — it never writes to the database.

    Args:
        app_id: Optional app ID to filter keywords linked to a specific app.
        country: Optional two-letter country code to filter results.

    Returns:
        JSON string with a list of keyword result dicts, sorted by most
        recently searched first. Each entry contains: keyword, country,
        app_name, popularity_score, difficulty_score, difficulty_label,
        opportunity_score, classification, app_rank, competitor_count,
        searched_at.
    """
    
    # FastMCP calls @mcp.resource() functions inside an async event loop,
    # but Django's ORM is synchronous and refuses to run in an async context.
    # We offload the entire DB query to a thread to avoid the conflict.
    return await asyncio.to_thread(_fetch_saved_keywords, app_id, country)


def _fetch_saved_keywords(app_id: str, country: str) -> str:
    """Synchronous helper — runs Django ORM queries in a worker thread."""
    bootstrap_django()

    from django.db.models import Max

    from aso.models import SearchResult

    # Build the same "latest result per keyword+country" query used by
    # dashboard_view in aso/views.py (lines 99-134).
    filters = {}
    if app_id:
        filters["keyword__app_id"] = int(app_id)
    if country:
        filters["country"] = country.strip().lower()

    latest_ids = (
        SearchResult.objects
        .filter(**filters)
        .values("keyword_id", "country")
        .annotate(latest_id=Max("id"))
        .values_list("latest_id", flat=True)
    )

    results = (
        SearchResult.objects
        .filter(id__in=list(latest_ids))
        .select_related("keyword", "keyword__app")
        .order_by("-searched_at")
    )

    # Serialize to a compact list — we intentionally omit:
    #
    #   - difficulty_breakdown: deeply nested dict (7 sub-scores + download
    #     estimates table). Multiplied by N keywords this creates massive
    #     token bloat. Use search_keyword to drill into a specific keyword.
    #
    #   - competitors_data: ~25 app dicts per keyword — thousands of tokens.
    #     Use search_keyword to get the full competitor list for one keyword.
    keywords = []
    for result in results:
        keywords.append({
            "keyword": result.keyword.keyword,
            "country": result.country,
            "app_name": result.keyword.app.name if result.keyword.app else None,
            "popularity_score": result.popularity_score,
            "difficulty_score": result.difficulty_score,
            "difficulty_label": result.difficulty_label,
            "opportunity_score": result.opportunity_score,
            "classification": result.classification,
            "app_rank": result.app_rank,
            "competitor_count": len(result.competitors_data or []),
            "searched_at": result.searched_at.isoformat(),
        })

    return json.dumps({
        "total_keywords": len(keywords),
        "filters_applied": {
            "app_id": app_id or None,
            "country": country or None,
        },
        "keywords": keywords,
    })
