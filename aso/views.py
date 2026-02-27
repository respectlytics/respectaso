import csv
import json
import logging
import re
import time
import urllib.request

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from .forms import AppForm, KeywordSearchForm, OpportunitySearchForm, COUNTRY_CHOICES
from .models import App, Keyword, SearchResult
from .services import (
    DifficultyCalculator,
    DownloadEstimator,
    ITunesSearchService,
    PopularityEstimator,
)

logger = logging.getLogger(__name__)


# app_rank is now persisted directly on SearchResult during search/refresh.
# No need for a helper to find rank in stored competitors.


def methodology_view(request):
    """Our Methodology page — explains how RespectASO works."""
    return render(request, "aso/methodology.html")


def setup_view(request):
    """Setup guide — custom domain, Docker config, and getting started."""
    return render(request, "aso/setup.html")


def dashboard_view(request):
    """
    Main dashboard with keyword search bar, results, and full search history.

    Shows only the latest result per keyword+country pair.  Each result
    is annotated with trend data (comparison to previous result) for
    inline ↑↓ indicators.
    """
    apps = App.objects.all()
    search_form = KeywordSearchForm()

    # --- History table (latest result per keyword+country) ---
    app_id = request.GET.get("app")
    country_filter = request.GET.get("country", "")

    # Get the latest result ID for each keyword+country pair
    from django.db.models import Max

    latest_filter = {}
    if app_id:
        latest_filter["keyword__app_id"] = app_id
    if country_filter:
        latest_filter["country"] = country_filter.lower()

    latest_ids_qs = (
        SearchResult.objects
        .filter(**latest_filter)
        .values("keyword_id", "country")
        .annotate(latest_id=Max("id"))
        .values_list("latest_id", flat=True)
    )

    # Distinct countries that have results (for the history country filter)
    country_base_filter = {}
    if app_id:
        country_base_filter["keyword__app_id"] = app_id
    available_countries = (
        SearchResult.objects
        .filter(**country_base_filter)
        .values_list("country", flat=True)
        .distinct()
        .order_by("country")
    )
    latest_ids = list(latest_ids_qs)

    results_qs = (
        SearchResult.objects
        .filter(id__in=latest_ids)
        .select_related("keyword", "keyword__app")
        .order_by("-searched_at")
    )

    # Count unique keywords for the toolbar
    keyword_qs = Keyword.objects.all()
    if app_id:
        keyword_qs = keyword_qs.filter(app_id=app_id)
    keyword_count = keyword_qs.count()

    # Pagination (25 per page)
    page = request.GET.get("page", "1")
    try:
        page = max(1, int(page))
    except (ValueError, TypeError):
        page = 1

    per_page = 25
    total_count = results_qs.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    history_results = list(results_qs[start : start + per_page])

    # Annotate each result with trend data (previous result comparison)
    for result in history_results:
        prev = (
            SearchResult.objects
            .filter(
                keyword_id=result.keyword_id,
                country=result.country,
                searched_at__lt=result.searched_at,
            )
            .order_by("-searched_at")
            .first()
        )
        history_count = SearchResult.objects.filter(
            keyword_id=result.keyword_id, country=result.country
        ).count()
        result.has_history = history_count > 1
        if prev:
            result.prev_popularity = prev.popularity_score
            result.prev_difficulty = prev.difficulty_score
            result.prev_rank = prev.app_rank
            # Calculate deltas
            if result.popularity_score is not None and prev.popularity_score is not None:
                result.popularity_delta = result.popularity_score - prev.popularity_score
            else:
                result.popularity_delta = None
            result.difficulty_delta = result.difficulty_score - prev.difficulty_score
            if result.app_rank is not None and prev.app_rank is not None:
                result.rank_delta = prev.app_rank - result.app_rank  # Lower rank = better = positive delta
            else:
                result.rank_delta = None
        else:
            result.prev_popularity = None
            result.prev_difficulty = None
            result.prev_rank = None
            result.popularity_delta = None
            result.difficulty_delta = None
            result.rank_delta = None

    # Show rank column when filtering by an app that has a track_id
    show_rank = False
    selected_app_name = None
    if app_id:
        selected_app_obj = App.objects.filter(id=app_id).first()
        if selected_app_obj:
            selected_app_name = selected_app_obj.name
            if selected_app_obj.track_id:
                show_rank = True

    return render(
        request,
        "aso/dashboard.html",
        {
            "apps": apps,
            "search_form": search_form,
            # History table context
            "history_results": history_results,
            "keyword_count": keyword_count,
            "selected_app": int(app_id) if app_id else None,
            "selected_app_name": selected_app_name,
            "selected_country": country_filter,
            "available_countries": list(available_countries),
            "show_rank": show_rank,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "has_prev": page > 1,
            "has_next": page < total_pages,
        },
    )


