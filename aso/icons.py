"""The icons a keyword's tags and notes are drawn with, one per meaning, by name.

The data (aso/scoring.py's tags, aso/services.py's notes on a keyword) holds only a name from
ICONS; every screen draws it through svg() here: the app's {% icon %} tag, the legend JSON its
scripts read (static/js/icons.js), and respectaso.com's keyword checker, which vendors this file
unedited. An emoji is drawn by each computer in its own style, so none is used (the
loheden-design skill: icons come from the product's own set, never emoji, except a country's
flag). Each icon is 1em square and takes the colour of the text around it.

Pure Python with no Django, because respectaso.com imports it without the app.

The paths are Heroicons 2.2.0, 24px outline (github.com/tailwindlabs/heroicons), except
`target` and `dot`, drawn here in the same style. Heroicons is under the MIT License:

    MIT License

    Copyright (c) Tailwind Labs, Inc.

    Permission is hereby granted, free of charge, to any person obtaining a copy
    of this software and associated documentation files (the "Software"), to deal
    in the Software without restriction, including without limitation the rights
    to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
    copies of the Software, and to permit persons to whom the Software is
    furnished to do so, subject to the following conditions:

    The above copyright notice and this permission notice shall be included in all
    copies or substantial portions of the Software.

    THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
    IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
    FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
    AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
    LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
    OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
    SOFTWARE.
"""


def _path(d: str) -> str:
    return f'<path d="{d}"/>'


