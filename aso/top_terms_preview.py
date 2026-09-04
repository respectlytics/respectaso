"""Sample-data preview of the Top Search Terms page.

Shown wherever the real page cannot render yet: the free edition (Pro
feature), a Pro install without a valid license, and a licensed install
whose Apple Ads connection is not set up (or has not synced a week yet).
The preview mirrors the live page - filters, mover cards, the terms table
with sparklines - filled with hand-written SAMPLE rows whose search terms
are blurred, so people see what the tab looks like and what it is for
before they get a license or connect Apple Ads.

None of this touches Apple: the rows below are invented, plausible-looking
values on Apple's real scales (popularity 1-100 with the observed ~40
floor, 1-5 tiers, category ranks). The page labels them as sample data.

Free-tier module: no aso_pro imports, no network, no database.
"""

import datetime as dt
import json

from django.urls import reverse

from aso.apple_ads.genres import genre_label

PRICING_URL = "https://respectaso.com/pricing/"
RENEW_URL = "https://respectaso.com/license/renew/"

# States the preview page can be in. Each maps to one CTA block.
STATE_FREE = "free"                    # free edition: Pro feature
STATE_UNLICENSED = "unlicensed"        # Pro build, no license key
STATE_EXPIRED = "expired"              # Pro build, license expired
STATE_NOT_CONNECTED = "not_connected"  # licensed, Apple Ads not connected
STATE_SYNCING = "syncing"              # licensed + verified, first week pending
STATES = (
    STATE_FREE, STATE_UNLICENSED, STATE_EXPIRED,
    STATE_NOT_CONNECTED, STATE_SYNCING,
)

SPARKLINE_WEEKS = 12

# (term, genre, rank_in_genre, popularity, popularity_in_genre, tier, trend)
# Sorted by storefront popularity, ranks consistent within each category.
SAMPLE_ROWS = (
    ("photo editor", "PHOTO_VIDEO", 1, 92, 100, 5, 2),
    ("video chat", "SOCIAL_NETWORKING", 1, 90, 100, 5, 0),
    ("puzzle games", "GAMES", 1, 88, 100, 5, 1),
    ("qr code scanner", "PRODUCTIVITY_UTILITIES", 1, 85, 100, 5, -1),
    ("step counter", "HEALTH_FITNESS", 1, 83, 100, 5, 3),
    ("budget planner", "FINANCE", 1, 79, 100, 4, 5),
    ("word games", "GAMES", 2, 77, 88, 4, -2),
    ("live tv", "ENTERTAINMENT", 1, 76, 100, 4, 1),
    ("flight tracker", "TRAVEL", 1, 74, 100, 4, 6),
    ("learn spanish", "EDUCATION", 1, 72, 100, 4, 0),
    ("video editor", "PHOTO_VIDEO", 2, 71, 77, 4, -1),
    ("coupons", "SHOPPING", 1, 68, 100, 3, 2),
    ("sleep sounds", "HEALTH_FITNESS", 2, 66, 79, 3, 4),
    ("habit tracker", "LIFESTYLE", 1, 63, 100, 3, 1),
    ("invoice maker", "BUSINESS", 1, 61, 100, 3, -3),
)

# (term, genre, prev_rank, rank)
SAMPLE_RISERS = (
    ("ai photo generator", "PHOTO_VIDEO", 48, 12),
    ("flight tracker", "TRAVEL", 7, 1),
    ("budget planner", "FINANCE", 9, 1),
    ("sleep sounds", "HEALTH_FITNESS", 6, 2),
    ("tax calculator", "FINANCE", 31, 14),
    ("study timer", "EDUCATION", 40, 22),
)
SAMPLE_FALLERS = (
    ("word games", "GAMES", 1, 2),
    ("weather radar", "PRODUCTIVITY_UTILITIES", 3, 9),
    ("video editor", "PHOTO_VIDEO", 1, 2),
    ("meal planner", "FOOD_DRINK", 4, 11),
    ("invoice maker", "BUSINESS", 1, 4),
    ("bike computer", "SPORTS", 8, 19),
)
# (term, genre, popularity)
SAMPLE_NEW_TERMS = (
    ("receipt scanner", "FINANCE", 58),
    ("workout planner", "HEALTH_FITNESS", 52),
    ("plant identifier", "EDUCATION", 49),
    ("split bill", "FINANCE", 47),
    ("noise cancelling", "PRODUCTIVITY_UTILITIES", 45),
    ("car wash near me", "LIFESTYLE", 44),
)

