"""Background sync of Apple's weekly top-terms dataset into local tables.

The Apple Ads Platform API v1 popularity endpoint is a DATASET, not a
lookup: per storefront and completed Sun-Sat week it returns Apple's top
search terms (up to 500 per genre, 15 genres). This module downloads that
dataset per tracked country, keeps `AppleTopTerm` history, refreshes the
`AppleSearchPopularity` current-value cache, and backfills up to 65 weeks
so trends work from day one. Scoring never waits on the network - it
reads the local tables only. The one bounded synchronous path is
`ensure_country_dataset()` for a country's very first use.

Week activation and bad-data quarantine: a downloaded week only becomes
the ACTIVE week for a country (feeding lookups, band ceilings, trends)
after per-row validation and dataset-level sanity checks pass. A failing
week is discarded, the previous good week keeps serving, and the sync
retries on later ticks.

Rate-limit policy (see api.py for Layer 2):
  Layer 1 - proactive pacing: one worker, sequential page requests with
            a courtesy delay, plus header-aware waits when
            RateLimit-Remaining runs low (observed quota: 5/second).
  Layer 3 - adaptive slow-down: pacing doubles for the rest of a run on
            every 429 exhaustion; the run aborts gracefully and resumes
            on the next scheduler tick.
  Layer 4 - self-imposed ceilings: MAX_REQUESTS_PER_RUN per run and
            MAX_REQUESTS_PER_DAY per rolling 24h (request log in
            settings). Weekly syncs use a few requests; only backfill
            ever approaches the ceilings, by design.
"""

import datetime as dt
import logging
import random
import threading
import time
from datetime import timedelta

from django.utils import timezone

from . import api, storage
from .api import (
    AppleAdsAccessError,
    AppleAdsAuthError,
    AppleAdsError,
    AppleAdsRateLimitedError,
)

logger = logging.getLogger(__name__)

BASE_PACING_DELAY = 1.0        # Seconds between page requests (Layer 1).
MAX_PACING_DELAY = 30.0        # Adaptive ceiling (Layer 3).
MAX_REQUESTS_PER_RUN = 150     # Layer 4 ceilings.
MAX_REQUESTS_PER_DAY = 400
STARTUP_JITTER_SECONDS = 300   # New-week sync starts at a randomized offset.

INLINE_MAX_REQUESTS = 8        # Page ceiling per ensure_country_dataset().
INLINE_BACKOFF_SECONDS = 120.0  # Pause inline downloads after a failure.

# Retention (see the migration plan; tuned from Phase 0 measurements:
# a country-week is ~5-8k rows / under 1 MB).
FULL_WEEKS_RETAINED = 8        # Full dataset kept for the last N weeks.
TRACKED_WEEKS_RETAINED = 65    # Tracked-term history kept as far as Apple's
BACKFILL_WEEKS = 65            # rolling retention reaches.

INGEST_BATCH_ROWS = 2000       # bulk_create chunk size.

# Week-activation sanity gates (bad-data quarantine).
MIN_WEEK_ROWS = 500
MIN_PREV_RATIO = 0.3           # vs the previous active week's row count.
MAX_CONSTANT_SHARE = 0.95      # a week that is >95% one value is broken.
MIN_GENRES = 3

# ── In-memory state ───────────────────────────────────────────────────────

_status_lock = threading.Lock()
_sync_status = {
    "running": False,
    "phase": "",               # "" | weekly | impressions | backfill
    "country": "",
    "pages_done": 0,
    "started_at": None,
}

_worker_lock = threading.Lock()
_worker_running = False

_inline_lock = threading.Lock()
_inline_backoff_until = 0.0  # time.monotonic() before which inline skips.


class _CeilingReached(Exception):
    """Internal flow control: a Layer 4 ceiling stopped the run."""


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
            "active_weeks": block["active_weeks"],
            "backfill": block["backfill"],
            "impression_share": block["impression_share"],
            "credentials_rejected": block["credentials_rejected"],
        }
    )
    return status


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


def _tracked_countries() -> list[str]:
    return sorted({country for _term, country in _tracked_pairs()})


def _tracked_terms(country: str) -> set[str]:
    return {term for term, c in _tracked_pairs() if c == country}


# ── Pacing (Layer 1) ─────────────────────────────────────────────────────

def _pace(delay: float, sleeper=time.sleep) -> None:
    """Courtesy delay plus header-aware wait when quota runs low."""
    sleeper(delay)
    headers = api.get_last_rate_headers()
    if 0 <= headers["remaining"] < api.LOW_REMAINING_THRESHOLD:
        sleeper(max(0, headers["reset"]))


