"""One Apple read per keyword, per storefront, per day, used everywhere.

Every code path that needs Apple's search results for a keyword asks this
module, never the iTunes service: the daily refresh, a Dashboard search and
Refresh, keyword search jobs, country scans, the three AI tabs, the metadata
coverage step, the MCP tools and, in Pro, the Rival Tracker.

One read is one search of up to 200 apps (ITunesSearchService.search_ranked).
Its first 25 apps feed every score exactly as before; the order of all of
them gives every app's rank, the tracked app, its twins and rivals alike, so
a row's rank and its own competitor list can never disagree.

A read already stored for today (the reader's day, aso/local_day.py) is
reused, so a keyword costs Apple one search per storefront per day wherever
it is scored. A user's Refresh passes ``force=True``: Apple is asked again
and the day's read is replaced.

    normalize_term(term)        the one keyword form (aso.popularity)
    get_or_fetch(term, country, *, itunes_service, force=False)
                                today's read, from the table or from Apple
    stored_today(term, country) today's read, or None; never asks Apple
    store(term, country, ranked) keeps a search as today's read
    rank_of(read, track_id)     1-based rank in the read, or None
    Pacer(wait)                 waits before each of a run's real Apple
                                reads at the shared gate (aso/throttle.py),
                                which get_or_fetch tells when each ends,
                                never before a read already stored today
    ReadAhead(country, ...)     reads keywords a run is sure to score on a
                                thread while the run waits for its AI
    tidy(retention_days)        empties finished days' competitors, deletes
                                reads older than the retention
    forget_untracked()          deletes the finished days of keywords no
                                longer tracked (also right after a Keyword
                                is deleted: aso/disk_space.py)

Ships in the free-tier `aso` app, so it must not import from aso_pro or
licensing.
"""

from __future__ import annotations

import logging
import threading
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .db_writes import delete_in_batches, update_in_batches
from .local_day import local_date
from .models import Keyword, KeywordDayRead
from .popularity import normalize_term
from .throttle import apple_gate

logger = logging.getLogger(__name__)

__all__ = ["Pacer", "ReadAhead", "forget_terms_not_tracked", "forget_untracked", "get_or_fetch", "normalize_term",
           "rank_of", "store", "stored_today", "tidy"]


def _today():
    return local_date(timezone.now())


def _key(term, country):
    return normalize_term(term), (country or "").strip().lower()


def stored_today(term, country):
    """Today's read of a keyword in a storefront, or None. Never asks Apple."""
    key_term, key_country = _key(term, country)
    return KeywordDayRead.objects.filter(term=key_term, country=key_country, day=_today()).first()


def get_or_fetch(term, country, *, itunes_service, force=False):
    """Today's read of a keyword in a storefront.

    From the table when the keyword was read today anywhere in the app (in
    this process or another: the MCP server shares the database), from Apple
    otherwise. ``force=True`` asks Apple in any case and replaces the day's
    read: only a user's Refresh passes it.

    Raises ITunesRateLimited and SearchAPIUnavailableError from the search.
    """
    key_term, key_country = _key(term, country)
    day = _today()
    if not force:
        stored = KeywordDayRead.objects.filter(term=key_term, country=key_country, day=day).first()
        if stored is not None:
            return stored

    try:
        ranked = itunes_service.search_ranked(key_term, country=key_country)
    finally:
        apple_gate.ended()      # the next paced request counts its pause from now
    return store(key_term, key_country, ranked)


def store(term, country, ranked):
    """Keep ``ranked`` (a services.RankedSearch) as today's read of a keyword
    in a storefront, replacing an earlier read of the day, and return it."""
    key_term, key_country = _key(term, country)
    day = _today()
    values = {
        "ranked_ids": ranked.ranked_ids,
        "result_count": ranked.result_count,
        "competitors": ranked.apps,
        "fetched_at": timezone.now(),
    }
    for attempt in range(2):
        try:
            with transaction.atomic():
                read, _ = KeywordDayRead.objects.update_or_create(
                    term=key_term, country=key_country, day=day, defaults=values,
                )
            break
        except IntegrityError:
            # Another writer (the MCP server, a second browser window) stored
            # the same day between our check and our write: the second pass
            # updates that row with this answer.
            if attempt:
                raise
    logger.info("Apple read %r (%s): %d apps", key_term, key_country, ranked.result_count)
    return read


def rank_of(read, track_id):
    """The 1-based rank of an app in a read, or None when it is not among
    the apps the search returned (or there is no read, or no app id)."""
    if read is None or not track_id:
        return None
    try:
        return read.ranked_ids.index(int(track_id)) + 1
    except (TypeError, ValueError):
        return None


class Pacer:
    """Paces one run's Apple reads.

    Waits (the run's own wait: a rate limiter's, which queues at the app's
    one shared gate, aso/throttle.py AppleGate) before every real Apple
    read, the run's first included, since another run may have just asked
    Apple; the gate lets it through at once when nobody did. Never waits
    before a read already stored today, which costs Apple nothing.
    ``before`` answers whether the coming read goes to Apple, so the run's
    rate limiter learns only from real requests.
    """

    def __init__(self, wait):
        self._wait = wait

    def before(self, term, country, *, force=False) -> bool:
        if not force and stored_today(term, country) is not None:
            return False
        self._wait()
        return True


