"""No uppercase, letter-spaced labels anywhere in the app.

The loheden-design skill bans uppercase, letter-spaced labels and eyebrows
(No machine-made look): they are one of the most recognisable signs of a
machine-made screen, and a label in capitals is harder to read than the same
words in sentence case. Until 2026-11-18 the app had them in table headers
(Rival Tracker, Top Terms), section labels ("Recent simulations"), the leader
cards of Sales & growth, the word before each section's tabs and some thirty
other places. Labels are now in sentence case with the existing type
hierarchy (section-label in static/css/tailwind.source.css), and this test
fails on the classes and CSS that would bring the look back: Tailwind's
``uppercase`` and wide ``tracking-*``, and ``text-transform: uppercase`` or a
positive ``letter-spacing`` in a stylesheet or inline style.

A real exception names its file, the text matched and why in ALLOWED; a
second test fails when an exception no longer matches anything, so a dead
one cannot reopen the door.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BASE = Path(settings.BASE_DIR)
# Every source of what a person reads: templates, scripts, the stylesheet
# source and the Python that emits class names.
ROOTS = ("aso/templates", "aso_pro/templates", "_public_overrides", "static/js", "aso", "aso_pro",
         "licensing", "llm_providers", "core", "desktop")
FILES = ("static/css/tailwind.source.css",)
SUFFIXES = {".html", ".js", ".css", ".py"}
SKIP_PARTS = {"tests", "migrations", "vendor", "__pycache__"}

BANNED = (
    (re.compile(r"(?<![\w:-])uppercase(?![\w-])"), "Tailwind uppercase"),
    (re.compile(r"(?<![\w-])tracking-(?:wide|wider|widest|\[(?!-)[^\]]+\])(?![\w-])"), "wide letter-spacing"),
    (re.compile(r"text-transform\s*:\s*uppercase"), "text-transform: uppercase"),
    (re.compile(r"letter-spacing\s*:\s*(?!-|0[;\s}\"'])[0-9.]"), "positive letter-spacing"),
)

# (file, matched text, why it may stay)
ALLOWED = (
    ("aso_pro/language_validation.py", "uppercase", "a docstring about capitalised words, not a class"),
)


def _sources():
    for root in ROOTS:
        base = BASE / root
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.suffix not in SUFFIXES or not path.is_file() or SKIP_PARTS & set(path.parts):
                continue
            yield path
    for rel in FILES:
        yield BASE / rel


def _hits():
    seen = set()
    for path in _sources():
        rel = str(path.relative_to(BASE))
        if rel in seen:
            continue
        seen.add(rel)
        text = path.read_text(encoding="utf-8")
        for pattern, what in BANNED:
            for match in pattern.finditer(text):
                line = text.count("\n", 0, match.start()) + 1
                yield rel, line, match.group(0), what


class NoUppercaseLabelsTest(SimpleTestCase):
    def test_no_uppercase_or_letter_spaced_text(self):
        allowed = {(f, m) for f, m, _why in ALLOWED}
        bad = [f"{rel}:{line}: {what} ({text})" for rel, line, text, what in _hits()
               if (rel, text) not in allowed]
        self.assertEqual(bad, [], "Use sentence case with the type hierarchy (section-label), "
                                  "never capitals or wide letter-spacing (loheden-design, No machine-made look).")

    def test_every_exception_still_matches_something(self):
        found = {(rel, text) for rel, _line, text, _what in _hits()}
        # The free edition carries no aso_pro, so an exception there is not dead in it.
        dead = [f"{f}: {m}" for f, m, _why in ALLOWED if (BASE / f).exists() and (f, m) not in found]
        self.assertEqual(dead, [])

    def test_the_section_label_is_in_sentence_case(self):
        css = (BASE / "static/css/tailwind.source.css").read_text(encoding="utf-8")
        rule = re.search(r"\.section-label \{ @apply ([^;]*);", css).group(1).split()
        self.assertNotIn("uppercase", rule)
        self.assertFalse([w for w in rule if w.startswith("tracking-")])

    def test_the_section_tab_bars_carry_no_word_the_top_bar_already_says(self):
        """Discover and Store are underlined in the top bar right above their
        tabs, so no word repeats them before the tabs; Settings keeps its word,
        in sentence case, since the top bar shows only a gear for it."""
        tabs = (BASE / "aso/templates/aso/partials/section_tabs.html").read_text(encoding="utf-8")
        preview = (BASE / "aso/templates/aso/partials/store_preview_tabs.html").read_text(encoding="utf-8")
        for word in (">Discover<", ">Store<"):
            self.assertNotIn(word, tabs)
            self.assertNotIn(word, preview)
        self.assertIn('<span class="mr-1 text-sm text-slate-400">Settings</span>', tabs)
