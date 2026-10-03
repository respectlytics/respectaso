"""Every word of an app's description reaches the code that reads it.

Until this change the iTunes parser cut every description to 200
characters, so the AI Competitor read about a sentence of the app it
analysed. The parser now keeps the whole text; the screens' stored form
(display_snippet) still keeps 200 characters, exactly as before, and no
score reads a description at all.
"""

import json
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from aso.keyword_scoring import row_fields, store_read
from aso.models import (
    App,
    Keyword,
    OpportunityScan,
    OpportunityScanResult,
    SearchResult,
)
from aso.popularity import resolve_popularity
from aso.services import (
    DESCRIPTION_SNIPPET_CHARS,
    STORED_APP_KEYS,
    DifficultyCalculator,
    ITunesSearchService,
    display_snippet,
    display_snippets,
)

# The module, not the class: importing the class would run its tests twice.
from aso.tests import test_ssr_fallback

LONG = ("Track every habit and routine you care about, day after day. " * 80)[:3990] + " ENDOFTEXT"


def _raw(description=LONG, **extra):
    return {
        "trackId": 42, "trackName": "Habit App", "artworkUrl100": "", "averageUserRating": 4.5,
        "userRatingCount": 900, "releaseDate": "2021-01-01T00:00:00Z",
        "currentVersionReleaseDate": "2026-09-01T00:00:00Z", "primaryGenreName": "Productivity",
        "formattedPrice": "Free", "description": description, "version": "5.2",
        "sellerName": "Dev", "bundleId": "com.dev.habit", "trackViewUrl": "https://apps.apple.com/app/id42",
        **extra,
    }


class _Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload
        self.headers = {}

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class TheParserKeepsEveryWordTest(SimpleTestCase):
    def test_the_description_is_whole(self):
        self.assertEqual(len(LONG), 4000)
        self.assertEqual(ITunesSearchService._parse_app(_raw())["description"], LONG)

    def test_the_version_is_kept(self):
        self.assertEqual(ITunesSearchService._parse_app(_raw())["version"], "5.2")

    def test_a_lookup_returns_the_whole_description(self):
        with patch("aso.services.requests.get", return_value=_Response({"results": [_raw()]})):
            app = ITunesSearchService().lookup_by_id(42, country="us")
        self.assertEqual(app["description"], LONG)

    def test_a_search_returns_the_whole_description(self):
        with patch("aso.services.requests.get", return_value=_Response({"results": [_raw()]})):
            apps = ITunesSearchService().search_apps("habit", country="us", limit=25)
        self.assertEqual(apps[0]["description"], LONG)


class TheStoredFormIsUnchangedTest(SimpleTestCase):
    def test_the_snippet_is_the_form_stored_until_now(self):
        stored = display_snippet(ITunesSearchService._parse_app(_raw()))
        self.assertEqual(stored["description"], LONG[:DESCRIPTION_SNIPPET_CHARS] + "...")
        self.assertEqual(len(stored["description"]), DESCRIPTION_SNIPPET_CHARS + 3)
        self.assertNotIn("version", stored)
        self.assertEqual(set(stored), set(STORED_APP_KEYS) - {"_data_source"})

    def test_the_search_tag_stays(self):
        stored = display_snippet({**ITunesSearchService._parse_app(_raw()), "_data_source": "itunes"})
        self.assertEqual(stored["_data_source"], "itunes")

    def test_twice_is_once(self):
        once = display_snippet(ITunesSearchService._parse_app(_raw()))
        self.assertEqual(display_snippet(once), once)

    def test_the_input_is_not_changed(self):
        app = ITunesSearchService._parse_app(_raw())
        display_snippet(app)
        self.assertEqual(app["description"], LONG)

    def test_a_short_description_and_odd_values_pass_through(self):
        self.assertEqual(display_snippet({"description": "Short."})["description"], "Short.")
        self.assertIsNone(display_snippet(None))
        self.assertEqual(display_snippets([]), [])
        self.assertIsNone(display_snippets(None))

    def test_the_stored_size_is_what_it_was(self):
        old_form = {**ITunesSearchService._parse_app(_raw()), "description": LONG[:200] + "..."}
        old_form.pop("version")
        self.assertEqual(json.dumps(display_snippet(ITunesSearchService._parse_app(_raw()))), json.dumps(old_form))


class SearchHistoryStoresTheDisplayFormTest(TestCase):
    def test_row_fields_store_the_snippet(self):
        apps = [ITunesSearchService._parse_app(_raw())]
        read = {"popularity": resolve_popularity(apps, "habit", "us"), "difficulty_score": 10,
                "difficulty_breakdown": {}, "competitors": apps}
        self.assertEqual(row_fields(read)["competitors_data"][0]["description"], LONG[:200] + "...")
        self.assertEqual(read["competitors"][0]["description"], LONG)

    def test_a_stored_search_row_holds_the_snippet(self):
        keyword = Keyword.objects.create(keyword="habit tracker", app=App.objects.create(name="Mine"))
        apps = [ITunesSearchService._parse_app(_raw())]
        read = {"popularity": resolve_popularity(apps, "habit tracker", "us"), "difficulty_score": 10,
                "difficulty_breakdown": {}, "competitors": apps}
        store_read(keyword, "us", row_fields(read), app_rank=None, twin_rank=lambda twin: None)
        stored = SearchResult.objects.get(keyword=keyword).competitors_data[0]
        self.assertEqual(stored["description"], LONG[:200] + "...")
        self.assertNotIn("version", stored)

    def test_an_opportunity_scan_row_holds_the_snippet(self):
        from aso.opportunity_scans import _store

        scan = OpportunityScan.objects.create(keyword="habit tracker", countries=["us"])
        apps = [ITunesSearchService._parse_app(_raw())]
        scored = {"popularity": resolve_popularity(apps, "habit tracker", "us"), "difficulty_score": 10,
                  "difficulty_breakdown": {}, "competitors": apps, "app_rank": None}
        _store(scan.pk, "habit tracker", 0, "us", scored)
        stored = OpportunityScanResult.objects.get(scan=scan).competitors_data[0]
        self.assertEqual(stored["description"], LONG[:200] + "...")


class NoScoreReadsTheDescriptionTest(TestCase):
    def test_the_scores_are_the_same_for_a_short_and_a_whole_description(self):
        for keyword in ("habit tracker", "meditation", "budget planner"):
            with self.subTest(keyword=keyword):
                short = test_ssr_fallback.ScoringParityTest._build_mock_apps(keyword)
                whole = [{**app, "description": LONG} for app in short]
                self.assertEqual(DifficultyCalculator().calculate(short, keyword=keyword),
                                 DifficultyCalculator().calculate(whole, keyword=keyword))
                self.assertEqual(resolve_popularity(short, keyword, "us").internal,
                                 resolve_popularity(whole, keyword, "us").internal)
