"""Tests for the adaptive iTunes rate limiter and the iTunes service's
429/503 handling that feeds it.
"""

from unittest.mock import MagicMock, patch

import requests
from django.test import TestCase

from aso.services import (
    ITunesRateLimited,
    ITunesSearchService,
)
from aso.throttle import (
    ABORT_AFTER_FAILURES,
    AdaptiveITunesRateLimiter,
    BASE_DELAY,
    MAX_DELAY,
    PAUSED_AFTER_FAILURES,
    classify_throttle_state,
)


class AdaptiveITunesRateLimiterTest(TestCase):
    def test_starts_at_base_delay(self):
        lim = AdaptiveITunesRateLimiter()
        self.assertEqual(lim.current_delay, BASE_DELAY)
        self.assertFalse(lim.is_slowed_down)

    def test_failure_grows_delay(self):
        lim = AdaptiveITunesRateLimiter()
        lim.record_failure()
        self.assertGreater(lim.current_delay, BASE_DELAY)
        self.assertEqual(lim.consecutive_failures, 1)

    def test_failure_caps_at_max(self):
        lim = AdaptiveITunesRateLimiter()
        for _ in range(50):
            lim.record_failure()
        self.assertLessEqual(lim.current_delay, MAX_DELAY)

    def test_retry_after_honoured(self):
        lim = AdaptiveITunesRateLimiter()
        lim.record_failure(retry_after=8.0)
        self.assertGreaterEqual(lim.current_delay, 8.0)
        self.assertLessEqual(lim.current_delay, MAX_DELAY)

    def test_retry_after_capped_at_max(self):
        lim = AdaptiveITunesRateLimiter()
        lim.record_failure(retry_after=999.0)
        self.assertEqual(lim.current_delay, MAX_DELAY)

    def test_decay_after_consecutive_successes(self):
        lim = AdaptiveITunesRateLimiter()
        lim.record_failure()
        elevated = lim.current_delay
        for _ in range(3):
            lim.record_success()
        self.assertLess(lim.current_delay, elevated)

    def test_success_resets_consecutive_failures(self):
        lim = AdaptiveITunesRateLimiter()
        lim.record_failure()
        lim.record_failure()
        self.assertEqual(lim.consecutive_failures, 2)
        lim.record_success()
        self.assertEqual(lim.consecutive_failures, 0)

    def test_is_slowed_down_threshold(self):
        lim = AdaptiveITunesRateLimiter()
        # Several failures should push us above the slowed-down threshold.
        for _ in range(2):
            lim.record_failure()
        self.assertTrue(lim.is_slowed_down)


class ClassifyThrottleStateTest(TestCase):
    def test_normal_state_when_healthy(self):
        lim = AdaptiveITunesRateLimiter()
        self.assertEqual(classify_throttle_state(lim, attempted=10, failed=0), "normal")

    def test_slowed_down_after_growth(self):
        lim = AdaptiveITunesRateLimiter()
        for _ in range(2):
            lim.record_failure()
        # 2 failures < PAUSED_AFTER_FAILURES, so we should still be in
        # slowed_down rather than paused.
        self.assertLess(lim.consecutive_failures, PAUSED_AFTER_FAILURES)
        self.assertEqual(
            classify_throttle_state(lim, attempted=2, failed=2), "slowed_down"
        )

    def test_paused_after_consecutive_failures(self):
        lim = AdaptiveITunesRateLimiter()
        for _ in range(PAUSED_AFTER_FAILURES):
            lim.record_failure()
        # Mix in many prior successes so the failure-rate abort path doesn't
        # fire — we want to verify the consecutive-failures "paused" state.
        self.assertEqual(
            classify_throttle_state(
                lim,
                attempted=PAUSED_AFTER_FAILURES + 50,
                failed=PAUSED_AFTER_FAILURES,
            ),
            "paused",
        )

    def test_aborted_after_too_many_failures(self):
        lim = AdaptiveITunesRateLimiter()
        for _ in range(ABORT_AFTER_FAILURES):
            lim.record_failure()
        self.assertEqual(
            classify_throttle_state(
                lim,
                attempted=ABORT_AFTER_FAILURES,
                failed=ABORT_AFTER_FAILURES,
            ),
            "aborted",
        )


class ITunesRateLimitedSignalTest(TestCase):
    """Verify _search_itunes raises ITunesRateLimited on 429 with Retry-After,
    and on persistent 503."""

    def _resp(self, status, retry_after=None, body=None):
        r = MagicMock()
        r.status_code = status
        r.headers = {"Retry-After": retry_after} if retry_after is not None else {}
        r.json.return_value = body or {"results": []}
        # raise_for_status mimics requests behaviour
        if status >= 400:
            err = requests.HTTPError(f"{status}")
            err.response = r
            r.raise_for_status.side_effect = err
        else:
            r.raise_for_status.return_value = None
        return r

    @patch("aso.services.time.sleep", lambda *_a, **_kw: None)  # speed up retry wait
    @patch("aso.services.requests.get")
    def test_persistent_429_raises_rate_limited(self, mock_get):
        mock_get.return_value = self._resp(429, retry_after="3")
        svc = ITunesSearchService()
        with self.assertRaises(ITunesRateLimited) as ctx:
            svc._search_itunes("yoga", country="us", limit=10)
        self.assertEqual(ctx.exception.retry_after, 3.0)
        # Two attempts were made before raising.
        self.assertEqual(mock_get.call_count, 2)

    @patch("aso.services.time.sleep", lambda *_a, **_kw: None)
    @patch("aso.services.requests.get")
    def test_first_503_then_recovers(self, mock_get):
        # First call 503, second call 200 with results.
        ok_body = {"results": [{"trackId": 1, "trackName": "Test", "bundleId": "x", "primaryGenreName": "Health"}]}
        mock_get.side_effect = [self._resp(503), self._resp(200, body=ok_body)]
        svc = ITunesSearchService()
        out = svc._search_itunes("yoga", country="us", limit=10)
        self.assertEqual(len(out), 1)
        self.assertEqual(mock_get.call_count, 2)

    @patch("aso.services.time.sleep", lambda *_a, **_kw: None)
    @patch("aso.services.requests.get")
    def test_persistent_503_raises_rate_limited(self, mock_get):
        # Both attempts 503 — raises ITunesRateLimited (treats as throttle).
        mock_get.return_value = self._resp(503)
        svc = ITunesSearchService()
        with self.assertRaises(ITunesRateLimited):
            svc._search_itunes("yoga", country="us", limit=10)
        self.assertEqual(mock_get.call_count, 2)
