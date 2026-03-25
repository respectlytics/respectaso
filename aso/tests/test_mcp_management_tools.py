from django.test import TestCase

from aso.models import App, Keyword, SearchResult
from aso.mcp_server import add_app, delete_keyword, delete_app


class AddAppToolTest(TestCase):
    def test_creates_app(self):
        result = add_app(name="My App", bundle_id="com.test.app")
        self.assertIn("id", result)
        self.assertEqual(result["name"], "My App")
        self.assertEqual(App.objects.count(), 1)

    def test_duplicate_track_id_returns_error(self):
        App.objects.create(name="Existing", track_id=999)
        result = add_app(name="Duplicate", track_id=999)
        self.assertIn("error", result)
        self.assertEqual(App.objects.count(), 1)

    def test_no_track_id_is_fine(self):
        result = add_app(name="No Track ID")
        self.assertIn("id", result)


class DeleteKeywordToolTest(TestCase):
    def setUp(self):
        self.kw = Keyword.objects.create(keyword="yoga")
        SearchResult.objects.create(keyword=self.kw, difficulty_score=30, country="us")

    def test_deletes_keyword_and_results(self):
        result = delete_keyword(keyword_id=self.kw.id)
        self.assertTrue(result["success"])
        self.assertIn("yoga", result["deleted"])
        self.assertEqual(Keyword.objects.count(), 0)
        self.assertEqual(SearchResult.objects.count(), 0)

    def test_not_found(self):
        result = delete_keyword(keyword_id=99999)
        self.assertIn("error", result)


class DeleteAppToolTest(TestCase):
    def setUp(self):
        self.app = App.objects.create(name="My App")
        self.kw = Keyword.objects.create(keyword="yoga", app=self.app)

    def test_deletes_app_preserves_keywords(self):
        result = delete_app(app_id=self.app.id)
        self.assertTrue(result["success"])
        self.assertIn("My App", result["deleted"])
        self.assertEqual(App.objects.count(), 0)
        # Keyword is preserved, app set to null
        self.kw.refresh_from_db()
        self.assertIsNone(self.kw.app)

    def test_not_found(self):
        result = delete_app(app_id=99999)
        self.assertIn("error", result)
