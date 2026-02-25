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

import json
import logging
import threading
import time
from datetime import date, timedelta

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
    # Get all distinct keyword+country pairs that have ever been searched
    pairs = (
        SearchResult.objects
        .values("keyword_id", "country")
        .distinct()
    )
    for pair in pairs:
        latest = (
            SearchResult.objects
            .filter(keyword_id=pair["keyword_id"], country=pair["country"])
            .order_by("-searched_at")
            .first()
        )
        if latest and latest.searched_at < today_start:
            return True
    return bool(pairs) and pairs.exists()


def _get_pairs_to_refresh():
    """Return list of (keyword_id, country) pairs that need refreshing today."""
    from .models import SearchResult

    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    pairs = (
        SearchResult.objects
        .values("keyword_id", "country")
        .distinct()
    )
    to_refresh = []
    for pair in pairs:
        latest = (
            SearchResult.objects
            .filter(keyword_id=pair["keyword_id"], country=pair["country"])
            .order_by("-searched_at")
            .first()
        )
        if latest and latest.searched_at < today_start:
            to_refresh.append((pair["keyword_id"], pair["country"]))
    return to_refresh


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

    download_estimates = download_est.estimate(popularity or 0, len(competitors))
    breakdown["download_estimates"] = download_estimates

    return SearchResult.objects.create(
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