# Fixed week-to-week wiggle so every sparkline looks like real history
# without being random (a random preview would flicker between reloads).
_WIGGLE = (0, -1, 1, 0, -2, 1, 2, 0, -1, 1, 0, 0)


def _sparkline_series(popularity: int, trend: int) -> list[list]:
    """12 weekly points ending at `popularity`, drifting by `trend`.

    The last two points differ by exactly `trend`, matching the trend
    figure shown next to the sparkline. Week dates are the last 12
    completed Sunday-Saturday weeks; the sparkline ignores them, they only
    keep the payload shape identical to the live page's series.
    """
    today = dt.date.today()
    last_sunday = today - dt.timedelta(days=(today.weekday() + 1) % 7 or 7)
    series = []
    for i in range(SPARKLINE_WEEKS):
        weeks_back = SPARKLINE_WEEKS - 1 - i
        week = last_sunday - dt.timedelta(days=7 * weeks_back)
        if weeks_back == 0:
            value = popularity
        elif weeks_back == 1:
            value = popularity - trend
        else:
            value = popularity - trend - round(trend * weeks_back / 4) + _WIGGLE[i]
        series.append([week.isoformat(), max(40, min(100, value))])
    return series


def sample_rows() -> list[dict]:
    """Table rows in the live page's shape (see aso_pro.top_terms)."""
    rows = []
    for term, genre, rank, popularity, in_genre, tier, trend in SAMPLE_ROWS:
        rows.append({
            "term": term,
            "genre": genre,
            "genre_pretty": genre_label(genre),
            "rank": rank,
            "popularity": popularity,
            "popularity_in_genre": in_genre,
            "tier": tier,
            "trend": trend,
            "tracked": False,
            "series_json": json.dumps(_sparkline_series(popularity, trend)),
        })
    return rows


def sample_movers() -> tuple[list[dict], list[dict], list[dict]]:
    """(risers, fallers, new_terms) in the live page's shape."""
    risers = [
        {"term": t, "genre": g, "genre_pretty": genre_label(g),
         "prev_rank": prev, "rank": rank, "tracked": False}
        for t, g, prev, rank in SAMPLE_RISERS
    ]
    fallers = [
        {"term": t, "genre": g, "genre_pretty": genre_label(g),
         "prev_rank": prev, "rank": rank, "tracked": False}
        for t, g, prev, rank in SAMPLE_FALLERS
    ]
    new_terms = [
        {"term": t, "genre": g, "genre_pretty": genre_label(g),
         "popularity": pop, "tracked": False}
        for t, g, pop in SAMPLE_NEW_TERMS
    ]
    return risers, fallers, new_terms


def sample_terms() -> set[str]:
    """Every invented term the preview renders (guard tests use this to
    prove none of them can leak into a live page)."""
    return (
        {r[0] for r in SAMPLE_ROWS}
        | {r[0] for r in SAMPLE_RISERS}
        | {r[0] for r in SAMPLE_FALLERS}
        | {r[0] for r in SAMPLE_NEW_TERMS}
    )


