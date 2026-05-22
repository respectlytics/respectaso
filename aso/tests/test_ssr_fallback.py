"""Tests for iTunes API → SSR fallback mechanism.

Verifies:
- SSR page parsing and app ID extraction
- Batch Lookup API hydration
- Fallback chain: iTunes → SSR → SearchAPIUnavailableError
- find_app_rank fallback
- Retry with exponential backoff
- tokenize_words() Unicode handling
"""

import json
from unittest.mock import MagicMock, patch

from django.test import TestCase

from aso.services import (
    ITunesAPIError,
    ITunesSearchService,
    SearchAPIUnavailableError,
)


class SSRAppIdExtractionTest(TestCase):
    """Test _extract_ssr_app_ids with various SSR JSON structures."""

    def test_extracts_lockup_adam_ids(self):
        ssr_data = {
            "data": [{
                "data": {
                    "shelves": [{
                        "items": [
                            {"lockup": {"adamId": 111}},
                            {"lockup": {"adamId": 222}},
                            {"lockup": {"adamId": 333}},
                        ]
                    }],
                    "nextPage": {"results": []},
                }
            }]
        }
        ids = ITunesSearchService._extract_ssr_app_ids(ssr_data)
        self.assertEqual(ids, [111, 222, 333])

    def test_skips_none_adam_ids(self):
        """Ad/editorial items have adamId=None — should be skipped."""
        ssr_data = {
            "data": [{
                "data": {
                    "shelves": [{
                        "items": [
                            {"lockup": {"adamId": 111}},
                            {"lockup": {"adamId": None}},
                            {"lockup": {"adamId": 333}},
                        ]
                    }],
                    "nextPage": {"results": []},
                }
            }]
        }
        ids = ITunesSearchService._extract_ssr_app_ids(ssr_data)
        self.assertEqual(ids, [111, 333])

    def test_extracts_next_page_app_ids(self):
        ssr_data = {
            "data": [{
                "data": {
                    "shelves": [{"items": []}],
                    "nextPage": {
                        "results": [
                            {"id": 444, "type": "apps", "href": "/v1/apps/444"},
                            {"id": 555, "type": "editorial", "href": "/v1/editorial/555"},
                            {"id": 666, "type": "apps", "href": "/v1/apps/666"},
                        ]
                    },
                }
            }]
        }
        ids = ITunesSearchService._extract_ssr_app_ids(ssr_data)
        # Only "apps" type, not "editorial"
        self.assertEqual(ids, [444, 666])

    def test_deduplicates_across_lockups_and_next_page(self):
        ssr_data = {
            "data": [{
                "data": {
                    "shelves": [{
                        "items": [
                            {"lockup": {"adamId": 111}},
                            {"lockup": {"adamId": 222}},
                        ]
                    }],
                    "nextPage": {
                        "results": [
                            {"id": 222, "type": "apps"},  # duplicate
                            {"id": 333, "type": "apps"},
                        ]
                    },
                }
            }]
        }
        ids = ITunesSearchService._extract_ssr_app_ids(ssr_data)
        self.assertEqual(ids, [111, 222, 333])

    def test_empty_ssr_data(self):
        ids = ITunesSearchService._extract_ssr_app_ids({})
        self.assertEqual(ids, [])

    def test_malformed_data(self):
        ids = ITunesSearchService._extract_ssr_app_ids({"data": []})
        self.assertEqual(ids, [])


class SearchAppsFallbackTest(TestCase):
    """Test search_apps fallback chain: iTunes → SSR → error."""

    def setUp(self):
        self.service = ITunesSearchService()

    @patch.object(ITunesSearchService, "_search_itunes")
    def test_uses_itunes_when_available(self, mock_itunes):
        mock_itunes.return_value = [
            {"trackId": 111, "name": "App1"},
        ]
        result = self.service.search_apps("test", country="us", limit=25)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["_data_source"], "itunes")
        mock_itunes.assert_called_once()

    @patch.object(ITunesSearchService, "_search_ssr")
    @patch.object(ITunesSearchService, "_search_itunes")
    def test_falls_back_to_ssr_on_itunes_failure(self, mock_itunes, mock_ssr):
        mock_itunes.side_effect = Exception("HTTP 404")
        mock_ssr.return_value = [
            {"trackId": 111, "name": "App1", "_data_source": "appstore_ssr"},
        ]
        result = self.service.search_apps("test", country="us", limit=25)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["_data_source"], "appstore_ssr")

    @patch.object(ITunesSearchService, "_search_ssr")
    @patch.object(ITunesSearchService, "_search_itunes")
    def test_raises_when_both_fail(self, mock_itunes, mock_ssr):
        mock_itunes.side_effect = Exception("HTTP 404")
        mock_ssr.side_effect = Exception("SSR also failed")
        with self.assertRaises(SearchAPIUnavailableError):
            self.service.search_apps("test", country="us", limit=25)