# ── Row validation (quarantine, per-row layer) ───────────────────────────

def _clean_rows(rows, country: str, week: dt.date) -> list[dict]:
    """Validate raw API rows into normalized ingest dicts; drop bad ones."""
    cleaned = []
    invalid = 0
    for row in rows:
        term = row.get("searchTerm")
        genre = row.get("genre")
        rank = row.get("rankInGenre")
        in_genre = row.get("searchPopularityInGenre")
        market = row.get("searchPopularity1to100")
        tier = row.get("searchPopularity1to5")
        if (
            not isinstance(term, str) or not term.strip()
            or not isinstance(genre, str) or not genre
            or not isinstance(rank, int) or not 1 <= rank <= 500
            or not isinstance(in_genre, int) or not 1 <= in_genre <= 100
            or not isinstance(market, int) or not 1 <= market <= 100
            or not isinstance(tier, int) or not 1 <= tier <= 5
        ):
            invalid += 1
            continue
        cleaned.append({
            "term": term.lower().strip()[:200],
            "country": country,
            "genre": genre[:100],
            "week": week,
            "rank_in_genre": rank,
            "popularity_in_genre": in_genre,
            "popularity": market,
            "popularity_tier": tier,
        })
    if invalid:
        logger.warning(
            "Apple top-terms ingest (%s, %s): dropped %d invalid rows.",
            country, week, invalid,
        )
    return cleaned


def _week_sane(country: str, week: dt.date, rows: list[dict]) -> str:
    """Dataset-level sanity gate. Returns "" when sane, else the reason."""
    from ..models import AppleTopTerm

    if len(rows) < MIN_WEEK_ROWS:
        return f"only {len(rows)} valid rows (need {MIN_WEEK_ROWS})"
    genres = {row["genre"] for row in rows}
    if len(genres) < MIN_GENRES:
        return f"only {len(genres)} genres (need {MIN_GENRES})"
    values = [row["popularity"] for row in rows]
    most_common = max(values.count(v) for v in set(values))
    if most_common / len(values) > MAX_CONSTANT_SHARE:
        return "popularity values are near-constant"
    active = storage.load_apple_settings()["apple_ads"]["active_weeks"].get(country)
    if active:
        prev_count = AppleTopTerm.objects.filter(
            country=country, week=dt.date.fromisoformat(active)
        ).count()
        if prev_count and len(rows) < prev_count * MIN_PREV_RATIO:
            return (
                f"{len(rows)} rows vs {prev_count} in the previous week "
                "(suspicious shrink)"
            )
    return ""


# ── Ingest ───────────────────────────────────────────────────────────────

def _fetch_week(credentials, ad_account_id, country, week, run_state,
                max_pages=None, sleeper=time.sleep) -> list[dict]:
    """Download and validate all pages of one country-week.

    Raises _CeilingReached when a Layer 4 ceiling stops the run, and
    AppleAdsError subclasses on API failures.
    """
    rows: list[dict] = []
    offset = 0
    pages = 0
    while True:
        if run_state["requests"] >= MAX_REQUESTS_PER_RUN:
            raise _CeilingReached("per-run request ceiling reached")
        if _requests_in_last_24h() >= MAX_REQUESTS_PER_DAY:
            raise _CeilingReached("daily request ceiling reached")
        if max_pages is not None and pages >= max_pages:
            raise _CeilingReached("inline page ceiling reached")
        if pages > 0:
            _pace(run_state["pacing"], sleeper=sleeper)
        _record_request()
        run_state["requests"] += 1
        page_rows, _total = api.query_search_term_popularity(
            credentials, ad_account_id,
            country=country, week_start=week, offset=offset,
            sleeper=sleeper,
        )
        rows.extend(_clean_rows(page_rows, country, week))
        pages += 1
        with _status_lock:
            _sync_status["pages_done"] += 1
        if len(page_rows) < api.PAGE_SIZE:
            return rows
        offset += len(page_rows)


def _persist_rows(rows: list[dict], tracked_only_terms=None) -> int:
    """Bulk-upsert ingest dicts into AppleTopTerm. Returns rows written."""
    from ..models import AppleTopTerm

    from django.db import transaction

    if tracked_only_terms is not None:
        rows = [row for row in rows if row["term"] in tracked_only_terms]
    written = 0
    now = timezone.now()
    for start in range(0, len(rows), INGEST_BATCH_ROWS):
        chunk = rows[start:start + INGEST_BATCH_ROWS]
        objects = [
            AppleTopTerm(fetched_at=now, **row) for row in chunk
        ]
        with transaction.atomic():
            AppleTopTerm.objects.bulk_create(
                objects,
                update_conflicts=True,
                update_fields=[
                    "rank_in_genre", "popularity_in_genre",
                    "popularity", "popularity_tier", "fetched_at",
                ],
                unique_fields=["term", "country", "genre", "week"],
            )
        written += len(chunk)
    return written