@require_POST
def search_view(request):
    """
    Process keyword search request across one or more countries (max 5).

    Accepts comma-separated keywords (max 20) and comma-separated countries (max 5).
    For each keyword × country combination:
      1. Search iTunes for top competitors
      2. Calculate difficulty score
      3. Estimate popularity from competitor data
      4. Save results to DB
    Returns JSON with results grouped by country.
    """
    form = KeywordSearchForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"error": "Invalid form data."}, status=400)

    raw_keywords = form.cleaned_data["keywords"]
    app_id = form.cleaned_data.get("app_id")
    countries = form.cleaned_data.get("countries", ["us"])

    # Parse comma-separated keywords, limit to 20
    keywords = [kw.strip() for kw in raw_keywords.split(",") if kw.strip()][:20]

    if not keywords:
        return JsonResponse({"error": "No keywords provided."}, status=400)

    # Get app if specified
    app = None
    if app_id:
        try:
            app = App.objects.get(id=app_id)
        except App.DoesNotExist:
            pass

    # Set up services
    itunes_service = ITunesSearchService()
    difficulty_calc = DifficultyCalculator()
    popularity_est = PopularityEstimator()
    download_est = DownloadEstimator()

    # Results grouped by country
    results_by_country = {}
    skipped = []
    call_count = 0

    for country in countries:
        country_results = []
        for kw_text in keywords:
            # Rate limit between API calls
            if call_count > 0:
                time.sleep(2)
            call_count += 1

            # Get or create keyword
            keyword_obj, created = Keyword.objects.get_or_create(
                keyword=kw_text.lower(),
                app=app,
            )

            # Skip if this keyword already has results for the SAME country today
            today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
            if not created and keyword_obj.results.filter(
                country=country, searched_at__gte=today_start
            ).exists():
                skipped.append(f"{kw_text} ({country.upper()})")
                continue

            # iTunes Search
            competitors = itunes_service.search_apps(kw_text, country=country, limit=25)

            # Difficulty Score
            difficulty_score, breakdown = difficulty_calc.calculate(
                competitors, keyword=kw_text
            )

            # Find user's app rank (if app has a track_id)
            app_rank = None
            if app and app.track_id:
                app_rank = itunes_service.find_app_rank(
                    kw_text, app.track_id, country=country
                )

            # Popularity (estimated from competitor data)
            popularity = popularity_est.estimate(competitors, kw_text)

            # Download estimates
            download_estimates = download_est.estimate(popularity or 0, len(competitors))
            breakdown["download_estimates"] = download_estimates

            # Save result (one entry per keyword+country per day)
            search_result = SearchResult.upsert_today(
                keyword=keyword_obj,
                popularity_score=popularity,
                difficulty_score=difficulty_score,
                difficulty_breakdown=breakdown,
                competitors_data=competitors,
                app_rank=app_rank,
                country=country,
            )

            country_results.append(
                {
                    "keyword": kw_text,
                    "country": country,
                    "popularity_score": popularity,
                    "difficulty_score": difficulty_score,
                    "difficulty_label": search_result.difficulty_label,
                    "difficulty_color": search_result.difficulty_color,
                    "difficulty_breakdown": breakdown,
                    "competitors": competitors,
                    "result_id": search_result.id,
                    "app_rank": app_rank,
                    "app_name": app.name if app else None,
                    "app_icon": app.icon_url if app else None,
                }
            )
        results_by_country[country] = country_results

    # Build opportunity ranking when multiple countries searched
    opportunity_ranking = []
    if len(countries) > 1:
        # Group results by keyword across countries
        kw_map = {}
        for country, cresults in results_by_country.items():
            for r in cresults:
                kw = r["keyword"]
                if kw not in kw_map:
                    kw_map[kw] = {}
                pop = r["popularity_score"] or 0
                diff = r["difficulty_score"]
                opp = round(pop * (100 - diff) / 100)
                kw_map[kw][country] = {
                    "popularity": pop,
                    "difficulty": diff,
                    "opportunity": opp,
                }
        for kw, country_data in kw_map.items():
            best_country = max(country_data, key=lambda c: country_data[c]["opportunity"])
            opportunity_ranking.append({
                "keyword": kw,
                "countries": country_data,
                "best_country": best_country,
                "best_score": country_data[best_country]["opportunity"],
            })
        opportunity_ranking.sort(key=lambda x: x["best_score"], reverse=True)

    response_data = {
        "results_by_country": results_by_country,
        "countries": countries,
        "opportunity_ranking": opportunity_ranking,
    }
    if skipped:
        response_data["skipped"] = skipped
        response_data["warning"] = (
            f"Skipped {len(skipped)} keyword(s) already in your list: "
            + ", ".join(skipped)
            + ". Use Refresh to update them."
        )
    return JsonResponse(response_data)


