"""Every App Store storefront RespectASO supports, and what is true in each one.

ONE source of truth. Nothing else in the codebase may define a country list:
the guard test in aso/tests/test_no_hardcoded_countries.py fails the build if a
second one appears. It used to exist six times (aso/forms.py, aso/locale_data.py
twice, aso/templatetags/aso_tags.py, and two hardcoded JavaScript copies inside
dashboard.html and opportunity.html) and those copies had already drifted apart.

Each row carries the four facts the product needs:

  name     what the storefront is called in the UI
  region   the grouping used by every country picker
  locales  the App Store metadata locales Apple offers for that storefront,
           primary first. An EMPTY tuple is meaningful and common: Apple has no
           App Store listing language for that country, so a listing there
           appears in the app's fallback language. The UI states this rather
           than hiding it. Every locale must be a key of LOCALE_LABELS in
           aso/locale_data.py, which a test enforces.
  market   search volume relative to the US App Store, consumed only by
           DownloadEstimator. "measured" values were fitted against observed
           App Store data and must never be recomputed by a script. "derived"
           values come from population times regional iOS share, calibrated
           against the measured markets at or below 0.08. The derivation and
           its inputs live in scripts/build_market_sizes.py; the full reasoning
           is in docs/development/COUNTRY_COVERAGE_PLAN.md.

Apple Ads coverage is deliberately NOT a column. Whether Apple reports search
popularity for a storefront is a fact about the owner's Apple Ads account and
the week being served, so live coverage is read from the synced data
(aso.apple_ads.storage active_weeks). APPLE_ADS_STOREFRONTS below is the dated
probe result used before any sync exists, so the UI can say what to expect
before the user spends time on a country.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Country:
    """One App Store storefront."""

    code: str
    name: str
    region: str
    locales: tuple[str, ...]
    market: float
    market_source: str  # "measured" | "derived"

    @property
    def flag(self) -> str:
        """Regional indicator pair, computed from the ISO code. No table."""
        return "".join(chr(0x1F1E6 + ord(c.upper()) - ord("A")) for c in self.code)

    @property
    def label(self) -> str:
        """Flag plus name, the shape the old COUNTRY_CHOICES used."""
        return f"{self.flag} {self.name}"

    @property
    def has_app_store_language(self) -> bool:
        """False when Apple offers no App Store listing language here."""
        return bool(self.locales)

    @property
    def primary_locale(self) -> str:
        """The storefront's own App Store language, or English as a fallback."""
        return self.locales[0] if self.locales else "en-US"


# Picker order. Not alphabetical by accident: it runs west to east, which is
# how the regions read on a map and how the presets are grouped.
REGIONS = ("Americas", "Europe", "Middle East", "Africa", "Asia", "Oceania")


C = Country

