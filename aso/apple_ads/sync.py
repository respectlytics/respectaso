"""Background sync of Apple popularity values into AppleSearchPopularity.

Fetches Apple's granular popularity for every tracked keyword+country pair
(plus any terms queued by the resolution layer's on-miss enrichment) and
stores them locally. Scoring never waits on this - it reads the table only.

Pacing (see client.py for the full policy):
  * One worker; batch calls strictly sequential with a courtesy delay.
  * The delay doubles for the rest of a run on every 429 (adaptive).
  * When retries are exhausted on 429, the run aborts gracefully: fetched
    values are already committed batch-by-batch, unfetched terms remain
    queued, and the next scheduler tick resumes automatically.
  * Self-imposed ceilings: MAX_REQUESTS_PER_RUN per run and
    MAX_REQUESTS_PER_DAY per rolling 24h (request log in settings).
"""

import logging
import random
import threading
import time
from datetime import timedelta

from django.utils import timezone

from . import auth, storage
from .client import (
    MAX_TERMS_PER_CALL,
    AppleAdsAppAccessError,
    AppleAdsAuthError,
    AppleAdsError,
    AppleAdsRateLimitedError,
    fetch_popularities,
)

logger = logging.getLogger(__name__)

BASE_PACING_DELAY = 2.0        # Seconds between batch calls (Layer 1).
MAX_PACING_DELAY = 30.0        # Adaptive ceiling (Layer 3).
MAX_REQUESTS_PER_RUN = 200     # Layer 4 ceilings.
MAX_REQUESTS_PER_DAY = 500
STARTUP_JITTER_SECONDS = 300   # Daily sync starts at a randomized offset.

INLINE_MAX_REQUESTS = 5        # Ceiling per ensure_apple_values() call.
INLINE_BACKOFF_SECONDS = 120.0  # Pause inline fetching after a failure.

# ── In-memory state ───────────────────────────────────────────────────────

_status_lock = threading.Lock()
_sync_status = {
    "running": False,
    "total_batches": 0,
    "completed_batches": 0,
    "started_at": None,
}

_queue_lock = threading.Lock()
_enrichment_queue: set[tuple[str, str]] = set()  # (term, country)

_daily_thread_lock = threading.Lock()
_daily_thread_running = False

_inline_lock = threading.Lock()
_inline_backoff_until = 0.0  # time.monotonic() before which inline fetches skip.


def get_status() -> dict:
    """Snapshot of the in-memory sync progress plus persisted results."""
    with _status_lock:
        status = dict(_sync_status)
    block = storage.load_apple_settings()["apple_ads"]
    status.update(
        {
            "last_sync_at": block["last_sync_at"],
            "last_sync_status": block["last_sync_status"],
            "last_sync_error": block["last_sync_error"],
            "coverage": block["coverage"],
            "session_expired": block["session_expired"],
        }
    )
    return status


def enqueue_term(term: str, country: str) -> None:
    """Queue a term the Apple source lacked (called from aso.popularity)."""
    if not term or not country:
        return
    with _queue_lock:
        _enrichment_queue.add((term, country.lower()))


def _drain_queue() -> list[tuple[str, str]]:
    with _queue_lock:
        items = list(_enrichment_queue)
        _enrichment_queue.clear()
        return items


def _requeue(items) -> None:
    with _queue_lock:
        _enrichment_queue.update(items)


# ── Request budget (Layer 4) ─────────────────────────────────────────────

def _requests_in_last_24h() -> int:
    cutoff = timezone.now() - timedelta(hours=24)
    log = storage.load_apple_settings()["apple_ads"]["request_log"]
    return sum(1 for ts in log if _parse_ts(ts) and _parse_ts(ts) > cutoff)


def _record_request() -> None:
    cutoff = timezone.now() - timedelta(hours=24)
    log = storage.load_apple_settings()["apple_ads"]["request_log"]
    log = [ts for ts in log if _parse_ts(ts) and _parse_ts(ts) > cutoff]
    log.append(timezone.now().isoformat())
    storage.save_apple_settings(apple_ads={"request_log": log})


