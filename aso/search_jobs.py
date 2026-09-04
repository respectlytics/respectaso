"""Keyword searches as persistent, resumable background jobs.

A search from the Keyword Research tab is a ``KeywordSearchJob`` row: the
keywords, the countries and a cursor. The run queue (``aso/run_queue.py``)
executes one job at a time on a daemon thread; ``execute_job`` works through
the pairs in keyword-major order, writes the cursor and the counters after
every pair, and stops at the next keyword boundary whenever the row's status
is no longer "running" (Pause, Discard, Run now). Because everything the
job needs is in the row, it continues after the app was quit, after a crash
and after a container restart - ``requeue_interrupted`` puts it back at the
front of the queue on the next start.

Limits: 1,000 keywords per search and the queue with a Pro license, 3 per
search and one search at a time without (``keyword_limit``). The limit is
an error with a number, never a silent cut.

Ships in the free-tier ``aso`` app: no ``aso_pro`` or ``licensing`` imports
at module level (``aso.pro_access`` does the license check).
"""

from __future__ import annotations

import logging
import time

import requests
from django.apps import apps as django_apps
from django.conf import settings
from django.db.models import F
from django.urls import reverse
from django.utils import timezone

from . import run_queue
from .forms import COUNTRY_CHOICES
from .keyword_scoring import result_payload, score_keyword_pair
from .models import App, Keyword, KeywordSearchJob, SearchResult
from .popularity import (
    SOURCE_APPLE,
    effective_from_pair,
    get_popularity_source,
    make_absent_cap_lookup,
    prefetch_apple_values,
)
from .pro_access import has_pro_license
from .scoring import calc_opportunity
from .services import (
    DifficultyCalculator,
    DownloadEstimator,
    ITunesRateLimited,
    ITunesSearchService,
    SearchAPIUnavailableError,
)
from .throttle import AdaptiveITunesRateLimiter, classify_throttle_state

logger = logging.getLogger(__name__)

FEATURE_KEY = "keyword_search"
FEATURE_LABEL = "Keyword Research"

PRO_KEYWORD_LIMIT = 1000
FREE_KEYWORD_LIMIT = 3
RESULT_CARD_CAP = 50        # result cards on the Done panel; the rest is in Search History
COOLDOWN_SECONDS = 120      # after Apple rejects requests repeatedly
MAX_COOLDOWNS = 3           # consecutive cool-downs before the job pauses and asks the user
STATUS_POLL_SLICE = 5       # seconds between status checks during a cool-down
FAILED_ITEMS_CAP = 200      # failed keywords kept on the row (the retry uses them)
LIST_DISPLAY_CAP = 30       # names spelled out in a warning before "and N more"
ETA_MIN_PAIRS = 3           # pairs done in this run before an ETA is shown

PRICING_URL = "https://respectaso.com/pricing"
FREE_BUSY_MESSAGE = (
    "Your current search is still running. Wait for it to finish, or get Pro "
    "to queue searches and run up to 1,000 keywords at a time."
)
STATUS_NAME_FOR_THROTTLE = {"aborted": "cooldown"}

# "🇺🇸 United States" -> "United States"
COUNTRY_NAMES = {code: label.split(" ", 1)[1] for code, label in COUNTRY_CHOICES}


def fmt(n) -> str:
    """Thousands separators in copy: 1,000."""
    return f"{int(n):,}"


# ---------------------------------------------------------------------------
# Limits and parsing
# ---------------------------------------------------------------------------

def keyword_limit() -> int:
    return PRO_KEYWORD_LIMIT if has_pro_license() else FREE_KEYWORD_LIMIT


def parse_keywords(raw: str) -> list[str]:
    """Split on commas and newlines, strip, drop empties, and dedupe
    case-insensitively keeping the first spelling and the order.

    Mirrored in static/js/keyword-search-job.js for the live counter.
    """
    seen = set()
    keywords = []
    for chunk in (raw or "").replace("\r", "\n").replace(",", "\n").split("\n"):
        text = " ".join(chunk.split())
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        keywords.append(text)
    return keywords


