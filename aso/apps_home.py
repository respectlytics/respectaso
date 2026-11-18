"""What the Apps page shows for each app (docs/development/DAILY_USE_PLAN.md).

Without a Pro license a row is today's: the app and its keyword count (D12).
With one it has its ranks in brief (aso/rank_change.py), its To do lines and
what App Store Connect reports through ``number_sources``
(aso_pro.store.home), and what opening the page should start through
``open_hooks``.

Free-tier module: no aso_pro imports.
"""

from __future__ import annotations

import logging

from . import todo
from .rank_change import brief

logger = logging.getLogger(__name__)

PERIODS = (7, 30)
DEFAULT_PERIOD = 7

# Callables (apps, days, request) -> {"apps": {app_id: {...}}, "totals": {...} | None, "currency": str}
number_sources: list = []
# Callables () run when a person opens the page (not on its own refresh).
open_hooks: list = []


def rows(days: int, request=None) -> dict:
    """Each app's row, the totals and the currency. ``request`` is None in
    the MCP server's process: a source then uses the currency chosen last."""
    from .models import App

    apps = list(App.objects.all())
    numbers: dict = {}
    totals = None
    currency = ""
    for source in number_sources:
        try:
            found = source(apps, days, request)
        except Exception:  # the home page must open even when one source fails
            logger.exception("The Apps page could not read an app's numbers")
            continue
        numbers.update(found.get("apps") or {})
        totals = found.get("totals") or totals
        currency = found.get("currency") or currency
    from .pro_access import has_pro_license

    licensed = has_pro_license()      # a row's numbers and lines are part of Pro (D12)
    lines = todo.by_app(days)         # empty without a license; the lines speak of these days
    built = []
    for app in apps:
        open_lines = lines.get(app.pk, [])
        shape = brief(app) if licensed else None
        built.append({
            "app": app,
            "brief": shape,           # None: the row is today's (the keyword count)
            "keywords": (shape["keywords"] if shape else 0) or app.keywords.count(),
            "store": numbers.get(app.pk) if licensed else None,   # None without App Store Connect numbers
            "lines": open_lines[:todo.MAX_SHOWN],
            "more": open_lines[todo.MAX_SHOWN:],
        })
    built.sort(key=_order)
    return {"rows": built, "totals": totals if licensed else None, "currency": currency, "days": days}


def page(request, days: int) -> dict:
    """Everything the Apps template draws."""
    built = rows(days, request)
    return {**built,
            "periods": [{"days": d, "label": f"{d} days", "active": d == days} for d in PERIODS],
            "show_period": any(row["store"] and row["store"].get("ready") for row in built["rows"]),
            "stamp": todo.state(days)["stamp"]}


def opened() -> None:
    """A person opened the page: let the Pro build check Apple for anything due."""
    for hook in open_hooks:
        try:
            hook()
        except Exception:  # a hook must never stop the home page
            logger.exception("An Apps page open hook failed")


def _order(row):
    """The app that needs the most comes first: by its worst line that asks
    for something, then by the downloads it lost, then by name."""
    asking = [line for line in row["lines"] + row["more"] if line.counts]
    tone = todo.TONES.index(asking[0].tone) if asking else len(todo.TONES)
    change = (row["store"] or {}).get("downloads_change")
    return (tone, change if change is not None else 0, row["app"].name.lower())
