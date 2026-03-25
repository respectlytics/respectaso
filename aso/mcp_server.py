from __future__ import annotations

from functools import wraps

from asgiref.sync import sync_to_async
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from .forms import COUNTRY_CHOICES  # module-level — required for test patching
from .services import ITunesSearchService  # module-level — required for test patching

# stateless_http=True: each request is independent (no session negotiation required).
# Disable DNS rebinding protection — local-only tool on a trusted Docker network.
mcp = FastMCP(
    "RespectASO",
    stateless_http=True,
    transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
)


def tool(fn):
    """Register a sync function as an MCP tool, running it in a thread pool.

    FastMCP calls sync tools directly in the async event loop. Django ORM cannot
    be called from an async context without sync_to_async. This decorator registers
    an async wrapper in MCP's tool registry but returns the original sync function
    so tests can call it directly without async overhead.
    """
    @mcp.tool()
    @wraps(fn)
    async def wrapper(*args, **kwargs):
        return await sync_to_async(fn)(*args, **kwargs)
    return fn  # Tests and direct callers get the original sync function


def _difficulty_label(score: int) -> str:
    if score <= 15:
        return "Very Easy"
    if score <= 35:
        return "Easy"
    if score <= 55:
        return "Moderate"
    if score <= 75:
        return "Hard"
    if score <= 90:
        return "Very Hard"
    return "Extreme"


# ---------------------------------------------------------------------------
# Read Tools
# ---------------------------------------------------------------------------

@tool
def list_apps() -> list[dict]:
    """List all tracked iOS apps."""
    from .models import App
    return [
        {
            "id": a.id,
            "name": a.name,
            "bundle_id": a.bundle_id,
            "track_id": a.track_id,
            "store_url": a.store_url,
            "icon_url": a.icon_url,
            "seller_name": a.seller_name,
        }
        for a in App.objects.all()
    ]


@tool
def list_keywords(app_id: int | None = None) -> list[dict]:
    """
    List tracked keywords with their latest score per country.

    If app_id is provided, returns only keywords linked to that app.
    """
    from django.db.models import Max
    from .models import Keyword, SearchResult

    qs = list(Keyword.objects.select_related("app").all())
    if app_id is not None:
        qs = [kw for kw in qs if kw.app_id == app_id]

    if not qs:
        return []

    kw_ids = [kw.id for kw in qs]

    # Single batch query: latest result ID per (keyword, country) across all keywords
    latest_ids = list(
        SearchResult.objects.filter(keyword_id__in=kw_ids)
        .values("keyword_id", "country")
        .annotate(latest_id=Max("id"))
        .values_list("latest_id", flat=True)
    )

    # Fetch all latest results in one query, group by keyword_id
    from collections import defaultdict
    grouped: dict[int, list] = defaultdict(list)
    for r in SearchResult.objects.filter(id__in=latest_ids).order_by("keyword_id", "country"):
        grouped[r.keyword_id].append({
            "country": r.country,
            "popularity_score": r.popularity_score,
            "difficulty_score": r.difficulty_score,
            "difficulty_label": r.difficulty_label,
            "searched_at": r.searched_at.isoformat(),
        })

    return [
        {
            "id": kw.id,
            "keyword": kw.keyword,
            "app_id": kw.app_id,
            "app_name": kw.app.name if kw.app else None,
            "results": grouped[kw.id],
        }
        for kw in qs
    ]


@tool
def get_keyword_scores(keyword_id: int, country: str | None = None) -> dict:
    """
    Get the latest scores for a keyword.

    Returns popularity, difficulty, targeting advice, competitor data, and app rank.
    If country is omitted, returns the most recently searched country's result.
    """
    from .models import Keyword, SearchResult

    try:
        kw = Keyword.objects.get(id=keyword_id)
    except Keyword.DoesNotExist:
        return {"error": f"Keyword {keyword_id} not found"}

    qs = SearchResult.objects.filter(keyword=kw)
    if country:
        qs = qs.filter(country=country.lower())

    result = qs.order_by("-searched_at").first()
    if not result:
        return {"error": f"No results found for keyword {keyword_id}" + (f" in {country}" if country else "")}

    _, advice_label, _, advice_desc = result.targeting_advice
    return {
        "keyword": kw.keyword,
        "country": result.country,
        "popularity_score": result.popularity_score,
        "difficulty_score": result.difficulty_score,
        "difficulty_label": result.difficulty_label,
        "targeting_advice": {"label": advice_label, "description": advice_desc},
        "competitors_data": result.competitors_data,
        "app_rank": result.app_rank,
        "searched_at": result.searched_at.isoformat(),
    }


