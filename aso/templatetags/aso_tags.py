import json
import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

from aso.services import compound_form

register = template.Library()

# Minimum Apple week-over-week popularity move that renders a trend
# arrow. MUST match APPLE_TREND_MIN_DELTA in popularity-display.js
# (renderer-twin rule, guarded by test_scoring_consistency).
APPLE_TREND_MIN_DELTA = 3


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
        f'<span class="{color} ml-1.5" title="Change since this keyword&#x27;s '
        f'previous check">({arrow}{abs(delta)})</span>'
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


def _fmt_dl(n):
    """Mirror the canonical fmt() in static/js/ai-tabs-shared.js.

    Preserves 1 decimal for values < 10 so we never display "<1" or rounded zeros.
    See scoring-consistency.instructions.md.
    """
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "0"
    if n >= 1000:
        s = f"{n / 1000:.1f}"
        return (s[:-2] if s.endswith(".0") else s) + "K"
    if n < 1:
        return f"{n:.1f}"
    if n < 10:
        s = f"{n:.1f}"
        return s[:-2] if s.endswith(".0") else s
    return str(round(n))


@register.simple_tag
def download_cell(estimates, idx=0, total=0):
    """Render the Est. Downloads cell — rank #1 range with hover tooltip for #1/#5/#10.

    Server-side counterpart of formatDownloadCell() in static/js/ai-tabs-shared.js.
    Uses positions[0]/[4]/[9] (NOT tier averages) per
    scoring-consistency.instructions.md.

    `idx` (0-based row index) and `total` (row count) drive tooltip placement —
    top-half rows show the tooltip below to avoid clipping against the table
    header; bottom-half rows show it above. Mirrors the JS module.
    """
    if not isinstance(estimates, dict):
        return mark_safe('<span class="text-xs text-slate-500">—</span>')
    positions = estimates.get("positions") or []
    if len(positions) < 10:
        return mark_safe('<span class="text-xs text-slate-500">—</span>')
    p1, p5, p10 = positions[0], positions[4], positions[9]
    p1_lo, p1_hi = _fmt_dl(p1.get("downloads_low", 0)), _fmt_dl(p1.get("downloads_high", 0))
    p5_lo, p5_hi = _fmt_dl(p5.get("downloads_low", 0)), _fmt_dl(p5.get("downloads_high", 0))
    p10_lo, p10_hi = _fmt_dl(p10.get("downloads_low", 0)), _fmt_dl(p10.get("downloads_high", 0))
    show_below = total > 0 and idx < total / 2
    pos_class = "top-full mt-2" if show_below else "bottom-full mb-2"
    return mark_safe(
        # Named group ("group/dl") scopes the hover to THIS cell only. A plain
        # "group" would also respond to the parent <tr class="... group">, making
        # the tooltip appear on row-hover instead of cell-hover.
        '<div class="group/dl relative inline-block">'
        '<span class="text-xs font-mono text-slate-300 cursor-help border-b border-dotted border-slate-600">'
        f'{p1_lo}–{p1_hi}<span class="text-slate-500">/day</span></span>'
        f'<div class="hidden group-hover/dl:block absolute z-20 {pos_class} left-1/2 -translate-x-1/2 w-48 bg-slate-800 border border-white/10 rounded-lg p-3 shadow-xl text-left">'
        '<p class="text-[10px] text-slate-500 mb-2 font-medium uppercase tracking-wider">Est. daily downloads</p>'
        '<div class="space-y-1.5">'
        f'<div class="flex justify-between text-xs"><span class="text-emerald-400">Rank #1</span><span class="text-slate-300 font-mono">{p1_lo}–{p1_hi}</span></div>'
        f'<div class="flex justify-between text-xs"><span class="text-amber-400">Rank #5</span><span class="text-slate-300 font-mono">{p5_lo}–{p5_hi}</span></div>'
        f'<div class="flex justify-between text-xs"><span class="text-slate-400">Rank #10</span><span class="text-slate-300 font-mono">{p10_lo}–{p10_hi}</span></div>'
        '</div></div></div>'
    )


