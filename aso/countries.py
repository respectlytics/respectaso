"""Every App Store storefront RespectASO supports, and what is true in each one.

ONE source of truth. Nothing else in the codebase may define a country list:
the guard test in aso/tests/test_no_hardcoded_countries.py fails the build if a
second one appears. It used to exist six times (aso/forms.py, aso/locale_data.py
twice, aso/templatetags/aso_tags.py, and two hardcoded JavaScript copies inside
dashboard.html and opportunity.html) and those copies had already drifted apart.

Each row carries these facts the product needs:

  name     what the storefront is called in the UI
  region   the grouping used by every country picker
  locales  the App Store metadata locales Apple offers for that storefront,
           primary first. An EMPTY tuple is meaningful and common: Apple has no
           App Store listing language for that country, so a listing there
           appears in the app's fallback language. The UI states this rather
           than hiding it. Every locale must be a key of LOCALE_LABELS in
           aso/locale_data.py, which a test enforces.
  alpha3   the three letter code App Store Connect names the storefront by
           (a review's territory), so its name and flag read the same as
           the two letter codes of the sales reports
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
    alpha3: str  # App Store Connect's territory code: ISO 3166-1 alpha-3, XKS for Kosovo

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
    C("ai", "Anguilla", "Americas", ("en-GB",), 0.0001, "derived", "AIA"),
    C("ag", "Antigua & Barbuda", "Americas", ("en-GB",), 0.0002, "derived", "ATG"),
    C("ar", "Argentina", "Americas", ("es-MX",), 0.03, "measured", "ARG"),
    C("bs", "Bahamas", "Americas", ("en-GB",), 0.001, "derived", "BHS"),
    C("bb", "Barbados", "Americas", ("en-GB",), 0.0006, "derived", "BRB"),
    C("bz", "Belize", "Americas", ("en-GB",), 0.0006, "derived", "BLZ"),
    C("bm", "Bermuda", "Americas", ("en-GB",), 0.0002, "derived", "BMU"),
    C("bo", "Bolivia", "Americas", ("es-MX",), 0.0083, "derived", "BOL"),
    C("br", "Brazil", "Americas", ("pt-BR",), 0.18, "measured", "BRA"),
    C("vg", "British Virgin Islands", "Americas", ("en-GB",), 0.0001, "derived", "VGB"),
    C("ca", "Canada", "Americas", ("en-CA", "fr-CA"), 0.15, "measured", "CAN"),
    C("ky", "Cayman Islands", "Americas", ("en-GB",), 0.0002, "derived", "CYM"),
    C("cl", "Chile", "Americas", ("es-MX",), 0.03, "measured", "CHL"),
    C("co", "Colombia", "Americas", ("es-MX",), 0.03, "measured", "COL"),
    C("cr", "Costa Rica", "Americas", ("es-MX",), 0.0089, "derived", "CRI"),
    C("dm", "Dominica", "Americas", ("en-GB",), 0.0001, "derived", "DMA"),
    C("do", "Dominican Republic", "Americas", ("es-MX",), 0.0139, "derived", "DOM"),
    C("ec", "Ecuador", "Americas", ("es-MX",), 0.0172, "derived", "ECU"),
    C("sv", "El Salvador", "Americas", ("es-MX",), 0.0061, "derived", "SLV"),
    C("gd", "Grenada", "Americas", ("en-GB",), 0.0002, "derived", "GRD"),
    C("gt", "Guatemala", "Americas", ("es-MX",), 0.0168, "derived", "GTM"),
    C("gy", "Guyana", "Americas", ("en-GB",), 0.0008, "derived", "GUY"),
    C("hn", "Honduras", "Americas", ("es-MX",), 0.0087, "derived", "HND"),
    C("jm", "Jamaica", "Americas", ("en-GB",), 0.0038, "derived", "JAM"),
    C("mx", "Mexico", "Americas", ("es-MX",), 0.1, "measured", "MEX"),
    C("ms", "Montserrat", "Americas", ("en-GB",), 0.0001, "derived", "MSR"),
    C("ni", "Nicaragua", "Americas", ("es-MX",), 0.0046, "derived", "NIC"),
    C("pa", "Panama", "Americas", ("es-MX",), 0.0077, "derived", "PAN"),
    C("py", "Paraguay", "Americas", ("es-MX",), 0.0056, "derived", "PRY"),
    C("pe", "Peru", "Americas", ("es-MX",), 0.0324, "derived", "PER"),
    C("kn", "St. Kitts & Nevis", "Americas", ("en-GB",), 0.0001, "derived", "KNA"),
    C("lc", "St. Lucia", "Americas", ("en-GB",), 0.0003, "derived", "LCA"),
    C("vc", "St. Vincent", "Americas", ("en-GB",), 0.0002, "derived", "VCT"),
    C("sr", "Suriname", "Americas", ("nl-NL",), 0.0006, "derived", "SUR"),
    C("tt", "Trinidad & Tobago", "Americas", ("en-GB",), 0.0026, "derived", "TTO"),
    C("tc", "Turks & Caicos", "Americas", ("en-GB",), 0.0001, "derived", "TCA"),
    C("us", "United States", "Americas", ("en-US",), 1.0, "measured", "USA"),
    C("uy", "Uruguay", "Americas", ("es-MX",), 0.0046, "derived", "URY"),
    C("ve", "Venezuela", "Americas", ("es-MX",), 0.0193, "derived", "VEN"),
    C("al", "Albania", "Europe", (), 0.0023, "derived", "ALB"),
    C("am", "Armenia", "Europe", (), 0.0041, "derived", "ARM"),
    C("at", "Austria", "Europe", ("de-DE",), 0.04, "measured", "AUT"),
    C("az", "Azerbaijan", "Europe", (), 0.0104, "derived", "AZE"),
    C("by", "Belarus", "Europe", ("ru",), 0.0075, "derived", "BLR"),
    C("be", "Belgium", "Europe", ("nl-NL", "fr-FR"), 0.04, "measured", "BEL"),
    C("ba", "Bosnia & Herzegovina", "Europe", (), 0.0033, "derived", "BIH"),
    C("bg", "Bulgaria", "Europe", (), 0.0087, "derived", "BGR"),
    C("hr", "Croatia", "Europe", ("hr",), 0.0066, "derived", "HRV"),
    C("cy", "Cyprus", "Europe", ("el",), 0.0027, "derived", "CYP"),
    C("cz", "Czechia", "Europe", ("cs",), 0.0163, "derived", "CZE"),
    C("dk", "Denmark", "Europe", ("da",), 0.04, "measured", "DNK"),
    C("ee", "Estonia", "Europe", (), 0.0029, "derived", "EST"),
    C("fi", "Finland", "Europe", ("fi",), 0.03, "measured", "FIN"),
    C("fr", "France", "Europe", ("fr-FR",), 0.22, "measured", "FRA"),
    C("ge", "Georgia", "Europe", (), 0.0038, "derived", "GEO"),
    C("de", "Germany", "Europe", ("de-DE",), 0.25, "measured", "DEU"),
    C("gr", "Greece", "Europe", ("el",), 0.0177, "derived", "GRC"),
    C("hu", "Hungary", "Europe", ("hu",), 0.0131, "derived", "HUN"),
    C("is", "Iceland", "Europe", (), 0.0015, "derived", "ISL"),
    C("ie", "Ireland", "Europe", ("en-GB",), 0.03, "measured", "IRL"),
    C("it", "Italy", "Europe", ("it",), 0.12, "measured", "ITA"),
    C("xk", "Kosovo", "Europe", (), 0.0015, "derived", "XKS"),
    C("lv", "Latvia", "Europe", (), 0.0032, "derived", "LVA"),
    C("lt", "Lithuania", "Europe", (), 0.0049, "derived", "LTU"),
    C("lu", "Luxembourg", "Europe", ("fr-FR", "de-DE"), 0.0022, "derived", "LUX"),
    C("mt", "Malta", "Europe", ("en-GB",), 0.0017, "derived", "MLT"),
    C("md", "Moldova", "Europe", ("ro", "ru"), 0.002, "derived", "MDA"),
    C("me", "Montenegro", "Europe", (), 0.0008, "derived", "MNE"),
    C("nl", "Netherlands", "Europe", ("nl-NL",), 0.07, "measured", "NLD"),
    C("mk", "North Macedonia", "Europe", (), 0.0018, "derived", "MKD"),
    C("no", "Norway", "Europe", ("no",), 0.04, "measured", "NOR"),
    C("pl", "Poland", "Europe", ("pl",), 0.05, "measured", "POL"),
    C("pt", "Portugal", "Europe", ("pt-PT",), 0.03, "measured", "PRT"),
    C("ro", "Romania", "Europe", ("ro",), 0.026, "derived", "ROU"),
    C("ru", "Russia", "Europe", ("ru",), 0.12, "measured", "RUS"),
    C("rs", "Serbia", "Europe", (), 0.0081, "derived", "SRB"),
    C("sk", "Slovakia", "Europe", ("sk",), 0.0081, "derived", "SVK"),
    C("si", "Slovenia", "Europe", ("en-GB", "sl-SI"), 0.0036, "derived", "SVN"),
    C("es", "Spain", "Europe", ("es-ES",), 0.1, "measured", "ESP"),
    C("se", "Sweden", "Europe", ("sv",), 0.06, "measured", "SWE"),
    C("ch", "Switzerland", "Europe", ("de-DE", "fr-FR", "it"), 0.06, "measured", "CHE"),
    C("tr", "Türkiye", "Europe", ("tr",), 0.05, "measured", "TUR"),
    C("ua", "Ukraine", "Europe", ("uk",), 0.0387, "derived", "UKR"),
    C("gb", "United Kingdom", "Europe", ("en-GB",), 0.3, "measured", "GBR"),
    C("bh", "Bahrain", "Middle East", ("ar-SA",), 0.0036, "derived", "BHR"),
    C("iq", "Iraq", "Middle East", ("ar-SA",), 0.0368, "derived", "IRQ"),
    C("il", "Israel", "Middle East", ("he",), 0.04, "measured", "ISR"),
    C("jo", "Jordan", "Middle East", ("ar-SA",), 0.0139, "derived", "JOR"),
    C("kw", "Kuwait", "Middle East", ("ar-SA",), 0.0117, "derived", "KWT"),
    C("lb", "Lebanon", "Middle East", ("ar-SA", "fr-FR"), 0.0074, "derived", "LBN"),
    C("om", "Oman", "Middle East", ("ar-SA",), 0.0078, "derived", "OMN"),
    C("qa", "Qatar", "Middle East", ("ar-SA",), 0.0074, "derived", "QAT"),
    C("sa", "Saudi Arabia", "Middle East", ("ar-SA",), 0.04, "measured", "SAU"),
    C("ae", "UAE", "Middle East", ("ar-SA",), 0.04, "measured", "ARE"),
    C("ye", "Yemen", "Middle East", ("ar-SA",), 0.0117, "derived", "YEM"),
    C("dz", "Algeria", "Africa", ("ar-SA", "fr-FR"), 0.0249, "derived", "DZA"),
    C("ao", "Angola", "Africa", ("pt-PT",), 0.0147, "derived", "AGO"),
    C("bj", "Benin", "Africa", ("fr-FR",), 0.0047, "derived", "BEN"),
    C("bw", "Botswana", "Africa", ("en-GB",), 0.0022, "derived", "BWA"),
    C("bf", "Burkina Faso", "Africa", ("fr-FR",), 0.0063, "derived", "BFA"),
    C("cm", "Cameroon", "Africa", ("fr-FR",), 0.0097, "derived", "CMR"),
    C("cv", "Cape Verde", "Africa", ("pt-PT",), 0.0005, "derived", "CPV"),
    C("td", "Chad", "Africa", ("fr-FR",), 0.0037, "derived", "TCD"),
    C("cg", "Congo", "Africa", ("fr-FR",), 0.0021, "derived", "COG"),
    C("cd", "Congo (DRC)", "Africa", ("fr-FR",), 0.0208, "derived", "COD"),
    C("ci", "Côte d'Ivoire", "Africa", ("fr-FR",), 0.0118, "derived", "CIV"),
    C("eg", "Egypt", "Africa", ("ar-SA",), 0.03, "measured", "EGY"),
    C("sz", "Eswatini", "Africa", ("en-GB",), 0.0007, "derived", "SWZ"),
    C("ga", "Gabon", "Africa", ("fr-FR",), 0.0016, "derived", "GAB"),
    C("gm", "Gambia", "Africa", ("en-GB",), 0.0011, "derived", "GMB"),
    C("gh", "Ghana", "Africa", ("en-GB",), 0.02, "measured", "GHA"),
    C("gw", "Guinea-Bissau", "Africa", ("pt-PT",), 0.0006, "derived", "GNB"),
    C("ke", "Kenya", "Africa", ("en-GB",), 0.02, "measured", "KEN"),
    C("lr", "Liberia", "Africa", ("en-GB",), 0.0018, "derived", "LBR"),
    C("ly", "Libya", "Africa", ("ar-SA",), 0.0047, "derived", "LBY"),
    C("mg", "Madagascar", "Africa", ("fr-FR",), 0.0082, "derived", "MDG"),
    C("mw", "Malawi", "Africa", ("en-GB",), 0.0057, "derived", "MWI"),
    C("ml", "Mali", "Africa", ("fr-FR",), 0.0063, "derived", "MLI"),
    C("mr", "Mauritania", "Africa", ("ar-SA", "fr-FR"), 0.002, "derived", "MRT"),
    C("mu", "Mauritius", "Africa", ("en-GB", "fr-FR"), 0.0018, "derived", "MUS"),
    C("ma", "Morocco", "Africa", ("ar-SA", "fr-FR"), 0.0307, "derived", "MAR"),
    C("mz", "Mozambique", "Africa", ("pt-PT",), 0.0092, "derived", "MOZ"),
    C("na", "Namibia", "Africa", ("en-GB",), 0.0021, "derived", "NAM"),
    C("ne", "Niger", "Africa", ("fr-FR",), 0.0055, "derived", "NER"),
    C("ng", "Nigeria", "Africa", ("en-GB",), 0.03, "measured", "NGA"),
    C("rw", "Rwanda", "Africa", ("en-GB", "fr-FR"), 0.0048, "derived", "RWA"),
    C("sn", "Senegal", "Africa", ("fr-FR",), 0.0096, "derived", "SEN"),
    C("sc", "Seychelles", "Africa", ("en-GB", "fr-FR"), 0.0002, "derived", "SYC"),
    C("sl", "Sierra Leone", "Africa", ("en-GB",), 0.0029, "derived", "SLE"),
    C("za", "South Africa", "Africa", ("en-GB",), 0.03, "measured", "ZAF"),
    C("st", "São Tomé & Príncipe", "Africa", ("pt-PT",), 0.0001, "derived", "STP"),
    C("tz", "Tanzania", "Africa", ("en-GB",), 0.02, "measured", "TZA"),
    C("tn", "Tunisia", "Africa", ("ar-SA", "fr-FR"), 0.0102, "derived", "TUN"),
    C("ug", "Uganda", "Africa", ("en-GB",), 0.02, "measured", "UGA"),
    C("zm", "Zambia", "Africa", ("en-GB",), 0.0084, "derived", "ZMB"),
    C("zw", "Zimbabwe", "Africa", ("en-GB",), 0.0091, "derived", "ZWE"),
    C("af", "Afghanistan", "Asia", (), 0.0086, "derived", "AFG"),
    C("bt", "Bhutan", "Asia", ("en-GB",), 0.0005, "derived", "BTN"),
    C("bn", "Brunei", "Asia", ("ms",), 0.0011, "derived", "BRN"),
    C("kh", "Cambodia", "Asia", (), 0.0139, "derived", "KHM"),
    C("cn", "China", "Asia", ("zh-Hans",), 0.45, "measured", "CHN"),
    C("hk", "Hong Kong", "Asia", ("zh-Hant", "en-GB"), 0.0281, "derived", "HKG"),
    C("in", "India", "Asia", ("en-GB", "hi", "bn-BD", "gu-IN", "kn-IN", "ml-IN", "mr-IN", "or-IN", "pa-IN", "ta-IN", "te-IN", "ur-PK"), 0.15, "measured", "IND"),
    C("id", "Indonesia", "Asia", ("id",), 0.05, "measured", "IDN"),
    C("jp", "Japan", "Asia", ("ja",), 0.35, "measured", "JPN"),
    C("kz", "Kazakhstan", "Asia", ("ru",), 0.0272, "derived", "KAZ"),
    C("kg", "Kyrgyzstan", "Asia", ("ru",), 0.0048, "derived", "KGZ"),
    C("la", "Laos", "Asia", (), 0.0041, "derived", "LAO"),
    C("mo", "Macao", "Asia", ("zh-Hant",), 0.0023, "derived", "MAC"),
    C("my", "Malaysia", "Asia", ("ms", "en-GB", "zh-Hans"), 0.04, "measured", "MYS"),
    C("mv", "Maldives", "Asia", ("en-GB",), 0.0011, "derived", "MDV"),
    C("mn", "Mongolia", "Asia", (), 0.0035, "derived", "MNG"),
    C("mm", "Myanmar", "Asia", (), 0.0294, "derived", "MMR"),
    C("np", "Nepal", "Asia", ("en-GB",), 0.0123, "derived", "NPL"),
    C("pk", "Pakistan", "Asia", ("en-GB", "ur-PK"), 0.02, "measured", "PAK"),
    C("ph", "Philippines", "Asia", ("en-GB",), 0.04, "measured", "PHL"),
    C("sg", "Singapore", "Asia", ("en-GB", "zh-Hans"), 0.04, "measured", "SGP"),
    C("kr", "South Korea", "Asia", ("ko",), 0.2, "measured", "KOR"),
    C("lk", "Sri Lanka", "Asia", ("en-GB",), 0.015, "derived", "LKA"),
    C("tw", "Taiwan", "Asia", ("zh-Hant",), 0.08, "measured", "TWN"),
    C("tj", "Tajikistan", "Asia", ("ru",), 0.0041, "derived", "TJK"),
    C("th", "Thailand", "Asia", ("th",), 0.05, "measured", "THA"),
    C("tm", "Turkmenistan", "Asia", ("ru",), 0.0022, "derived", "TKM"),
    C("uz", "Uzbekistan", "Asia", ("ru",), 0.0243, "derived", "UZB"),
    C("vn", "Vietnam", "Asia", ("vi",), 0.1349, "derived", "VNM"),
    C("au", "Australia", "Oceania", ("en-AU",), 0.12, "measured", "AUS"),
    C("fj", "Fiji", "Oceania", ("en-GB",), 0.0016, "derived", "FJI"),
    C("fm", "Micronesia", "Oceania", ("en-US",), 0.0001, "derived", "FSM"),
    C("nr", "Nauru", "Oceania", ("en-GB",), 0.0001, "derived", "NRU"),
    C("nz", "New Zealand", "Oceania", ("en-AU",), 0.03, "measured", "NZL"),
    C("pw", "Palau", "Oceania", ("en-US",), 0.0001, "derived", "PLW"),
    C("pg", "Papua New Guinea", "Oceania", ("en-GB",), 0.0056, "derived", "PNG"),
    C("sb", "Solomon Islands", "Oceania", ("en-GB",), 0.0004, "derived", "SLB"),
    C("to", "Tonga", "Oceania", ("en-GB",), 0.0001, "derived", "TON"),
    C("vu", "Vanuatu", "Oceania", ("en-GB", "fr-FR"), 0.0002, "derived", "VUT"),
)


COUNTRIES: dict[str, Country] = {c.code: c for c in _TABLE}
_BY_ALPHA3: dict[str, Country] = {c.alpha3: c for c in _TABLE}
CODES = frozenset(COUNTRIES)

# The storefronts English names with "the" inside a sentence: plural names
# ("the Netherlands", "the Bahamas", "the Cayman Islands"), names built on a
# common noun ("the United States", "the Dominican Republic", "the UAE") and
# the Gambia. Every other name reads without one ("in Argentina"), including
# the plural names that never take it (Barbados, Honduras, Seychelles, St.
# Kitts & Nevis). aso/tests/test_store_phrase.py holds the reviewed list.
_NAMED_WITH_THE = frozenset({"bs", "vg", "ky", "do", "tc", "us", "nl", "gb", "ae", "gm", "mv", "ph", "sb"})

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
    """The storefront for a two letter code ("us") or App Store Connect's
    three letter territory code ("USA")."""
    code = (code or "").strip()
    if len(code) == 3:
        return _BY_ALPHA3.get(code.upper())
    return COUNTRIES.get(code.lower())


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


def sentence_name(code: str) -> str:
    """The storefront's name as a sentence uses it: "the United States",
    "the Netherlands", "Argentina"."""
    country = get(code)
    if country is None:
        return name(code)
    return f"the {country.name}" if country.code in _NAMED_WITH_THE else country.name


def store_phrase(code: str) -> str:
    """"the App Store in Argentina", "the App Store in the United States".

    The one way a sentence names a storefront's App Store. The country goes
    after "in" because most country names are not adjectives: "the Argentina
    App Store" is not English, "the App Store in Argentina" is. The browser
    reads the same phrase from the country catalog (aso/country_picker.py).
    """
    return f"the App Store in {sentence_name(code or 'us')}"


def stores_phrase(codes) -> str:
    """store_phrase for one or more storefronts: "the App Store in Sweden",
    "the App Store in Sweden or the United States", "the App Store in Sweden,
    Norway or Denmark"."""
    names = [sentence_name(code) for code in codes or ()]
    if len(names) < 2:
        return store_phrase(next(iter(codes or ()), ""))
    return f"the App Store in {', '.join(names[:-1])} or {names[-1]}"


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