@tool
def get_keyword_trend(keyword_id: int, country: str | None = None) -> list[dict]:
    """
    Get historical trend data for a keyword.

    Returns a list of data points ordered by date. Optionally filter by country.
    """
    from .models import Keyword, SearchResult

    try:
        kw = Keyword.objects.get(id=keyword_id)
    except Keyword.DoesNotExist:
        return [{"error": f"Keyword {keyword_id} not found"}]

    qs = SearchResult.objects.filter(keyword=kw).order_by("searched_at")
    if country:
        qs = qs.filter(country=country.lower())

    return [
        {
            "date": r.searched_at.strftime("%Y-%m-%d"),
            "popularity": r.popularity_score,
            "difficulty": r.difficulty_score,
            "rank": r.app_rank,
            "country": r.country,
        }
        for r in qs
    ]


@tool
def get_search_history(
    app_id: int | None = None,
    country: str | None = None,
    page: int = 1,
) -> dict:
    """
    Get paginated search history (latest result per keyword+country).

    Returns 25 results per page.
    """
    from django.db.models import Max
    from .models import SearchResult

    filter_kwargs: dict = {}
    if app_id is not None:
        filter_kwargs["keyword__app_id"] = app_id
    if country:
        filter_kwargs["country"] = country.lower()

    latest_ids = (
        SearchResult.objects.filter(**filter_kwargs)
        .values("keyword_id", "country")
        .annotate(latest_id=Max("id"))
        .values_list("latest_id", flat=True)
    )

    qs = (
        SearchResult.objects.filter(id__in=list(latest_ids))
        .select_related("keyword", "keyword__app")
        .order_by("-searched_at")
    )

    per_page = 25
    total_count = qs.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = max(1, min(page, total_pages))
    offset = (page - 1) * per_page

    results = [
        {
            "keyword": r.keyword.keyword,
            "app_name": r.keyword.app.name if r.keyword.app else None,
            "country": r.country,
            "popularity_score": r.popularity_score,
            "difficulty_score": r.difficulty_score,
            "difficulty_label": r.difficulty_label,
            "app_rank": r.app_rank,
            "searched_at": r.searched_at.isoformat(),
        }
        for r in qs[offset : offset + per_page]
    ]

    return {
        "results": results,
        "page": page,
        "total_pages": total_pages,
        "total_count": total_count,
    }


# ---------------------------------------------------------------------------
# Search Tools
# ---------------------------------------------------------------------------

@tool
def search_keywords(
    keywords: str,
    countries: list[str] | None = None,
    app_id: int | None = None,
) -> dict:
    """
    Search 1–20 keywords across 1–5 countries (comma-separated keywords string).

    Triggers iTunes API calls. Rate-limited at 2s per call.
    Worst case: 20 keywords × 5 countries ≈ 3 minutes. Connection stays open.
    Returns results grouped by country plus an opportunity ranking for multi-country searches.
    """
    import time
    from django.utils import timezone
    from .models import App, Keyword, SearchResult
    from .services import DifficultyCalculator, PopularityEstimator, DownloadEstimator

    if countries is None:
        countries = ["us"]
    countries = countries[:5]

    kw_list = [k.strip() for k in keywords.split(",") if k.strip()][:20]
    if not kw_list:
        return {"error": "No keywords provided"}

    app = None
    if app_id:
        try:
            app = App.objects.get(id=app_id)
        except App.DoesNotExist:
            return {"error": f"App {app_id} not found"}

    itunes = ITunesSearchService()
    diff_calc = DifficultyCalculator()
    pop_est = PopularityEstimator()
    dl_est = DownloadEstimator()

    results_by_country: dict = {}
    skipped: list[str] = []
    call_count = 0

    for country in countries:
        country_results = []
        for kw_text in kw_list:
            kw_obj, created = Keyword.objects.get_or_create(
                keyword=kw_text.lower(), app=app
            )
            today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
            if not created and kw_obj.results.filter(
                country=country, searched_at__gte=today_start
            ).exists():
                skipped.append(f"{kw_text} ({country.upper()})")
                continue

            if call_count > 0:
                time.sleep(2)
            call_count += 1

            competitors = itunes.search_apps(kw_text, country=country, limit=25)
            difficulty_score, breakdown = diff_calc.calculate(competitors, keyword=kw_text)

            app_rank = None
            if app and app.track_id:
                app_rank = itunes.find_app_rank(kw_text, app.track_id, country=country)

            popularity = pop_est.estimate(competitors, kw_text)
            dl_estimates = dl_est.estimate(popularity or 0, country=country)
            breakdown["download_estimates"] = dl_estimates

            sr = SearchResult.upsert_today(
                keyword=kw_obj,
                popularity_score=popularity,
                difficulty_score=difficulty_score,
                difficulty_breakdown=breakdown,
                competitors_data=competitors,
                app_rank=app_rank,
                country=country,
            )
            _, advice_label, _, advice_desc = sr.targeting_advice
            country_results.append({
                "keyword": kw_text,
                "country": country,
                "popularity_score": popularity,
                "difficulty_score": difficulty_score,
                "difficulty_label": sr.difficulty_label,
                "targeting_advice": {"label": advice_label, "description": advice_desc},
                "app_rank": app_rank,
                "result_id": sr.id,
            })
        results_by_country[country] = country_results

    # Opportunity ranking for multi-country
    opportunity_ranking: list[dict] = []
    if len(countries) > 1:
        kw_map: dict = {}
        for country, cresults in results_by_country.items():
            for r in cresults:
                kw = r["keyword"]
                if kw not in kw_map:
                    kw_map[kw] = {}
                pop = r["popularity_score"] or 0
                diff = r["difficulty_score"]
                kw_map[kw][country] = {
                    "popularity": pop,
                    "difficulty": diff,
                    "opportunity": round(pop * (100 - diff) / 100),
                }
        for kw, country_data in kw_map.items():
            best = max(country_data, key=lambda c: country_data[c]["opportunity"])
            opportunity_ranking.append({
                "keyword": kw,
                "countries": country_data,
                "best_country": best,
                "best_score": country_data[best]["opportunity"],
            })
        opportunity_ranking.sort(key=lambda x: x["best_score"], reverse=True)

    response: dict = {"results_by_country": results_by_country}
    if opportunity_ranking:
        response["opportunity_ranking"] = opportunity_ranking
    if skipped:
        response["skipped"] = skipped
    return response