def _parse_ts(value):
    try:
        parsed = timezone.datetime.fromisoformat(value)
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed)
        return parsed
    except (TypeError, ValueError):
        return None


# ── Work-list construction ───────────────────────────────────────────────

def _tracked_pairs() -> list[tuple[str, str]]:
    """Distinct (normalized term, country) pairs from tracked keywords."""
    from ..models import SearchResult

    pairs = (
        SearchResult.objects.values_list("keyword__keyword", "country")
        .distinct()
        .order_by()
    )
    seen = set()
    result = []
    for keyword, country in pairs:
        term = (keyword or "").lower().strip()
        key = (term, (country or "").lower())
        if term and key not in seen:
            seen.add(key)
            result.append(key)
    return result


def _pairs_needing_fetch(pairs) -> list[tuple[str, str]]:
    """Drop pairs already fetched today (makes abort-resume natural)."""
    from ..models import AppleSearchPopularity

    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    fresh = set(
        AppleSearchPopularity.objects.filter(fetched_at__gte=today_start)
        .values_list("term", "country")
    )
    return [pair for pair in pairs if pair not in fresh]


# ── Core sync run ────────────────────────────────────────────────────────

def _run_sync(pairs) -> None:
    """Fetch Apple popularity for the given (term, country) pairs.

    Commits batch-by-batch; on rate-limit exhaustion aborts gracefully and
    requeues the remainder. Updates persisted status and coverage.
    """
    from ..models import AppleSearchPopularity

    block = storage.load_apple_settings()["apple_ads"]
    primary_app_id = block["primary_app_id"]
    header = auth.cookie_header()
    if not header or not primary_app_id:
        _finish("error", "Not signed in or Primary App ID missing.")
        return

    by_country: dict[str, list[str]] = {}
    for term, country in pairs:
        by_country.setdefault(country, []).append(term)

    batches = []
    for country, terms in sorted(by_country.items()):
        for i in range(0, len(terms), MAX_TERMS_PER_CALL):
            batches.append((country, terms[i : i + MAX_TERMS_PER_CALL]))

    with _status_lock:
        _sync_status.update(
            running=True,
            total_batches=len(batches),
            completed_batches=0,
            started_at=timezone.now().isoformat(),
        )

    pacing_delay = BASE_PACING_DELAY
    requests_this_run = 0
    outcome, error_message = "completed", ""

    for index, (country, terms) in enumerate(batches):
        if requests_this_run >= MAX_REQUESTS_PER_RUN:
            outcome, error_message = "partial", (
                "Per-run request ceiling reached - remaining keywords "
                "resume on the next automatic sync."
            )
            _requeue((t, country) for t in terms)
            _requeue_remaining(batches[index + 1 :])
            break
        if _requests_in_last_24h() >= MAX_REQUESTS_PER_DAY:
            outcome, error_message = "partial", (
                "Daily request ceiling reached - remaining keywords resume "
                "automatically within 24 hours."
            )
            _requeue((t, country) for t in terms)
            _requeue_remaining(batches[index + 1 :])
            break

        if index > 0:
            time.sleep(pacing_delay)

        try:
            _record_request()
            requests_this_run += 1
            values = fetch_popularities(terms, country, primary_app_id, header)
        except AppleAdsAuthError:
            auth.mark_session_expired()
            outcome, error_message = "error", (
                "Apple sign-in expired. Sign in again from Settings."
            )
            _requeue((t, country) for t in terms)
            _requeue_remaining(batches[index + 1 :])
            break
        except AppleAdsAppAccessError as e:
            outcome, error_message = "error", str(e)
            break
        except AppleAdsRateLimitedError:
            pacing_delay = min(MAX_PACING_DELAY, pacing_delay * 2)
            outcome, error_message = "rate_limited", (
                "Rate limited by Apple - remaining keywords resume "
                "automatically on the next sync."
            )
            _requeue((t, country) for t in terms)
            _requeue_remaining(batches[index + 1 :])
            break
        except AppleAdsError as e:
            logger.warning("Apple popularity batch failed (%s): %s", country, e)
            _requeue((t, country) for t in terms)
            outcome, error_message = "partial", str(e)
            with _status_lock:
                _sync_status["completed_batches"] = index + 1
            continue

        # Commit this batch: every requested term gets a row; terms Apple
        # returned null (or didn't echo) are stored as null so the daily
        # sync doesn't re-query known-empty terms.
        for term in terms:
            AppleSearchPopularity.objects.update_or_create(
                term=term,
                country=country,
                defaults={"popularity": values.get(term)},
            )
        with _status_lock:
            _sync_status["completed_batches"] = index + 1

    _patch_today_rows()
    _update_coverage()
    _finish(outcome, error_message)


