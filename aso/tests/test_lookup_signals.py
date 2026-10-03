"""Apple's Lookup tells "no such app" apart from "Apple could not be reached
or is busy", and asks again before giving up.

A user's AI Competitor run for Forest (ID 866450515, listed in the United
States) failed with "not found": until 2.28.1 ``lookup_by_id`` turned a
timeout, a connection error, a busy answer or unreadable JSON into None, the
same None as an app Apple does not list, and asked once. Now a failure raises
ITunesRateLimited or SearchAPIUnavailableError, a background run asks again
with the adaptive pacing, an empty answer is asked once more after a short
pause, and a request a person waits on still gives up at once.

Free-tier test: no aso_pro import.
"""

import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from aso.models import App, Keyword
from aso.services import (
    APP_STORE_UNAVAILABLE,
    LOOKUP_ATTEMPTS,
    ITunesAPIError,
    ITunesRateLimited,
    ITunesSearchService,
    SearchAPIUnavailableError,
)
from aso.throttle import BASE_DELAY

from .helpers import AppleLookup

FOREST = {"trackId": 866450515, "trackName": "Forest: Focus for Productivity",
          "primaryGenreName": "Productivity", "averageUserRating": 4.8, "userRatingCount": 1_000_000,
          "description": "Stay focused, be present.", "sellerName": "Seekrtech", "version": "4.80"}


class _Lookup:
    """Runs a lookup against a scripted Apple, with every pause recorded
    instead of slept."""

    def lookup(self, *outcomes, **kwargs):
        apple = AppleLookup(*outcomes)
        sleeps = []
        with mock.patch("aso.services.requests.get", side_effect=apple), \
                mock.patch("aso.services.time.sleep", side_effect=sleeps.append):
            try:
                result = ITunesSearchService().lookup_by_id(866450515, country="us", **kwargs)
            finally:
                self.apple, self.sleeps = apple, sleeps
        return result


class EachFailureRaisesItsSignalTest(_Lookup, SimpleTestCase):
    """One call (retry=False): what Apple did comes back as what it is."""

    def test_an_app_apple_lists_comes_back(self):
        self.assertEqual(self.lookup(FOREST, retry=False)["trackName"], "Forest: Focus for Productivity")

    def test_an_empty_answer_is_none(self):
        self.assertIsNone(self.lookup("empty", retry=False))
        self.assertEqual(len(self.apple.calls), 1)

    def test_a_timeout_or_a_lost_connection_is_apple_unreachable(self):
        for outcome, words in (("timeout", "did not answer in time"), ("connection", "could not be reached"),
                               (500, "answered with an error"), ("garbage", "could not be read")):
            with self.subTest(outcome=outcome), self.assertRaises(SearchAPIUnavailableError) as ctx:
                self.lookup(outcome, retry=False)
            self.assertIn(words, str(ctx.exception))
            self.assertEqual(len(self.apple.calls), 1)
            self.assertEqual(self.sleeps, [])

    def test_a_busy_answer_is_rate_limited_with_its_retry_after(self):
        for status in (403, 429, 503):
            with self.subTest(status=status), self.assertRaises(ITunesRateLimited) as ctx:
                self.lookup(status, retry=False)
            self.assertEqual(ctx.exception.retry_after, 2.0)
            self.assertNotIn(str(status), str(ctx.exception))


class ABackgroundLookupAsksAgainTest(_Lookup, SimpleTestCase):
    """retry=True, the default: what a background run uses."""

    def test_one_failure_then_the_app(self):
        for outcome in ("timeout", "connection", 429, 503, 403, 500, "garbage"):
            with self.subTest(outcome=outcome):
                self.assertEqual(self.lookup(outcome, FOREST)["trackId"], 866450515)
                self.assertEqual(len(self.apple.calls), 2)
                self.assertEqual(len(self.sleeps), 1)

    def test_the_pause_honours_apples_retry_after(self):
        apple = AppleLookup(429, FOREST, retry_after="7")
        sleeps = []
        with mock.patch("aso.services.requests.get", side_effect=apple), \
                mock.patch("aso.services.time.sleep", side_effect=sleeps.append):
            ITunesSearchService().lookup_by_id(866450515)
        self.assertEqual(sleeps, [7.0])

    def test_the_pauses_grow_with_each_failure(self):
        self.lookup("timeout", "timeout", FOREST)
        self.assertEqual(len(self.sleeps), 2)
        self.assertGreater(self.sleeps[1], self.sleeps[0])
        self.assertGreaterEqual(self.sleeps[0], BASE_DELAY)

    def test_it_gives_up_after_its_attempts_with_apples_signal(self):
        with self.assertRaises(SearchAPIUnavailableError):
            self.lookup("timeout")
        self.assertEqual(len(self.apple.calls), LOOKUP_ATTEMPTS)
        with self.assertRaises(ITunesRateLimited):
            self.lookup(503)
        self.assertEqual(len(self.apple.calls), LOOKUP_ATTEMPTS)

    def test_an_empty_answer_is_asked_once_more_after_a_short_pause(self):
        self.assertEqual(self.lookup("empty", FOREST)["trackId"], 866450515)
        self.assertEqual(self.sleeps, [BASE_DELAY])
        self.assertIsNone(self.lookup("empty"))
        self.assertEqual(len(self.apple.calls), 2)

    def test_a_failure_after_an_empty_answer_is_still_retried(self):
        self.assertEqual(self.lookup("empty", "timeout", FOREST)["trackId"], 866450515)
        self.assertEqual(len(self.apple.calls), 3)

    def test_the_storefront_and_the_id_are_asked_for(self):
        self.lookup(FOREST)
        self.assertEqual(self.apple.calls, [{"id": "866450515", "country": "us"}])


