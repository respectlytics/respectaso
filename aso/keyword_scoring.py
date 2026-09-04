"""The per-keyword scoring pipeline, written once.

``score_keyword_pair`` is what a keyword search, a single-keyword Refresh and
the daily ranking refresh all run for one keyword in one country: search the
competitors (``limit=25``, the canonical scoring limit), calculate
difficulty, look up the tracked app's rank, resolve popularity from both
sources, attach the download estimates, and store today's ``SearchResult``.
It used to exist three times (``search_view``, ``keyword_refresh_view`` and
``scheduler._refresh_pair``); no-duplicate-logic.instructions.md lists this
module as the single source.

``result_payload`` builds the dict the dashboard's result cards render from
a stored row, so a search that finished hours ago (or in a previous app
session) shows exactly what a live one did.
"""

from __future__ import annotations

from .models import SearchResult
from .popularity import popularity_fields, resolve_popularity
from .scoring import calc_opportunity
from .services import SearchAPIUnavailableError


def score_keyword_pair(keyword_obj, country, *, app=None, itunes_service,
                       difficulty_calc, download_est) -> SearchResult:
    """Score one keyword in one country and store today's result.

    Raises ``SearchAPIUnavailableError`` and ``ITunesRateLimited`` to the
    caller, which decides what a failure means (a view answers 503, a
    background job records the keyword as not checked and carries on).
    ``app`` defaults to the keyword's own app.
    """
    if app is None:
        app = keyword_obj.app
    kw_text = keyword_obj.keyword

    competitors = itunes_service.search_apps(kw_text, country=country, limit=25)

    difficulty_score, breakdown = difficulty_calc.calculate(competitors, keyword=kw_text)

    # The tracked app's rank is optional: a failed rank lookup never costs
    # the user the keyword.
    app_rank = None
    if app and app.track_id:
        try:
            app_rank = itunes_service.find_app_rank(kw_text, app.track_id, country=country)
        except SearchAPIUnavailableError:
            pass

    # Popularity from both sources; ``pop.effective`` feeds all math.
    pop = resolve_popularity(competitors, kw_text, country)
    breakdown["download_estimates"] = download_est.estimate(pop.effective or 0, country=country)

    return SearchResult.upsert_today(
        keyword=keyword_obj,
        popularity_score=pop.internal,
        inferred_genre=pop.genre_hint,
        apple_popularity_score=pop.apple,
        difficulty_score=difficulty_score,
        difficulty_breakdown=breakdown,
        competitors_data=competitors,
        app_rank=app_rank,
        country=country,
    )


def result_payload(search_result, app=None) -> dict:
    """The dict one dashboard result card renders, from a stored row.

    Field for field what the old blocking search answered with, so
    ``createResultCard`` / ``renderResultTabs`` / ``renderOpportunityRanking``
    and ``static/js/popularity-display.js`` need no change. ``app`` defaults
    to the keyword's own app.
    """
    if app is None:
        app = search_result.keyword.app
    pop = search_result.popularity_resolution()
    popularity = pop.effective
    return {
        "keyword": search_result.keyword.keyword,
        "country": search_result.country,
        "popularity_score": popularity,
        **popularity_fields(pop),
        "difficulty_score": search_result.difficulty_score,
        "opportunity_score": calc_opportunity(popularity or 0, search_result.difficulty_score),
        "difficulty_label": search_result.difficulty_label,
        "difficulty_color": search_result.difficulty_color,
        "difficulty_breakdown": search_result.difficulty_breakdown,
        "competitors": search_result.competitors_data,
        "result_id": search_result.id,
        "app_rank": search_result.app_rank,
        "app_name": app.name if app else None,
        "app_icon": app.icon_url if app else None,
        "classification": search_result.classification,
    }