def limit_context() -> dict:
    """What the form and the JSON errors need to explain the limit."""
    if has_pro_license():
        return {"limit": PRO_KEYWORD_LIMIT, "is_pro": True, "upgrade_url": None, "upgrade_label": ""}
    if django_apps.is_installed("aso_pro"):
        return {
            "limit": FREE_KEYWORD_LIMIT, "is_pro": False,
            "upgrade_url": reverse("aso_pro:settings_license"), "upgrade_label": "Activate Pro",
        }
    return {
        "limit": FREE_KEYWORD_LIMIT, "is_pro": False,
        "upgrade_url": PRICING_URL, "upgrade_label": "Get Pro",
    }


def limit_error(count: int, limit: int, is_pro: bool) -> str:
    if is_pro:
        return (f"That is {fmt(count)} keywords. A search holds up to {fmt(limit)} - "
                "start a second search for the rest.")
    return f"That is {fmt(count)} keywords. The free version runs up to {limit} per search."


# ---------------------------------------------------------------------------
# Reading jobs
# ---------------------------------------------------------------------------

def active_jobs():
    """Queued, running, paused and failed (= resumable) jobs."""
    return KeywordSearchJob.objects.filter(status__in=KeywordSearchJob.ACTIVE_STATUSES)


def active_job():
    """The newest active job, or None."""
    return active_jobs().order_by("-created_at", "-pk").first()


def panel_job():
    """The active job the dashboard's status panel shows: the running one,
    else the newest paused one, else the oldest queued one."""
    running = KeywordSearchJob.objects.filter(status="running").order_by("-created_at", "-pk").first()
    if running is not None:
        return running
    paused = (KeywordSearchJob.objects.filter(status__in=("paused", "failed"))
              .order_by("-created_at", "-pk").first())
    if paused is not None:
        return paused
    return KeywordSearchJob.objects.filter(status="queued").order_by("created_at", "pk").first()


def other_paused_jobs(panel):
    """Paused jobs the panel does not show, newest first."""
    qs = KeywordSearchJob.objects.filter(status__in=("paused", "failed"))
    if panel is not None:
        qs = qs.exclude(pk=panel.pk)
    return list(qs.order_by("-created_at", "-pk"))


def finished_job():
    """The newest finished job the user has not dismissed yet, or None."""
    return (KeywordSearchJob.objects
            .filter(status__in=KeywordSearchJob.TERMINAL_STATUSES, acknowledged=False)
            .order_by("-finished_at", "-pk").first())


def strip_job():
    """What the global strip shows: the active job, else the newest
    finished one not yet dismissed."""
    return panel_job() or finished_job()


# ---------------------------------------------------------------------------
# Creating and describing jobs
# ---------------------------------------------------------------------------

def create_job(app, countries, keywords, *, run_now=False) -> KeywordSearchJob:
    """Create a queued job and kick the lane. With ``run_now`` the job goes
    first, pausing a running keyword search if there is one (Top Search
    Terms tracks one keyword this way)."""
    job = KeywordSearchJob.objects.create(
        app=app, countries=list(countries), keywords=list(keywords),
        status="queued", progress_message="Waiting to start...",
    )
    if run_now:
        run_queue.run_now(FEATURE_KEY, job.pk)
    else:
        run_queue.kick()
    job.refresh_from_db()
    return job


def country_names(codes) -> list[str]:
    return [COUNTRY_NAMES.get(code, code.upper()) for code in codes]


def countries_text(codes) -> str:
    """'United States', 'United States and Germany', '3 countries'."""
    names = country_names(codes)
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{len(names)} countries"


def keywords_text(count: int) -> str:
    return f"{fmt(count)} keyword" + ("" if count == 1 else "s")


