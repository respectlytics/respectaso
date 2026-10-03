"""Respectlytics lives in the Help menu, never as a banner above the app
(docs/development/APP_SHELL_PLAN.md), and the per-install notice store
(aso.ui_state) keeps working for the notices that still use it.
"""

import tempfile
from pathlib import Path

from django.test import TestCase, override_settings
from django.urls import reverse

from aso import ui_state
from aso.links import RESPECTLYTICS_URL


class RespectlyticsInTheHelpMenuTest(TestCase):
    def test_no_banner_one_menu_row(self):
        html = self.client.get(reverse("aso:methodology")).content.decode()
        self.assertNotIn('id="respectlytics-banner"', html)
        self.assertIn("Respectlytics: privacy-first app analytics", html)
        self.assertIn(RESPECTLYTICS_URL.replace("&", "&amp;"), html)


class UiStateTest(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self._override = override_settings(DATA_DIR=self.data_dir)
        self._override.enable()
        self.addCleanup(self._override.disable)
        self.addCleanup(self._tmp.cleanup)

    def test_defaults_to_not_dismissed(self):
        self.assertFalse(ui_state.is_dismissed(ui_state.KEYWORD_CLEANUP_BANNER))

    def test_dismissals_are_independent_and_survive_a_reread(self):
        ui_state.dismiss("other_notice")
        self.assertFalse(ui_state.is_dismissed(ui_state.KEYWORD_CLEANUP_BANNER))
        ui_state.dismiss(ui_state.KEYWORD_CLEANUP_BANNER)
        self.assertTrue(ui_state.is_dismissed("other_notice"))
        self.assertTrue(ui_state.is_dismissed(ui_state.KEYWORD_CLEANUP_BANNER))

    def test_corrupt_file_is_ignored_not_fatal(self):
        (self.data_dir / "ui_state.json").write_text("{not json")
        self.assertFalse(ui_state.is_dismissed(ui_state.KEYWORD_CLEANUP_BANNER))
        ui_state.dismiss(ui_state.KEYWORD_CLEANUP_BANNER)
        self.assertTrue(ui_state.is_dismissed(ui_state.KEYWORD_CLEANUP_BANNER))
