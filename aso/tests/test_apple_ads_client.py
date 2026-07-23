"""Tests for the Apple Ads keyword-planner client (mocked HTTP).

Covers the full rate-limit policy: Retry-After honoring (both forms),
per-status backoff curves, batching limits, error taxonomy, and
response-contract tolerance.
"""

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from aso.apple_ads.client import (
    MAX_ATTEMPTS,
    MAX_TERMS_PER_CALL,
    AppleAdsAPIError,
    AppleAdsAppAccessError,
    AppleAdsAuthError,
    AppleAdsRateLimitedError,
    _classify_response,
    _parse_retry_after,
    _retry_delay,
    fetch_popularities,
)


def _response(status=200, payload=None, headers=None):
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload if payload is not None else {}
    resp.headers = headers or {}
    return resp


def _success(terms_values):
    return _response(200, {
        "status": "success",
        "data": [{"name": t, "popularity": v} for t, v in terms_values.items()],
    })


COOKIES = "myacinfo=abc; dqsid=def"


class RetryAfterParsingTest(SimpleTestCase):
    def test_delta_seconds(self):
        self.assertEqual(_parse_retry_after({"Retry-After": "7"}), 7.0)

    def test_http_date(self):
        import time
        from email.utils import formatdate

        future = formatdate(time.time() + 30, usegmt=True)
        parsed = _parse_retry_after({"Retry-After": future})
        self.assertIsNotNone(parsed)
        self.assertGreater(parsed, 20)
        self.assertLessEqual(parsed, 31)

    def test_missing_or_garbage(self):
        self.assertIsNone(_parse_retry_after({}))
        self.assertIsNone(_parse_retry_after({"Retry-After": "soonish"}))


class RetryDelayPolicyTest(SimpleTestCase):
    def test_rate_limit_backoff_progression(self):
        # 5s base doubling, jitter adds 0-25%
        for attempt, base in ((1, 5), (2, 10), (3, 20)):
            d = _retry_delay(429, {}, attempt)
            self.assertGreaterEqual(d, base)
            self.assertLessEqual(d, base * 1.25)

    def test_transient_backoff_progression(self):
        for attempt, base in ((1, 1), (2, 2), (3, 4)):
            d = _retry_delay(503, {}, attempt)
            self.assertGreaterEqual(d, base)
            self.assertLessEqual(d, base * 1.25)

    def test_retry_after_honored_exactly_with_jitter(self):
        d = _retry_delay(429, {"Retry-After": "12"}, 1)
        self.assertGreaterEqual(d, 12.0)
        self.assertLessEqual(d, 15.0)

    def test_delay_capped(self):
        d = _retry_delay(429, {"Retry-After": "300"}, 1)
        self.assertLessEqual(d, 60.0)


class ClassifyResponseTest(SimpleTestCase):
    def test_matrix(self):
        cases = [
            (200, {"status": "success"}, "ok"),
            (401, {}, "auth"),
            (403, {"internalErrorCode": "REFRESH"}, "auth"),
            (403, {"error": {"errors": [{"messageCode": "NO_USER_OWNED_APPS_FOUND_CODE"}]}}, "app_access"),
            (403, {"error": {"errors": [{"message": "No user owned apps found"}]}}, "app_access"),
            (403, {"error": {"errors": [{"messageCode": "KWS_NO_ORG_CONTENT_PROVIDERS"}]}}, "transient"),
            (403, {}, "auth"),
            (429, {}, "rate_limited"),
            (500, {}, "transient"),
            (503, {}, "transient"),
            (400, {}, "error"),
        ]
        for status, payload, expected in cases:
            self.assertEqual(
                _classify_response(status, payload), expected, (status, payload)
            )


