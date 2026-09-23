"""Country Opportunity Finder scans as persistent, resumable background jobs.

A scan from the Opportunity tab is an ``OpportunityScan`` row: one keyword,
the storefronts the user picked, and a cursor. The run queue
(``aso/run_queue.py``) executes one at a time on a daemon thread, the worker
writes the cursor and the counters after every country, and it stops at the
next country boundary whenever the row's status is no longer "running" (Pause,
Discard, Run now). Because everything it needs is in the row, a scan continues
after navigating away, after the app was quit and after a crash.

The scan deliberately persists NOTHING to the search history. Results land in
``OpportunityScanResult`` and only reach ``SearchResult`` when the user saves
the rows worth keeping. That is the feature's whole point: you scan sixty
countries to find the two you care about.

This used to run in the browser, one fetch per country with a fixed 1.5 second
sleep, holding partial results in sessionStorage and dying if you navigated
away. That was workable for 30 countries and is not for 175.

Ships in the free-tier ``aso`` app: no ``aso_pro`` or ``licensing`` imports at
module level (``aso.pro_access`` does the license check).
"""

from __future__ import annotations

import logging
import statistics
import time

import requests
from django.conf import settings
from django.db.models import F
from django.utils import timezone

from . import countries, run_queue, throttle
from .column_tips import opportunity_subline
from .keyword_scoring import score_country
from .models import App, OpportunityScan, OpportunityScanResult, SearchResult
from .popularity import popularity_fields, prefetch_apple_values
from .scoring import (
    calc_opportunity,
    classify_keyword,
    difficulty_color,
    difficulty_label,
    opportunity_css,
    opportunity_reach,
    scored_for,
    targeting_description,
    targeting_payload,
    top_spot_range,
)
from .services import (
    DifficultyCalculator,
    DownloadEstimator,
    ITunesRateLimited,
    ITunesSearchService,
    SearchAPIUnavailableError,
)
from .strength import AppProfile
from .throttle import AdaptiveITunesRateLimiter, classify_throttle_state

logger = logging.getLogger(__name__)

FEATURE_KEY = "opportunity_scan"
FEATURE_LABEL = "Country Opportunity Finder"

FAILED_ITEMS_CAP = 200      # failed countries kept on the row (the retry uses them)
COOLDOWN_SECONDS = 120      # after Apple rejects requests repeatedly
MAX_COOLDOWNS = 3           # consecutive cool-downs before the scan pauses and asks
STATUS_POLL_SLICE = 5       # seconds between status checks during a cool-down
ETA_MIN_COUNTRIES = 3       # countries done this run before an ETA is shown
SCAN_HISTORY_KEEP = 10      # finished scans kept; older ones are pruned on create
# A scan started from the MCP server runs in another process. If that process
# dies its row would hold the single run lane for ever, so a heartbeat older
# than this is treated as abandoned and the scan goes back in the queue.
STALE_HEARTBEAT_SECONDS = 300
FALLBACK_SECONDS_PER_COUNTRY = throttle.BASE_DELAY + 0.6

FREE_BUSY_MESSAGE = (
    "Your current country scan is still running. Wait for it to finish, or "
    "get Pro to queue scans and run them alongside your keyword research."
)
STATUS_NAME_FOR_THROTTLE = {"aborted": "cooldown"}


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def active_scans():
    return OpportunityScan.objects.filter(status__in=OpportunityScan.ACTIVE_STATUSES)


def active_scan():
    """The newest active scan, or None."""
    return active_scans().order_by("-created_at", "-pk").first()


def panel_scan():
    """The scan the Opportunity page's status panel shows: the running one,
    else the newest paused one, else the oldest queued one."""
    running = (OpportunityScan.objects.filter(status="running")
               .order_by("-created_at", "-pk").first())
    if running is not None:
        return running
    paused = (OpportunityScan.objects.filter(status__in=("paused", "failed"))
              .order_by("-created_at", "-pk").first())
    if paused is not None:
        return paused
    return (OpportunityScan.objects.filter(status="queued")
            .order_by("created_at", "pk").first())


def finished_scan():
    """The newest finished scan, unless the user closed it, or None.

    Only ever the newest, the rule of search_jobs.finished_job(): closing it
    never brings up an older scan in its place.
    """
    newest = (OpportunityScan.objects
              .filter(status__in=OpportunityScan.TERMINAL_STATUSES)
              .order_by("-finished_at", "-pk").first())
    return newest if newest is not None and not newest.acknowledged else None


