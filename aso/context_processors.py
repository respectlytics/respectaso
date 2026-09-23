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


def job_strip(request):
    """The one line the global strip shows about whatever long job is running,
    a keyword search or a country scan (see aso.job_strip). Never breaks a
    page render."""
    from . import job_strip as strip

    try:
        state = strip.strip_state()
    except Exception:  # e.g. the table does not exist yet on first migrate
        state = None
    return {"job_strip": state}


def country_catalog(request):
    """Every storefront, once per page, for the shared country picker.

    Emitted by base.html as JSON and read by static/js/country-picker.js. It
    lives in a context processor rather than in the picker include so that a
    page with two pickers sends the list once, and so that any script that
    needs a country name can read it instead of keeping its own map.
    Never breaks a page render.
    """
    from . import country_picker
    from .apple_ads import storage as apple_storage

    try:
        apple_source = (
            apple_storage.load_apple_settings()["popularity_source"]
            == apple_storage.SOURCE_APPLE
        )
        return {
            "country_catalog": country_picker.catalog(
                tracked=country_picker.tracked_countries(),
                apple_source=apple_source,
            )
        }
    except Exception:  # e.g. the table does not exist yet on first migrate
        return {"country_catalog": country_picker.catalog()}

# Pages that already carry their own Pro call to action, where the button
# under the navbar would only repeat it.
PRO_INVITE_HIDDEN_ON = {
    "pro_promo_researcher", "pro_promo_competitor", "pro_promo_simulator",
    "pro_promo_top_terms", "top_terms", "ai_researcher", "ai_competitor",
    "simulator", "settings_license",
}
PRO_INVITE_MAC = "Go Pro: let AI find your best keywords"
PRO_INVITE_DOCKER = "Automate your ASO with Pro for Mac"


def _pro_or_expired() -> bool:
    """Pro works here, or it did: an expired license has its own banner with
    its own button, so the invite stays out of the way."""
    from django.apps import apps as django_apps

    from .pro_access import has_pro_license

    if has_pro_license():
        return True
    if not django_apps.is_installed("licensing"):
        return False
    from licensing.decorators import get_license_info

    info = get_license_info()
    return info is not None and info.is_expired


def pro_invite(request):
    """The button under the navbar that invites a free user to Pro.

    In the Mac app without a license it points to the pricing page; in
    Docker, where Pro cannot run, it points to the Pro page with the Mac
    download. Hidden for Pro users, for an expired license (which has its own
    banner) and on pages that already make the offer. ``pricing_url`` is the
    one pricing address every template links to.
    """
    from django.conf import settings

    from .links import PRICING_URL, PRO_PAGE_URL

    context = {"pricing_url": PRICING_URL, "pro_invite": None}
    try:
        match = getattr(request, "resolver_match", None)
        if match is not None and match.url_name in PRO_INVITE_HIDDEN_ON:
            return context
        if getattr(settings, "IS_NATIVE_APP", False):
            if not _pro_or_expired():
                context["pro_invite"] = {"text": PRO_INVITE_MAC, "url": PRICING_URL}
        else:
            context["pro_invite"] = {"text": PRO_INVITE_DOCKER, "url": PRO_PAGE_URL}
    except Exception:
        # A button must never break a page.
        context["pro_invite"] = None
    return context


def difficulty_factors(request):
    """The difficulty factors and their weights, for every breakdown on screen.

    Published once per page, read by static/js/difficulty-factors.js, so the
    percentages shown are always the ones the score uses.
    """
    from .scoring import difficulty_factor_legend

    try:
        return {"difficulty_factors": difficulty_factor_legend()}
    except Exception:
        return {"difficulty_factors": []}


def classification_legend(request):
    """The seven keyword classifications, for any screen that shows one.

    Published once per page like the country catalog, because the icons, the
    colours and the wording used to be copied into every renderer that drew a
    badge and each copy drifted from the classifier at its own pace.
    """
    from .scoring import classification_legend as legend

    try:
        return {"classification_legend": legend()}
    except Exception:
        return {"classification_legend": []}