# name: the SVG's inner markup, stroked in the text's colour unless a shape fills itself.
ICONS = {
    # The keyword tags (aso/scoring.py, _TARGETING).
    "target": ('<circle cx="12" cy="12" r="9"/><circle cx="12" cy="12" r="5.25"/>'
               '<circle cx="12" cy="12" r="1.5" fill="currentColor"/>'),                     # Sweet Spot
    "check_circle": _path("M9 12.75 11.25 15 15 9.75M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"),  # Good Target
    "thumb_up": _path(                                                                          # Supporting
        "M6.633 10.25c.806 0 1.533-.446 2.031-1.08a9.041 9.041 0 0 1 2.861-2.4c.723-.384 1.35-.956 "
        "1.653-1.715a4.498 4.498 0 0 0 .322-1.672V2.75a.75.75 0 0 1 .75-.75 2.25 2.25 0 0 1 2.25 "
        "2.25c0 1.152-.26 2.243-.723 3.218-.266.558.107 1.282.725 1.282m0 0h3.126c1.026 0 1.945.694 "
        "2.054 1.715.045.422.068.85.068 1.285a11.95 11.95 0 0 1-2.649 7.521c-.388.482-.987.729-1.605"
        ".729H13.48c-.483 0-.964-.078-1.423-.23l-3.114-1.04a4.501 4.501 0 0 0-1.423-.23H5.904m10.598"
        "-9.75H14.25M5.904 18.5c.083.205.173.405.27.602.197.4-.078.898-.523.898h-.908c-.889 0-1.713"
        "-.518-1.972-1.368a12 12 0 0 1-.521-3.507c0-1.553.295-3.036.831-4.398C3.387 9.953 4.167 9.5 "
        "5 9.5h1.053c.472 0 .745.556.5.96a8.958 8.958 0 0 0-1.302 4.665c0 1.194.232 2.333.654 3.375Z"),
    "trending_up": _path("M2.25 18 9 11.25l4.306 4.306a11.95 11.95 0 0 1 5.814-5.518l2.74-1.22m0 0"
                         "-5.94-2.281m5.94 2.28-2.28 5.941"),                                    # Worth Climbing
    "minus_circle": _path("M15 12H9m12 0a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"),                     # Low Volume
    # A difficulty tier, shown while a keyword has no popularity yet; the chip's colour and word
    # tell the tiers apart.
    "dot": '<circle cx="12" cy="12" r="4.5" fill="currentColor" stroke="none"/>',
    # The notes on a keyword's competition (aso/services.py, insights and opportunity signals).
    "info": _path("m11.25 11.25.041-.02a.75.75 0 0 1 1.063.852l-.708 2.836a.75.75 0 0 0 1.063.853"
                  "l.041-.021M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Zm-9-3.75h.008v.008H12V8.25Z"),
    "tag": _path("M9.568 3H5.25A2.25 2.25 0 0 0 3 5.25v4.318c0 .597.237 1.17.659 1.591l9.581 9.581"
                 "c.699.699 1.78.872 2.607.33a18.095 18.095 0 0 0 5.223-5.223c.542-.827.369-1.908-.33"
                 "-2.607L11.16 3.66A2.25 2.25 0 0 0 9.568 3Z") + _path("M6 6h.008v.008H6V6Z"),
    "building": _path("M3.75 21h16.5M4.5 3h15M5.25 3v18m13.5-18v18M9 6.75h1.5m-1.5 3h1.5m-1.5 3h1.5m3"
                      "-6H15m-1.5 3H15m-1.5 3H15M9 21v-3.375c0-.621.504-1.125 1.125-1.125h3.75c.621 0 "
                      "1.125.504 1.125 1.125V21"),
    "chart_bar": _path("M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 "
                       "20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 0 1 3 19.875v-6.75ZM9.75 8.625c0"
                       "-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 "
                       "1.125-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V8.625ZM16.5 4.125c0-.621"
                       ".504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125"
                       "-1.125 1.125h-2.25a1.125 1.125 0 0 1-1.125-1.125V4.125Z"),
    "title": _path("M3.75 6.75h16.5M3.75 12h16.5m-16.5 5.25H12"),
    "lock": _path("M16.5 10.5V6.75a4.5 4.5 0 1 0-9 0v3.75m-.75 11.25h10.5a2.25 2.25 0 0 0 2.25-2.25"
                  "v-6.75a2.25 2.25 0 0 0-2.25-2.25H6.75a2.25 2.25 0 0 0-2.25 2.25v6.75a2.25 2.25 0 0 "
                  "0 2.25 2.25Z"),
    "star": _path("M11.48 3.499a.562.562 0 0 1 1.04 0l2.125 5.111a.563.563 0 0 0 .475.345l5.518.442"
                  "c.499.04.701.663.321.988l-4.204 3.602a.563.563 0 0 0-.182.557l1.285 5.385a.562.562 0 "
                  "0 1-.84.61l-4.725-2.885a.562.562 0 0 0-.586 0L6.982 20.54a.562.562 0 0 1-.84-.61l1.285"
                  "-5.386a.562.562 0 0 0-.182-.557l-4.204-3.602a.562.562 0 0 1 .321-.988l5.518-.442a.563"
                  ".563 0 0 0 .475-.345L11.48 3.5Z"),
    "trending_down": _path("M2.25 6 9 12.75l4.286-4.286a11.948 11.948 0 0 1 4.306 6.43l.776 2.898m0 0 "
                           "3.182-5.511m-3.182 5.51-5.511-3.181"),
    "sparkles": _path("M9.813 15.904 9 18.75l-.813-2.846a4.5 4.5 0 0 0-3.09-3.09L2.25 12l2.846-.813a4.5 "
                      "4.5 0 0 0 3.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 0 0 3.09 3.09L15.75 12l-2.846.813"
                      "a4.5 4.5 0 0 0-3.09 3.09ZM18.259 8.715 18 9.75l-.259-1.035a3.375 3.375 0 0 0-2.455"
                      "-2.456L14.25 6l1.036-.259a3.375 3.375 0 0 0 2.455-2.456L18 2.25l.259 1.035a3.375 "
                      "3.375 0 0 0 2.456 2.456L21.75 6l-1.035.259a3.375 3.375 0 0 0-2.456 2.456ZM16.894 "
                      "20.567 16.5 21.75l-.394-1.183a2.25 2.25 0 0 0-1.423-1.423L13.5 18.75l1.183-.394a2.25 "
                      "2.25 0 0 0 1.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 0 0 1.423 1.423l1.183.394"
                      "-1.183.394a2.25 2.25 0 0 0-1.423 1.423Z"),
    "mixed": _path("M7.5 21 3 16.5m0 0L7.5 12M3 16.5h13.5m0-13.5L21 7.5m0 0L16.5 12M21 7.5H7.5"),
    # The marks a screen draws beside its words: done, failed, refresh, close. They replaced
    # check, cross, arrow and times characters typed as text (2026-11-18); `sparkles` also marks
    # what the AI suggested.
    "check": _path("m4.5 12.75 6 6 9-13.5"),
    "x_circle": _path("m9.75 9.75 4.5 4.5m0-4.5-4.5 4.5M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0Z"),
    "x_mark": _path("M6 18 18 6M6 6l12 12"),
    "arrow_path": _path("M16.023 9.348h4.992v-.001M2.985 19.644v-4.992m0 0h4.992m-4.993 0 3.181 "
                        "3.183a8.25 8.25 0 0 0 13.803-3.7M4.031 9.865a8.25 8.25 0 0 1 13.803-3.7l3.181 "
                        "3.182m0-4.991v4.99"),
}

