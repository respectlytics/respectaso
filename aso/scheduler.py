"""
Background auto-refresh scheduler for RespectASO.

Runs a daemon thread that periodically refreshes all tracked keywords.
Progress is tracked in-memory so the dashboard can show a non-blocking
progress indicator.

Schedule:
  - Checks once per hour whether today's refresh has run, and at once when
    nudge() asks (the Mac app does, a minute after the Mac wakes).
  - If any keywords haven't been refreshed today, refreshes them all.
  - Two seconds between App Store reads while Apple answers; longer, through
    the adaptive pacing every App Store loop uses (aso/throttle.py), while
    Apple says it is busy or does not answer. A refresh Apple keeps turning
    away stops, and the next check carries on with the keywords left.
  - Cleans up results older than 90 days after each refresh cycle.
  - Deletes the sessions that have expired at every check.

Only one refresh runs at a time, whoever starts it (the daily check, the
Dashboard's Refresh Rankings button, the Mac app's menu bar): each claims
the running flag through _claim_refresh() first.
"""

import logging
import threading
import time
from contextlib import contextmanager
from datetime import timedelta

from django.db import models
from django.utils import timezone

from . import run_queue

logger = logging.getLogger(__name__)

# ── In-memory progress state (single-worker, thread-safe enough) ──────────

_status_lock = threading.Lock()
_refresh_status = {
    "running": False,
    "total": 0,
    "completed": 0,
    "current_keyword": "",
    "started_at": None,
    "last_completed_at": None,
    "error": None,
}

RETENTION_DAYS = 90

# The pause between real App Store reads while Apple answers normally.
REFRESH_PACE_SECONDS = 2.0

# Set by nudge(): the loop checks for a due refresh at once instead of
# finishing its hourly wait.
_wake = threading.Event()


def get_status():
    """Return a snapshot of the current refresh status."""
    with _status_lock:
        return dict(_refresh_status)


def _update_status(**kwargs):
    with _status_lock:
        _refresh_status.update(kwargs)


def _claim_refresh(total):
    """Mark a refresh as running, unless one already is. True when claimed.

    The one gate every refresh passes, so two can never run at once. Before
    it, the hourly check could start the daily refresh while a manual one
    was still running, and two clicks could start two manual refreshes.
    """
    with _status_lock:
        if _refresh_status["running"]:
            return False
        _refresh_status.update(
            running=True,
            total=total,
            completed=0,
            current_keyword="",
            started_at=timezone.now().isoformat(),
            error=None,
        )
    return True


# The run queue (keyword searches, AI runs) shares Apple's request budget
# with the ranking refresh: neither starts while the other runs.
run_queue.busy_probes.append(lambda: "the ranking refresh" if get_status()["running"] else None)


# What other features add to the daily routine. The Pro build's Rival
# Tracker registers into both in its ready(); this module never imports it.
#   extra_pair_sources  callables returning (keyword_id, country) pairs the
#                       daily refresh must read today even when they have
#                       no row yet (a keyword just added to a Rival Tracker)
#   daily_hooks         callables run after each hourly check, while
#                       nothing else holds Apple's request budget
#   busy_hooks          callables run after each hourly check while something
#                       does hold it; they may only queue work, never read Apple
extra_pair_sources: list = []
daily_hooks: list = []
busy_hooks: list = []


def _extra_pairs() -> list:
    pairs = []
    for source in extra_pair_sources[:]:
        try:
            pairs.extend((int(keyword_id), str(country)) for keyword_id, country in source() or ())
        except Exception:
            logger.exception("A daily pair source failed")
    return pairs


def _refresh_finished(**kwargs):
    """The last status write of a refresh, then start whatever waited."""
    _update_status(running=False, current_keyword="", **kwargs)
    run_queue.kick()


# ── Core refresh logic ────────────────────────────────────────────────────

def _needs_refresh_today():
    """Check if any keyword+country pair hasn't been refreshed today, in the
    reader's own day (aso/local_day.py)."""
    from .local_day import today_start as local_today_start
    from .models import SearchResult

    today_start = local_today_start()
    return (
        SearchResult.objects
        .values("keyword_id", "country")
        .annotate(latest=models.Max("searched_at"))
        .filter(latest__lt=today_start)
        .exists()
    ) or bool(_extra_pairs())


