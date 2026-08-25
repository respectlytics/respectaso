"""Tests for the dismissible Respectlytics cross-promo banner.

Free-edition behaviour only (no license installed). The "hidden for Pro
users" half lives in licensing/tests.py, which is Pro-only and never
syncs to the public repo.
"""

import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse

from aso import ui_state


class PromoBannerTest(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self._override = override_settings(DATA_DIR=self.data_dir)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(self._tmp.cleanup)

    def test_banner_shows_without_a_license(self):
        response = self.client.get(reverse("aso:methodology"))
        self.assertContains(response, 'id="respectlytics-banner"')
        self.assertContains(response, reverse("aso:respectlytics_banner_dismiss"))

    def test_dismiss_endpoint_hides_it_for_good(self):
        response = self.client.post(reverse("aso:respectlytics_banner_dismiss"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ui_state.is_dismissed(ui_state.RESPECTLYTICS_BANNER))

        response = self.client.get(reverse("aso:methodology"))
        self.assertNotContains(response, 'id="respectlytics-banner"')
        # Still gone on the next visit (state is on disk, not in the page).
        response = self.client.get(reverse("aso:dashboard"))
        self.assertNotContains(response, 'id="respectlytics-banner"')

    def test_dismiss_rejects_get(self):
        response = self.client.get(reverse("aso:respectlytics_banner_dismiss"))
        self.assertEqual(response.status_code, 405)


class UiStateTest(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self._override = override_settings(DATA_DIR=self.data_dir)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(self._tmp.cleanup)

    def test_defaults_to_not_dismissed(self):
        self.assertFalse(ui_state.is_dismissed(ui_state.RESPECTLYTICS_BANNER))

    def test_dismissals_are_independent_and_survive_a_reread(self):
        ui_state.dismiss("other_notice")
        self.assertFalse(ui_state.is_dismissed(ui_state.RESPECTLYTICS_BANNER))
        ui_state.dismiss(ui_state.RESPECTLYTICS_BANNER)
        self.assertTrue(ui_state.is_dismissed("other_notice"))
        self.assertTrue(ui_state.is_dismissed(ui_state.RESPECTLYTICS_BANNER))

    def test_corrupt_file_is_ignored_not_fatal(self):
        (self.data_dir / "ui_state.json").write_text("{not json")
        self.assertFalse(ui_state.is_dismissed(ui_state.RESPECTLYTICS_BANNER))
        ui_state.dismiss(ui_state.RESPECTLYTICS_BANNER)
        self.assertTrue(ui_state.is_dismissed(ui_state.RESPECTLYTICS_BANNER))