# Sorted by REGIONS order, then by name. Nothing may depend on this order for
# prominence: prominence comes from the presets and the default selection.
_TABLE: tuple[Country, ...] = (
    C("ai", "Anguilla", "Americas", ("en-GB",), 0.0001, "derived"),
    C("ag", "Antigua & Barbuda", "Americas", ("en-GB",), 0.0002, "derived"),
    C("ar", "Argentina", "Americas", ("es-MX",), 0.03, "measured"),
    C("bs", "Bahamas", "Americas", ("en-GB",), 0.001, "derived"),
    C("bb", "Barbados", "Americas", ("en-GB",), 0.0006, "derived"),
    C("bz", "Belize", "Americas", ("en-GB",), 0.0006, "derived"),
    C("bm", "Bermuda", "Americas", ("en-GB",), 0.0002, "derived"),
    C("bo", "Bolivia", "Americas", ("es-MX",), 0.0083, "derived"),
    C("br", "Brazil", "Americas", ("pt-BR",), 0.18, "measured"),
    C("vg", "British Virgin Islands", "Americas", ("en-GB",), 0.0001, "derived"),
    C("ca", "Canada", "Americas", ("en-CA", "fr-CA"), 0.15, "measured"),
    C("ky", "Cayman Islands", "Americas", ("en-GB",), 0.0002, "derived"),
    C("cl", "Chile", "Americas", ("es-MX",), 0.03, "measured"),
    C("co", "Colombia", "Americas", ("es-MX",), 0.03, "measured"),
    C("cr", "Costa Rica", "Americas", ("es-MX",), 0.0089, "derived"),
    C("dm", "Dominica", "Americas", ("en-GB",), 0.0001, "derived"),
    C("do", "Dominican Republic", "Americas", ("es-MX",), 0.0139, "derived"),
    C("ec", "Ecuador", "Americas", ("es-MX",), 0.0172, "derived"),
    C("sv", "El Salvador", "Americas", ("es-MX",), 0.0061, "derived"),
    C("gd", "Grenada", "Americas", ("en-GB",), 0.0002, "derived"),
    C("gt", "Guatemala", "Americas", ("es-MX",), 0.0168, "derived"),
    C("gy", "Guyana", "Americas", ("en-GB",), 0.0008, "derived"),
    C("hn", "Honduras", "Americas", ("es-MX",), 0.0087, "derived"),
    C("jm", "Jamaica", "Americas", ("en-GB",), 0.0038, "derived"),
    C("mx", "Mexico", "Americas", ("es-MX",), 0.1, "measured"),
    C("ms", "Montserrat", "Americas", ("en-GB",), 0.0001, "derived"),
    C("ni", "Nicaragua", "Americas", ("es-MX",), 0.0046, "derived"),
    C("pa", "Panama", "Americas", ("es-MX",), 0.0077, "derived"),
    C("py", "Paraguay", "Americas", ("es-MX",), 0.0056, "derived"),
    C("pe", "Peru", "Americas", ("es-MX",), 0.0324, "derived"),
    C("kn", "St. Kitts & Nevis", "Americas", ("en-GB",), 0.0001, "derived"),
    C("lc", "St. Lucia", "Americas", ("en-GB",), 0.0003, "derived"),
    C("vc", "St. Vincent", "Americas", ("en-GB",), 0.0002, "derived"),
    C("sr", "Suriname", "Americas", ("nl-NL",), 0.0006, "derived"),
    C("tt", "Trinidad & Tobago", "Americas", ("en-GB",), 0.0026, "derived"),
    C("tc", "Turks & Caicos", "Americas", ("en-GB",), 0.0001, "derived"),
    C("us", "United States", "Americas", ("en-US",), 1.0, "measured"),
    C("uy", "Uruguay", "Americas", ("es-MX",), 0.0046, "derived"),
    C("ve", "Venezuela", "Americas", ("es-MX",), 0.0193, "derived"),
    C("al", "Albania", "Europe", (), 0.0023, "derived"),
    C("am", "Armenia", "Europe", (), 0.0041, "derived"),
    C("at", "Austria", "Europe", ("de-DE",), 0.04, "measured"),
    C("az", "Azerbaijan", "Europe", (), 0.0104, "derived"),
    C("by", "Belarus", "Europe", ("ru",), 0.0075, "derived"),
    C("be", "Belgium", "Europe", ("nl-NL", "fr-FR"), 0.04, "measured"),
    C("ba", "Bosnia & Herzegovina", "Europe", (), 0.0033, "derived"),
    C("bg", "Bulgaria", "Europe", (), 0.0087, "derived"),
    C("hr", "Croatia", "Europe", ("hr",), 0.0066, "derived"),
    C("cy", "Cyprus", "Europe", ("el",), 0.0027, "derived"),
    C("cz", "Czechia", "Europe", ("cs",), 0.0163, "derived"),
    C("dk", "Denmark", "Europe", ("da",), 0.04, "measured"),
    C("ee", "Estonia", "Europe", (), 0.0029, "derived"),
    C("fi", "Finland", "Europe", ("fi",), 0.03, "measured"),
    C("fr", "France", "Europe", ("fr-FR",), 0.22, "measured"),
    C("ge", "Georgia", "Europe", (), 0.0038, "derived"),
    C("de", "Germany", "Europe", ("de-DE",), 0.25, "measured"),
    C("gr", "Greece", "Europe", ("el",), 0.0177, "derived"),
    C("hu", "Hungary", "Europe", ("hu",), 0.0131, "derived"),
    C("is", "Iceland", "Europe", (), 0.0015, "derived"),
    C("ie", "Ireland", "Europe", ("en-GB",), 0.03, "measured"),
    C("it", "Italy", "Europe", ("it",), 0.12, "measured"),
    C("xk", "Kosovo", "Europe", (), 0.0015, "derived"),
    C("lv", "Latvia", "Europe", (), 0.0032, "derived"),
    C("lt", "Lithuania", "Europe", (), 0.0049, "derived"),
    C("lu", "Luxembourg", "Europe", ("fr-FR", "de-DE"), 0.0022, "derived"),
    C("mt", "Malta", "Europe", ("en-GB",), 0.0017, "derived"),
    C("md", "Moldova", "Europe", ("ro", "ru"), 0.002, "derived"),
    C("me", "Montenegro", "Europe", (), 0.0008, "derived"),
    C("nl", "Netherlands", "Europe", ("nl-NL",), 0.07, "measured"),
    C("mk", "North Macedonia", "Europe", (), 0.0018, "derived"),
    C("no", "Norway", "Europe", ("no",), 0.04, "measured"),
    C("pl", "Poland", "Europe", ("pl",), 0.05, "measured"),
    C("pt", "Portugal", "Europe", ("pt-PT",), 0.03, "measured"),
    C("ro", "Romania", "Europe", ("ro",), 0.026, "derived"),
    C("ru", "Russia", "Europe", ("ru",), 0.12, "measured"),
    C("rs", "Serbia", "Europe", (), 0.0081, "derived"),
    C("sk", "Slovakia", "Europe", ("sk",), 0.0081, "derived"),
    C("si", "Slovenia", "Europe", (), 0.0036, "derived"),
    C("es", "Spain", "Europe", ("es-ES",), 0.1, "measured"),
    C("se", "Sweden", "Europe", ("sv",), 0.06, "measured"),
    C("ch", "Switzerland", "Europe", ("de-DE", "fr-FR", "it"), 0.06, "measured"),
    C("tr", "Türkiye", "Europe", ("tr",), 0.05, "measured"),
    C("ua", "Ukraine", "Europe", ("uk",), 0.0387, "derived"),
    C("gb", "United Kingdom", "Europe", ("en-GB",), 0.3, "measured"),
    C("bh", "Bahrain", "Middle East", ("ar-SA",), 0.0036, "derived"),
    C("iq", "Iraq", "Middle East", ("ar-SA",), 0.0368, "derived"),
    C("il", "Israel", "Middle East", ("he",), 0.04, "measured"),
    C("jo", "Jordan", "Middle East", ("ar-SA",), 0.0139, "derived"),
    C("kw", "Kuwait", "Middle East", ("ar-SA",), 0.0117, "derived"),
    C("lb", "Lebanon", "Middle East", ("ar-SA", "fr-FR"), 0.0074, "derived"),
    C("om", "Oman", "Middle East", ("ar-SA",), 0.0078, "derived"),
    C("qa", "Qatar", "Middle East", ("ar-SA",), 0.0074, "derived"),
    C("sa", "Saudi Arabia", "Middle East", ("ar-SA",), 0.04, "measured"),
    C("ae", "UAE", "Middle East", ("ar-SA",), 0.04, "measured"),
    C("ye", "Yemen", "Middle East", ("ar-SA",), 0.0117, "derived"),
    C("dz", "Algeria", "Africa", ("ar-SA", "fr-FR"), 0.0249, "derived"),
    C("ao", "Angola", "Africa", ("pt-PT",), 0.0147, "derived"),
    C("bj", "Benin", "Africa", ("fr-FR",), 0.0047, "derived"),
    C("bw", "Botswana", "Africa", ("en-GB",), 0.0022, "derived"),
    C("bf", "Burkina Faso", "Africa", ("fr-FR",), 0.0063, "derived"),
    C("cm", "Cameroon", "Africa", ("fr-FR",), 0.0097, "derived"),
    C("cv", "Cape Verde", "Africa", ("pt-PT",), 0.0005, "derived"),
    C("td", "Chad", "Africa", ("fr-FR",), 0.0037, "derived"),
    C("cg", "Congo", "Africa", ("fr-FR",), 0.0021, "derived"),
    C("cd", "Congo (DRC)", "Africa", ("fr-FR",), 0.0208, "derived"),
    C("ci", "Côte d'Ivoire", "Africa", ("fr-FR",), 0.0118, "derived"),
    C("eg", "Egypt", "Africa", ("ar-SA",), 0.03, "measured"),
    C("sz", "Eswatini", "Africa", ("en-GB",), 0.0007, "derived"),
    C("ga", "Gabon", "Africa", ("fr-FR",), 0.0016, "derived"),
    C("gm", "Gambia", "Africa", ("en-GB",), 0.0011, "derived"),
    C("gh", "Ghana", "Africa", ("en-GB",), 0.02, "measured"),
    C("gw", "Guinea-Bissau", "Africa", ("pt-PT",), 0.0006, "derived"),
    C("ke", "Kenya", "Africa", ("en-GB",), 0.02, "measured"),
    C("lr", "Liberia", "Africa", ("en-GB",), 0.0018, "derived"),
    C("ly", "Libya", "Africa", ("ar-SA",), 0.0047, "derived"),
    C("mg", "Madagascar", "Africa", ("fr-FR",), 0.0082, "derived"),
    C("mw", "Malawi", "Africa", ("en-GB",), 0.0057, "derived"),
    C("ml", "Mali", "Africa", ("fr-FR",), 0.0063, "derived"),
    C("mr", "Mauritania", "Africa", ("ar-SA", "fr-FR"), 0.002, "derived"),
    C("mu", "Mauritius", "Africa", ("en-GB", "fr-FR"), 0.0018, "derived"),
    C("ma", "Morocco", "Africa", ("ar-SA", "fr-FR"), 0.0307, "derived"),
    C("mz", "Mozambique", "Africa", ("pt-PT",), 0.0092, "derived"),
    C("na", "Namibia", "Africa", ("en-GB",), 0.0021, "derived"),
    C("ne", "Niger", "Africa", ("fr-FR",), 0.0055, "derived"),
    C("ng", "Nigeria", "Africa", ("en-GB",), 0.03, "measured"),
    C("rw", "Rwanda", "Africa", ("en-GB", "fr-FR"), 0.0048, "derived"),
    C("sn", "Senegal", "Africa", ("fr-FR",), 0.0096, "derived"),
    C("sc", "Seychelles", "Africa", ("en-GB", "fr-FR"), 0.0002, "derived"),
    C("sl", "Sierra Leone", "Africa", ("en-GB",), 0.0029, "derived"),
    C("za", "South Africa", "Africa", ("en-GB",), 0.03, "measured"),
    C("st", "São Tomé & Príncipe", "Africa", ("pt-PT",), 0.0001, "derived"),
    C("tz", "Tanzania", "Africa", ("en-GB",), 0.02, "measured"),
    C("tn", "Tunisia", "Africa", ("ar-SA", "fr-FR"), 0.0102, "derived"),
    C("ug", "Uganda", "Africa", ("en-GB",), 0.02, "measured"),
    C("zm", "Zambia", "Africa", ("en-GB",), 0.0084, "derived"),
    C("zw", "Zimbabwe", "Africa", ("en-GB",), 0.0091, "derived"),
    C("af", "Afghanistan", "Asia", (), 0.0086, "derived"),
    C("bt", "Bhutan", "Asia", ("en-GB",), 0.0005, "derived"),
    C("bn", "Brunei", "Asia", ("ms",), 0.0011, "derived"),
    C("kh", "Cambodia", "Asia", (), 0.0139, "derived"),
    C("cn", "China", "Asia", ("zh-Hans",), 0.45, "measured"),
    C("hk", "Hong Kong", "Asia", ("zh-Hant", "en-GB"), 0.0281, "derived"),
    C("in", "India", "Asia", ("en-IN", "hi"), 0.15, "measured"),
    C("id", "Indonesia", "Asia", ("id",), 0.05, "measured"),
    C("jp", "Japan", "Asia", ("ja",), 0.35, "measured"),
    C("kz", "Kazakhstan", "Asia", ("ru",), 0.0272, "derived"),
    C("kg", "Kyrgyzstan", "Asia", ("ru",), 0.0048, "derived"),
    C("la", "Laos", "Asia", (), 0.0041, "derived"),
    C("mo", "Macao", "Asia", ("zh-Hant",), 0.0023, "derived"),
    C("my", "Malaysia", "Asia", ("ms", "en-GB", "zh-Hans"), 0.04, "measured"),
    C("mv", "Maldives", "Asia", ("en-GB",), 0.0011, "derived"),
    C("mn", "Mongolia", "Asia", (), 0.0035, "derived"),
    C("mm", "Myanmar", "Asia", (), 0.0294, "derived"),
    C("np", "Nepal", "Asia", ("en-GB",), 0.0123, "derived"),
    C("pk", "Pakistan", "Asia", ("en-GB",), 0.02, "measured"),
    C("ph", "Philippines", "Asia", ("en-PH",), 0.04, "measured"),
    C("sg", "Singapore", "Asia", ("en-SG", "zh-Hans"), 0.04, "measured"),
    C("kr", "South Korea", "Asia", ("ko",), 0.2, "measured"),
    C("lk", "Sri Lanka", "Asia", ("en-GB",), 0.015, "derived"),
    C("tw", "Taiwan", "Asia", ("zh-Hant",), 0.08, "measured"),
    C("tj", "Tajikistan", "Asia", ("ru",), 0.0041, "derived"),
    C("th", "Thailand", "Asia", ("th",), 0.05, "measured"),
    C("tm", "Turkmenistan", "Asia", ("ru",), 0.0022, "derived"),
    C("uz", "Uzbekistan", "Asia", ("ru",), 0.0243, "derived"),
    C("vn", "Vietnam", "Asia", ("vi",), 0.1349, "derived"),
    C("au", "Australia", "Oceania", ("en-AU",), 0.12, "measured"),
    C("fj", "Fiji", "Oceania", ("en-GB",), 0.0016, "derived"),
    C("fm", "Micronesia", "Oceania", ("en-US",), 0.0001, "derived"),
    C("nr", "Nauru", "Oceania", ("en-GB",), 0.0001, "derived"),
    C("nz", "New Zealand", "Oceania", ("en-AU",), 0.03, "measured"),
    C("pw", "Palau", "Oceania", ("en-US",), 0.0001, "derived"),
    C("pg", "Papua New Guinea", "Oceania", ("en-GB",), 0.0056, "derived"),
    C("sb", "Solomon Islands", "Oceania", ("en-GB",), 0.0004, "derived"),
    C("to", "Tonga", "Oceania", ("en-GB",), 0.0001, "derived"),
    C("vu", "Vanuatu", "Oceania", ("en-GB", "fr-FR"), 0.0002, "derived"),
)


