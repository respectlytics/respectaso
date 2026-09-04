import csv
import json
import logging
import re
import time

logger = logging.getLogger(__name__)

from django.conf import settings
from django.http import HttpResponse, JsonResponse
from django.utils import timezone
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_POST

from . import run_queue, search_jobs, update_check
from .forms import AppForm, KeywordSearchForm, OpportunitySearchForm, COUNTRY_CHOICES
from .keyword_scoring import score_keyword_pair
from .models import App, Keyword, KeywordSearchJob, SearchResult
from .pro_access import pro_required_json
from .dashboard_summary import compute_app_summary
from .popularity import (
    annotate_effective_popularity,
    popularity_fields,
    resolve_popularity,
)
from .scoring import calc_opportunity, classify_keyword, CLASSIFICATION_LABELS
from .services import (
    DifficultyCalculator,
    DownloadEstimator,
    ITunesSearchService,
    SearchAPIUnavailableError,
)

logger = logging.getLogger(__name__)


# app_rank is now persisted directly on SearchResult during search/refresh.
# No need for a helper to find rank in stored competitors.

# Allowed page sizes for the dashboard history table.
HISTORY_PER_PAGE_CHOICES = (25, 50, 100, 200)
HISTORY_PER_PAGE_DEFAULT = 25


def methodology_view(request):
    """Our Methodology page — explains how RespectASO works."""
    return render(request, "aso/methodology.html")


def whats_new_view(request):
    """What's New page — the app's release history, newest first.

    Opening the page counts as having seen the current version's notes,
    which clears the one-time update notice.
    """
    from .release_notes import RELEASES, mark_seen

    mark_seen()
    return render(request, "aso/whats_new.html", {"releases": RELEASES})


@require_POST
def whats_new_seen_view(request):
    """Dismiss the one-time update notice without opening the page."""
    from .release_notes import mark_seen

    mark_seen()
    return JsonResponse({"ok": True})


@require_POST
def respectlytics_banner_dismiss_view(request):
    """Hide the Respectlytics banner for good on this install.

    Pro users never see the banner at all (base.html); this is the free
    edition's opt-out. Stored server-side so it survives a restart in the
    desktop app, where localStorage is not available.
    """
    from . import ui_state

    ui_state.dismiss(ui_state.RESPECTLYTICS_BANNER)
    return JsonResponse({"ok": True})


def setup_view(request):
    """Setup guide — custom domain, Docker config, and getting started."""
    return render(request, "aso/setup.html")


