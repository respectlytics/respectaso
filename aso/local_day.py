"""The reader's day: when today starts, and which day a moment belongs to.

Search History keeps one read per keyword and storefront per day, the daily
refresh runs once a day, and trend charts show one value per day. The day
used to end at midnight UTC: a click at 01:30 in Sweden counted for the day
before, while the Date column already showed the new date (2026-09-30), and
a user in California had their "day" roll over at 17:00.

The day is now the reader's own, anywhere in the world: the time zone their
browser reports (base.html sends it when it differs from the one in use), or,
until a browser has reported one, the machine's own zone. Every request
renders dates in it (``UserTimeZoneMiddleware``), and every "today" in the
code comes from here.

    user_timezone()        the zone days are counted in
    zone_name()            its name, for the page to compare with the browser's
    today_bounds()         (start, end) of today there, as aware datetimes
    today_start()          the start alone
    local_date(moment)     the day a moment belongs to there
    remember(name)         store a zone a browser reported; True if it changed

Ships in the free-tier `aso` app, so it must not import from aso_pro or
licensing.
"""

from __future__ import annotations

import os
import threading
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings
from django.utils import timezone

from . import ui_state

_lock = threading.Lock()
_cache = {"stamp": object(), "zone": None}


def _zone(name):
    """A ZoneInfo for an IANA name, or None when the name is not one."""
    if not isinstance(name, str) or not name or len(name) > 64:
        return None
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 (an unknown name, bad characters or no time zone database)
        return None


def _machine_zone():
    """The zone this machine runs in, by name where it can be read."""
    fallback = getattr(settings, "LOCAL_DAY_FALLBACK_ZONE", None)
    if fallback:
        return _zone(fallback) or UTC
    # Not the TZ variable: Django sets it to TIME_ZONE ("UTC") when it
    # starts, so inside the app it never names the machine's zone. The
    # system's /etc/localtime link does (macOS and Linux).
    try:
        real = os.path.realpath("/etc/localtime")
    except OSError:
        real = ""
    if "zoneinfo/" in real:
        zone = _zone(real.split("zoneinfo/", 1)[1])
        if zone is not None:
            return zone
    return UTC


def user_timezone():
    """The zone days are counted in: the reported one, else the machine's."""
    stamp = ui_state.stamp()
    with _lock:
        if _cache["stamp"] != stamp or _cache["zone"] is None:
            _cache["zone"] = _zone(ui_state.time_zone()) or _machine_zone()
            _cache["stamp"] = stamp
        return _cache["zone"]


def zone_name() -> str:
    zone = user_timezone()
    return getattr(zone, "key", None) or str(zone)


def today_bounds(now=None):
    """(start, end) of today in the reader's zone, as aware datetimes."""
    zone = user_timezone()
    day = timezone.localtime(now or timezone.now(), zone).date()
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return start, end


def today_start(now=None):
    return today_bounds(now)[0]


def local_date(moment):
    """The day an aware datetime belongs to in the reader's zone."""
    return timezone.localtime(moment, user_timezone()).date()


def remember(name) -> bool:
    """Store the zone a browser reported. True when it changed what days
    are counted in; ValueError when it is not a time zone name."""
    zone = _zone(name)
    if zone is None:
        raise ValueError(f"Not a time zone: {name!r}")
    before = zone_name()
    ui_state.set_time_zone(zone.key)
    return zone_name() != before


class UserTimeZoneMiddleware:
    """Render every request's dates in the reader's zone, so a date the
    server writes and one the page's script writes always agree."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        timezone.activate(user_timezone())
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()
