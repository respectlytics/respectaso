"""The keyword cleanup suggestion on the dashboard.

Every tracked keyword and country pair is re-checked once a day while the
app is open, at roughly five seconds per pair. Once that takes an hour or
more, the dashboard says so and points at the pairs least worth keeping -
by default the ones classified Low Volume or Avoid in their latest result -
with a one-click filter to review and delete them. The classes are a
default suggestion (``CLEANUP_CLASSIFICATIONS``); the user keeps full control
through the History filters and can snooze the banner for 30 days.
"""

from __future__ import annotations

from urllib.parse import urlencode

from django.urls import reverse

from . import ui_state

REFRESH_SECONDS_PER_PAIR = 5          # 3 s pacing plus a typical 2 s call
CLEANUP_BANNER_MIN_SECONDS = 3600     # show once the daily refresh reaches an hour
CLEANUP_MIN_CANDIDATES = 10           # and at least this many pairs are worth reviewing
CLEANUP_CLASSIFICATIONS = ("Low Volume", "Avoid")
CLEANUP_SNOOZE_DAYS = 30


def duration_text(seconds: float) -> str:
    """'about 3 h 30 min', 'about 48 min', 'about 1 min', 'less than a minute'."""
    minutes = int(round(seconds / 60))
    if minutes < 1:
        return "less than a minute"
    if minutes < 60:
        return f"about {minutes} min"
    hours, minutes = divmod(minutes, 60)
    if minutes:
        return f"about {hours} h {minutes} min"
    return f"about {hours} h"


def cleanup_suggestion(latest_results_qs, app_id=None) -> dict | None:
    """The banner's numbers, or None when it should not show.

    ``latest_results_qs`` is the unfiltered latest-per-pair queryset the
    dashboard already builds (before the insight filter), scoped to the
    current app filter; ``app_id`` keeps that filter in the "Show them" link.
    """
    pairs = latest_results_qs.count()
    refresh_seconds = pairs * REFRESH_SECONDS_PER_PAIR
    if refresh_seconds < CLEANUP_BANNER_MIN_SECONDS:
        return None
    candidates = latest_results_qs.filter(classification__in=CLEANUP_CLASSIFICATIONS).count()
    if candidates < CLEANUP_MIN_CANDIDATES:
        return None
    if ui_state.is_dismissed(ui_state.KEYWORD_CLEANUP_BANNER):
        return None
    params = [("insight", label) for label in CLEANUP_CLASSIFICATIONS]
    if app_id:
        params.append(("app", app_id))
    return {
        "pairs": pairs,
        "pairs_text": f"{pairs:,}",
        "refresh_seconds": refresh_seconds,
        "refresh_text": duration_text(refresh_seconds),
        "candidates": candidates,
        "candidates_text": f"{candidates:,}",
        "filter_url": reverse("aso:dashboard") + "?" + urlencode(params) + "#history-section",
    }