def describe(job) -> dict:
    """The queue row: '1,000 keywords', 'United States, Germany', 'US, DE'."""
    return {
        "label": keywords_text(job.total_keywords),
        "detail": ", ".join(country_names(job.countries)),
        "country": ", ".join(c.upper() for c in job.countries),
        "is_refinement": False,
        "quote_label": False,
    }


def _pair_text(keyword, country) -> str:
    return f"{keyword} ({country.upper()})"


def _list_text(items) -> str:
    """'a, b, c' or 'a, b, ... and 12 more'."""
    items = list(items)
    if len(items) <= LIST_DISPLAY_CAP:
        return ", ".join(items)
    return ", ".join(items[:LIST_DISPLAY_CAP]) + f" and {fmt(len(items) - LIST_DISPLAY_CAP)} more"


def skipped_warning(job) -> str | None:
    if not job.skipped_items:
        return None
    return (f"Skipped {keywords_text(job.skipped_count)} already in your list today: "
            + _list_text(job.skipped_items) + ". Use Refresh to update them.")


def failed_text(job) -> str | None:
    if not job.failed_items:
        return None
    names = [_pair_text(item["keyword"], item["country"]) for item in job.failed_items]
    return f"Could not check {keywords_text(job.failed_count)}: " + _list_text(names) + "."


def _waiting_for(job) -> tuple:
    """(label of what the queued job waits for, whether Run now can pass it)."""
    running = run_queue.running_run()
    if running is not None:
        feature, row = running
        if feature.key == FEATURE_KEY:
            return "the current search", row.pk != job.pk
        return feature.label, feature.can_yield
    busy = run_queue.busy_reason()
    if busy:
        return busy, False
    return None, False


