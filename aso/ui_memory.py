"""What the app remembers for one visitor between pages, in the Django session.

    the Dashboard's view       the app, country, page size and filters it
                               shows; a full page load of the Dashboard
                               without them opens the view last shown
    the App Summary folded     drawn folded by the server
    the setup checklist folded the Keywords page's "Get set up" card, folded
    the Discover tab           the tab of Discover shown last, which the top
                               bar's Discover opens (context_processors.nav)
    the current app            the app picked last on any page; every app
                               picker starts on it (KEYWORDS_PAGE_PLAN.md M2.1)
    each country picker        the countries last picked on the Dashboard's
                               keyword search and on the Opportunity page

These used to live in the browser's storage, which desktop-compat keeps out
of the app (docs/development/NO_BROWSER_STORAGE_PLAN.md). The session lasts
as long as that storage did in the Mac app: pywebview starts every launch
with no cookies, so until the app quits. In a browser the session cookie
lasts a year from the last change (SESSION_COOKIE_AGE), where the browser's
storage kept them for good.

The mechanism is the one the Dashboard's selection uses
(aso/history_selection.py): the session holds the state, the page receives
it with its render, and each change is one small POST (``ui_memory_view``
through static/js/ui-memory.js). Notices dismissed for good are per install
and survive a restart, so they live in aso/ui_state.py instead.

Every write happens only when the value changes: Django saves the whole
session at once, so a write that changes nothing could only overwrite what
another request just saved.

Ships in the free-tier ``aso`` app: no ``aso_pro`` or ``licensing`` imports.
"""

from __future__ import annotations

from . import countries
from .history_filters import (
    FILTER_KEYS,
    FILTER_LIST_KEYS,
    FILTER_VALUE_KEYS,
    HISTORY_PER_PAGE_CHOICES,
    HISTORY_PER_PAGE_DEFAULT,
)

DASHBOARD_VIEW = "dashboard_view"
APP_SUMMARY_FOLDED = "app_summary_folded"
COUNTRY_PICKS = "country_picks"
SETUP_CHECKLIST_FOLDED = "setup_checklist_folded"
# Kept for good, in the settings file and not the session: a Pro user who
# closed "Get set up" (the owner, 2026-10-02). Free users cannot close it.
SETUP_CHECKLIST_CLOSED = "setup_checklist_closed"
DISCOVER_TAB = "discover_tab"
CURRENT_APP = "current_app"

# The pickers that remember their countries (the memory_key of
# aso/partials/country_picker.html). The Rival Tracker's setup picker is
# filled from its tracker and remembers nothing.
COUNTRY_PICKERS = ("search", "opportunity")


def is_full_page_load(request) -> bool:
    """False for an in-place update (static/js/in-place.js sends the header)."""
    return request.headers.get("X-Requested-With") != "XMLHttpRequest"


def is_background(request) -> bool:
    """A refresh the page made by itself, not a view the reader chose."""
    return request.headers.get("X-In-Place") == "background"


# ---- the app picked last, on any page -------------------------------------


def current_app_id(request) -> int | None:
    """The app the visitor picked last on any page, if it still exists."""
    from .models import App

    session = getattr(request, "session", None)   # a request built by hand has none
    value = session.get(CURRENT_APP) if session is not None else None
    if isinstance(value, int) and App.objects.filter(pk=value).exists():
        return value
    return None


def remember_app(request, app_id) -> None:
    """Remember an app the visitor picked. "All apps" and empty choices are a
    view, not an app, and leave the memory as it is."""
    try:
        app_id = int(app_id)
    except (TypeError, ValueError):
        return
    session = getattr(request, "session", None)
    if session is not None and app_id > 0 and session.get(CURRENT_APP) != app_id:
        session[CURRENT_APP] = app_id


# ---- the Dashboard's view ------------------------------------------------


def _per_page(raw) -> int | None:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return value if value in HISTORY_PER_PAGE_CHOICES else None


def _view_from(query, before: dict) -> dict:
    """The view an address shows, as remembered. The page size is kept when
    the address has none: switching apps starts from a bare address, and the
    page size chosen before still applies the next time."""
    view = {}
    app = (query.get("app") or "").strip()
    if app.isdigit():
        view["app"] = app
    country = (query.get("country") or "").strip().lower()
    if countries.is_valid(country):
        view["country"] = country
    per_page = _per_page(query.get("per_page"))
    if per_page is None:
        per_page = _per_page(before.get("per_page"))
    if per_page:
        view["per_page"] = per_page
    filters = {}
    for key in FILTER_LIST_KEYS:
        values = [value for value in query.getlist(key) if value]
        if values:
            filters[key] = values
    for key in FILTER_VALUE_KEYS:
        value = (query.get(key) or "").strip()
        if value:
            filters[key] = value
    if filters:
        view["filters"] = filters
    return view


def _remembered_view(request) -> dict:
    view = request.session.get(DASHBOARD_VIEW)
    return view if isinstance(view, dict) else {}


def remember_dashboard_view(request) -> None:
    """Remember the view this request shows, unless the page asked by itself
    (a background refresh). A part the address leaves out is forgotten,
    except the page size."""
    if is_background(request):
        return
    before = _remembered_view(request)
    view = _view_from(request.GET, before)
    if view.get("app"):
        remember_app(request, view["app"])
    if view == before:
        return
    if view:
        request.session[DASHBOARD_VIEW] = view
    else:
        request.session.pop(DASHBOARD_VIEW, None)