def opportunity_view(request):
    """Country Opportunity Finder — search a keyword across all 30 countries."""
    apps = App.objects.all()
    form = OpportunitySearchForm()
    return render(request, "aso/opportunity.html", {"apps": apps, "form": form})


@require_POST
def opportunity_search_view(request):
    """
    AJAX endpoint: search a single keyword across all 30 countries.

    Returns ranked list of countries by opportunity score.
    """
    form = OpportunitySearchForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"error": "Invalid form data."}, status=400)

    kw_text = form.cleaned_data["keyword"].strip().lower()
    app_id = form.cleaned_data.get("app_id")

    if not kw_text:
        return JsonResponse({"error": "No keyword provided."}, status=400)

    app = None
    if app_id:
        try:
            app = App.objects.get(id=app_id)
        except App.DoesNotExist:
            pass

    itunes_service = ITunesSearchService()
    difficulty_calc = DifficultyCalculator()
    popularity_est = PopularityEstimator()
    download_est = DownloadEstimator()

    results = []
    for i, (country_code, country_name) in enumerate(COUNTRY_CHOICES):
        if i > 0:
            time.sleep(2)

        competitors = itunes_service.search_apps(kw_text, country=country_code, limit=25)
        difficulty_score, breakdown = difficulty_calc.calculate(
            competitors, keyword=kw_text
        )
        popularity = popularity_est.estimate(competitors, kw_text)

        download_estimates = download_est.estimate(popularity or 0, len(competitors))
        breakdown["download_estimates"] = download_estimates

        app_rank = None
        if app and app.track_id:
            app_rank = itunes_service.find_app_rank(
                kw_text, app.track_id, country=country_code
            )

        # Compute difficulty label from score (same logic as model property)
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

        opportunity = round(popularity * (100 - difficulty_score) / 100) if popularity else 0
        top_competitor = competitors[0]["trackName"] if competitors else "—"
        top_ratings = competitors[0].get("userRatingCount", 0) if competitors else 0

        results.append({
            "country": country_code,
            "popularity": popularity,
            "difficulty": difficulty_score,
            "difficulty_label": diff_label,
            "difficulty_breakdown": breakdown,
            "competitors_data": competitors,
            "opportunity": opportunity,
            "app_rank": app_rank,
            "competitor_count": len(competitors),
            "top_competitor": top_competitor,
            "top_ratings": top_ratings,
        })

    results.sort(key=lambda x: x["opportunity"], reverse=True)

    return JsonResponse({
        "keyword": kw_text,
        "app_id": app.id if app else None,
        "results": results,
        "total_countries": len(results),
    })


