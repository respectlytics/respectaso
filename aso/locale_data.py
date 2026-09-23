"""Country to App Store metadata locale mapping.

Used by the AI tabs (Simulator, Researcher, Competitor) to let the user pick
which language the LLM should write the suggested metadata in.

The country list is NOT kept here any more: it lives in aso/countries.py, and
every locale below comes from that registry. Apple offers App Store listing
languages for roughly 40 languages, not for 175 storefronts, so a storefront
with an empty ``locales`` tuple genuinely has none. Those countries fall back
to English here, and the UI says so out loud instead of implying a Bulgarian
or Serbian listing is possible.
"""

from __future__ import annotations

from aso import countries

# Every App Store metadata locale the registry is allowed to reference, and
# the label the language dropdown shows. A test asserts that no storefront
# names a locale that is missing from this table, which is what stops an
# invented locale reaching a user.
LOCALE_LABELS: dict[str, str] = {
    "ar-SA": "Arabic",
    "ca": "Catalan",
    "cs": "Czech",
    "da": "Danish",
    "de-DE": "German",
    "el": "Greek",
    "en-AU": "English (Australia)",
    "en-CA": "English (Canada)",
    "en-GB": "English (UK)",
    "en-IN": "English (India)",
    "en-PH": "English (Philippines)",
    "en-SG": "English (Singapore)",
    "en-US": "English (US)",
    "es-ES": "Spanish (Spain)",
    "es-MX": "Spanish (Mexico)",
    "fi": "Finnish",
    "fr-CA": "French (Canada)",
    "fr-FR": "French",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "id": "Indonesian",
    "it": "Italian",
    "ja": "Japanese",
    "ko": "Korean",
    "ms": "Malay",
    "nl-NL": "Dutch",
    "no": "Norwegian",
    "pl": "Polish",
    "pt-BR": "Portuguese (Brazil)",
    "pt-PT": "Portuguese (Portugal)",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sv": "Swedish",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "vi": "Vietnamese",
    "zh-Hans": "Chinese (Simplified)",
    "zh-Hant": "Chinese (Traditional)",
}

ENGLISH_FALLBACK: tuple[str, str] = ("en-US", "English (US)")

# Derived from the registry, primary locale first. Kept as a module-level name
# because several call sites and tests read it directly.
COUNTRY_LOCALES: dict[str, list[tuple[str, str]]] = {
    country.code: [(code, LOCALE_LABELS[code]) for code in country.locales]
    for country in countries.COUNTRIES.values()
}


def locales_for(country: str) -> list[tuple[str, str]]:
    """Return the list of (locale_code, display_label) options for a country.

    Always appends English (US) as a fallback if no English variant is already
    listed. That covers two cases: a user targeting a foreign market with an
    English-only app, and a storefront where Apple offers no App Store
    listing language at all, where English is the only honest answer.
    """
    code = (country or "us").lower()
    locales = list(COUNTRY_LOCALES.get(code, []))
    if not any(loc[0].startswith("en") for loc in locales):
        locales.append(ENGLISH_FALLBACK)
    return locales


def primary_locale(country: str) -> str:
    """Return the default locale code for a country (the first entry)."""
    return locales_for(country)[0][0]


def label_for(locale_code: str) -> str:
    """Return the human-readable label for a locale, e.g. 'fr-CA' to
    'French (Canada)'. Falls back to the locale code itself."""
    return LOCALE_LABELS.get(locale_code, locale_code)


def country_name(country: str) -> str:
    """Return the display name for a country code, e.g. 'mx' to 'Mexico'."""
    return countries.name(country or "us")


def has_app_store_language(country: str) -> bool:
    """False when Apple offers no App Store listing language for a storefront.

    A listing there appears in the app's fallback language, so the AI tabs say
    so rather than offering a language Apple will never show.
    """
    entry = countries.get(country)
    return bool(entry and entry.locales)


def countries_without_app_store_language() -> list[str]:
    """Storefront codes where Apple offers no App Store listing language."""
    return countries.without_app_store_language()


# ISO 639-1 to friendly label, used to humanise lingua's detection output
# (e.g., when warning the user that their source appears to be in 'en' / 'fr').
ISO_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English", "fr": "French", "de": "German", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "sv": "Swedish",
    "no": "Norwegian", "da": "Danish", "fi": "Finnish", "ru": "Russian",
    "tr": "Turkish", "ar": "Arabic", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "hi": "Hindi", "th": "Thai", "id": "Indonesian",
    "vi": "Vietnamese", "ca": "Catalan", "cs": "Czech", "el": "Greek",
    "he": "Hebrew", "hr": "Croatian", "hu": "Hungarian", "ms": "Malay",
    "pl": "Polish", "ro": "Romanian", "sk": "Slovak", "uk": "Ukrainian",
}


def label_for_iso(iso_code: str) -> str:
    """Human label for an ISO 639-1 code (lingua's detection output).
    Falls back to the code itself for unknown codes."""
    if not iso_code:
        return ""
    return ISO_LANGUAGE_NAMES.get(iso_code.lower(), iso_code)


def is_english_locale(locale_code: str) -> bool:
    """True if the locale's primary language is English."""
    return (locale_code or "").lower().startswith("en")


def locale_country_locales_for_template() -> dict[str, list[list[str]]]:
    """JSON-serialisable form of COUNTRY_LOCALES for embedding in templates.
    Tuples become lists; outer dict keys are country codes; values are lists
    of [code, label] pairs.
    """
    out: dict[str, list[list[str]]] = {}
    for country in COUNTRY_LOCALES:
        out[country] = [[code, label] for code, label in locales_for(country)]
    return out


def is_valid_locale_for_country(country: str, locale_code: str) -> bool:
    """Validate a locale belongs to the country's available list (or is English fallback)."""
    available = {code for code, _ in locales_for(country)}
    return locale_code in available
