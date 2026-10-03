"""The one App Store search behind every score and rank
(ITunesSearchService.search_ranked).

Free-tier test: no aso_pro import.
"""

from unittest import mock

from django.test import SimpleTestCase

from aso.services import (
    SCORING_POOL,
    ITunesRateLimited,
    ITunesSearchService,
    SearchAPIUnavailableError,
)


def parsed(n, first_id=1000):
    return [{"trackId": first_id + i, "trackName": f"App {i}"} for i in range(n)]


class SearchRankedTest(SimpleTestCase):
    def setUp(self):
        self.service = ITunesSearchService()

    def test_one_itunes_search_of_200_gives_order_first_25_and_count(self):
        with mock.patch.object(self.service, "_search_itunes", return_value=parsed(180)) as search:
            result = self.service.search_ranked("habit tracker", country="us")
        search.assert_called_once_with("habit tracker", country="us", limit=200)
        self.assertEqual(result.result_count, 180)
        self.assertEqual(len(result.ranked_ids), 180)
        self.assertEqual(len(result.apps), SCORING_POOL)
        self.assertEqual([a["trackId"] for a in result.apps], result.ranked_ids[:25])
        self.assertEqual(result.source, "itunes")
        self.assertEqual(result.apps[0]["_data_source"], "itunes")

    def test_an_app_listed_twice_counts_once(self):
        apps = parsed(3) + [{"trackId": 1001, "trackName": "App 1"}]
        with mock.patch.object(self.service, "_search_itunes", return_value=apps):
            result = self.service.search_ranked("x", country="us")
        self.assertEqual(result.ranked_ids, [1000, 1001, 1002])

    def test_the_page_fallback_looks_up_only_the_first_25(self):
        page_ids = list(range(1000, 1060))
        looked_up = {i: {"trackId": i, "trackName": f"App {i}"} for i in page_ids[:25]}
        with mock.patch.object(self.service, "_search_itunes", side_effect=RuntimeError("down")), \
                mock.patch.object(self.service, "_fetch_ssr_page", return_value={}), \
                mock.patch.object(self.service, "_extract_ssr_app_ids", return_value=page_ids), \
                mock.patch.object(self.service, "_batch_lookup", return_value=looked_up) as lookup:
            result = self.service.search_ranked("x", country="us")
        lookup.assert_called_once_with(page_ids[:25], country="us")
        self.assertEqual(result.ranked_ids, page_ids)
        self.assertEqual(result.source, "appstore_ssr")

    def test_an_app_the_lookup_missed_leaves_the_order_too(self):
        page_ids = [1000, 1001, 1002, 1003]
        looked_up = {i: {"trackId": i} for i in (1000, 1002, 1003)}
        with mock.patch.object(self.service, "_search_itunes", side_effect=RuntimeError("down")), \
                mock.patch.object(self.service, "_fetch_ssr_page", return_value={}), \
                mock.patch.object(self.service, "_extract_ssr_app_ids", return_value=page_ids), \
                mock.patch.object(self.service, "_batch_lookup", return_value=looked_up):
            result = self.service.search_ranked("x", country="us")
        self.assertEqual(result.ranked_ids, [1000, 1002, 1003])
        self.assertEqual([a["trackId"] for a in result.apps], result.ranked_ids)

    def test_a_rate_limit_is_raised_not_answered_from_the_page(self):
        with mock.patch.object(self.service, "_search_itunes", side_effect=ITunesRateLimited("slow")), \
                mock.patch.object(self.service, "_fetch_ssr_page") as page, \
                self.assertRaises(ITunesRateLimited):
            self.service.search_ranked("x", country="us")
        page.assert_not_called()

    def test_both_sources_down_raise_unavailable(self):
        with mock.patch.object(self.service, "_search_itunes", side_effect=RuntimeError("down")), \
                mock.patch.object(self.service, "_fetch_ssr_page", side_effect=RuntimeError("down")), \
                self.assertRaises(SearchAPIUnavailableError):
            self.service.search_ranked("x", country="us")

    def test_no_second_way_to_a_rank_exists(self):
        self.assertFalse(hasattr(ITunesSearchService, "find_app_rank"))
        self.assertFalse(hasattr(ITunesSearchService, "_find_rank_in_ssr"))
