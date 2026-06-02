"""Tests for the per-app "refresh from App Store" action.

App name/icon/seller are a snapshot taken when an app is first added. When the
developer renames the app on the App Store, the per-app refresh button pulls
the current values from iTunes (via track_id) and writes them back to the App
row — the single source of truth every screen reads from.

Verifies:
- A renamed app gets its name/icon/seller updated and a "renamed" message.
- An unchanged app reports "current" without spurious edits.
- An iTunes failure leaves the row untouched and reports "failed".
- Manual apps (no track_id) are a no-op (nothing to look up).
"""

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from aso.models import App


class AppRefreshViewTest(TestCase):
    def setUp(self):
        self.app = App.objects.create(
            name="Old Title",
            track_id=123456789,
            icon_url="https://example.com/old.png",
            seller_name="Old Seller",
        )

    def _fresh(self, **overrides):
        data = {
            "trackName": "New Title",
            "artworkUrl100": "https://example.com/new.png",
            "sellerName": "New Seller",
        }
        data.update(overrides)
        return data

    @patch("aso.views.ITunesSearchService.lookup_by_id")
    def test_renamed_app_is_updated(self, mock_lookup):
        mock_lookup.return_value = self._fresh()

        resp = self.client.post(reverse("aso:app_refresh", args=[self.app.id]))

        mock_lookup.assert_called_once_with(123456789)
        self.assertRedirects(resp, reverse("aso:apps") + "?refresh=renamed")
        self.app.refresh_from_db()
        self.assertEqual(self.app.name, "New Title")
        self.assertEqual(self.app.icon_url, "https://example.com/new.png")
        self.assertEqual(self.app.seller_name, "New Seller")

    @patch("aso.views.ITunesSearchService.lookup_by_id")
    def test_unchanged_app_reports_current(self, mock_lookup):
        mock_lookup.return_value = self._fresh(trackName="Old Title")

        resp = self.client.post(reverse("aso:app_refresh", args=[self.app.id]))

        self.assertRedirects(resp, reverse("aso:apps") + "?refresh=current")
        self.app.refresh_from_db()
        self.assertEqual(self.app.name, "Old Title")

    @patch("aso.views.ITunesSearchService.lookup_by_id")
    def test_itunes_failure_leaves_row_untouched(self, mock_lookup):
        mock_lookup.return_value = None

        resp = self.client.post(reverse("aso:app_refresh", args=[self.app.id]))

        self.assertRedirects(resp, reverse("aso:apps") + "?refresh=failed")
        self.app.refresh_from_db()
        self.assertEqual(self.app.name, "Old Title")
        self.assertEqual(self.app.icon_url, "https://example.com/old.png")

    @patch("aso.views.ITunesSearchService.lookup_by_id")
    def test_manual_app_is_a_noop(self, mock_lookup):
        manual = App.objects.create(name="Manual App")

        resp = self.client.post(reverse("aso:app_refresh", args=[manual.id]))

        mock_lookup.assert_not_called()
        self.assertRedirects(resp, reverse("aso:apps"))
        manual.refresh_from_db()
        self.assertEqual(manual.name, "Manual App")

    def test_get_is_rejected(self):
        resp = self.client.get(reverse("aso:app_refresh", args=[self.app.id]))
        self.assertEqual(resp.status_code, 405)

    @patch("aso.views.ITunesSearchService.lookup_by_id")
    def test_refresh_message_renders_on_apps_page(self, mock_lookup):
        mock_lookup.return_value = self._fresh()
        self.client.post(reverse("aso:app_refresh", args=[self.app.id]))

        resp = self.client.get(reverse("aso:apps") + "?refresh=renamed")
        self.assertContains(resp, "App details updated from the App Store.")