def _get_pairs_to_refresh():
    """Return list of (keyword_id, country) pairs that need refreshing today."""
    from .local_day import today_start as local_today_start
    from .models import SearchResult

    today_start = local_today_start()
    stale = (
        SearchResult.objects
        .values("keyword_id", "country")
        .annotate(latest=models.Max("searched_at"))
        .filter(latest__lt=today_start)
    )
    pairs = [(row["keyword_id"], row["country"]) for row in stale]
    seen = set(pairs)
    for pair in _extra_pairs():
        if pair not in seen:
            seen.add(pair)
            pairs.append(pair)
    return pairs


def _refresh_pair(keyword_obj, country, *, force=False):
    """Refresh a single keyword+country pair. Returns the new SearchResult,
    or None when App Store search data is unavailable (logged, skipped).

    ``force=False`` (the daily refresh) writes the row from today's read
    when the keyword was already read today anywhere in the app;
    ``force=True`` (the user's bulk refresh) asks Apple again."""
    from .keyword_scoring import score_keyword_pair
    from .services import (
        DifficultyCalculator,
        DownloadEstimator,
        ITunesSearchService,
        SearchAPIUnavailableError,
    )

    try:
        return score_keyword_pair(
            keyword_obj, country,
            itunes_service=ITunesSearchService(),
            difficulty_calc=DifficultyCalculator(),
            download_est=DownloadEstimator(),
            force=force,
        )
    except SearchAPIUnavailableError as e:
        logger.warning(
            f"Skipping refresh for {keyword_obj.keyword} ({country}): {e}"
        )
        return None


def _cleanup_old_results():
    """Delete SearchResults older than RETENTION_DAYS, and tidy the day
    reads (aso/day_reads.py) with the same retention."""
    from . import day_reads
    from .models import SearchResult

    cutoff = timezone.now() - timedelta(days=RETENTION_DAYS)
    deleted_count, _ = SearchResult.objects.filter(searched_at__lt=cutoff).delete()
    if deleted_count:
        logger.info(f"Cleaned up {deleted_count} results older than {RETENTION_DAYS} days.")
    day_reads.tidy(RETENTION_DAYS)


