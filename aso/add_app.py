"""Adding an app: the one Add an app dialog every page opens
(aso/partials/add_app_dialog.html, static/js/add-app.js) and the writers
behind it.

The dialog finds any app in the App Store by name or link, or takes one by
hand (name and an optional bundle ID), in both editions. The Pro build adds
the apps of the connected App Store Connect account through
``account_sources``: callables that answer ``{"apps": [{"id", "name",
"bundle_id"}], "url": <the endpoint that adds them>}`` or None. The free
edition registers none, so its dialog is search and manual only.

Adding is idempotent: an app already tracked (by its App Store ID, or by
hand with the same name and bundle ID) is found, never added twice, so a
second click or a second request changes nothing.
"""

from __future__ import annotations

from django.db import IntegrityError, transaction

from .models import App

account_sources: list = []


def account_section() -> dict | None:
    """The account apps the dialog offers, from the first source that has
    any; None without a source (the free edition) or with nothing to add."""
    for source in account_sources:
        section = source()
        if section and section.get("apps"):
            return section
    return None


def add_from_store(track_id: int, *, name: str = "", bundle_id: str = "", store_url: str = "",
                   icon_url: str = "", seller_name: str = "") -> tuple[App, bool]:
    """Track the App Store app ``track_id``: (the app, whether it was new).
    An app already tracked is answered as it is."""
    defaults = {"name": (name or "App")[:200], "bundle_id": (bundle_id or "")[:200],
                "store_url": store_url or "", "icon_url": icon_url or "",
                "seller_name": (seller_name or "")[:200]}
    try:
        with transaction.atomic():
            return App.objects.get_or_create(track_id=int(track_id), defaults=defaults)
    except IntegrityError:          # the same app added by a second request at the same moment
        return App.objects.get(track_id=int(track_id)), False


def add_manual(name: str, bundle_id: str = "") -> tuple[App, bool]:
    """Track an app by hand (no App Store listing): (the app, whether it was
    new). The same name and bundle ID added again is the app already there."""
    name, bundle_id = (name or "").strip()[:200], (bundle_id or "").strip()[:200]
    same = App.objects.filter(track_id__isnull=True, name__iexact=name, bundle_id__iexact=bundle_id).first()
    if same is not None:
        return same, False
    return App.objects.create(name=name, bundle_id=bundle_id), True


def names_sentence(names: list[str]) -> str:
    """'Lullgate', 'Lullgate and Moonpond', 'Lullgate, Moonpond and Tidewell'."""
    names = [n for n in names if n]
    if len(names) <= 1:
        return "".join(names)
    return ", ".join(names[:-1]) + " and " + names[-1]


def result_message(added: list[str], already: list[str]) -> str:
    """What the dialog says after an add."""
    parts = []
    if added:
        parts.append(f"Added {names_sentence(added)}.")
    if already:
        verb = "was" if len(already) == 1 else "were"
        parts.append(f"{names_sentence(already)} {verb} already in your apps.")
    return " ".join(parts) or "Nothing to add."
