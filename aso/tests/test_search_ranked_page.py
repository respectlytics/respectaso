"""The App Store web search page, search_ranked's fallback when the iTunes
Search API fails (ITunesSearchService.search_ranked_page). Free tier, so
these run in the public repo too.
"""

from unittest import mock

import requests

from aso.services import AppStorePageRefused, ITunesSearchService, SearchAPIUnavailableError
from aso.tests.test_search_jobs import JobTestBase


class SearchRankedPageTest(JobTestBase):
    """The page answers 403 or 429: AppStorePageRefused; search_ranked's
    fallback still says the App Store is not answering, as before."""

    def page(self, status):
        response = mock.Mock(status_code=status, text="")
        response.raise_for_status.side_effect = requests.exceptions.HTTPError(response=response)
        return mock.patch("aso.services.requests.get", return_value=response)

    def test_403_and_429_are_a_refusal(self):
        for status in (403, 429):
            with self.subTest(status=status), self.page(status), self.assertRaises(AppStorePageRefused):
                ITunesSearchService().search_ranked_page("habit", country="us")

    def test_another_error_is_unavailability(self):
        with self.page(404), self.assertRaises(SearchAPIUnavailableError) as caught:
            ITunesSearchService().search_ranked_page("habit", country="us")
        self.assertNotIsInstance(caught.exception, AppStorePageRefused)

    def test_the_fallback_still_says_the_app_store_is_not_answering(self):
        service = ITunesSearchService()
        down = mock.patch.object(service, "_search_itunes", side_effect=RuntimeError("api down"))
        with self.page(403), down, self.assertRaises(SearchAPIUnavailableError) as caught:
            service.search_ranked("habit", country="us")
        self.assertNotIsInstance(caught.exception, AppStorePageRefused)
        self.assertEqual(str(caught.exception), "Apple's App Store is not answering right now. Try again in a few minutes.")