def _cta(state: str, license_url: str | None) -> dict:
    """The state-specific call to action: hero copy, buttons, and the one
    line shown on the card that sits over the blurred table.

    `primary_tone` picks the button colour the rest of the app already
    uses for that action: purple = buy Pro, amber = renew, sky = Apple
    Ads connection (the template maps it to whole class literals).
    """
    connect_url = reverse("aso:settings_popularity")
    guide_url = reverse("aso:apple_ads_setup")
    if state == STATE_FREE:
        return {
            "eyebrow": "Pro feature",
            "headline": "This is the page you unlock with RespectASO Pro",
            "body": (
                "Below is what this page looks like live, with sample "
                "data and the search terms locked. RespectASO Pro plus a "
                "free Apple Ads connection (a one-time API key, about 5 "
                "minutes, no ads or spend required) unlocks Apple's real "
                "ranking for your storefronts, refreshed every week."
            ),
            "primary_label": "Get RespectASO Pro",
            "primary_url": PRICING_URL,
            "primary_external": True,
            "primary_tone": "purple",
            "secondary_label": "",
            "secondary_url": "",
            "table_line": "Unlock the real terms with RespectASO Pro.",
        }
    if state == STATE_UNLICENSED:
        return {
            "eyebrow": "Pro feature · license required",
            "headline": "Activate a Pro license to unlock the real terms",
            "body": (
                "Below is what this page looks like live, with sample "
                "data and the search terms locked. A Pro license plus a "
                "free Apple Ads connection (a one-time API key, about 5 "
                "minutes, no ads or spend required) unlocks Apple's real "
                "ranking for your storefronts, refreshed every week."
            ),
            "primary_label": "Get a Pro License",
            "primary_url": PRICING_URL,
            "primary_external": True,
            "primary_tone": "purple",
            "secondary_label": "Already have a key? Activate it",
            "secondary_url": license_url or "",
            "table_line": "Unlock the real terms with a Pro license.",
        }
    if state == STATE_EXPIRED:
        return {
            "eyebrow": "Pro feature · license expired",
            "headline": "Your Pro license has expired",
            "body": (
                "Renew to unlock Apple's top search terms again. Until "
                "then this page shows sample data with the search terms "
                "locked; your Apple Ads connection and synced weeks are "
                "untouched and pick up right where they left off."
            ),
            "primary_label": "Renew License",
            "primary_url": RENEW_URL,
            "primary_external": True,
            "primary_tone": "amber",
            "secondary_label": "Already renewed? Refresh your key",
            "secondary_url": license_url or "",
            "table_line": "Renew your license to unlock the real terms.",
        }
    if state == STATE_SYNCING:
        return {
            "eyebrow": "Apple Ads connected · first sync pending",
            "headline": "Your first week of Apple data is on its way",
            "body": (
                "Apple Ads is connected and the first weekly dataset is "
                "downloading in the background. This page unlocks by "
                "itself once a week of data has landed; until then it "
                "shows sample data with the search terms locked."
            ),
            "primary_label": "View sync status",
            "primary_url": connect_url + "#apple-connection",
            "primary_external": False,
            "primary_tone": "sky",
            "secondary_label": "",
            "secondary_url": "",
            "table_line": (
                "The real terms unlock here as soon as the first sync "
                "finishes."
            ),
        }
    return {
        "eyebrow": "One step left",
        "headline": "Connect Apple Ads to unlock the real terms",
        "body": (
            "Your license is active. Below is what this page looks like "
            "live, with sample data and the search terms locked; a free "
            "Apple Ads connection (a one-time API key, about 5 minutes, "
            "no ads or spend required) unlocks Apple's official ranking "
            "for your storefronts, refreshed every week."
        ),
        "primary_label": "Connect Apple Ads",
        "primary_url": connect_url + "#apple-connection",
        "primary_external": False,
        "primary_tone": "sky",
        "secondary_label": "Read the setup guide",
        "secondary_url": guide_url,
        "table_line": "Connect Apple Ads to unlock the real terms.",
    }


def preview_context(state: str, *, license_url: str | None = None) -> dict:
    """Template context for aso/top_terms_preview.html.

    `license_url` is the Pro edition's Settings → License page, passed in
    by the Pro view so this free-tier module never names an aso_pro route.
    """
    if state not in STATES:
        raise ValueError(f"unknown preview state: {state!r}")
    risers, fallers, new_terms = sample_movers()
    return {
        "preview": {
            "state": state,
            "rows": sample_rows(),
            "risers": risers,
            "fallers": fallers,
            "new_terms": new_terms,
            "cta": _cta(state, license_url),
        },
    }
