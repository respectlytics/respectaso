"""
Background auto-refresh scheduler for RespectASO.

Runs a daemon thread that periodically refreshes all tracked keywords.
Progress is tracked in-memory so the dashboard can show a non-blocking
progress indicator.

Schedule:
  - Checks once per hour whether today's refresh has run.
  - If any keywords haven't been refreshed today, refreshes them all.
  - 2-second sleep between API calls to respect Apple rate limits.
  - Cleans up results older than 90 days after each refresh cycle.
"""

import logging
import threading
import time
from datetime import timedelta

from django.db import models
from django.utils import timezone

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


def get_status():
    """Return a snapshot of the current refresh status."""
    with _status_lock:
        return dict(_refresh_status)


def _update_status(**kwargs):
    with _status_lock:
        _refresh_status.update(kwargs)


# ── Core refresh logic ────────────────────────────────────────────────────

def _needs_refresh_today():
    """Check if any keyword+country pair hasn't been refreshed today."""
    from .models import SearchResult

    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return (
        SearchResult.objects
        .values("keyword_id", "country")
        .annotate(latest=models.Max("searched_at"))
        .filter(latest__lt=today_start)
        .exists()
    )


def _get_pairs_to_refresh():
    """Return list of (keyword_id, country) pairs that need refreshing today."""
    from .models import SearchResult

    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    stale = (
        SearchResult.objects
        .values("keyword_id", "country")
        .annotate(latest=models.Max("searched_at"))
        .filter(latest__lt=today_start)
    )
    return [(row["keyword_id"], row["country"]) for row in stale]


def _refresh_pair(keyword_obj, country):
    """Refresh a single keyword+country pair. Returns the new SearchResult."""
    from .models import SearchResult
    from .services import (
        DifficultyCalculator,
        DownloadEstimator,
        ITunesSearchService,
        PopularityEstimator,
    )

    itunes_service = ITunesSearchService()
    difficulty_calc = DifficultyCalculator()
    popularity_est = PopularityEstimator()
    download_est = DownloadEstimator()

    competitors = itunes_service.search_apps(
        keyword_obj.keyword, country=country, limit=25
    )
    difficulty_score, breakdown = difficulty_calc.calculate(
        competitors, keyword=keyword_obj.keyword
    )

    app_rank = None
    if keyword_obj.app and keyword_obj.app.track_id:
        app_rank = itunes_service.find_app_rank(
            keyword_obj.keyword, keyword_obj.app.track_id, country=country
        )

    popularity = popularity_est.estimate(competitors, keyword_obj.keyword)

    download_estimates = download_est.estimate(popularity or 0, country=country)
    breakdown["download_estimates"] = download_estimates

    return SearchResult.upsert_today(
        keyword=keyword_obj,
        popularity_score=popularity,
        difficulty_score=difficulty_score,
        difficulty_breakdown=breakdown,
        competitors_data=competitors,
        app_rank=app_rank,
        country=country,
    )


def _cleanup_old_results():
    """Delete SearchResults older than RETENTION_DAYS."""
    from .models import SearchResult

    cutoff = timezone.now() - timedelta(days=RETENTION_DAYS)
    deleted_count, _ = SearchResult.objects.filter(searched_at__lt=cutoff).delete()
    if deleted_count:
        logger.info(f"Cleaned up {deleted_count} results older than {RETENTION_DAYS} days.")


def _run_daily_refresh():
    """Refresh all keyword+country pairs that haven't been updated today."""
    from .models import Keyword

    pairs = _get_pairs_to_refresh()
    if not pairs:
        return

    total = len(pairs)
    _update_status(
        running=True,
        total=total,
        completed=0,
        current_keyword="",
        started_at=timezone.now().isoformat(),
        error=None,
    )

    logger.info(f"Auto-refresh starting: {total} keyword+country pairs to refresh.")

    for i, (keyword_id, country) in enumerate(pairs):
        try:
            keyword_obj = Keyword.objects.select_related("app").get(id=keyword_id)
        except Keyword.DoesNotExist:
            _update_status(completed=i + 1)
            continue

        _update_status(
            current_keyword=f"{keyword_obj.keyword} ({country.upper()})",
            completed=i,
        )

        try:
            if i > 0:
                time.sleep(2)  # Rate limit
            _refresh_pair(keyword_obj, country)
        except Exception as e:
            logger.warning(f"Auto-refresh failed for {keyword_obj.keyword} ({country}): {e}")

    _update_status(
        running=False,
        completed=total,
        current_keyword="",
        last_completed_at=timezone.now().isoformat(),
    )

    # Cleanup old results after refresh
    _cleanup_old_results()

    logger.info(f"Auto-refresh complete: {total} pairs refreshed.")


# ── Scheduler thread ─────────────────────────────────────────────────────

def _scheduler_loop():
    """Main scheduler loop. Checks hourly if a refresh is needed."""
    # Wait 30 seconds for the app to fully start
    time.sleep(30)

    while True:
        try:
            if _needs_refresh_today():
                _run_daily_refresh()
        except Exception as e:
            logger.error(f"Scheduler error: {e}")
            _update_status(running=False, error=str(e))

        # Sleep 1 hour before checking again
        time.sleep(3600)


def run_manual_refresh(pairs):
    """
    Run a manual bulk refresh in a background thread.

    *pairs* is a list of (keyword_id, country) tuples — only these will be
    refreshed.  Uses the same in-memory progress state as the automatic
    scheduler so the dashboard progress bar works identically.

    Returns immediately.  If a refresh (manual or automatic) is already
    running, does nothing.
    """
    from .models import Keyword

    with _status_lock:
        if _refresh_status["running"]:
            return  # Already busy

    if not pairs:
        return

    def _work():
        total = len(pairs)
        _update_status(
            running=True,
            total=total,
            completed=0,
            current_keyword="",
            started_at=timezone.now().isoformat(),
            error=None,
        )

        logger.info(f"Manual bulk refresh starting: {total} keyword+country pairs.")

        for i, (keyword_id, country) in enumerate(pairs):
            try:
                keyword_obj = Keyword.objects.select_related("app").get(id=keyword_id)
            except Keyword.DoesNotExist:
                _update_status(completed=i + 1)
                continue

            _update_status(
                current_keyword=f"{keyword_obj.keyword} ({country.upper()})",
                completed=i,
            )

            try:
                if i > 0:
                    time.sleep(2)  # Rate limit
                _refresh_pair(keyword_obj, country)
            except Exception as e:
                logger.warning(
                    f"Manual refresh failed for {keyword_obj.keyword} ({country}): {e}"
                )

        _update_status(
            running=False,
            completed=total,
            current_keyword="",
            last_completed_at=timezone.now().isoformat(),
        )
        logger.info(f"Manual bulk refresh complete: {total} pairs refreshed.")

    thread = threading.Thread(target=_work, daemon=True, name="aso-manual-refresh")
    thread.start()


_scheduler_started = False
_scheduler_lock = threading.Lock()


def start_scheduler():
    """Start the background scheduler thread (idempotent)."""
    global _scheduler_started
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True

    thread = threading.Thread(target=_scheduler_loop, daemon=True, name="aso-auto-refresh")
    thread.start()
    logger.info("Auto-refresh scheduler started.")
