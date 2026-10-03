"""The words every Pro preview uses to sell Pro, in one place
(docs/development/PRO_PREVIEWS_PLAN.md, UI_REDESIGN_PLAN.md 11.3).

Five previews (AI Researcher, AI Competitor, ASO Simulator, Top Terms,
Rival Tracker) show the real page with sample results; each says the same
thing in the same words: what the feature does for the user's app, that this
is a preview with sample data, and two clear buttons; the one that buys or
renews Pro is the app's one Pro button (btn-pro). No price and no list
of Pro's features (they change; the website's pricing page has the price).

Free-tier module: no aso_pro imports.
"""

from .links import PRICING_URL, PRO_PAGE_URL, RENEW_URL

STATE_FREE = "free"                # the free edition: Pro needs the Mac app
STATE_UNLICENSED = "unlicensed"    # the Mac app without a license key
STATE_EXPIRED = "expired"          # the license has ended

EYEBROW = "Part of RespectASO Pro"

HEADLINES = {
    "researcher": "See the best keywords in any niche, and a title that targets them",
    "competitor": "See which of a rival's keywords your app can win",
    "simulator": "Know what a new title will bring before you ship it",
    "top_terms": "See what people search for this week",
    "rival_tracker": "Know the day a rival passes you",
}

# The feature's demo on The Creator Behind (only the AI tools have one).
VIDEOS = {
    "researcher": "https://www.youtube.com/watch?v=b-CjlO0w1fw",
    "competitor": "https://www.youtube.com/watch?v=8G7kVR2b2rY",
    "simulator": "https://www.youtube.com/watch?v=EgRptlzCrzU",
}


def cta(feature: str, state: str, license_url: str | None = None, *, needs_apple: bool = False) -> dict:
    """The hero, the buttons and the line over the locked table, for one
    feature in one buying state. ``needs_apple``: the feature also needs a
    free Apple Ads connection (Top Terms), which the line says."""
    extra = " and a free Apple Ads connection" if needs_apple else ""
    common = {"eyebrow": EYEBROW, "headline": HEADLINES[feature], "video_url": VIDEOS.get(feature, "")}
    if state == STATE_FREE:
        return {
            **common,
            "body": "A preview with sample data. Pro runs in the RespectASO Mac app.",
            "primary_label": "Get Pro for Mac", "primary_url": PRO_PAGE_URL,
            "primary_external": True, "primary_tone": "pro",
            "secondary_label": "", "secondary_url": "",
            "table_line": "Unlock the real results with Pro for Mac.",
        }
    if state == STATE_EXPIRED:
        return {
            **common,
            "body": "Your Pro license has ended. Renew to see your real results again.",
            "primary_label": "Renew Pro", "primary_url": RENEW_URL,
            "primary_external": True, "primary_tone": "renew",
            "secondary_label": "I renewed: refresh my key", "secondary_url": license_url or "",
            "table_line": "Renew Pro to unlock your real results again.",
        }
    return {
        **common,
        "body": f"A preview with sample data. Pro{extra} shows the real results for your apps.",
        "primary_label": "Get RespectASO Pro", "primary_url": PRICING_URL,
        "primary_external": True, "primary_tone": "pro",
        "secondary_label": "I have a license key", "secondary_url": license_url or "",
        "table_line": "Unlock the real results with RespectASO Pro.",
    }
