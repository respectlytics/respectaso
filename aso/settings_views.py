"""Views for the Popularity Source settings page and Apple Ads endpoints.

Free-tier (no license gating): manual keyword research, including the
choice of popularity source, is free for all users.

The Apple Ads connection uses the official Apple Ads Platform API v1 with
OAuth client credentials: a guided wizard generates an EC key pair
locally, the user uploads the public key in the Apple Ads UI once, pastes
back the three credential ids, and verification runs a real API call.
Fully headless - works identically in the desktop, browser, and Docker
editions.
"""

import json
import logging

from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.http import require_POST

from .apple_ads import api as apple_api
from .apple_ads import keys as apple_keys
from .apple_ads import storage, sync
from .popularity import recompute_all_classifications

logger = logging.getLogger(__name__)


def popularity_banner_view(request):
    """Rendered banner partial for the live region (popularity-banner.js).

    The banner state comes entirely from the popularity_source context
    processor, so this simply re-renders the shared partial - the client
    swaps it in whenever the state may have changed.
    """
    return render(request, "aso/partials/popularity_banner.html")


def _wizard_state(block) -> str:
    """Derive the connection wizard's state for the settings template."""
    if storage.apple_source_ready():
        return "connected"
    if storage.has_credentials():
        if block["credentials_rejected"]:
            return "credential_rejected"
        return "unverified"
    if apple_keys.has_private_key():
        return "keys_generated"
    return "no_credentials"


def settings_popularity_view(request):
    """The Popularity Source page: source cards, connection wizard, sync."""
    message = ""
    message_type = ""

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "select_source":
            message, message_type = _handle_select_source(request)
        elif action == "estimate_opt_out":
            message, message_type = _handle_estimate_opt_out(request)

    data = storage.load_apple_settings()
    block = data["apple_ads"]
    try:
        public_key_pem = (
            apple_keys.public_key_pem() if apple_keys.has_private_key() else ""
        )
    except apple_keys.AppleKeyError:  # unreadable key must not 500 the page
        public_key_pem = ""
    return render(request, "aso/settings_popularity.html", {
        "message": message,
        "message_type": message_type,
        "popularity_source": data["popularity_source"],
        "wizard_state": _wizard_state(block),
        "apple_ready": storage.apple_source_ready(),
        "apple_block": block,
        "public_key_pem": public_key_pem,
        "sync_status": sync.get_status(),
    })


def _handle_select_source(request):
    source = request.POST.get("popularity_source", "")
    if source not in (storage.SOURCE_INTERNAL, storage.SOURCE_APPLE):
        return "Unknown popularity source.", "error"
    if source == storage.SOURCE_APPLE and not storage.apple_source_ready():
        return (
            "Apple Ads isn't connected yet - complete the connection "
            "steps below first.",
            "error",
        )
    previous = storage.get_popularity_source()
    storage.save_apple_settings(popularity_source=source)
    if previous != source:
        updated = recompute_all_classifications()
        logger.info(
            "Popularity source switched %s -> %s (%d rows reclassified).",
            previous or "unset", source, updated,
        )
    label = "Apple Ads" if source == storage.SOURCE_APPLE else "RespectASO estimate"
    return (
        f"Popularity source set to {label}. Popularity, opportunity, insights, "
        "and download estimates now use it everywhere, including your history "
        "and trends. Difficulty is unaffected, and saved AI analyses keep the "
        "source they were run with.",
        "success",
    )


def _handle_estimate_opt_out(request):
    opt_out = request.POST.get("opt_out") == "1"
    storage.save_apple_settings(apple_ads={"estimate_opt_out": opt_out})
    if opt_out:
        return (
            "Noted - you'll stay on the RespectASO estimate and the "
            "recommendation banner is hidden. You can connect Apple Ads "
            "here any time.",
            "success",
        )
    return "The Apple Ads recommendation is back on.", "success"


# ── Connection wizard endpoints ──────────────────────────────────────────

@require_POST
def apple_keys_generate_view(request):
    """Generate the local EC P-256 key pair; returns the public key."""
    if apple_keys.has_private_key() and request.POST.get("replace") != "1":
        return JsonResponse({
            "ok": False,
            "error": (
                "A key already exists. Replacing it invalidates the key "
                "uploaded to Apple - confirm to continue."
            ),
            "needs_confirm": True,
        }, status=409)
    public_pem = apple_keys.generate_key_pair()
    # A fresh key invalidates any verified state tied to the old one.
    storage.save_apple_settings(apple_ads={
        "tested_ok": False, "credentials_rejected": False,
    })
    return JsonResponse({"ok": True, "public_key": public_pem})


@require_POST
def apple_keys_import_view(request):
    """Advanced path: import an existing EC P-256 private key PEM."""
    pem = request.POST.get("private_key", "")
    try:
        apple_keys.save_private_key(pem)
    except apple_keys.AppleKeyError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    storage.save_apple_settings(apple_ads={
        "tested_ok": False, "credentials_rejected": False,
    })
    return JsonResponse({"ok": True, "public_key": apple_keys.public_key_pem()})


