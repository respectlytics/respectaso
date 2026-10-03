"""Sample-data previews of the three AI tools (docs/development/PRO_PREVIEWS_PLAN.md).

AI Researcher, AI Competitor and the ASO Simulator, opened without Pro, show
the real result page filled with invented sample results, the keywords
locked, the promise of the feature and two clear buttons (the words live in
aso.pro_preview_words). Built like aso/top_terms_preview.py: one sample data
module, one template (aso/ai_tool_preview.html), no live controls.

The sample rows are invented but consistent with the app's own rules: every
tag is the range its Opportunity falls in (aso.scoring), and the downloads at
#1 are plausible for the popularity beside them.

Free-tier module: no aso_pro imports, no network, no database.
"""

from . import pro_preview_words
from .pro_preview_words import STATE_EXPIRED, STATE_FREE, STATE_UNLICENSED

STATES = (STATE_FREE, STATE_UNLICENSED, STATE_EXPIRED)
FEATURES = ("researcher", "competitor", "simulator")

TITLES = {
    "researcher": ("AI Niche Researcher", "Type a keyword. Get the best keywords in its niche and a title that targets them."),
    "competitor": ("AI Competitor Analyzer", "Pick any app. See which of its keywords your app can win."),
    "simulator": ("ASO Score Simulator", "Score your title, subtitle and keywords before you ship them."),
}

# (keyword, popularity, difficulty, opportunity, downloads at #1 as the table
#  prints it, the same in words, tag, the competitor's rank or None)
SAMPLE_ROWS = (
    ("daily habit tracker", 52, 28, 71, "4.1–41/day", "4.1 to 41 downloads a day", "Sweet Spot", 7),
    ("habit streaks", 47, 24, 63, "2.2–22/day", "2.2 to 22 downloads a day", "Good Target", 3),
    ("routine planner", 45, 33, 55, "1.7–17/day", "1.7 to 17 downloads a day", "Good Target", 12),
    ("habit journal", 39, 22, 48, "0.8–8.1/day", "0.8 to 8.1 downloads a day", "Supporting", 9),
    ("self care checklist", 31, 18, 41, "0.3–3.4/day", "0.3 to 3.4 downloads a day", "Supporting", None),
    ("goal tracker", 55, 61, 27, "3.2–32/day", "3.2 to 32 downloads a day", "Worth Climbing", 15),
    ("morning routine", 49, 57, 24, "2.6–26/day", "2.6 to 26 downloads a day", "Worth Climbing", None),
    ("streak counter", 22, 15, 19, "0.1–0.9/day", "at most 0.9 downloads a day", "Low Volume", None),
)

SAMPLE_COUNTS = (("Target now", 14), ("Target and climb", 9), ("Skip", 6))
SAMPLE_TOTAL = 29

COMPETITOR_NAME = "Tallyo"
APP_NAME = "Habitly"


def sample_names() -> set[str]:
    """Every invented keyword and app name the previews render; guard tests
    prove none of them reaches a live page."""
    return {row[0] for row in SAMPLE_ROWS} | {APP_NAME, COMPETITOR_NAME, "Tallyo: Habit Streaks"}


def _verdict(competitor: bool) -> dict:
    rows = list(SAMPLE_ROWS[:5])
    if competitor:
        covered = [r for r in SAMPLE_ROWS if r[7] is not None]
        rows = sorted(covered, key=lambda r: -r[3])[:5]   # as run_verdict orders them
    return {
        "counts": [{"decision": d, "count": n} for d, n in SAMPLE_COUNTS],
        "best": [
            {"keyword": r[0], "classification": r[6], "opportunity": r[3], "at_top": r[5],
             "competitor_rank": r[7] if competitor else None}
            for r in rows
        ],
        "total": SAMPLE_TOTAL,
    }


def _table_rows() -> list[dict]:
    return [
        {"keyword": r[0], "popularity": r[1], "difficulty": r[2], "opportunity": r[3],
         "downloads": r[4], "classification": r[6]}
        for r in SAMPLE_ROWS
    ]


def state_of(request=None) -> str:
    """free in the free edition; expired or unlicensed in the Mac app."""
    from django.apps import apps as django_apps

    from .pro_access import license_expired

    if not django_apps.is_installed("aso_pro"):
        return STATE_FREE
    return STATE_EXPIRED if license_expired() else STATE_UNLICENSED


def context(feature: str, state: str, *, license_url: str | None = None) -> dict:
    """Template context for aso/ai_tool_preview.html."""
    from .scoring import classification_legend

    if feature not in FEATURES:
        raise ValueError(f"unknown feature: {feature!r}")
    if state not in STATES:
        raise ValueError(f"unknown preview state: {state!r}")
    title, lead = TITLES[feature]
    competitor = feature == "competitor"
    sample = {
        "verdict": _verdict(competitor),
        "listing": ({"title": "Habitly: Streaks and Routines", "subtitle": "A calmer daily habit tracker"}
                    if competitor else
                    {"title": "Habitly: Daily Habit Tracker", "subtitle": "Build streaks, plan routines"}),
        "competitor_name": COMPETITOR_NAME if competitor else "",
        "rows": _table_rows(),
        "simulator": {
            "title": "Habitly: Daily Habit Tracker",
            "subtitle": "Build streaks, plan routines",
            "keyword_field": "goals,journal,checklist,morning,streak",
            "readiness": 64,
            "readiness_label": "Good: these keywords can bring this app steady search traffic.",
            "ranking": 38,
            "ranking_label": "Fair: your app shows up for some of these keywords.",
            "reach": ("Scored for Habitly as it is today, this metadata can expect 2 to 20 downloads a day "
                      "from search."),
        },
    }
    return {
        "feature": feature,
        "state": state,
        "page_title": title,
        "page_lead": lead,
        "cta": pro_preview_words.cta(feature, state, license_url),
        "sample": sample,
        "chips": classification_legend(),
    }