def _refresh_pairs(pairs, label, *, force=False, claimed=False):
    """Refresh each (keyword_id, country) pair in turn, with the progress the
    Dashboard's bar shows. Each pair's new row reaches the Dashboard's table
    as it is written (aso/history_revision.py). Returns the pairs refreshed.

    The one loop behind the daily refresh (``force=False``: a keyword read
    today anywhere in the app is written from that read, without asking
    Apple) and the manual bulk refresh (``force=True``: the user asked for
    fresh numbers, so each keyword is read from Apple again).

    ``claimed`` is True when the caller already holds the running flag
    (run_manual_refresh claims it before its thread starts); otherwise this
    claims it, and returns 0 at once when another refresh holds it.

    Paced by the adaptive limiter every App Store loop uses (aso/throttle.py):
    REFRESH_PACE_SECONDS between real reads while Apple answers, longer (at
    least what Apple's Retry-After asks) while Apple says it is busy or does
    not answer. When Apple keeps turning reads away (the limiter's "aborted"
    state), the refresh stops instead of asking for every pair left: those
    pairs have no row from today, so the next hourly check carries on with
    them. Until 2.28.1 a busy answer failed each pair in turn at the same
    two second pace, through the whole list.
    """
    from . import day_reads
    from .models import Keyword
    from .services import ITunesRateLimited
    from .throttle import AdaptiveITunesRateLimiter, classify_throttle_state

    total = len(pairs)
    if not claimed and not _claim_refresh(total):
        logger.info(f"{label} skipped: another refresh is running.")
        return 0
    logger.info(f"{label} starting: {total} keyword+country pairs.")

    refreshed = skipped = busy = failed = 0
    # A keyword tracked for two apps (or for an app and for none) in one
    # storefront is one App Store search: the first read writes both rows
    # (keyword_scoring.store_read), so the second is not read again.
    read_in_run = set()
    limiter = AdaptiveITunesRateLimiter(base_delay=REFRESH_PACE_SECONDS)
    # Waits on the limiter before real App Store reads only, never before a
    # read already stored today (aso/day_reads.py).
    pacer = day_reads.Pacer(wait=limiter.wait)
    asked = turned_away = 0     # real App Store reads, and those Apple refused or left unanswered
    try:
        for i, (keyword_id, country) in enumerate(pairs):
            try:
                keyword_obj = Keyword.objects.select_related("app").get(id=keyword_id)
            except Keyword.DoesNotExist:
                _update_status(completed=i + 1)
                continue

            search = (keyword_obj.keyword.lower(), country)
            if search in read_in_run:
                refreshed += 1
                continue

            _update_status(
                current_keyword=f"{keyword_obj.keyword} ({country.upper()})",
                completed=i,
            )

            try:
                fetches = pacer.before(keyword_obj.keyword, country, force=force)
                result = _refresh_pair(keyword_obj, country, force=force)
            except ITunesRateLimited as e:
                busy += 1
                asked += 1
                turned_away += 1
                limiter.record_failure(retry_after=e.retry_after)
                logger.info(f"{label}: Apple's App Store is busy at {keyword_obj.keyword} ({country}).")
            except Exception as e:  # noqa: BLE001 (one pair's failure must not end the run)
                failed += 1
                logger.warning(f"{label} failed for {keyword_obj.keyword} ({country}): {e}")
            else:
                if result is None:
                    skipped += 1
                else:
                    refreshed += 1
                    read_in_run.add(search)
                if fetches:
                    asked += 1
                    if result is None:     # Apple could not be reached
                        turned_away += 1
                        limiter.record_failure()
                    else:
                        limiter.record_success()

            if classify_throttle_state(limiter, attempted=asked, failed=turned_away) == "aborted":
                left = total - (i + 1)
                logger.warning(
                    f"{label} stopped: Apple's App Store keeps turning reads away. "
                    f"{refreshed} of {total} pairs refreshed; {left + busy + skipped} wait for the next check."
                )
                _refresh_finished(completed=i + 1)
                return refreshed
    except Exception as e:  # noqa: BLE001 (recorded below; the flag must never stay stuck)
        # Outside any one pair (the database itself): end the run, or the
        # status would say "running" until the app restarts and every later
        # refresh would be refused.
        logger.error(f"{label} stopped after {refreshed} of {total} pairs: {e}")
        _refresh_finished(error=str(e))
        return refreshed

    _refresh_finished(completed=total, last_completed_at=timezone.now().isoformat())
    logger.info(
        f"{label} complete: {refreshed} of {total} pairs refreshed, "
        f"{skipped} skipped (App Store unavailable), {busy} busy, {failed} failed."
    )
    return refreshed


def _run_daily_refresh():
    """Refresh all keyword+country pairs that haven't been updated today."""
    pairs = _get_pairs_to_refresh()
    if not pairs:
        return
    _refresh_pairs(pairs, "Auto-refresh")
    _cleanup_old_results()


def _clear_expired_sessions():
    """Delete the sessions whose time is up, with Django's own clearsessions.

    What the app remembers for a visitor lives in the session for a year
    (SESSION_COOKIE_AGE in core/settings.py, aso/ui_memory.py), the Mac app
    starts a new one at every launch, and Django deletes an expired row only
    when clearsessions runs. Nothing else runs it, so the hourly tick does.
    """
    from django.core.management import call_command

    call_command("clearsessions")


# ── Scheduler thread ─────────────────────────────────────────────────────

def _tick():
    """One hourly check: sync Apple popularity, then run today's refresh if
    it is still due and nothing else holds the Apple budget."""
    try:
        # Apple popularity sync first, so today's refresh snapshots can
        # pick up fresh Apple values (the sync also patches rows created
        # before it finished). Internally a no-op unless configured.
        from .apple_ads.sync import maybe_run_sync

        maybe_run_sync()
    except Exception as e:  # noqa: BLE001 (the refresh below must still run)
        logger.error(f"Apple popularity sync scheduling error: {e}")

    try:
        # Empty the competitor lists of days that ended since the last tick
        # (aso/day_reads.py); a no-op when there is nothing to do.
        from . import day_reads

        day_reads.tidy(RETENTION_DAYS)
    except Exception as e:  # noqa: BLE001 (the hourly loop must outlive any error here)
        logger.error(f"Day read tidy error: {e}")

    try:
        _clear_expired_sessions()
    except Exception as e:  # noqa: BLE001 (the hourly loop must outlive any error here)
        logger.error(f"Session cleanup error: {e}")

    try:
        if _needs_refresh_today():
            if run_queue.lane_state() != "idle":
                # A keyword search or an AI run holds the Apple budget;
                # try again next hour.
                logger.info("Daily refresh postponed: the run lane is busy.")
            else:
                _run_daily_refresh()
    except Exception as e:  # noqa: BLE001 (the scheduler thread must survive to the next check)
        logger.error(f"Scheduler error: {e}")
        _refresh_finished(error=str(e))

    # What other features do once a day, after the refresh (the Rival
    # Tracker's check), while nothing else holds the Apple budget.
    for hook in daily_hooks[:]:
        try:
            if run_queue.lane_state() == "idle" and not get_status()["running"]:
                hook()
        except Exception as e:  # noqa: BLE001 (one hook's failure must not stop the hourly loop)
            logger.error(f"Daily hook error: {e}")
    if run_queue.lane_state() != "idle" or get_status()["running"]:
        for hook in busy_hooks[:]:
            try:
                hook()
            except Exception as e:  # noqa: BLE001 (one hook's failure must not stop the hourly loop)
                logger.error(f"Busy hook error: {e}")