@require_POST
def opportunity_save_view(request):
    """
    Save selected opportunity results to search history.

    Accepts JSON body with keyword, app_id, and selected results
    (each containing country, popularity, difficulty, breakdown, competitors, etc.).
    """
    try:
        body = json.loads(request.body)
    except json.JSONDecodeError:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    kw_text = body.get("keyword", "").strip().lower()
    app_id = body.get("app_id")
    selected = body.get("results", [])

    if not kw_text or not selected:
        return JsonResponse({"error": "No keyword or results provided."}, status=400)

    app = None
    if app_id:
        try:
            app = App.objects.get(id=app_id)
        except App.DoesNotExist:
            pass

    keyword_obj, _ = Keyword.objects.get_or_create(keyword=kw_text, app=app)
    saved = 0

    for item in selected:
        country = item.get("country", "us")
        # One entry per keyword+country per day (preserves historical trend data)
        SearchResult.upsert_today(
            keyword=keyword_obj,
            popularity_score=item.get("popularity", 0),
            difficulty_score=item.get("difficulty", 0),
            difficulty_breakdown=item.get("difficulty_breakdown", {}),
            competitors_data=item.get("competitors_data", []),
            app_rank=item.get("app_rank"),
            country=country,
        )
        saved += 1

    return JsonResponse({"success": True, "saved": saved})


def app_lookup_view(request):
    """
    AJAX endpoint: search the App Store for apps by name or URL.

    Accepts GET parameter 'q' — either:
      - An App Store URL (https://apps.apple.com/...id123456789)
      - A search query (app name)

    Returns JSON list of matching apps with icon, name, bundle_id, track_id.
    """
    query = request.GET.get("q", "").strip()
    if not query or len(query) < 2:
        return JsonResponse({"apps": []})

    itunes_service = ITunesSearchService()

    # Check if the query is an App Store URL
    url_match = re.search(r"/id(\d+)", query)
    if url_match:
        track_id = int(url_match.group(1))
        app_data = itunes_service.lookup_by_id(track_id)
        if app_data:
            return JsonResponse(
                {
                    "apps": [
                        {
                            "trackId": app_data["trackId"],
                            "trackName": app_data["trackName"],
                            "artworkUrl100": app_data["artworkUrl100"],
                            "bundleId": app_data["bundleId"],
                            "sellerName": app_data["sellerName"],
                        }
                    ]
                }
            )
        return JsonResponse({"apps": []})

    # Otherwise search by name
    results = itunes_service.search_apps(query, limit=5)
    return JsonResponse(
        {
            "apps": [
                {
                    "trackId": r["trackId"],
                    "trackName": r["trackName"],
                    "artworkUrl100": r["artworkUrl100"],
                    "bundleId": r["bundleId"],
                    "sellerName": r["sellerName"],
                }
                for r in results
            ]
        }
    )


def apps_view(request):
    """
    Manage apps for keyword categorization.

    Supports two flows:
      1. Manual entry (name + optional bundle_id)
      2. App Store lookup (sets track_id, icon, seller from iTunes data)
    """
    message = None
    message_type = None

    if request.method == "POST":
        # Check if this is from App Store lookup (has track_id)
        track_id = request.POST.get("track_id")
        if track_id:
            try:
                track_id_int = int(track_id)
                # Prevent duplicate
                if App.objects.filter(track_id=track_id_int).exists():
                    message = "This app has already been added."
                    message_type = "error"
                else:
                    App.objects.create(
                        name=request.POST.get("name", "Unknown App"),
                        bundle_id=request.POST.get("bundle_id", ""),
                        track_id=track_id_int,
                        store_url=request.POST.get("store_url", ""),
                        icon_url=request.POST.get("icon_url", ""),
                        seller_name=request.POST.get("seller_name", ""),
                    )
                    message = f"App '{request.POST.get('name')}' added from App Store."
                    message_type = "success"
            except (ValueError, TypeError):
                message = "Invalid app data."
                message_type = "error"
        else:
            # Manual entry
            form = AppForm(request.POST)
            if form.is_valid():
                form.save()
                message = f"App '{form.cleaned_data['name']}' created."
                message_type = "success"
            else:
                message = "Please fix the errors below."
                message_type = "error"

    form = AppForm()
    apps = App.objects.prefetch_related("keywords")

    return render(
        request,
        "aso/apps.html",
        {
            "form": form,
            "apps": apps,
            "message": message,
            "message_type": message_type,
        },
    )


@require_POST
def app_delete_view(request, app_id):
    """Delete an app. Keywords are preserved (app set to null)."""
    app = get_object_or_404(App, id=app_id)
    name = app.name
    app.delete()
    return redirect("aso:apps")


@require_POST
def keyword_delete_view(request, keyword_id):
    """Delete a keyword and all its search results."""
    keyword = get_object_or_404(Keyword, id=keyword_id)
    keyword.delete()
    return JsonResponse({"success": True})


