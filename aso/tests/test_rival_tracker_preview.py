"""The sample-data preview of the Rival Tracker (aso/rival_tracker_preview.py
and its templates): what the free edition and an unlicensed Pro install see.

Free-tier test: no aso_pro import.
"""

import re

from django.template.loader import render_to_string
from django.test import RequestFactory, TestCase
from django.urls import reverse

from aso import rival_tracker_preview as preview


class SampleDataTest(TestCase):
    def test_every_state_builds_a_complete_context(self):
        for state in preview.STATES:
            ctx = preview.preview_context(state, license_url="/lic/")["preview"]
            self.assertEqual(len(ctx["columns"]), len(preview.SAMPLE_APPS))
            self.assertEqual(len(ctx["rows"]), len(preview.SAMPLE_ROWS))
            for row in ctx["rows"]:
                self.assertEqual(len(row["cells"]), len(preview.SAMPLE_APPS))
            for key in ("eyebrow", "headline", "body", "primary_label", "primary_url", "table_line"):
                self.assertTrue(ctx["cta"][key], f"{state}: empty {key}")
            self.assertEqual(bool(ctx["cta"]["secondary_label"]), bool(ctx["cta"]["secondary_url"]))

    def test_unknown_state_is_rejected(self):
        with self.assertRaises(ValueError):
            preview.preview_context("nope")

    def test_the_tracked_app_comes_first(self):
        self.assertTrue(preview.SAMPLE_APPS[0][1])
        self.assertFalse(any(tracked for _name, tracked in preview.SAMPLE_APPS[1:]))


class PreviewPartialTest(TestCase):
    def render(self, state=preview.STATE_FREE):
        return render_to_string("aso/partials/rival_tracker_preview.html",
                                preview.preview_context(state, license_url="/lic/"))

    def test_every_name_and_keyword_is_locked(self):
        html = self.render()
        visible = re.sub(r'<span class="[^"]*preview-term blur-sm[^"]*">[^<]*</span>', "", html)
        for name in preview.sample_names():
            self.assertNotIn(f">{name}<", visible, name)

    def test_nothing_in_the_preview_is_live(self):
        html = self.render()
        for tag in ("<button", "<form", "<input", "<select", "onclick="):
            self.assertNotIn(tag, html)
        # The only links are the call to action over the locked table.
        self.assertEqual(html.count("<a "), html.count("data-preview-primary") + html.count("data-preview-secondary"))

    def test_columns_match_the_shared_header(self):
        html = self.render()
        first_row = html.split("<tbody>")[1].split("</tr>")[0]
        self.assertEqual(first_row.count("<td "), len(preview.SAMPLE_APPS) + 3)

    def test_copy_says_locked_not_blurred(self):
        for state in preview.STATES:
            visible = re.sub(r"<[^>]+>", " ", self.render(state))
            self.assertNotIn("blur", visible.lower(), state)
            self.assertIn("unlock", preview.preview_context(state)["preview"]["cta"]["table_line"].lower())


class FreeEditionPageTest(TestCase):
    """aso:pro_promo_rival_tracker. In the Pro build the aso_pro route shadows
    the same path, so the view is called directly."""

    def test_renders_the_preview_with_a_buy_pro_cta(self):
        from aso.views import pro_promo_rival_tracker_view

        response = pro_promo_rival_tracker_view(RequestFactory().get(reverse("aso:pro_promo_rival_tracker")))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Rival Tracker")
        self.assertContains(response, "Know the day a rival passes you")
        self.assertContains(response, "Get Pro for Mac")
        self.assertContains(response, "Preview with sample data")
        self.assertNotContains(response, "I have a license key")

    def test_template_needs_no_pro_url(self):
        html = render_to_string("aso/rival_tracker_preview.html", preview.preview_context(preview.STATE_FREE))
        self.assertNotIn("aso_pro:", html)