COUNTRIES: dict[str, Country] = {c.code: c for c in _TABLE}
CODES = frozenset(COUNTRIES)

# Storefronts where Apple Ads operates and therefore publishes search-term
# popularity. Filled by `manage.py probe_storefronts --apple-ads`, which needs
# a working Apple Ads connection.
#
# EMPTY MEANS UNKNOWN, NOT UNCOVERED. Nobody has asked Apple yet on this
# install, and not knowing is not the same as knowing there is no data, so the
# UI stays silent rather than marking all 175 storefronts as uncovered. Once
# the probe has run, the storefronts missing from this set are positively
# known to have no Apple data and the picker says so.
APPLE_ADS_STOREFRONTS: frozenset[str] = frozenset()


def get(code: str) -> Country | None:
    return COUNTRIES.get((code or "").lower())


def name(code: str) -> str:
    """Display name for a code, falling back to the uppercased code."""
    country = get(code)
    return country.name if country else (code or "").upper()


def flag(code: str) -> str:
    """Flag emoji for a code, empty for anything that is not two letters."""
    country = get(code)
    if country:
        return country.flag
    code = (code or "").strip()
    if len(code) != 2 or not code.isalpha():
        return ""
    return "".join(chr(0x1F1E6 + ord(c.upper()) - ord("A")) for c in code)


