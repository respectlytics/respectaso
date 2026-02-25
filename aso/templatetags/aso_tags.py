import json

from django import template
from django.utils.safestring import mark_safe

register = template.Library()


@register.filter(is_safe=True)
def to_json(value):
    """Serialize a Python value to a JSON string (safe for embedding in <script>)."""
    return mark_safe(json.dumps(value))


@register.filter
def abs_val(value):
    """Return the absolute value.  Usage: {{ delta|abs_val }}"""
    try:
        return abs(value)
    except (TypeError, ValueError):
        return value


@register.filter(is_safe=True)
def trend_arrow(delta, metric="higher_better"):
    """Render a coloured ↑/↓ arrow for a delta value.

    Usage:
        {{ result.popularity_delta|trend_arrow }}           → green ↑ / red ↓
        {{ result.difficulty_delta|trend_arrow:"lower_better" }} → green ↓ / red ↑
        {{ result.rank_delta|trend_arrow }}                 → positive = improved
    """
    if delta is None:
        return ""
    try:
        delta = int(delta)
    except (TypeError, ValueError):
        return ""
    if delta == 0:
        return ""

    # Determine if the change is positive or negative
    if metric == "lower_better":
        is_good = delta < 0
    else:
        is_good = delta > 0

    color = "text-green-400" if is_good else "text-red-400"
    arrow = "↑" if delta > 0 else "↓"
    return mark_safe(
        f'<span class="{color} ml-1.5" title="Change from previous">({arrow}{abs(delta)})</span>'
    )

# ISO 3166-1 alpha-2 → country name (covers all App Store countries)
COUNTRY_NAMES = {
    "ad": "Andorra",
    "ae": "UAE",
    "af": "Afghanistan",
    "ag": "Antigua & Barbuda",
    "ai": "Anguilla",
    "al": "Albania",
    "am": "Armenia",
    "ao": "Angola",
    "ar": "Argentina",
    "at": "Austria",
    "au": "Australia",
    "az": "Azerbaijan",
    "bb": "Barbados",
    "bd": "Bangladesh",
    "be": "Belgium",
    "bf": "Burkina Faso",
    "bg": "Bulgaria",
    "bh": "Bahrain",
    "bj": "Benin",
    "bm": "Bermuda",
    "bn": "Brunei",
    "bo": "Bolivia",
    "br": "Brazil",
    "bs": "Bahamas",
    "bt": "Bhutan",
    "bw": "Botswana",
    "by": "Belarus",
    "bz": "Belize",
    "ca": "Canada",
    "cg": "Congo",
    "ch": "Switzerland",
    "cl": "Chile",
    "cn": "China",
    "co": "Colombia",
    "cr": "Costa Rica",
    "cv": "Cape Verde",
    "cy": "Cyprus",
    "cz": "Czechia",
    "de": "Germany",
    "dk": "Denmark",
    "dm": "Dominica",
    "do": "Dominican Republic",
    "dz": "Algeria",
    "ec": "Ecuador",
    "ee": "Estonia",
    "eg": "Egypt",
    "es": "Spain",
    "fi": "Finland",
    "fj": "Fiji",
    "fm": "Micronesia",
    "fr": "France",
    "ga": "Gabon",
    "gb": "United Kingdom",
    "gd": "Grenada",
    "ge": "Georgia",
    "gh": "Ghana",
    "gm": "Gambia",
    "gr": "Greece",
    "gt": "Guatemala",
    "gw": "Guinea-Bissau",
    "gy": "Guyana",
    "hk": "Hong Kong",
    "hn": "Honduras",
    "hr": "Croatia",
    "hu": "Hungary",
    "id": "Indonesia",
    "ie": "Ireland",
    "il": "Israel",
    "in": "India",
    "iq": "Iraq",
    "is": "Iceland",
    "it": "Italy",
    "jm": "Jamaica",
    "jo": "Jordan",
    "jp": "Japan",
    "ke": "Kenya",
    "kg": "Kyrgyzstan",
    "kh": "Cambodia",
    "kn": "St. Kitts & Nevis",
    "kr": "South Korea",
    "kw": "Kuwait",
    "ky": "Cayman Islands",
    "kz": "Kazakhstan",
    "la": "Laos",
    "lb": "Lebanon",
    "lc": "St. Lucia",
    "lk": "Sri Lanka",
    "lr": "Liberia",
    "lt": "Lithuania",
    "lu": "Luxembourg",
    "lv": "Latvia",
    "md": "Moldova",
    "mg": "Madagascar",
    "mk": "North Macedonia",
    "ml": "Mali",
    "mm": "Myanmar",
    "mn": "Mongolia",
    "mo": "Macao",
    "mr": "Mauritania",
    "ms": "Montserrat",
    "mt": "Malta",
    "mu": "Mauritius",
    "mv": "Maldives",
    "mw": "Malawi",
    "mx": "Mexico",
    "my": "Malaysia",
    "mz": "Mozambique",
    "na": "Namibia",
    "ne": "Niger",
    "ng": "Nigeria",
    "ni": "Nicaragua",
    "nl": "Netherlands",
    "no": "Norway",
    "np": "Nepal",
    "nz": "New Zealand",
    "om": "Oman",
    "pa": "Panama",
    "pe": "Peru",
    "pg": "Papua New Guinea",
    "ph": "Philippines",
    "pk": "Pakistan",
    "pl": "Poland",
    "pt": "Portugal",
    "pw": "Palau",
    "py": "Paraguay",
    "qa": "Qatar",
    "ro": "Romania",
    "rs": "Serbia",
    "ru": "Russia",
    "rw": "Rwanda",
    "sa": "Saudi Arabia",
    "sb": "Solomon Islands",
    "sc": "Seychelles",
    "se": "Sweden",
    "sg": "Singapore",
    "si": "Slovenia",
    "sk": "Slovakia",
    "sl": "Sierra Leone",
    "sn": "Senegal",
    "sr": "Suriname",
    "st": "São Tomé & Príncipe",
    "sv": "El Salvador",
    "sz": "Eswatini",
    "tc": "Turks & Caicos",
    "td": "Chad",
    "th": "Thailand",
    "tj": "Tajikistan",
    "tm": "Turkmenistan",
    "tn": "Tunisia",
    "to": "Tonga",
    "tr": "Türkiye",
    "tt": "Trinidad & Tobago",
    "tw": "Taiwan",
    "tz": "Tanzania",
    "ua": "Ukraine",
    "ug": "Uganda",
    "us": "United States",
    "uy": "Uruguay",
    "uz": "Uzbekistan",
    "vc": "St. Vincent",
    "ve": "Venezuela",
    "vg": "British Virgin Islands",
    "vn": "Vietnam",
    "vu": "Vanuatu",
    "ye": "Yemen",
    "za": "South Africa",
    "zw": "Zimbabwe",
}