@tool
def opportunity_search(keyword: str, app_id: int | None = None) -> dict:
    """
    Search a single keyword across all 30 countries.

    Returns a ranked list of countries by opportunity score.
    Runtime: ~60 seconds (30 iTunes calls at 2s each). Connection stays open.
    """
    import time
    from .models import App
    # COUNTRY_CHOICES is imported at module level for test patchability
    from .services import DifficultyCalculator, PopularityEstimator, DownloadEstimator

    kw_text = keyword.strip().lower()
    if not kw_text:
        return {"error": "No keyword provided"}

    app = None
    if app_id:
        try:
            app = App.objects.get(id=app_id)
        except App.DoesNotExist:
            return {"error": f"App {app_id} not found"}

    itunes = ITunesSearchService()
    diff_calc = DifficultyCalculator()
    pop_est = PopularityEstimator()
    dl_est = DownloadEstimator()

    results = []
    for i, (country_code, _country_name) in enumerate(COUNTRY_CHOICES):
        if i > 0:
            time.sleep(2)

        competitors = itunes.search_apps(kw_text, country=country_code, limit=25)
        difficulty_score, breakdown = diff_calc.calculate(competitors, keyword=kw_text)
        popularity = pop_est.estimate(competitors, kw_text)
        dl_estimates = dl_est.estimate(popularity or 0, country=country_code)
        breakdown["download_estimates"] = dl_estimates

        app_rank = None
        if app and app.track_id:
            app_rank = itunes.find_app_rank(kw_text, app.track_id, country=country_code)

        opportunity = round(popularity * (100 - difficulty_score) / 100) if popularity else 0
        results.append({
            "country": country_code,
            "popularity": popularity,
            "difficulty": difficulty_score,
            "difficulty_label": _difficulty_label(difficulty_score),
            "opportunity": opportunity,
            "app_rank": app_rank,
            "competitor_count": len(competitors),
        })

    results.sort(key=lambda x: x["opportunity"], reverse=True)
    return {"keyword": kw_text, "results": results, "total_countries": len(results)}


