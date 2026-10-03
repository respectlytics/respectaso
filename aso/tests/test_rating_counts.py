"""A rating count reads the same in every competitor list: "50,672".

The Search History list printed the raw number (50672) while the result
card and the Opportunity row formatted it in the browser, in the browser's
own locale. Now one Python function formats it, the History list through
the ``format_number`` filter and the scripts through the ``ratings_text``
the server sends with each competitor (CLEANUPS_PLAN.md, item 4).
"""

import os

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from aso import opportunity_scans
from aso.keyword_scoring import result_payload
from aso.models import (
    App,
    Keyword,
    OpportunityScan,
    OpportunityScanResult,
    SearchResult,
)
from aso.scoring import display_competitors, fmt_count

COMPETITOR = {
    "trackId": 1, "trackName": "Habit Tracker Pro", "sellerName": "Someone",
    "averageUserRating": 4.6, "userRatingCount": 50672, "primaryGenreName": "Health & Fitness",
    "formattedPrice": "Free", "releaseDate": "2020-01-01T00:00:00Z",
    "currentVersionReleaseDate": "2026-09-01T00:00:00Z", "trackViewUrl": "https://apps.apple.com/app/id1",
    "artworkUrl100": "",
}


class FmtCountTest(TestCase):
    def test_words(self):
        self.assertEqual(fmt_count(50672), "50,672")
        self.assertEqual(fmt_count(1234567), "1,234,567")
        self.assertEqual(fmt_count(12), "12")
        self.assertEqual(fmt_count(0), "0")
        self.assertEqual(fmt_count(None), "0")
        self.assertEqual(fmt_count(""), "0")
        self.assertEqual(fmt_count("x"), "0")

    def test_display_competitors_adds_the_text_and_keeps_the_rest(self):
        (app,) = display_competitors([COMPETITOR])
        self.assertEqual(app["ratings_text"], "50,672")
        self.assertEqual(app["userRatingCount"], 50672)
        self.assertNotIn("ratings_text", COMPETITOR)
        self.assertEqual(display_competitors(None), [])


class EveryListPrintsTheSameTest(TestCase):
    def setUp(self):
        app = App.objects.create(name="Habito")
        keyword = Keyword.objects.create(keyword="habit tracker", app=app)
        self.row = SearchResult.objects.create(
            keyword=keyword, country="us", difficulty_score=40, popularity_score=50,
            competitors_data=[COMPETITOR],
        )

    def test_the_search_history_list(self):
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertIn(">50,672</td>", html)
        self.assertNotIn(">50672</td>", html)

    def test_the_result_card(self):
        self.assertEqual(result_payload(self.row)["competitors"][0]["ratings_text"], "50,672")

    def test_the_opportunity_row(self):
        scan = OpportunityScan.objects.create(keyword="habit tracker", countries=["us"], status="completed")
        row = OpportunityScanResult.objects.create(
            scan=scan, country="us", order_index=0, keyword_text="habit tracker",
            popularity_score=50, difficulty_score=40, competitors_data=[COMPETITOR],
        )
        data = opportunity_scans.country_payload(row, heavy=True)
        self.assertEqual(data["competitors"][0]["ratings_text"], "50,672")

    def test_no_script_formats_a_count_itself(self):
        for rel in ("aso/templates/aso/dashboard.html", "static/js/opportunity-scan.js"):
            with open(os.path.join(settings.BASE_DIR, rel)) as f:
                self.assertNotIn("userRatingCount.toLocaleString", f.read(), rel)
