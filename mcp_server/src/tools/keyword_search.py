"""
Keyword search tool — full ASO analysis for a keyword.
"""

from mcp_server.src.app import mcp
from mcp_server.src.django_bootstrap import bootstrap_django

@mcp.tool()
def search_keyword(
    keyword: str,
    country: str = "us",
    limit: int = 25,
    save: bool = False,
    app_id: int | None = None,
) -> dict:
    bootstrap_django()  # idempotent

    from aso.forms import COUNTRY_CHOICES
    from aso.models import App, Keyword, SearchResult
    from aso.scoring import calc_opportunity, classify_keyword
    from aso.services import (
        ITunesSearchService,
        DifficultyCalculator,
        PopularityEstimator,
        DownloadEstimator,
    )

    kw = (keyword or "").strip().lower()
    if not kw:
        return {"error": "keyword is required"}

    valid_countries = {code for code, _ in COUNTRY_CHOICES}
    cc = (country or "us").lower()
    if cc not in valid_countries:
        return {"error": f"invalid country: {country}"}

    lim = max(1, min(int(limit), 25))

    itunes = ITunesSearchService()
    diff_calc = DifficultyCalculator()
    pop_est = PopularityEstimator()
    dl_est = DownloadEstimator()

    competitors = itunes.search_apps(kw, country=cc, limit=lim)
    difficulty_score, breakdown = diff_calc.calculate(competitors, keyword=kw)
    popularity_score = pop_est.estimate(competitors, kw)
    breakdown["download_estimates"] = dl_est.estimate(popularity_score or 0, country=cc)

    opportunity_score = calc_opportunity(popularity_score or 0, difficulty_score)
    classification = classify_keyword(popularity_score or 0, difficulty_score)

    saved_result_id = None
    if save:
        app = App.objects.filter(id=app_id).first() if app_id else None
        keyword_obj, _ = Keyword.objects.get_or_create(keyword=kw, app=app)
        row = SearchResult.upsert_today(
            keyword=keyword_obj,
            country=cc,
            popularity_score=popularity_score,
            difficulty_score=difficulty_score,
            difficulty_breakdown=breakdown,
            competitors_data=competitors,
            app_rank=None,
        )
        saved_result_id = row.id

    return {
        "keyword": kw,
        "country": cc,
        "popularity_score": popularity_score,
        "difficulty_score": difficulty_score,
        "opportunity_score": opportunity_score,
        "classification": classification,
        "difficulty_breakdown": breakdown,
        "competitors": competitors,
        "saved_result_id": saved_result_id,
    }