def is_valid(code: str) -> bool:
    return (code or "").lower() in CODES


def clean(codes) -> list[str]:
    """Lowercase, drop unknown codes, keep the caller's order, de-duplicate."""
    seen: set[str] = set()
    out: list[str] = []
    for raw in codes or ():
        code = (raw or "").strip().lower()
        if code in CODES and code not in seen:
            seen.add(code)
            out.append(code)
    return out


def choices() -> list[tuple[str, str]]:
    """The shape aso.forms.COUNTRY_CHOICES has always had: (code, "flag Name")."""
    return [(c.code, c.label) for c in _TABLE]


def by_region() -> dict[str, list[Country]]:
    return {region: [c for c in _TABLE if c.region == region] for region in REGIONS}


def in_region(region: str) -> list[str]:
    return [c.code for c in _TABLE if c.region == region]


def top_markets(n: int = 10) -> list[str]:
    """The n largest storefronts by market size. Computed, never typed."""
    return [c.code for c in sorted(_TABLE, key=lambda c: -c.market)[:n]]


def smallest_market() -> float:
    """The floor used for a storefront the registry does not know."""
    return min(c.market for c in _TABLE)


def without_app_store_language() -> list[str]:
    """Storefronts where Apple offers no App Store listing language."""
    return [c.code for c in _TABLE if not c.locales]


