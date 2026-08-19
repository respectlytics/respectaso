"""Tests for trend plumbing and the impression-share summary section.
(The Top Terms discovery view itself is Pro - see
aso_pro/tests/test_top_terms.py.)"""

import datetime as dt
from unittest import mock

from django.urls import reverse

from aso.apple_ads import storage
from aso.models import (
    App,
    AppleImpressionShare,
    AppleTopTerm,
    Keyword,
    SearchResult,
)
from aso.tests.test_popularity_views import PopularityViewTestBase

WEEK = dt.date(2026, 8, 9)
PREV_WEEK = dt.date(2026, 8, 2)


def _term(term, week=WEEK, country="us", genre="BUSINESS", rank=1,
          popularity=70, tier=4):
    AppleTopTerm.objects.create(
        term=term, country=country, genre=genre, week=week,
        rank_in_genre=rank, popularity_in_genre=90, popularity=popularity,
        popularity_tier=tier,
    )


class TrendPlumbingTest(PopularityViewTestBase):
    def test_dashboard_attaches_apple_trend(self):
        from aso.views import _attach_apple_trends

        self._connect(source="internal")
        storage.save_apple_settings(apple_ads={
            "active_weeks": {"us": WEEK.isoformat()},
        })
        _term("fitness", week=WEEK, popularity=70)
        _term("fitness", week=PREV_WEEK, popularity=62)
        _term("newcomer", week=WEEK, popularity=50, rank=2)
        keyword = Keyword.objects.create(keyword="Fitness")
        result = SearchResult.objects.create(
            keyword=keyword, country="us", difficulty_score=30,
            popularity_score=60,
        )
        keyword2 = Keyword.objects.create(keyword="newcomer")
        result2 = SearchResult.objects.create(
            keyword=keyword2, country="us", difficulty_score=30,
            popularity_score=60,
        )
        _attach_apple_trends([result, result2])
        self.assertEqual(result.apple_trend, 8)
        # Present only this week: coverage change, not a measured trend.
        self.assertIsNone(result2.apple_trend)

    def test_trend_renderer_twins_share_threshold(self):
        import re

        from aso.templatetags.aso_tags import APPLE_TREND_MIN_DELTA

        js_source = open("static/js/popularity-display.js").read()
        match = re.search(r"APPLE_TREND_MIN_DELTA = (\d+)", js_source)
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), APPLE_TREND_MIN_DELTA)

    def test_csv_includes_trend_column(self):
        response = self.client.get(reverse("aso:export_history_csv"))
        self.assertIn(
            "Apple Popularity Trend", response.content.decode().splitlines()[0]
        )


class ImpressionShareSummaryTest(PopularityViewTestBase):
    def test_summary_none_without_rows(self):
        from aso.dashboard_summary import _impression_share_summary

        app = App.objects.create(name="My App", track_id=1)
        self.assertIsNone(_impression_share_summary(app.id))

    def test_summary_rows_with_labels_and_delta(self):
        from aso.dashboard_summary import _impression_share_summary

        app = App.objects.create(name="My App", track_id=1)
        AppleImpressionShare.objects.create(
            app=app, country="us", search_term="travel app", week=WEEK,
            low_share=0.42, high_share=0.42, rank=2, popularity_tier=4,
        )
        AppleImpressionShare.objects.create(
            app=app, country="us", search_term="travel app", week=PREV_WEEK,
            low_share=0.30, high_share=0.30, rank=3, popularity_tier=4,
        )
        AppleImpressionShare.objects.create(
            app=app, country="us", search_term="dominant term", week=WEEK,
            low_share=0.91, high_share=1.0, rank=1, popularity_tier=5,
        )
        summary = _impression_share_summary(app.id)
        self.assertEqual(summary["week"], WEEK)
        by_term = {row["term"]: row for row in summary["rows"]}
        self.assertEqual(by_term["travel app"]["share"], "42%")
        self.assertEqual(by_term["travel app"]["delta"], 12)
        self.assertEqual(by_term["dominant term"]["share"], "91-100%")
