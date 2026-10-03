"""A page reached from somewhere else leads back there
(aso/back_link.py, docs/development/BACK_LINK_PLAN.md).

The owner, 2026-10-02: on the Apple Ads setup guide there was no way back to
the screen before it, and the Mac app has no browser Back button.
"""

import re

from django.apps import apps as django_apps
from django.test import TestCase, override_settings
from django.urls import reverse

PRO = django_apps.is_installed("aso_pro")
LINK = re.compile(r'<a href="([^"]*)" data-back-link[^>]*>.*?Back to ([^<]+?)\s*</a>', re.DOTALL)


def back(html):
    found = LINK.findall(html)
    return found[0] if found else None


class TheWayBackTest(TestCase):
    def get(self, name, referer=None, **headers):
        if referer is not None:
            headers["HTTP_REFERER"] = referer
        return self.client.get(reverse(name), **headers).content.decode()

    @override_settings(DEBUG_SKIP_LICENSE=True)   # the Pro pages themselves, not their previews
    def test_every_detail_page_has_one_and_the_top_level_none(self):
        detail = ["aso:apple_ads_setup", "aso:methodology", "aso:whats_new", "aso:setup",
                  "aso:apps", "aso:settings_popularity"]
        top = ["aso:dashboard", "aso:opportunity"]
        if PRO:
            detail += ["aso_pro:settings_ai", "aso_pro:settings_license", "aso_pro:settings_mcp",
                       "aso_pro:settings_setup", "aso_pro:rival_setup"]
            top += ["aso_pro:ai_researcher", "aso_pro:ai_competitor", "aso_pro:top_terms",
                    "aso_pro:simulator", "aso_pro:rival_tracker"]
        for name in detail:
            with self.subTest(page=name):
                self.assertIsNotNone(back(self.get(name)))
        for name in top:
            with self.subTest(page=name):
                self.assertIsNone(back(self.get(name)))

    def test_opened_directly_it_leads_to_the_natural_parent(self):
        self.assertEqual(back(self.get("aso:apple_ads_setup")),
                         (reverse("aso:settings_popularity"), "Apple Ads settings"))
        self.assertEqual(back(self.get("aso:methodology")), (reverse("aso:dashboard"), "Keywords"))

    def test_it_leads_to_the_page_the_reader_came_from_with_its_view(self):
        html = self.get("aso:methodology", referer="http://testserver/?app=5&country=de")
        self.assertEqual(back(html), ("/?app=5&amp;country=de", "Keywords"))
        html = self.get("aso:apple_ads_setup", referer="http://testserver" + reverse("aso:opportunity"))
        self.assertEqual(back(html), (reverse("aso:opportunity"), "Countries"))

    def test_moving_between_settings_tabs_keeps_where_the_reader_was(self):
        self.get("aso:settings_popularity", referer="http://testserver" + reverse("aso:opportunity"))
        other = "aso_pro:settings_ai" if PRO else "aso:settings_popularity"
        html = self.get(other, referer="http://testserver" + reverse("aso:settings_popularity"))
        self.assertEqual(back(html), (reverse("aso:opportunity"), "Countries"))
        # And a reload keeps it too.
        html = self.get(other)
        self.assertEqual(back(html), (reverse("aso:opportunity"), "Countries"))

    def test_the_setup_guide_leads_back_to_settings_when_opened_from_there(self):
        html = self.get("aso:apple_ads_setup", referer="http://testserver" + reverse("aso:settings_popularity"))
        self.assertEqual(back(html), (reverse("aso:settings_popularity"), "Apple Ads settings"))

    def test_returning_from_a_page_opened_here_keeps_the_way_back(self):
        """Countries, Settings, the setup guide, then back to Settings: Back
        there still leads to Countries, never back into the guide."""
        self.get("aso:settings_popularity", referer="http://testserver" + reverse("aso:opportunity"))
        self.get("aso:apple_ads_setup", referer="http://testserver" + reverse("aso:settings_popularity"))
        html = self.get("aso:settings_popularity", referer="http://testserver" + reverse("aso:apple_ads_setup"))
        self.assertEqual(back(html), (reverse("aso:opportunity"), "Countries"))

    def test_another_site_or_an_unknown_address_is_ignored(self):
        for referer in ("https://ads.apple.com/", "http://testserver/no-such-page/"):
            with self.subTest(referer=referer):
                self.assertEqual(back(self.get("aso:methodology", referer=referer)),
                                 (reverse("aso:dashboard"), "Keywords"))

    def test_an_in_place_update_does_not_change_the_way_back(self):
        self.get("aso:settings_popularity", referer="http://testserver" + reverse("aso:opportunity"))
        self.get("aso:settings_popularity", referer="http://testserver" + reverse("aso:apps"),
                 HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        html = self.get("aso:settings_popularity")
        self.assertEqual(back(html), (reverse("aso:opportunity"), "Countries"))


class AJumpLeadsBackTest(TestCase):
    """The owner, 2026-10-02: after Simulate this metadata there was no way
    back to the AI Competitor result. A link in a page's content that opens
    another page carries ``back`` (static/js/back-link.js), and the page it
    opens, top level included, leads back there."""

    def get(self, name, **params):
        return self.client.get(reverse(name), params).content.decode()

    def test_a_top_level_page_reached_by_a_jump_leads_back(self):
        html = self.get("aso:opportunity", back="/?app=5&country=de")
        self.assertEqual(back(html), ("/?app=5&amp;country=de", "Keywords"))
        if PRO:
            with override_settings(DEBUG_SKIP_LICENSE=True):
                html = self.get("aso_pro:simulator", back=reverse("aso_pro:ai_competitor") + "?run=3")
            self.assertEqual(back(html), (reverse("aso_pro:ai_competitor") + "?run=3", "AI Competitor"))

    def test_only_a_page_of_this_app_and_never_the_page_itself(self):
        for value in ("https://example.com/", "//example.com/x", "/\\example.com", "/no-such-page/",
                      reverse("aso:opportunity")):
            with self.subTest(back=value):
                self.assertIsNone(back(self.get("aso:opportunity", back=value)))

    def test_a_detail_page_reads_back_before_the_referer(self):
        html = self.client.get(reverse("aso:methodology"), {"back": reverse("aso:opportunity")},
                               HTTP_REFERER="http://testserver/").content.decode()
        self.assertEqual(back(html), (reverse("aso:opportunity"), "Countries"))

    def test_every_page_loads_the_script_and_tabs_are_navigation(self):
        from pathlib import Path

        from django.conf import settings

        base = Path(settings.BASE_DIR)
        for rel in ("aso/templates/aso/base.html", "_public_overrides/aso/templates/aso/base.html"):
            if (base / rel).is_file():   # the free edition's own copy is its base.html
                self.assertIn("js/back-link.js", (base / rel).read_text())
        script = (base / "static/js/back-link.js").read_text()
        self.assertIn("link.closest('nav')", script)
        self.assertIn("url.searchParams.set('back', here());", script)
        tabs = (base / "aso/templates/aso/partials/section_tabs.html").read_text()
        self.assertEqual(tabs.count("<nav "), 2)