def _requeue_remaining(remaining_batches) -> None:
    for country, terms in remaining_batches:
        _requeue((t, country) for t in terms)


def _patch_today_rows() -> None:
    """Copy fresh Apple values into today's SearchResult snapshots.

    save() recomputes the stored classification from the effective value.
    """
    from ..models import AppleSearchPopularity, SearchResult
    from ..popularity import normalize_term

    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    for result in SearchResult.objects.filter(
        searched_at__gte=today_start
    ).select_related("keyword"):
        apple = AppleSearchPopularity.lookup(
            normalize_term(result.keyword.keyword), result.country.lower()
        )
        if apple != result.apple_popularity_score:
            result.apple_popularity_score = apple
            result.save()


def _update_coverage() -> None:
    from ..models import AppleSearchPopularity

    pairs = _tracked_pairs()
    matched = 0
    by_country: dict[str, list[str]] = {}
    for term, country in pairs:
        by_country.setdefault(country, []).append(term)
    for country, terms in by_country.items():
        values = AppleSearchPopularity.bulk_lookup(terms, country)
        matched += sum(1 for t in terms if values.get(t) is not None)
    storage.save_apple_settings(
        apple_ads={
            "coverage": {
                "terms": AppleSearchPopularity.objects.filter(
                    popularity__isnull=False
                ).count(),
                "tracked_matched": matched,
                "tracked_total": len(pairs),
            }
        }
    )


def _finish(outcome: str, error_message: str) -> None:
    storage.save_apple_settings(
        apple_ads={
            "last_sync_at": timezone.now().isoformat(),
            "last_sync_status": outcome,
            "last_sync_error": error_message,
        }
    )
    with _status_lock:
        _sync_status.update(running=False, started_at=None)
    logger.info("Apple popularity sync finished: %s %s", outcome, error_message)


# ── Entry points ─────────────────────────────────────────────────────────

def _sync_ready() -> bool:
    block = storage.load_apple_settings()["apple_ads"]
    return bool(block["tested_ok"]) and bool(block["cookies"])


def _synced_today() -> bool:
    block = storage.load_apple_settings()["apple_ads"]
    last = _parse_ts(block["last_sync_at"])
    if not last or block["last_sync_status"] != "completed":
        return False
    today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
    return last >= today_start


def _start_worker(pairs, *, jitter: float = 0.0) -> bool:
    """Start a sync worker thread unless one is already running."""
    global _daily_thread_running
    with _daily_thread_lock:
        if _daily_thread_running:
            return False
        _daily_thread_running = True

    def work():
        global _daily_thread_running
        try:
            if jitter:
                time.sleep(jitter)
            _run_sync(pairs)
        except Exception as e:  # Sync must never take down the scheduler.
            logger.error("Apple popularity sync crashed: %s", e)
            _finish("error", str(e))
        finally:
            with _daily_thread_lock:
                _daily_thread_running = False

    threading.Thread(target=work, daemon=True, name="apple-popularity-sync").start()
    return True


