"""Keyword-in-title matching and highlighting.

Regression suite for a support case: for "scroll less" (US) the Dashboard
listed "ScrollLess: App Blocker" and "Scrollless - Screen Time Block" in
the top 5, yet the Top 5 card said "Only 1 of 5 apps use this keyword in
their title" (three did, two of them run together), and the highlighter
drew each run-together name as two padded chips that read "Scroll Less".

Guards three things:
  * run-together spellings count as the exact phrase (services),
  * the server filter and the shared JS highlighter emit identical HTML,
    with a run-together word drawn as ONE mark,
  * the tier cards and insights speak of "ratings" (the column they are
    read against is Ratings, i.e. userRatingCount), never "reviews".
"""

import json
import os
import shutil
import subprocess
import unittest

from django.conf import settings
from django.test import SimpleTestCase

from aso.services import DifficultyCalculator, _keyword_title_evidence, compound_form
from aso.templatetags.aso_tags import (
    _HL_CLS_ALL,
    _HL_CLS_EXACT,
    _HL_CLS_PART,
    highlight_keyword,
)

BASE_DIR = str(settings.BASE_DIR)
JS_PATH = os.path.join(BASE_DIR, "static", "js", "keyword-highlight.js")


def _mark(text, cls):
    return f'<mark class="{cls}">{text}</mark>'


# The real top 5 for "scroll less" (US, 2026-08-28) as Apple named them.
SCROLL_LESS_TOP5 = [
    ("Scroll Less Block Reels Shorts", 20, "2025-03-01T00:00:00Z"),
    ("ScrollLess: App Blocker", 0, "2026-03-01T00:00:00Z"),
    ("Scrollless - Screen Time Block", 1, "2026-06-01T00:00:00Z"),
    ("ClearSpace: Reduce Screen Time", 8807, "2021-07-01T00:00:00Z"),
    ("No Scroll - Limit Screen Time", 678, "2024-03-01T00:00:00Z"),
]


def _app(name, ratings, released, genre="Productivity"):
    return {
        "trackName": name,
        "userRatingCount": ratings,
        "averageUserRating": 4.5,
        "releaseDate": released,
        "currentVersionReleaseDate": released,
        "primaryGenreName": genre,
        "sellerName": f"{name} Inc",
    }


class CompoundFormTest(SimpleTestCase):
    def test_multi_word_keyword_runs_together(self):
        self.assertEqual(compound_form(["scroll", "less"]), "scrollless")

    def test_single_word_has_no_compound_form(self):
        self.assertEqual(compound_form(["scroll"]), "")


class TitleEvidenceCompoundTest(SimpleTestCase):
    def test_run_together_spelling_is_an_exact_phrase(self):
        for title in ("ScrollLess: App Blocker", "Scrollless - Screen Time Block"):
            ev = _keyword_title_evidence("scroll less", title, "Productivity")
            self.assertTrue(ev["exact_phrase"], title)
            self.assertEqual(ev["evidence"], 1.0, title)

    def test_spaced_phrase_is_still_exact(self):
        ev = _keyword_title_evidence("scroll less", "Holdout: Scroll Less Together")
        self.assertTrue(ev["exact_phrase"])

    def test_compound_must_start_a_word(self):
        # "smartapp" contains "artapp" but does not start with it.
        ev = _keyword_title_evidence("art app", "SmartApp Studio")
        self.assertFalse(ev["exact_phrase"])
        # A longer word that starts with the compound still counts.
        ev = _keyword_title_evidence("scroll less", "ScrollLessApp")
        self.assertTrue(ev["exact_phrase"])

    def test_unrelated_title_is_untouched(self):
        ev = _keyword_title_evidence("scroll less", "ClearSpace: Reduce Screen Time")
        self.assertFalse(ev["exact_phrase"])
        self.assertFalse(ev["all_words"])
        self.assertEqual(ev["evidence"], 0.0)


class TierTitleCountTest(SimpleTestCase):
    def setUp(self):
        self.apps = [_app(*row) for row in SCROLL_LESS_TOP5]
        _, self.breakdown = DifficultyCalculator().calculate(self.apps, "scroll less")
        self.top5 = self.breakdown["ranking_tiers"]["top_5"]

    def test_run_together_titles_are_counted(self):
        self.assertEqual(self.top5["title_keyword_count"], 3)
        self.assertIn(
            "3 of 5 apps already target this keyword in their title.",
            self.top5["highlights"],
        )

    def test_weakest_app_is_named_as_apple_names_it(self):
        self.assertEqual(self.top5["weakest_app"], "ScrollLess: App Blocker")
        self.assertIn("The easiest app to beat has just 0 ratings.", self.top5["highlights"])


class WordingTest(SimpleTestCase):
    """Every sentence on the difficulty card counts ratings, and uses a
    plain dash (the project bans the em dash in authored copy)."""

    def _all_texts(self, apps, keyword):
        _, breakdown = DifficultyCalculator().calculate(apps, keyword)
        texts = [i["text"] for i in breakdown["insights"]]
        texts += [s["detail"] for s in breakdown.get("opportunities", [])]
        for tier in breakdown["ranking_tiers"].values():
            texts += tier["highlights"]
        return texts

    def test_cards_say_ratings_never_reviews(self):
        fixtures = [
            [_app(*row) for row in SCROLL_LESS_TOP5],
            [_app(f"Giant {i}", 2_000_000 - i, "2015-01-01T00:00:00Z") for i in range(12)],
            [_app(f"Mid {i}", 5_000 + i, "2022-01-01T00:00:00Z") for i in range(8)],
            [_app("Only One", 3, "2026-01-01T00:00:00Z")],
        ]
        for apps in fixtures:
            for text in self._all_texts(apps, "scroll less"):
                self.assertNotIn("review", text.lower(), text)
                self.assertNotIn("—", text, text)


