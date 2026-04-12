"""
Opportunity search tool — scan a keyword across all 30 App Store countries
and rank them by opportunity score.

Based on opportunity_search_view in aso/views.py.
"""

import logging
import time

from mcp_server.src import mcp
from mcp_server.src.django_bootstrap import bootstrap_django

logger = logging.getLogger(__name__)


@mcp.tool()
def opportunity_search(keyword: str) -> dict:
    """
    Scan a keyword across all 30 App Store countries and rank by opportunity.

    For each country, fetches live iTunes data and computes popularity (5–100),
    difficulty (1–100), and opportunity score (0–100). Returns a compact ranked
    list so you can identify which markets offer the best ranking opportunity.

    NOTE: This tool makes 30 sequential iTunes API calls with a 2-second rate
    limit between each. Expect ~60 seconds total execution time.

    To drill into a specific country's full competitor list and download
    estimates, use the search_keyword tool instead.

    Args:
        keyword: The App Store keyword to evaluate (e.g. 'habit tracker').

    Returns:
        Dict with keyword, total_countries, and results (list of 30 entries
        sorted by opportunity desc). Each entry contains: country, popularity,
        difficulty, difficulty_label, opportunity, classification,
        competitor_count, top_competitor, top_ratings.
    """
    bootstrap_django()

    from aso.forms import COUNTRY_CHOICES
    from aso.scoring import calc_opportunity, classify_keyword
    from aso.services import (
        DifficultyCalculator,
        ITunesSearchService,
        PopularityEstimator,
    )

    kw = (keyword or "").strip().lower()
    if not kw:
        return {"error": "keyword is required"}

    itunes = ITunesSearchService()
    diff_calc = DifficultyCalculator()
    pop_est = PopularityEstimator()

    results = []
    for i, (country_code, _country_name) in enumerate(COUNTRY_CHOICES):
        # Respect Apple rate limits — same 2s sleep used by views.py and scheduler.py
        if i > 0:
            time.sleep(2)

        competitors = itunes.search_apps(kw, country=country_code, limit=25)
        difficulty_score, _breakdown = diff_calc.calculate(competitors, keyword=kw)
        popularity = pop_est.estimate(competitors, kw)

        opportunity = calc_opportunity(popularity or 0, difficulty_score)
        classification = classify_keyword(popularity or 0, difficulty_score)

        # Difficulty label — same thresholds as SearchResult.difficulty_label property
        if difficulty_score <= 15:
            diff_label = "Very Easy"
        elif difficulty_score <= 35:
            diff_label = "Easy"
        elif difficulty_score <= 55:
            diff_label = "Moderate"
        elif difficulty_score <= 75:
            diff_label = "Hard"
        elif difficulty_score <= 90:
            diff_label = "Very Hard"
        else:
            diff_label = "Extreme"

        top_competitor = competitors[0]["trackName"] if competitors else "—"
        top_ratings = competitors[0].get("userRatingCount", 0) if competitors else 0

        # We intentionally omit three fields that the Django view returns:
        #
        #   - difficulty_breakdown: deeply nested dict (7 sub-scores + full
        #     download table for positions 1-20). Across 30 countries this
        #     creates massive token bloat with low decision value.
        #
        #   - competitors_data: ~25 app dicts per country × 30 = ~750 objects.
        #     Impractical for LLM context. Use search_keyword to drill into
        #     a specific country instead.
        #
        #   - app_rank: requires an app_id + track_id plus an extra iTunes API
        #     call per country, which would double latency (~120s total).
        #     Use search_keyword with app_id for rank tracking on individual
        #     countries.
        results.append({
            "country": country_code,
            "popularity": popularity,
            "difficulty": difficulty_score,
            "difficulty_label": diff_label,
            "opportunity": opportunity,
            "classification": classification,
            "competitor_count": len(competitors),
            "top_competitor": top_competitor,
            "top_ratings": top_ratings,
        })

    results.sort(key=lambda x: x["opportunity"], reverse=True)

    return {
        "keyword": kw,
        "total_countries": len(results),
        "results": results,
    }