# Badge chip classes - MUST match BADGES in popularity-display.js (twin
# rule). EST* is deliberately as quiet as EST: fallback is the NORMAL case
# for long-tail keywords under the Apple source, not a warning; blue ASA
# is the scannable "official value" signal.
_BADGE_EST_CLS = "bg-slate-700/40 text-slate-300 border border-white/10"
_BADGE_ASA_CLS = "bg-sky-900/40 text-sky-300 border border-sky-500/30"


def _badge_popover(badge_cls, label, heading, paragraphs, note="",
                   idx=0, total=0):
    """Source badge + hover popover. Mirror of badgeHtml() in the JS twin.

    `idx`/`total` flip the popover below for top-half rows (avoids
    clipping against the table header) and above otherwise - same rule
    as download_cell.
    """
    show_below = total > 0 and idx < total / 2
    pos = "top-full mt-2" if show_below else "bottom-full mb-2"
    paras = "".join(
        f'<p class="text-[11px] leading-relaxed text-slate-300'
        f'{" mt-1.5" if i else ""}">{escape(p)}</p>'
        for i, p in enumerate(paragraphs)
    )
    note_html = (
        '<p class="text-[10px] leading-relaxed text-slate-500 mt-2 pt-1.5 '
        f'border-t border-white/5">{escape(note)}</p>'
        if note
        else ""
    )
    return (
        '<span class="group/pop relative inline-flex">'
        '<span class="text-[8px] font-semibold uppercase tracking-wide rounded '
        f'px-0.5 py-px {badge_cls} cursor-help">{label}</span>'
        f'<div class="hidden group-hover/pop:block absolute z-20 {pos} '
        'left-1/2 -translate-x-1/2 w-64 bg-slate-800 border border-white/10 '
        'rounded-lg p-3 shadow-xl text-left normal-case font-normal '
        'tracking-normal whitespace-normal">'
        '<p class="text-[10px] text-slate-500 mb-1.5 font-medium uppercase '
        f'tracking-wider">{escape(heading)}</p>'
        f"{paras}{note_html}"
        "</div></span>"
    )


def _popularity_badge(internal, apple, source, is_fallback, cap, genre,
                      apple_configured, idx=0, total=0):
    """Badge + popover for a resolved popularity row. Mirror of
    resolveTip() in the JS twin - same cases, same copy."""
    if is_fallback:
        if cap is None:
            return _badge_popover(
                _BADGE_EST_CLS, "EST*", "No Apple data for this storefront",
                ["Apple publishes no search-popularity data for this "
                 "storefront, so RespectASO's estimate powers the score "
                 "directly."],
                idx=idx, total=total,
            )
        where = f"the {genre} category" if genre else "its category"
        absent_para = (
            "Apple lists each category's ~500 most-searched terms, and "
            f"this keyword is not among them for {where} in this "
            "storefront this week."
        )
        if internal is not None and internal > cap:
            return _badge_popover(
                _BADGE_EST_CLS, "EST*", "Not in Apple's top terms - capped",
                [absent_para,
                 "It cannot score above Apple's lowest reported value "
                 f"there ({cap + 1}), so RespectASO's estimate of "
                 f"{internal} is scored as {cap}."],
                idx=idx, total=total,
            )
        est_ref = f" ({internal})" if internal is not None else ""
        return _badge_popover(
            _BADGE_EST_CLS, "EST*", "Not in Apple's top terms",
            [absent_para,
             f"RespectASO's estimate{est_ref} already sits below Apple's "
             f"lowest reported value there ({cap + 1}), so it powers the "
             "score unchanged."],
            idx=idx, total=total,
        )
    if source == "apple":
        note = (
            f"RespectASO estimate for comparison: {internal}"
            if internal is not None else ""
        )
        return _badge_popover(
            _BADGE_ASA_CLS, "ASA", "Apple Ads popularity",
            ["Apple's official search popularity for this storefront, "
             "updated weekly - the active source powering your scores."],
            note=note, idx=idx, total=total,
        )
    if apple is not None:
        note = f"Apple's official value for comparison: {apple}"
    elif apple_configured:
        note = ("Not among Apple's top terms in this storefront - Apple "
                "reports no value.")
    else:
        note = ""
    return _badge_popover(
        _BADGE_EST_CLS, "EST", "RespectASO estimate",
        ["RespectASO's own estimate, calibrated to Apple's official 1-100 "
         "popularity scale - the active source powering your scores."],
        note=note, idx=idx, total=total,
    )


