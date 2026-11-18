"""The Apps page's "Get set up" strip (docs/development/DAILY_USE_PLAN.md, step 3.7a).

One row of tiles says everything RespectASO can be connected to and which of
it is missing: the Pro license, App Store Connect, the AI provider, Apple Ads
and the Mac notifications. Every tile that applies is drawn, so a person sees all
that is missing at once, never one thing at a time (the owner, 2026-11-17).

The Pro build says how its three tiles stand through ``pro_states``;
without it, or without a licence, they are locked.

Free-tier module: no aso_pro imports.
"""

from __future__ import annotations

import logging

from django.urls import reverse

logger = logging.getLogger(__name__)

DONE, OPEN, WARN, LOCKED = "done", "open", "warn", "locked"

# key, name, what it gives. The order is the order on screen.
TILES = (
    ("pro", "Pro license", "Store numbers, AI tools and notifications."),
    ("asc", "App Store Connect", "Downloads, proceeds and your store page."),
    ("ai", "AI provider", "Keyword ideas, drafts and review replies."),
    ("apple", "Apple Ads", "Apple's own popularity numbers."),
    ("notify", "Notifications", "One a day, when an app needs you."),
)
# key -> callable(request) -> {"state", "word", "action", "quiet"}: the Pro build fills it (aso_pro.setup_tiles).
pro_states: dict = {}
HIDDEN = "setup_hidden"      # in the "ui" block: the tiles that were warn when the strip was hidden
SKIPPED = "setup_skipped"    # in the "ui" block: tiles settled with their quiet choice ("notify")
OLD_CLOSED = "setup_checklist_closed"   # 3.2.0: a Pro user closed the old card for good


def _ui() -> dict:
    from .apple_ads import storage

    return storage.load_block("ui")


def skipped(key: str) -> bool:
    return key in (_ui().get(SKIPPED) or [])


def _link(label: str, url: str, *, tone: str = "secondary", external: bool = False) -> dict:
    return {"label": label, "url": url, "tone": tone, "external": external, "fields": None}


def _post(label: str, url: str, fields: dict, *, in_place: bool = False) -> dict:
    """A button that posts. With ``in_place`` the strip's script sends it and
    redraws the strip; without it the page follows the answer's redirect."""
    return {"label": label, "url": url, "tone": "secondary", "external": False, "fields": fields,
            "in_place": in_place}


def _pro_tile() -> dict:
    """The license tile's button is the top bar's Pro button, so the two never differ
    (and "Get Pro" fits a tile at 900 px, where "Get RespectASO Pro" does not). Its quiet
    link is the second button of the Pro previews, with their words."""
    from django.apps import apps as django_apps

    from . import pro_preview_words as words
    from .context_processors import pro_button
    from .pro_access import has_pro_license, license_expired

    if has_pro_license():
        return {"state": DONE, "word": "Active"}
    context = pro_button(None)
    button = context["pro_button"] or {"label": "Get Pro", "url": context["pricing_url"]}
    ended = license_expired()
    quiet = None
    if django_apps.is_installed("aso_pro"):
        cta = words.cta("apps", words.STATE_EXPIRED if ended else words.STATE_UNLICENSED,
                        reverse("aso_pro:settings_license"))
        quiet = _link(cta["secondary_label"], cta["secondary_url"])
    return {"state": WARN if ended else OPEN, "word": "Ended" if ended else "",
            "action": _link(button["label"], button["url"], tone="pro", external=True),
            "quiet": quiet}


def _apple_tile(request) -> dict:
    from .apple_ads import storage
    from .context_processors import popularity_source

    source = popularity_source(request)
    address = reverse("aso:settings_popularity") + "#apple-connection"
    if source["apple_recommend_connect"]:
        return {"state": OPEN, "action": _link("Connect", address),
                "quiet": _post("Stay on the estimate", reverse("aso:settings_popularity"),
                               {"action": "estimate_opt_out", "opt_out": "1"})}
    if source["apple_source_broken"]:
        return {"state": WARN, "word": "Needs you", "action": _link("Open", address)}
    return {"state": DONE, "word": "Connected" if storage.apple_source_ready() else "Estimate"}


def tiles(request) -> list[dict]:
    """Every tile with its state, in the order of TILES."""
    from . import desktop_bridge
    from .pro_access import has_pro_license

    licensed = has_pro_license()
    built = []
    for key, name, gives in TILES:
        if key == "notify" and not desktop_bridge.is_desktop_app():
            continue
        try:
            if key == "pro":
                state = _pro_tile()
            elif key == "apple":
                state = _apple_tile(request)
            elif not licensed or key not in pro_states:
                state = {"state": LOCKED, "word": "With Pro"}
            else:
                state = pro_states[key](request)
        except Exception:  # one tile must never hide the page, and every tile is always drawn
            logger.exception("The setup strip could not read the state of %s", key)
            state = {"state": LOCKED, "word": ""}
        if state is None:
            continue
        built.append({"key": key, "name": name, "gives": gives, "word": "", "action": None, "quiet": None, **state})
    return built


def strip(request) -> dict | None:
    """What the Apps page draws, or None when nothing is left to set up."""
    built = tiles(request)
    left = [tile for tile in built if tile["state"] != DONE]
    if not left:
        return None
    ui = _ui()
    kept = ui.get(HIDDEN)
    if not isinstance(kept, list):       # None, or a value a hand edit left there
        kept = [] if ui.get(OLD_CLOSED) else None
    warn = {tile["key"] for tile in built if tile["state"] == WARN}
    return {"tiles": built, "done": len(built) - len(left), "total": len(built), "left": len(left),
            "hidden": kept is not None and warn <= set(kept)}


def hide(request) -> None:
    from .apple_ads import storage

    warn = sorted(tile["key"] for tile in tiles(request) if tile["state"] == WARN)
    storage.save_block("ui", {HIDDEN: warn})


def show() -> None:
    from .apple_ads import storage

    # save_block merges and cannot remove a key, so "not hidden" is stored as None and False.
    storage.save_block("ui", {HIDDEN: None, OLD_CLOSED: False})


def skip(key: str) -> None:
    """Settle a tile with its quiet choice. Only the notifications tile is settled here;
    Apple Ads keeps its own switch (estimate_opt_out)."""
    from .apple_ads import storage

    if key != "notify":
        raise ValueError("This tile cannot be skipped.")
    storage.save_block("ui", {SKIPPED: sorted({*(_ui().get(SKIPPED) or []), key})})
