"""The sample-data previews of the three AI tools (aso/ai_tools_preview.py,
aso/templates/aso/ai_tool_preview.html, docs/development/PRO_PREVIEWS_PLAN.md).

AI Researcher, AI Competitor and the ASO Simulator, opened without Pro, show
the real result page with invented results, the keywords locked and the same
two clear buttons at the top, over the table and at the bottom. These tests
pin what the owner asked for (sample results on every locked Pro screen, and
call to action buttons nobody can miss) and what must never happen: a sample
name on a live page, a live control in a preview, or the old buying words.
"""

import os
import re
from pathlib import Path

from django.conf import settings
from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase

from aso import ai_tools_preview as preview
from aso.links import PRICING_URL, PRO_PAGE_URL, RENEW_URL
from aso.scoring import GOOD_TARGET_SCORE, SUPPORTING_SCORE, SWEET_SPOT_SCORE

# The buying words of the old locked and promo pages (UI_REDESIGN_PLAN.md
# 11.3): every preview now says the same few words from aso.pro_preview_words.
OLD_BUYING_WORDS = (
    "Get a Pro License", "Buy a license", "Already have a key? Activate",
    "License Required", "LICENSE REQUIRED", "Download RespectASO Pro (.dmg)",
    "Go Pro: let AI", "Automate your ASO with Pro", "Renew License",
)


def _render(feature, state, license_url="/lic/"):
    return render_to_string("aso/ai_tool_preview.html",
                            preview.context(feature, state, license_url=license_url))


def _visible(html):
    body = html.split("<main", 1)[-1]
    body = re.sub(r"<script\b.*?</script>", " ", body, flags=re.DOTALL)
    return re.sub(r"<[^>]+>", " ", body)


class SampleDataTest(TestCase):
    def test_every_feature_and_state_builds(self):
        for feature in preview.FEATURES:
            for state in preview.STATES:
                ctx = preview.context(feature, state, license_url="/lic/")
                self.assertTrue(ctx["page_title"])
                self.assertTrue(ctx["cta"]["primary_label"])
                self.assertEqual(len(ctx["sample"]["verdict"]["best"]), 5,
                                 "the owner asked for the five best keywords")

    def test_unknown_feature_or_state_is_rejected(self):
        with self.assertRaises(ValueError):
            preview.context("nope", preview.STATE_FREE)
        with self.assertRaises(ValueError):
            preview.context("researcher", "nope")

    def test_every_tag_is_the_range_its_opportunity_falls_in(self):
        """Invented rows still follow aso.scoring.classify_keyword, so the
        preview never teaches a tag rule the real page breaks."""
        for keyword, _pop, _diff, opportunity, _cell, at_top, tag, _rank in preview.SAMPLE_ROWS:
            with self.subTest(keyword=keyword):
                if tag == "Low Volume":
                    self.assertIn("at most 0.", at_top)
                    continue
                expected = ("Sweet Spot" if opportunity >= SWEET_SPOT_SCORE else
                            "Good Target" if opportunity >= GOOD_TARGET_SCORE else
                            "Supporting" if opportunity >= SUPPORTING_SCORE else
                            "Worth Climbing")
                self.assertEqual(tag, expected)

    def test_the_counts_add_up(self):
        self.assertEqual(sum(n for _d, n in preview.SAMPLE_COUNTS), preview.SAMPLE_TOTAL)

    def test_competitor_best_keywords_are_ones_the_rival_ranks_for(self):
        best = preview.context("competitor", preview.STATE_FREE)["sample"]["verdict"]["best"]
        self.assertTrue(all(row["competitor_rank"] for row in best))
        self.assertEqual([r["opportunity"] for r in best],
                         sorted((r["opportunity"] for r in best), reverse=True))


class PreviewPageTest(TestCase):
    def test_keywords_are_locked_and_unselectable(self):
        for feature in ("researcher", "competitor"):
            html = _render(feature, preview.STATE_UNLICENSED)
            with self.subTest(feature=feature):
                self.assertEqual(html.count("preview-term blur-sm"), len(preview.SAMPLE_ROWS))
                self.assertEqual(html.count("blur-sm select-none"), 5)
                self.assertIn('class="space-y-4 select-none"', html)

    def test_nothing_in_the_sample_is_live(self):
        """A control whose function is empty is a lie: the sample has no
        buttons, forms or inputs, and every link is a call to action."""
        for feature in preview.FEATURES:
            for state in preview.STATES:
                html = _render(feature, state)
                main = html.split("<main", 1)[-1].split("</main>", 1)[0]
                sample = main.split("data-preview-hero", 1)[-1].split("data-preview-footer", 1)[0]
                with self.subTest(feature=feature, state=state):
                    for tag in ("<form", "<input", "<select", "<textarea", "data-switch-tab", "onclick="):
                        self.assertNotIn(tag, sample)
                    self.assertNotIn("<button", sample)

    def test_three_clear_calls_to_action(self):
        """The buttons sit at the top, over the locked table and at the
        bottom, wherever the reader stops scrolling."""
        for feature in preview.FEATURES:
            for state, url in ((preview.STATE_FREE, PRO_PAGE_URL),
                               (preview.STATE_UNLICENSED, PRICING_URL),
                               (preview.STATE_EXPIRED, RENEW_URL)):
                html = _render(feature, state)
                with self.subTest(feature=feature, state=state):
                    self.assertEqual(html.count("data-preview-primary"), 3)
                    self.assertEqual(html.count(f'href="{url}"'), 3)
                    if state == preview.STATE_FREE:
                        self.assertNotIn("data-preview-secondary", html)
                    else:
                        self.assertEqual(html.count('href="/lic/" data-preview-secondary'), 3)

    def test_labelled_as_sample_data(self):
        html = _render("researcher", preview.STATE_FREE)
        self.assertIn("Preview with sample data", html)
        self.assertIn("keywords locked", html)

    def test_copy_says_locked_not_blurred(self):
        for feature in preview.FEATURES:
            for state in preview.STATES:
                with self.subTest(feature=feature, state=state):
                    self.assertNotIn("blur", _visible(_render(feature, state)).lower())

    def test_each_tool_has_its_demo(self):
        for feature in preview.FEATURES:
            self.assertIn("Watch the demo", _render(feature, preview.STATE_FREE))

    def test_template_needs_no_pro_url(self):
        """Synced to the public repo, where aso_pro routes do not exist."""
        for feature in preview.FEATURES:
            self.assertNotIn("aso_pro:", _render(feature, preview.STATE_FREE, license_url=None))


