"""Sample-data preview of the Rival Tracker page.

Shown wherever the real page cannot render: the free edition (a Pro
feature), a Pro install without a valid license, and one whose license
expired. It mirrors the live page card for card (Since yesterday, Keywords,
Ratings, complaints) with invented apps and keywords, the app names and
keywords locked, so people see what the tab does before they get it.

None of this touches Apple or the database: the apps, keywords and numbers
are invented and the page labels them as sample data.

Free-tier module: no aso_pro imports, no network, no database.
"""

from . import pro_preview_words

STATE_FREE = "free"
STATE_UNLICENSED = "unlicensed"
STATE_EXPIRED = "expired"
STATES = (STATE_FREE, STATE_UNLICENSED, STATE_EXPIRED)

# (name, is the tracked app); the tracked app comes first, as on the live page.
SAMPLE_APPS = (
    ("Habito", True),
    ("Streakly", False),
    ("Tiny Steps", False),
    ("Routine Lab", False),
    ("Daybloom", False),
)

# (keyword, popularity, rank per app in SAMPLE_APPS order, change per app).
# A rank of None is outside the top 200. A change is places climbed (+),
# places fallen (-), "new" (entered the top 200) or None (no change shown).
SAMPLE_ROWS = (
    ("habit tracker", 62, (14, 3, 8, 21, None), (3, None, -2, 1, None)),
    ("daily routine", 48, (9, 2, 40, 5, 77), (-6, None, 3, None, "new")),
    ("streak app", 41, (4, 1, 12, None, 33), (1, None, None, None, -4)),
    ("morning routine", 39, (22, 6, 15, 3, 18), (2, -1, 5, None, None)),
    ("goal planner", 37, (31, None, 11, 26, 9), (None, None, None, -3, 2)),
    ("habit journal", 29, (6, 17, None, 42, 12), (None, 2, None, None, -1)),
)

# Each sentence in pieces: ("app", name) and ("kw", keyword) are locked,
# plain strings are shown.
SAMPLE_SINCE = (
    ("entered_top10", (("app", "Streakly"), " entered the top 10 for “",
                       ("kw", "morning routine"), "” (#12 → #6).")),
    ("dropped", (("app", "Habito"), " fell 6 places on “", ("kw", "daily routine"),
                 "” (#3 → #9).")),
    ("new_top10_app", ("A new app, ", ("app", "Focus Garden"), ", entered the top 10 for “",
                       ("kw", "habit tracker"), "” at #7.")),
    ("rival_update", (("app", "Tiny Steps"), " shipped version 4.2 and changed its title and screenshots.")),
    ("rating_spike", (("app", "Routine Lab"),
                      " gained 310 ratings in one day in the App Store in the United States, about 6 times its usual pace.")),
)

# (name, ratings, new this week, average)
SAMPLE_RATINGS = (
    ("Habito", 1840, 42, 4.6),
    ("Streakly", 52310, 610, 4.7),
    ("Tiny Steps", 8920, 95, 4.4),
    ("Routine Lab", 12400, 310, 4.5),
    ("Daybloom", 2130, 18, 4.2),
)

# (complaint, what goes wrong, reviews, apps)
SAMPLE_THEMES = (
    ("Streaks reset after updates", "Users lose long streaks when the app updates, and support does not restore them.",
     23, ("Streakly", "Tiny Steps")),
    ("Reminders stop firing", "Daily reminders stop after a few days until the app is opened again.",
     17, ("Routine Lab", "Daybloom")),
    ("Too many upsells", "Core features moved behind the subscription, with prompts on every screen.",
     14, ("Streakly",)),
)
SAMPLE_OPPORTUNITY = (
    "An app that keeps streaks safe across updates and sends reliable reminders "
    "would answer the most common complaints."
)

CHANGE_CSS = {"up": "text-emerald-400", "down": "text-red-400", "new": "text-sky-300"}


def _change(value) -> dict:
    if value is None:
        return {"text": "", "css": ""}
    if value == "new":
        return {"text": "new", "css": CHANGE_CSS["new"]}
    if value > 0:
        return {"text": f"▲{value}", "css": CHANGE_CSS["up"]}
    return {"text": f"▼{-value}", "css": CHANGE_CSS["down"]}


def sample_columns() -> list[dict]:
    """The keyword table's columns in the live page's shape."""
    return [{"short_name": name, "icon_url": "", "is_tracked_app": tracked}
            for name, tracked in SAMPLE_APPS]


def sample_rows() -> list[dict]:
    """The keyword table's rows in the live page's shape."""
    rows = []
    for keyword, popularity, ranks, changes in SAMPLE_ROWS:
        cells = [
            {"rank": rank, "change": _change(change), "is_tracked_app": tracked}
            for rank, change, (_name, tracked) in zip(ranks, changes, SAMPLE_APPS)
        ]
        own = ranks[0]
        ahead = any(r is not None and (own is None or r < own) for r in ranks[1:])
        rows.append({"keyword": keyword, "popularity": popularity, "cells": cells, "rivals_ahead": ahead})
    return rows


def sample_since() -> list[dict]:
    return [
        {"kind": kind, "pieces": [
            {"locked": isinstance(p, tuple), "text": p[1] if isinstance(p, tuple) else p} for p in pieces
        ]}
        for kind, pieces in SAMPLE_SINCE
    ]


def sample_ratings() -> list[dict]:
    return [
        {"name": name, "is_tracked_app": index == 0, "ratings_text": f"{ratings:,}",
         "new_text": f"+{new:,}", "average_text": f"{average:.1f}"}
        for index, (name, ratings, new, average) in enumerate(SAMPLE_RATINGS)
    ]


def sample_themes() -> list[dict]:
    return [{"theme": theme, "detail": detail, "review_count": count, "apps": list(apps)}
            for theme, detail, count, apps in SAMPLE_THEMES]


def sample_names() -> set[str]:
    """Every invented app name and keyword the preview renders (guard tests
    use this to prove none of them can reach a live page)."""
    names = {name for name, _t in SAMPLE_APPS} | {row[0] for row in SAMPLE_ROWS}
    for _kind, pieces in SAMPLE_SINCE:
        names |= {p[1] for p in pieces if isinstance(p, tuple)}
    return names


def _cta(state: str, license_url: str | None) -> dict:
    """The state's call to action, in the words every Pro preview uses
    (aso.pro_preview_words)."""
    return pro_preview_words.cta("rival_tracker", state, license_url)


def preview_context(state: str, *, license_url: str | None = None) -> dict:
    """Template context for aso/rival_tracker_preview.html.

    ``license_url`` is the Pro build's Settings, License page, passed in by
    the Pro view so this free-tier module never names an aso_pro route.
    """
    if state not in STATES:
        raise ValueError(f"unknown preview state: {state!r}")
    return {
        "preview": {
            "state": state,
            "columns": sample_columns(),
            "rows": sample_rows(),
            "since": sample_since(),
            "ratings": sample_ratings(),
            "themes": sample_themes(),
            "opportunity": SAMPLE_OPPORTUNITY,
            "cta": _cta(state, license_url),
        },
    }