def apple_reports_data(code: str) -> bool | None:
    """Whether Apple Ads publishes search popularity for this storefront.

    None means nobody has asked Apple yet on this install. Callers must treat
    None as "say nothing", never as "no data": claiming an absence we have not
    observed would be the same mistake as inventing a number.
    """
    if not APPLE_ADS_STOREFRONTS:
        return None
    return (code or "").lower() in APPLE_ADS_STOREFRONTS


# Named selections offered by every country picker. Each one resolves to real
# codes at render time, so a preset can never drift from the table above.
# "tracked" is resolved from the database by aso.country_picker.
PRESETS: tuple[tuple[str, str], ...] = (
    ("tracked", "Countries I track"),
    ("top5", "Top 5 markets"),
    ("top10", "Top 10 markets"),
    ("apple", "Where Apple reports data"),
    ("americas", "Americas"),
    ("europe", "Europe"),
    ("middle-east", "Middle East"),
    ("africa", "Africa"),
    ("asia", "Asia"),
    ("oceania", "Oceania"),
    ("all", "Everywhere"),
)

_REGION_PRESETS = {
    "americas": "Americas",
    "europe": "Europe",
    "middle-east": "Middle East",
    "africa": "Africa",
    "asia": "Asia",
    "oceania": "Oceania",
}


