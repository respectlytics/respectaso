"""View tests: choose-source banner, settings gating, CSV dual columns."""

import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse

from aso.apple_ads import storage
from aso.models import App, Keyword, SearchResult


class PopularityViewTestBase(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._override = override_settings(DATA_DIR=Path(self._tmp.name))
        self._override.enable()
        storage.reset_cache()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self._override.disable()
        storage.reset_cache()
        self._tmp.cleanup()


class ChooseSourceBannerTest(PopularityViewTestBase):
    def test_banner_shown_until_source_selected(self):
        response = self.client.get(reverse("aso:dashboard"))
        self.assertContains(response, "Choose your popularity source")

    def test_banner_hidden_after_selecting_internal(self):
        storage.save_apple_settings(popularity_source="internal")
        response = self.client.get(reverse("aso:dashboard"))
        self.assertNotContains(response, "Choose your popularity source")

    def test_expired_banner_when_apple_selected_and_session_dead(self):
        # Expiry keeps the stored cookies - only sign-out clears them.
        storage.save_apple_settings(
            popularity_source="apple",
            apple_ads={
                "session_expired": True,
                "tested_ok": True,
                "cookies": [{"name": "myacinfo", "value": "x", "domain": "", "path": "/", "expires": 0}],
            },
        )
        response = self.client.get(reverse("aso:dashboard"))
        self.assertContains(response, "Apple Ads sign-in expired")
        # Not the signed-out banner - the states are mutually exclusive.
        self.assertNotContains(response, "disconnected but still your popularity source")

    def test_signed_out_banner_when_apple_selected(self):
        """Explicit sign-out while ASA is active must be loudly visible."""
        from aso.apple_ads import auth

        storage.save_apple_settings(
            popularity_source="apple", apple_ads={"tested_ok": True}
        )
        auth.sign_out()
        response = self.client.get(reverse("aso:dashboard"))
        self.assertContains(response, "disconnected but still your popularity source")
        self.assertContains(response, "Open Settings")
        self.assertNotContains(response, "Apple Ads sign-in expired")

    COOKIE = {"name": "myacinfo", "value": "x", "domain": "", "path": "/", "expires": 0}

    def test_resignin_clears_signed_out_banner_immediately(self):
        """Regression (owner-reported): after signing back in, the
        'disconnected' state must clear at once - cookies define signed-out,
        not the connection-test flag. The remaining gap is 'needs_test'."""
        from aso.apple_ads import auth

        storage.save_apple_settings(
            popularity_source="apple", apple_ads={"tested_ok": True}
        )
        auth.sign_out()
        # Re-sign-in: the harvest stores cookies (tested_ok still False).
        storage.save_apple_settings(
            apple_ads={"cookies": [dict(self.COOKIE)], "session_expired": False}
        )
        response = self.client.get(reverse("aso:dashboard"))
        self.assertNotContains(response, "disconnected but still your popularity source")
        self.assertContains(response, "one step left")
        self.assertContains(response, "Run the connection test")

    def test_needs_test_banner_when_signed_in_but_untested(self):
        storage.save_apple_settings(
            popularity_source="apple",
            apple_ads={"cookies": [dict(self.COOKIE)], "tested_ok": False},
        )
        response = self.client.get(reverse("aso:dashboard"))
        self.assertContains(response, "apple-needstest-banner")
        self.assertNotContains(response, "apple-signedout-banner")

    def test_banner_endpoint_serves_live_region(self):
        """The live-region endpoint returns the partial for the current
        state - what popularity-banner.js swaps in without a reload."""
        from aso.apple_ads import auth

        storage.save_apple_settings(
            popularity_source="apple", apple_ads={"tested_ok": True}
        )
        auth.sign_out()
        response = self.client.get(reverse("aso:popularity_banner"))
        self.assertContains(response, "disconnected but still your popularity source")
        # Sign back in: same endpoint now serves the needs-test banner.
        storage.save_apple_settings(apple_ads={"cookies": [dict(self.COOKIE)]})
        response = self.client.get(reverse("aso:popularity_banner"))
        self.assertContains(response, "one step left")
        # Test passes: banner-free.
        storage.save_apple_settings(apple_ads={"tested_ok": True})
        response = self.client.get(reverse("aso:popularity_banner"))
        self.assertNotContains(response, "banner")

    def test_banner_partial_stays_script_free(self):
        """Swapped innerHTML never executes scripts - the partial must not
        contain any (behavior lives in popularity-banner.js)."""
        import os

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "templates/aso/partials/popularity_banner.html",
        )
        with open(path) as f:
            self.assertNotIn("<script", f.read())

    def test_soft_stale_notice_when_internal_selected_and_expired(self):
        """EST active + involuntary expiry: soft dismissible notice, keyed
        by the expiry timestamp - never the red banner."""
        from aso.apple_ads import auth

        storage.save_apple_settings(
            popularity_source="internal", apple_ads={"tested_ok": True}
        )
        auth.mark_session_expired()
        response = self.client.get(reverse("aso:dashboard"))
        self.assertContains(response, "apple-stale-banner")
        self.assertContains(response, "no longer refreshing")
        self.assertContains(response, "data-expired-at=")
        self.assertNotContains(response, "disconnected but still your popularity source")
        # The dismiss key carries the expiry event timestamp
        expired_at = storage.load_apple_settings()["apple_ads"]["session_expired_at"]
        self.assertTrue(expired_at)
        self.assertContains(response, expired_at)

    def test_no_banner_for_deliberate_sign_out_under_internal(self):
        """EST active + explicit sign-out: silence - the confirm dialog
        already explained the consequence."""
        from aso.apple_ads import auth

        storage.save_apple_settings(
            popularity_source="internal", apple_ads={"tested_ok": True}
        )
        auth.sign_out()
        response = self.client.get(reverse("aso:dashboard"))
        self.assertNotContains(response, "apple-stale-banner")
        self.assertNotContains(response, "disconnected but still your popularity source")
        self.assertNotContains(response, "Apple Ads sign-in expired")

    def test_no_banner_for_internal_never_connected(self):
        storage.save_apple_settings(popularity_source="internal")
        response = self.client.get(reverse("aso:dashboard"))
        self.assertNotContains(response, "apple-stale-banner")
        self.assertNotContains(response, "Apple Ads sign-in expired")

    def test_settings_page_signout_confirm_and_disconnected_card(self):
        from aso.apple_ads import auth

        storage.save_apple_settings(
            popularity_source="apple",
            apple_ads={
                "tested_ok": True,
                "cookies": [{"name": "x", "value": "1", "domain": "", "path": "/", "expires": 0}],
            },
        )
        response = self.client.get(reverse("aso:settings_popularity"))
        self.assertContains(response, "Apple Ads is your active popularity source. After signing out")
        auth.sign_out()
        response = self.client.get(reverse("aso:settings_popularity"))
        self.assertContains(response, "Active, but disconnected")
        self.assertContains(response, "You signed out, but Apple Ads is still your popularity source")


