"""Template context processors for the free-tier aso app."""

from django.apps import apps as django_apps

from .apple_ads import storage


def popularity_source(request):
    """Expose the popularity-source selection state to every template.

    Drives the "choose your popularity source" banner (shown until a source
    is explicitly selected) and the "Apple sign-in expired" banner. Reads
    are mtime-cached in storage - no per-request disk cost.
    """
    data = storage.load_apple_settings()
    source = data["popularity_source"]
    block = data["apple_ads"]

    # Signal matrix (apple-ads.instructions.md): the ACTIVE source being
    # broken is loud and persistent; secondary-data staleness under the
    # internal source is a soft dismissible notice; a deliberate sign-out
    # under the internal source (and never-connected) stays silent.
    apple_source_broken = ""
    if source == storage.SOURCE_APPLE:
        if not block["cookies"]:
            # Truly signed out - no session at all.
            apple_source_broken = "signed_out"
        elif block["session_expired"]:
            apple_source_broken = "expired"
        elif not block["tested_ok"]:
            # Signed back in but the connection test has not passed yet -
            # syncing stays paused until it does (one actionable step left).
            apple_source_broken = "needs_test"

    return {
        "popularity_source": source,
        "popularity_source_selected": source != storage.SOURCE_UNSET,
        "apple_session_expired": (
            source == storage.SOURCE_APPLE and bool(block["session_expired"])
        ),
        # "" | "expired" | "signed_out" - only ever set while apple is the
        # selected source (drives the red non-dismissible banners).
        "apple_source_broken": apple_source_broken,
        # Internal source selected but the Apple session expired: the
        # secondary "ASA: n" values stopped refreshing (soft notice).
        "apple_secondary_stale": (
            source == storage.SOURCE_INTERNAL
            and bool(block["session_expired"])
        ),
        "apple_session_expired_at": block.get("session_expired_at", ""),
        # True once the Apple Ads integration passed its connection test.
        # Display code uses this to decide whether "no Apple data" is a
        # meaningful statement (integration active, keyword below Apple's
        # threshold) or just noise (integration never set up).
        "apple_popularity_configured": bool(block["tested_ok"]),
        # Free edition ships without aso_pro; templates use this to decide
        # whether Pro settings tabs exist.
        "pro_edition": django_apps.is_installed("aso_pro"),
    }
