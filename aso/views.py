import csv
import json
import logging

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST

from . import (
    app_profiles,
    countries,
    history_filters,
    history_revision,
    history_selection,
    keyword_labels,
    pair_hooks,
    run_queue,
    search_jobs,
    ui_memory,
    update_check,
)
from .dashboard_summary import compute_app_summary
from .forms import AppForm, KeywordSearchForm
from .history_filters import (
    HISTORY_PER_PAGE_CHOICES,
    HISTORY_PER_PAGE_DEFAULT,
    HistoryFilters,
)
from .keyword_scoring import score_keyword_pair
from .models import App, Keyword, KeywordSearchJob, SearchResult
from .popularity import (
    popularity_fields,
)
from .scoring import (
    calc_opportunity,
    scoring_guide,
    sentence_app_name,
)
from .services import (
    APP_STORE_UNAVAILABLE,
    DifficultyCalculator,
    DownloadEstimator,
    ITunesAPIError,
    ITunesSearchService,
)

logger = logging.getLogger(__name__)


# app_rank is now persisted directly on SearchResult during search/refresh.
# No need for a helper to find rank in stored competitors.

def methodology_view(request):
    """Our Methodology page — explains how RespectASO works.

    Every section is hand written except Country coverage, which is generated
    from aso/countries.py. A hand written storefront table would be wrong the
    day a storefront changes, and this page is where a reader goes to find out
    what a number means.
    """
    by_region = countries.by_region()
    covered = countries.APPLE_ADS_STOREFRONTS & countries.CODES
    no_language = countries.without_app_store_language()
    return render(request, "aso/methodology.html", {
        # The ranks per difficulty tier and the opportunity scale, from the
        # scoring code, so this page cannot describe a model the app no
        # longer runs.
        "scoring_guide": scoring_guide(),
        "coverage_regions": by_region,
        "coverage_total": len(countries.CODES),
        "coverage_apple_known": bool(covered),
        "coverage_apple_count": len(covered),
        "coverage_apple_codes": covered,
        "coverage_language_count": len(countries.CODES) - len(no_language),
        "coverage_no_language_count": len(no_language),
        "coverage_measured_count": sum(
            1 for c in countries.COUNTRIES.values() if c.market_source == "measured"
        ),
    })


def whats_new_view(request):
    """What's New page — the app's release history, newest first.

    Opening the page counts as having seen the current version's notes,
    which clears the one-time update notice.
    """
    from .copy_rules import no_dash_in_markup, walk_prose
    from .release_notes import RELEASES, mark_seen

    mark_seen()
    # The notes are the release history, written once and never rewritten;
    # the page still reads without a dash between words (aso/copy_rules.py).
    releases = walk_prose(RELEASES, no_dash_in_markup, keep=("version", "date", "kind", "notice"))
    return render(request, "aso/whats_new.html", {"releases": releases})


@require_POST
def whats_new_seen_view(request):
    """Dismiss the one-time update notice without opening the page."""
    from .release_notes import mark_seen

    mark_seen()
    return JsonResponse({"ok": True})