def _activate_week(country: str, week: dt.date) -> None:
    """Advance the country's active week and refresh everything derived."""
    from ..models import AppleTopTerm

    active_weeks = dict(
        storage.load_apple_settings()["apple_ads"]["active_weeks"]
    )
    active_weeks[country] = week.isoformat()
    storage.save_apple_settings(apple_ads={"active_weeks": active_weeks})
    AppleTopTerm.clear_floor_cache()  # restated weeks must refresh caps
    _refresh_current_values(country, week)
    _patch_today_rows()
    logger.info("Apple top-terms week %s activated for %s.", week, country)


def _refresh_current_values(country: str, week: dt.date) -> None:
    """Refresh the AppleSearchPopularity cache from the active week.

    Every cached term for the country is re-resolved (terms fall out of
    the dataset week to week), and every tracked term gets a row - null
    when absent, which downstream reads as "below Apple's threshold".
    """
    from ..models import AppleSearchPopularity, AppleTopTerm

    cached = set(
        AppleSearchPopularity.objects.filter(country=country)
        .values_list("term", flat=True)
    )
    terms = sorted(cached | _tracked_terms(country))
    if not terms:
        return
    values = AppleTopTerm.values_for_week(terms, country, week)
    for term in terms:
        AppleSearchPopularity.objects.update_or_create(
            term=term,
            country=country,
            defaults={"popularity": values.get(term)},
        )


def prune_top_terms() -> None:
    """Apply the retention policy (also covers the old missing-prune gap)."""
    from ..models import AppleTopTerm

    latest = api.latest_available_week()
    tracked_cutoff = api.weeks_back(latest, TRACKED_WEEKS_RETAINED)
    full_cutoff = api.weeks_back(latest, FULL_WEEKS_RETAINED)
    AppleTopTerm.objects.filter(week__lt=tracked_cutoff).delete()
    for country in (
        AppleTopTerm.objects.values_list("country", flat=True).distinct()
    ):
        AppleTopTerm.objects.filter(
            country=country, week__lt=full_cutoff
        ).exclude(term__in=_tracked_terms(country)).delete()


# ── Core sync run ────────────────────────────────────────────────────────

def _run_sync(force: bool = False) -> None:
    credentials = storage.api_credentials()
    block = storage.load_apple_settings()["apple_ads"]
    ad_account_id = block["ad_account_id"]
    if not credentials or not ad_account_id:
        _finish("error", "Apple Ads is not connected.")
        return

    countries = _tracked_countries() or ["us"]
    target = api.latest_available_week()
    run_state = {"requests": 0, "pacing": BASE_PACING_DELAY}
    outcome, error_message = "completed", ""

    with _status_lock:
        _sync_status.update(
            running=True, phase="weekly", country="", pages_done=0,
            started_at=timezone.now().isoformat(),
        )

    try:
        for country in countries:
            with _status_lock:
                _sync_status["country"] = country
            active = block["active_weeks"].get(country)
            weeks = _missing_weeks(active, target, force)
            for week in weeks:
                rows = _fetch_week(
                    credentials, ad_account_id, country, week, run_state
                )
                reason = _week_sane(country, week, rows)
                if reason:
                    outcome = "partial"
                    error_message = (
                        f"Apple's data for {country.upper()} (week of "
                        f"{week}) looked incomplete ({reason}) - keeping "
                        "the last good week. Retrying automatically."
                    )
                    logger.warning("Week quarantined: %s", error_message)
                    continue
                _persist_rows(rows)
                _activate_week(country, week)
            # Reload the block so later countries see fresh active_weeks.
            block = storage.load_apple_settings()["apple_ads"]

        _run_impressions(credentials, ad_account_id, run_state)
        _run_backfill(credentials, ad_account_id, run_state)
    except _CeilingReached as e:
        outcome, error_message = "partial", (
            f"{e} - the remaining work resumes on the next automatic sync."
        )
    except AppleAdsAuthError:
        storage.mark_credentials_rejected()
        outcome, error_message = "error", (
            "Apple rejected the API credentials - reconnect from Settings."
        )
    except AppleAdsAccessError as e:
        outcome, error_message = "error", str(e)
    except AppleAdsRateLimitedError:
        run_state["pacing"] = min(MAX_PACING_DELAY, run_state["pacing"] * 2)
        outcome, error_message = "rate_limited", (
            "Rate limited by Apple - the sync resumes automatically."
        )
    except AppleAdsError as e:
        outcome, error_message = "partial", str(e)

    prune_top_terms()
    _update_coverage()
    _finish(outcome, error_message)


