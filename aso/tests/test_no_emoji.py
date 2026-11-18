"""Icons come from the product's own set, never emoji, except a country's
flag (the loheden-design skill; DAILY_USE_PLAN.md step 10.2).

Scans every template, every script and every Python module a screen, an
export, a message or the MCP server reads, for emoji written out or as an
escape (\\U0001F4A1, \\ud83d\\udca1, \\u{1F4A1}, &#x1F4A1;): U+1F000 to
U+1FAFF, the characters that draw as emoji by default (EMOJI_PRESENTATION),
the warning sign U+26A0 and the emoji presentation selector U+FE0F. A
country's flag (regional indicators, U+1F1E6 to U+1F1FF) stays. A keyword's
tags and notes name their icon (aso/icons.py), and the screens draw it.

Each allowance is named below, and a test fails when one no longer excuses
anything, so a dead exception cannot reopen the door.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

# Text an end user meets: the templates, the scripts, and every Python module
# outside the tests (messages, exports, What's New, MCP texts, the data the
# screens draw).
TEXT_ROOTS = (("aso/templates", "*.html"), ("aso_pro/templates", "*.html"),
              ("_public_overrides", "*.html"), ("static/js", "*.js"))
PYTHON_ROOTS = ("aso", "aso_pro", "core", "licensing", "llm_providers", "desktop", "_public_overrides")

# Characters drawn as emoji with no selector (Unicode emoji-data.txt,
# Emoji_Presentation=Yes, below U+1F000), and two that draw as emoji on a Mac.
EMOJI_PRESENTATION = {
    *range(0x231A, 0x231C), *range(0x23E9, 0x23ED), 0x23F0, 0x23F3, 0x25FD, 0x25FE,
    0x2614, 0x2615, *range(0x2648, 0x2654), 0x267F, 0x2693, 0x26A1, 0x26AA, 0x26AB,
    0x26BD, 0x26BE, 0x26C4, 0x26C5, 0x26CE, 0x26D4, 0x26EA, 0x26F2, 0x26F3, 0x26F5,
    0x26FA, 0x26FD, 0x2705, 0x270A, 0x270B, 0x2728, 0x274C, 0x274E, *range(0x2753, 0x2756),
    0x2757, *range(0x2795, 0x2798), 0x27B0, 0x27BF, 0x2B1B, 0x2B1C, 0x2B50, 0x2B55,
    0x26A0, 0xFE0F,
}

# Allowance 1: What's New entries of past releases are history and stay as
# they shipped (aso/release_notes.py; checked on the data, not the lines).
PAST_RELEASES_WITH_EMOJI = {"2.9.0"}
# Allowance 2: the legacy emoji the migrations rename in stored rows,
# written as escapes in aso/icons.py (LEGACY_TAG_EMOJI, LEGACY_NOTE_EMOJI).
LEGACY_TABLE = "aso/icons.py"
SKIPPED_FILES = {"aso/release_notes.py", LEGACY_TABLE}

_ESCAPES = re.compile(
    r"\\U([0-9a-fA-F]{8})|\\u\{([0-9a-fA-F]{1,6})\}|&#x([0-9a-fA-F]{1,6});|&#(\d{1,7});"
    r"|\\u([dD][89abAB][0-9a-fA-F]{2})\\u([dD][c-fC-F][0-9a-fA-F]{2})|\\u([0-9a-fA-F]{4})"
)


def is_emoji(char: str) -> bool:
    code = ord(char)
    if 0x1F1E6 <= code <= 0x1F1FF:     # half of a country's flag
        return False
    return 0x1F000 <= code <= 0x1FAFF or code in EMOJI_PRESENTATION


def _escaped(text: str) -> str:
    """The characters ``text`` writes as escapes."""
    out = []
    for m in _ESCAPES.finditer(text):
        big, braced, hexa, dec, high, low, small = m.groups()
        if high:
            out.append(chr(0x10000 + ((int(high, 16) - 0xD800) << 10) + (int(low, 16) - 0xDC00)))
            continue
        value = int(dec) if dec else int(big or braced or hexa or small, 16)
        if value <= 0x10FFFF:
            out.append(chr(value))
    return "".join(out)


def emoji_in(text: str) -> list[str]:
    return sorted({char for char in text + _escaped(text) if is_emoji(char)})


def _hits(base: Path, path: Path) -> list[str]:
    rel = path.relative_to(base)
    found = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        chars = emoji_in(line)
        if chars:
            found.append(f"{rel}:{number}: {' '.join(chars)}")
    return found


# Text marks typed as characters, which each computer draws in its own font:
# a check, a cross, a sparkle, a star outline, a ballot box, a refresh arrow,
# a pointer triangle, a diamond (the loheden-design skill: a check or a
# warning is drawn from the product's icons in the colour of its text, or
# said in words). They became aso/icons.py names on 2026-11-18. Three
# characters stay, because they are data drawn as text, not decoration:
# ▲ and ▼ for a rank that moved up or down, and ★ for a star rating.
TEXT_MARKS = set("✓✔✗✘✕✖✦✧✩✪✫✬✭✮✯✰☆☑☒☐↻↺▸▹►◆◇❖♦✱✳")
DATA_SYMBOLS = set("▲▼★")
# A close or remove button labelled with the times sign, and an arrow tacked
# onto the end or start of a link or button ("Next →", "← Prev", "Migration
# guide ↓"). A menu path ("Settings → AI") and a before and after ("#5 → #1")
# have words on both sides and stay.
_CLOSE_GLYPH = re.compile(r">\s*(?:×|&times;)\s*<|['\"]×['\"]")
_TACKED_ARROW = re.compile(r"[\w.)]\s*[→↓](?=\s*(?:<|$|['\"]\s*[+;)\]]))|(?:>|^|['\"])\s*[←▸]\s*\w", re.MULTILINE)


def marks_in(text: str, arrows: bool = True) -> list[str]:
    """The text marks ``text`` types, written out or as an escape, any
    times-sign button and, with ``arrows``, any tacked arrow. Python is read
    without the arrows: its docstrings and parsers use them as words."""
    found = {char for char in text + _escaped(text) if char in TEXT_MARKS}
    if _CLOSE_GLYPH.search(text):
        found.add("×")
    if arrows:
        found.update(m.group(0).strip(" >'\"") for m in _TACKED_ARROW.finditer(text))
    return sorted(found)


def _mark_hits(base: Path, path: Path) -> list[str]:
    rel = path.relative_to(base)
    found = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        marks = marks_in(line, arrows=path.suffix != ".py")
        if marks:
            found.append(f"{rel}:{number}: {' '.join(marks)}")
    return found


class NoTextMarksTest(SimpleTestCase):
    def test_no_template_script_or_python_text_types_a_mark(self):
        base = Path(settings.BASE_DIR)
        hits = []
        for root, pattern in TEXT_ROOTS:
            for path in sorted((base / root).rglob(pattern)):
                if "vendor" not in path.parts:
                    hits += _mark_hits(base, path)
        for root in PYTHON_ROOTS:
            for path in sorted((base / root).rglob("*.py")):
                rel = path.relative_to(base).as_posix()
                if "tests" in path.parts or rel in SKIPPED_FILES:
                    continue
                hits += _mark_hits(base, path)
        self.assertEqual(hits, [], "Draw the mark with aso/icons.py ({% icon %}, ProductIcon) or say it "
                                   "in words:\n" + "\n".join(hits))

    def test_the_scan_finds_each_mark_and_spares_the_data_symbols(self):
        for text, mark in (("✓ Tested", "✓"), ("'✗ ' + message", "✗"), ("<span>✦</span>Suggest", "✦"),
                           ("✔ Done", "✔"), ("✘ Failed", "✘"), ("☆ 4", "☆"), ("↻ Detect", "↻"),
                           ("'Copied \\u2713'", "✓"), ("&#x2726; New", "✦"),
                           ('aria-label="Close">×</button>', "×"), ("Next →</a>", "→"),
                           ("<a href>← Previous</a>", "← P"), ("Migration guide ↓\n", "↓")):
            with self.subTest(text=text):
                self.assertIn(mark, " ".join(marks_in(text)))
        for text in ("▲ 3", "▼ 2", "4.8 ★", "#5 → #1", "Settings → AI", "Title × Keyword Field"):
            with self.subTest(text=text):
                self.assertEqual(marks_in(text), [])
        self.assertEqual(TEXT_MARKS & DATA_SYMBOLS, set())


class NoEmojiTest(SimpleTestCase):
    def test_no_template_or_script_draws_an_emoji(self):
        base = Path(settings.BASE_DIR)
        hits = []
        for root, pattern in TEXT_ROOTS:
            for path in sorted((base / root).rglob(pattern)):
                if "vendor" not in path.parts:
                    hits += _hits(base, path)
        self.assertEqual(hits, [], "Use the product's own icons or words:\n" + "\n".join(hits))

    def test_no_python_text_carries_an_emoji(self):
        base = Path(settings.BASE_DIR)
        hits = []
        for root in PYTHON_ROOTS:
            for path in sorted((base / root).rglob("*.py")):
                rel = path.relative_to(base).as_posix()
                if "tests" in path.parts or rel in SKIPPED_FILES:
                    continue
                hits += _hits(base, path)
        self.assertEqual(hits, [], "Use the product's own icons (aso/icons.py) or words:\n" + "\n".join(hits))

    def test_only_past_releases_keep_their_emoji(self):
        from aso.release_notes import RELEASES

        with_emoji = {release["version"] for release in RELEASES if emoji_in(repr(release))}
        self.assertEqual(with_emoji - PAST_RELEASES_WITH_EMOJI, set(),
                         "A new What's New entry uses words, not emoji")
        self.assertEqual(PAST_RELEASES_WITH_EMOJI - with_emoji, set(),
                         "This allowance excuses nothing any more: remove it")
        self.assertNotIn(settings.VERSION, PAST_RELEASES_WITH_EMOJI)

    def test_the_legacy_tables_are_the_only_emoji_in_their_file(self):
        from aso import icons

        legacy = set("".join([*icons.LEGACY_TAG_EMOJI, *icons.LEGACY_NOTE_EMOJI]))
        text = (Path(settings.BASE_DIR) / LEGACY_TABLE).read_text(encoding="utf-8")
        found = set(emoji_in(text))
        self.assertTrue(found, "This allowance excuses nothing any more: remove it")
        self.assertLessEqual(found, legacy)
        self.assertEqual(emoji_in(text.split("LEGACY_TAG_EMOJI = {")[0]), [],
                         "Only the legacy tables may name an emoji")

    def test_the_scan_finds_an_emoji_and_spares_a_flag(self):
        self.assertEqual(emoji_in("\U0001F4A1 Keywords from previous analysis"), ["\U0001F4A1"])
        self.assertEqual(emoji_in("⭐ 4.8"), ["⭐"])
        self.assertEqual(emoji_in("⚠ Check the key"), ["⚠"])
        self.assertEqual(emoji_in("✅ Covered"), ["✅"])
        self.assertEqual(emoji_in("'\\ud83d\\udca1 Ranking'"), ["\U0001F4A1"])
        self.assertEqual(emoji_in('"\\U0001F3AF"'), ["\U0001F3AF"])
        self.assertEqual(emoji_in("&#x1F3AF; Sweet Spot"), ["\U0001F3AF"])
        self.assertEqual(emoji_in("\U0001F1FA\U0001F1F8 United States"), [])
        self.assertEqual(emoji_in("'\\ud83c\\uddfa\\ud83c\\uddf8'"), [])
        self.assertEqual(emoji_in("★ 4.8"), [])
        self.assertEqual(emoji_in("'Copied \\u2713'"), [])


class IconNamesTest(SimpleTestCase):
    """Every icon the data names is one aso/icons.py draws, through that one
    module (the {% icon %} tag, and static/js/icons.js from its catalogue)."""

    def test_every_tag_and_note_names_a_known_icon(self):
        from aso import icons, scoring

        for item in scoring.classification_legend():
            self.assertIn(item["icon"], icons.ICONS, item["label"])
        for advice in [*scoring._DIFF_ONLY_TARGETING.values(), scoring._DIFF_ONLY_DEFAULT]:
            self.assertIn(advice[0], icons.ICONS, advice[1])
        source = (Path(settings.BASE_DIR) / "aso/services.py").read_text(encoding="utf-8")
        names = re.findall(r'"icon": "([^"]*)"', source)
        self.assertTrue(names)
        for name in names:
            self.assertIn(name, icons.ICONS)

    def test_an_icon_is_svg_in_the_text_colour_and_an_unknown_name_draws_nothing(self):
        from aso import icons

        for name in icons.ICONS:
            markup = icons.svg(name)
            self.assertTrue(markup.startswith("<svg") and 'stroke="currentColor"' in markup, name)
            self.assertIn('aria-hidden="true"', markup)
        self.assertEqual(icons.svg("\U0001F3AF"), "")
        self.assertEqual(icons.svg(""), "")

    def test_stored_emoji_become_names(self):
        from aso.icons import upgrade_stored

        stored = {"insights": [{"icon": "\U0001F3AF", "type": "opportunity", "text": "No competitors"},
                               {"icon": "⚠️", "type": "barrier", "text": "2 apps"}],
                  "opportunity_signals": [{"signal": "Cross-Genre", "icon": "\U0001F500"}],
                  "targeting": {"icon": "\U0001F3AF", "label": "Sweet Spot"},
                  "app_icon": "https://example.com/a.png"}
        value, changed = upgrade_stored(stored)
        self.assertTrue(changed)
        self.assertEqual([i["icon"] for i in value["insights"]], ["title", "building"])
        self.assertEqual(value["opportunity_signals"][0]["icon"], "mixed")
        self.assertEqual(value["targeting"]["icon"], "target")
        self.assertEqual(value["app_icon"], "https://example.com/a.png")
        self.assertEqual(upgrade_stored(value), (value, False))


class StoredIconsRenamedTest(TestCase):
    """The 3.2.1 updates rename the emoji older versions stored, on every row
    that holds one (aso 0027, aso_pro 0034), and leave the rest alone."""

    def test_the_migrations_rename_stored_emoji(self):
        from importlib import import_module

        from django.apps import apps

        from aso.models import App, Keyword, OpportunityScan, OpportunityScanResult, SearchResult

        old = {"insights": [{"icon": "\U0001F3E2", "type": "barrier", "text": "1 app"}],
               "opportunity_signals": [{"signal": "Active Market", "icon": "\U0001F195"}]}
        keyword = Keyword.objects.create(keyword="sleep", app=App.objects.create(name="A"))
        stored = SearchResult.objects.create(keyword=keyword, country="us", difficulty_score=40,
                                             difficulty_breakdown=old)
        plain = SearchResult.objects.create(keyword=keyword, country="gb", difficulty_score=40,
                                            difficulty_breakdown={"download_estimates": {"low": 1}})
        scan = OpportunityScan.objects.create(keyword="sleep", countries=["us"])
        scanned = OpportunityScanResult.objects.create(scan=scan, country="us", order_index=0,
                                                       keyword_text="sleep", difficulty_breakdown=old)

        import_module("aso.migrations.0027_icon_names").name_the_icons(apps, None)

        stored.refresh_from_db()
        scanned.refresh_from_db()
        plain.refresh_from_db()
        for row in (stored, scanned):
            self.assertEqual(row.difficulty_breakdown["insights"][0]["icon"], "building")
            self.assertEqual(row.difficulty_breakdown["opportunity_signals"][0]["icon"], "sparkles")
        self.assertEqual(plain.difficulty_breakdown, {"download_estimates": {"low": 1}})

    def test_an_ai_runs_rows_are_renamed(self):
        from importlib import import_module

        from django.apps import apps

        if not apps.is_installed("aso_pro"):
            self.skipTest("the free edition has no AI runs")
        from aso_pro.models import AIResearchSession

        row = {"keyword": "sleep", "difficulty_breakdown": {"insights": [{"icon": "\U0001F512", "text": "x"}]},
               "targeting": {"icon": "\U0001F3AF", "label": "Sweet Spot"}}
        session = AIResearchSession.objects.create(session_type="keyword", country="us", llm_provider="openai",
                                                   llm_model="m", keyword_results=[row])

        import_module("aso_pro.migrations.0034_icon_names").name_the_icons(apps, None)

        session.refresh_from_db()
        self.assertEqual(session.keyword_results[0]["difficulty_breakdown"]["insights"][0]["icon"], "lock")
        self.assertEqual(session.keyword_results[0]["targeting"]["icon"], "target")