class FreeEditionPagesTest(TestCase):
    """The free edition's three AI tabs. In the Pro build the aso_pro routes
    shadow the same paths, so the views are called directly."""

    def test_each_tab_is_the_preview(self):
        from aso import views

        for view, title in ((views.pro_promo_researcher_view, "AI Niche Researcher"),
                            (views.pro_promo_competitor_view, "AI Competitor Analyzer"),
                            (views.pro_promo_simulator_view, "ASO Score Simulator")):
            response = view(RequestFactory().get("/"))
            with self.subTest(title=title):
                self.assertContains(response, title)
                self.assertContains(response, "Get Pro for Mac")
                self.assertContains(response, PRO_PAGE_URL)
                self.assertNotContains(response, "I have a license key")


class OldBuyingWordsTest(TestCase):
    """No template, script or view still says the old words."""

    ROOTS = ("aso/templates", "aso_pro/templates", "_public_overrides", "static/js",
             "aso", "aso_pro", "licensing")

    def test_gone_everywhere(self):
        skip = os.path.join("aso", "tests")
        for root in self.ROOTS:
            for dirpath, _dirs, files in os.walk(os.path.join(settings.BASE_DIR, root)):
                if f"{os.sep}tests" in dirpath or skip in dirpath:
                    continue
                for name in files:
                    if not name.endswith((".html", ".js", ".py")):
                        continue
                    path = os.path.join(dirpath, name)
                    text = Path(path).read_text(encoding="utf-8")
                    for words in OLD_BUYING_WORDS:
                        with self.subTest(file=path, words=words):
                            self.assertNotIn(words, text)


class SampleNamesNeverReachLivePagesTest(TestCase):
    """The invented keywords and app names belong to the previews only."""

    def test_live_templates_do_not_carry_the_invented_apps(self):
        """The invented app names, that is: a sample keyword such as
        "morning routine" is an ordinary example elsewhere too."""
        allowed = {"ai_tool_preview.html"}
        names = {preview.APP_NAME, preview.COMPETITOR_NAME}
        for root in ("aso/templates", "aso_pro/templates", "static/js"):
            for dirpath, _dirs, files in os.walk(os.path.join(settings.BASE_DIR, root)):
                for name in files:
                    if name in allowed:
                        continue
                    text = Path(dirpath, name).read_text(encoding="utf-8")
                    for sample in names:
                        with self.subTest(file=name, sample=sample):
                            self.assertNotIn(sample, text)

    def _pro_pages(self):
        from django.apps import apps as django_apps
        from django.urls import reverse

        if not django_apps.is_installed("aso_pro"):
            return []   # the free edition has no Pro routes
        return [reverse(f"aso_pro:{name}") for name in ("ai_researcher", "ai_competitor", "simulator")]

    def test_licensed_pages_do_not_render_them(self):
        from django.test import Client, override_settings

        with override_settings(DEBUG_SKIP_LICENSE=True):
            for path in self._pro_pages():
                html = Client().get(path).content.decode()
                with self.subTest(path=path):
                    self.assertNotIn("data-preview-hero", html)
                    for sample in preview.sample_names():
                        self.assertNotIn(sample, html)

    def test_unlicensed_pages_are_the_preview_with_a_key_button(self):
        from unittest.mock import patch

        from django.test import Client, override_settings
        from django.urls import reverse

        with override_settings(DEBUG_SKIP_LICENSE=False), \
                patch("aso.ai_tools_preview.state_of", return_value=preview.STATE_UNLICENSED):
            for path in self._pro_pages():
                response = Client().get(path)
                with self.subTest(path=path):
                    self.assertContains(response, "data-preview-hero")
                    self.assertContains(response, "Get RespectASO Pro")
                    self.assertContains(response, f'href="{reverse("aso_pro:settings_license")}" data-preview-secondary', 3)


class NoPriceTest(TestCase):
    """A preview never names a price: the pricing page fetches it live, and
    any amount written here would be wrong the day the price changes."""

    PRICE = re.compile(r"[$€£]\s?\d|\d\s?(?:USD|EUR|SEK|kr)\b|per (?:month|year)|/(?:month|year|mo|yr)\b", re.IGNORECASE)

    def test_no_preview_names_a_price(self):
        from aso import rival_tracker_preview, top_terms_preview

        pages = [_render(feature, state) for feature in preview.FEATURES for state in preview.STATES]
        pages += [render_to_string("aso/top_terms_preview.html",
                                   top_terms_preview.preview_context(state, license_url="/lic/"))
                  for state in top_terms_preview.STATES]
        pages += [render_to_string("aso/rival_tracker_preview.html",
                                   rival_tracker_preview.preview_context(state, license_url="/lic/"))
                  for state in rival_tracker_preview.STATES]
        for html in pages:
            self.assertIsNone(self.PRICE.search(_visible(html)))
