"""Country → App Store metadata locale mapping.

Used by the AI tabs (Simulator, Researcher, Competitor) to let the user pick
which language the LLM should write the suggested metadata in. The country
list mirrors COUNTRY_CHOICES in aso/forms.py.
"""

from __future__ import annotations

# Apple App Store metadata locales available per storefront, primary first.
# Display labels are shown in the language dropdown.
COUNTRY_LOCALES: dict[str, list[tuple[str, str]]] = {
    "us": [("en-US", "English (US)")],
    "gb": [("en-GB", "English (UK)")],
    "ca": [("en-CA", "English (Canada)"), ("fr-CA", "French (Canada)")],
    "au": [("en-AU", "English (Australia)")],
    "de": [("de-DE", "German")],
    "fr": [("fr-FR", "French")],
    "jp": [("ja", "Japanese")],
    "kr": [("ko", "Korean")],
    "cn": [("zh-Hans", "Chinese (Simplified)")],
    "br": [("pt-BR", "Portuguese (Brazil)")],
    "in": [("en-IN", "English (India)"), ("hi", "Hindi")],
    "mx": [("es-MX", "Spanish (Mexico)")],
    "es": [("es-ES", "Spanish (Spain)")],
    "it": [("it", "Italian")],
    "nl": [("nl-NL", "Dutch")],
    "se": [("sv", "Swedish")],
    "no": [("no", "Norwegian")],
    "dk": [("da", "Danish")],
    "fi": [("fi", "Finnish")],
    "pt": [("pt-PT", "Portuguese (Portugal)")],
    "ru": [("ru", "Russian")],
    "tr": [("tr", "Turkish")],
    "sa": [("ar-SA", "Arabic")],
    "ae": [("ar-SA", "Arabic")],
    "sg": [("en-SG", "English (Singapore)"), ("zh-Hans", "Chinese (Simplified)")],
    "th": [("th", "Thai")],
    "id": [("id", "Indonesian")],
    "ph": [("en-PH", "English (Philippines)")],
    "vn": [("vi", "Vietnamese")],
    "tw": [("zh-Hant", "Chinese (Traditional)")],
}

ENGLISH_FALLBACK: tuple[str, str] = ("en-US", "English (US)")

# Pretty country names for prompts (e.g. "Mexico" instead of "mx").
COUNTRY_DISPLAY_NAMES: dict[str, str] = {
    "us": "United States", "gb": "United Kingdom", "ca": "Canada",
    "au": "Australia", "de": "Germany", "fr": "France", "jp": "Japan",
    "kr": "South Korea", "cn": "China", "br": "Brazil", "in": "India",
    "mx": "Mexico", "es": "Spain", "it": "Italy", "nl": "Netherlands",
    "se": "Sweden", "no": "Norway", "dk": "Denmark", "fi": "Finland",
    "pt": "Portugal", "ru": "Russia", "tr": "Turkey", "sa": "Saudi Arabia",
    "ae": "UAE", "sg": "Singapore", "th": "Thailand", "id": "Indonesia",
    "ph": "Philippines", "vn": "Vietnam", "tw": "Taiwan",
}


def locales_for(country: str) -> list[tuple[str, str]]:
    """Return the list of (locale_code, display_label) options for a country.
    Always appends English (US) as a fallback if no English variant is already
    listed — covers users targeting a foreign market with an English-only app.
    """
    code = (country or "us").lower()
    locales = list(COUNTRY_LOCALES.get(code, [ENGLISH_FALLBACK]))
    if not any(loc[0].startswith("en") for loc in locales):
        locales.append(ENGLISH_FALLBACK)
    return locales


def primary_locale(country: str) -> str:
    """Return the default locale code for a country (the first entry)."""
    return locales_for(country)[0][0]


def label_for(locale_code: str) -> str:
    """Return the human-readable label for a locale, e.g. 'fr-CA' → 'French (Canada)'.
    Falls back to the locale code itself if not found in any country's list.
    """
    for entries in COUNTRY_LOCALES.values():
        for code, label in entries:
            if code == locale_code:
                return label
    if locale_code == ENGLISH_FALLBACK[0]:
        return ENGLISH_FALLBACK[1]
    return locale_code


def country_name(country: str) -> str:
    """Return the display name for a country code, e.g. 'mx' → 'Mexico'."""
    return COUNTRY_DISPLAY_NAMES.get((country or "us").lower(), country.upper())


# ISO 639-1 → friendly label, used to humanise lingua's detection output
# (e.g., when warning the user that their source appears to be in 'en' / 'fr').
ISO_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English", "fr": "French", "de": "German", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "sv": "Swedish",
    "no": "Norwegian", "da": "Danish", "fi": "Finnish", "ru": "Russian",
    "tr": "Turkish", "ar": "Arabic", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese", "hi": "Hindi", "th": "Thai", "id": "Indonesian",
    "vi": "Vietnamese",
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
    for country, entries in COUNTRY_LOCALES.items():
        merged = list(entries)
        if not any(loc[0].startswith("en") for loc in merged):
            merged.append(ENGLISH_FALLBACK)
        out[country] = [[code, label] for code, label in merged]
    return out


def is_valid_locale_for_country(country: str, locale_code: str) -> bool:
    """Validate a locale belongs to the country's available list (or is English fallback)."""
    available = {code for code, _ in locales_for(country)}
    return locale_code in available
