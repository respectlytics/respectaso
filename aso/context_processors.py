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
    except Exception:  # noqa: BLE001 (the notice must never break a page)
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

    # The soft notice below, unless the reader dismissed this rejection
    # (aso.ui_state, per install: it stays dismissed after a restart).
    apple_secondary_stale = (
        source == storage.SOURCE_INTERNAL
        and bool(block["credentials_rejected"])
        and not _stale_notice_dismissed(block["credentials_rejected_at"])
    )

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
        # (soft notice, dismissible per rejection).
        "apple_secondary_stale": apple_secondary_stale,
        # True once the Apple Ads integration passed verification.
        # Display code uses this to decide whether "below Apple's
        # threshold" is a meaningful statement (integration active) or
        # just noise (integration never set up).
        "apple_popularity_configured": bool(block["tested_ok"]),
        # Free edition ships without aso_pro; templates use this to decide
        # whether Pro settings tabs exist.
        "pro_edition": django_apps.is_installed("aso_pro"),
    }


def _stale_notice_dismissed(rejected_at) -> bool:
    """Whether the Apple staleness notice of this rejection was dismissed
    (ui_state reads an unreadable file as nothing dismissed)."""
    from . import ui_state as state

    return state.is_dismissed(state.apple_stale_banner_key(rejected_at))


def ui_state(request):
    """The time zone days are counted in, which base.html compares with the
    browser's (aso.local_day)."""
    from . import local_day

    try:
        zone = local_day.zone_name()
    except Exception:  # noqa: BLE001 (an unreadable time zone must never break a page)
        zone = ""
    return {"USER_TIME_ZONE": zone}


def job_strip(request):
    """The one line the global strip shows about whatever long job is running,
    a keyword search or a country scan (see aso.job_strip). Never breaks a
    page render."""
    from . import job_strip as strip

    try:
        state = strip.strip_state()
    except Exception:  # noqa: BLE001 (the table may not exist yet, on the first migrate)
        state = None
    # The run queue shows in the activity panel in every edition: it holds
    # keyword searches and country scans too (the owner, 2026-10-02).
    return {"job_strip": state, "run_queue_enabled": True}


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
    except Exception:  # noqa: BLE001 (the table may not exist yet, on the first migrate)
        return {"country_catalog": country_picker.catalog()}

def pro_button(request):
    """The one Pro button in the top bar (docs/development/APP_SHELL_PLAN.md,
    UI_REDESIGN_PLAN.md 11.2).

    In the Mac app without a license it says Get Pro and opens the pricing
    page; with an expired license it says Renew Pro; licensed, there is none.
    In Docker, where Pro cannot run, it says Get Pro for Mac and opens the Pro
    page with the Mac download. ``pricing_url`` and ``renew_url`` are the one
    pricing and renewal addresses every template links to.
    """
    from django.conf import settings

    from .links import PRICING_URL, PRO_PAGE_URL, RENEW_URL
    from .pro_access import has_pro_license, license_expired

    context = {"pricing_url": PRICING_URL, "renew_url": RENEW_URL, "pro_button": None}
    try:
        if not getattr(settings, "IS_NATIVE_APP", False):
            context["pro_button"] = {"label": "Get Pro for Mac", "url": PRO_PAGE_URL}
        elif license_expired():
            context["pro_button"] = {"label": "Renew Pro", "url": RENEW_URL}
        elif not has_pro_license():
            context["pro_button"] = {"label": "Get Pro", "url": PRICING_URL}
    except Exception:  # noqa: BLE001 (a button must never break a page)
        context["pro_button"] = None
    return context


# The four sections of the top bar and the pages each one lights
# (UI_REDESIGN_PLAN.md 3.1). Pages listed nowhere (Apps, the Help pages)
# light none.
NAV_SECTIONS = {
    "keywords": {"dashboard"},
    "discover": {"ai_researcher", "ai_competitor", "top_terms", "opportunity",
                 "pro_promo_researcher", "pro_promo_competitor", "pro_promo_top_terms"},
    "metadata": {"simulator", "pro_promo_simulator"},
    "rivals": {"rival_tracker", "rival_setup", "pro_promo_rival_tracker"},
    "settings": {"settings_ai", "settings_license", "settings_popularity",
                 "settings_mcp", "settings_mac_app", "apple_ads_setup"},
}

# Discover's tabs, in order: key, label, the Pro edition's url, the free
# edition's url (UI_REDESIGN_PLAN.md 3.2).
DISCOVER_TABS = (
    ("ai_researcher", "AI Researcher", "aso_pro:ai_researcher", "aso:pro_promo_researcher"),
    ("ai_competitor", "AI Competitor", "aso_pro:ai_competitor", "aso:pro_promo_competitor"),
    ("top_terms", "Top Terms", "aso_pro:top_terms", "aso:pro_promo_top_terms"),
    ("opportunity", "Countries", "aso:opportunity", "aso:opportunity"),
)
# The Pro features behind a tab or a section, by aso.pro_preview_words key:
# without Pro they carry a lock and say what they do for the reader.
PRO_TABS = {"ai_researcher": "researcher", "ai_competitor": "competitor", "top_terms": "top_terms"}
PRO_SECTIONS = {"metadata": "simulator", "rivals": "rival_tracker"}


