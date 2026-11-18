"""The To do lines of the Apps page (docs/development/DAILY_USE_PLAN.md).

A line says one thing an app needs, with one button that opens the place
that fixes it. Sources add lines; each is an object with ``stamp()`` (a
cheap value that changes when its lines could have changed) and
``lines(apps, days)``. Every source is the Pro build's (aso_pro.apps
registers them), and without a Pro license no line is shown (D12).

``days`` is the period the Apps page shows (aso.apps_home.PERIODS): a line
that names a period (downloads fell, a step from seen to paid fell) speaks
of those days against the days before, so it always agrees with the row's
numbers. Where no page is drawn (the daily notification, an AI assistant
that names no period) the period is the page's default, DEFAULT_PERIOD.

A line's ``key`` is the name of its X in aso.ui_state: it holds the state
the line reports, so a closed line stays closed until that state changes.
A line that only brings news (``counts`` False) is shown but is not counted
beside Apps and is never the reason for a notification.

Free-tier module: no aso_pro imports.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)

TONES = ("error", "warning", "info", "success")   # the order lines are shown in
MAX_SHOWN = 3                                      # per app; the rest folds behind "N more"
MIN_SECONDS = 30                                   # lines are worked out again at most this often


@dataclass(frozen=True)
class Line:
    app_id: int
    key: str
    tone: str
    text: str
    label: str
    url: str
    external: bool = False   # opens outside the app (App Store Connect)
    counts: bool = True      # False: news that asks for nothing

    @property
    def topic(self) -> str:
        """The key without its last part (the state or the day): what the line is about."""
        return self.key.rsplit(":", 1)[0]


sources: list = []
# Callables (lines) -> lines, run after the sources: the Pro build points a keyword line at Get found.
refiners: list = []
_lock = threading.Lock()
_cache: dict = {}    # days -> {"stamp", "lines", "at", "apps"}: each period is worked out on its own


def register(source) -> None:
    if source not in sources:
        sources.append(source)


def _source_stamp() -> str:
    from django.utils import timezone

    from . import history_revision
    from .local_day import local_date

    # The history revision counts every change to the tracked keywords, the
    # apps and the Pro build's store and rival tables.
    parts = [str(local_date(timezone.now())), history_revision.current()]
    for source in sources:
        try:
            parts.append(repr(source.stamp()))
        except Exception:  # one source must never hide the others
            logger.exception("A To do source could not say whether it changed")
            parts.append("?")
    return "|".join(parts)


def period(days: int | None = None) -> int:
    """``days`` when it is one of the Apps page's periods, else the page's default."""
    from .apps_home import DEFAULT_PERIOD, PERIODS

    return days if days in PERIODS else DEFAULT_PERIOD


def all_lines(days: int | None = None) -> list[Line]:
    """Every line for the period ``days``, closed ones included. Worked out
    again when a stamp changed or the day turned, and at most every
    MIN_SECONDS, so a refresh that changes a row a second does not rebuild
    them a second."""
    from .models import App
    from .pro_access import has_pro_license

    if not has_pro_license():
        return []     # every line is part of Pro (D12): the one gate for the page, the count and the tools
    days = period(days)
    # An app added or deleted is worked out at once, wait or not: a new app said "Nothing to do."
    # until the wait was over, where it has no keywords yet (2026-11-18).
    app_ids = tuple(App.objects.order_by("pk").values_list("pk", flat=True))
    with _lock:
        kept = _cache.get(days)
        if kept and time.monotonic() - kept["at"] < MIN_SECONDS and kept["apps"] == app_ids:
            return list(kept["lines"])
    stamp = _source_stamp()
    with _lock:
        kept = _cache.get(days)
        if kept and kept["stamp"] == stamp and kept["apps"] == app_ids:
            kept["at"] = time.monotonic()
            return list(kept["lines"])
    apps = list(App.objects.all())
    lines: list[Line] = []
    for source in sources:
        try:
            lines.extend(source.lines(apps, days))
        except Exception:  # one source must never hide the others
            logger.exception("A To do source failed")
    for refine in refiners:
        try:
            lines = list(refine(lines))
        except Exception:  # a refiner must never hide the lines
            logger.exception("A To do refiner failed")
    # By tone, and within a tone the lines that ask for something before news,
    # so news never folds a line that asks behind "N more".
    lines.sort(key=lambda line: (TONES.index(line.tone) if line.tone in TONES else len(TONES), not line.counts))
    with _lock:
        _cache[days] = {"stamp": stamp, "lines": lines, "at": time.monotonic(), "apps": app_ids}
    return list(lines)


def open_lines(days: int | None = None) -> list[Line]:
    from . import ui_state

    closed = ui_state.dismissed_notices()      # one read of the file for every line
    return [line for line in all_lines(days) if line.key not in closed]


def by_app(days: int | None = None) -> dict[int, list[Line]]:
    grouped: dict[int, list[Line]] = {}
    for line in open_lines(days):
        grouped.setdefault(line.app_id, []).append(line)
    return grouped


def state(days: int | None = None) -> dict:
    """{"stamp", "count"} for the period ``days`` from one pass: the stamp
    changes whenever the Apps page would draw something else; the count is
    the lines that ask for something."""
    from . import history_revision

    lines = open_lines(days)
    # What a line says, not only its key: "4 reviews wait" becoming "3 reviews wait" keeps its key, and a page
    # drawn while the lines were still the old ones (within MIN_SECONDS) must be drawn again once they are not.
    text = history_revision.current() + "|" + "\n".join(
        f"{line.key}|{line.tone}|{line.text}|{line.label}|{line.url}" for line in lines)
    return {"stamp": hashlib.sha256(text.encode()).hexdigest()[:16],
            "count": sum(1 for line in lines if line.counts)}


def count(days: int | None = None) -> int:
    return state(days)["count"]


def reset() -> None:
    """For tests: forget what was worked out."""
    with _lock:
        _cache.clear()