@require_POST
def apple_stale_banner_dismiss_view(request):
    """Hide the soft notice that Apple rejected credentials which used to
    work (partials/popularity_banner.html), for the rejection stored now.

    Kept per install in aso.ui_state, so it stays hidden after a restart; a
    later rejection has a new time and shows the notice again.
    """
    from . import ui_state
    from .apple_ads import storage

    rejected_at = storage.load_apple_settings()["apple_ads"]["credentials_rejected_at"]
    ui_state.dismiss(ui_state.apple_stale_banner_key(rejected_at))
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

    A full page load without a view in its address (the nav link, any link
    to the bare Dashboard) is sent to the view last shown, kept in the
    session by aso/ui_memory.py; the address then carries it, as it does
    after every in-place update.
    """
    restored = ui_memory.restored_dashboard_query(request)
    if restored is not None:
        return redirect(f"{request.path}?{restored}")
    ui_memory.remember_dashboard_view(request)

    # Read before any row. A change that lands while the page is drawn is
    # then in the rows but not in this count, and the page's next check only
    # fetches once more. Read after the rows, the same change would be
    # counted as drawn when it never was.
    revision = history_revision.current()

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
    selected_app_obj = None
    if app_id:
        selected_app_obj = App.objects.filter(id=app_id).first()
        if selected_app_obj:
            selected_app_name = selected_app_obj.name

    # --- Filters (search text, Insight, tags, popularity, difficulty) ---
    # Read and applied by aso/history_filters.py, as the CSV export does.
    filters = HistoryFilters.from_query(request.GET)

    from django.db.models import Case, IntegerField, Max, Value, When
    from django.db.models.functions import Lower

    latest_filter = {}
    if app_id:
        latest_filter["keyword__app_id"] = app_id
    if country_filter:
        latest_filter["country"] = country_filter.lower()

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
    # The latest result of each keyword and country pair
    latest_ids = history_filters.latest_ids(filters)

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

    # Total unfiltered count (before the filters that hide rows)
    total_unfiltered_count = results_qs.count()
    results_qs = history_filters.narrow(results_qs, filters)

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

    # The user's own labels on each row's keyword (aso/keyword_labels.py)
    labels_by_keyword = keyword_labels.names_by_keyword({r.keyword_id for r in history_results})
    for result in history_results:
        result.label_names = labels_by_keyword.get(result.keyword_id, [])
    # The Labels filter offers the labels in this app's table, and keeps one that
    # is chosen even when no row carries it any more, so it can be cleared.
    label_choices = keyword_labels.names_in_use(app_id=filters.app_id)
    label_choices += [t for t in filters.labels if t not in label_choices]

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
    from .scheduler import bulk_refresh_question

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

    try:
        from . import setup_checklist

        setup = setup_checklist.checklist(request)
    except Exception:  # noqa: BLE001 (the checklist must never break the page)
        setup = None

    # First run: no app and nothing tracked yet (KEYWORDS_PAGE_PLAN.md M2.5).
    welcome = not apps.exists() and not SearchResult.objects.exists()

    # The table's columns (KEYWORDS_PAGE_PLAN.md M1.2): Country only when the
    # rows in view span more than one storefront.
    show_country_column = len(available_countries) > 1 and not country_filter
    history_colspan = 8 + (1 if show_rank else 0) + (1 if show_country_column else 0)

    return render(
        request,
        "aso/dashboard.html",
        {
            "apps": apps,
            # "Get set up" (aso/setup_checklist.py), None when every step is done
            "setup": setup,
            "welcome": welcome,
            "search_form": search_form,
            # App Summary panel (None when hidden)
            "app_summary": app_summary,
            # Folded or open, as this visitor left it (aso/ui_memory.py)
            "app_summary_folded": ui_memory.app_summary_folded(request),
            # History table context
            "history_results": history_results,
            "keyword_count": keyword_count,
            "selected_app": int(app_id) if app_id else None,
            "selected_app_name": selected_app_name,
            # The app switcher beside the title, each app with its logo
            # (aso/partials/country_picker.html with items)
            "app_menu": [
                {"value": "", "label": "All apps", "glyph": "all", "selected": not app_id},
                *({"value": str(app.pk), "label": app.name, "icon": app.icon_url or "",
                   "selected": str(app.pk) == str(app_id)} for app in apps),
                {"value": "__manage__", "label": "Add or remove apps…", "glyph": "manage"},
            ],
            "selected_country": country_filter,
            "available_countries": list(available_countries),
            # What Refresh Rankings refreshes in this view, for its confirm.
            "refresh_question": bulk_refresh_question(
                selected_app_obj, country_filter or "", available_countries,
            ),
            "show_rank": show_rank,
            "show_country_column": show_country_column,
            "history_colspan": history_colspan,
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
            "selected_insights": list(filters.insights),
            "selected_labels": list(filters.labels),
            "label_choices": label_choices,
            # The filters as a query, for links that keep the view (Prev, Next)
            "history_query": filters.query_string(),
            # Built from the scoring code, so the guide cannot grade a number
            # the rows beneath it grade differently.
            "scoring_guide": scoring_guide(),
            "selected_pop_min": filters.pop_min,
            "selected_diff_max": filters.diff_max,
            "search_q": filters.q,
            "has_filters": filters.narrowing,
            # Keyword search jobs (aso/search_jobs.py)
            "search_job": search_job_bootstrap,
            "keyword_limit_context": search_jobs.limit_context(),
            "cleanup": cleanup,
            # What the sections were drawn from (aso/history_revision.py)
            "history_revision": revision,
            # The rows ticked in the table, kept on the server (aso/history_selection.py)
            "history_selection": history_selection.state(request),
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
    """"Close" on a finished search: it does not come back on reload, and
    no older search takes its place (search_jobs.finished_job)."""
    KeywordSearchJob.objects.filter(
        pk=job_id, status__in=KeywordSearchJob.TERMINAL_STATUSES,
    ).update(acknowledged=True)
    return JsonResponse({"ok": True})


@require_POST
def time_zone_view(request):
    """The browser tells the app its time zone (base.html), so every day in
    Search History is the reader's own (aso/local_day.py).

    POST body: {"time_zone": "Europe/Stockholm"}. When the zone changes, the
    stored history is regrouped into the new days in the background, and
    the Dashboard follows through its change count.
    """
    from . import local_day

    try:
        name = json.loads(request.body or b"{}").get("time_zone")
        changed = local_day.remember(name)
    except (ValueError, AttributeError, json.JSONDecodeError):
        return JsonResponse({"success": False, "error": "That is not a time zone."}, status=400)
    if changed:
        import threading

        from .popularity import tidy_history_days

        threading.Thread(target=tidy_history_days, daemon=True, name="history-days").start()
    return JsonResponse({"success": True, "changed": changed, "time_zone": local_day.zone_name()})


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


def _finished_run(spec):
    """How the run ``"<feature>:<id>"`` ended, for the activity indicator on
    pages that did not start it; None while it has not ended or is unknown."""
    feature_key, _sep, raw_id = spec.partition(":")
    feature = run_queue.get_feature(feature_key) if feature_key else None
    if feature is None or not raw_id.isdigit():
        return None
    row = feature.model.objects.filter(pk=int(raw_id), **feature.filter_kwargs).first()
    if row is None or row.status not in ("completed", "failed", "cancelled"):
        return None
    url = feature.open_url if feature.key == "keyword_search" else f"{feature.open_url}?run={row.pk}"
    return {"feature": feature.key, "id": row.pk, "status": row.status,
            "label": feature.describe(row)["label"], "url": url}


def queue_status_view(request):
    """Everything a tab needs to draw its progress panel and the queue.

    In every edition: the queue holds keyword searches and country scans too,
    so everyone sees what runs and what waits, and can reorder it (the owner,
    2026-10-02); the AI runs in it exist only with Pro.

    Without ``feature`` (a page that owns no runs, such as Apps or Settings)
    every running run is "elsewhere": the activity indicator in the top bar
    shows it on every page."""
    raw = (request.GET.get("feature") or "").strip()
    feature = run_queue.get_feature(raw) if raw else None
    if raw and feature is None:
        return JsonResponse({"error": "Unknown feature."}, status=400)

    running_here = running_elsewhere = None
    executing_can_yield = False
    running = run_queue.running_run()
    if running is not None:
        run_feature, run_row = running
        executing_can_yield = run_feature.can_yield
        if feature is not None and run_feature.key == feature.key:
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
        "feature": feature.key if feature is not None else "",
        "finished": _finished_run(request.GET.get("finished") or ""),
        "lane_state": run_queue.lane_state(),
        "busy_with": run_queue.busy_reason() if running is None else None,
        "running_here": running_here,
        "running_elsewhere": running_elsewhere,
        "queued": queued,
    })


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
        {"error": "This run already started. Cancel it from its tab instead."},
        status=400,
    )


@require_POST
def queue_clear_view(request):
    """Take every waiting run out of the queue. The executing run is never touched."""
    removed = run_queue.clear_queued()
    run_queue.kick()
    return JsonResponse({"ok": True, "removed": removed})


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


def app_lookup_view(request):
    """
    AJAX endpoint: search the App Store for apps by name or URL.

    Accepts GET parameter 'q' — either:
      - An App Store URL (https://apps.apple.com/...id123456789)
      - A search query (app name)

    Returns JSON list of matching apps with icon, name, bundle_id, track_id.
    A bare App Store ID finds that app too (ITunesSearchService.find_apps).
    """
    query = request.GET.get("q", "").strip()
    if not query or len(query) < 2:
        return JsonResponse({"apps": []})

    try:
        results = ITunesSearchService().find_apps(query)
    except ITunesAPIError:
        # Apple cannot be reached or is busy: say so, never "no apps found".
        return JsonResponse(
            {"apps": [], "error": APP_STORE_UNAVAILABLE}
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
                    "genre": r.get("primaryGenreName") or "",
                    "shortName": sentence_app_name(r["trackName"]),
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
    elif refresh_status in ("missing", "unanswered"):
        message = _refresh_problem(refresh_status, request.GET)
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
    app.delete()
    return redirect("aso:apps")


# How many of an app's storefronts a refresh asks in turn while Apple has
# no listing there: a person waits on the answer.
REFRESH_STOREFRONTS = 3


def _refresh_problem(status, query) -> str:
    """The one sentence a refresh that found nothing shows, naming the app:
    Apple has no listing for it where it is known ("missing"), or Apple did
    not answer ("unanswered")."""
    try:
        app = App.objects.filter(pk=int(query.get("app") or 0)).first()
    except (TypeError, ValueError):
        app = None
    name = sentence_app_name(app.name) if app else "this app"
    if status == "missing":
        where = countries.stores_phrase(countries.clean((query.get("in") or "").split(",")))
        return f"Apple has no listing for {name} in {where}."
    return f"Apple did not answer about {name}; try again in a few minutes."


@require_POST
def app_refresh_view(request, app_id):
    """Re-sync an app's title, icon, and seller from the App Store.

    Name/icon/seller are a snapshot taken when the app was first added. If the
    developer later renames the app (or changes its icon) on the App Store, the
    stored values go stale. This pulls the current values from iTunes via the
    app's track_id and writes them back to the App row (the single source of
    truth every screen reads from), so the refresh propagates everywhere the
    title is shown. Manual apps (no track_id) can't be refreshed.

    It looks in the storefronts the app is known in (app_profiles.
    known_storefronts: its link's, then those its profile was read in), up
    to REFRESH_STOREFRONTS of them in turn while Apple has no listing. Until
    2.28.1 it looked only in the United States and said "Couldn't reach the
    App Store" for an app Apple lists only elsewhere. Apple having no
    listing and Apple not answering are told apart (_refresh_problem).
    """
    app = get_object_or_404(App, id=app_id)
    if not app.track_id:
        return redirect("aso:apps")

    itunes = ITunesSearchService()
    asked = []
    fresh = None
    for country in app_profiles.known_storefronts(app)[:REFRESH_STOREFRONTS]:
        asked.append(country)
        # A person waits on the page: one call each, no retries (lookup_by_id).
        try:
            fresh = itunes.lookup_by_id(app.track_id, country=country, retry=False)
        except ITunesAPIError:
            return redirect(f"{reverse('aso:apps')}?refresh=unanswered&app={app.pk}")
        if fresh:
            break
    if not fresh:
        return redirect(f"{reverse('aso:apps')}?refresh=missing&app={app.pk}&in={','.join(asked)}")

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

    # Features that follow some of these pairs (the Pro build's Rival
    # Tracker) stop following them: the row the user deleted is the one
    # they followed.
    pair_hooks.pairs_removed(existing)

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


# --- Labels on keywords (aso/keyword_labels.py) ---
# The Labels dialog of the Dashboard's multi-select bar and the x on a row's
# chip. A label belongs to the keyword, so the ids are keyword ids.

def _label_request(request):
    """(keyword ids, label name) from a label request; ValueError when there
    are no keyword ids."""
    body = json.loads(request.body or b"{}")
    ids = body.get("keyword_ids")
    if not isinstance(ids, list) or not ids:
        raise ValueError
    return ids, body.get("name", "")


def _label_request_invalid():
    return JsonResponse({"success": False, "error": "Select at least one keyword first."}, status=400)


@require_POST
def keyword_labels_add_view(request):
    """Put a label on keywords. POST body: {"keyword_ids": [int], "name": str}"""
    try:
        ids, name = _label_request(request)
    except (ValueError, AttributeError, json.JSONDecodeError):
        return _label_request_invalid()
    try:
        name, added = keyword_labels.add(ids, name)
    except keyword_labels.LabelError as e:
        return JsonResponse({"success": False, "error": str(e)}, status=400)
    return JsonResponse({"success": True, "name": name, "added": added})


@require_POST
def keyword_labels_remove_view(request):
    """Take a label off keywords. POST body: {"keyword_ids": [int], "name": str}"""
    try:
        ids, name = _label_request(request)
    except (ValueError, AttributeError, json.JSONDecodeError):
        return _label_request_invalid()
    return JsonResponse({"success": True, "removed": keyword_labels.remove(ids, name)})


@require_POST
def keyword_labels_of_view(request):
    """The labels these keywords carry, with how many of them carry each, and
    every label in use, to suggest. POST body: {"keyword_ids": [int]}"""
    try:
        ids, _ = _label_request(request)
    except (ValueError, AttributeError, json.JSONDecodeError):
        return _label_request_invalid()
    return JsonResponse({
        "success": True,
        "labels": keyword_labels.labels_of(ids),
        "in_use": keyword_labels.names_in_use(),
    })


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


@require_http_methods(["GET", "POST"])
def history_selection_view(request):
    """The Dashboard's ticked rows (aso/history_selection.py).

    GET answers {"seq": n, "entries": [{"pair": "12:us", "kw": "..."}]}.
    POST takes {"add": ["12:us"], "remove": [...], "clear": true}, any of
    them, applies it and answers the same way.
    """
    if request.method == "GET":
        return JsonResponse(history_selection.state(request))
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:  # json.JSONDecodeError and bad UTF-8 both are
        body = None
    lists = [body.get(name, []) for name in ("add", "remove")] if isinstance(body, dict) else None
    if lists is None or not all(
        isinstance(items, list) and all(isinstance(item, str) for item in items) for items in lists
    ):
        return JsonResponse(
            {"error": "Send add and remove as lists of rows such as \"12:us\"."}, status=400,
        )
    add, remove = lists
    return JsonResponse(history_selection.change(request, add=add, remove=remove, clear=bool(body.get("clear"))))


@require_POST
def ui_memory_view(request):
    """Something the page asks the app to remember for this visitor
    (aso/ui_memory.py), sent by static/js/ui-memory.js.

    POST {"name": "app_summary_folded", "value": true} or
    {"name": "countries.search", "value": ["us", "de"]}; answers
    {"ok": true, "value": <as stored>}, or 400 with an error.
    """
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:  # json.JSONDecodeError and bad UTF-8 both are
        body = None
    if not isinstance(body, dict):
        return JsonResponse({"error": "Send a name and a value."}, status=400)
    try:
        value = ui_memory.save(request, body.get("name"), body.get("value"))
    except ValueError as e:
        return JsonResponse({"error": str(e)}, status=400)
    return JsonResponse({"ok": True, "value": value})


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
        # The user asked for fresh numbers: Apple is asked again, and the
        # new read replaces the day for this row and every twin.
        search_result = score_keyword_pair(
            keyword_obj, country,
            itunes_service=ITunesSearchService(),
            difficulty_calc=DifficultyCalculator(),
            download_est=DownloadEstimator(),
            force=True,
        )
    except ITunesAPIError:
        # Apple cannot be reached or is busy (a busy answer used to end in a 500).
        return JsonResponse({"error": APP_STORE_UNAVAILABLE}, status=503)

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

    The rows the Dashboard's table shows under the same filters (app,
    country, search text, Insight, tags, popularity, difficulty), read and
    applied by aso/history_filters.py: the latest result per keyword and
    country, newest first.
    """
    filters = HistoryFilters.from_query(request.GET)
    results_qs = history_filters.narrow(
        SearchResult.objects
        .filter(id__in=history_filters.latest_ids(filters))
        .select_related("keyword", "keyword__app"),
        filters,
    ).order_by("-searched_at")

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
        "Keyword", "App", "Labels", "Country", "Popularity",
        "Popularity (RespectASO)", "Popularity (Apple Ads)",
        "Popularity Source", "Popularity Fallback",
        "Apple Popularity Trend",
        "Difficulty", "Difficulty Label", "Opportunity",
        "Scored For", "App Ratings Used", "Expected Rank",
        "Downloads/Day at Expected Rank", "Downloads/Day at #1",
        "Insight", "Rank", "Competitors", "Date",
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
    labels_by_keyword = keyword_labels.names_by_keyword({r.keyword_id for r in export_results})
    for r in export_results:
        # "Popularity" is the effective value (per the user's source
        # selection); the per-source columns carry both raw values.
        effective = r.effective_popularity
        pop = effective if effective is not None else ""
        opportunity = (
            calc_opportunity(effective, r.difficulty_score, r.country,
                             app=r.app_profile, app_rank=r.app_rank, keyword=r.keyword_text)
            if effective is not None
            else ""
        )
        # The working behind the score, the same figures the table shows
        # under it: the rank whose taps the app can expect, and what it pays.
        if effective is not None:
            reach = r.opportunity_reach
            expected_rank = reach["position"]
            at_expected = round(reach["downloads"], 6)
            profile = r.app_profile
            known = profile is not None and profile.known
            ratings_used = profile.ratings if known else None
            scored_for_cell = r.app.name if known else "new app"
            first = (r.effective_download_estimates or {}).get("positions") or []
            at_first = (
                f"{first[0]['downloads_low']}-{first[0]['downloads_high']}"
                if first else ""
            )
        else:
            expected_rank = at_expected = at_first = ""
            ratings_used, scored_for_cell = None, ""
        base_row = [
            r.keyword.keyword,
            r.keyword.app.name if r.keyword.app else "",
            "; ".join(labels_by_keyword.get(r.keyword_id, [])),
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
            scored_for_cell,
            "" if ratings_used is None else ratings_used,
            expected_rank,
            at_expected,
            at_first,
            r.classification,
            r.app_rank if r.app_rank else "",
            len(r.competitors_data) if r.competitors_data else 0,
            timezone.localtime(r.searched_at).strftime("%Y-%m-%d %H:%M") if r.searched_at else "",
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
    writer.writerow(["Privacy-first mobile analytics: https://respectlytics.com"])

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

    The pairs and the start come from aso/scheduler.py, which the Mac app's
    menu bar Update All Tracked Keywords uses too.
    """
    from .scheduler import bulk_refresh_pairs, start_bulk_refresh

    body = json.loads(request.body)
    pairs = bulk_refresh_pairs(
        app_id=body.get("app_id"),
        country=(body.get("country") or "").strip().lower(),
    )
    outcome, message = start_bulk_refresh(pairs)
    if outcome == "nothing":
        return JsonResponse({"success": True, "started": False, "total": 0})
    if outcome == "run_busy":
        return JsonResponse({"success": False, "error": message}, status=400)
    if outcome == "refreshing":
        return JsonResponse({"success": False, "error": message})
    return JsonResponse({"success": True, "started": True, "total": len(pairs)})


def version_check_view(request):
    """Report whether a newer release exists, for the update banner.

    Every page load calls this. GitHub is asked at most once every ten
    minutes (see aso/update_check.py); pages in between get the cached
    answer, so an active session can never exhaust GitHub's rate limit.
    """
    return JsonResponse(update_check.check_for_update())


def auto_refresh_status_view(request):
    """The ranking refresh's progress, and the change count the Dashboard
    compares with the one its sections were drawn from."""
    from .scheduler import get_status
    return JsonResponse({**get_status(), "history_revision": history_revision.current()})


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
        # The reader's own day (aso/local_day.py, activated per request).
        read_at = timezone.localtime(r.searched_at)
        data_points.append({
            "date": read_at.strftime("%Y-%m-%d"),
            "date_display": read_at.strftime("%b %d"),
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
    """Free edition: the AI Niche Researcher tab shows the result page with sample
    data and the keywords locked (aso/ai_tools_preview.py)."""
    from .ai_tools_preview import STATE_FREE, context

    return render(request, "aso/ai_tool_preview.html", context("researcher", STATE_FREE))


def pro_promo_top_terms_view(request):
    """Top Search Terms in the free edition: the sample-data preview of the
    Pro page, with the search terms blurred (aso/top_terms_preview.py)."""
    from .top_terms_preview import STATE_FREE, preview_context

    return render(
        request, "aso/top_terms_preview.html", preview_context(STATE_FREE)
    )


def pro_promo_rival_tracker_view(request):
    """Free edition: the Rival Tracker tab shows what the Pro page looks
    like, with sample data and the app names and keywords locked
    (aso/rival_tracker_preview.py)."""
    from .rival_tracker_preview import STATE_FREE, preview_context

    return render(
        request, "aso/rival_tracker_preview.html", preview_context(STATE_FREE)
    )


def pro_promo_competitor_view(request):
    """Free edition: the AI Competitor Analyzer tab shows the result page with sample
    data and the keywords locked (aso/ai_tools_preview.py)."""
    from .ai_tools_preview import STATE_FREE, context

    return render(request, "aso/ai_tool_preview.html", context("competitor", STATE_FREE))


def pro_promo_simulator_view(request):
    """Free edition: the ASO Score Simulator tab shows the result page with sample
    data and the keywords locked (aso/ai_tools_preview.py)."""
    from .ai_tools_preview import STATE_FREE, context

    return render(request, "aso/ai_tool_preview.html", context("simulator", STATE_FREE))
