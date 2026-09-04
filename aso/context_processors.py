"""Template context processors for the free-tier aso app."""

from django.apps import apps as django_apps

from .apple_ads import storage


def whats_new(request):
    """One-time "see what's new" notice after a feature update.

    Tiered by version bump (aso/release_notes.py): minor/major updates
    show it once; patch updates and fresh installs are absorbed silently.
    """
    from .release_notes import should_show_notice

    try:
        return {"show_whats_new_notice": should_show_notice()}
    except Exception:  # The notice must never break a page render.
        return {"show_whats_new_notice": False}


def popularity_source(request):
    """Expose the popularity-source connection state to every template.

    Drives the banner matrix (partials/popularity_banner.html). Reads are
    mtime-cached in storage - no per-request disk cost.
    """
    data = storage.load_apple_settings()
    source = data["popularity_source"]
    block = data["apple_ads"]
    connected = storage.has_credentials()

    # Signal matrix v2 (apple-ads.instructions.md): the ACTIVE source
    # being broken is loud and persistent; secondary-data staleness under
    # the internal source is a soft dismissible notice; the standing
    # recommendation to connect Apple is informational, non-dismissible,
    # and silenced only by connecting or by the explicit opt-out.
    apple_source_broken = ""
    if source == storage.SOURCE_APPLE:
        if block["legacy_upgrade_pending"]:
            apple_source_broken = "upgrade_reconnect"
        elif not connected:
            apple_source_broken = "not_connected"
        elif block["credentials_rejected"]:
            apple_source_broken = "credential_rejected"
        elif not block["tested_ok"]:
            apple_source_broken = "needs_verify"

    apple_ready = storage.apple_source_ready()

    return {
        "popularity_source": source,
        # "" | "upgrade_reconnect" | "not_connected" | "credential_rejected"
        # | "needs_verify" - only ever set while apple is the selected
        # source (drives the red/amber non-dismissible banners).
        "apple_source_broken": apple_source_broken,
        # Standing recommendation: internal source, Apple not yet fully
        # connected, no explicit opt-out. A rejected previously working
        # connection is excluded - the specific stale notice below wins
        # over the generic recommendation.
        "apple_recommend_connect": (
            source != storage.SOURCE_APPLE
            and not apple_ready
            and not block["estimate_opt_out"]
            and not block["credentials_rejected"]
        ),
        # Internal source selected but the previously working credentials
        # got rejected: the secondary "ASA: n" values stopped refreshing
        # (soft dismissible notice keyed by the rejection timestamp).
        "apple_secondary_stale": (
            source == storage.SOURCE_INTERNAL
            and bool(block["credentials_rejected"])
        ),
        "apple_credentials_rejected_at": block["credentials_rejected_at"],
        # True once the Apple Ads integration passed verification.
        # Display code uses this to decide whether "below Apple's
        # threshold" is a meaningful statement (integration active) or
        # just noise (integration never set up).
        "apple_popularity_configured": bool(block["tested_ok"]),
        # Free edition ships without aso_pro; templates use this to decide
        # whether Pro settings tabs exist.
        "pro_edition": django_apps.is_installed("aso_pro"),
    }


def ui_state(request):
    """Dismissed-notice flags for templates (see aso.ui_state)."""
    from . import ui_state as state

    try:
        dismissed = state.is_dismissed(state.RESPECTLYTICS_BANNER)
    except Exception:  # A dismissal flag must never break a page render.
        dismissed = False
    return {"respectlytics_banner_dismissed": dismissed}


def search_job_strip(request):
    """The keyword search the global strip shows on every page but the
    dashboard: the active job, else the newest finished one not yet
    dismissed (see aso.search_jobs). Never breaks a page render."""
    from . import search_jobs

    try:
        job = search_jobs.strip_job()
        payload = search_jobs.job_payload(job) if job else None
    except Exception:  # e.g. the table does not exist yet on first migrate
        payload = None
    return {"search_job_strip": payload}
