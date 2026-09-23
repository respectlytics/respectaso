"""The per-keyword scoring pipeline, written once.

``score_country`` scores one keyword in one storefront and stores NOTHING:
search the competitors (``limit=25``, the canonical scoring limit), calculate
difficulty, look up the tracked app's rank, resolve popularity from both
sources, attach the download estimates. It is the unit of work of the Country
Opportunity Finder, which deliberately keeps its results out of the search
history until the user saves them.

``score_keyword_pair`` is ``score_country`` plus today's ``SearchResult``:
what a keyword search, a single-keyword Refresh and the daily ranking refresh
all run. It used to exist three times (``search_view``,
``keyword_refresh_view`` and ``scheduler._refresh_pair``), and its scoring
half used to exist twice more inside the opportunity views and once again in
the MCP scan; no-duplicate-logic.instructions.md lists this module as the
single source. Splitting it is what guarantees that a keyword search and a
country scan score a keyword identically.

``result_payload`` builds the dict the dashboard's result cards render from
a stored row, so a search that finished hours ago (or in a previous app
session) shows exactly what a live one did.
"""

from __future__ import annotations

from .models import SearchResult
from .popularity import popularity_fields, resolve_popularity
from .scoring import calc_opportunity, targeting_payload
from .services import SearchAPIUnavailableError


def score_country(kw_text, country, *, app=None, itunes_service,
                  difficulty_calc, download_est) -> dict:
    """Score one keyword in one storefront WITHOUT persisting anything.

    Raises ``SearchAPIUnavailableError`` and ``ITunesRateLimited`` to the
    caller, which decides what a failure means (a view answers 503, a
    background job records the country as not checked and carries on).

    Returns a dict with ``country``, ``competitors``, ``difficulty_score``,
    ``difficulty_breakdown``, ``popularity`` (the resolution object),
    ``app_rank`` and ``app_profile``.
    """
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

    # The app's own profile in this storefront (ratings, stars, age),
    # refreshed at most daily. Every score for this app here reads the same
    # cached value.
    app_profile = None
    if app is not None:
        from .app_profiles import profile_in_storefront

        app_profile = profile_in_storefront(app, country, itunes_service=itunes_service)

    return {
        "country": country,
        "competitors": competitors,
        "difficulty_score": difficulty_score,
        "difficulty_breakdown": breakdown,
        "popularity": pop,
        "app_rank": app_rank,
        "app_profile": app_profile,
    }


def score_keyword_pair(keyword_obj, country, *, app=None, itunes_service,
                       difficulty_calc, download_est) -> SearchResult:
    """Score one keyword in one country and store today's result.

    ``score_country`` does the work, this adds the row. ``app`` defaults to
    the keyword's own app.
    """
    if app is None:
        app = keyword_obj.app

    scored = score_country(
        keyword_obj.keyword, country, app=app, itunes_service=itunes_service,
        difficulty_calc=difficulty_calc, download_est=download_est,
    )
    pop = scored["popularity"]

    return SearchResult.upsert_today(
        keyword=keyword_obj,
        popularity_score=pop.internal,
        inferred_genre=pop.genre_hint,
        apple_popularity_score=pop.apple,
        difficulty_score=scored["difficulty_score"],
        difficulty_breakdown=scored["difficulty_breakdown"],
        competitors_data=scored["competitors"],
        app_rank=scored["app_rank"],
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
        "opportunity_score": calc_opportunity(
            popularity or 0, search_result.difficulty_score, search_result.country,
            app=search_result.app_profile, app_rank=search_result.app_rank, keyword=search_result.keyword_text,
        ),
        "difficulty_label": search_result.difficulty_label,
        "difficulty_color": search_result.difficulty_color,
        "difficulty_breakdown": search_result.difficulty_breakdown,
        "competitors": search_result.competitors_data,
        "result_id": search_result.id,
        "app_rank": search_result.app_rank,
        "app_name": app.name if app else None,
        "app_icon": app.icon_url if app else None,
        "classification": search_result.classification,
        # Icon, wording, colours and the one line of arithmetic behind the
        # score, composed on the server so no renderer carries a copy.
        "targeting": targeting_payload(
            popularity, search_result.difficulty_score, search_result.country,
            app=search_result.app_profile, app_rank=search_result.app_rank, keyword=search_result.keyword_text,
        ),
    }