class SettingsPopularityPageTest(PopularityViewTestBase):
    def test_page_renders_with_both_cards_and_no_recommended_badge(self):
        response = self.client.get(reverse("aso:settings_popularity"))
        self.assertContains(response, "RespectASO Estimate")
        self.assertContains(response, "Apple Ads Popularity")
        self.assertContains(response, "Which should I choose?")
        self.assertNotContains(response, "Recommended")

    def test_page_discloses_apple_scale_minimum(self):
        """The ASA cons must state the scale-minimum (5) resolution limit
        and its effect on opportunity and AI simulation scores."""
        response = self.client.get(reverse("aso:settings_popularity"))
        self.assertContains(response, "minimum (5)")
        self.assertContains(response, "AI simulation scores")

    def test_apple_source_not_selectable_before_test(self):
        response = self.client.post(reverse("aso:settings_popularity"), {
            "action": "select_source",
            "popularity_source": "apple",
        })
        self.assertContains(response, "sign in and run the connection")
        self.assertEqual(storage.get_popularity_source(), "")

    def test_internal_selectable_immediately(self):
        response = self.client.post(reverse("aso:settings_popularity"), {
            "action": "select_source",
            "popularity_source": "internal",
        })
        self.assertEqual(response.status_code, 200)
        self.assertEqual(storage.get_popularity_source(), "internal")

    def test_apple_selectable_after_test_passed(self):
        storage.save_apple_settings(apple_ads={"tested_ok": True})
        self.client.post(reverse("aso:settings_popularity"), {
            "action": "select_source",
            "popularity_source": "apple",
        })
        self.assertEqual(storage.get_popularity_source(), "apple")

    def test_app_id_validation(self):
        response = self.client.post(reverse("aso:settings_popularity"), {
            "action": "save_app_id",
            "primary_app_id": "not-a-number",
        })
        self.assertContains(response, "must be numeric")

    def test_test_endpoint_requires_setup(self):
        response = self.client.post(reverse("aso:apple_test"))
        self.assertEqual(response.status_code, 400)

    def _ready_for_test(self):
        storage.save_apple_settings(apple_ads={
            "primary_app_id": "42",
            "cookies": [{"name": "myacinfo", "value": "x", "domain": "", "path": "/", "expires": 0}],
        })

    def test_test_endpoint_maps_warming_up_error(self):
        """A fresh session's KWS_NO_ORG_CONTENT_PROVIDERS must produce an
        actionable message, not raw API-speak."""
        from unittest.mock import patch

        from aso.apple_ads.client import AppleAdsAPIError

        self._ready_for_test()
        with patch(
            "aso.settings_views.fetch_popularities",
            side_effect=AppleAdsAPIError(
                "Apple popularity request failed after retries "
                "(status 403, KWS_NO_ORG_CONTENT_PROVIDERS)."
            ),
        ):
            response = self.client.post(reverse("aso:apple_test"))
        self.assertEqual(response.status_code, 502)
        self.assertIn("warming up", response.json()["error"])
        self.assertIn("30 seconds", response.json()["error"])

    def test_test_endpoint_never_returns_html_500(self):
        """Unexpected exceptions must come back as JSON so the page shows a
        real message instead of a generic fetch failure."""
        from unittest.mock import patch

        self._ready_for_test()
        with patch(
            "aso.settings_views.fetch_popularities",
            side_effect=RuntimeError("boom"),
        ):
            response = self.client.post(reverse("aso:apple_test"))
        self.assertEqual(response.status_code, 500)
        data = response.json()  # JSON, not an HTML error page
        self.assertFalse(data["ok"])
        self.assertIn("boom", data["error"])