def latest_scan():
    """What the Opportunity page opens on: the active scan, else the newest
    finished one, dismissed or not, so the results survive a reload."""
    return panel_scan() or (
        OpportunityScan.objects.order_by("-created_at", "-pk").first()
    )


def strip_scan():
    """What the global strip shows: the active scan, else the newest finished
    one not yet dismissed."""
    return panel_scan() or finished_scan()


def reclaim_stale() -> int:
    """Re-queue scans whose worker process died without finishing.

    Only scans, and only on a heartbeat older than STALE_HEARTBEAT_SECONDS.
    A scan started from MCP runs outside this process, so a crash there would
    otherwise leave a "running" row blocking the single lane for everyone.
    """
    cutoff = timezone.now() - timezone.timedelta(seconds=STALE_HEARTBEAT_SECONDS)
    stale = OpportunityScan.objects.filter(
        status="running", heartbeat_at__lt=cutoff,
    )
    count = stale.count()
    if count:
        logger.warning("Re-queueing %s country scan(s) with a stale heartbeat", count)
        requeue_interrupted(stale)
        run_queue.kick()
    return count


# ---------------------------------------------------------------------------
# Creating and describing
# ---------------------------------------------------------------------------

def prune_scans() -> None:
    """Keep the ten most recent finished scans. Their results cascade."""
    keep = list(
        OpportunityScan.objects
        .filter(status__in=OpportunityScan.TERMINAL_STATUSES)
        .order_by("-created_at", "-pk")
        .values_list("pk", flat=True)[:SCAN_HISTORY_KEEP]
    )
    (OpportunityScan.objects
     .filter(status__in=OpportunityScan.TERMINAL_STATUSES)
     .exclude(pk__in=keep)
     .delete())


def create_scan(keyword, codes, *, app=None, origin="web", run_now=False) -> OpportunityScan:
    """Create a queued scan and kick the lane."""
    prune_scans()
    scan = OpportunityScan.objects.create(
        keyword=keyword.strip(),
        app=app,
        countries=countries.clean(codes),
        status="queued",
        origin=origin,
        progress_message="Waiting to start...",
    )
    if run_now:
        run_queue.run_now(FEATURE_KEY, scan.pk)
    else:
        run_queue.kick()
    scan.refresh_from_db()
    return scan


def estimate_seconds(count: int) -> float:
    """How long a scan of ``count`` countries should take, from what the last
    few scans actually took rather than from a constant."""
    recent = list(
        OpportunityScan.objects
        .filter(status="completed", seconds_per_country__isnull=False)
        .order_by("-finished_at")
        .values_list("seconds_per_country", flat=True)[:3]
    )
    per = statistics.median(recent) if recent else FALLBACK_SECONDS_PER_COUNTRY
    return max(per, throttle.BASE_DELAY) * max(count, 0)


