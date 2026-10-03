"""The way back from a page the reader reached from somewhere else
(docs/development/BACK_LINK_PLAN.md).

The owner, 2026-10-02: on a page that explains details (the Apple Ads setup
guide, Settings, How the scores work) there was no way back to the screen
before it, and the Mac app has no browser Back button. Such a page now opens
with "Back to <the page you came from>" at the top left of its column.

Where it leads: the page the reader came from, when that is a page of this
app; moving between the tabs of one place (Settings) does not count, so Back
from any Settings tab returns to where the reader was before Settings. With
nothing to go on (a page opened directly), the page's natural parent. The
origin is kept per place in the session, so it survives a reload and a tab
switch.

The top level (Keywords, the Discover tabs, Metadata, Rivals) is reached from
the top bar and has no way back, except when a button on another page jumped
there, such as Simulate this metadata (the owner, 2026-10-02): such a link
carries ``back``, the page it was clicked on (static/js/back-link.js adds it
to every link in a page's content that opens another page), and the page it
opens then leads back there. A detail page reads ``back`` before the Referer.

Free-tier module: names aso_pro routes only as strings resolved when present.
"""

from __future__ import annotations

from urllib.parse import urlsplit

# What each page of the app is called in "Back to ...".
PAGE_NAMES = {
    "dashboard": "Keywords",
    "apps": "Apps",
    "ai_researcher": "AI Researcher", "pro_promo_researcher": "AI Researcher",
    "ai_competitor": "AI Competitor", "pro_promo_competitor": "AI Competitor",
    "top_terms": "Top Terms", "pro_promo_top_terms": "Top Terms",
    "opportunity": "Countries",
    "simulator": "Simulator", "pro_promo_simulator": "Simulator",
    "rival_tracker": "Rival Tracker", "pro_promo_rival_tracker": "Rival Tracker",
    "rival_setup": "Rival Tracker setup",
    "settings_ai": "AI settings",
    "settings_popularity": "Apple Ads settings",
    "settings_license": "License",
    "settings_mcp": "MCP settings",
    "settings_mac_app": "Mac App settings",
    "apple_ads_setup": "the Apple Ads setup guide",
    "methodology": "How the scores work",
    "whats_new": "What's new",
    "setup": "Install and update", "settings_setup": "Install and update",
}

# The pages with a way back: url name -> (the place it belongs to, its parent).
# Pages of one place share the place, so moving between them is not "coming
# from" anywhere.
BACK = {
    "settings_ai": ("settings", "aso:dashboard"),
    "settings_popularity": ("settings", "aso:dashboard"),
    "settings_license": ("settings", "aso:dashboard"),
    "settings_mcp": ("settings", "aso:dashboard"),
    "settings_mac_app": ("settings", "aso:dashboard"),
    "apple_ads_setup": ("apple_ads_setup", "aso:settings_popularity"),
    "methodology": ("methodology", "aso:dashboard"),
    "whats_new": ("whats_new", "aso:dashboard"),
    "setup": ("setup", "aso:dashboard"),
    "settings_setup": ("setup", "aso:dashboard"),
    "apps": ("apps", "aso:dashboard"),
    "rival_setup": ("rival_setup", "aso_pro:rival_tracker"),
}

# The top level: a way back only when a link jumped here (``back``).
TOP_LEVEL = {
    "dashboard", "opportunity", "ai_researcher", "ai_competitor", "top_terms", "simulator", "rival_tracker",
    "pro_promo_researcher", "pro_promo_competitor", "pro_promo_top_terms", "pro_promo_simulator",
    "pro_promo_rival_tracker",
}

SESSION_PREFIX = "back_to:"


def _jumped_from(request):
    """(path with query, url name) of the page a link jumped here from, its
    ``back``, or None. Only a path of this app: never another site."""
    back = request.GET.get("back") or ""
    if not back.startswith("/") or back.startswith("//") or "\\" in back:
        return None
    return _page_at(urlsplit(back))


def _page_at(parts):
    from django.urls import Resolver404, resolve

    try:
        name = resolve(parts.path).url_name
    except Resolver404:
        return None
    if name not in PAGE_NAMES:
        return None
    return parts.path + (f"?{parts.query}" if parts.query else ""), name


def _referring_page(request):
    """(path with query, url name) of the app page the reader came from, or
    None for no referrer, another site, or an address that is no page."""
    referrer = request.headers.get("Referer") or ""
    if not referrer:
        return None
    parts = urlsplit(referrer)
    if parts.netloc and parts.netloc != request.get_host():
        return None
    return _page_at(parts)


def _origin(session, place: str, parent: str) -> dict:
    """Where a place leads back to: its stored origin, else its parent."""
    from django.urls import NoReverseMatch, reverse

    stored = session.get(SESSION_PREFIX + place) if session is not None else None
    if isinstance(stored, dict) and stored.get("url") and stored.get("label"):
        return {"url": stored["url"], "label": stored["label"]}
    try:
        return {"url": reverse(parent), "label": PAGE_NAMES[parent.split(":", 1)[1]]}
    except NoReverseMatch:   # the free edition has no Rival Tracker route
        return {"url": reverse("aso:dashboard"), "label": PAGE_NAMES["dashboard"]}


def back_link(request) -> dict | None:
    """{"url", "label"} for the page's "Back to ...", or None at the top level."""
    from .ui_memory import is_full_page_load

    match = getattr(request, "resolver_match", None)
    name = match.url_name if match is not None else ""
    if name in TOP_LEVEL:
        jumped = _jumped_from(request)
        if jumped is None or jumped[1] == name:
            return None
        return {"url": jumped[0], "label": PAGE_NAMES[jumped[1]]}
    if name not in BACK:
        return None
    place, parent = BACK[name]
    session = getattr(request, "session", None)
    key = SESSION_PREFIX + place

    came_from = _jumped_from(request) or _referring_page(request)
    if came_from is not None:
        path, ref_name = came_from
        ref_place = BACK.get(ref_name, (None, None))
        # Coming back from a page opened from here (the setup guide, opened
        # from Settings) is a return, not an origin: the way back stays where
        # it was, or Back would lead in a loop between the two (2026-10-02).
        returning = (ref_place[0] is not None and ref_place[0] != place
                     and urlsplit(_origin(session, *ref_place)["url"]).path == request.path)
        if ref_place[0] != place and not returning:
            origin = {"url": path, "label": PAGE_NAMES[ref_name]}
            if session is not None and is_full_page_load(request) and session.get(key) != origin:
                session[key] = origin
            return origin
    return _origin(session, place, parent)