@require_POST
def result_delete_view(request, result_id):
    """Delete a single search result. If the parent keyword has no remaining results, delete the keyword too."""
    result = get_object_or_404(SearchResult, id=result_id)
    keyword = result.keyword
    result.delete()
    # Clean up orphaned keyword (no remaining search results)
    if not keyword.results.exists():
        keyword.delete()
    return JsonResponse({"success": True})


@require_POST
def keywords_bulk_delete_view(request):
    """
    Delete all keywords for an app, or ALL keywords when no app filter is active.

    POST body: {"app_id": int|null}
    """
    body = json.loads(request.body)
    app_id = body.get("app_id")

    if app_id:
        count, _ = Keyword.objects.filter(app_id=app_id).delete()
    else:
        # No app filter → delete ALL keywords (and cascade-delete their results)
        count, _ = Keyword.objects.all().delete()

    return JsonResponse({"success": True, "deleted": count})


@require_POST
def keyword_refresh_view(request, keyword_id):
    """
    Re-run the difficulty search for a single keyword.

    Uses the keyword's existing app and the country from the request.
    Returns the new result as JSON.
    """
    keyword_obj = get_object_or_404(Keyword, id=keyword_id)
    country = request.POST.get("country", "us")

    itunes_service = ITunesSearchService()
    difficulty_calc = DifficultyCalculator()
    popularity_est = PopularityEstimator()
    download_est = DownloadEstimator()

    # Search iTunes
    competitors = itunes_service.search_apps(
        keyword_obj.keyword, country=country, limit=25
    )

    # Calculate difficulty
    difficulty_score, breakdown = difficulty_calc.calculate(
        competitors, keyword=keyword_obj.keyword
    )

    # App rank
    app_rank = None
    app = keyword_obj.app
    if app and app.track_id:
        app_rank = itunes_service.find_app_rank(
            keyword_obj.keyword, app.track_id, country=country
        )

    # Popularity (estimated from competitor data)
    popularity = popularity_est.estimate(competitors, keyword_obj.keyword)

    # Download estimates
    download_estimates = download_est.estimate(popularity or 0, len(competitors))
    breakdown["download_estimates"] = download_estimates

    # Save new result (one entry per keyword+country per day)
    search_result = SearchResult.upsert_today(
        keyword=keyword_obj,
        popularity_score=popularity,
        difficulty_score=difficulty_score,
        difficulty_breakdown=breakdown,
        competitors_data=competitors,
        app_rank=app_rank,
        country=country,
    )

    return JsonResponse({
        "success": True,
        "result": {
            "keyword": keyword_obj.keyword,
            "keyword_id": keyword_obj.pk,
            "result_id": search_result.pk,
            "popularity_score": popularity,
            "difficulty_score": difficulty_score,
            "difficulty_label": search_result.difficulty_label,
            "difficulty_color": search_result.difficulty_color,
            "country": country,
            "searched_at": search_result.searched_at.strftime("%b %d, %H:%M"),
            "app_rank": app_rank,
            "app_name": app.name if app else None,
        },
    })


def export_history_csv_view(request):
    """
    Export all search history as a CSV file.

    Supports optional ?app= filter to limit to one app.
    """
    app_id = request.GET.get("app")
    country = request.GET.get("country")
    results_qs = SearchResult.objects.select_related("keyword", "keyword__app").order_by(
        "-searched_at"
    )
    if app_id:
        results_qs = results_qs.filter(keyword__app_id=app_id)
    if country:
        results_qs = results_qs.filter(country=country.lower())

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = 'attachment; filename="respectaso-search-history.csv"'

    writer = csv.writer(response)
    writer.writerow([
        "Keyword", "App", "Country", "Popularity", "Difficulty",
        "Difficulty Label", "Rank", "Competitors", "Date",
    ])

    for r in results_qs:
        writer.writerow([
            r.keyword.keyword,
            r.keyword.app.name if r.keyword.app else "",
            r.country.upper() if r.country else "",
            r.popularity_score if r.popularity_score is not None else "",
            r.difficulty_score,
            r.difficulty_label,
            r.app_rank if r.app_rank else "",
            len(r.competitors_data) if r.competitors_data else 0,
            r.searched_at.strftime("%Y-%m-%d %H:%M") if r.searched_at else "",
        ])

    # Respectlytics attribution row
    writer.writerow([])
    writer.writerow(["Privacy-first mobile analytics — https://respectlytics.com"])

    return response


