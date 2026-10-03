"""The daily keyword refresh backs off while Apple's App Store says it is busy.

Until 2.28.1 a busy answer (ITunesRateLimited) to a refresh's keyword search
was taken as one more failed pair: the refresh went on to the next keyword
at the same two second pace and asked Apple for every pair left, failing
each in turn. It now paces itself through the adaptive limiter every App
Store loop uses (aso/throttle.py, which honours Retry-After), stops when
Apple keeps turning it away, and the next hourly check carries on with the
pairs left. Apple's Search is faked at the HTTP layer (AppleSearch).
"""

from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from aso import scheduler
from aso.models import Keyword, SearchResult
from aso.tests.helpers import AppleSearch

PAIRS = 8
RETRY_AFTER = 5


def _apps():
    return [{"trackId": 1000 + n, "trackName": f"Sleep Sounds {n}", "primaryGenreName": "Health & Fitness",
             "averageUserRating": 4.5, "userRatingCount": 100 * (n + 1),
             "releaseDate": "2020-01-01T00:00:00Z"} for n in range(5)]


class TheRefreshBacksOffWhileAppleIsBusyTest(TestCase):
    def setUp(self):
        self.addCleanup(lambda: scheduler._update_status(running=False, error=None))
        scheduler._update_status(last_completed_at=None)
        yesterday = timezone.now() - timedelta(days=1, hours=1)
        for n in range(PAIRS):
            keyword = Keyword.objects.create(keyword=f"sleep sounds {n}")
            row = SearchResult.objects.create(keyword=keyword, country="us", popularity_score=30,
                                              difficulty_score=50, difficulty_breakdown={},
                                              competitors_data=[], app_rank=None)
            SearchResult.objects.filter(pk=row.pk).update(searched_at=yesterday)
        self.pairs = scheduler._get_pairs_to_refresh()
        self.assertEqual(len(self.pairs), PAIRS)

    def _refresh(self, apple):
        with mock.patch("aso.services.requests.get", side_effect=apple), \
                mock.patch("aso.scheduler.time.sleep") as sleep, \
                mock.patch("aso.scheduler.run_queue.kick"), \
                self.assertLogs("aso.scheduler", level="INFO") as logs:
            refreshed = scheduler._refresh_pairs(self.pairs, "Auto-refresh")
        waits = [call.args[0] for call in sleep.call_args_list]
        return refreshed, waits, "\n".join(logs.output)

    def test_apple_busy_throughout_stops_the_refresh_and_leaves_the_rest_for_the_next_check(self):
        apple = AppleSearch(429, retry_after=str(RETRY_AFTER))
        refreshed, waits, log = self._refresh(apple)
        self.assertEqual(refreshed, 0)
        # Five keywords turned away, then the refresh stops: Apple is not
        # asked for the three left (each keyword is one search asked twice).
        self.assertEqual(len(apple.calls), 2 * 5)
        self.assertIn("stopped: Apple's App Store keeps turning reads away", log)
        self.assertIn(f"0 of {PAIRS} pairs refreshed; {PAIRS} wait for the next check", log)
        # Every wait honours Apple's Retry-After, above the normal pace.
        self.assertTrue(waits)
        self.assertGreaterEqual(min(waits), RETRY_AFTER)
        status = scheduler.get_status()
        self.assertFalse(status["running"])
        self.assertIsNone(status["last_completed_at"])
        # Nothing was written: every pair is still due.
        self.assertEqual(len(scheduler._get_pairs_to_refresh()), PAIRS)

    def test_a_moment_of_busy_slows_the_refresh_and_it_carries_on(self):
        apple = AppleSearch(429, 429, _apps(), retry_after=str(RETRY_AFTER))
        refreshed, waits, log = self._refresh(apple)
        self.assertEqual(refreshed, PAIRS - 1)
        self.assertIn(f"{PAIRS - 1} of {PAIRS} pairs refreshed, 0 skipped (App Store unavailable), "
                      "1 busy, 0 failed", log)
        # The pause before the next keyword is Apple's Retry-After, not two seconds.
        self.assertEqual(waits[:2], [RETRY_AFTER, RETRY_AFTER])
        # The keyword Apple turned away is the one left for the next check.
        self.assertEqual(scheduler._get_pairs_to_refresh(), [self.pairs[0]])

    def test_a_healthy_refresh_keeps_its_pace(self):
        apple = AppleSearch(_apps())
        refreshed, waits, _log = self._refresh(apple)
        self.assertEqual(refreshed, PAIRS)
        self.assertEqual(waits, [scheduler.REFRESH_PACE_SECONDS] * (PAIRS - 1))

    def test_the_next_check_carries_on_with_the_pairs_left(self):
        self._refresh(AppleSearch(429, retry_after=str(RETRY_AFTER)))
        self.assertTrue(scheduler._needs_refresh_today())
        apple = AppleSearch(_apps())
        with mock.patch("aso.services.requests.get", side_effect=apple), \
                mock.patch("aso.scheduler.time.sleep"), \
                mock.patch("aso.scheduler.run_queue.kick"), \
                mock.patch("aso.scheduler.run_queue.lane_state", return_value="idle"), \
                mock.patch("aso.apple_ads.sync.maybe_run_sync"), \
                mock.patch.object(scheduler, "daily_hooks", []), \
                self.assertLogs("aso.scheduler", level="INFO"):
            scheduler._tick()
        self.assertEqual(len(apple.calls), PAIRS)
        self.assertEqual(scheduler._get_pairs_to_refresh(), [])
        self.assertFalse(scheduler._needs_refresh_today())
