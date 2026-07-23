"""Views for the Popularity Source settings page and Apple Ads endpoints.

Free-tier (no license gating): manual keyword research, including the
choice of popularity source, is free for all users.
"""

import logging

from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from .apple_ads import auth as apple_auth
from .apple_ads import storage, sync
from .apple_ads.client import (
    AppleAdsAppAccessError,
    AppleAdsAuthError,
    AppleAdsError,
    fetch_popularities,
)
from .popularity import recompute_all_classifications

logger = logging.getLogger(__name__)

TEST_TERM = "fitness"  # High-volume term used for the one-term test fetch.


def popularity_banner_view(request):
    """Rendered banner partial for the live region (popularity-banner.js).

    The banner state comes entirely from the popularity_source context
    processor, so this simply re-renders the shared partial - the client
    swaps it in whenever the state may have changed.
    """
    return render(request, "aso/partials/popularity_banner.html")


def settings_popularity_view(request):
    """The Popularity Source page: source cards, Apple sign-in, sync status."""
    message = ""
    message_type = ""

    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "select_source":
            message, message_type = _handle_select_source(request)
        elif action == "save_app_id":
            message, message_type = _handle_save_app_id(request)

    data = storage.load_apple_settings()
    block = data["apple_ads"]
    return render(request, "aso/settings_popularity.html", {
        "message": message,
        "message_type": message_type,
        "popularity_source": data["popularity_source"],
        "apple_signed_in": apple_auth.session_valid(),
        "apple_tested_ok": block["tested_ok"],
        "apple_session_expired": block["session_expired"],
        "apple_primary_app_id": block["primary_app_id"],
        "apple_ready": storage.apple_source_ready(),
        "sync_status": sync.get_status(),
        "is_native": apple_auth.is_native(),
        "tracked_apps": _tracked_apps(),
    })


def _tracked_apps():
    from .models import App

    return list(App.objects.exclude(track_id__isnull=True).values("name", "track_id"))


def _handle_select_source(request):
    source = request.POST.get("popularity_source", "")
    if source not in (storage.SOURCE_INTERNAL, storage.SOURCE_APPLE):
        return "Unknown popularity source.", "error"
    if source == storage.SOURCE_APPLE and not storage.apple_source_ready():
        return (
            "Apple Ads isn't ready yet - sign in and run the connection "
            "test first.",
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


def _handle_save_app_id(request):
    app_id = request.POST.get("primary_app_id", "").strip()
    if app_id and not app_id.isdigit():
        return (
            "The Primary App ID must be numeric (e.g. 1234567890).",
            "error",
        )
    storage.save_apple_settings(apple_ads={"primary_app_id": app_id, "tested_ok": False})
    if not app_id:
        return "Primary App ID cleared.", "success"
    return (
        "Primary App ID saved. Run the connection test to activate Apple Ads.",
        "success",
    )


@require_POST
def apple_signin_view(request):
    """Start the embedded Apple sign-in window (native mode)."""
    state = apple_auth.start_signin()
    return JsonResponse(state)


def apple_signin_status_view(request):
    """Poll endpoint while the sign-in window is open."""
    state = apple_auth.get_signin_status()
    state["signed_in"] = apple_auth.session_valid()
    return JsonResponse(state)


@require_POST
def apple_signout_view(request):
    apple_auth.sign_out()
    return redirect("aso:settings_popularity")


@require_POST
def apple_test_view(request):
    """One-term test fetch - gates selecting Apple as the source."""
    block = storage.load_apple_settings()["apple_ads"]
    app_id = block["primary_app_id"]
    if not app_id:
        return JsonResponse(
            {"ok": False, "error": "Set a Primary App ID first."}, status=400
        )
    header = apple_auth.cookie_header()
    if not header:
        return JsonResponse(
            {"ok": False, "error": "Sign in with your Apple Ads account first."},
            status=400,
        )
    try:
        # Capped sleeper: the embedded webview aborts fetches after ~60s,
        # so the test must stay fast even through the client's retries.
        values = fetch_popularities(
            [TEST_TERM], "us", app_id, header, sleeper=apple_auth.FAST_SLEEPER
        )
    except AppleAdsAuthError:
        apple_auth.mark_session_expired()
        return JsonResponse(
            {"ok": False, "error": "Apple rejected the session - sign in again."},
            status=400,
        )
    except AppleAdsAppAccessError as e:
        return JsonResponse({"ok": False, "error": str(e)}, status=400)
    except AppleAdsError as e:
        message = str(e)
        if "KWS_NO_ORG_CONTENT_PROVIDERS" in message:
            # Transient right after a fresh sign-in: Apple's backend has
            # not finished provisioning the session's org context yet.
            message = (
                "Apple's session is still warming up after sign-in - wait "
                "about 30 seconds and run the test again. If it keeps "
                "failing, sign out and sign in once more. "
                f"({message})"
            )
        return JsonResponse({"ok": False, "error": message}, status=502)
    except Exception as e:  # Never return an HTML 500 to the fetch() caller.
        logger.exception("Apple connection test crashed")
        return JsonResponse(
            {"ok": False, "error": f"Unexpected error during the test: {e}"},
            status=500,
        )

    storage.save_apple_settings(
        apple_ads={"tested_ok": True, "session_expired": False}
    )
    return JsonResponse({
        "ok": True,
        "sample_term": TEST_TERM,
        "sample_popularity": values.get(TEST_TERM),
    })


@require_POST
def apple_sync_now_view(request):
    started = sync.run_manual_sync()
    if not started:
        return JsonResponse({
            "started": False,
            "error": (
                "Sync could not start - either one is already running or "
                "Apple Ads isn't configured yet."
            ),
        })
    return JsonResponse({"started": True})


def apple_sync_status_view(request):
    return JsonResponse(sync.get_status())
