"""View tests: banner matrix v2, settings wizard, CSV dual columns."""

import tempfile
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse

from aso.apple_ads import api as apple_api
from aso.apple_ads import keys as apple_keys
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

    def _connect(self, source="apple", tested_ok=True):
        """Simulate a full v1 connection (key + ids + verified)."""
        storage.save_apple_settings(
            popularity_source=source,
            apple_ads={
                "client_id": "SEARCHADS.c", "team_id": "SEARCHADS.t",
                "key_id": "k", "ad_account_id": "1",
                "ad_account_name": "Org", "tested_ok": tested_ok,
            },
        )
        patcher = mock.patch(
            "aso.apple_ads.keys.has_private_key", return_value=True
        )
        patcher.start()
        self.addCleanup(patcher.stop)


class BannerMatrixTest(PopularityViewTestBase):
    """The six-state signal matrix v2 (apple-ads.instructions.md)."""

    def test_recommend_banner_for_fresh_install(self):
        response = self.client.get(reverse("aso:dashboard"))
        self.assertContains(response, "apple-recommend-banner")
        self.assertContains(response, "Recommended: connect Apple Ads")

    def test_recommend_banner_hidden_after_opt_out(self):
        storage.save_apple_settings(apple_ads={"estimate_opt_out": True})
        response = self.client.get(reverse("aso:dashboard"))
        self.assertNotContains(response, "apple-recommend-banner")

    def test_recommend_banner_hidden_when_connected(self):
        self._connect(source="internal")
        response = self.client.get(reverse("aso:dashboard"))
        self.assertNotContains(response, "apple-recommend-banner")

    def test_upgrade_reconnect_banner_for_migrated_apple_user(self):
        storage.save_apple_settings(
            popularity_source="apple",
            apple_ads={"legacy_upgrade_pending": True},
        )
        response = self.client.get(reverse("aso:dashboard"))
        self.assertContains(response, "apple-upgrade-banner")
        self.assertContains(response, "official API")
        self.assertNotContains(response, "apple-recommend-banner")

    def test_not_connected_banner_when_apple_active_without_credentials(self):
        storage.save_apple_settings(popularity_source="apple")
        response = self.client.get(reverse("aso:dashboard"))
        self.assertContains(response, "apple-notconnected-banner")
        self.assertContains(response, "still your popularity source")

    def test_credential_rejected_banner_when_apple_active(self):
        self._connect(source="apple")
        storage.mark_credentials_rejected()
        response = self.client.get(reverse("aso:dashboard"))
        self.assertContains(response, "apple-rejected-banner")

    def test_needs_verify_banner(self):
        self._connect(source="apple", tested_ok=False)
        response = self.client.get(reverse("aso:dashboard"))
        self.assertContains(response, "apple-needsverify-banner")
        self.assertContains(response, "one step left")

    def test_verified_connection_clears_banners_immediately(self):
        self._connect(source="apple", tested_ok=False)
        self.assertContains(
            self.client.get(reverse("aso:dashboard")),
            "apple-needsverify-banner",
        )
        storage.save_apple_settings(apple_ads={"tested_ok": True})
        response = self.client.get(reverse("aso:dashboard"))
        for banner_id in (
            "apple-needsverify-banner", "apple-notconnected-banner",
            "apple-rejected-banner", "apple-recommend-banner",
        ):
            self.assertNotContains(response, banner_id)

    def test_soft_stale_notice_under_internal_source(self):
        self._connect(source="internal")
        storage.mark_credentials_rejected()
        response = self.client.get(reverse("aso:dashboard"))
        self.assertContains(response, "apple-stale-banner")
        self.assertContains(response, "data-expired-at")
        self.assertNotContains(response, "apple-rejected-banner")

    def test_silence_when_internal_and_healthy(self):
        self._connect(source="internal")
        response = self.client.get(reverse("aso:dashboard"))
        for banner_id in (
            "apple-recommend-banner", "apple-stale-banner",
            "apple-notconnected-banner",
        ):
            self.assertNotContains(response, banner_id)

    def test_banner_endpoint_serves_live_region(self):
        response = self.client.get(reverse("aso:popularity_banner"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "apple-recommend-banner")

    def test_banner_partial_stays_script_free(self):
        """Swapped innerHTML never executes scripts - the partial must not
        contain any (behavior lives in popularity-banner.js)."""
        response = self.client.get(reverse("aso:popularity_banner"))
        self.assertNotContains(response, "<script")


class AppleAdsSetupPageTest(PopularityViewTestBase):
    """The setup guide must render in both editions (it syncs to the free
    repo, so it may only reference aso_pro URLs behind a pro_edition guard
    - regression: an unguarded Top Terms link 500ed the page)."""

    def test_renders_with_top_terms_link(self):
        response = self.client.get(reverse("aso:apple_ads_setup"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Top Terms")

    def test_free_edition_branch_uses_promo_link(self):
        from django.template.loader import get_template

        html = get_template("aso/apple_ads_setup.html").render(
            {"pro_edition": False}
        )
        self.assertIn(reverse("aso:pro_promo_top_terms"), html)


class SettingsPageTest(PopularityViewTestBase):
    def test_apple_card_shows_recommended_badge(self):
        """Product decision (v1 migration): Apple is the recommended
        source - flip of the old no-recommendation rule."""
        response = self.client.get(reverse("aso:settings_popularity"))
        self.assertContains(response, "RespectASO Estimate")
        self.assertContains(response, "Apple Ads Popularity")
        self.assertContains(response, "Recommended")

    def test_page_discloses_top_terms_and_alignment(self):
        response = self.client.get(reverse("aso:settings_popularity"))
        self.assertContains(response, "top 500")
        self.assertContains(response, "aligned to")

    def test_apple_source_not_selectable_before_verification(self):
        response = self.client.post(reverse("aso:settings_popularity"), {
            "action": "select_source", "popularity_source": "apple",
        })
        self.assertContains(response, "isn&#x27;t connected yet")
        self.assertEqual(storage.get_popularity_source(), "internal")

    def test_internal_selectable_immediately(self):
        self.client.post(reverse("aso:settings_popularity"), {
            "action": "select_source", "popularity_source": "internal",
        })
        self.assertEqual(storage.get_popularity_source(), "internal")

    def test_apple_selectable_after_verification(self):
        self._connect(source="internal")
        self.client.post(reverse("aso:settings_popularity"), {
            "action": "select_source", "popularity_source": "apple",
        })
        self.assertEqual(storage.get_popularity_source(), "apple")

    def test_estimate_opt_out_toggle(self):
        self.client.post(reverse("aso:settings_popularity"), {
            "action": "estimate_opt_out", "opt_out": "1",
        })
        self.assertTrue(
            storage.load_apple_settings()["apple_ads"]["estimate_opt_out"]
        )
        self.client.post(reverse("aso:settings_popularity"), {
            "action": "estimate_opt_out", "opt_out": "0",
        })
        self.assertFalse(
            storage.load_apple_settings()["apple_ads"]["estimate_opt_out"]
        )

    def test_wizard_states_render(self):
        # no_credentials
        response = self.client.get(reverse("aso:settings_popularity"))
        self.assertContains(response, "generate my key pair")
        # keys_generated
        apple_keys.generate_key_pair()
        response = self.client.get(reverse("aso:settings_popularity"))
        self.assertContains(response, "BEGIN PUBLIC KEY")
        self.assertContains(response, "Client ID")
        # unverified
        storage.save_apple_settings(apple_ads={
            "client_id": "SEARCHADS.c", "team_id": "SEARCHADS.t", "key_id": "k",
        })
        response = self.client.get(reverse("aso:settings_popularity"))
        self.assertContains(response, "verify the connection")
        # connected
        storage.save_apple_settings(apple_ads={
            "tested_ok": True, "ad_account_name": "My Org",
        })
        response = self.client.get(reverse("aso:settings_popularity"))
        self.assertContains(response, "Connected to Apple")
        self.assertContains(response, "My Org")

    def test_mid_wizard_states_offer_start_over(self):
        """Regression: a user who generated keys by accident (or wants the
        import path instead) was stuck on step 2 with no way back. Every
        mid-wizard state must offer the Start over escape hatch."""
        # keys_generated
        apple_keys.generate_key_pair()
        response = self.client.get(reverse("aso:settings_popularity"))
        self.assertContains(response, "Start over")
        # unverified
        storage.save_apple_settings(apple_ads={
            "client_id": "SEARCHADS.c", "team_id": "SEARCHADS.t", "key_id": "k",
        })
        response = self.client.get(reverse("aso:settings_popularity"))
        self.assertContains(response, "Start over")
        # Start over resets the wizard to step 1: key gone, ids cleared.
        self.client.post(reverse("aso:apple_disconnect"))
        self.assertFalse(apple_keys.has_private_key())
        response = self.client.get(reverse("aso:settings_popularity"))
        self.assertContains(response, "generate my key pair")
        self.assertNotContains(response, "BEGIN PUBLIC KEY")


class WizardEndpointsTest(PopularityViewTestBase):
    def test_generate_keys_and_replace_confirm(self):
        response = self.client.post(reverse("aso:apple_keys_generate"))
        data = response.json()
        self.assertTrue(data["ok"])
        self.assertIn("BEGIN PUBLIC KEY", data["public_key"])
        # A second call without confirmation must refuse.
        response = self.client.post(reverse("aso:apple_keys_generate"))
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()["needs_confirm"])
        # Confirmed replacement works and resets verification.
        storage.save_apple_settings(apple_ads={"tested_ok": True})
        response = self.client.post(
            reverse("aso:apple_keys_generate"), {"replace": "1"}
        )
        self.assertTrue(response.json()["ok"])
        self.assertFalse(
            storage.load_apple_settings()["apple_ads"]["tested_ok"]
        )

    def test_import_key_rejects_garbage_with_json(self):
        response = self.client.post(
            reverse("aso:apple_keys_import"), {"private_key": "junk"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("PEM", response.json()["error"])

    def test_credentials_require_all_three(self):
        response = self.client.post(reverse("aso:apple_credentials"), {
            "client_id": "SEARCHADS.c", "team_id": "", "key_id": "k",
        })
        self.assertEqual(response.status_code, 400)
        response = self.client.post(reverse("aso:apple_credentials"), {
            "client_id": "SEARCHADS.c", "team_id": "SEARCHADS.t",
            "key_id": "k",
        })
        self.assertTrue(response.json()["ok"])
        block = storage.load_apple_settings()["apple_ads"]
        self.assertEqual(block["client_id"], "SEARCHADS.c")
        self.assertFalse(block["tested_ok"])

    def _saved_credentials(self):
        apple_keys.generate_key_pair()
        storage.save_apple_settings(apple_ads={
            "client_id": "SEARCHADS.c", "team_id": "SEARCHADS.t", "key_id": "k",
        })

    def test_verify_happy_path_single_account(self):
        self._saved_credentials()
        acl = [{"ad_account_id": 42, "ad_account_name": "Org",
                "org_id": 7, "roles": ["API Campaign Manager"]}]
        with mock.patch.object(apple_api, "list_acls", return_value=acl), \
                mock.patch.object(
                    apple_api, "query_search_term_popularity",
                    return_value=([{"searchTerm": "x"}], -1),
                ), \
                mock.patch("aso.apple_ads.sync.run_manual_sync") as run_sync:
            response = self.client.post(reverse("aso:apple_verify"))
        data = response.json()
        self.assertTrue(data["ok"])
        block = storage.load_apple_settings()["apple_ads"]
        self.assertTrue(block["tested_ok"])
        self.assertEqual(block["ad_account_id"], "42")
        self.assertEqual(block["ad_account_name"], "Org")
        run_sync.assert_called_once()
        self.assertTrue(storage.apple_source_ready())

    def test_verify_clears_legacy_upgrade_flag(self):
        self._saved_credentials()
        storage.save_apple_settings(apple_ads={"legacy_upgrade_pending": True})
        acl = [{"ad_account_id": 42, "ad_account_name": "Org",
                "org_id": 7, "roles": ["Admin"]}]
        with mock.patch.object(apple_api, "list_acls", return_value=acl), \
                mock.patch.object(
                    apple_api, "query_search_term_popularity",
                    return_value=([], -1),
                ), \
                mock.patch("aso.apple_ads.sync.run_manual_sync"):
            self.client.post(reverse("aso:apple_verify"))
        self.assertFalse(
            storage.load_apple_settings()["apple_ads"]["legacy_upgrade_pending"]
        )

    def test_verify_multi_account_returns_picker_then_choice(self):
        self._saved_credentials()
        acls = [
            {"ad_account_id": 1, "ad_account_name": "A", "org_id": 7,
             "roles": ["Admin"]},
            {"ad_account_id": 2, "ad_account_name": "B", "org_id": 7,
             "roles": ["Admin"]},
        ]
        with mock.patch.object(apple_api, "list_acls", return_value=acls), \
                mock.patch.object(
                    apple_api, "query_search_term_popularity",
                    return_value=([], -1),
                ), \
                mock.patch("aso.apple_ads.sync.run_manual_sync"):
            response = self.client.post(reverse("aso:apple_verify"))
            data = response.json()
            self.assertFalse(data["ok"])
            self.assertTrue(data["needs_account_choice"])
            self.assertEqual(len(data["accounts"]), 2)
            response = self.client.post(
                reverse("aso:apple_verify"), {"ad_account_id": "2"}
            )
        self.assertTrue(response.json()["ok"])
        self.assertEqual(
            storage.load_apple_settings()["apple_ads"]["ad_account_id"], "2"
        )

    def test_verify_rejection_marks_state_with_json_error(self):
        self._saved_credentials()
        with mock.patch.object(
            apple_api, "list_acls",
            side_effect=apple_api.AppleAdsAuthError("no"),
        ):
            response = self.client.post(reverse("aso:apple_verify"))
        self.assertEqual(response.status_code, 400)
        self.assertIn("rejected", response.json()["error"])
        self.assertTrue(
            storage.load_apple_settings()["apple_ads"]["credentials_rejected"]
        )

    def test_verify_never_returns_html_500(self):
        self._saved_credentials()
        with mock.patch.object(
            apple_api, "list_acls",
            side_effect=apple_api.AppleAdsAPIError("boom"),
        ):
            response = self.client.post(reverse("aso:apple_verify"))
        self.assertEqual(response.status_code, 502)
        self.assertIn("boom", response.json()["error"])

    def test_disconnect_clears_everything(self):
        self._saved_credentials()
        storage.save_apple_settings(apple_ads={"tested_ok": True})
        response = self.client.post(reverse("aso:apple_disconnect"))
        self.assertTrue(response.json()["ok"])
        self.assertFalse(apple_keys.has_private_key())
        block = storage.load_apple_settings()["apple_ads"]
        self.assertEqual(block["client_id"], "")
        self.assertFalse(block["tested_ok"])


class CsvDualColumnsTest(PopularityViewTestBase):
    def test_export_includes_dual_popularity_columns(self):
        app = App.objects.create(name="Test App")
        keyword = Keyword.objects.create(keyword="fitness", app=app)
        SearchResult.objects.create(
            keyword=keyword,
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
        # Internal active -> effective column equals internal value
        self.assertIn("62,62,48,internal,no", content)

    def test_export_effective_switches_with_source(self):
        storage.save_apple_settings(
            popularity_source="apple", apple_ads={"tested_ok": True}
        )
        app = App.objects.create(name="Test App")
        keyword = Keyword.objects.create(keyword="fitness", app=app)
        SearchResult.objects.create(
            keyword=keyword,
            popularity_score=62,
            apple_popularity_score=48,
            difficulty_score=30,
            country="us",
        )
        response = self.client.get(reverse("aso:export_history_csv"))
        self.assertIn("48,62,48,apple,no", response.content.decode())