def pro_benefit(feature: str) -> str:
    """The one line a locked Pro tab says on hover: what it does for the
    reader, the headline of its preview."""
    from .pro_preview_words import HEADLINES

    return f"Pro: {HEADLINES[feature]}."


# A free edition page belongs to the tab of the feature it shows.
_DISCOVER_TAB_OF = {
    "ai_researcher": "ai_researcher", "pro_promo_researcher": "ai_researcher",
    "ai_competitor": "ai_competitor", "pro_promo_competitor": "ai_competitor",
    "top_terms": "top_terms", "pro_promo_top_terms": "top_terms",
    "opportunity": "opportunity",
}


def nav(request):
    """What the top bar, the Help menu and the section tabs need on every page.

    nav_section: which of the four sections (or settings) the page is in;
    discover_url: the Discover tab used last, remembered for the visitor
    (aso.ui_memory.DISCOVER_TAB), the first tab the first time;
    discover_tabs and settings_tabs: each tab's label, url and whether it is
    the page shown; a Discover tab of a Pro feature also says, without Pro,
    that it is locked and what it does (``locked``, ``benefit``), and
    section_locks does the same for the Metadata and Rivals sections; links:
    the outside addresses the bar and the Help menu open. Never breaks a
    page render.
    """
    from django.urls import reverse

    from . import links, ui_memory
    from .desktop_bridge import is_desktop_app
    from .pro_access import has_pro_license

    context = {
        "nav_section": "", "nav_tab": "", "discover_url": "", "discover_tabs": [],
        "settings_tabs": [], "settings_url": "", "setup_guide_url": "", "section_locks": {},
        "links": {
            "youtube": links.YOUTUBE_URL, "x": links.X_URL, "github": links.GITHUB_URL,
            "respectlytics": links.RESPECTLYTICS_URL, "contact": links.CONTACT_EMAIL,
        },
    }
    try:
        pro = django_apps.is_installed("aso_pro")
        match = getattr(request, "resolver_match", None)
        name = (match.url_name or "") if match is not None else ""
        section = next((key for key, names in NAV_SECTIONS.items() if name in names), "")
        tab = _DISCOVER_TAB_OF.get(name, "")
        session = getattr(request, "session", None)
        if (tab and session is not None and ui_memory.is_full_page_load(request)
                and session.get(ui_memory.DISCOVER_TAB) != tab):
            session[ui_memory.DISCOVER_TAB] = tab
        remembered = (session.get(ui_memory.DISCOVER_TAB) if session is not None else "") or "ai_researcher"

        locked = not has_pro_license()   # always, in the free edition
        discover_tabs = []
        for key, label, pro_url, free_url in DISCOVER_TABS:
            url = reverse(pro_url if pro else free_url)
            tab_locked = locked and key in PRO_TABS
            discover_tabs.append({"key": key, "label": label, "url": url, "active": key == tab,
                                  "locked": tab_locked,
                                  "benefit": pro_benefit(PRO_TABS[key]) if tab_locked else ""})
            if key == remembered:
                context["discover_url"] = url
        if not context["discover_url"]:
            context["discover_url"] = discover_tabs[0]["url"]

        settings_tabs = []
        if pro:
            settings_tabs.append(("settings_ai", "AI", reverse("aso_pro:settings_ai")))
        settings_tabs.append(("settings_popularity", "Apple Ads", reverse("aso:settings_popularity")))
        if pro:
            settings_tabs.append(("settings_license", "License", reverse("aso_pro:settings_license")))
            settings_tabs.append(("settings_mcp", "MCP", reverse("aso_pro:settings_mcp")))
        if is_desktop_app():
            settings_tabs.append(("settings_mac_app", "Mac App", reverse("aso:settings_mac_app")))

        context.update({
            "nav_section": section,
            "nav_tab": name,
            "discover_tabs": discover_tabs,
            "section_locks": ({key: pro_benefit(feature) for key, feature in PRO_SECTIONS.items()}
                              if locked else {}),
            "settings_tabs": [
                {"key": key, "label": label, "url": url,
                 "active": key == name or (key == "settings_popularity" and name == "apple_ads_setup")}
                for key, label, url in settings_tabs
            ],
            "settings_url": settings_tabs[0][2],
            "setup_guide_url": reverse("aso_pro:settings_setup") if pro else reverse("aso:setup"),
        })
    except Exception:  # noqa: BLE001 (the top bar must never break a page)
        return context
    return context


def back(request):
    """The page's "Back to ..." (aso/back_link.py), or None at the top level.
    Never breaks a page render."""
    from .back_link import back_link

    try:
        return {"back_link": back_link(request)}
    except Exception:  # noqa: BLE001 (a way back must never break a page)
        return {"back_link": None}


def difficulty_factors(request):
    """The difficulty factors and their weights, for every breakdown on screen.

    Published once per page, read by static/js/difficulty-factors.js, so the
    percentages shown are always the ones the score uses.
    """
    from .scoring import difficulty_factor_legend

    try:
        return {"difficulty_factors": difficulty_factor_legend()}
    except Exception:  # noqa: BLE001 (the legend must never break a page)
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
    except Exception:  # noqa: BLE001 (the legend must never break a page)
        return {"classification_legend": []}
