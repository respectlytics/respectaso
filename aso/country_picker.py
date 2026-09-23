"""The data behind every country picker in the app.

One catalog, emitted once per page from the base template as JSON, read by
static/js/country-picker.js. It exists so that no template and no script ever
carries its own country list again: dashboard.html used to hold three of them
and they disagreed with each other, so the label map knew about Poland while
the picker could not select it.

The catalog carries two ABSENCE markers per storefront, and only absences,
because marking the normal case on 175 rows would be noise:

  apple_ads     False means Apple Ads does not operate there, so popularity is
                RespectASO's estimate. Only surfaced when the user is actually
                on the Apple source; on the internal estimate every storefront
                is equal and the marker would say nothing.
  has_language  False means Apple offers no App Store listing language for
                that storefront, so a listing appears in the app's fallback
                language. Keyword research still works there.

Both are facts about the market, never warnings.
"""

from __future__ import annotations

import unicodedata

from aso import countries

# Search aliases. A picker with 175 rows is only usable if the obvious word
# finds the row, so these cover what a person actually types: the old name,
# the abbreviation, the local spelling.
ALIASES: dict[str, tuple[str, ...]] = {
    "us": ("usa", "america", "united states"),
    "gb": ("uk", "britain", "england", "scotland", "wales"),
    "ae": ("uae", "emirates", "dubai"),
    "kr": ("south korea", "korea"),
    "tr": ("turkey",),
    "cz": ("czech republic", "czechia"),
    "nl": ("holland",),
    "ch": ("suisse", "schweiz"),
    "hk": ("hong kong",),
    "mo": ("macau", "macao"),
    "tw": ("taiwan",),
    "cn": ("prc", "mainland china"),
    "ru": ("russian federation",),
    "ba": ("bosnia",),
    "mk": ("macedonia",),
    "ci": ("ivory coast",),
    "cd": ("drc", "democratic republic of the congo"),
    "cg": ("republic of the congo",),
    "sz": ("swaziland",),
    "mm": ("burma",),
    "la": ("laos",),
    "do": ("dominican republic",),
    "za": ("south africa", "rsa"),
}


def _fold(text: str) -> str:
    """Lowercase and strip diacritics, so "turkiye" finds "Türkiye"."""
    normalized = unicodedata.normalize("NFD", text or "")
    return "".join(c for c in normalized if not unicodedata.combining(c)).lower()


def _search_text(country) -> str:
    parts = [country.name, country.code, *ALIASES.get(country.code, ())]
    return " ".join(_fold(p) for p in parts)


def catalog(*, tracked=(), apple_source=False) -> dict:
    """The whole picker payload.

    ``tracked`` are the storefronts this install already has results for,
    which only the caller can know. ``apple_source`` says whether the user is
    on the Apple popularity source, which decides whether the Apple marker
    means anything on this screen.
    """
    tracked_codes = countries.clean(tracked)
    entries = []
    for country in countries.COUNTRIES.values():
        entries.append({
            "code": country.code,
            "name": country.name,
            "flag": country.flag,
            "region": country.region,
            "search": _search_text(country),
            "has_language": country.has_app_store_language,
            # Only a real signal under the Apple source, and only when the
            # coverage probe has actually run: unknown must never render as
            # an absence. See the module note and countries.apple_reports_data.
            "apple_ads": True if not apple_source else (
                countries.apple_reports_data(country.code) is not False
            ),
        })
    return {
        "countries": entries,
        "regions": list(countries.REGIONS),
        "presets": presets(tracked=tracked_codes),
        "default": ["us"],
        "apple_source": apple_source,
    }


def presets(*, tracked=()) -> list[dict]:
    """Named selections, each with its codes already resolved.

    Resolving here rather than in the browser is what stops a preset drifting
    from the table: there is no second definition of "Europe" anywhere.
    """
    out = []
    for key, label in countries.PRESETS:
        codes = countries.preset_codes(key, tracked=tracked)
        if not codes:
            continue  # "Countries I track" before anything is tracked
        out.append({"id": key, "label": label, "codes": codes})
    return out


def tracked_countries() -> list[str]:
    """Storefronts this install already has results for, most used first."""
    from django.db.models import Count

    from aso.models import SearchResult

    rows = (
        SearchResult.objects.values("country")
        .annotate(n=Count("id"))
        .order_by("-n")
    )
    return countries.clean(row["country"] for row in rows)