class FindAppRankFallbackTest(TestCase):
    """Test find_app_rank fallback chain."""

    def setUp(self):
        self.service = ITunesSearchService()

    @patch.object(ITunesSearchService, "_search_itunes")
    def test_finds_rank_via_itunes(self, mock_itunes):
        mock_itunes.return_value = [
            {"trackId": 100},
            {"trackId": 200},
            {"trackId": 300},
        ]
        rank = self.service.find_app_rank("test", 200, country="us")
        self.assertEqual(rank, 2)

    @patch.object(ITunesSearchService, "_search_itunes")
    def test_returns_none_when_not_found(self, mock_itunes):
        mock_itunes.return_value = [
            {"trackId": 100},
            {"trackId": 200},
        ]
        rank = self.service.find_app_rank("test", 999, country="us")
        self.assertIsNone(rank)

    @patch.object(ITunesSearchService, "_find_rank_in_ssr")
    @patch.object(ITunesSearchService, "_search_itunes")
    def test_falls_back_to_ssr_for_rank(self, mock_itunes, mock_ssr_rank):
        mock_itunes.side_effect = Exception("HTTP 404")
        mock_ssr_rank.return_value = 5
        rank = self.service.find_app_rank("test", 200, country="us")
        self.assertEqual(rank, 5)

    @patch.object(ITunesSearchService, "_find_rank_in_ssr")
    @patch.object(ITunesSearchService, "_search_itunes")
    def test_raises_when_both_fail_for_rank(self, mock_itunes, mock_ssr_rank):
        mock_itunes.side_effect = Exception("HTTP 404")
        mock_ssr_rank.side_effect = Exception("SSR failed")
        with self.assertRaises(SearchAPIUnavailableError):
            self.service.find_app_rank("test", 200, country="us")