def _country_flag(code: str) -> str:
    """Convert 2-letter ISO country code to flag emoji."""
    if not code or len(code) != 2:
        return ""
    return "".join(chr(0x1F1E6 + ord(c.upper()) - ord("A")) for c in code)


def _country_name(code: str) -> str:
    """Get country name from 2-letter code; falls back to uppercase code."""
    if not code:
        return ""
    return COUNTRY_NAMES.get(code.lower(), code.upper())


@register.filter
def country_display(code):
    """
    Return flag emoji + country name from 2-letter ISO code.

    Usage: {{ result.country|country_display }}
    """
    if not code:
        return mark_safe("—")
    flag = _country_flag(code)
    name = _country_name(code)
    return mark_safe(f'{flag} <span class="ml-0.5">{name}</span>')


@register.filter
def country_flag(code):
    """Return just the flag emoji for a 2-letter ISO code."""
    return _country_flag(code) if code else ""


@register.filter
def country_name(code):
    """Return just the country name for a 2-letter ISO code."""
    return _country_name(code) if code else ""


@register.filter
def get_tier(d, key):
    """Access a dict key safely. Usage: {{ tiers|get_tier:'top_5' }}"""
    if isinstance(d, dict):
        return d.get(key)
    return None


@register.filter
def format_number(value):
    """Format an integer with comma separators. Usage: {{ num|format_number }}"""
    try:
        return f"{int(value):,}"
    except (ValueError, TypeError):
        return value


@register.filter
def format_release_date(value):
    """Format an ISO release date string to 'Mon YYYY'. Usage: {{ date_str|format_release_date }}"""
    if not value:
        return "\u2014"
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.strftime("%b %Y")
    except (ValueError, TypeError):
        return str(value)[:10]


@register.filter(needs_autoescape=True, is_safe=True)
def highlight_keyword(title, keyword, autoescape=True):
    """Highlight keyword words in app title with 3-tier colour coding.

    Tier 1 (green)  – exact phrase appears in title.
    Tier 2 (amber)  – all words present but not as exact phrase.
    Tier 3 (slate)  – only some keyword words appear.

    Usage: {{ comp.trackName|highlight_keyword:result.keyword.keyword }}
    """
    import re
    from django.utils.html import escape
    from django.utils.safestring import mark_safe

    if not keyword or not title:
        return escape(str(title)) if title else ""

    title_str = str(title)
    kw_str = str(keyword).strip()
    words = [w for w in kw_str.split() if w]
    if not words:
        return escape(title_str)

    title_lower = title_str.lower()
    kw_lower = kw_str.lower()

    # Determine which keyword words appear in the title
    present = [w for w in words if w.lower() in title_lower]
    has_exact_phrase = len(words) > 1 and kw_lower in title_lower
    all_present = len(present) == len(words)

    # Pick highlight class
    # Exact phrase → green | All words scattered → amber | Partial → slate
    CLS_EXACT = 'bg-green-500/25 text-green-300 rounded px-0.5'
    CLS_ALL   = 'bg-amber-500/25 text-amber-300 rounded px-0.5'
    CLS_PART  = 'bg-slate-400/20 text-slate-300 rounded px-0.5'

    if has_exact_phrase:
        # Highlight the full phrase with green, remaining individual words
        # also green (they're part of the phrase context)
        phrase_re = re.compile(f"({re.escape(kw_str)})", re.IGNORECASE)
        parts = phrase_re.split(title_str)
        result_parts = []
        for part in parts:
            if phrase_re.fullmatch(part):
                result_parts.append(f'<mark class="{CLS_EXACT}">{escape(part)}</mark>')
            else:
                result_parts.append(escape(part))
        return mark_safe("".join(result_parts))

    if not present:
        return escape(title_str)

    # For single-word keywords, decide colour by presence
    if len(words) == 1:
        cls = CLS_EXACT  # single word = exact match if present
    elif all_present:
        cls = CLS_ALL
    else:
        cls = CLS_PART

    word_re = re.compile(
        "(" + "|".join(re.escape(w) for w in present) + ")",
        re.IGNORECASE,
    )
    parts = word_re.split(title_str)
    result_parts = []
    for part in parts:
        if word_re.fullmatch(part):
            result_parts.append(f'<mark class="{cls}">{escape(part)}</mark>')
        else:
            result_parts.append(escape(part))
    return mark_safe("".join(result_parts))