def job_payload(job, *, include_results=False) -> dict:
    """Everything the dashboard panel, the strip and the queue draw a job
    from. Results (heavy) only on request."""
    waiting_for = None
    can_run_now = False
    if job.status == "queued":
        waiting_for, can_run_now = _waiting_for(job)
    remaining_pairs = max(job.total_pairs - job.next_index, 0)
    eta = (remaining_pairs * job.seconds_per_pair) if (job.seconds_per_pair and job.status == "running") else None
    data = {
        "id": job.pk,
        "status": job.status,
        "total_keywords": job.total_keywords,
        "total_pairs": job.total_pairs,
        "keywords_done": job.keywords_done,
        "remaining_count": job.remaining_count,
        "done_count": job.done_count,
        "skipped_count": job.skipped_count,
        "failed_count": job.failed_count,
        "progress_percent": job.progress_percent,
        "progress_message": job.progress_message or "",
        "current_pair": job.current_pair or "",
        "throttle_state": job.throttle_state,
        "auto_resume": job.auto_resume,
        "eta_seconds": int(eta) if eta is not None else None,
        "countries": list(job.countries),
        "country_names": country_names(job.countries),
        "countries_text": countries_text(job.countries),
        "queue_position": run_queue.queue_position(job) if job.status == "queued" else None,
        "waiting_for": waiting_for,
        "can_run_now": can_run_now,
        "yielded_for": job.yielded_for_label if job.auto_resume else None,
        "skipped_items": list(job.skipped_items),
        "failed_items": list(job.failed_items),
        "warning": skipped_warning(job),
        "failed_text": failed_text(job),
        "error_message": job.error_message or "",
        "acknowledged": job.acknowledged,
        "restart_resumes": job.restart_resumes,
        "is_native": bool(getattr(settings, "IS_NATIVE_APP", False)),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
    if include_results:
        data.update(_results(job))
        data["remaining_keywords"] = job.remaining_keywords
    return data


def compact_payload(job) -> dict:
    """The one-line summary the 'also paused' list draws."""
    return {
        "id": job.pk,
        "status": job.status,
        "keywords_done": job.keywords_done,
        "total_keywords": job.total_keywords,
        "remaining_count": job.remaining_count,
        "countries_text": countries_text(job.countries),
        "country_codes": ", ".join(c.upper() for c in job.countries),
        "auto_resume": job.auto_resume,
        "error_message": job.error_message or "",
    }


def _results(job) -> dict:
    """Today's stored results for the pairs the job worked through, in pair
    order: result cards for the first ``RESULT_CARD_CAP``, and the
    opportunity ranking over all of them when several countries were
    searched. Heavy columns are loaded only for the cards."""
    n = len(job.countries)
    upto = min(job.next_index, job.total_pairs)
    if not n or not upto:
        return {"results": [], "results_total": 0, "opportunity_ranking": []}

    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    scope = {"keyword__app_id": job.app_id} if job.app_id else {"keyword__app__isnull": True}
    light_rows = (
        SearchResult.objects
        .filter(country__in=job.countries, searched_at__gte=today_start, **scope)
        .select_related("keyword")
        .only("id", "country", "popularity_score", "apple_popularity_score",
              "difficulty_score", "inferred_genre", "keyword__keyword", "keyword__app_id")
        .order_by("searched_at")
    )
    by_pair = {}
    for row in light_rows:
        by_pair[(row.keyword.keyword, row.country)] = row   # the newest wins

    ordered = []
    for index in range(upto):
        keyword, country = job.pair(index)
        row = by_pair.get((keyword.lower(), country))
        if row is not None:
            ordered.append((keyword, row))

    card_rows = {
        r.pk: r for r in (
            SearchResult.objects.filter(pk__in=[row.pk for _kw, row in ordered[:RESULT_CARD_CAP]])
            .select_related("keyword", "keyword__app")
        )
    }
    results = [result_payload(card_rows[row.pk], app=job.app)
               for _kw, row in ordered[:RESULT_CARD_CAP] if row.pk in card_rows]

    ranking = []
    if n > 1:
        cap_for = make_absent_cap_lookup()
        source_setting = get_popularity_source()
        kw_map = {}
        for keyword, row in ordered:
            ceiling = (cap_for(row.country, row.inferred_genre)
                       if source_setting == SOURCE_APPLE else None)
            effective = effective_from_pair(
                row.popularity_score, row.apple_popularity_score, source_setting,
                absent_ceiling=ceiling,
            )[0]
            pop = effective or 0
            kw_map.setdefault(keyword, {})[row.country] = {
                "popularity": pop,
                "difficulty": row.difficulty_score,
                "opportunity": calc_opportunity(pop, row.difficulty_score),
            }
        for keyword, country_data in kw_map.items():
            best_country = max(country_data, key=lambda c: country_data[c]["opportunity"])
            ranking.append({
                "keyword": keyword,
                "countries": country_data,
                "best_country": best_country,
                "best_score": country_data[best_country]["opportunity"],
            })
        ranking.sort(key=lambda x: x["best_score"], reverse=True)

    return {"results": results, "results_total": len(ordered), "opportunity_ranking": ranking}


# ---------------------------------------------------------------------------
# Queue hooks
# ---------------------------------------------------------------------------

def _front_rank() -> int:
    ranks = [row.queue_rank for _f, row in run_queue.queued_runs() if row.queue_rank is not None]
    return (min(ranks) if ranks else 1) - 1


def requeue_interrupted(queryset) -> None:
    """Searches left "running" by a quit, a crash or a container restart go
    back to the FRONT of the queue (they were executing, not waiting) and
    continue from the first keyword that was not finished."""
    queryset.update(
        status="queued", queue_rank=_front_rank(), auto_resume=False,
        yielded_for_feature="", yielded_for_id=None, yielded_for_label="",
        current_pair="", progress_message="Resuming...",
        restart_resumes=F("restart_resumes") + 1,
    )


def remove_from_queue(job) -> bool:
    """A queued search that never ran is deleted; one that already has
    progress (continued after a restart, waiting its turn) is paused
    instead, so half-done work is never lost from a queue list."""
    if job.next_index == 0:
        deleted, _ = KeywordSearchJob.objects.filter(pk=job.pk, status="queued").delete()
        return bool(deleted)
    updated = KeywordSearchJob.objects.filter(pk=job.pk, status="queued").update(
        status="paused", auto_resume=False, queue_rank=None,
        progress_message="Paused - removed from the queue. Resume it from the Keyword Research tab.",
    )
    return bool(updated)


def retry_failed_job(job) -> KeywordSearchJob:
    """A new search with exactly the keywords that could not be checked, in
    the same countries; pairs that did succeed are skipped as already
    searched today, so only the missing checks run."""
    keywords = parse_keywords("\n".join(item["keyword"] for item in job.failed_items))
    return create_job(job.app, job.countries, keywords)


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------

def _status_and_app(pk):
    row = KeywordSearchJob.objects.filter(pk=pk).values_list("status", "app_id").first()
    return row if row else ("missing", None)


def _has_result_today(keyword_obj, country) -> bool:
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return keyword_obj.results.filter(country=country, searched_at__gte=today_start).exists()


def _cooldown(pk) -> bool:
    """Sleep out the cool-down in slices; False when the job stopped running."""
    waited = 0
    while waited < COOLDOWN_SECONDS:
        time.sleep(STATUS_POLL_SLICE)
        waited += STATUS_POLL_SLICE
        if _status_and_app(pk)[0] != "running":
            return False
    return True


def _throttle_message(state, limiter) -> str:
    if state == "slowed_down":
        return f"Apple is slowing responses - now pacing at {round(limiter.current_delay)} s per keyword."
    if state == "paused":
        return (f"Apple is not answering - {limiter.consecutive_failures} requests failed in a row, "
                f"retrying at {round(limiter.current_delay)} s per keyword.")
    if state == "aborted":
        return "Apple is rejecting requests - cooling down for 2 minutes, then retrying."
    return "Researching..."


def execute_job(pk) -> None:
    """The run queue's execute hook. Never raises: any exception outside the
    per-pair handling pauses the search with the error, so a crash never
    fails or discards it."""
    try:
        _execute(pk)
    except Exception as exc:
        logger.exception("Keyword search %s stopped on an error", pk)
        KeywordSearchJob.objects.filter(pk=pk, status="running").update(
            status="paused", auto_resume=False, current_pair="",
            error_message=(str(exc) or exc.__class__.__name__)[:500],
            progress_message="Paused after an error",
        )


def _execute(pk) -> None:
    job = KeywordSearchJob.objects.select_related("app").get(pk=pk)
    countries = list(job.countries)
    keywords = list(job.keywords)
    n = len(countries)
    total = len(keywords) * n
    start_index = job.next_index

    if start_index >= total:
        _finish(pk)
        return

    # Local table work: materialize Apple values for the keywords still to do.
    for country in countries:
        prefetch_apple_values(keywords[start_index // n:], country)

    itunes = ITunesSearchService()
    difficulty_calc = DifficultyCalculator()
    download_est = DownloadEstimator()
    limiter = AdaptiveITunesRateLimiter()

    app, app_id = job.app, job.app_id
    done, skipped, failed_total = job.done_count, job.skipped_count, job.failed_count
    skipped_items, failed_items = list(job.skipped_items), list(job.failed_items)
    attempted = failed = 0          # this run, for the throttle classifier
    cooldowns = 0
    pairs_this_run = 0
    run_started = time.monotonic()
    elapsed_before = job.elapsed_seconds or 0.0
    seconds_per_pair = job.seconds_per_pair
    first_call = True

    KeywordSearchJob.objects.filter(pk=pk, status="running").update(
        progress_message="Researching...", error_message="", throttle_state="normal",
        current_pair=_pair_text(*job.pair(start_index)),
    )

    def record_failure(keyword, country, error):
        nonlocal failed_total
        failed_total += 1
        if len(failed_items) < FAILED_ITEMS_CAP:
            failed_items.append({"keyword": keyword, "country": country, "error": error[:200]})

    for index in range(start_index, total):
        status, current_app_id = _status_and_app(pk)
        if status != "running":     # Pause, Discard, Run now, or removed
            return
        if current_app_id != app_id:    # the app was deleted meanwhile
            app_id = current_app_id
            app = App.objects.filter(pk=app_id).first() if app_id else None

        keyword, country = keywords[index // n], countries[index % n]
        keyword_obj, created = Keyword.objects.get_or_create(keyword=keyword.lower(), app=app)

        if not created and _has_result_today(keyword_obj, country):
            skipped += 1
            skipped_items.append(_pair_text(keyword, country))
        else:
            if not first_call:
                limiter.wait()
            first_call = False
            attempted += 1
            try:
                score_keyword_pair(keyword_obj, country, app=app, itunes_service=itunes,
                                   difficulty_calc=difficulty_calc, download_est=download_est)
                limiter.record_success()
                done += 1
            except ITunesRateLimited as exc:
                limiter.record_failure(retry_after=exc.retry_after)
                failed += 1
                record_failure(keyword, country, "Apple rate limit")
            except (SearchAPIUnavailableError, requests.RequestException) as exc:
                limiter.record_failure()
                failed += 1
                record_failure(keyword, country, str(exc) or exc.__class__.__name__)
            except Exception as exc:    # one keyword never ends the job
                logger.exception("Keyword search %s: unexpected error on %s (%s)", pk, keyword, country)
                record_failure(keyword, country, "Unexpected error")

        pairs_this_run += 1
        active_seconds = time.monotonic() - run_started
        if pairs_this_run >= ETA_MIN_PAIRS:
            seconds_per_pair = active_seconds / pairs_this_run
        state = classify_throttle_state(limiter, attempted=attempted, failed=failed)
        next_pair = _pair_text(*job.pair(index + 1)) if index + 1 < total else ""
        # The cursor and the counters are guarded by the cursor, not the
        # status, so a pause that lands mid-pair still records the pair that
        # finished. What the panel shows (ticker, throttle line) is written
        # only while the job still runs, so a Pause's own message stays.
        KeywordSearchJob.objects.filter(pk=pk, next_index=index).update(
            next_index=index + 1, done_count=done, skipped_count=skipped,
            failed_count=failed_total, skipped_items=skipped_items, failed_items=failed_items,
            seconds_per_pair=seconds_per_pair, elapsed_seconds=elapsed_before + active_seconds,
        )
        KeywordSearchJob.objects.filter(pk=pk, status="running").update(
            current_pair=next_pair, throttle_state=STATUS_NAME_FOR_THROTTLE.get(state, state),
            progress_message=_throttle_message(state, limiter),
        )

        if state == "aborted":
            cooldowns += 1
            if cooldowns > MAX_COOLDOWNS:
                KeywordSearchJob.objects.filter(pk=pk, status="running").update(
                    status="paused", throttle_state="paused", auto_resume=False, current_pair="",
                    progress_message=(f"Apple rejected {limiter.consecutive_failures} requests in a row. "
                                      "Wait a few minutes, then press Resume."),
                )
                return
            if not _cooldown(pk):
                return
            limiter = AdaptiveITunesRateLimiter()
            attempted = failed = 0
        elif limiter.consecutive_failures == 0 and state == "normal":
            cooldowns = 0

    _finish(pk)


def _finish(pk) -> None:
    KeywordSearchJob.objects.filter(pk=pk, status="running").update(
        status="completed", finished_at=timezone.now(), progress_message="Done",
        throttle_state="normal", current_pair="", auto_resume=False,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

run_queue.register(run_queue.Feature(
    key=FEATURE_KEY, label=FEATURE_LABEL, model=KeywordSearchJob, filter_kwargs={},
    execute=execute_job, describe=describe, progress=job_payload, open_url="/",
    interrupted=requeue_interrupted, on_remove=remove_from_queue, can_yield=True,
))
