"""The keyword cleanup banner (aso/keyword_cleanup.py)."""

import tempfile
from datetime import timedelta
from pathlib import Path
from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aso import ui_state
from aso.keyword_cleanup import cleanup_suggestion, duration_text
from aso.models import Keyword, SearchResult


def seed(pairs, low_volume):
    """`pairs` keyword+country pairs, `low_volume` of them classified Low Volume."""
    keywords = Keyword.objects.bulk_create([Keyword(keyword=f"kw{i:04d}") for i in range(pairs)])
    keywords = list(Keyword.objects.filter(keyword__startswith="kw").order_by("keyword"))
    SearchResult.objects.bulk_create([
        SearchResult(keyword=keywords[i], country="us", difficulty_score=40, popularity_score=50,
                     classification="Low Volume" if i < low_volume else "Moderate")
        for i in range(pairs)
    ])


class CleanupSuggestionTest(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        override = override_settings(DATA_DIR=Path(self._tmp.name))
        override.enable()
        self.addCleanup(override.disable)
        self.addCleanup(self._tmp.cleanup)

    def latest(self):
        return SearchResult.objects.all()

    def test_no_banner_under_an_hour_or_under_ten_candidates(self):
        seed(700, 50)
        self.assertIsNone(cleanup_suggestion(self.latest()))
        SearchResult.objects.all().delete()
        Keyword.objects.all().delete()
        seed(720, 9)
        self.assertIsNone(cleanup_suggestion(self.latest()))

    def test_the_banner_numbers_and_link(self):
        seed(800, 50)
        data = cleanup_suggestion(self.latest(), app_id=3)
        self.assertEqual(data["pairs"], 800)
        self.assertEqual(data["candidates"], 50)
        self.assertEqual(data["refresh_text"], "about 1 h 7 min")
        self.assertIn("insight=Low+Volume", data["filter_url"])
        self.assertIn("insight=Avoid", data["filter_url"])
        self.assertIn("app=3", data["filter_url"])
        self.assertTrue(data["filter_url"].endswith("#history-section"))

    def test_snooze_hides_it_for_thirty_days(self):
        seed(800, 50)
        resp = self.client.post(reverse("aso:keyword_cleanup_snooze"))
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(ui_state.is_dismissed(ui_state.KEYWORD_CLEANUP_BANNER))
        self.assertIsNone(cleanup_suggestion(self.latest()))
        later = timezone.now() + timedelta(days=31)
        with mock.patch("aso.ui_state.datetime") as dt:
            dt.now.return_value = later
            dt.fromisoformat.side_effect = lambda s: __import__("datetime").datetime.fromisoformat(s)
            self.assertFalse(ui_state.is_dismissed(ui_state.KEYWORD_CLEANUP_BANNER))

    def test_dashboard_renders_the_banner(self):
        seed(800, 50)
        resp = self.client.get(reverse("aso:dashboard"))
        self.assertContains(resp, "Your daily refresh is getting long")
        self.assertContains(resp, "re-checks 800 keyword and country pairs")
        self.assertContains(resp, "50 of them are Low Volume or Avoid")

    def test_duration_text(self):
        self.assertEqual(duration_text(20), "less than a minute")
        self.assertEqual(duration_text(48 * 60), "about 48 min")
        self.assertEqual(duration_text(2 * 3600 + 10 * 60), "about 2 h 10 min")
        self.assertEqual(duration_text(3 * 3600), "about 3 h")