def _missing_weeks(active_iso, target: dt.date, force: bool) -> list[dt.date]:
    """Weeks to ingest for a country, oldest first (normally just one)."""
    if not active_iso:
        return [target]
    try:
        active = dt.date.fromisoformat(active_iso)
    except ValueError:
        return [target]
    weeks = []
    week = active + timedelta(days=7)
    while week <= target:
        weeks.append(week)
        week += timedelta(days=7)
    if force and target not in weeks:
        weeks.append(target)  # Re-fetch: Apple occasionally restates data.
    return weeks


# ── Impression share (failure-isolated sub-run) ──────────────────────────

def _run_impressions(credentials, ad_account_id, run_state) -> None:
    from . import impressions

    def spend_request() -> bool:
        if run_state["requests"] >= MAX_REQUESTS_PER_RUN:
            return False
        if _requests_in_last_24h() >= MAX_REQUESTS_PER_DAY:
            return False
        _record_request()
        run_state["requests"] += 1
        return True

    with _status_lock:
        _sync_status["phase"] = "impressions"
    try:
        impressions.run_weekly(
            credentials, ad_account_id,
            spend_request=spend_request,
            pace=lambda: _pace(run_state["pacing"]),
        )
    except AppleAdsAuthError:
        raise  # Credential problems are never impression-share-specific.
    except Exception as e:
        # Impression share must never mark the dataset sync as failed.
        logger.warning("Impression-share sync failed: %s", e)
        storage.save_apple_settings(apple_ads={"impression_share": {
            **storage.load_apple_settings()["apple_ads"]["impression_share"],
            "status": "error",
            "error": str(e),
        }})


# ── Backfill (idle-budget background job) ────────────────────────────────

def _run_backfill(credentials, ad_account_id, run_state) -> None:
    """Walk each country's history newest-to-oldest within the budget."""
    with _status_lock:
        _sync_status["phase"] = "backfill"
    target = api.latest_available_week()
    tracked_cutoff = api.weeks_back(target, BACKFILL_WEEKS)
    full_cutoff = api.weeks_back(target, FULL_WEEKS_RETAINED)

    for country in _tracked_countries():
        state = dict(
            storage.load_apple_settings()["apple_ads"]["backfill"].get(country)
            or {}
        )
        if state.get("done"):
            continue
        cursor_iso = state.get("cursor")
        cursor = (
            dt.date.fromisoformat(cursor_iso) if cursor_iso
            else _oldest_ingested_week(country) or target
        )
        week = cursor - timedelta(days=7)
        while week >= tracked_cutoff:
            tracked_only = (
                _tracked_terms(country) if week < full_cutoff else None
            )
            if tracked_only is not None and not tracked_only:
                # Nothing tracked for this country: older weeks would
                # persist zero rows - the backfill is effectively done.
                break
            rows = _fetch_week(
                credentials, ad_account_id, country, week, run_state
            )
            _persist_rows(rows, tracked_only_terms=tracked_only)
            _save_backfill_state(country, cursor=week.isoformat(), done=False)
            week -= timedelta(days=7)
        # Loop completed (or nothing left worth persisting): mark done.
        # A _CeilingReached mid-loop propagates before reaching this line,
        # leaving the saved cursor for the next run to resume from.
        _save_backfill_state(country, cursor=None, done=True)


def _oldest_ingested_week(country):
    from ..models import AppleTopTerm

    return (
        AppleTopTerm.objects.filter(country=country)
        .order_by("week")
        .values_list("week", flat=True)
        .first()
    )


def _save_backfill_state(country, *, cursor, done) -> None:
    backfill = dict(storage.load_apple_settings()["apple_ads"]["backfill"])
    state = {"done": done}
    if cursor:
        state["cursor"] = cursor
    backfill[country] = state
    storage.save_apple_settings(apple_ads={"backfill": backfill})