def nudge():
    """Check for a due refresh now instead of at the next hourly check.

    The Mac app calls it a minute after the Mac wakes
    (desktop/mac_integration.py): the hourly wait does not count time
    asleep, so without it a missed daily refresh could wait up to an hour
    after the lid opens. Harmless at any time: a check with nothing due
    does nothing, and checks never overlap (one scheduler thread).
    """
    logger.info("Scheduler nudged: checking for a due refresh now.")
    _wake.set()


def _wait_for_next_check(seconds=3600):
    """Wait until the next check: an hour, or until nudge(). True when nudged."""
    nudged = _wake.wait(seconds)
    _wake.clear()
    return nudged


def _scheduler_loop():
    """Main scheduler loop. Checks hourly if a refresh is needed, and at once
    when nudge() asks."""
    # Wait 30 seconds for the app to fully start
    time.sleep(30)

    while True:
        _tick()
        _wait_for_next_check()


def run_manual_refresh(pairs):
    """
    Run a manual bulk refresh in a background thread.

    *pairs* is a list of (keyword_id, country) tuples — only these will be
    refreshed.  Uses the same in-memory progress state as the automatic
    scheduler so the dashboard progress bar works identically.

    Returns True when the refresh started and False when it could not: a
    refresh (manual or automatic) is already running, or the run lane is
    busy with a keyword search or an AI run (they share Apple's budget).
    """
    if not pairs:
        return False
    if run_queue.lane_state() != "idle":
        return False
    if not _claim_refresh(len(pairs)):
        return False  # Already busy

    def _work():
        _refresh_pairs(pairs, "Manual bulk refresh", force=True, claimed=True)

    thread = threading.Thread(target=_work, daemon=True, name="aso-manual-refresh")
    thread.start()
    return True


def refresh_pairs_now(pairs, label, *, force=False):
    """Refresh pairs in the calling thread, for a caller that already runs
    in the background (the Rival Tracker's daily check and its Refresh
    button). Returns how many were refreshed, or None when another refresh
    holds the running flag or the run lane is busy (they share Apple's
    budget). ``force`` is the bulk refresh's: Apple is read again even for a
    keyword read today, through score_keyword_pair(..., force=True)."""
    if not pairs:
        return 0
    if run_queue.lane_state() != "idle":
        return None
    if not _claim_refresh(len(pairs)):
        return None
    return _refresh_pairs(pairs, label, force=force, claimed=True)


@contextmanager
def refresh_claimed(label, total):
    """Hold the one refresh flag (_claim_refresh) for Apple work that is not
    a keyword refresh (the Rival Tracker's lookups and reviews), so it never
    overlaps a refresh. Yields False, holding nothing, when another refresh
    holds the flag. The Dashboard's progress bar shows ``label`` meanwhile;
    ``step(done)`` inside the block moves it."""
    if not _claim_refresh(total):
        yield False
        return
    _update_status(current_keyword=label)
    try:
        yield True
    finally:
        _refresh_finished(completed=total)


def refresh_step(done, label=None):
    """Move the progress bar of work held through refresh_claimed()."""
    if label:
        _update_status(completed=done, current_keyword=label)
    else:
        _update_status(completed=done)