def apple_ads_setup_view(request):
    """Step-by-step guide for connecting an Apple Ads account."""
    return render(request, "aso/apple_ads_setup.html")


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
    sort_by = request.GET.get("sort", "date")
    sort_dir = request.GET.get("dir", "desc")

    valid_sort_fields = {
        "keyword",
        "rank",
        "popularity",
        "difficulty",
        "opportunity",
        "est_downloads",
        "insight",
        "country",
        "competitors",
        "date",
    }
    if sort_by not in valid_sort_fields:
        sort_by = "date"
    if sort_dir not in {"asc", "desc"}:
        sort_dir = "desc"

    # Rank column is always visible. Per-result rendering shows the app's
    # rank for keywords tied to a tracked app, and "—" when the keyword
    # has no associated app (or the app has no track_id).
    show_rank = True
    selected_app_name = None
    if app_id:
        selected_app_obj = App.objects.filter(id=app_id).first()
        if selected_app_obj:
            selected_app_name = selected_app_obj.name

    # --- Filter params (insight, popularity, difficulty) ---
    insight_filter = request.GET.getlist("insight")
    pop_min_param = request.GET.get("pop_min", "")
    diff_max_param = request.GET.get("diff_max", "")
    search_q = request.GET.get("q", "").strip()

    try:
        pop_min = int(pop_min_param) if pop_min_param else None
    except (ValueError, TypeError):
        pop_min = None
    try:
        diff_max = int(diff_max_param) if diff_max_param else None
    except (ValueError, TypeError):
        diff_max = None

    # Get the latest result ID for each keyword+country pair
    from django.db.models import Case, IntegerField, Max, Value, When
    from django.db.models.functions import Lower

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

    # Most recent refresh timestamp (respects app/country filters above).
    # Surfaces the auto-refresh the scheduler runs in the background so users
    # see "Rankings auto-refreshed X ago" without needing to click anything.
    last_refresh = (
        SearchResult.objects
        .filter(**latest_filter)
        .aggregate(latest=Max("searched_at"))["latest"]
    )

    results_qs = (
        SearchResult.objects
        .filter(id__in=latest_ids)
        .select_related("keyword", "keyword__app")
    )

    # Total unfiltered count (before insight/pop/diff filters)
    total_unfiltered_count = results_qs.count()

    # Apply keyword text search
    if search_q:
        results_qs = results_qs.filter(keyword__keyword__icontains=search_q)

    # Apply popularity / difficulty filters (on the EFFECTIVE popularity —
    # the value the user sees, per their source selection)
    results_qs = annotate_effective_popularity(results_qs)
    if pop_min is not None:
        results_qs = results_qs.filter(
            effective_pop__isnull=False,
            effective_pop__gte=pop_min,
        )
    if diff_max is not None:
        results_qs = results_qs.filter(
            difficulty_score__isnull=False,
            difficulty_score__lte=diff_max,
        )

    # Apply insight filter using the stored classification column.
    # classify_keyword() is the single source of truth — the column
    # is set on save(), so a simple __in filter is always exact.
    valid_insights = [i for i in insight_filter if i in CLASSIFICATION_LABELS]
    if valid_insights:
        results_qs = results_qs.filter(classification__in=valid_insights)

    sorted_results = None

    if sort_by == "keyword":
        keyword_order = Lower("keyword__keyword")
        results_qs = results_qs.order_by(
            keyword_order.asc() if sort_dir == "asc" else keyword_order.desc(),
            "-searched_at",
        )
    elif sort_by == "rank":
        if show_rank:
            rank_is_null = Case(
                When(app_rank__isnull=True, then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
            rank_order = "app_rank" if sort_dir == "asc" else "-app_rank"
            results_qs = results_qs.order_by(rank_is_null, rank_order, "-searched_at")
        else:
            sort_by = "date"
            sort_dir = "desc"
            results_qs = results_qs.order_by("-searched_at")
    elif sort_by == "popularity":
        popularity_is_null = Case(
            When(effective_pop__isnull=True, then=Value(1)),
            default=Value(0),
            output_field=IntegerField(),
        )
        popularity_order = "effective_pop" if sort_dir == "asc" else "-effective_pop"
        results_qs = results_qs.order_by(popularity_is_null, popularity_order, "-searched_at")
    elif sort_by == "difficulty":
        difficulty_order = "difficulty_score" if sort_dir == "asc" else "-difficulty_score"
        results_qs = results_qs.order_by(difficulty_order, "-searched_at")
    elif sort_by == "opportunity":
        sorted_results = list(results_qs)
        reverse = sort_dir == "desc"
        sorted_results.sort(
            key=lambda r: (r.opportunity_score, r.searched_at.timestamp()),
            reverse=reverse,
        )
    elif sort_by == "country":
        country_order = "country" if sort_dir == "asc" else "-country"
        results_qs = results_qs.order_by(country_order, "-searched_at")
    elif sort_by == "insight":
        insight_order = "classification" if sort_dir == "asc" else "-classification"
        results_qs = results_qs.order_by(insight_order, "-searched_at")
    elif sort_by == "est_downloads":
        # download_estimates lives in the difficulty_breakdown JSONField, so
        # sort in Python — same pattern as the opportunity/competitors branches.
        # Per scoring-consistency.instructions.md: sort by positions[0].downloads_high
        # (rank #1 high estimate), NEVER tier averages.
        def _dl_high(result):
            est = result.effective_download_estimates or {}
            positions = est.get("positions") or []
            if not positions:
                return -1.0
            try:
                return float(positions[0].get("downloads_high", -1))
            except (TypeError, ValueError):
                return -1.0

        sorted_results = list(results_qs)
        reverse = sort_dir == "desc"
        sorted_results.sort(
            key=lambda r: (_dl_high(r), r.searched_at.timestamp()),
            reverse=reverse,
        )
    elif sort_by == "competitors":
        sorted_results = list(results_qs)
        sorted_results.sort(
            key=lambda result: (
                len(result.competitors_data or []),
                -result.searched_at.timestamp(),
            )
            if sort_dir == "asc"
            else (
                -len(result.competitors_data or []),
                -result.searched_at.timestamp(),
            )
        )
    else:
        date_order = "searched_at" if sort_dir == "asc" else "-searched_at"
        results_qs = results_qs.order_by(date_order)

    # Count unique keywords for the toolbar
    keyword_qs = Keyword.objects.all()
    if app_id:
        keyword_qs = keyword_qs.filter(app_id=app_id)
    keyword_count = keyword_qs.count()

    # Pagination
    page = request.GET.get("page", "1")
    try:
        page = max(1, int(page))
    except (ValueError, TypeError):
        page = 1

    try:
        per_page = int(request.GET.get("per_page", HISTORY_PER_PAGE_DEFAULT))
    except (ValueError, TypeError):
        per_page = HISTORY_PER_PAGE_DEFAULT
    if per_page not in HISTORY_PER_PAGE_CHOICES:
        per_page = HISTORY_PER_PAGE_DEFAULT
    total_count = len(sorted_results) if sorted_results is not None else results_qs.count()
    total_pages = max(1, (total_count + per_page - 1) // per_page)
    page = min(page, total_pages)
    start = (page - 1) * per_page
    if sorted_results is not None:
        history_results = sorted_results[start : start + per_page]
    else:
        history_results = list(results_qs[start : start + per_page])

    # Annotate each result with trend data (previous result comparison).
    # Fetched as one superset query for the page instead of two queries per
    # row — the __in filters may match extra keyword/country combinations,
    # but grouping keys are exact so the extras are simply unused.
    from collections import defaultdict

    history_by_pair = defaultdict(list)
    if history_results:
        snapshot_rows = (
            SearchResult.objects
            .filter(
                keyword_id__in={r.keyword_id for r in history_results},
                country__in={r.country for r in history_results},
            )
            .order_by("-searched_at")
            .values(
                "keyword_id", "country", "searched_at",
                "popularity_score", "apple_popularity_score",
                "difficulty_score", "app_rank", "inferred_genre",
            )
        )
        for row in snapshot_rows:
            history_by_pair[(row["keyword_id"], row["country"])].append(row)

    from .popularity import (
        SOURCE_APPLE,
        effective_from_pair,
        get_popularity_source,
        make_absent_cap_lookup,
    )

    source_setting = get_popularity_source()
    cap_for = make_absent_cap_lookup()

    def _row_effective(row):
        ceiling = None
        if source_setting == SOURCE_APPLE:
            ceiling = cap_for(row["country"], row.get("inferred_genre", ""))
        return effective_from_pair(
            row["popularity_score"], row["apple_popularity_score"],
            source_setting, absent_ceiling=ceiling,
        )[0]

    for result in history_results:
        pair_rows = history_by_pair[(result.keyword_id, result.country)]
        prev = next(
            (row for row in pair_rows if row["searched_at"] < result.searched_at),
            None,
        )
        result.has_history = len(pair_rows) > 1
        if prev:
            prev_effective = _row_effective(prev)
            result.prev_popularity = prev_effective
            result.prev_difficulty = prev["difficulty_score"]
            result.prev_rank = prev["app_rank"]
            # Deltas compare the EFFECTIVE popularity across snapshots
            if result.effective_popularity is not None and prev_effective is not None:
                result.popularity_delta = result.effective_popularity - prev_effective
            else:
                result.popularity_delta = None
            result.difficulty_delta = result.difficulty_score - prev["difficulty_score"]
            if result.app_rank is not None and prev["app_rank"] is not None:
                result.rank_delta = prev["app_rank"] - result.app_rank  # Lower rank = better = positive delta
            else:
                result.rank_delta = None
        else:
            result.prev_popularity = None
            result.prev_difficulty = None
            result.prev_rank = None
            result.popularity_delta = None
            result.difficulty_delta = None
            result.rank_delta = None

    _attach_apple_trends(history_results)

    # Determine if any filters are active
    has_filters = bool(valid_insights or pop_min is not None or diff_max is not None or search_q)

    # App Summary panel — aggregates the user's tracked-keyword data into a
    # 30-second read of the app's ASO posture. Returns None when no app is
    # selected or the app has zero rankings anywhere; the template hides the
    # panel in that case.
    app_summary = compute_app_summary(
        selected_app=int(app_id) if app_id else None,
        selected_app_name=selected_app_name,
        last_refresh=last_refresh,
    )

    # The keyword search in progress (or paused, or queued) and the newest
    # finished one not yet dismissed: rendered on page load so switching back
    # to the tab shows the live state at once. Results are fetched by the JS.
    from .keyword_cleanup import cleanup_suggestion

    panel = search_jobs.panel_job()
    finished = search_jobs.finished_job()
    search_job_bootstrap = {
        "job": search_jobs.job_payload(panel) if panel else None,
        "finished": search_jobs.job_payload(finished) if finished else None,
        "others": [search_jobs.compact_payload(j) for j in search_jobs.other_paused_jobs(panel)],
    }
    cleanup = cleanup_suggestion(
        SearchResult.objects.filter(id__in=latest_ids), app_id=int(app_id) if app_id else None,
    )

    return render(
        request,
        "aso/dashboard.html",
        {
            "apps": apps,
            "search_form": search_form,
            # App Summary panel (None when hidden)
            "app_summary": app_summary,
            # History table context
            "history_results": history_results,
            "keyword_count": keyword_count,
            "selected_app": int(app_id) if app_id else None,
            "selected_app_name": selected_app_name,
            "selected_country": country_filter,
            "available_countries": list(available_countries),
            "show_rank": show_rank,
            "page": page,
            "per_page": per_page,
            "per_page_choices": HISTORY_PER_PAGE_CHOICES,
            "total_pages": total_pages,
            "total_count": total_count,
            "total_unfiltered_count": total_unfiltered_count,
            "last_refresh": last_refresh,
            "has_prev": page > 1,
            "has_next": page < total_pages,
            "current_sort": sort_by,
            "current_dir": sort_dir,
            # Filter state
            "selected_insights": valid_insights,
            "selected_pop_min": pop_min,
            "selected_diff_max": diff_max,
            "search_q": search_q,
            "has_filters": has_filters,
            # Keyword search jobs (aso/search_jobs.py)
            "search_job": search_job_bootstrap,
            "keyword_limit_context": search_jobs.limit_context(),
            "cleanup": cleanup,
        },
    )


def _attach_apple_trends(results) -> None:
    """Attach `apple_trend` (Apple popularity delta vs the previous
    dataset week) to SearchResult rows - bulk per country, local reads
    only. None when the country has no dataset or the term is not in
    both weeks; the popularity cell renders the arrow from it."""
    import datetime as dt

    from .apple_ads import storage as apple_storage
    from .models import AppleTopTerm
    from .popularity import normalize_term

    for result in results:
        result.apple_trend = None
    active_weeks = apple_storage.load_apple_settings()["apple_ads"][
        "active_weeks"
    ]
    by_country: dict = {}
    for result in results:
        by_country.setdefault((result.country or "").lower(), []).append(result)
    for country, country_results in by_country.items():
        active = active_weeks.get(country)
        if not active:
            continue
        trends = AppleTopTerm.trend_lookup(
            [normalize_term(r.keyword.keyword) for r in country_results],
            country,
            dt.date.fromisoformat(active),
        )
        for result in country_results:
            result.apple_trend = trends.get(
                normalize_term(result.keyword.keyword)
            )


@require_POST
def search_view(request):
    """Start a keyword search: create a job and answer at once.

    The search itself runs in the background through the run queue
    (aso/search_jobs.py): one keyword and country pair at a time, resumable
    after a restart. The limit per search depends on the edition (1,000
    with Pro, 3 without) and is an error with a number, never a silent cut.
    ``run_now=1`` puts the job first, pausing a running keyword search
    (Top Search Terms tracks one keyword this way).
    """
    form = KeywordSearchForm(request.POST)
    if not form.is_valid():
        return JsonResponse({"error": "Invalid form data."}, status=400)

    keywords = search_jobs.parse_keywords(form.cleaned_data["keywords"])
    app_id = form.cleaned_data.get("app_id")
    countries = form.cleaned_data.get("countries", ["us"])

    if not keywords:
        return JsonResponse({"error": "No keywords provided."}, status=400)

    context = search_jobs.limit_context()
    limit = context["limit"]
    if len(keywords) > limit:
        return JsonResponse({
            "error": search_jobs.limit_error(len(keywords), limit, context["is_pro"]),
            "count": len(keywords), **context,
        }, status=400)

    if not context["is_pro"] and search_jobs.active_job() is not None:
        return JsonResponse({"error": search_jobs.FREE_BUSY_MESSAGE, **context}, status=400)

    app = App.objects.filter(id=app_id).first() if app_id else None

    running = run_queue.running_run()
    queued_behind = None
    eta_seconds = None
    if running is not None:
        run_feature, run_row = running
        queued_behind = run_feature.label
        if run_feature.key == search_jobs.FEATURE_KEY:
            queued_behind = "the current search"
            eta_seconds = search_jobs.job_payload(run_row)["eta_seconds"]
    elif run_queue.busy_reason():
        queued_behind = run_queue.busy_reason()

    run_now = request.POST.get("run_now") in ("1", "true", "on")
    job = search_jobs.create_job(app, countries, keywords, run_now=run_now)
    return JsonResponse({
        "job": search_jobs.job_payload(job),
        "queued_behind": queued_behind if job.status == "queued" else None,
        "eta_seconds": eta_seconds if job.status == "queued" else None,
    })


# ---------------------------------------------------------------------------
# Keyword search jobs: what the dashboard panel and the global strip poll
# and press. No Pro gate here - keyword search is a free feature with a
# size limit, and the limit is checked at creation only.
# ---------------------------------------------------------------------------

def _job_or_404(job_id):
    return get_object_or_404(KeywordSearchJob, pk=job_id)


def search_job_current_view(request):
    """The active job (running, paused or queued), the newest finished job
    not yet dismissed, and any other paused searches - without results."""
    panel = search_jobs.panel_job()
    finished = search_jobs.finished_job()
    return JsonResponse({
        "job": search_jobs.job_payload(panel) if panel else None,
        "finished": search_jobs.job_payload(finished) if finished else None,
        "others": [search_jobs.compact_payload(j) for j in search_jobs.other_paused_jobs(panel)],
    })


def search_job_detail_view(request, job_id):
    """One job with its results (cards for the first 50 pairs, the
    opportunity ranking over all of them)."""
    job = _job_or_404(job_id)
    return JsonResponse({"job": search_jobs.job_payload(job, include_results=True)})


@require_POST
def search_job_pause_view(request, job_id):
    job = _job_or_404(job_id)
    updated = KeywordSearchJob.objects.filter(pk=job.pk, status="running").update(
        status="paused", auto_resume=False, throttle_state="normal",
        yielded_for_feature="", yielded_for_id=None, yielded_for_label="",
        progress_message="Paused", current_pair="",
    )
    if not updated:
        return JsonResponse({"error": "This search is not running."}, status=400)
    run_queue.kick()
    job.refresh_from_db()
    return JsonResponse({"job": search_jobs.job_payload(job)})


@require_POST
def search_job_resume_view(request, job_id):
    """Resume a paused search. It re-queues at the back like any re-queued
    run; with ``now=1`` it goes first and pauses whatever keyword search
    runs (the "Resume now" button on a search that stepped aside)."""
    job = _job_or_404(job_id)
    updated = KeywordSearchJob.objects.filter(pk=job.pk, status__in=("paused", "failed")).update(
        status="queued", auto_resume=False, queue_rank=None,
        yielded_for_feature="", yielded_for_id=None, yielded_for_label="",
        error_message="", progress_message="Resuming...",
    )
    if not updated:
        return JsonResponse({"error": "This search is not paused."}, status=400)
    if request.POST.get("now") in ("1", "true", "on"):
        run_queue.run_now(search_jobs.FEATURE_KEY, job.pk)
    else:
        run_queue.kick()
    job.refresh_from_db()
    return JsonResponse({"job": search_jobs.job_payload(job)})


@require_POST
def search_job_discard_view(request, job_id):
    """Discard the rest of a paused search (the researched keywords stay in
    Search History). A queued search leaves the queue instead: deleted when
    it never ran, paused when it already has progress."""
    job = _job_or_404(job_id)
    if job.status == "running":
        return JsonResponse({"error": "Pause the search before discarding the rest."}, status=400)
    if job.status == "queued":
        removed = run_queue.remove_queued(search_jobs.FEATURE_KEY, job.pk)
        if not removed:
            return JsonResponse({"error": "This search already started."}, status=400)
        run_queue.kick()
        job = KeywordSearchJob.objects.filter(pk=job.pk).first()
        return JsonResponse({"job": search_jobs.job_payload(job) if job else None})
    updated = KeywordSearchJob.objects.filter(pk=job.pk, status__in=("paused", "failed")).update(
        status="cancelled", auto_resume=False, finished_at=timezone.now(),
        yielded_for_feature="", yielded_for_id=None, yielded_for_label="",
        progress_message="Discarded the rest", current_pair="",
    )
    if not updated:
        return JsonResponse({"error": "This search already finished."}, status=400)
    run_queue.kick()
    job.refresh_from_db()
    return JsonResponse({"job": search_jobs.job_payload(job, include_results=True)})


@require_POST
def search_job_retry_failed_view(request, job_id):
    """A new search with the keywords that could not be checked."""
    job = _job_or_404(job_id)
    if not job.is_terminal or not job.failed_items:
        return JsonResponse({"error": "Nothing to search again."}, status=400)
    context = search_jobs.limit_context()
    if not context["is_pro"] and search_jobs.active_job() is not None:
        return JsonResponse({"error": search_jobs.FREE_BUSY_MESSAGE, **context}, status=400)
    KeywordSearchJob.objects.filter(pk=job.pk).update(acknowledged=True)
    new_job = search_jobs.retry_failed_job(job)
    return JsonResponse({"job": search_jobs.job_payload(new_job)})


@require_POST
def search_job_dismiss_view(request, job_id):
    """"Done" on a finished search: it does not come back on reload."""
    KeywordSearchJob.objects.filter(
        pk=job_id, status__in=KeywordSearchJob.TERMINAL_STATUSES,
    ).update(acknowledged=True)
    return JsonResponse({"ok": True})


@require_POST
def keyword_cleanup_snooze_view(request):
    """"Remind me in 30 days" on the keyword cleanup banner."""
    from . import ui_state
    from .keyword_cleanup import CLEANUP_SNOOZE_DAYS

    ui_state.snooze(ui_state.KEYWORD_CLEANUP_BANNER, days=CLEANUP_SNOOZE_DAYS)
    return JsonResponse({"ok": True})


# ---------------------------------------------------------------------------
# The run queue (GitHub respectlytics/respectaso#18, #23)
#
# One run executes at a time across the Pro AI tabs and keyword searches, in
# an order the user can change. These endpoints are what every tab polls and
# what the queue panel's controls call. Pro only: the free tier runs one
# keyword search at a time and has no queue to show.
# ---------------------------------------------------------------------------

def _queue_entry(feature, row, **extra):
    """One queue-panel row: who owns the run, what it is, where it lives."""
    described = feature.describe(row)
    return {
        "feature": feature.key,
        "feature_label": feature.label,
        "id": row.pk,
        "label": described["label"],
        "detail": described["detail"],
        "country": described["country"],
        "is_refinement": described["is_refinement"],
        "quote_label": described.get("quote_label", True),
        "url": feature.open_url,
        **extra,
    }


def _queue_target(request):
    """(feature, session_id) from a queue POST, or an error response."""
    feature_key = (request.POST.get("feature") or "").strip()
    feature = run_queue.get_feature(feature_key)
    if feature is None:
        return None, None, JsonResponse({"error": "Unknown feature."}, status=400)
    try:
        session_id = int(request.POST.get("session_id") or "")
    except ValueError:
        return None, None, JsonResponse({"error": "Unknown run."}, status=404)
    return feature, session_id, None


@pro_required_json
def queue_status_view(request):
    """Everything a tab needs to draw its progress panel and the queue."""
    feature = run_queue.get_feature((request.GET.get("feature") or "").strip())
    if feature is None:
        return JsonResponse({"error": "Unknown feature."}, status=400)

    running_here = running_elsewhere = None
    executing_can_yield = False
    running = run_queue.running_run()
    if running is not None:
        run_feature, run_row = running
        executing_can_yield = run_feature.can_yield
        if run_feature.key == feature.key:
            progress = run_feature.progress(run_row) if run_feature.progress else {}
            running_here = _queue_entry(run_feature, run_row, **progress)
        else:
            running_elsewhere = _queue_entry(
                run_feature, run_row,
                progress_percent=run_row.progress_percent,
                progress_message=run_row.progress_message or "",
                can_yield=run_feature.can_yield,
            )

    queued = [
        _queue_entry(queued_feature, queued_row, position=position,
                     can_run_now=executing_can_yield)
        for position, (queued_feature, queued_row)
        in enumerate(run_queue.queued_runs(), start=1)
    ]
    return JsonResponse({
        "feature": feature.key,
        "lane_state": run_queue.lane_state(),
        "busy_with": run_queue.busy_reason() if running is None else None,
        "running_here": running_here,
        "running_elsewhere": running_elsewhere,
        "queued": queued,
    })


@pro_required_json
@require_POST
def queue_remove_view(request):
    """Take one waiting run out of the queue. A run that never started
    leaves no history row; a keyword search with progress is paused."""
    feature, session_id, error = _queue_target(request)
    if error is not None:
        return error
    if run_queue.remove_queued(feature.key, session_id):
        run_queue.kick()
        return JsonResponse({"ok": True})
    exists = feature.model.objects.filter(pk=session_id, **feature.filter_kwargs).exists()
    if not exists:
        return JsonResponse({"error": "Unknown run."}, status=404)
    return JsonResponse(
        {"error": "This run already started - cancel it from its tab instead."},
        status=400,
    )


@pro_required_json
@require_POST
def queue_clear_view(request):
    """Take every waiting run out of the queue. The executing run is never touched."""
    removed = run_queue.clear_queued()
    run_queue.kick()
    return JsonResponse({"ok": True, "removed": removed})


@pro_required_json
@require_POST
def queue_move_view(request):
    """Move a waiting run up, down or to the front (``direction``)."""
    feature, session_id, error = _queue_target(request)
    if error is not None:
        return error
    position = run_queue.move(feature.key, session_id, (request.POST.get("direction") or "").strip())
    if position is None:
        return JsonResponse({"error": "This run is not waiting in the queue."}, status=400)
    return JsonResponse({"position": position})


@pro_required_json
@require_POST
def queue_run_now_view(request):
    """Put a waiting run first and pause the executing run when it can
    step aside (a keyword search can, an AI run cannot)."""
    feature, session_id, error = _queue_target(request)
    if error is not None:
        return error
    result = run_queue.run_now(feature.key, session_id)
    if result is None:
        return JsonResponse({"error": "This run is not waiting in the queue."}, status=400)
    return JsonResponse(result)


def opportunity_view(request):
    """Country Opportunity Finder — search a keyword across all 30 countries."""
    apps = App.objects.all()
    form = OpportunitySearchForm()
    return render(request, "aso/opportunity.html", {"apps": apps, "form": form})


@require_POST
def opportunity_search_country_view(request):
    """AJAX endpoint: search a keyword in a single country.

    Called once per country by the frontend (30 sequential calls).
    """
    keyword = request.POST.get("keyword", "").strip().lower()
    country_code = request.POST.get("country", "").strip().lower()
    app_id = request.POST.get("app_id", "")

    valid_codes = {code for code, _ in COUNTRY_CHOICES}
    if not keyword or country_code not in valid_codes:
        return JsonResponse({"error": "Missing or invalid keyword/country."}, status=400)

    app = None
    if app_id:
        try:
            app = App.objects.get(id=app_id)
        except App.DoesNotExist:
            pass

    itunes_service = ITunesSearchService()
    difficulty_calc = DifficultyCalculator()
    download_est = DownloadEstimator()

    try:
        competitors = itunes_service.search_apps(keyword, country=country_code, limit=25)
    except SearchAPIUnavailableError as e:
        return JsonResponse({"error": str(e)}, status=503)

    difficulty_score, breakdown = difficulty_calc.calculate(
        competitors, keyword=keyword
    )
    pop = resolve_popularity(competitors, keyword, country_code)
    popularity = pop.effective

    download_estimates = download_est.estimate(popularity or 0, country=country_code)
    breakdown["download_estimates"] = download_estimates

    app_rank = None
    if app and app.track_id:
        try:
            app_rank = itunes_service.find_app_rank(
                keyword, app.track_id, country=country_code
            )
        except SearchAPIUnavailableError:
            pass  # Rank is optional

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

    opportunity = calc_opportunity(popularity, difficulty_score)
    top_competitor = competitors[0]["trackName"] if competitors else "—"
    top_ratings = competitors[0].get("userRatingCount", 0) if competitors else 0

    return JsonResponse({
        "country": country_code,
        "popularity": popularity,
        **popularity_fields(pop),
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
    download_est = DownloadEstimator()

    results = []
    errors = []
    for i, (country_code, country_name) in enumerate(COUNTRY_CHOICES):
        if i > 0:
            time.sleep(2)

        try:
            competitors = itunes_service.search_apps(kw_text, country=country_code, limit=25)
        except SearchAPIUnavailableError as e:
            errors.append({"country": country_code, "error": str(e)})
            continue

        difficulty_score, breakdown = difficulty_calc.calculate(
            competitors, keyword=kw_text
        )
        pop = resolve_popularity(competitors, kw_text, country_code)
        popularity = pop.effective

        download_estimates = download_est.estimate(
            popularity or 0,
            country=country_code,
        )
        breakdown["download_estimates"] = download_estimates

        app_rank = None
        if app and app.track_id:
            try:
                app_rank = itunes_service.find_app_rank(
                    kw_text, app.track_id, country=country_code
                )
            except SearchAPIUnavailableError:
                pass  # Rank is optional

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

        opportunity = calc_opportunity(popularity, difficulty_score)
        top_competitor = competitors[0]["trackName"] if competitors else "—"
        top_ratings = competitors[0].get("userRatingCount", 0) if competitors else 0

        results.append({
            "country": country_code,
            "popularity": popularity,
            **popularity_fields(pop),
            "difficulty": difficulty_score,
            "difficulty_label": diff_label,
            "difficulty_breakdown": breakdown,
            "competitors_data": competitors,
            "opportunity": opportunity,
            "app_rank": app_rank,
            "competitor_count": len(competitors),
            "top_competitor": top_competitor,
            "top_ratings": top_ratings,
            "classification": classify_keyword(popularity or 0, difficulty_score),
        })

    results.sort(key=lambda x: x["opportunity"], reverse=True)

    response_data = {
        "keyword": kw_text,
        "app_id": app.id if app else None,
        "results": results,
        "total_countries": len(results),
    }
    if errors:
        response_data["errors"] = errors
        response_data["error_count"] = len(errors)
    return JsonResponse(response_data)


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
        # One entry per keyword+country per day (preserves historical trend data).
        # popularity_score stores the internal estimate; older clients that only
        # send "popularity" fall back to it (their value was internal-only).
        SearchResult.upsert_today(
            keyword=keyword_obj,
            popularity_score=item.get("popularity_internal", item.get("popularity", 0)),
            apple_popularity_score=item.get("popularity_apple"),
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
        # Extract country code from URL (e.g. apps.apple.com/de/app/...)
        country_match = re.search(
            r"apps\.apple\.com/([a-z]{2})/", query, re.IGNORECASE
        )
        country = country_match.group(1).lower() if country_match else "us"
        app_data = itunes_service.lookup_by_id(track_id, country=country)
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
    try:
        results = itunes_service.search_apps(query, limit=5)
    except SearchAPIUnavailableError:
        return JsonResponse(
            {"apps": [], "error": "App Store search is temporarily unavailable."}
        )
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

    # Feedback from the per-app refresh action, which redirects back here.
    refresh_status = request.GET.get("refresh")
    if refresh_status == "renamed":
        message = "App details updated from the App Store."
        message_type = "success"
    elif refresh_status == "current":
        message = "App is already up to date."
        message_type = "success"
    elif refresh_status == "failed":
        message = "Couldn't reach the App Store to refresh. Please try again."
        message_type = "error"

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
def app_refresh_view(request, app_id):
    """Re-sync an app's title, icon, and seller from the App Store.

    Name/icon/seller are a snapshot taken when the app was first added. If the
    developer later renames the app (or changes its icon) on the App Store, the
    stored values go stale. This pulls the current values from iTunes via the
    app's track_id and writes them back to the App row — the single source of
    truth every screen reads from — so the refresh propagates everywhere the
    title is shown. Manual apps (no track_id) can't be refreshed.
    """
    app = get_object_or_404(App, id=app_id)
    if not app.track_id:
        return redirect("aso:apps")

    fresh = ITunesSearchService().lookup_by_id(app.track_id)
    if not fresh:
        return redirect(f"{reverse('aso:apps')}?refresh=failed")

    old_name = app.name
    app.name = fresh.get("trackName") or app.name
    app.icon_url = fresh.get("artworkUrl100") or app.icon_url
    app.seller_name = fresh.get("sellerName") or app.seller_name
    app.save(update_fields=["name", "icon_url", "seller_name"])

    status = "renamed" if app.name != old_name else "current"
    return redirect(f"{reverse('aso:apps')}?refresh={status}")


@require_POST
def keyword_delete_view(request, keyword_id):
    """Delete a keyword and all its search results."""
    keyword = get_object_or_404(Keyword, id=keyword_id)
    keyword.delete()
    return JsonResponse({"success": True})


def _delete_tracking_entries(pairs):
    """Delete the Search History rows identified by (keyword_id, country) pairs.

    Each dashboard row represents a keyword tracked in a country, so for every
    pair we drop EVERY SearchResult snapshot, not just the latest one. Deleting
    only the latest snapshot would silently resurrect the previous one on
    reload, making the click feel like a no-op.

    The pair is the stable row identity — snapshot ids churn because
    SearchResult.upsert_today replaces today's snapshot on refresh, so callers
    must not identify rows by result id when the reference can outlive a
    refresh (e.g. the dashboard's cross-page selection).

    Keywords left with no results in any country are cleaned up to avoid
    orphans. Pairs that no longer exist are skipped. Returns the number of
    tracking entries (dashboard rows) deleted.
    """
    from django.db.models import Q

    requested = set(pairs)
    if not requested:
        return 0

    pair_filter = Q()
    for keyword_id, country in requested:
        pair_filter |= Q(keyword_id=keyword_id, country=country)

    existing = set(SearchResult.objects.filter(pair_filter).values_list("keyword_id", "country"))
    if not existing:
        return 0

    SearchResult.objects.filter(pair_filter).delete()

    Keyword.objects.filter(
        id__in={keyword_id for keyword_id, _ in existing}, results__isnull=True
    ).delete()

    return len(existing)


@require_POST
def result_delete_view(request, result_id):
    """Remove a single Search History row — all snapshots of its (keyword, country) pair.

    See _delete_tracking_entries for the deletion semantics.
    """
    result = get_object_or_404(SearchResult, id=result_id)
    _delete_tracking_entries([(result.keyword_id, result.country)])
    return JsonResponse({"success": True})


@require_POST
def results_bulk_delete_view(request):
    """
    Delete the selected Search History rows.

    POST body: {"entries": [{"keyword_id": int, "country": str}, ...]}

    Entries whose (keyword, country) pair no longer exists (e.g. removed by a
    concurrent action) are skipped, so the endpoint is idempotent; "deleted"
    reflects the tracking entries actually removed.
    """
    try:
        body = json.loads(request.body)
        entries = body.get("entries")
        if not isinstance(entries, list) or not entries:
            raise ValueError
        pairs = []
        for entry in entries:
            keyword_id = entry.get("keyword_id") if isinstance(entry, dict) else None
            country = entry.get("country") if isinstance(entry, dict) else None
            if not isinstance(keyword_id, int) or not isinstance(country, str) or not country:
                raise ValueError
            pairs.append((keyword_id, country))
    except (ValueError, AttributeError, json.JSONDecodeError):
        return JsonResponse(
            {
                "success": False,
                "error": "entries must be a non-empty list of {keyword_id, country} objects.",
            },
            status=400,
        )

    from django.db import transaction

    with transaction.atomic():
        deleted = _delete_tracking_entries(pairs)

    return JsonResponse({"success": True, "deleted": deleted})


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

    try:
        search_result = score_keyword_pair(
            keyword_obj, country,
            itunes_service=ITunesSearchService(),
            difficulty_calc=DifficultyCalculator(),
            download_est=DownloadEstimator(),
        )
    except SearchAPIUnavailableError as e:
        return JsonResponse({"error": str(e)}, status=503)

    app = keyword_obj.app
    pop = search_result.popularity_resolution()
    return JsonResponse({
        "success": True,
        "result": {
            "keyword": keyword_obj.keyword,
            "keyword_id": keyword_obj.pk,
            "result_id": search_result.pk,
            "popularity_score": pop.effective,
            **popularity_fields(pop),
            "difficulty_score": search_result.difficulty_score,
            "difficulty_label": search_result.difficulty_label,
            "difficulty_color": search_result.difficulty_color,
            "country": country,
            "searched_at": search_result.searched_at.strftime("%b %d, %H:%M"),
            "app_rank": search_result.app_rank,
            "app_name": app.name if app else None,
        },
    })


def export_history_csv_view(request):
    """
    Export search history as a CSV file.

    Supports the same filters as the dashboard: app, country, insight,
    pop_min, diff_max.  Only the latest result per keyword+country is
    exported (matching the dashboard table).
    """
    app_id = request.GET.get("app")
    country = request.GET.get("country")
    insight_filter = request.GET.getlist("insight")
    pop_min_raw = request.GET.get("pop_min")
    diff_max_raw = request.GET.get("diff_max")
    search_q = request.GET.get("q", "").strip()

    pop_min = int(pop_min_raw) if pop_min_raw and pop_min_raw.isdigit() else None
    diff_max = int(diff_max_raw) if diff_max_raw and diff_max_raw.isdigit() else None

    from django.db.models import Max

    # Deduplicate: keep only the latest result per keyword+country
    latest_filter = {}
    if app_id:
        latest_filter["keyword__app_id"] = app_id
    if country:
        latest_filter["country"] = country.lower()

    latest_ids = list(
        SearchResult.objects
        .filter(**latest_filter)
        .values("keyword_id", "country")
        .annotate(latest_id=Max("id"))
        .values_list("latest_id", flat=True)
    )

    results_qs = (
        SearchResult.objects
        .filter(id__in=latest_ids)
        .select_related("keyword", "keyword__app")
    )

    # Apply keyword text search
    if search_q:
        results_qs = results_qs.filter(keyword__keyword__icontains=search_q)

    # Apply popularity / difficulty filters (on the EFFECTIVE popularity —
    # the value the user sees, per their source selection)
    results_qs = annotate_effective_popularity(results_qs)
    if pop_min is not None:
        results_qs = results_qs.filter(
            effective_pop__isnull=False,
            effective_pop__gte=pop_min,
        )
    if diff_max is not None:
        results_qs = results_qs.filter(
            difficulty_score__isnull=False,
            difficulty_score__lte=diff_max,
        )

    # Apply insight filter using stored classification column
    valid_insights = [i for i in insight_filter if i in CLASSIFICATION_LABELS]
    if valid_insights:
        results_qs = results_qs.filter(classification__in=valid_insights)

    results_qs = results_qs.order_by("-searched_at")

    # Determine export mode: summary (default) or with competitor apps
    include_apps = request.GET.get("include_apps", "").strip().lower()
    apps_limit = {"top5": 5, "top10": 10}.get(include_apps, 0)

    if apps_limit:
        filename = "respectaso-search-history-with-apps.csv"
    else:
        filename = "respectaso-search-history.csv"

    response = HttpResponse(content_type="text/csv")
    response["Content-Disposition"] = f'attachment; filename="{filename}"'

    writer = csv.writer(response)

    base_columns = [
        "Keyword", "App", "Country", "Popularity",
        "Popularity (RespectASO)", "Popularity (Apple Ads)",
        "Popularity Source", "Popularity Fallback",
        "Apple Popularity Trend",
        "Difficulty", "Difficulty Label", "Opportunity", "Insight", "Rank",
        "Competitors", "Date",
    ]
    app_columns = [
        "Competitor Position", "Competitor App", "Competitor Seller",
        "Competitor Rating", "Competitor Ratings Count",
        "Competitor Genre", "Competitor Price",
        "Competitor App Store URL",
    ]
    writer.writerow(base_columns + app_columns if apps_limit else base_columns)

    export_results = list(results_qs)
    _attach_apple_trends(export_results)
    for r in export_results:
        # "Popularity" is the effective value (per the user's source
        # selection); the per-source columns carry both raw values.
        effective = r.effective_popularity
        pop = effective if effective is not None else ""
        opportunity = (
            calc_opportunity(effective, r.difficulty_score)
            if effective is not None
            else ""
        )
        base_row = [
            r.keyword.keyword,
            r.keyword.app.name if r.keyword.app else "",
            r.country.upper() if r.country else "",
            pop,
            r.popularity_score if r.popularity_score is not None else "",
            r.apple_popularity_score if r.apple_popularity_score is not None else "",
            r.popularity_source_used,
            "yes" if r.popularity_is_fallback else "no",
            r.apple_trend if r.apple_trend is not None else "",
            r.difficulty_score,
            r.difficulty_label,
            opportunity,
            r.classification,
            r.app_rank if r.app_rank else "",
            len(r.competitors_data) if r.competitors_data else 0,
            r.searched_at.strftime("%Y-%m-%d %H:%M") if r.searched_at else "",
        ]

        if not apps_limit:
            writer.writerow(base_row)
        else:
            competitors = (r.competitors_data or [])[:apps_limit]
            if not competitors:
                writer.writerow(base_row + [""] * len(app_columns))
            else:
                for idx, comp in enumerate(competitors, 1):
                    writer.writerow(base_row + [
                        idx,
                        comp.get("trackName", ""),
                        comp.get("sellerName", ""),
                        comp.get("averageUserRating", ""),
                        comp.get("userRatingCount", ""),
                        comp.get("primaryGenreName", ""),
                        comp.get("formattedPrice", ""),
                        comp.get("trackViewUrl", ""),
                    ])

    # Respectlytics attribution row
    writer.writerow([])
    writer.writerow(["Privacy-first mobile analytics - https://respectlytics.com"])

    return response


@require_POST
def keywords_bulk_refresh_view(request):
    """
    Refresh keyword+country pairs that already have results, scoped by
    the user's current app and country filters.  Runs in a background
    thread so the user can navigate away safely.  The dashboard progress
    bar polls ``auto_refresh_status`` to show live progress.

    POST body: {"app_id": int|null, "country": str|""}
      - app_id=null  → all keywords (every app + unassigned)
      - app_id=<int> → only keywords linked to that app
      - country=""   → all countries
      - country="fr"  → only that country
    """
    body = json.loads(request.body)
    app_id = body.get("app_id")
    country = (body.get("country") or "").strip().lower()

    from django.db.models import Max

    # Find every keyword+country pair that already has at least one
    # SearchResult.  This prevents the bug where keywords from other
    # countries get scored in the wrong country.
    base_qs = SearchResult.objects.all()
    if app_id:
        base_qs = base_qs.filter(keyword__app_id=app_id)
    # app_id=null means "all" — no app filter applied

    if country:
        base_qs = base_qs.filter(country=country)

    pairs = list(
        base_qs
        .values("keyword_id", "country")
        .annotate(_latest=Max("id"))
        .values_list("keyword_id", "country")
    )

    if not pairs:
        return JsonResponse({"success": True, "started": False, "total": 0})

    from .scheduler import get_status, run_manual_refresh

    status = get_status()
    if status["running"]:
        return JsonResponse({"success": False, "error": "A refresh is already in progress."})
    running = run_queue.running_run()
    if running is not None:
        return JsonResponse({
            "success": False,
            "error": f"{running[0].label} is running. Refresh when it finishes.",
        }, status=400)

    if not run_manual_refresh(pairs):
        return JsonResponse({"success": False, "error": "A refresh is already in progress."})
    return JsonResponse({"success": True, "started": True, "total": len(pairs)})


def version_check_view(request):
    """Report whether a newer release exists, for the update banner.

    Every page load calls this. GitHub is asked at most once every ten
    minutes (see aso/update_check.py); pages in between get the cached
    answer, so an active session can never exhaust GitHub's rate limit.
    """
    return JsonResponse(update_check.check_for_update())


def auto_refresh_status_view(request):
    """Return the current auto-refresh progress as JSON."""
    from .scheduler import get_status
    return JsonResponse(get_status())


GITHUB_RELEASES_URL = "https://github.com/respectlytics/respectaso/releases/latest"


def download_dmg_view(request):
    """Redirect to the latest .dmg as a direct file download.

    Reuses the update banner's cached GitHub answer (aso/update_check.py),
    so a click never adds a GitHub request of its own. When the latest
    release is not known, the GitHub releases page is the fallback.
    """
    return redirect(update_check.check_for_update().get("download_url") or GITHUB_RELEASES_URL)


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

    from .popularity import get_popularity_source

    data_points = []
    for r in qs:
        data_points.append({
            "date": r.searched_at.strftime("%Y-%m-%d"),
            "date_display": r.searched_at.strftime("%b %d"),
            # "popularity" stays the effective value (primary chart line);
            # both raw series ride along for the secondary dashed line.
            "popularity": r.effective_popularity,
            "popularity_internal": r.popularity_score,
            "popularity_apple": r.apple_popularity_score,
            "difficulty": r.difficulty_score,
            "rank": r.app_rank,
            "country": r.country,
        })

    return JsonResponse({
        "keyword": keyword_obj.keyword,
        "keyword_id": keyword_obj.pk,
        "app_name": keyword_obj.app.name if keyword_obj.app else None,
        "popularity_source": get_popularity_source() or "internal",
        "data_points": data_points,
    })


def pro_promo_researcher_view(request):
    """Promotional page for AI Niche Researcher (free version)."""
    return render(request, "aso/pro_promo/ai_researcher.html")


def pro_promo_top_terms_view(request):
    """Promotional page for Top Search Terms (free version)."""
    return render(request, "aso/pro_promo/top_terms.html")


def pro_promo_competitor_view(request):
    """Promotional page for AI Competitor Analyzer (free version)."""
    return render(request, "aso/pro_promo/ai_competitor.html")


def pro_promo_simulator_view(request):
    """Promotional page for ASO Score Simulator (free version)."""
    return render(request, "aso/pro_promo/simulator.html")