class HighlightFilterTest(SimpleTestCase):
    def test_run_together_word_is_one_green_mark(self):
        out = highlight_keyword("ScrollLess: App Blocker", "scroll less")
        self.assertEqual(out, _mark("ScrollLess", _HL_CLS_EXACT) + ": App Blocker")

    def test_spaced_phrase_is_green(self):
        out = highlight_keyword("Holdout: Scroll Less Together", "scroll less")
        self.assertEqual(out, "Holdout: " + _mark("Scroll Less", _HL_CLS_EXACT) + " Together")

    def test_adjacent_scattered_words_merge_into_one_mark(self):
        # Not the exact phrase, not its compound, but "Scroll" and "Less"
        # touch: they must be one chip, never two chips that read "Scroll Less".
        out = highlight_keyword("ScrollLess App", "scroll less app")
        self.assertEqual(
            out,
            _mark("ScrollLess", _HL_CLS_ALL) + " " + _mark("App", _HL_CLS_ALL),
        )
        self.assertNotIn("</mark><mark", out)

    def test_partial_overlap_is_slate(self):
        out = highlight_keyword("No Scroll - Limit Screen Time", "scroll less")
        self.assertEqual(out, "No " + _mark("Scroll", _HL_CLS_PART) + " - Limit Screen Time")

    def test_single_word_keyword_is_green(self):
        out = highlight_keyword("Freedom: Screen Time Control", "screen")
        self.assertEqual(out, "Freedom: " + _mark("Screen", _HL_CLS_EXACT) + " Time Control")

    def test_html_in_titles_is_escaped_everywhere(self):
        out = highlight_keyword("<b>Scroll</b> & \"Less\"", "scroll less")
        self.assertEqual(
            out,
            "&lt;b&gt;" + _mark("Scroll", _HL_CLS_ALL) + "&lt;/b&gt; &amp; &quot;"
            + _mark("Less", _HL_CLS_ALL) + "&quot;",
        )
        self.assertEqual(highlight_keyword("<i>x</i>", "scroll"), "&lt;i&gt;x&lt;/i&gt;")

    def test_empty_inputs(self):
        self.assertEqual(highlight_keyword("", "scroll"), "")
        self.assertEqual(highlight_keyword(None, "scroll"), "")
        self.assertEqual(highlight_keyword("Plain", ""), "Plain")
        self.assertEqual(highlight_keyword("Plain", "   "), "Plain")


class SharedHighlighterWiringTest(SimpleTestCase):
    """One JS highlighter, loaded where titles are rendered client-side;
    no inline copies that could drift from the server filter."""

    TEMPLATES = (
        "aso/templates/aso/dashboard.html",
        "aso/templates/aso/opportunity.html",
    )

    def test_templates_load_the_shared_script_and_define_no_copy(self):
        for rel in self.TEMPLATES:
            with open(os.path.join(BASE_DIR, rel)) as f:
                html = f.read()
            self.assertIn("js/keyword-highlight.js", html, rel)
            self.assertIn("highlightKeyword(", html, rel)
            self.assertNotIn("function highlightKeyword", html, rel)

    def test_js_and_python_share_the_tier_classes(self):
        with open(JS_PATH) as f:
            js = f.read()
        for cls in (_HL_CLS_EXACT, _HL_CLS_ALL, _HL_CLS_PART):
            self.assertIn(f"'{cls}'", js, cls)


@unittest.skipUnless(shutil.which("node"), "node is not installed")
class PythonJsParityTest(SimpleTestCase):
    """The server filter and the browser highlighter must emit the same HTML
    for the same title, or a row would change on refresh."""

    CASES = [
        ("ScrollLess: App Blocker", "scroll less"),
        ("Scrollless - Screen Time Block", "scroll less"),
        ("Scroll Less Block Reels Shorts", "scroll less"),
        ("Holdout: Scroll Less Together", "scroll less"),
        ("ClearSpace: Reduce Screen Time", "scroll less"),
        ("No Scroll - Limit Screen Time", "scroll less"),
        ("ScrollLess App", "scroll less app"),
        ("SmartApp Studio", "art app"),
        ("Freedom: Screen Time Control", "screen"),
        ("<b>Scroll</b> & \"Less\" 'x'", "scroll less"),
        ("C++ Compiler & IDE", "c++ ide"),
        ("Café Ménager", "café ménager"),
        ("ÉCOLE de Ski", "école ski"),
        ("Scroll Scroll Scroll", "scroll"),
        ("Untitled", ""),
    ]

    def test_same_html_from_both_renderers(self):
        script = (
            "const {highlightKeyword} = require(process.argv[1]);"
            "let input = '';"
            "process.stdin.on('data', d => input += d);"
            "process.stdin.on('end', () => {"
            "  const cases = JSON.parse(input);"
            "  console.log(JSON.stringify(cases.map(([t, k]) => highlightKeyword(t, k))));"
            "});"
        )
        proc = subprocess.run(
            ["node", "-e", script, JS_PATH],
            input=json.dumps(self.CASES),
            capture_output=True,
            text=True,
            check=True,
        )
        js_out = json.loads(proc.stdout)
        py_out = [str(highlight_keyword(t, k)) for t, k in self.CASES]
        for case, py, js in zip(self.CASES, py_out, js_out):
            self.assertEqual(py, js, case)