class FetchPopularitiesTest(SimpleTestCase):
    def test_success_maps_terms(self):
        with patch("aso.apple_ads.client.requests.post") as mock_post:
            mock_post.return_value = _success({"fitness": 71, "tiny": None})
            values = fetch_popularities(
                ["fitness", "tiny"], "us", "123", COOKIES, sleeper=lambda s: None
            )
        self.assertEqual(values, {"fitness": 71, "tiny": None})
        # Storefront sent uppercase; adamId in query string; Origin header set.
        args, kwargs = mock_post.call_args
        self.assertIn("adamId=123", args[0])
        self.assertEqual(kwargs["json"]["storefronts"], ["US"])
        self.assertEqual(kwargs["headers"]["Origin"], "https://app-ads.apple.com")

    def test_response_name_normalization(self):
        """Apple echoing different casing still maps to requested terms."""
        with patch("aso.apple_ads.client.requests.post") as mock_post:
            mock_post.return_value = _response(200, {
                "status": "success",
                "data": [{"name": "  Fitness ", "popularity": 60}],
            })
            values = fetch_popularities(
                ["fitness"], "us", "123", COOKIES, sleeper=lambda s: None
            )
        self.assertEqual(values, {"fitness": 60})

    def test_batch_limit_enforced(self):
        with self.assertRaises(ValueError):
            fetch_popularities(["a"] * (MAX_TERMS_PER_CALL + 1), "us", "1", COOKIES)

    def test_empty_terms_no_request(self):
        with patch("aso.apple_ads.client.requests.post") as mock_post:
            self.assertEqual(fetch_popularities([], "us", "1", COOKIES), {})
        mock_post.assert_not_called()

    def test_missing_cookies_raises_auth(self):
        with self.assertRaises(AppleAdsAuthError):
            fetch_popularities(["a"], "us", "1", "   ")

    def test_401_raises_auth_error(self):
        with patch("aso.apple_ads.client.requests.post") as mock_post:
            mock_post.return_value = _response(401)
            with self.assertRaises(AppleAdsAuthError):
                fetch_popularities(["a"], "us", "1", COOKIES, sleeper=lambda s: None)

    def test_refresh_403_raises_auth_error(self):
        with patch("aso.apple_ads.client.requests.post") as mock_post:
            mock_post.return_value = _response(403, {"internalErrorCode": "REFRESH"})
            with self.assertRaises(AppleAdsAuthError):
                fetch_popularities(["a"], "us", "1", COOKIES, sleeper=lambda s: None)

    def test_app_access_error(self):
        with patch("aso.apple_ads.client.requests.post") as mock_post:
            mock_post.return_value = _response(
                403,
                {"error": {"errors": [{"messageCode": "NO_USER_OWNED_APPS_FOUND_CODE"}]}},
            )
            with self.assertRaises(AppleAdsAppAccessError):
                fetch_popularities(["a"], "us", "1", COOKIES, sleeper=lambda s: None)

    def test_rate_limited_retries_then_raises(self):
        sleeps = []
        with patch("aso.apple_ads.client.requests.post") as mock_post:
            mock_post.return_value = _response(429)
            with self.assertRaises(AppleAdsRateLimitedError):
                fetch_popularities(
                    ["a"], "us", "1", COOKIES, sleeper=sleeps.append
                )
        self.assertEqual(mock_post.call_count, MAX_ATTEMPTS)
        self.assertEqual(len(sleeps), MAX_ATTEMPTS - 1)
        # Waits grow (jittered exponential)
        self.assertGreater(sleeps[-1], sleeps[0])

    def test_transient_then_success(self):
        with patch("aso.apple_ads.client.requests.post") as mock_post:
            mock_post.side_effect = [_response(503), _success({"a": 40})]
            values = fetch_popularities(
                ["a"], "us", "1", COOKIES, sleeper=lambda s: None
            )
        self.assertEqual(values, {"a": 40})

    def test_kws_transient_retried(self):
        payload = {"error": {"errors": [{"messageCode": "KWS_NO_ORG_CONTENT_PROVIDERS"}]}}
        with patch("aso.apple_ads.client.requests.post") as mock_post:
            mock_post.side_effect = [_response(403, payload), _success({"a": 40})]
            values = fetch_popularities(
                ["a"], "us", "1", COOKIES, sleeper=lambda s: None
            )
        self.assertEqual(values, {"a": 40})

    def test_hard_error_no_retry(self):
        with patch("aso.apple_ads.client.requests.post") as mock_post:
            mock_post.return_value = _response(400, {"error": {"errors": [{"message": "bad"}]}})
            with self.assertRaises(AppleAdsAPIError):
                fetch_popularities(["a"], "us", "1", COOKIES, sleeper=lambda s: None)
        self.assertEqual(mock_post.call_count, 1)

    def test_contract_change_tolerated(self):
        """A malformed data payload logs a warning and returns {} - no crash."""
        with patch("aso.apple_ads.client.requests.post") as mock_post:
            mock_post.return_value = _response(200, {"status": "success", "data": "??"})
            values = fetch_popularities(
                ["a"], "us", "1", COOKIES, sleeper=lambda s: None
            )
        self.assertEqual(values, {})