def duration_text(seconds: float) -> str:
    """Twin of durationText() in static/js/country-picker.js."""
    if not seconds or seconds <= 0:
        return ""
    if seconds < 60:
        return f"{max(10, round(seconds / 10) * 10)} seconds"
    minutes = int(seconds // 60) + (1 if seconds % 60 else 0)
    return f"about {minutes} minute" + ("" if minutes == 1 else "s")


def cost_text(count: int) -> str:
    """"Scan 62 countries, about 3 minutes", before the user commits."""
    if not count:
        return "Pick at least one country"
    noun = "country" if count == 1 else "countries"
    return f"Scan {count} {noun}, {duration_text(estimate_seconds(count))}"


def country_names(codes) -> list[str]:
    return [countries.name(code) for code in codes or ()]


def countries_text(codes) -> str:
    names = country_names(codes)
    if not names:
        return "no countries"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return f"{names[0]} and {names[1]}"
    return f"{len(names)} countries"


def describe(scan) -> dict:
    """What the queue list shows for this run."""
    codes = scan.countries or []
    return {
        "label": scan.keyword,
        "detail": countries_text(codes),
        "country": ", ".join(c.upper() for c in codes) if len(codes) <= 2 else "",
        "is_refinement": False,
    }


def failed_text(scan) -> str | None:
    if not scan.failed_items:
        return None
    names = [countries.name(item["country"]) for item in scan.failed_items]
    shown = ", ".join(names[:30])
    if len(names) > 30:
        shown += f" and {len(names) - 30} more"
    noun = "country" if scan.failed_count == 1 else "countries"
    return f"Could not check {scan.failed_count} {noun}: {shown}."


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------

def country_payload(row, *, heavy=False) -> dict:
    """One result row, as the table renders it.

    Light by default: the two heavy JSON columns are fetched only when the
    user expands a row. A 175 country scan polled every three seconds would
    otherwise move more than a megabyte per tick.
    """
    resolution = row.popularity_resolution()
    popularity = resolution.effective
    entry = countries.get(row.country)
    # Scored for the scan's app, from its profile in THIS storefront, which
    # differs from storefront to storefront, and its real rank there; a
    # brand new app without one.
    app = row.app_profile
    reach = opportunity_reach(
        popularity or 0, row.difficulty_score, row.country,
        app=app, app_rank=row.app_rank, keyword=row.keyword_text,
    )
    label = classify_keyword(
        popularity or 0, row.difficulty_score, row.country,
        app=app, app_rank=row.app_rank, keyword=row.keyword_text,
    )
    opportunity = calc_opportunity(
        popularity or 0, row.difficulty_score, row.country,
        app=app, app_rank=row.app_rank, keyword=row.keyword_text,
    )
    data = {
        "country": row.country,
        "country_name": countries.name(row.country),
        "country_flag": countries.flag(row.country),
        "region": entry.region if entry else "",
        # From the registry, not from the heavy breakdown column: the same
        # value, and the light payload stays light.
        "market_source": entry.market_source if entry else "derived",
        "popularity": popularity,
        **popularity_fields(resolution),
        "difficulty": row.difficulty_score,
        "difficulty_label": difficulty_label(row.difficulty_score),
        "difficulty_color": difficulty_color(row.difficulty_score),
        "opportunity": opportunity,
        "opportunity_css": opportunity_css(opportunity),
        # What #1 pays here, low and high, as the Downloads at #1 column of
        # every other table shows it: with one Opportunity number, this is
        # where the upside lives.
        "downloads_at_first": list(top_spot_range(popularity or 0, row.country)),
        # The old per-country endpoint never sent this, so every expanded row
        # in the Opportunity Finder used to read "Moderate" whatever the data.
        "classification": label,
        "classification_tip": targeting_description(
            label, popularity or 0, row.difficulty_score, row.country, app,
            row.app_rank, row.keyword_text, reach=reach,
        ),
        # How the score is worked out, shown on hover over it, the same the
        # Dashboard shows. About 400 bytes a row; on localhost the three
        # second poll carries 175 of them without trouble.
        "opportunity_tip": reach["explanation"],
        "app_rank": row.app_rank,
        "competitor_count": row.competitor_count,
        "top_competitor": row.top_competitor,
        "top_ratings": row.top_ratings,
    }
    if heavy:
        data["difficulty_breakdown"] = row.difficulty_breakdown
        data["competitors"] = row.competitors_data
        # Only the expanded row shows the badge, and its sentence is about
        # ninety bytes. On 175 light rows every three seconds that is a poll
        # twice the size for something nobody is looking at.
        data["targeting"] = targeting_payload(
            popularity or 0, row.difficulty_score, row.country,
            app=app, app_rank=row.app_rank, keyword=row.keyword_text,
        )
    return data


def _waiting_for(scan) -> tuple:
    """(label of what the queued scan waits for, whether Run now can pass it)."""
    running = run_queue.running_run()
    if running is not None:
        feature, row = running
        if feature.key == FEATURE_KEY:
            return "the current scan", row.pk != scan.pk
        return feature.label, feature.can_yield
    busy = run_queue.busy_reason()
    if busy:
        return busy, False
    return None, False


def scan_payload(scan, *, include_results=False) -> dict:
    """Everything the scan panel, the strip and the queue draw from.

    The key names match ``search_jobs.job_payload`` wherever the meaning is
    the same, so one JavaScript core can drive both job types.
    """
    waiting_for = None
    can_run_now = False
    if scan.status == "queued":
        waiting_for, can_run_now = _waiting_for(scan)
    eta = (
        scan.remaining_count * scan.seconds_per_country
        if (scan.seconds_per_country and scan.status == "running") else None
    )
    data = {
        "id": scan.pk,
        "status": scan.status,
        "keyword": scan.keyword,
        "app_id": scan.app_id,
        "total_countries": scan.total_countries,
        "done_count": scan.done_count,
        "failed_count": scan.failed_count,
        "remaining_count": scan.remaining_count,
        "progress_percent": scan.progress_percent,
        "progress_message": scan.progress_message or "",
        "current_country": scan.current_country or "",
        "current_country_name": countries.name(scan.current_country) if scan.current_country else "",
        "throttle_state": scan.throttle_state,
        "auto_resume": scan.auto_resume,
        "eta_seconds": int(eta) if eta is not None else None,
        "seconds_per_country": scan.seconds_per_country,
        "countries": list(scan.countries),
        "countries_text": countries_text(scan.countries),
        "queue_position": run_queue.queue_position(scan) if scan.status == "queued" else None,
        # Whose opportunity the table shows: the scan's app, from its ratings
        # in each storefront, or a brand new app. An app added by hand has no
        # App Store listing (no track id), so none of its ratings exist.
        "scored_for": scored_for(
            AppProfile(name=scan.app.name, known=bool(scan.app.track_id)) if scan.app else None,
            reason="each_storefront" if scan.app else None,
        ),
        # The words under the Opportunity heading: the app, or a new app.
        "opportunity_subline": opportunity_subline("new_app", scan.app.name if scan.app else None),
        "waiting_for": waiting_for,
        "can_run_now": can_run_now,
        "yielded_for": scan.yielded_for_label if scan.auto_resume else None,
        "failed_items": list(scan.failed_items),
        "failed_text": failed_text(scan),
        "error_message": scan.error_message or "",
        "acknowledged": scan.acknowledged,
        "restart_resumes": scan.restart_resumes,
        "saved_count": scan.saved_count,
        "is_native": bool(getattr(settings, "IS_NATIVE_APP", False)),
        "created_at": scan.created_at.isoformat() if scan.created_at else None,
        "finished_at": scan.finished_at.isoformat() if scan.finished_at else None,
    }
    if include_results:
        rows = list(scan.results.all().only(
            "country", "order_index", "keyword_text", "popularity_score",
            "apple_popularity_score", "inferred_genre", "difficulty_score",
            "app_rank", "competitor_count", "top_competitor", "top_ratings",
        ))
        results = [country_payload(row) for row in rows]
        results.sort(key=lambda r: (-(r["opportunity"] or 0), r["country_name"]))
        data["results"] = results
        data["results_total"] = len(results)
        data["best"] = results[0] if results else None
    return data


def compact_payload(scan) -> dict:
    """The one-line summary the "also paused" list draws."""
    return {
        "id": scan.pk,
        "status": scan.status,
        "keyword": scan.keyword,
        "done_count": scan.done_count,
        "total_countries": scan.total_countries,
        "remaining_count": scan.remaining_count,
        "countries_text": countries_text(scan.countries),
        "auto_resume": scan.auto_resume,
        "error_message": scan.error_message or "",
    }


# ---------------------------------------------------------------------------
# Saving to the search history
# ---------------------------------------------------------------------------

def save_to_history(scan, codes=None) -> int:
    """Copy scan rows into the search history, which is the only way they
    ever become ``SearchResult``s.

    Reads the stored rows server side: the browser never round-trips the
    competitor payload back to us. ``inferred_genre`` travels with them,
    without which the Apple fallback cap falls back to the country-wide floor
    instead of the keyword's own category.
    """
    from .models import Keyword

    rows = scan.results.all()
    if codes is not None:
        wanted = countries.clean(codes)
        rows = rows.filter(country__in=wanted)

    keyword_obj, _ = Keyword.objects.get_or_create(
        keyword=scan.keyword.lower(), app=scan.app,
    )
    saved = 0
    for row in rows:
        SearchResult.upsert_today(
            keyword=keyword_obj,
            country=row.country,
            popularity_score=row.popularity_score,
            apple_popularity_score=row.apple_popularity_score,
            inferred_genre=row.inferred_genre,
            difficulty_score=row.difficulty_score,
            difficulty_breakdown=row.difficulty_breakdown,
            competitors_data=row.competitors_data,
            app_rank=row.app_rank,
        )
        saved += 1
    if saved:
        OpportunityScan.objects.filter(pk=scan.pk).update(
            saved_count=F("saved_count") + saved,
        )
    return saved


# ---------------------------------------------------------------------------
# Queue hooks
# ---------------------------------------------------------------------------

def requeue_interrupted(queryset) -> None:
    """Scans left "running" by a quit, a crash or a container restart go back
    to the FRONT of the queue (they were executing, not waiting) and continue
    from the first country that was not finished."""
    queryset.update(
        status="queued", queue_rank=run_queue.front_rank(), auto_resume=False,
        yielded_for_feature="", yielded_for_id=None, yielded_for_label="",
        current_country="", progress_message="Resuming...",
        restart_resumes=F("restart_resumes") + 1,
    )


def remove_from_queue(scan) -> bool:
    """A queued scan that never ran is deleted; one that already has progress
    is paused instead, so half-done work is never lost from a queue list."""
    if scan.next_index == 0:
        deleted, _ = OpportunityScan.objects.filter(pk=scan.pk, status="queued").delete()
        return bool(deleted)
    updated = OpportunityScan.objects.filter(pk=scan.pk, status="queued").update(
        status="paused", auto_resume=False, queue_rank=None,
        progress_message="Paused, removed from the queue. Resume it from the Opportunity tab.",
    )
    return bool(updated)


def retry_failed_scan(scan) -> OpportunityScan:
    """A new scan over exactly the countries that could not be checked."""
    codes = [item["country"] for item in scan.failed_items]
    return create_scan(scan.keyword, codes, app=scan.app, origin=scan.origin)


def cancel_scan(pk) -> bool:
    """Stop a scan and keep what it already found. Used by MCP and the UI."""
    updated = OpportunityScan.objects.filter(
        pk=pk, status__in=OpportunityScan.ACTIVE_STATUSES,
    ).update(
        status="cancelled", finished_at=timezone.now(), auto_resume=False,
        current_country="", progress_message="Cancelled",
    )
    if updated:
        run_queue.kick()
    return bool(updated)


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------

def _status_and_app(pk):
    row = OpportunityScan.objects.filter(pk=pk).values_list("status", "app_id").first()
    return row if row else ("missing", None)


def _cooldown(pk) -> bool:
    """Sleep out the cool-down in slices; False when the scan stopped running."""
    waited = 0
    while waited < COOLDOWN_SECONDS:
        time.sleep(STATUS_POLL_SLICE)
        waited += STATUS_POLL_SLICE
        if _status_and_app(pk)[0] != "running":
            return False
    return True


def _throttle_message(state, limiter) -> str:
    if state == "slowed_down":
        return f"Apple is slowing responses, now pacing at {round(limiter.current_delay)} s per country."
    if state == "paused":
        return (f"Apple is not answering, {limiter.consecutive_failures} requests failed in a row, "
                f"retrying at {round(limiter.current_delay)} s per country.")
    if state == "aborted":
        return "Apple is rejecting requests, cooling down for 2 minutes, then retrying."
    return "Scanning..."


def execute_scan(pk) -> None:
    """The run queue's execute hook. Never raises: any exception outside the
    per-country handling pauses the scan with the error, so a crash never
    loses a half finished 175 country scan."""
    try:
        _execute(pk)
    except Exception as exc:
        logger.exception("Country scan %s stopped on an error", pk)
        OpportunityScan.objects.filter(pk=pk, status="running").update(
            status="paused", auto_resume=False, current_country="",
            error_message=(str(exc) or exc.__class__.__name__)[:500],
            progress_message="Paused after an error",
        )


def _execute(pk) -> None:
    scan = OpportunityScan.objects.select_related("app").get(pk=pk)
    codes = list(scan.countries)
    total = len(codes)
    start_index = scan.next_index

    if start_index >= total:
        _finish(pk)
        return

    # Local table work: materialize Apple values for the keyword in every
    # country still to do, so scoring reads local rows.
    for code in codes[start_index:]:
        prefetch_apple_values([scan.keyword], code)

    itunes = ITunesSearchService()
    difficulty_calc = DifficultyCalculator()
    download_est = DownloadEstimator()
    limiter = AdaptiveITunesRateLimiter()

    app, app_id = scan.app, scan.app_id
    done, failed_total = scan.done_count, scan.failed_count
    failed_items = list(scan.failed_items)
    attempted = failed = 0          # this run, for the throttle classifier
    cooldowns = 0
    countries_this_run = 0
    run_started = time.monotonic()
    elapsed_before = scan.elapsed_seconds or 0.0
    seconds_per_country = scan.seconds_per_country
    first_call = True

    OpportunityScan.objects.filter(pk=pk, status="running").update(
        progress_message="Scanning...", error_message="", throttle_state="normal",
        current_country=codes[start_index], started_at=scan.started_at or timezone.now(),
    )

    def record_failure(country, error):
        nonlocal failed_total
        failed_total += 1
        if len(failed_items) < FAILED_ITEMS_CAP:
            failed_items.append({"country": country, "error": error[:200]})

    for index in range(start_index, total):
        status, current_app_id = _status_and_app(pk)
        if status != "running":     # Pause, Discard, Run now, cancel, removed
            return
        if current_app_id != app_id:    # the app was deleted meanwhile
            app_id = current_app_id
            app = App.objects.filter(pk=app_id).first() if app_id else None

        code = codes[index]
        if not first_call:
            limiter.wait()
        first_call = False
        attempted += 1
        try:
            scored = score_country(
                scan.keyword, code, app=app, itunes_service=itunes,
                difficulty_calc=difficulty_calc, download_est=download_est,
            )
            limiter.record_success()
            _store(pk, scan.keyword, index, code, scored)
            done += 1
        except ITunesRateLimited as exc:
            limiter.record_failure(retry_after=exc.retry_after)
            failed += 1
            record_failure(code, "Apple rate limit")
        except (SearchAPIUnavailableError, requests.RequestException) as exc:
            limiter.record_failure()
            failed += 1
            record_failure(code, str(exc) or exc.__class__.__name__)
        except Exception:    # one country never ends the scan
            logger.exception("Country scan %s: unexpected error on %s", pk, code)
            record_failure(code, "Unexpected error")

        countries_this_run += 1
        active_seconds = time.monotonic() - run_started
        if countries_this_run >= ETA_MIN_COUNTRIES:
            seconds_per_country = active_seconds / countries_this_run
        state = classify_throttle_state(limiter, attempted=attempted, failed=failed)
        next_code = codes[index + 1] if index + 1 < total else ""
        # The cursor and the counters are guarded by the cursor, not the
        # status, so a pause that lands mid-country still records the country
        # that finished. What the panel shows is written only while the scan
        # still runs, so a Pause's own message stays.
        OpportunityScan.objects.filter(pk=pk, next_index=index).update(
            next_index=index + 1, done_count=done, failed_count=failed_total,
            failed_items=failed_items, seconds_per_country=seconds_per_country,
            elapsed_seconds=elapsed_before + active_seconds,
        )
        OpportunityScan.objects.filter(pk=pk, status="running").update(
            current_country=next_code, heartbeat_at=timezone.now(),
            throttle_state=STATUS_NAME_FOR_THROTTLE.get(state, state),
            progress_message=_throttle_message(state, limiter),
        )

        if state == "aborted":
            cooldowns += 1
            if cooldowns > MAX_COOLDOWNS:
                OpportunityScan.objects.filter(pk=pk, status="running").update(
                    status="paused", throttle_state="paused", auto_resume=False,
                    current_country="",
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


def _store(pk, keyword, index, code, scored) -> None:
    """One country's result. update_or_create, not create: a country re-run
    after an interrupt replaces its row instead of colliding with it."""
    resolution = scored["popularity"]
    competitors = scored["competitors"] or []
    top = competitors[0] if competitors else {}
    OpportunityScanResult.objects.update_or_create(
        scan_id=pk, country=code,
        defaults={
            "order_index": index,
            "keyword_text": keyword,
            "popularity_score": resolution.internal,
            "apple_popularity_score": resolution.apple,
            "inferred_genre": resolution.genre_hint or "",
            "difficulty_score": scored["difficulty_score"],
            "difficulty_breakdown": scored["difficulty_breakdown"],
            "competitors_data": competitors,
            "app_rank": scored["app_rank"],
            "competitor_count": len(competitors),
            "top_competitor": (top.get("trackName") or "")[:255],
            "top_ratings": top.get("userRatingCount") or 0,
        },
    )


def _finish(pk) -> None:
    OpportunityScan.objects.filter(pk=pk, status="running").update(
        status="completed", finished_at=timezone.now(), progress_message="Done",
        throttle_state="normal", current_country="", auto_resume=False,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

run_queue.register(run_queue.Feature(
    key=FEATURE_KEY, label=FEATURE_LABEL, model=OpportunityScan, filter_kwargs={},
    execute=execute_scan, describe=describe, progress=scan_payload,
    open_url="/opportunity/",
    interrupted=requeue_interrupted, on_remove=remove_from_queue,
    # A 175 country scan is about ten minutes. Making a keyword search the
    # user just started wait behind it would be worse than the old behaviour,
    # and a scan can stop at a country boundary and resume from its cursor,
    # which is exactly what can_yield asks.
    can_yield=True,
))
