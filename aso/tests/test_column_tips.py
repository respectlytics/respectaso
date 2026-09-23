"""Every keyword table explains its columns from one place (aso/column_tips.py),
and the Opportunity column says whose score its first number is.

Free-tier test: the Dashboard and the Country Opportunity Finder are in the
public edition too.
"""

import os
import re

from django.conf import settings
from django.test import SimpleTestCase

from aso.column_tips import COLUMN_TIPS, OPPORTUNITY_SUBJECTS, OPPORTUNITY_TIPS, opportunity_subline


class ColumnTipsTest(SimpleTestCase):
    def test_no_screen_calls_the_first_number_now(self):
        # "now" is wrong for a brand new app, and the Dashboard's first number
        # is where an app can realistically rank, not where it ranks today.
        roots = ["aso/templates", "aso_pro/templates", "static/js"]
        for root in roots:
            for folder, _dirs, files in os.walk(os.path.join(settings.BASE_DIR, root)):
                for name in files:
                    if not name.endswith((".html", ".js")):
                        continue
                    path = os.path.join(folder, name)
                    text = open(path, encoding="utf-8").read()
                    with self.subTest(file=path):
                        self.assertNotRegex(text, r"now (→|&rarr;) at #1|, now and at #1")

    def test_every_tip_is_plain_prose(self):
        for text in [*COLUMN_TIPS.values(), *OPPORTUNITY_TIPS.values()]:
            with self.subTest(tip=text[:40]):
                self.assertNotRegex(text, "[—–]")

    def test_every_subject_has_a_tip_and_words(self):
        self.assertEqual(set(OPPORTUNITY_SUBJECTS), set(OPPORTUNITY_TIPS))
        self.assertEqual(opportunity_subline("new_app"), "for a new app")
        self.assertEqual(opportunity_subline("tracked"), "for the app under each keyword")
        self.assertEqual(opportunity_subline("new_app", "Calm Minutes: Sleep"), "for Calm Minutes")

    def test_every_column_a_template_names_has_a_tip(self):
        used = set()
        for root in ("aso/templates", "aso_pro/templates"):
            for folder, _dirs, files in os.walk(os.path.join(settings.BASE_DIR, root)):
                for name in files:
                    text = open(os.path.join(folder, name), encoding="utf-8").read()
                    used |= set(re.findall(r'{% column_info "(\w+)"', text))
        self.assertTrue(used)
        self.assertEqual(used - set(COLUMN_TIPS) - {"opportunity"}, set())


class EveryScoreIsColouredOnTheTagBarsTest(SimpleTestCase):
    """The colour of an Opportunity score follows the same bars as the tags,
    and the colour of a difficulty follows its band, on every table. The AI
    tabs coloured both on cut-offs of their own (2026-09-23)."""

    def test_opportunity_colours_change_exactly_at_the_tag_bars(self):
        from aso.scoring import GOOD_TARGET_SCORE, SUPPORTING_SCORE, SWEET_SPOT_SCORE, opportunity_css

        changes = [score for score in range(1, 101)
                   if opportunity_css(score) != opportunity_css(score - 1)]
        self.assertEqual(changes, [SUPPORTING_SCORE, GOOD_TARGET_SCORE, SWEET_SPOT_SCORE])

    def test_difficulty_chips_follow_the_bands(self):
        from aso.scoring import DIFFICULTY_BANDS, DIFFICULTY_CHIP, difficulty_chip, difficulty_color

        self.assertEqual(set(DIFFICULTY_CHIP), {band[1] for band in DIFFICULTY_BANDS})
        for score in range(0, 101):
            # The chip carries the band's own text colour; only Extreme's
            # darkest red is lifted one step, to read on its tint.
            text = difficulty_color(score)
            expected = "text-red-500" if text == "text-red-600" else text
            self.assertIn(expected, difficulty_chip(score).split(), score)


class TheTooltipBelongsToOneElementTest(SimpleTestCase):
    """static/js/tooltip.js: on a short, filtered table a row's tooltip
    opened over the rows below and, being hoverable, kept showing that row's
    text while the pointer sat on another row ("ranks #2" over a row that
    ranks #14, 2026-09-23). Browser checks drove the fix; this keeps its
    three parts in place."""

    def setUp(self):
        self.js = open(os.path.join(settings.BASE_DIR, "static/js/tooltip.js")).read()

    def test_it_never_catches_the_mouse(self):
        self.assertIn("tip.style.pointerEvents = 'none'", self.js)
        self.assertNotIn("tip.style.pointerEvents = 'auto'", self.js)
        self.assertNotIn("tip.addEventListener('mouseenter'", self.js)

    def test_it_closes_when_its_element_is_gone(self):
        self.assertIn("MutationObserver", self.js)
        self.assertIn("!current.isConnected", self.js)

    def test_it_closes_when_its_element_moves(self):
        self.assertIn("document.addEventListener('scroll'", self.js)
        self.assertIn("rect.top - anchor.top", self.js)