# ── Derived data maintenance ─────────────────────────────────────────────

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
    active_weeks = storage.load_apple_settings()["apple_ads"]["active_weeks"]
    storage.save_apple_settings(
        apple_ads={
            "coverage": {
                "terms": AppleSearchPopularity.objects.filter(
                    popularity__isnull=False
                ).count(),
                "tracked_matched": matched,
                "tracked_total": len(pairs),
                "week": max(active_weeks.values()) if active_weeks else "",
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
        _sync_status.update(running=False, phase="", country="", started_at=None)
    logger.info("Apple dataset sync finished: %s %s", outcome, error_message)


# ── Entry points ─────────────────────────────────────────────────────────

def _sync_ready() -> bool:
    return storage.apple_source_ready() and storage.has_credentials()


def _work_pending() -> bool:
    block = storage.load_apple_settings()["apple_ads"]
    target = api.latest_available_week().isoformat()
    countries = _tracked_countries() or ["us"]
    if any(block["active_weeks"].get(c, "") < target for c in countries):
        return True
    return any(
        not (block["backfill"].get(c) or {}).get("done")
        for c in countries
    )


def _recently_attempted() -> bool:
    """Avoid hammering Apple when a quarantined/failed week keeps failing:
    the hourly tick retries at most once per ~50 minutes."""
    last = _parse_ts(storage.load_apple_settings()["apple_ads"]["last_sync_at"])
    return bool(last and (timezone.now() - last) < timedelta(minutes=50))


def _start_worker(*, force: bool = False, jitter: float = 0.0) -> bool:
    """Start the sync worker thread unless one is already running."""
    global _worker_running
    with _worker_lock:
        if _worker_running:
            return False
        _worker_running = True

    def work():
        global _worker_running
        try:
            if jitter:
                time.sleep(jitter)
            _run_sync(force=force)
        except Exception as e:  # Sync must never take down the scheduler.
            logger.error("Apple dataset sync crashed: %s", e)
            _finish("error", str(e))
        finally:
            with _worker_lock:
                _worker_running = False

    threading.Thread(target=work, daemon=True, name="apple-dataset-sync").start()
    return True


def maybe_run_sync() -> None:
    """Hourly hook for the scheduler loop - fully automatic, no clicks.

    Picks up each newly published week (Mondays 07:00 UTC), catches up
    missed weeks after the app was closed, and drains the backfill within
    the request budget.
    """
    if not _sync_ready():
        return
    if not _work_pending() or _recently_attempted():
        return
    _start_worker(jitter=random.uniform(0, STARTUP_JITTER_SECONDS))


def run_manual_sync() -> bool:
    """"Sync now" from the settings page. Returns False if already running."""
    if not _sync_ready():
        return False
    return _start_worker(force=True)


def ensure_country_dataset(country: str) -> None:
    """Synchronously download a country's first dataset week (bounded).

    The ONLY synchronous network path in scoring: called via
    aso.popularity.prefetch_apple_values when a country has no local
    dataset at all (typically the first time a user scores keywords in a
    new storefront; the settings wizard pre-warms the first country so
    most users never hit this). Bounded by INLINE_MAX_REQUESTS pages and
    the shared daily budget, backs off after failures, and NEVER raises -
    on any failure scoring falls back to the internal estimate.
    """
    from ..models import AppleTopTerm

    global _inline_backoff_until
    country = (country or "").lower()
    if not country or not _sync_ready():
        return
    credentials = storage.api_credentials()
    block = storage.load_apple_settings()["apple_ads"]
    ad_account_id = block["ad_account_id"]
    if not credentials or not ad_account_id:
        return
    if AppleTopTerm.objects.filter(country=country).exists():
        return
    if time.monotonic() < _inline_backoff_until:
        return

    with _inline_lock:
        if AppleTopTerm.objects.filter(country=country).exists():
            return  # Another thread filled it while we waited.
        week = api.latest_available_week()
        run_state = {"requests": 0, "pacing": BASE_PACING_DELAY}
        try:
            rows = _fetch_week(
                credentials, ad_account_id, country, week, run_state,
                max_pages=INLINE_MAX_REQUESTS,
            )
        except AppleAdsAuthError:
            storage.mark_credentials_rejected()
            return
        except (_CeilingReached, AppleAdsError) as e:
            logger.warning("Inline dataset download failed (%s): %s", country, e)
            _inline_backoff_until = time.monotonic() + INLINE_BACKOFF_SECONDS
            return
        except Exception as e:  # Never let inline plumbing break scoring.
            logger.warning("Inline dataset download crashed (%s): %s", country, e)
            _inline_backoff_until = time.monotonic() + INLINE_BACKOFF_SECONDS
            return
        if _week_sane(country, week, rows):
            _inline_backoff_until = time.monotonic() + INLINE_BACKOFF_SECONDS
            return
        _persist_rows(rows)
        _activate_week(country, week)
        _update_coverage()
