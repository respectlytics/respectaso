"""The Keywords page's "Get set up" card (aso/setup_checklist.py,
docs/development/APP_SHELL_PLAN.md): its steps follow the app's own state,
the Apple Ads step carries the old recommendation banner's rule, and folding
is remembered.
"""

import json
import tempfile
from pathlib import Path

from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from aso import setup_checklist
from aso.apple_ads import storage
from aso.models import App, Keyword, SearchResult


class ChecklistTest(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._override = override_settings(DATA_DIR=Path(self._tmp.name), DEBUG_SKIP_LICENSE=False)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(self._tmp.cleanup)

    def _steps(self):
        request = RequestFactory().get("/")
        request.session = self.client.session
        card = setup_checklist.checklist(request)
        return None if card is None else {s["key"]: s["done"] for s in card["steps"]}

    def test_a_fresh_install_shows_the_first_run_card_instead(self):
        self.assertIsNone(self._steps())

    def test_with_an_app_four_steps_and_three_open(self):
        App.objects.create(name="Moonpond")
        self.assertEqual(self._steps(), {"app": True, "keywords": False, "apple": False, "pro": False})

    def test_each_step_closes_from_the_app_state(self):
        app = App.objects.create(name="Moonpond")
        self.assertTrue(self._steps()["app"])
        SearchResult.objects.create(keyword=Keyword.objects.create(keyword="sleep sounds", app=app),
                                    country="us", difficulty_score=40, popularity_score=50)
        self.assertTrue(self._steps()["keywords"])

    def test_staying_on_the_estimate_closes_the_apple_step_and_the_card(self):
        app = App.objects.create(name="Moonpond")
        SearchResult.objects.create(keyword=Keyword.objects.create(keyword="sleep sounds", app=app),
                                    country="us", difficulty_score=40, popularity_score=50)
        storage.save_apple_settings(apple_ads={"estimate_opt_out": True})
        # Without Pro the invitation stays: the card can be folded, not finished.
        self.assertEqual(self._steps(), {"app": True, "keywords": True, "apple": True, "pro": False})

    def test_with_pro_the_card_closes_when_every_step_is_done(self):
        from unittest.mock import patch

        from django.apps import apps as django_apps

        app = App.objects.create(name="Moonpond")
        SearchResult.objects.create(keyword=Keyword.objects.create(keyword="sleep sounds", app=app),
                                    country="us", difficulty_score=40, popularity_score=50)
        storage.save_apple_settings(apple_ads={"estimate_opt_out": True})
        with override_settings(DEBUG_SKIP_LICENSE=True, IS_NATIVE_APP=True):
            if not django_apps.is_installed("aso_pro"):
                self.skipTest("the free edition has no license")
            with patch("aso_pro.views._has_ai_configured", return_value=True):
                self.assertIsNone(self._steps())

    def test_the_pro_step_is_the_top_bar_button(self):
        """Same words, same address, same green button as the top bar
        (docs/development/PRO_AND_CLICKABLE_TEXT_PLAN.md)."""
        from aso.context_processors import pro_button

        App.objects.create(name="Moonpond")
        for native in (False, True):
            with self.subTest(native=native), override_settings(IS_NATIVE_APP=native):
                request = RequestFactory().get("/")
                request.session = self.client.session
                step = setup_checklist.checklist(request)["steps"][-1]
                top = pro_button(None)["pro_button"]
                self.assertEqual(step["key"], "pro")
                self.assertEqual((step["action"]["label"], step["action"]["url"]), (top["label"], top["url"]))
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        card = html[html.index('id="setup-checklist"'):html.index("</section>", html.index('id="setup-checklist"'))]
        self.assertIn('data-setup-step="pro"', card)
        self.assertIn('class="btn-pro', card)

    def test_the_card_renders_on_keywords_and_can_only_fold(self):
        App.objects.create(name="Moonpond")
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertIn('id="setup-checklist"', html)
        self.assertIn('data-setup-step="apple"', html)
        self.assertIn("Stay on the estimate", html)
        self.assertIn("data-setup-toggle", html)
        self.assertNotIn("Dismiss", html[html.index('id="setup-checklist"'):html.index("</section>", html.index('id="setup-checklist"'))])

    def test_folding_is_remembered(self):
        App.objects.create(name="Moonpond")
        response = self.client.post(reverse("aso:ui_memory"), json.dumps({"name": "setup_checklist_folded", "value": True}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertIn('data-folded="true"', html)


class CloseForGoodTest(TestCase):
    """The owner, 2026-10-02: without Pro the card stays for as long as Pro is
    not bought; with Pro it goes when every step is done, and a Pro user may
    also close it for good."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._override = override_settings(DATA_DIR=Path(self._tmp.name))
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(self._tmp.cleanup)
        storage.reset_cache()
        App.objects.create(name="Moonpond")

    def close(self):
        return self.client.post(reverse("aso:ui_memory"), json.dumps({"name": "setup_checklist_closed", "value": True}),
                                content_type="application/json")

    def card(self):
        request = RequestFactory().get("/")
        request.session = self.client.session
        return setup_checklist.checklist(request)

    @override_settings(DEBUG_SKIP_LICENSE=False)
    def test_without_pro_it_cannot_be_closed(self):
        self.assertFalse(self.card()["can_close"])
        self.assertEqual(self.close().status_code, 400)
        self.assertIsNotNone(self.card())
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertNotIn(">Close for good</button>", html)

    @override_settings(DEBUG_SKIP_LICENSE=True)
    def test_with_pro_it_closes_for_good(self):
        self.assertTrue(self.card()["can_close"])
        self.assertIn(">Close for good</button>", self.client.get(reverse("aso:dashboard")).content.decode())
        self.assertEqual(self.close().status_code, 200)
        self.assertIsNone(self.card())
        # For good: a new visitor session does not bring it back.
        self.client.logout()
        self.assertNotIn('id="setup-checklist"', self.client.get(reverse("aso:dashboard")).content.decode())
