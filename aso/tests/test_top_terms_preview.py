"""Tests for the sample-data preview of Top Search Terms
(aso/top_terms_preview.py + aso/templates/aso/top_terms_preview.html).

The free edition renders it from aso.views.pro_promo_top_terms_view; the
Pro view renders the same page for the unlicensed / not-connected states
(covered in aso_pro/tests/test_top_terms.py). These tests pin the parts
both editions rely on: the sample data's shape, the blur, the absence of
anything live, and the column parity with the shared header partial.
"""

import json
import re

from django.template.loader import render_to_string
from django.test import TestCase
from django.urls import reverse

from aso import top_terms_preview as preview
from aso.tests.test_popularity_views import PopularityViewTestBase


class SampleDataTest(TestCase):
    def test_every_state_builds_a_complete_context(self):
        for state in preview.STATES:
            ctx = preview.preview_context(state, license_url="/lic/")["preview"]
            self.assertEqual(ctx["state"], state)
            self.assertEqual(len(ctx["rows"]), len(preview.SAMPLE_ROWS))
            self.assertEqual(len(ctx["risers"]), len(preview.SAMPLE_RISERS))
            self.assertEqual(len(ctx["fallers"]), len(preview.SAMPLE_FALLERS))
            self.assertEqual(
                len(ctx["new_terms"]), len(preview.SAMPLE_NEW_TERMS)
            )
            cta = ctx["cta"]
            for key in ("eyebrow", "headline", "body", "primary_label",
                        "primary_url", "primary_tone", "table_line"):
                self.assertTrue(cta[key], f"{state}: empty {key}")
            self.assertIn(cta["primary_tone"], ("purple", "amber", "sky"))
            # A secondary link is either complete or absent.
            self.assertEqual(
                bool(cta["secondary_label"]), bool(cta["secondary_url"]),
                state,
            )

    def test_unknown_state_is_rejected(self):
        with self.assertRaises(ValueError):
            preview.preview_context("nope")

    def test_rows_use_apples_scales(self):
        """Invented values still respect Apple's real scales, so the
        preview teaches the right ranges (popularity floor ~40, tiers 1-5,
        in-category value never below the storefront-wide one's rank
        logic: #1 in a category is 100 within it)."""
        for row in preview.sample_rows():
            self.assertTrue(40 <= row["popularity"] <= 100, row)
            self.assertTrue(1 <= row["tier"] <= 5, row)
            self.assertTrue(1 <= row["popularity_in_genre"] <= 100, row)
            if row["rank"] == 1:
                self.assertEqual(row["popularity_in_genre"], 100, row)
            self.assertTrue(row["genre_pretty"], row)
            self.assertFalse(row["tracked"])

    def test_ranks_consistent_within_each_category(self):
        """Within a category, a higher popularity must not carry a worse
        rank - the sample must never contradict the column explainer."""
        by_genre = {}
        for term, genre, rank, pop, *_ in preview.SAMPLE_ROWS:
            by_genre.setdefault(genre, []).append((rank, pop))
        for genre, pairs in by_genre.items():
            ordered = sorted(pairs)
            pops = [pop for _rank, pop in ordered]
            self.assertEqual(pops, sorted(pops, reverse=True), genre)

    def test_sparkline_series_ends_on_the_shown_trend(self):
        for row in preview.sample_rows():
            series = json.loads(row["series_json"])
            self.assertEqual(len(series), preview.SPARKLINE_WEEKS)
            values = [v for _week, v in series]
            self.assertEqual(values[-1], row["popularity"])
            self.assertEqual(values[-1] - values[-2], row["trend"])
            self.assertTrue(all(40 <= v <= 100 for v in values), row["term"])
            # ISO week dates, ascending, one week apart.
            weeks = [w for w, _v in series]
            self.assertEqual(weeks, sorted(weeks))
            self.assertTrue(all(re.match(r"\d{4}-\d{2}-\d{2}$", w) for w in weeks))

    def test_sample_terms_cover_every_list(self):
        terms = preview.sample_terms()
        self.assertIn("photo editor", terms)
        self.assertIn("ai photo generator", terms)     # riser
        self.assertIn("weather radar", terms)          # faller
        self.assertIn("receipt scanner", terms)        # new this week