class TheDescriptionReadsTheSameLookupTest(_Lookup, SimpleTestCase):
    def describe(self, *outcomes):
        apple = AppleLookup(*outcomes)
        with mock.patch("aso.services.requests.get", side_effect=apple), \
                mock.patch("aso.services.time.sleep"):
            return ITunesSearchService().lookup_full_description(866450515, country="us"), apple

    def test_the_fields_come_from_the_app(self):
        info, _apple = self.describe(FOREST)
        self.assertEqual((info["description"], info["genre"], info["rating_count"], info["version"],
                          info["seller"]),
                         ("Stay focused, be present.", "Productivity", 1_000_000, "4.80", "Seekrtech"))

    def test_a_moment_of_trouble_is_asked_again(self):
        info, apple = self.describe("timeout", FOREST)
        self.assertEqual(info["genre"], "Productivity")
        self.assertEqual(len(apple.calls), 2)

    def test_the_defaults_stand_in_when_apple_never_answers_or_has_no_app(self):
        for outcome in ("timeout", "empty"):
            with self.subTest(outcome=outcome):
                info, _apple = self.describe(outcome)
                self.assertEqual((info["description"], info["genre"], info["price"]), ("", "", "Free"))


class FindAppsSaysWhenAppleIsDownTest(SimpleTestCase):
    """The app search box (a person waits): one call, and Apple's trouble
    is raised, never shown as "no apps"."""

    def test_an_id_is_one_call_and_a_failure_raises(self):
        for outcome, error in (("timeout", SearchAPIUnavailableError), (429, ITunesRateLimited)):
            apple = AppleLookup(outcome, FOREST)
            with self.subTest(outcome=outcome), mock.patch("aso.services.requests.get", side_effect=apple), \
                    mock.patch("aso.services.time.sleep") as sleep, self.assertRaises(error):
                ITunesSearchService().find_apps("866450515")
            self.assertEqual(len(apple.calls), 1)
            sleep.assert_not_called()

    def test_an_id_apple_does_not_list_finds_nothing_at_once(self):
        apple = AppleLookup("empty", FOREST)
        with mock.patch("aso.services.requests.get", side_effect=apple):
            self.assertEqual(ITunesSearchService().find_apps("866450515"), [])
        self.assertEqual(len(apple.calls), 1)


class TheScreensSayAppleIsDownTest(TestCase):
    def test_the_app_search_says_apple_is_not_answering(self):
        for outcome in ("timeout", "connection", 429, 503):
            with self.subTest(outcome=outcome), \
                    mock.patch("aso.services.requests.get", side_effect=AppleLookup(outcome)):
                body = self.client.get(reverse("aso:app_lookup"), {"q": "866450515"}).json()
            self.assertEqual(body, {"apps": [], "error": APP_STORE_UNAVAILABLE})

    def test_the_app_search_finds_the_app_by_id(self):
        with mock.patch("aso.services.requests.get", side_effect=AppleLookup(FOREST)):
            body = self.client.get(reverse("aso:app_lookup"), {"q": "866450515"}).json()
        self.assertEqual(body["apps"][0]["trackName"], "Forest: Focus for Productivity")

    def test_the_app_refresh_says_it_could_not_reach_apple(self):
        app = App.objects.create(name="Forest", track_id=866450515)
        for outcome in ("timeout", 429):
            apple = AppleLookup(outcome, FOREST)
            with self.subTest(outcome=outcome), mock.patch("aso.services.requests.get", side_effect=apple):
                response = self.client.post(reverse("aso:app_refresh", args=[app.pk]))
            self.assertRedirects(response, reverse("aso:apps") + f"?refresh=unanswered&app={app.pk}")
            self.assertEqual(len(apple.calls), 1)

    def test_a_keyword_refresh_while_apple_is_busy_says_so(self):
        keyword = Keyword.objects.create(keyword="focus timer")
        with mock.patch("aso.views.score_keyword_pair", side_effect=ITunesRateLimited(retry_after=5)):
            response = self.client.post(reverse("aso:keyword_refresh", args=[keyword.pk]), {"country": "us"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["error"], APP_STORE_UNAVAILABLE)


class EveryAppStoreErrorIsOneFamilyTest(SimpleTestCase):
    def test_busy_and_unreachable_share_the_base_class(self):
        self.assertTrue(issubclass(ITunesRateLimited, ITunesAPIError))
        self.assertTrue(issubclass(SearchAPIUnavailableError, ITunesAPIError))


class EverySearchBoxSaysAppleIsDownTest(SimpleTestCase):
    """Static guard: each screen that calls the app search shows the error
    it sends when Apple is down, instead of "No apps found" (the Apps page
    and the AI Competitor box did not until this fix)."""

    CALLS = re.compile(r"\{% url 'aso:app_lookup' %\}\?q=|lookupUrl \+ '\?q='")

    def test_each_caller_reads_the_error(self):
        root = Path(settings.BASE_DIR)
        calls = [
            (path, match.end())
            for pattern in ("aso/templates/**/*.html", "aso_pro/templates/**/*.html", "static/js/*.js")
            for path in sorted(root.glob(pattern))
            for match in self.CALLS.finditer(path.read_text(encoding="utf-8"))
        ]
        self.assertTrue(calls)
        for path, end in calls:
            text = path.read_text(encoding="utf-8")
            # The code that handles this call's answer: up to the next fetch.
            following = text.find("fetch(", end)
            with self.subTest(path=str(path.relative_to(root))):
                self.assertIn("data.error", text[end:following if following != -1 else len(text)])