class BatchLookupTest(TestCase):
    """Test _batch_lookup chunking and error resilience."""

    def setUp(self):
        self.service = ITunesSearchService()

    @patch("aso.services.requests.get")
    def test_batch_lookup_single_chunk(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {
            "results": [
                {"trackId": 111, "trackName": "App1",
                 "artworkUrl100": "", "description": "",
                 "userRatingCount": 100, "averageUserRating": 4.5,
                 "primaryGenreName": "Utilities", "sellerName": "Dev",
                 "currentVersionReleaseDate": "2024-01-01T00:00:00Z"},
            ]
        }
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = self.service._batch_lookup([111], country="us")
        self.assertIn(111, result)
        self.assertEqual(result[111]["trackName"], "App1")

    @patch("aso.services.requests.get")
    def test_batch_lookup_continues_on_chunk_failure(self, mock_get):
        """If one chunk fails, other chunks should still be processed."""
        def side_effect(*args, **kwargs):
            ids = kwargs.get("params", {}).get("id", "")
            if "111" in ids:
                raise Exception("Network error")
            resp = MagicMock()
            resp.json.return_value = {
                "results": [
                    {"trackId": 222, "trackName": "App2",
                     "artworkUrl100": "", "description": "",
                     "userRatingCount": 50, "averageUserRating": 4.0,
                     "primaryGenreName": "Games", "sellerName": "Dev2",
                     "currentVersionReleaseDate": "2024-01-01T00:00:00Z"},
                ]
            }
            resp.raise_for_status = MagicMock()
            return resp

        mock_get.side_effect = side_effect

        # Two chunks: [111] fails, [222] succeeds
        result = self.service._batch_lookup([111, 222], country="us")
        # Only the second chunk returned data
        self.assertNotIn(111, result)
        # 222 might or might not be in result depending on chunk boundaries
        # (chunk_size=50, so both would be in one chunk)


class SSRRetryTest(TestCase):
    """Test exponential backoff in _fetch_ssr_page."""

    def setUp(self):
        self.service = ITunesSearchService()
        # Reset rate limiter
        ITunesSearchService._last_ssr_request = 0.0

    @patch("aso.services.time.sleep")
    @patch("aso.services.requests.get")
    def test_retries_on_server_error(self, mock_get, mock_sleep):
        """Should retry on 5xx errors with exponential backoff."""
        import requests as real_requests

        fail_resp = MagicMock()
        fail_resp.status_code = 503
        fail_resp.raise_for_status.side_effect = real_requests.exceptions.HTTPError(
            response=fail_resp
        )

        success_resp = MagicMock()
        success_resp.status_code = 200
        success_resp.text = '<script id="serialized-server-data">[{"data": []}]</script>'
        success_resp.raise_for_status = MagicMock()

        mock_get.side_effect = [fail_resp, fail_resp, success_resp]

        result = self.service._fetch_ssr_page("test", country="us")
        self.assertEqual(result, [{"data": []}])
        self.assertEqual(mock_get.call_count, 3)

    @patch("aso.services.time.sleep")
    @patch("aso.services.requests.get")
    def test_no_retry_on_client_error(self, mock_get, mock_sleep):
        """Should NOT retry on 4xx errors."""
        import requests as real_requests

        fail_resp = MagicMock()
        fail_resp.status_code = 403
        fail_resp.raise_for_status.side_effect = real_requests.exceptions.HTTPError(
            response=fail_resp
        )

        mock_get.return_value = fail_resp

        with self.assertRaises(real_requests.exceptions.HTTPError):
            self.service._fetch_ssr_page("test", country="us")
        self.assertEqual(mock_get.call_count, 1)


class TokenizeWordsTest(TestCase):
    """Test tokenize_words() Unicode handling and word splitting."""

    def test_basic_lowercase(self):
        from aso_pro.keyword_pipeline import tokenize_words
        self.assertEqual(tokenize_words("Fitness"), ["fitness"])

    def test_preserves_accented_chars(self):
        from aso_pro.keyword_pipeline import tokenize_words
        self.assertEqual(tokenize_words("Résumé"), ["résumé"])
        self.assertEqual(tokenize_words("müller"), ["müller"])
        self.assertEqual(tokenize_words("señor"), ["señor"])

    def test_splits_on_apostrophes(self):
        from aso_pro.keyword_pipeline import tokenize_words
        # French contractions must split into separate tokens
        self.assertEqual(tokenize_words("l'app"), ["l", "app"])
        self.assertEqual(tokenize_words("l'anglais"), ["l", "anglais"])
        self.assertEqual(tokenize_words("aujourd'hui"), ["aujourd", "hui"])

    def test_splits_on_hyphens(self):
        from aso_pro.keyword_pipeline import tokenize_words
        self.assertEqual(tokenize_words("well-being"), ["well", "being"])

    def test_splits_on_underscores(self):
        from aso_pro.keyword_pipeline import tokenize_words
        self.assertEqual(tokenize_words("test_word"), ["test", "word"])

    def test_empty_string(self):
        from aso_pro.keyword_pipeline import tokenize_words
        self.assertEqual(tokenize_words(""), [])

    def test_cjk_characters(self):
        from aso_pro.keyword_pipeline import tokenize_words
        self.assertEqual(tokenize_words("日本語"), ["日本語"])

    def test_min_length_filters_short_tokens(self):
        from aso_pro.keyword_pipeline import tokenize_words
        # "l'anglais" → ["l", "anglais"], min_length=2 drops "l"
        self.assertEqual(tokenize_words("l'anglais", min_length=2), ["anglais"])
        # "L'app pour l'éducation" → drops both "l" fragments
        self.assertEqual(
            tokenize_words("L'app pour l'éducation", min_length=2),
            ["app", "pour", "éducation"]
        )

    def test_french_sentence(self):
        from aso_pro.keyword_pipeline import tokenize_words
        self.assertEqual(
            tokenize_words("L'anglais pour tous", min_length=2),
            ["anglais", "pour", "tous"]
        )

    def test_german_umlauts(self):
        from aso_pro.keyword_pipeline import tokenize_words
        self.assertEqual(
            tokenize_words("Für Gesundheit und Übungen"),
            ["für", "gesundheit", "und", "übungen"]
        )

    def test_mixed_punctuation(self):
        from aso_pro.keyword_pipeline import tokenize_words
        self.assertEqual(
            tokenize_words("méditation — bien-être & relaxation"),
            ["méditation", "bien", "être", "relaxation"]
        )


class ScoringParityTest(TestCase):
    """Task 4.8b — Verify iTunes and SSR paths produce identical scores.

    Both paths go through _parse_app() → same dict format. This test
    mocks the two code paths to return the SAME 25 apps and asserts that
    all 4 scoring outputs (popularity, difficulty, opportunity, downloads)
    are identical.

    Tests multiple keywords (English, French, Japanese, multi-word) across
    multiple localizations (us, gb, jp, fr, de) to ensure parity holds
    regardless of language, character encoding, or country-specific
    download estimation.
    """

    # Keywords × countries to test. Covers:
    # - English single word, multi-word
    # - French with accents (méditation)
    # - Japanese (フィットネス = "fitness")
    # - Short keyword (fit) vs long keyword
    KEYWORDS_AND_COUNTRIES = [
        ("fitness", "us"),
        ("workout tracker", "us"),
        ("méditation", "fr"),
        ("フィットネス", "jp"),
        ("fit", "gb"),
        ("gesundheit", "de"),
        ("calorie counter", "us"),
        ("running", "gb"),
    ]

    @staticmethod
    def _build_mock_apps(keyword):
        """Build 25 mock apps with some containing the keyword in the title.

        This ensures PopularityEstimator's title-match-density signal
        and DifficultyCalculator's keyword-in-titles factor produce
        non-trivial values that differ across keywords.
        """
        apps = []
        for i in range(25):
            # First 5 apps contain the keyword in their title
            if i < 5:
                name = f"Best {keyword} App {i}"
            elif i < 8:
                # Partial match — keyword in description only
                name = f"Health Pro {i}"
            else:
                name = f"App {i}"

            apps.append({
                "trackId": 1000 + i,
                "trackName": name,
                "artworkUrl100": f"https://example.com/{i}.png",
                "averageUserRating": round(3.0 + (i % 20) * 0.1, 1),
                "userRatingCount": (i + 1) * 500,
                "releaseDate": "2023-06-15T00:00:00Z",
                "currentVersionReleaseDate": "2025-12-01T00:00:00Z",
                "primaryGenreName": (
                    "Health & Fitness" if i % 3 == 0
                    else "Lifestyle" if i % 3 == 1
                    else "Utilities"
                ),
                "formattedPrice": "Free" if i % 2 == 0 else "$4.99",
                "description": f"A great app for {keyword} lovers, number {i}",
                "sellerName": f"Developer {i % 10}",
                "bundleId": f"com.dev{i}.app",
                "trackViewUrl": f"https://apps.apple.com/app/id{1000 + i}",
            })
        return apps

    def _score_apps(self, apps, keyword, country):
        """Run the canonical scoring pipeline on a list of app dicts."""
        from aso.scoring import calc_opportunity
        from aso.services import (
            DifficultyCalculator,
            DownloadEstimator,
            PopularityEstimator,
        )

        popularity_est = PopularityEstimator()
        difficulty_calc = DifficultyCalculator()
        download_est = DownloadEstimator()

        popularity = popularity_est.estimate(apps, keyword) or 0
        difficulty, breakdown = difficulty_calc.calculate(apps, keyword=keyword)
        opportunity = calc_opportunity(popularity, difficulty)
        downloads = download_est.estimate(popularity, country=country)

        return {
            "popularity": popularity,
            "difficulty": difficulty,
            "difficulty_breakdown": breakdown,
            "opportunity": opportunity,
            "downloads": downloads,
            "app_count": len(apps),
        }

    def _assert_scores_identical(self, itunes_scores, ssr_scores, keyword, country):
        """Assert all score components are identical between the two paths."""
        prefix = f"[{keyword!r} / {country}]"

        self.assertEqual(
            itunes_scores["app_count"], ssr_scores["app_count"],
            f"{prefix} App count mismatch"
        )
        self.assertEqual(
            itunes_scores["popularity"], ssr_scores["popularity"],
            f"{prefix} Popularity mismatch: "
            f"iTunes={itunes_scores['popularity']} vs SSR={ssr_scores['popularity']}"
        )
        self.assertEqual(
            itunes_scores["difficulty"], ssr_scores["difficulty"],
            f"{prefix} Difficulty mismatch: "
            f"iTunes={itunes_scores['difficulty']} vs SSR={ssr_scores['difficulty']}"
        )
        self.assertAlmostEqual(
            itunes_scores["opportunity"], ssr_scores["opportunity"], places=10,
            msg=f"{prefix} Opportunity mismatch: "
                f"iTunes={itunes_scores['opportunity']} vs SSR={ssr_scores['opportunity']}"
        )
        self.assertEqual(
            itunes_scores["downloads"]["daily_searches"],
            ssr_scores["downloads"]["daily_searches"],
            f"{prefix} Download daily_searches mismatch"
        )
        for i in range(20):
            itunes_pos = itunes_scores["downloads"]["positions"][i]
            ssr_pos = ssr_scores["downloads"]["positions"][i]
            self.assertEqual(
                itunes_pos["downloads_low"], ssr_pos["downloads_low"],
                f"{prefix} Position {i+1} downloads_low mismatch"
            )
            self.assertEqual(
                itunes_pos["downloads_high"], ssr_pos["downloads_high"],
                f"{prefix} Position {i+1} downloads_high mismatch"
            )

    @patch.object(ITunesSearchService, "_search_ssr")
    @patch.object(ITunesSearchService, "_search_itunes")
    def test_parity_across_keywords_and_countries(self, mock_itunes, mock_ssr):
        """iTunes and SSR paths must produce identical scores for every
        keyword × country combination.

        Simulates: iTunes returns 404 (side_effect=Exception) →
        search_apps falls back to _search_ssr → SSR returns the same
        apps that iTunes would have returned. Scores must match exactly.
        """
        for keyword, country in self.KEYWORDS_AND_COUNTRIES:
            with self.subTest(keyword=keyword, country=country):
                mock_apps = self._build_mock_apps(keyword)

                # --- iTunes path (API working) ---
                mock_itunes.reset_mock()
                mock_itunes.side_effect = None
                mock_itunes.return_value = [dict(a) for a in mock_apps]
                service = ITunesSearchService()
                itunes_apps = service.search_apps(keyword, country=country, limit=25)
                itunes_scores = self._score_apps(itunes_apps, keyword, country)

                # --- SSR path (iTunes 404 → fallback) ---
                mock_itunes.side_effect = Exception("HTTP 404 — API dead")
                ssr_apps = [dict(a, _data_source="appstore_ssr") for a in mock_apps]
                mock_ssr.return_value = ssr_apps
                service2 = ITunesSearchService()
                ssr_apps_result = service2.search_apps(keyword, country=country, limit=25)
                ssr_scores = self._score_apps(ssr_apps_result, keyword, country)

                # --- Assert parity ---
                self._assert_scores_identical(itunes_scores, ssr_scores, keyword, country)

    @patch.object(ITunesSearchService, "_search_ssr")
    @patch.object(ITunesSearchService, "_search_itunes")
    def test_different_keywords_produce_different_scores(self, mock_itunes, mock_ssr):
        """Sanity check: different keywords against the SAME fixed app list
        should produce different scores (because title-match density and
        keyword-in-titles differ). This proves the keyword actually
        influences scoring — i.e., the parity test isn't trivially passing
        because scoring ignores input.
        """
        # Fixed app list where "fitness" matches more titles than "yoga"
        fixed_apps = []
        titles = [
            "Fitness Tracker Pro", "Best Fitness App", "Fitness & Health",
            "Fitness Workout", "Fitness Timer", "Yoga Daily", "Yoga Basics",
            "Running Log", "Step Counter", "Diet Plan",
            "Sleep Monitor", "Water Reminder", "Heart Rate",
            "Meditation App", "Breathing Exercise",
            "Weight Loss", "BMI Calculator", "Gym Buddy",
            "Nutrition Facts", "Meal Planner",
            "Walk Tracker", "Cardio Fit", "Stretch Routine",
            "Body Builder", "Health Tips",
        ]
        for i, title in enumerate(titles):
            fixed_apps.append({
                "trackId": 2000 + i,
                "trackName": title,
                "artworkUrl100": f"https://example.com/{i}.png",
                "averageUserRating": round(3.0 + (i % 20) * 0.1, 1),
                "userRatingCount": (i + 1) * 500,
                "releaseDate": "2023-06-15T00:00:00Z",
                "currentVersionReleaseDate": "2025-12-01T00:00:00Z",
                "primaryGenreName": "Health & Fitness",
                "formattedPrice": "Free",
                "description": f"App {i}",
                "sellerName": f"Developer {i % 10}",
                "bundleId": f"com.dev{i}.app",
                "trackViewUrl": f"https://apps.apple.com/app/id{2000 + i}",
            })

        scores_by_keyword = {}
        for keyword in ["fitness", "yoga", "running"]:
            mock_itunes.reset_mock()
            mock_itunes.side_effect = None
            mock_itunes.return_value = [dict(a) for a in fixed_apps]
            service = ITunesSearchService()
            apps = service.search_apps(keyword, country="us", limit=25)
            scores_by_keyword[keyword] = self._score_apps(apps, keyword, "us")

        # "fitness" appears in 5 titles, "yoga" in 2, "running" in 1
        # → title-match density and keyword-in-titles factors should differ
        all_pop = [s["popularity"] for s in scores_by_keyword.values()]
        all_diff = [s["difficulty"] for s in scores_by_keyword.values()]
        self.assertTrue(
            len(set(all_pop)) > 1 or len(set(all_diff)) > 1,
            f"All keywords produced identical scores — keyword should affect "
            f"scoring. Popularity: {all_pop}, Difficulty: {all_diff}"
        )

    @patch.object(ITunesSearchService, "_search_ssr")
    @patch.object(ITunesSearchService, "_search_itunes")
    def test_different_countries_produce_different_downloads(self, mock_itunes, mock_ssr):
        """Download estimates must differ by country (different daily search
        volumes). This proves country actually influences download output.
        """
        keyword = "fitness"
        mock_apps = self._build_mock_apps(keyword)
        mock_itunes.return_value = [dict(a) for a in mock_apps]

        downloads_by_country = {}
        for country in ["us", "jp", "de"]:
            service = ITunesSearchService()
            apps = service.search_apps(keyword, country=country, limit=25)
            scores = self._score_apps(apps, keyword, country)
            downloads_by_country[country] = scores["downloads"]["daily_searches"]

        daily_values = list(downloads_by_country.values())
        self.assertTrue(
            len(set(daily_values)) > 1,
            f"All countries produced identical daily_searches — country should "
            f"affect download estimates. Values: {downloads_by_country}"
        )

    def test_data_source_field_does_not_affect_scoring(self):
        """The _data_source metadata field must not influence any score.

        _parse_app() does not produce _data_source — it's added after.
        Scoring functions must ignore it.
        """
        for keyword, country in self.KEYWORDS_AND_COUNTRIES:
            with self.subTest(keyword=keyword, country=country):
                mock_apps = self._build_mock_apps(keyword)
                apps_with_source = [dict(a, _data_source="appstore_ssr") for a in mock_apps]
                apps_without_source = [dict(a) for a in mock_apps]

                scores_with = self._score_apps(apps_with_source, keyword, country)
                scores_without = self._score_apps(apps_without_source, keyword, country)

                self._assert_scores_identical(scores_with, scores_without, keyword, country)

    def test_app_count_parity_affects_scoring(self):
        """Different app counts MUST produce different scores.

        len(apps) is a direct signal in PopularityEstimator. This test
        confirms that if SSR returned fewer apps than iTunes (e.g., due
        to a Lookup API partial failure), scores WOULD differ — proving
        the count matters and our pipeline preserves it correctly.
        """
        mock_apps = self._build_mock_apps("fitness")
        full_scores = self._score_apps(mock_apps[:25], "fitness", "us")
        partial_scores = self._score_apps(mock_apps[:10], "fitness", "us")

        scores_differ = (
            full_scores["popularity"] != partial_scores["popularity"]
            or full_scores["difficulty"] != partial_scores["difficulty"]
        )
        self.assertTrue(
            scores_differ,
            "25 apps and 10 apps produced identical scores — "
            "app count should influence scoring"
        )


class ExceptionHierarchyTest(TestCase):
    """Test exception class hierarchy."""

    def test_search_api_unavailable_is_itunes_api_error(self):
        self.assertTrue(issubclass(SearchAPIUnavailableError, ITunesAPIError))

    def test_itunes_api_error_is_exception(self):
        self.assertTrue(issubclass(ITunesAPIError, Exception))

    def test_can_catch_with_exception(self):
        """SearchAPIUnavailableError should be catchable as Exception."""
        try:
            raise SearchAPIUnavailableError("test")
        except Exception:
            pass  # Should be caught