@register.simple_tag
def popularity_cell(result, extra_html="", idx=0, total=0):
    """Render the popularity cell for a SearchResult row.

    Server-side counterpart of formatPopularityCell() in
    static/js/popularity-display.js - the two MUST stay visually identical
    (guarded by test_scoring_consistency).

    ONE number per cell: the effective value (feeds all calculations) +
    a source badge (EST / ASA / EST* fallback). Everything else lives in
    the badge's hover popover: what the source is, the other source's
    value for comparison, and on fallback rows the full cap story with
    the row's own numbers (raw estimate, cap, category floor) - the cap
    is explained, never hidden, but no longer competes with the score in
    the table.

    When the view attached `apple_trend` (Apple's week-over-week
    popularity delta, see views._attach_apple_trends), an arrow renders
    for moves of APPLE_TREND_MIN_DELTA or more - identical thresholds
    and glyphs to the JS twin.

    `extra_html` appends inline content without breaking the layout.
    `idx`/`total` (0-based row index, row count) flip the popover away
    from the nearest table edge - pass them in loops.
    """
    from ..apple_ads.storage import load_apple_settings
    from ..popularity import absent_cap
    from ..apple_ads.genres import genre_label

    effective = result.effective_popularity
    internal = result.popularity_score
    apple = result.apple_popularity_score
    source = result.popularity_source_used
    is_fallback = result.popularity_is_fallback
    apple_configured = bool(load_apple_settings()["apple_ads"]["tested_ok"])
    inferred_genre = getattr(result, "inferred_genre", "") or ""
    cap = (
        absent_cap(result.country, inferred_genre) if is_fallback else None
    )

    trend = getattr(result, "apple_trend", None)
    if trend is not None and abs(trend) >= APPLE_TREND_MIN_DELTA:
        arrow = "▲" if trend > 0 else "▼"
        tone = "text-emerald-400" if trend > 0 else "text-red-400"
        extra_html = (
            f'<span class="text-[9px] {tone} cursor-help" '
            f'title="Apple popularity vs previous week: {trend:+d}">'
            f"{arrow}</span>" + (extra_html or "")
        )

    try:
        idx, total = int(idx), int(total)
    except (TypeError, ValueError):
        idx, total = 0, 0
    badge = _popularity_badge(
        internal, apple, source, is_fallback, cap,
        genre_label(inferred_genre), apple_configured, idx=idx, total=total,
    )
    effective_txt = str(effective) if effective is not None else "—"
    return mark_safe(
        '<div class="leading-tight inline-block text-center">'
        '<span class="inline-flex items-center justify-center gap-1.5">'
        f'<span class="text-sm font-semibold text-purple-400">{effective_txt}</span>'
        f'{badge}{extra_html}'
        "</span>"
        "</div>"
    )


@register.filter
def download_sort_value(estimates):
    """Return positions[0].downloads_high as the sort key (rank #1 high estimate).

    Per scoring-consistency.instructions.md: download sorts must use
    positions[0].downloads_high, NEVER tier averages.
    Returns -1 when data is missing so those rows fall to the bottom on desc sort.
    """
    if not isinstance(estimates, dict):
        return -1
    positions = estimates.get("positions") or []
    if not positions:
        return -1
    try:
        return float(positions[0].get("downloads_high", -1))
    except (TypeError, ValueError):
        return -1


@register.filter
def dl_interval(value):
    """Render a [low, high] dict/tuple as '~120–180' (no '/day' suffix).

    Accepts dicts with 'low'/'high' keys, tuples, or lists. Returns '—' for
    empty intervals. Used by the App Summary panel.
    """
    from aso.dashboard_summary import format_interval

    if isinstance(value, dict):
        return format_interval(value.get("low", 0), value.get("high", 0))
    if isinstance(value, (list, tuple)) and len(value) >= 2:
        return format_interval(value[0], value[1])
    return "—"


