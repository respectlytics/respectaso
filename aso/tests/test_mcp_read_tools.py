from django.test import TestCase
from django.utils import timezone

from aso.models import App, Keyword, SearchResult
from aso.mcp_server import list_apps, list_keywords, get_keyword_scores, get_keyword_trend, get_search_history


class ListAppsToolTest(TestCase):
    def test_empty(self):
        result = list_apps()
        self.assertEqual(result, [])

    def test_returns_app_fields(self):
        App.objects.create(
            name="My App",
            bundle_id="com.test.app",
            track_id=123456,
            store_url="https://apps.apple.com/app/id123456",
            icon_url="https://example.com/icon.png",
            seller_name="Test Dev",
        )
        result = list_apps()
        self.assertEqual(len(result), 1)
        app = result[0]
        self.assertIn("id", app)
        self.assertEqual(app["name"], "My App")
        self.assertEqual(app["bundle_id"], "com.test.app")
        self.assertEqual(app["track_id"], 123456)
        self.assertEqual(app["store_url"], "https://apps.apple.com/app/id123456")
        self.assertEqual(app["icon_url"], "https://example.com/icon.png")
        self.assertEqual(app["seller_name"], "Test Dev")


class ListKeywordsToolTest(TestCase):
    def setUp(self):
        self.app = App.objects.create(name="My App")
        self.kw = Keyword.objects.create(keyword="fitness", app=self.app)
        SearchResult.objects.create(
            keyword=self.kw,
            popularity_score=60,
            difficulty_score=40,
            country="us",
        )

    def test_returns_all_keywords_no_filter(self):
        result = list_keywords()
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["keyword"], "fitness")
        self.assertEqual(len(result[0]["results"]), 1)

    def test_filters_by_app_id(self):
        other_kw = Keyword.objects.create(keyword="running")
        result = list_keywords(app_id=self.app.id)
        keywords = [r["keyword"] for r in result]
        self.assertIn("fitness", keywords)
        self.assertNotIn("running", keywords)

    def test_latest_result_per_country(self):
        # Two results for same keyword+country — only latest should appear
        SearchResult.objects.create(
            keyword=self.kw,
            popularity_score=70,
            difficulty_score=35,
            country="us",
        )
        result = list_keywords(app_id=self.app.id)
        self.assertEqual(len(result[0]["results"]), 1)
        self.assertEqual(result[0]["results"][0]["popularity_score"], 70)


class GetKeywordScoresToolTest(TestCase):
    def setUp(self):
        self.kw = Keyword.objects.create(keyword="yoga")
        self.sr = SearchResult.objects.create(
            keyword=self.kw,
            popularity_score=55,
            difficulty_score=30,
            country="us",
            app_rank=5,
        )

    def test_not_found(self):
        result = get_keyword_scores(keyword_id=99999)
        self.assertIn("error", result)

    def test_returns_scores(self):
        result = get_keyword_scores(keyword_id=self.kw.id)
        self.assertEqual(result["popularity_score"], 55)
        self.assertEqual(result["difficulty_score"], 30)
        self.assertEqual(result["app_rank"], 5)
        self.assertIn("label", result["targeting_advice"])
        self.assertIn("description", result["targeting_advice"])
        self.assertNotIn("css_classes", result["targeting_advice"])

    def test_country_filter(self):
        SearchResult.objects.create(
            keyword=self.kw, popularity_score=10, difficulty_score=80, country="gb"
        )
        result = get_keyword_scores(keyword_id=self.kw.id, country="gb")
        self.assertEqual(result["popularity_score"], 10)


class GetKeywordTrendToolTest(TestCase):
    def setUp(self):
        self.kw = Keyword.objects.create(keyword="meditation")
        SearchResult.objects.create(
            keyword=self.kw, popularity_score=40, difficulty_score=25, country="us"
        )
        SearchResult.objects.create(
            keyword=self.kw, popularity_score=45, difficulty_score=28, country="us"
        )

    def test_returns_data_points(self):
        result = get_keyword_trend(keyword_id=self.kw.id)
        self.assertEqual(len(result), 2)
        self.assertIn("date", result[0])
        self.assertIn("popularity", result[0])
        self.assertIn("difficulty", result[0])

    def test_not_found(self):
        result = get_keyword_trend(keyword_id=99999)
        self.assertIn("error", result[0])

    def test_empty_results_returns_empty_list(self):
        kw = Keyword.objects.create(keyword="empty_keyword")
        result = get_keyword_trend(keyword_id=kw.id)
        self.assertEqual(result, [])


class GetSearchHistoryToolTest(TestCase):
    def setUp(self):
        kw = Keyword.objects.create(keyword="running")
        for i in range(30):
            kw_i = Keyword.objects.create(keyword=f"keyword{i}")
            SearchResult.objects.create(
                keyword=kw_i, popularity_score=i, difficulty_score=i, country="us"
            )

    def test_pagination_default_page(self):
        result = get_search_history()
        self.assertEqual(len(result["results"]), 25)
        self.assertEqual(result["page"], 1)
        self.assertEqual(result["total_pages"], 2)

    def test_page_2(self):
        result = get_search_history(page=2)
        self.assertEqual(result["page"], 2)
        self.assertLessEqual(len(result["results"]), 25)

    def test_country_filter(self):
        kw = Keyword.objects.create(keyword="gb_only")
        SearchResult.objects.create(
            keyword=kw, popularity_score=50, difficulty_score=50, country="gb"
        )
        result = get_search_history(country="gb")
        self.assertEqual(result["total_count"], 1)