@require_POST
def keywords_bulk_refresh_view(request):
    """
    Re-run difficulty for all keywords under an app.

    POST body: {"app_id": int|null, "country": "us"}
    Returns JSON with all new results.
    """
    body = json.loads(request.body)
    app_id = body.get("app_id")
    country = body.get("country", "us")

    if app_id:
        keywords = Keyword.objects.filter(app_id=app_id)
    else:
        keywords = Keyword.objects.filter(app__isnull=True)

    if not keywords.exists():
        return JsonResponse({"success": True, "results": [], "refreshed": 0})

    itunes_service = ITunesSearchService()
    difficulty_calc = DifficultyCalculator()
    popularity_est = PopularityEstimator()
    download_est = DownloadEstimator()

    results = []
    for i, kw in enumerate(keywords):
        if i > 0:
            time.sleep(2)

        competitors = itunes_service.search_apps(kw.keyword, country=country, limit=25)
        difficulty_score, breakdown = difficulty_calc.calculate(
            competitors, keyword=kw.keyword
        )

        app_rank = None
        if kw.app and kw.app.track_id:
            app_rank = itunes_service.find_app_rank(
                kw.keyword, kw.app.track_id, country=country
            )

        popularity = popularity_est.estimate(competitors, kw.keyword)

        # Download estimates
        download_estimates = download_est.estimate(popularity or 0, len(competitors))
        breakdown["download_estimates"] = download_estimates

        search_result = SearchResult.upsert_today(
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
            "keyword_id": kw.pk,
            "result_id": search_result.pk,
            "popularity_score": popularity,
            "difficulty_score": difficulty_score,
            "difficulty_label": search_result.difficulty_label,
            "difficulty_color": search_result.difficulty_color,
            "country": country,
            "searched_at": search_result.searched_at.strftime("%b %d, %H:%M"),
            "app_rank": app_rank,
            "app_name": kw.app.name if kw.app else None,
        })

    return JsonResponse({"success": True, "results": results, "refreshed": len(results)})


def version_check_view(request):
    """Check GitHub for a newer release. Returns JSON with update info."""
    current = settings.VERSION
    try:
        url = "https://api.github.com/repos/respectlytics/respectaso/releases/latest"
        req = urllib.request.Request(url, headers={"Accept": "application/vnd.github.v3+json"})
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode())
        latest = data.get("tag_name", "").lstrip("v")
        if not latest:
            return JsonResponse({"update_available": False, "current": current})
        # Simple semver comparison
        current_parts = [int(x) for x in current.split(".")]
        latest_parts = [int(x) for x in latest.split(".")]
        update_available = latest_parts > current_parts
        return JsonResponse({
            "update_available": update_available,
            "current": current,
            "latest": latest,
            "release_url": data.get("html_url", ""),
        })
    except Exception:
        # Network error, GitHub down, etc. — silently fail
        return JsonResponse({"update_available": False, "current": current})


def auto_refresh_status_view(request):
    """Return the current auto-refresh progress as JSON."""
    from .scheduler import get_status
    return JsonResponse(get_status())


def keyword_trend_view(request, keyword_id):
    """
    Return historical trend data for a keyword across all countries.

    Query param: ?country=us (optional, defaults to all)
    Returns JSON with date-series data for charting.
    """
    keyword_obj = get_object_or_404(Keyword, id=keyword_id)
    country = request.GET.get("country")

    qs = SearchResult.objects.filter(keyword=keyword_obj).order_by("searched_at")
    if country:
        qs = qs.filter(country=country)

    data_points = []
    for r in qs:
        data_points.append({
            "date": r.searched_at.strftime("%Y-%m-%d"),
            "date_display": r.searched_at.strftime("%b %d"),
            "popularity": r.popularity_score,
            "difficulty": r.difficulty_score,
            "rank": r.app_rank,
            "country": r.country,
        })

    return JsonResponse({
        "keyword": keyword_obj.keyword,
        "keyword_id": keyword_obj.pk,
        "app_name": keyword_obj.app.name if keyword_obj.app else None,
        "data_points": data_points,
    })
