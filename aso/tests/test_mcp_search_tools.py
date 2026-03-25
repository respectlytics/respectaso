from unittest.mock import patch, MagicMock
from django.test import TestCase

from aso.models import App, Keyword, SearchResult
from aso.mcp_server import search_keywords, opportunity_search, refresh_keyword, bulk_refresh_keywords

MOCK_COMPETITORS = [
    {
        "trackId": 111,
        "trackName": "Fitness App",
        "bundleId": "com.fitness.app",
        "artworkUrl100": "https://example.com/icon.png",
        "userRatingCount": 5000,
        "averageUserRating": 4.5,
        "sellerName": "Fitness Co",
        "primaryGenreName": "Health & Fitness",
        "version": "1.0",
        "releaseDate": "2023-01-01T00:00:00Z",
        "price": 0.0,
    }
]


def _mock_itunes():
    """Return a mock ITunesSearchService that returns MOCK_COMPETITORS."""
    svc = MagicMock()
    svc.search_apps.return_value = MOCK_COMPETITORS
    svc.find_app_rank.return_value = None
    svc.lookup_by_id.return_value = None
    return svc


class SearchKeywordsToolTest(TestCase):
    @patch("aso.mcp_server.ITunesSearchService", side_effect=lambda: _mock_itunes())
    def test_creates_search_result(self, _mock):
        result = search_keywords(keywords="fitness", countries=["us"])
        self.assertIn("us", result["results_by_country"])
        self.assertEqual(len(result["results_by_country"]["us"]), 1)
        self.assertEqual(result["results_by_country"]["us"][0]["keyword"], "fitness")
        self.assertEqual(SearchResult.objects.count(), 1)
        self.assertNotIn("opportunity_ranking", result)  # single-country: no ranking key

    @patch("aso.mcp_server.ITunesSearchService", side_effect=lambda: _mock_itunes())
    def test_rejects_more_than_20_keywords(self, _mock):
        result = search_keywords(keywords=",".join(f"kw{i}" for i in range(25)), countries=["us"])
        # Should process exactly 20 (capped at 20, no prior results to skip in clean DB)
        self.assertEqual(
            sum(len(v) for v in result["results_by_country"].values()), 20
        )

    @patch("aso.mcp_server.ITunesSearchService", side_effect=lambda: _mock_itunes())
    def test_multi_country_produces_opportunity_ranking(self, _mock):
        result = search_keywords(keywords="fitness", countries=["us", "gb"])
        self.assertIn("opportunity_ranking", result)
        self.assertEqual(len(result["opportunity_ranking"]), 1)


class OpportunitySearchToolTest(TestCase):
    @patch("aso.mcp_server.COUNTRY_CHOICES", [("us", "United States"), ("gb", "United Kingdom")])
    @patch("aso.mcp_server.ITunesSearchService", side_effect=lambda: _mock_itunes())
    def test_returns_country_ranking(self, _mock):
        result = opportunity_search(keyword="yoga")
        self.assertEqual(result["keyword"], "yoga")
        self.assertEqual(result["total_countries"], 2)
        self.assertIn("results", result)
        self.assertIn("opportunity", result["results"][0])


class RefreshKeywordToolTest(TestCase):
    def setUp(self):
        self.kw = Keyword.objects.create(keyword="running")

    @patch("aso.mcp_server.ITunesSearchService", side_effect=lambda: _mock_itunes())
    def test_creates_new_result(self, _mock):
        result = refresh_keyword(keyword_id=self.kw.id)
        self.assertTrue(result["success"])
        self.assertEqual(result["result"]["keyword"], "running")
        self.assertEqual(SearchResult.objects.count(), 1)

    def test_not_found(self):
        result = refresh_keyword(keyword_id=99999)
        self.assertIn("error", result)


class BulkRefreshKeywordsToolTest(TestCase):
    def setUp(self):
        self.app = App.objects.create(name="My App")
        Keyword.objects.create(keyword="yoga", app=self.app)
        Keyword.objects.create(keyword="meditation", app=self.app)

    @patch("aso.mcp_server.ITunesSearchService", side_effect=lambda: _mock_itunes())
    def test_refreshes_all_for_app(self, _mock):
        result = bulk_refresh_keywords(app_id=self.app.id)
        self.assertTrue(result["success"])
        self.assertEqual(result["refreshed"], 2)

    @patch("aso.mcp_server.ITunesSearchService", side_effect=lambda: _mock_itunes())
    def test_unassigned_only_when_no_app_id(self, _mock):
        Keyword.objects.create(keyword="unassigned_kw")  # no app
        result = bulk_refresh_keywords()
        self.assertEqual(result["refreshed"], 1)  # only the unassigned one