def restored_dashboard_query(request) -> str | None:
    """For a full page load, the address query with the remembered view
    filled in where the address says nothing; None when nothing is added.

    Each part is added only when the address lacks it: the app when it still
    exists, the country when the resulting table has a row there (what its
    country menu offers), the page size when it is not the default, the
    filters when the address has none. The answer lacks nothing that could
    be added, so asking again with it adds nothing more."""
    if not is_full_page_load(request):
        return None
    remembered = dict(_remembered_view(request))
    # An app picked on another page since wins over the one this page showed
    # last; a view of All apps stays All apps.
    current = current_app_id(request)
    if current and (remembered.get("app") or not remembered):
        if str(remembered.get("app") or "") != str(current):
            remembered = {key: value for key, value in remembered.items() if key not in ("country", "filters")}
        remembered["app"] = str(current)
    if not remembered:
        return None
    query = request.GET.copy()
    # In this order: the country is checked against the app filled in first.
    added = [fill(query, remembered) for fill in (_fill_app, _fill_country, _fill_per_page, _fill_filters)]
    if not any(added):
        return None
    query.pop("page", None)
    return query.urlencode()


def _fill_app(query, remembered) -> bool:
    from .models import App

    app = str(remembered.get("app") or "")
    if "app" in query or not app.isdigit() or not App.objects.filter(pk=int(app)).exists():
        return False
    query["app"] = app
    return True


def _fill_country(query, remembered) -> bool:
    from .models import SearchResult

    country = str(remembered.get("country") or "")
    if "country" in query or not countries.is_valid(country):
        return False
    rows = SearchResult.objects.filter(country=country)
    shown_app = query.get("app") or ""
    if shown_app.isdigit():
        rows = rows.filter(keyword__app_id=int(shown_app))
    if not rows.exists():
        return False
    query["country"] = country
    return True


def _fill_per_page(query, remembered) -> bool:
    per_page = _per_page(remembered.get("per_page"))
    if "per_page" in query or not per_page or per_page == HISTORY_PER_PAGE_DEFAULT:
        return False
    query["per_page"] = str(per_page)
    return True


def _fill_filters(query, remembered) -> bool:
    filters = remembered.get("filters")
    if not isinstance(filters, dict) or any(key in query for key in FILTER_KEYS):
        return False
    added = False
    for key in FILTER_LIST_KEYS:
        for value in filters.get(key) or ():
            query.appendlist(key, str(value))
            added = True
    for key in FILTER_VALUE_KEYS:
        if filters.get(key):
            query[key] = str(filters[key])
            added = True
    return added


# ---- small values the page sends ------------------------------------------


def app_summary_folded(request) -> bool:
    """Whether the glance card's details (by country, rank spread, keyword
    mix) are folded: folded unless the visitor opened them
    (KEYWORDS_PAGE_PLAN.md M2.4)."""
    return request.session.get(APP_SUMMARY_FOLDED) is not False


def remembered_countries(request, picker: str) -> list[str]:
    """The countries a picker last held for this visitor; [] when none."""
    picks = request.session.get(COUNTRY_PICKS)
    if not isinstance(picks, dict):
        return []
    codes = picks.get(picker)
    if not isinstance(codes, list):
        return []
    return countries.clean(code for code in codes if isinstance(code, str))


def save(request, name, value):
    """Store what the page sends and answer with the value as stored.

    ``app_summary_folded`` and ``setup_checklist_folded`` take true or false; ``countries.<picker>`` takes
    a list of storefront codes, kept valid, in lower case, each once, in
    order. Anything else raises ValueError with a sentence for the page."""
    if name == APP_SUMMARY_FOLDED:
        # Folded is the default here, so both answers are kept.
        if not isinstance(value, bool):
            raise ValueError("Folded is true or false.")
        if request.session.get(name) is not value:
            request.session[name] = value
        return value
    if name == SETUP_CHECKLIST_CLOSED:
        from .pro_access import has_pro_license

        if value is not True:
            raise ValueError("Closing is for good: send true.")
        if not has_pro_license():
            raise ValueError("Get set up stays until Pro is unlocked.")
        from .apple_ads import storage

        storage.save_block("ui", {SETUP_CHECKLIST_CLOSED: True})
        return True
    if name == SETUP_CHECKLIST_FOLDED:
        if not isinstance(value, bool):
            raise ValueError("Folded is true or false.")
        if value:
            if request.session.get(name) is not True:
                request.session[name] = True
        else:
            request.session.pop(name, None)
        return value

    prefix = "countries."
    picker = name[len(prefix):] if isinstance(name, str) and name.startswith(prefix) else None
    if picker in COUNTRY_PICKERS:
        if not isinstance(value, list) or not all(isinstance(code, str) for code in value):
            raise ValueError("Countries are a list of storefront codes.")
        codes = countries.clean(value)
        picks = request.session.get(COUNTRY_PICKS)
        picks = dict(picks) if isinstance(picks, dict) else {}
        if picks.get(picker) != codes:
            picks[picker] = codes
            request.session[COUNTRY_PICKS] = picks
        return codes

    raise ValueError("RespectASO does not remember that.")