@tool
def refresh_keyword(keyword_id: int, country: str = "us") -> dict:
    """Re-run scoring for a single keyword+country combination."""
    import time
    from .models import Keyword, SearchResult
    from .services import DifficultyCalculator, PopularityEstimator, DownloadEstimator

    try:
        kw = Keyword.objects.select_related("app").get(id=keyword_id)
    except Keyword.DoesNotExist:
        return {"error": f"Keyword {keyword_id} not found"}

    itunes = ITunesSearchService()
    diff_calc = DifficultyCalculator()
    pop_est = PopularityEstimator()
    dl_est = DownloadEstimator()

    competitors = itunes.search_apps(kw.keyword, country=country, limit=25)
    difficulty_score, breakdown = diff_calc.calculate(competitors, keyword=kw.keyword)

    app_rank = None
    if kw.app and kw.app.track_id:
        app_rank = itunes.find_app_rank(kw.keyword, kw.app.track_id, country=country)

    popularity = pop_est.estimate(competitors, kw.keyword)
    dl_estimates = dl_est.estimate(popularity or 0, country=country)
    breakdown["download_estimates"] = dl_estimates

    sr = SearchResult.upsert_today(
        keyword=kw,
        popularity_score=popularity,
        difficulty_score=difficulty_score,
        difficulty_breakdown=breakdown,
        competitors_data=competitors,
        app_rank=app_rank,
        country=country,
    )

    return {
        "success": True,
        "result": {
            "keyword": kw.keyword,
            "keyword_id": kw.id,
            "result_id": sr.id,
            "popularity_score": popularity,
            "difficulty_score": difficulty_score,
            "difficulty_label": sr.difficulty_label,
            "country": country,
            "searched_at": sr.searched_at.isoformat(),
            "app_rank": app_rank,
        },
    }


@tool
def bulk_refresh_keywords(app_id: int | None = None, country: str = "us") -> dict:
    """
    Re-run scoring for all keywords under an app.

    If app_id is omitted, refreshes only keywords with no associated app.
    To refresh across all apps, call once per app.
    """
    import time
    from .models import Keyword, SearchResult
    from .services import DifficultyCalculator, PopularityEstimator, DownloadEstimator

    if app_id is not None:
        keywords = list(Keyword.objects.filter(app_id=app_id).select_related("app"))
    else:
        keywords = list(Keyword.objects.filter(app__isnull=True))

    if not keywords:
        return {"success": True, "results": [], "refreshed": 0}

    itunes = ITunesSearchService()
    diff_calc = DifficultyCalculator()
    pop_est = PopularityEstimator()
    dl_est = DownloadEstimator()

    results = []
    for i, kw in enumerate(keywords):
        if i > 0:
            time.sleep(2)

        competitors = itunes.search_apps(kw.keyword, country=country, limit=25)
        difficulty_score, breakdown = diff_calc.calculate(competitors, keyword=kw.keyword)

        app_rank = None
        if kw.app and kw.app.track_id:
            app_rank = itunes.find_app_rank(kw.keyword, kw.app.track_id, country=country)

        popularity = pop_est.estimate(competitors, kw.keyword)
        dl_estimates = dl_est.estimate(popularity or 0, country=country)
        breakdown["download_estimates"] = dl_estimates

        sr = SearchResult.upsert_today(
            keyword=kw,
            popularity_score=popularity,
            difficulty_score=difficulty_score,
            difficulty_breakdown=breakdown,
            competitors_data=competitors,
            app_rank=app_rank,
            country=country,
        )
        results.append({
            "keyword": kw.keyword,
            "keyword_id": kw.id,
            "result_id": sr.id,
            "popularity_score": popularity,
            "difficulty_score": difficulty_score,
            "country": country,
        })

    return {"success": True, "results": results, "refreshed": len(results)}


# ---------------------------------------------------------------------------
# Management Tools
# ---------------------------------------------------------------------------

@tool
def add_app(
    name: str,
    bundle_id: str = "",
    track_id: int | None = None,
    store_url: str = "",
    icon_url: str = "",
    seller_name: str = "",
) -> dict:
    """Add a new app for keyword tracking. track_id must be unique if provided."""
    from .models import App

    if track_id is not None and App.objects.filter(track_id=track_id).exists():
        return {"error": "An app with this track_id already exists"}

    app = App.objects.create(
        name=name,
        bundle_id=bundle_id,
        track_id=track_id,
        store_url=store_url,
        icon_url=icon_url,
        seller_name=seller_name,
    )
    return {"id": app.id, "name": app.name}


@tool
def delete_keyword(keyword_id: int) -> dict:
    """Delete a keyword and all its search results."""
    from .models import Keyword

    try:
        kw = Keyword.objects.get(id=keyword_id)
    except Keyword.DoesNotExist:
        return {"error": f"Keyword {keyword_id} not found"}

    name = kw.keyword
    kw.delete()
    return {"success": True, "deleted": name}


@tool
def delete_app(app_id: int) -> dict:
    """Delete an app. Keywords linked to this app are preserved (their app field is set to null)."""
    from .models import App

    try:
        app = App.objects.get(id=app_id)
    except App.DoesNotExist:
        return {"error": f"App {app_id} not found"}

    name = app.name
    app.delete()
    return {"success": True, "deleted": name}