def maybe_run_sync() -> None:
    """Hourly hook for the scheduler loop.

    Runs the full daily sync once per day (randomized start offset), and in
    between drains the on-miss enrichment queue so newly discovered keywords
    gain Apple values within the hour.
    """
    if not _sync_ready():
        return
    if not _synced_today():
        pairs = _pairs_needing_fetch(_tracked_pairs()) + _drain_queue()
        if pairs:
            _start_worker(
                _dedupe(pairs), jitter=random.uniform(0, STARTUP_JITTER_SECONDS)
            )
        else:
            _finish("completed", "")
        return
    queued = _drain_queue()
    if queued:
        _start_worker(_pairs_needing_fetch(_dedupe(queued)))


def run_manual_sync() -> bool:
    """"Sync now" from the settings page. Returns False if already running."""
    if not _sync_ready():
        return False
    pairs = _dedupe(_tracked_pairs() + _drain_queue())
    return _start_worker(pairs)


def ensure_apple_values(terms, country) -> None:
    """Synchronously fetch Apple popularity for terms with no local row yet.

    Called from the scoring path (via aso.popularity) so a keyword gets its
    Apple value - and the opportunity computed from it - in the same request
    that scores it. Batched (<=100 terms per call) with the shared courtesy
    delay between calls, so callers scoring a whole list must pass the list
    up front, never one term at a time.

    Failure-safe by design: no-op when the Apple connection is not ready,
    bounded by INLINE_MAX_REQUESTS and the shared daily budget, and any
    failure backs off inline fetching for INLINE_BACKOFF_SECONDS while the
    unfetched terms are queued for the background sync. Scoring then falls
    back to the internal estimate exactly as before. Never raises.
    """
    from ..models import AppleSearchPopularity

    global _inline_backoff_until
    country = (country or "").lower()
    wanted = sorted({(t or "").lower().strip() for t in (terms or [])} - {""})
    if not wanted or not country or not _sync_ready():
        return
    block = storage.load_apple_settings()["apple_ads"]
    if block["session_expired"]:
        return
    primary_app_id = block["primary_app_id"]
    header = auth.cookie_header()
    if not header or not primary_app_id:
        return

    def _missing():
        known = set(
            AppleSearchPopularity.objects.filter(
                term__in=wanted, country=country
            ).values_list("term", flat=True)
        )
        return [t for t in wanted if t not in known]

    if not _missing():
        return
    if time.monotonic() < _inline_backoff_until:
        for term in _missing():
            enqueue_term(term, country)
        return

    with _inline_lock:
        # Another thread may have fetched some of these while we waited.
        missing = _missing()
        requests_made = 0
        for i in range(0, len(missing), MAX_TERMS_PER_CALL):
            batch = missing[i : i + MAX_TERMS_PER_CALL]
            if (
                requests_made >= INLINE_MAX_REQUESTS
                or _requests_in_last_24h() >= MAX_REQUESTS_PER_DAY
            ):
                for term in missing[i:]:
                    enqueue_term(term, country)
                return
            if requests_made:
                time.sleep(BASE_PACING_DELAY)
            try:
                _record_request()
                requests_made += 1
                values = fetch_popularities(batch, country, primary_app_id, header)
            except AppleAdsAuthError:
                auth.mark_session_expired()
                for term in missing[i:]:
                    enqueue_term(term, country)
                return
            except AppleAdsError as e:
                # Rate limits, app-access and transient failures alike: stop
                # fetching inline for a while; the background sync retries.
                logger.warning("Inline Apple fetch failed (%s): %s", country, e)
                _inline_backoff_until = time.monotonic() + INLINE_BACKOFF_SECONDS
                for term in missing[i:]:
                    enqueue_term(term, country)
                return
            # Every requested term gets a row (null when Apple has no value)
            # so known-empty terms are never re-fetched inline.
            for term in batch:
                AppleSearchPopularity.objects.update_or_create(
                    term=term,
                    country=country,
                    defaults={"popularity": values.get(term)},
                )


def _dedupe(pairs) -> list[tuple[str, str]]:
    seen = set()
    result = []
    for pair in pairs:
        if pair not in seen:
            seen.add(pair)
            result.append(pair)
    return result