def preset_codes(key: str, *, tracked=()) -> list[str]:
    """Resolve a preset key to storefront codes.

    ``tracked`` is supplied by the caller because it comes from the database.
    """
    if key == "all":
        return [c.code for c in _TABLE]
    if key == "top10":
        return top_markets(10)
    if key == "top5":
        return top_markets(5)
    if key == "apple":
        return [c.code for c in _TABLE if c.code in APPLE_ADS_STOREFRONTS]
    if key == "tracked":
        return clean(tracked)
    region = _REGION_PRESETS.get(key)
    if region:
        return in_region(region)
    return []


# Storefront probe log. `manage.py probe_storefronts` asks Apple for real
# search results in every candidate code, so the list above is observed
# rather than assumed.
# Probed 2026-09-22 against the live iTunes Search API, then re-checked by
# hand with "game", "app" and "whatsapp". 175 storefronts answered, which
# matches Apple's own "more than 175 countries and regions".
#
# Eleven candidates were removed because Apple serves no App Store search
# there at all, so a keyword result would have been an empty promise:
#
#   Andorra, Liechtenstein, Monaco  served by a neighbouring storefront
#   Bangladesh, Ethiopia, Guinea, Palestinian Territories, Samoa
#                                   HTTP 200 with zero results for every term
#   Haiti, Kiribati, Togo           HTTP 400, not a storefront
#
# Re-run `manage.py probe_storefronts` when Apple opens a new storefront, and
# record the change here.