def bulk_refresh_pairs(app_id=None, country=""):
    """Every (keyword_id, country) pair that already has a result, narrowed
    to one app and/or one storefront when given. Only pairs with a result:
    that is what keeps a keyword from being scored in a country it was never
    searched in.

    The Dashboard's Refresh Rankings button passes its filters; the Mac app's
    Update All Tracked Keywords passes none.
    """
    from django.db.models import Max

    from .models import SearchResult

    rows = SearchResult.objects.all()
    if app_id:
        rows = rows.filter(keyword__app_id=app_id)
    if country:
        rows = rows.filter(country=country)
    return list(
        rows.values("keyword_id", "country")
        .annotate(_latest=Max("id"))
        .values_list("keyword_id", "country")
    )


def bulk_refresh_question(app=None, country="", countries=()) -> str:
    """The Refresh Rankings confirm: one sentence saying what
    bulk_refresh_pairs() for this app and storefront will refresh.

    ``app`` is the App the Dashboard shows, or None for All Apps, which also
    refreshes keywords that belong to no app; ``country`` the storefront
    filter, "" for every storefront; ``countries`` the storefronts with
    results in this view, so a view of one storefront names it. The dialog
    used to say "all tracked keywords across all countries" whatever the
    view, while the button refreshed only the app and country on screen.
    """
    from aso.countries import store_phrase

    from .scoring import sentence_app_name

    countries = list(countries)
    if not country and len(countries) == 1:
        country = countries[0]
    where = store_phrase(country.lower()) if country else "every country"
    if app is not None:
        return f"Update every keyword of {sentence_app_name(app.name)} in {where} now?"
    return f"Update every tracked keyword, for all apps, in {where} now?"


def start_bulk_refresh(pairs):
    """Start a manual refresh of *pairs*: (outcome, sentence).

    outcome is "started", "nothing" (no pairs), "refreshing" (a refresh
    already runs) or "run_busy" (a keyword search or an AI run holds Apple's
    budget); the sentence says why when it did not start.
    """
    if not pairs:
        return "nothing", "No tracked keywords to refresh yet."
    if get_status()["running"]:
        return "refreshing", "A refresh is already in progress."
    running = run_queue.running_run()
    if running is not None:
        return "run_busy", f"{running[0].label} is running. Refresh when it finishes."
    if not run_manual_refresh(pairs):
        return "refreshing", "A refresh is already in progress."
    return "started", ""


def menu_status(now=None):
    """What the Mac app's menu bar menu shows (desktop/mac_integration.py):
    (status line, whether Update All Tracked Keywords can start, that item's title).

    The line names the refresh in progress, or when the newest ranking was
    read, in the reader's own day (aso/local_day.py).
    """
    from .desktop_bridge import COPY
    from .local_day import local_date, user_timezone
    from .models import SearchResult

    refresh_title = COPY["menu_refresh"]
    status = get_status()
    if status["running"]:
        total = status["total"] or 0
        done = min((status["completed"] or 0) + 1, total)
        return f"Updating tracked keywords: {done} of {total}", False, refresh_title

    newest = (
        SearchResult.objects.order_by("-searched_at")
        .values_list("searched_at", flat=True)
        .first()
    )
    if newest is None:
        return "No tracked keywords yet", False, refresh_title

    moment = timezone.localtime(newest, user_timezone())
    today = local_date(now or timezone.now())
    clock = moment.strftime("%H:%M")
    if moment.date() == today:
        line = f"Tracked keywords updated today at {clock}"
    elif moment.date() == today - timedelta(days=1):
        line = f"Tracked keywords updated yesterday at {clock}"
    else:
        line = f"Tracked keywords updated on {moment.day} {moment.strftime('%b')} at {clock}"

    running = run_queue.running_run()
    if running is not None:
        return line, False, f"Update after {running[0].label} finishes"
    if run_queue.lane_state() != "idle":
        return line, False, refresh_title
    return line, True, refresh_title


_scheduler_started = False
_scheduler_lock = threading.Lock()


def start_scheduler():
    """Start the background scheduler thread (idempotent).

    RESPECTASO_DISABLE_SCHEDULER=1 keeps it off - used by scratch/E2E
    servers so background refreshes and Apple syncs never mutate seeded
    data or hit live APIs mid-test.
    """
    import os

    if os.environ.get("RESPECTASO_DISABLE_SCHEDULER") == "1":
        logger.info("Auto-refresh scheduler disabled via environment.")
        return
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    thread = threading.Thread(target=_scheduler_loop, daemon=True, name="aso-auto-refresh")
    thread.start()
    logger.info("Auto-refresh scheduler started.")
