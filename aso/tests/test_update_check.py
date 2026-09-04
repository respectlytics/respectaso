"""Tests for the update check's 10-minute cache (aso/update_check.py).

The contract: GitHub is contacted at most once per CHECK_INTERVAL_SECONDS,
whatever the outcome, so an active session can never exhaust GitHub's
60-requests-per-hour unauthenticated quota and every page in between
gets the cached answer.
"""

import json
import urllib.error
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.urls import reverse

from aso import update_check


def _github_response(tag, dmg_url="https://example.com/RespectASO.dmg"):
    payload = {
        "tag_name": tag,
        "html_url": f"https://github.com/respectlytics/respectaso/releases/tag/{tag}",
        "body": "## What's New\n- Something",
        "assets": [
            {"name": "checksums.txt", "browser_download_url": "https://example.com/sums"},
            {"name": "RespectASO.dmg", "browser_download_url": dmg_url},
        ],
    }
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
    return response


class UpdateCheckCacheTest(TestCase):
    def setUp(self):
        # Every test starts with an empty cache, as a freshly launched app does.
        patcher_result = patch.object(update_check, "_last_result", None)
        patcher_attempt = patch.object(update_check, "_last_attempt", None)
        patcher_result.start()
        patcher_attempt.start()
        self.addCleanup(patcher_result.stop)
        self.addCleanup(patcher_attempt.stop)

    @override_settings(VERSION="2.0.0", IS_NATIVE_APP=True)
    def test_newer_release_is_reported_with_its_dmg(self):
        with patch("aso.update_check.urllib.request.urlopen", return_value=_github_response("v2.1.0")):
            result = update_check.check_for_update()
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest"], "2.1.0")
        self.assertEqual(result["current"], "2.0.0")
        self.assertEqual(result["download_url"], "https://example.com/RespectASO.dmg")
        self.assertTrue(result["is_native"])
        self.assertNotIn("error", result)

    @override_settings(VERSION="2.1.0")
    def test_same_version_means_no_update(self):
        with patch("aso.update_check.urllib.request.urlopen", return_value=_github_response("v2.1.0")):
            result = update_check.check_for_update()
        self.assertFalse(result["update_available"])
        self.assertNotIn("error", result)

    @override_settings(VERSION="2.0.0")
    def test_pages_within_the_interval_share_one_github_call(self):
        with patch("aso.update_check.urllib.request.urlopen", return_value=_github_response("v2.1.0")) as urlopen:
            first = update_check.check_for_update()
            for _ in range(20):
                self.assertEqual(update_check.check_for_update(), first)
        self.assertEqual(urlopen.call_count, 1)

    @override_settings(VERSION="2.0.0")
    def test_a_failed_attempt_is_not_retried_within_the_interval(self):
        offline = urllib.error.URLError("nodename nor servname provided, or not known")
        with patch("aso.update_check.urllib.request.urlopen", side_effect=offline) as urlopen:
            first = update_check.check_for_update()
            for _ in range(20):
                self.assertEqual(update_check.check_for_update(), first)
        self.assertEqual(urlopen.call_count, 1)
        self.assertFalse(first["update_available"])
        self.assertEqual(first["error"], "URLError")
        self.assertEqual(first["current"], "2.0.0")

    @override_settings(VERSION="2.0.0")
    def test_github_rate_limit_is_reported_as_an_error(self):
        limited = urllib.error.HTTPError(update_check.RELEASES_URL, 403, "rate limit exceeded", {}, None)
        with patch("aso.update_check.urllib.request.urlopen", side_effect=limited):
            result = update_check.check_for_update()
        self.assertEqual(result["error"], "HTTPError")
        self.assertFalse(result["update_available"])

    @override_settings(VERSION="2.0.0")
    def test_github_is_asked_again_once_the_interval_has_passed(self):
        clock = [1000.0]
        with patch("aso.update_check.time.monotonic", side_effect=lambda: clock[0]), \
             patch("aso.update_check.urllib.request.urlopen", return_value=_github_response("v2.1.0")) as urlopen:
            update_check.check_for_update()
            clock[0] += update_check.CHECK_INTERVAL_SECONDS - 1
            update_check.check_for_update()
            self.assertEqual(urlopen.call_count, 1, "still inside the interval")
            clock[0] += 1
            update_check.check_for_update()
            self.assertEqual(urlopen.call_count, 2, "interval elapsed, ask GitHub again")

    @override_settings(VERSION="2.0.0")
    def test_a_recovered_network_is_noticed_after_the_interval(self):
        clock = [1000.0]
        offline = urllib.error.URLError("nodename nor servname provided, or not known")
        with patch("aso.update_check.time.monotonic", side_effect=lambda: clock[0]), \
             patch("aso.update_check.urllib.request.urlopen",
                   side_effect=[offline, _github_response("v2.1.0")]) as urlopen:
            self.assertIn("error", update_check.check_for_update())
            clock[0] += update_check.CHECK_INTERVAL_SECONDS
            recovered = update_check.check_for_update()
        self.assertEqual(urlopen.call_count, 2)
        self.assertNotIn("error", recovered)
        self.assertTrue(recovered["update_available"])

    def test_interval_caps_github_calls_at_six_per_hour(self):
        self.assertEqual(3600 // update_check.CHECK_INTERVAL_SECONDS, 6)

    @override_settings(VERSION="2.0.0")
    def test_callers_cannot_corrupt_the_cached_payload(self):
        with patch("aso.update_check.urllib.request.urlopen", return_value=_github_response("v2.1.0")):
            first = update_check.check_for_update()
            first["update_available"] = False
            self.assertTrue(update_check.check_for_update()["update_available"])


class VersionCheckViewTest(TestCase):
    def setUp(self):
        patcher_result = patch.object(update_check, "_last_result", None)
        patcher_attempt = patch.object(update_check, "_last_attempt", None)
        patcher_result.start()
        patcher_attempt.start()
        self.addCleanup(patcher_result.stop)
        self.addCleanup(patcher_attempt.stop)

    @override_settings(VERSION="2.0.0")
    def test_every_page_load_gets_the_cached_answer(self):
        url = reverse("aso:version_check")
        with patch("aso.update_check.urllib.request.urlopen", return_value=_github_response("v2.1.0")) as urlopen:
            bodies = [self.client.get(url).json() for _ in range(5)]
        self.assertEqual(urlopen.call_count, 1)
        for body in bodies:
            self.assertTrue(body["update_available"])
            self.assertEqual(body["latest"], "2.1.0")
            self.assertEqual(body["current"], "2.0.0")
            self.assertEqual(body["download_url"], "https://example.com/RespectASO.dmg")
            self.assertIn("release_url", body)
            self.assertIn("release_notes", body)
            self.assertIn("is_native", body)

    @override_settings(VERSION="2.0.0")
    def test_failure_is_reported_to_the_page_as_an_error(self):
        offline = urllib.error.URLError("no network")
        with patch("aso.update_check.urllib.request.urlopen", side_effect=offline):
            body = self.client.get(reverse("aso:version_check")).json()
        self.assertEqual(body["error"], "URLError")
        self.assertFalse(body["update_available"])


class DownloadDmgViewTest(TestCase):
    def setUp(self):
        patcher_result = patch.object(update_check, "_last_result", None)
        patcher_attempt = patch.object(update_check, "_last_attempt", None)
        patcher_result.start()
        patcher_attempt.start()
        self.addCleanup(patcher_result.stop)
        self.addCleanup(patcher_attempt.stop)

    def test_redirects_to_the_latest_dmg(self):
        with patch("aso.update_check.urllib.request.urlopen", return_value=_github_response("v2.1.0")):
            response = self.client.get(reverse("aso:download_dmg"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://example.com/RespectASO.dmg")

    def test_falls_back_to_the_releases_page_when_github_is_unreachable(self):
        with patch("aso.update_check.urllib.request.urlopen", side_effect=urllib.error.URLError("no network")):
            response = self.client.get(reverse("aso:download_dmg"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://github.com/respectlytics/respectaso/releases/latest")

    def test_shares_the_update_banner_cache(self):
        with patch("aso.update_check.urllib.request.urlopen", return_value=_github_response("v2.1.0")) as urlopen:
            self.client.get(reverse("aso:version_check"))
            response = self.client.get(reverse("aso:download_dmg"))
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(response["Location"], "https://example.com/RespectASO.dmg")