class ReadAhead:
    """Reads keywords a run is sure to score while the run waits for its AI.

    A run knows some of its keywords (the Simulator its metadata's words)
    minutes before its AI steps finish; this reads them meanwhile, on one
    thread, through the same search every score reads (``search_ranked``)
    and at the run's own pace (its ``wait``, the shared gate): one request
    at a time, so Apple sees the run's usual pace and no score changes, and
    the run finds those keywords stored when it scores them.

    ``add(terms)`` queues keywords (each once) and starts the thread when
    none runs; the thread ends when the queue is empty, so nothing is left
    running when a run fails before scoring. ``finish()`` waits for the
    thread, stores every read as today's read on the caller's thread (the
    thread itself only asks Apple, never the database) and returns one
    ``(term, success, retry_after)`` per real request for the run's rate
    limiter; keywords not read by then are the run's to read. The first
    failure stops the reading: that keyword and the rest are left to the
    run, which handles a failure as it always did. ``stop()`` ends it after
    the read in progress (a run that failed), keeping what was read.
    """

    def __init__(self, country, *, itunes_service, wait):
        self._country = country
        self._itunes = itunes_service
        self._wait = wait
        # Re-entrant: a test's synchronous stand-in for threading.Thread runs
        # the reads inside add().
        self._lock = threading.RLock()
        self._queue = []
        self._queued = set()
        self._read = []                 # (term, RankedSearch), in order
        self._failure = None            # (term, retry_after)
        self._stopped = False
        self._ended = False
        self._thread = None

    def add(self, terms) -> None:
        with self._lock:
            if self._ended or self._stopped or self._failure is not None:
                return
            for term in terms:
                term = normalize_term(term)
                if term and term not in self._queued:
                    self._queued.add(term)
                    self._queue.append(term)
            if self._queue and self._thread is None:
                self._thread = threading.Thread(target=self._run, name="read-ahead", daemon=True)
                self._thread.start()

    def _next(self):
        with self._lock:
            if not self._queue or self._stopped or self._failure is not None:
                self._thread = None
                return None
            return self._queue.pop(0)

    def _run(self):
        from .services import ITunesAPIError

        while (term := self._next()) is not None:
            self._wait()
            try:
                ranked = self._itunes.search_ranked(term, country=self._country)
            except ITunesAPIError as exc:
                self._failure = (term, getattr(exc, "retry_after", None))
                continue
            except Exception as exc:  # noqa: BLE001 (the run reads this keyword itself and meets the error there)
                logger.info("Read ahead of %r (%s) stopped: %s", term, self._country, exc)
                self._stopped = True
                continue
            finally:
                apple_gate.ended()
            self._read.append((term, ranked))

    def _end(self) -> list:
        with self._lock:
            self._ended = True
            thread = self._thread
        if thread is not None:
            thread.join()
        for term, ranked in self._read:
            if stored_today(term, self._country) is None:
                store(term, self._country, ranked)
        outcomes = [(term, True, None) for term, _ in self._read]
        if self._failure is not None:
            outcomes.append((self._failure[0], False, self._failure[1]))
        self._read = []
        return outcomes

    def finish(self) -> list:
        """Wait for the reads queued, store them, and return the requests'
        outcomes (once; later calls return nothing)."""
        if self._ended:
            return []
        return self._end()

    def stop(self) -> None:
        """Stop after the read in progress and keep what was read."""
        if not self._ended:
            self._stopped = True
            self._end()


def tidy(retention_days):
    """Empty the competitor lists of finished days, delete reads older than
    ``retention_days`` and the finished days of keywords no longer tracked
    (forget_untracked). Returns (emptied, deleted). Changes nothing when
    there is nothing to do, so it may run every hour."""
    today = _today()
    emptied = update_in_batches(KeywordDayRead.objects.filter(day__lt=today).exclude(competitors=[]),
                                competitors=[])
    cutoff = today - timedelta(days=retention_days)
    deleted = delete_in_batches(KeywordDayRead.objects.filter(day__lt=cutoff))
    return emptied, deleted + forget_untracked()


def forget_untracked() -> int:
    """Delete the finished days' reads of every keyword that no tracked
    keyword (any app) has any more. Returns how many reads went.

    Only today's read is ever reused; a finished day's read is kept for the
    Rival Tracker, which copies a keyword's earlier days when it starts
    following it, and it can follow only a tracked keyword. So once the user
    deletes a keyword, its finished days are of no use and give their disk
    space back (aso/disk_space.py). Today's read stays: anything else that
    reads the keyword today reuses it."""
    deleted = forget_terms_not_tracked(KeywordDayRead.objects.filter(day__lt=_today()))
    if deleted:
        logger.info("Deleted %d earlier reads of keywords no longer tracked.", deleted)
    return deleted


def forget_terms_not_tracked(rows) -> int:
    """Delete the rows of ``rows`` (a queryset of a model with a ``term`` in
    normalize_term form) whose keyword no tracked keyword of any app has.
    The one rule for every table keyed by a keyword's text. Returns how
    many rows went."""
    tracked = {normalize_term(text) for text in Keyword.objects.values_list("keyword", flat=True)}
    gone = sorted(set(rows.values_list("term", flat=True).distinct()) - tracked)
    deleted = 0
    for start in range(0, len(gone), 500):
        deleted += delete_in_batches(rows.filter(term__in=gone[start:start + 500]))
    return deleted