class PreviewPartialTest(TestCase):
    """The preview partial rendered on its own."""

    def _render(self, state=preview.STATE_FREE):
        return render_to_string(
            "aso/partials/top_terms_preview.html",
            preview.preview_context(state, license_url="/lic/"),
        )

    def test_every_term_is_blurred_and_unselectable(self):
        html = self._render()
        ctx = preview.preview_context(preview.STATE_FREE)["preview"]
        expected = (
            len(ctx["rows"]) + len(ctx["risers"]) + len(ctx["fallers"])
            + len(ctx["new_terms"])
        )
        self.assertEqual(html.count('class="preview-term blur-sm"'), expected)
        # The blurred text is never selectable / copyable.
        self.assertGreaterEqual(html.count("select-none"), 3)

    def test_nothing_in_the_preview_is_live(self):
        """A control whose function is empty is a lie - the preview has no
        buttons, forms, or handlers at all; the only link is the CTA."""
        html = self._render()
        self.assertNotIn("<button", html)
        self.assertNotIn("<form", html)
        self.assertNotIn("onclick=", html)
        self.assertNotIn("<select", html)
        self.assertNotIn("<input", html)
        self.assertEqual(html.count("<a "), 1)
        self.assertIn(preview.PRICING_URL, html)

    def test_columns_match_the_shared_header(self):
        """Row cells line up with the header partial the live page uses."""
        html = self._render()
        head = html.split("<thead>")[1].split("</thead>")[0]
        header_cells = head.count("<th ")
        first_row = html.split("<tbody")[1].split("</tr>")[0]
        self.assertEqual(first_row.count("<td "), header_cells)
        self.assertEqual(header_cells, 7)

    def test_rows_carry_sparkline_payloads(self):
        html = self._render()
        self.assertEqual(
            html.count('class="spark-btn'), len(preview.SAMPLE_ROWS)
        )
        self.assertEqual(html.count("data-series="), len(preview.SAMPLE_ROWS))

    def test_labelled_as_sample_data(self):
        html = self._render()
        self.assertIn("Preview with sample data", html)
        self.assertIn("search terms locked", html)

    def test_copy_says_locked_not_blurred(self):
        """Product wording: the terms are 'locked' and the CTA 'unlocks'
        them - 'blurred' is the visual, never the message."""
        for state in preview.STATES:
            html = self._render(state)
            visible = re.sub(r"<[^>]+>", " ", html)
            self.assertNotIn("blur", visible.lower(), state)
            cta = preview.preview_context(state)["preview"]["cta"]
            for key in ("headline", "body", "table_line"):
                self.assertNotIn("blur", cta[key].lower(), f"{state}.{key}")
            self.assertIn("unlock", cta["table_line"].lower(), state)

    def test_cta_line_follows_the_state(self):
        self.assertIn("Connect Apple Ads to unlock",
                      self._render(preview.STATE_NOT_CONNECTED))
        self.assertIn("Renew your license", self._render(preview.STATE_EXPIRED))
        self.assertIn("first sync", self._render(preview.STATE_SYNCING))


class FreeEditionPageTest(PopularityViewTestBase):
    """aso:pro_promo_top_terms - the Top Terms tab in the free edition.

    In the Pro build the aso_pro route shadows the same path, so the view
    is called directly instead of through the URL."""

    def test_renders_the_preview_with_a_buy_pro_cta(self):
        from django.test import RequestFactory

        from aso.views import pro_promo_top_terms_view

        request = RequestFactory().get(reverse("aso:pro_promo_top_terms"))
        response = pro_promo_top_terms_view(request)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Top Search Terms")
        self.assertContains(response, "unlock with RespectASO Pro")
        self.assertContains(response, "Get RespectASO Pro")
        self.assertContains(response, preview.PRICING_URL)
        self.assertContains(response, "Preview with sample data")
        self.assertContains(response, "preview-term blur-sm")
        self.assertContains(response, "js/sparkline.js")
        # The value points sit above the preview.
        self.assertContains(response, "Apple's official ranking")
        self.assertContains(response, "Weekly movement")
        self.assertContains(response, "One-click actions")
        # No license-activation link: the free edition has no license page.
        self.assertNotContains(response, "Activate it")

    def test_template_needs_no_pro_url(self):
        """The page template is synced to the public repo, where aso_pro
        routes do not exist - it must render with a bare context."""
        html = render_to_string(
            "aso/top_terms_preview.html",
            preview.preview_context(preview.STATE_FREE),
        )
        self.assertNotIn("aso_pro:", html)
        self.assertIn("Get RespectASO Pro", html)