# Hidden from screen readers: the word beside every icon says what it means.
_SVG = ('<svg class="shrink-0" width="1em" height="1em" viewBox="0 0 24 24" fill="none" '
        'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" '
        'aria-hidden="true" focusable="false">{}</svg>')


def svg(name: str) -> str:
    """The icon as inline SVG markup, or "" for a name it does not know (such as an emoji
    stored by an older version), so a screen never prints the raw value."""
    inner = ICONS.get(name)
    return _SVG.format(inner) if inner else ""


# The emoji older versions stored as an icon, and the name each became; the migrations that
# repair stored rows read them (aso 0027, aso_pro 0034). A keyword tag (a dict with a "label",
# such as an AI run's stored targeting) and a note on the competition (insights, opportunity
# signals) used the same target emoji for different things, hence two tables. Escapes, so the
# emoji guard (aso/tests/test_no_emoji.py) reads no emoji in this file.
LEGACY_TAG_EMOJI = {
    "\U0001F3AF": "target", "\u2705": "check_circle", "\U0001F44D": "thumb_up",
    "\U0001F331": "trending_up", "\U0001F50D": "minus_circle",
    "\U0001F7E2": "dot", "\U0001F7E1": "dot", "\U0001F7E0": "dot", "\U0001F534": "dot",
}
LEGACY_NOTE_EMOJI = {
    "\U0001F3F7\uFE0F": "tag", "\U0001F3F7": "tag",
    "\U0001F4CD": "info",
    "\U0001F3E2": "building", "\u26A0\uFE0F": "building", "\u26A0": "building",
    "\U0001F4CA": "chart_bar",
    "\U0001F3AF": "title",
    "\U0001F512": "lock",
    "\u2B50": "star",
    "\U0001F4A1": "trending_down", "\U0001F4C9": "trending_down",
    "\U0001F195": "sparkles",
    "\U0001F500": "mixed",
}


def upgrade_stored(value):
    """``value`` (stored JSON) with every legacy emoji under an "icon" key renamed, and whether
    anything changed; anything else is returned as it was."""
    if isinstance(value, dict):
        legacy = LEGACY_TAG_EMOJI if "label" in value else LEGACY_NOTE_EMOJI
        changed = False
        out = {}
        for key, item in value.items():
            if key == "icon" and isinstance(item, str) and item in legacy:
                out[key], changed = legacy[item], True
            else:
                out[key], sub = upgrade_stored(item)
                changed = changed or sub
        return out, changed
    if isinstance(value, list):
        pairs = [upgrade_stored(item) for item in value]
        return [item for item, _ in pairs], any(sub for _, sub in pairs)
    return value, False


def catalog() -> dict[str, str]:
    """Every icon's markup by name, for the scripts that draw icons in the browser."""
    return {name: svg(name) for name in ICONS}
