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
    rank_of(read, track_id)     1-based rank in the read, or None
    Pacer(wait)                 waits between one run's real Apple reads,
                                never before a read already stored today
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
from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from .db_writes import delete_in_batches, update_in_batches
from .local_day import local_date
from .models import Keyword, KeywordDayRead
from .popularity import normalize_term

logger = logging.getLogger(__name__)

__all__ = ["Pacer", "forget_terms_not_tracked", "forget_untracked", "get_or_fetch", "normalize_term", "rank_of", "stored_today", "tidy"]


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

    ranked = itunes_service.search_ranked(key_term, country=key_country)
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

    Waits (the run's own wait: a rate limiter's, or a fixed sleep) before
    every real Apple read except the run's first, and never before a read
    already stored today, which costs Apple nothing. ``before`` answers
    whether the coming read goes to Apple, so the run's rate limiter learns
    only from real requests.
    """

    def __init__(self, wait):
        self._wait = wait
        self._fetched = False

    def before(self, term, country, *, force=False) -> bool:
        if not force and stored_today(term, country) is not None:
            return False
        if self._fetched:
            self._wait()
        self._fetched = True
        return True


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