@register.filter
def dl_interval_from(low, high):
    """Render a two-arg interval. Usage: ``{{ low|dl_interval_from:high }}``.

    Lets templates render a [low, high] without us packing every interval
    into a dict in the view. Returns '—' when both ends are zero.
    """
    from aso.dashboard_summary import format_interval

    return format_interval(low, high)


@register.filter
def pct_of(part, whole):
    """Return part/whole as an integer percentage (clamped 0–100). Safe for div-by-zero."""
    try:
        whole = float(whole)
        if whole <= 0:
            return 0
        pct = round(float(part) / whole * 100)
        return max(0, min(100, pct))
    except (TypeError, ValueError):
        return 0


@register.filter
def get_item(d, key):
    """Look up a dict key from a template. Usage: {{ mydict|get_item:'foo' }}."""
    if isinstance(d, dict):
        return d.get(key)
    return None


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


# Highlight tiers shared with static/js/keyword-highlight.js. The two
# renderers MUST emit identical HTML (aso/tests/test_keyword_highlight.py
# runs the JS under node and compares the output).
_HL_CLS_EXACT = "bg-green-500/25 text-green-300 rounded px-0.5"
_HL_CLS_ALL = "bg-amber-500/25 text-amber-300 rounded px-0.5"
_HL_CLS_PART = "bg-slate-400/20 text-slate-300 rounded px-0.5"


def _compound_span(title, compound):
    """(start, end) of the keyword's run-together form where it starts a
    word of the title ("ScrollLess" for "scroll less"), or None."""
    if not compound:
        return None
    for m in re.finditer(r"[^\W_]+", title):
        if m.group().lower().startswith(compound):
            return (m.start(), m.start() + len(compound))
    return None


def _render_marks(title, spans, cls):
    """Wrap each span in a <mark>. Touching spans are merged into ONE mark,
    so a run-together word ("ScrollLess") is never drawn as two padded
    chips that read like two words."""
    merged = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    parts = []
    pos = 0
    for start, end in merged:
        parts.append(escape(title[pos:start]))
        parts.append(f'<mark class="{cls}">{escape(title[start:end])}</mark>')
        pos = end
    parts.append(escape(title[pos:]))
    return mark_safe("".join(parts))


@register.filter(needs_autoescape=True, is_safe=True)
def highlight_keyword(title, keyword, autoescape=True):
    """Highlight keyword words in an app title with 3-tier colour coding.

    Tier 1 (green)  - exact phrase in the title, or its run-together
                      spelling ("ScrollLess" for "scroll less").
    Tier 2 (amber)  - all words present but not as the exact phrase.
    Tier 3 (slate)  - only some keyword words appear.

    Client-side twin: highlightKeyword() in static/js/keyword-highlight.js.

    Usage: {{ comp.trackName|highlight_keyword:result.keyword.keyword }}
    """
    if not title:
        return ""
    title_str = str(title)
    if not keyword:
        return escape(title_str)
    words = [w for w in str(keyword).strip().split() if w]
    if not words:
        return escape(title_str)

    title_lower = title_str.lower()
    kw_lower = " ".join(words).lower()
    present = [w for w in words if w.lower() in title_lower]

    if len(words) > 1 and kw_lower in title_lower:
        phrase_re = re.compile(re.escape(kw_lower), re.IGNORECASE)
        spans = [m.span() for m in phrase_re.finditer(title_str)]
        return _render_marks(title_str, spans, _HL_CLS_EXACT)

    compound = _compound_span(
        title_str, compound_form([w.lower() for w in words])
    )
    if compound:
        return _render_marks(title_str, [compound], _HL_CLS_EXACT)

    if not present:
        return escape(title_str)
    if len(words) == 1:
        cls = _HL_CLS_EXACT
    elif len(present) == len(words):
        cls = _HL_CLS_ALL
    else:
        cls = _HL_CLS_PART
    word_re = re.compile(
        "|".join(re.escape(w) for w in present), re.IGNORECASE
    )
    spans = [m.span() for m in word_re.finditer(title_str)]
    return _render_marks(title_str, spans, cls)