class CsvDualColumnsTest(PopularityViewTestBase):
    def test_export_includes_dual_popularity_columns(self):
        app = App.objects.create(name="Test App")
        kw = Keyword.objects.create(keyword="fitness", app=app)
        SearchResult.objects.create(
            keyword=kw,
            popularity_score=62,
            apple_popularity_score=48,
            difficulty_score=30,
            country="us",
        )
        response = self.client.get(reverse("aso:export_history_csv"))
        content = response.content.decode()
        header = content.splitlines()[0]
        for column in (
            "Popularity",
            "Popularity (RespectASO)",
            "Popularity (Apple Ads)",
            "Popularity Source",
            "Popularity Fallback",
        ):
            self.assertIn(column, header)
        # Internal active → effective column equals internal value
        self.assertIn("62,62,48,internal,no", content)

    def test_export_effective_switches_with_source(self):
        storage.save_apple_settings(
            popularity_source="apple", apple_ads={"tested_ok": True}
        )
        app = App.objects.create(name="Test App")
        kw = Keyword.objects.create(keyword="fitness", app=app)
        SearchResult.objects.create(
            keyword=kw,
            popularity_score=62,
            apple_popularity_score=48,
            difficulty_score=30,
            country="us",
        )
        response = self.client.get(reverse("aso:export_history_csv"))
        self.assertIn("48,62,48,apple,no", response.content.decode())