@require_POST
def apple_credentials_view(request):
    """Save the three credential ids Apple shows after the key upload."""
    values = {}
    for field in ("client_id", "team_id", "key_id"):
        value = request.POST.get(field, "").strip()
        if not value:
            return JsonResponse({
                "ok": False,
                "error": "All three values are required - Apple shows them "
                         "right above the public key field after saving.",
            }, status=400)
        values[field] = value
    values["tested_ok"] = False
    values["credentials_rejected"] = False
    storage.save_apple_settings(apple_ads=values)
    return JsonResponse({"ok": True})


@require_POST
def apple_verify_view(request):
    """Verify the connection with real API calls and activate it.

    Steps: access token -> GET /acls (ad account discovery; a picker is
    returned when several exist) -> one real popularity probe -> mark
    verified and start the first sync in the background.
    """
    credentials = storage.api_credentials()
    if not credentials:
        return JsonResponse({
            "ok": False,
            "error": "Save the key and the three credential ids first.",
        }, status=400)

    try:
        acls = apple_api.list_acls(credentials, sleeper=apple_api.FAST_SLEEPER)
    except apple_api.AppleAdsAuthError:
        storage.save_apple_settings(apple_ads={"credentials_rejected": True})
        return JsonResponse({
            "ok": False,
            "error": (
                "Apple rejected these credentials. Double-check the three "
                "ids, and that the public key shown in the Apple Ads UI "
                "matches the one generated here."
            ),
        }, status=400)
    except apple_api.AppleAdsError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=502)

    if not acls:
        return JsonResponse({
            "ok": False,
            "error": (
                "The credentials work, but no ad account is visible to "
                "them. In the Apple Ads UI, check the API user's role "
                "(API Account Manager recommended)."
            ),
        }, status=400)

    chosen_id = request.POST.get("ad_account_id", "").strip()
    if not chosen_id and len(acls) == 1:
        chosen_id = str(acls[0]["ad_account_id"])
    if not chosen_id:
        return JsonResponse({
            "ok": False,
            "needs_account_choice": True,
            "accounts": [
                {
                    "id": str(entry["ad_account_id"]),
                    "name": entry["ad_account_name"],
                    "roles": entry["roles"],
                }
                for entry in acls
            ],
        })
    chosen = next(
        (a for a in acls if str(a["ad_account_id"]) == chosen_id), None
    )
    if chosen is None:
        return JsonResponse({
            "ok": False, "error": "Unknown ad account selection.",
        }, status=400)

    # Real probe: one page of the latest week's dataset.
    probe_country = _first_tracked_country()
    try:
        rows, _total = apple_api.query_search_term_popularity(
            credentials, chosen_id,
            country=probe_country,
            week_start=apple_api.latest_available_week(),
            page_size=1,
            sleeper=apple_api.FAST_SLEEPER,
        )
    except apple_api.AppleAdsAuthError:
        storage.save_apple_settings(apple_ads={"credentials_rejected": True})
        return JsonResponse({
            "ok": False,
            "error": "Apple rejected the session during the probe - "
                     "re-check the credentials.",
        }, status=400)
    except apple_api.AppleAdsError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=502)

    storage.save_apple_settings(apple_ads={
        "ad_account_id": chosen_id,
        "ad_account_name": chosen["ad_account_name"],
        "org_id": str(chosen["org_id"] or ""),
        "tested_ok": True,
        "credentials_rejected": False,
        "credentials_rejected_at": "",
        "legacy_upgrade_pending": False,
    })
    # First dataset sync (and backfill) starts immediately in the
    # background - the wizard's success message can honestly say data is
    # on its way.
    sync.run_manual_sync()
    return JsonResponse({
        "ok": True,
        "ad_account_name": chosen["ad_account_name"],
        "probe_country": probe_country,
        "probe_rows": len(rows),
    })


def _first_tracked_country() -> str:
    from .models import SearchResult

    country = (
        SearchResult.objects.values_list("country", flat=True)
        .order_by("country")
        .first()
    )
    return (country or "us").lower()


@require_POST
def apple_disconnect_view(request):
    """Remove the key and credentials (confirmed client-side)."""
    apple_keys.delete_private_key()
    storage.save_apple_settings(apple_ads={
        "client_id": "", "team_id": "", "key_id": "",
        "ad_account_id": "", "ad_account_name": "", "org_id": "",
        "tested_ok": False,
        "credentials_rejected": False,
        "credentials_rejected_at": "",
        "legacy_upgrade_pending": False,
    })
    return JsonResponse({"ok": True})


# ── Sync endpoints ───────────────────────────────────────────────────────

@require_POST
def apple_sync_now_view(request):
    started = sync.run_manual_sync()
    if not started:
        return JsonResponse({
            "started": False,
            "error": (
                "Sync could not start - either one is already running or "
                "Apple Ads isn't connected yet."
            ),
        })
    return JsonResponse({"started": True})


def apple_sync_status_view(request):
    return JsonResponse(sync.get_status())
