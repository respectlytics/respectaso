"""One rule for "how did the rank move" on every screen, and an app's
tracked keywords in brief (docs/development/DAILY_USE_PLAN.md).

A move compares a rank with the newest earlier read that is at most
MAX_GAP_DAYS days older, in the reader's own days. With nothing that
recent there is no move to show.

Free-tier module: no aso_pro imports.
"""

from __future__ import annotations

import threading
from datetime import timedelta

from django.db.models import Max
from django.utils import timezone

MAX_GAP_DAYS = 3
RANK_GAP = timedelta(days=MAX_GAP_DAYS)

_lock = threading.Lock()
_briefs: dict = {}     # app id -> (history revision, brief)


def within_gap(now_at, before_at) -> bool:
    """Whether a read at ``before_at`` is recent enough to compare with one at ``now_at``."""
    from .local_day import local_date

    return local_date(now_at) - local_date(before_at) <= RANK_GAP


def brief(app) -> dict:
    """keywords: how many keywords the app tracks; top10: how many of them
    rank in the top 10 in at least one country; up, down: how many keyword
    and country pairs moved since the read before; ranking: how many pairs
    rank at all; countries: how many countries. Worked out again only when
    the tracked data changed."""
    from . import history_revision
    from .models import SearchResult

    revision = history_revision.current()
    with _lock:
        kept = _briefs.get(app.pk)
        if kept and kept[0] == revision:
            return kept[1]
    newest_ids = list(SearchResult.objects.filter(keyword__app=app)
                      .values("keyword_id", "country").annotate(newest=Max("id"))
                      .values_list("newest", flat=True))
    newest = {(row["keyword_id"], row["country"]): row for row in SearchResult.objects
              .filter(id__in=newest_ids).values("id", "keyword_id", "country", "app_rank", "searched_at")}
    up = down = 0
    if newest:
        since = timezone.now() - timedelta(days=MAX_GAP_DAYS * 2 + 2)
        seen = set()
        earlier = (SearchResult.objects.filter(keyword__app=app, searched_at__gte=since)
                   .exclude(id__in=newest_ids).order_by("-searched_at", "-id")
                   .values("keyword_id", "country", "app_rank", "searched_at"))
        for row in earlier:
            pair = (row["keyword_id"], row["country"])
            if pair in seen or pair not in newest:
                continue
            seen.add(pair)
            now = newest[pair]
            if now["app_rank"] is None or row["app_rank"] is None or not within_gap(now["searched_at"], row["searched_at"]):
                continue
            if now["app_rank"] < row["app_rank"]:
                up += 1
            elif now["app_rank"] > row["app_rank"]:
                down += 1
    keywords = {pair[0] for pair in newest}
    result = {
        "keywords": len(keywords),
        "top10": len({pair[0] for pair, row in newest.items() if row["app_rank"] is not None and row["app_rank"] <= 10}),
        "ranking": sum(1 for row in newest.values() if row["app_rank"] is not None),
        "pairs": len(newest),
        "countries": len({pair[1] for pair in newest}),
        "up": up, "down": down,
    }
    with _lock:
        _briefs[app.pk] = (revision, result)
    return result


def reset() -> None:
    """For tests: forget every brief (a rolled back test can leave the same
    revision and app id behind with other rows)."""
    with _lock:
        _briefs.clear()
