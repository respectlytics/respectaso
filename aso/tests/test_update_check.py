"""The daily update check through respectaso.com (aso/update_check.py).

The contract (docs/development/STORE_AUTOPILOT_PLAN.md step 14): at most one
call per UTC day, kept in DATA_DIR/update_check.json; the query carries
exactly v, e and os, plus manual=1 for Check now; nothing is called when
the setting is off; offline, the stored answer stands; the banner and the
.dmg download keep working from the server's answer
{version, release_url, download_url, notes}.
"""

import json
import tempfile
import urllib.error
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.urls import reverse

from aso import desktop_bridge, update_check
from aso.apple_ads import storage
from aso.tests.test_desktop_bridge import FakeBackend

DMG = "https://github.com/respectlytics/respectaso/releases/download/v2.1.0/RespectASO.dmg"
RELEASE = "https://github.com/respectlytics/respectaso/releases/tag/v2.1.0"


def _answer(version="2.1.0", **extra):
    payload = {"version": version, "release_url": RELEASE, "download_url": DMG,
               "notes": "## What's New\n- Something"}
    payload.update(extra)
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
    return response


def _query(urlopen_mock, call=0) -> dict:
    request = urlopen_mock.call_args_list[call].args[0]
    parts = urllib.parse.urlsplit(request.full_url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == update_check.UPDATE_URL
    return dict(urllib.parse.parse_qsl(parts.query, keep_blank_values=True))


class _Fresh(TestCase):
    """Every test starts as a fresh install: its own DATA_DIR, no stored
    answer, no failed call to wait out."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data_dir = Path(tmp.name)
        override = override_settings(DATA_DIR=self.data_dir)
        override.enable()
        self.addCleanup(override.disable)
        retry = patch.object(update_check, "_retry_after", None)
        retry.start()
        self.addCleanup(retry.stop)
        storage.reset_cache()
        self.addCleanup(storage.reset_cache)

    def _set_day(self, day: str):
        clock = patch("aso.update_check._today", return_value=day)
        clock.start()
        self.addCleanup(clock.stop)


@override_settings(VERSION="2.0.0", IS_NATIVE_APP=True)
class DailyCallTest(_Fresh):
    def test_newer_version_is_reported_with_its_dmg(self):
        with patch("aso.update_check.urllib.request.urlopen", return_value=_answer()):
            result = update_check.check_for_update()
        self.assertTrue(result["update_available"])
        self.assertEqual(result["latest"], "2.1.0")
        self.assertEqual(result["current"], "2.0.0")
        self.assertEqual(result["download_url"], DMG)
        self.assertEqual(result["release_url"], RELEASE)
        self.assertEqual(result["release_notes"], "## What's New\n- Something")
        self.assertTrue(result["is_native"])
        self.assertNotIn("error", result)

    @override_settings(VERSION="2.1.0")
    def test_same_version_means_no_update(self):
        with patch("aso.update_check.urllib.request.urlopen", return_value=_answer()):
            result = update_check.check_for_update()
        self.assertFalse(result["update_available"])
        self.assertNotIn("error", result)

    def test_one_call_per_day_across_twenty_page_loads(self):
        self._set_day("2026-11-13")
        with patch("aso.update_check.urllib.request.urlopen", return_value=_answer()) as urlopen:
            first = update_check.check_for_update()
            for _ in range(20):
                self.assertEqual(update_check.check_for_update(), first)
        self.assertEqual(urlopen.call_count, 1)

    def test_the_next_utc_day_calls_again(self):
        day = ["2026-11-13"]
        with patch("aso.update_check._today", side_effect=lambda: day[0]), \
                patch("aso.update_check.urllib.request.urlopen", return_value=_answer()) as urlopen:
            for _ in range(5):
                update_check.check_for_update()
            day[0] = "2026-11-14"
            for _ in range(5):
                update_check.check_for_update()
        self.assertEqual(urlopen.call_count, 2)

    def test_the_day_is_the_utc_day(self):
        self.assertEqual(update_check._today(), datetime.now(UTC).date().isoformat())

    def test_a_restart_the_same_day_does_not_call_again(self):
        self._set_day("2026-11-13")
        with patch("aso.update_check.urllib.request.urlopen", return_value=_answer()):
            update_check.check_for_update()
        stored = json.loads((self.data_dir / "update_check.json").read_text())
        self.assertEqual(stored["date"], "2026-11-13")
        self.assertEqual(stored["answer"]["version"], "2.1.0")
        # A new process has no memory; only the file.
        with patch("aso.update_check.urllib.request.urlopen") as urlopen:
            result = update_check.check_for_update()
        urlopen.assert_not_called()
        self.assertTrue(result["update_available"])

    def test_query_contains_exactly_the_three_keys(self):
        with patch("aso.update_check.urllib.request.urlopen", return_value=_answer()) as urlopen, \
                patch("aso.update_check.platform.mac_ver", return_value=("15.3.1", ("", "", ""), "arm64")):
            update_check.check_for_update()
        query = _query(urlopen)
        self.assertEqual(set(query), {"v", "e", "os"})
        self.assertEqual(query, {"v": "2.0.0", "e": update_check.edition(), "os": "15"})

    def test_the_call_names_the_app(self):
        with patch("aso.update_check.urllib.request.urlopen", return_value=_answer()) as urlopen:
            update_check.check_for_update()
        request = urlopen.call_args.args[0]
        self.assertEqual(request.get_header("User-agent"), "RespectASO/2.0.0 (+https://respectaso.com)")

    def test_os_is_empty_off_macos(self):
        with patch("aso.update_check.platform.mac_ver", return_value=("", ("", "", ""), "")):
            self.assertEqual(update_check.query()["os"], "")

    def test_check_now_calls_again_with_the_manual_flag(self):
        self._set_day("2026-11-13")
        with patch("aso.update_check.urllib.request.urlopen", return_value=_answer()) as urlopen:
            update_check.check_for_update()
            update_check.check_for_update(force=True)
        self.assertEqual(urlopen.call_count, 2)
        self.assertNotIn("manual", _query(urlopen, 0))
        manual = _query(urlopen, 1)
        self.assertEqual(manual.pop("manual"), "1")
        self.assertEqual(set(manual), {"v", "e", "os"})

    def test_offline_answer_is_the_stored_one(self):
        day = ["2026-11-13"]
        offline = urllib.error.URLError("nodename nor servname provided, or not known")
        with patch("aso.update_check._today", side_effect=lambda: day[0]):
            with patch("aso.update_check.urllib.request.urlopen", return_value=_answer()):
                online = update_check.check_for_update()
            day[0] = "2026-11-14"
            with patch("aso.update_check.urllib.request.urlopen", side_effect=offline) as urlopen:
                for _ in range(10):
                    self.assertEqual(update_check.check_for_update(), online)
        self.assertEqual(urlopen.call_count, 1, "a failed call waits before it is tried again")

    def test_offline_with_nothing_stored_stays_quiet(self):
        offline = urllib.error.URLError("no network")
        with patch("aso.update_check.urllib.request.urlopen", side_effect=offline):
            result = update_check.check_for_update()
            self.assertNotIn("error", result)
            self.assertFalse(result["update_available"])
            self.assertEqual(result["current"], "2.0.0")
            self.assertEqual(update_check.check_for_update(force=True)["error"], "unavailable",
                             "Check now says it could not reach respectaso.com")

    def test_server_503_is_like_offline(self):
        day = ["2026-11-13"]
        unavailable = urllib.error.HTTPError(update_check.UPDATE_URL, 503, "unavailable", {}, None)
        with patch("aso.update_check._today", side_effect=lambda: day[0]):
            with patch("aso.update_check.urllib.request.urlopen", return_value=_answer()):
                online = update_check.check_for_update()
            day[0] = "2026-11-14"
            with patch("aso.update_check.urllib.request.urlopen", side_effect=unavailable) as urlopen:
                for _ in range(5):
                    self.assertEqual(update_check.check_for_update(), online)
        self.assertEqual(urlopen.call_count, 1)
        stored = json.loads((self.data_dir / "update_check.json").read_text())
        self.assertEqual(stored["date"], "2026-11-13", "a 503 is not the day's call; it is tried again later")

    def test_server_503_with_nothing_stored_shows_no_error(self):
        unavailable = urllib.error.HTTPError(update_check.UPDATE_URL, 503, "unavailable", {}, None)
        with patch("aso.update_check.urllib.request.urlopen", side_effect=unavailable):
            result = update_check.check_for_update()
        self.assertNotIn("error", result)
        self.assertFalse(result["update_available"])

    def test_version_compared_with_or_without_a_v(self):
        for version in ("3.2.0", "v3.2.0"):
            with self.subTest(version=version), override_settings(VERSION="3.1.2"), \
                    patch.object(update_check, "_load_state", return_value={}), \
                    patch("aso.update_check.urllib.request.urlopen", return_value=_answer(version=version)):
                result = update_check.check_for_update(force=True)
            self.assertTrue(result["update_available"])
            self.assertEqual(result["latest"], "3.2.0")

    def test_an_unreached_call_is_tried_again_after_an_hour(self):
        self._set_day("2026-11-13")
        clock = [1000.0]
        offline = urllib.error.URLError("no network")
        with patch("aso.update_check.time.monotonic", side_effect=lambda: clock[0]), \
                patch("aso.update_check.urllib.request.urlopen", side_effect=[offline, _answer()]) as urlopen:
            self.assertFalse(update_check.check_for_update()["update_available"])
            clock[0] += update_check.RETRY_AFTER_FAILURE_SECONDS - 1
            update_check.check_for_update()
            self.assertEqual(urlopen.call_count, 1)
            clock[0] += 1
            recovered = update_check.check_for_update()
        self.assertEqual(urlopen.call_count, 2)
        self.assertTrue(recovered["update_available"])

    def test_any_server_error_status_is_quiet_and_tried_again(self):
        for code in (404, 500, 502):
            with self.subTest(code=code):
                (self.data_dir / "update_check.json").unlink(missing_ok=True)
                update_check._retry_after = None
                failing = urllib.error.HTTPError(update_check.UPDATE_URL, code, "error", {}, None)
                with patch("aso.update_check.urllib.request.urlopen", side_effect=failing) as urlopen:
                    for _ in range(5):
                        result = update_check.check_for_update()
                self.assertEqual(urlopen.call_count, 1, "tried again only after an hour")
                self.assertNotIn("error", result, "a server problem is never the person's to fix")
                self.assertFalse((self.data_dir / "update_check.json").exists(),
                                 "an error status is not the day's call")

    def test_links_to_other_hosts_are_dropped(self):
        bad = _answer(download_url="https://evil.example/RespectASO.dmg", release_url="http://github.com/x")
        with patch("aso.update_check.urllib.request.urlopen", return_value=bad):
            result = update_check.check_for_update()
        self.assertEqual(result["download_url"], "")
        self.assertEqual(result["release_url"], "")
        self.assertTrue(result["update_available"])

    def test_an_answer_without_a_version_is_not_an_update(self):
        with patch("aso.update_check.urllib.request.urlopen", return_value=_answer(version="latest")):
            result = update_check.check_for_update()
        self.assertFalse(result["update_available"])
        self.assertEqual(result["error"], "unavailable")

    def test_callers_cannot_corrupt_the_stored_answer(self):
        with patch("aso.update_check.urllib.request.urlopen", return_value=_answer()):
            first = update_check.check_for_update()
            first["update_available"] = False
            self.assertTrue(update_check.check_for_update()["update_available"])

    def test_no_github_call_is_left(self):
        source = Path(update_check.__file__).read_text(encoding="utf-8")
        self.assertNotIn("api.github.com", source)


class EditionTest(TestCase):
    def test_pro_when_the_pro_app_is_installed(self):
        with patch("django.apps.apps.is_installed", return_value=True):
            self.assertEqual(update_check.edition(), "pro")

    def test_docker_in_the_docker_image(self):
        with patch("django.apps.apps.is_installed", return_value=False), \
                patch.dict("os.environ", {"RESPECTASO_DOCKER": "1"}):
            self.assertEqual(update_check.edition(), "docker")

    def test_free_otherwise(self):
        with patch("django.apps.apps.is_installed", return_value=False), \
                patch.dict("os.environ", {"RESPECTASO_DOCKER": ""}):
            self.assertEqual(update_check.edition(), "free")

    def test_the_docker_image_sets_the_flag(self):
        dockerfile = (Path(__file__).resolve().parents[2] / "Dockerfile").read_text(encoding="utf-8")
        self.assertIn("ENV RESPECTASO_DOCKER=1", dockerfile)


@override_settings(VERSION="2.0.0")
class VersionCheckViewTest(_Fresh):
    def test_every_page_load_gets_the_stored_answer(self):
        url = reverse("aso:version_check")
        with patch("aso.update_check.urllib.request.urlopen", return_value=_answer()) as urlopen:
            bodies = [self.client.get(url).json() for _ in range(5)]
        self.assertEqual(urlopen.call_count, 1)
        for body in bodies:
            self.assertTrue(body["update_available"])
            self.assertEqual(body["latest"], "2.1.0")
            self.assertEqual(body["download_url"], DMG)
            self.assertIn("release_url", body)
            self.assertIn("release_notes", body)
            self.assertIn("is_native", body)

    def test_post_is_check_now(self):
        url = reverse("aso:version_check")
        with patch("aso.update_check.urllib.request.urlopen", return_value=_answer()) as urlopen:
            self.client.get(url)
            self.client.post(url)
        self.assertEqual(urlopen.call_count, 2)
        self.assertEqual(_query(urlopen, 1).get("manual"), "1")

    def test_an_unusable_answer_is_reported_to_the_page_as_an_error(self):
        with patch("aso.update_check.urllib.request.urlopen", return_value=_answer(version="latest")):
            body = self.client.get(reverse("aso:version_check")).json()
        self.assertEqual(body["error"], "unavailable")
        self.assertFalse(body["update_available"])

    def test_offline_the_page_shows_no_error(self):
        with patch("aso.update_check.urllib.request.urlopen", side_effect=urllib.error.URLError("no network")):
            body = self.client.get(reverse("aso:version_check")).json()
        self.assertNotIn("error", body)
        self.assertFalse(body["update_available"])

    def test_the_banner_has_check_now_on_its_error_line(self):
        markup = self.client.get(reverse("aso:settings_popularity")).content.decode()
        self.assertIn('id="update-check-now"', markup)
        self.assertNotIn("data.disabled", markup)


class DownloadDmgViewTest(_Fresh):
    def test_redirects_to_the_latest_dmg(self):
        with patch("aso.update_check.urllib.request.urlopen", return_value=_answer()):
            response = self.client.get(reverse("aso:download_dmg"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], DMG)

    def test_falls_back_to_the_releases_page_when_unreachable(self):
        with patch("aso.update_check.urllib.request.urlopen", side_effect=urllib.error.URLError("no network")):
            response = self.client.get(reverse("aso:download_dmg"))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], "https://github.com/respectlytics/respectaso/releases/latest")

    def test_shares_the_update_banner_answer(self):
        with patch("aso.update_check.urllib.request.urlopen", return_value=_answer()) as urlopen:
            self.client.get(reverse("aso:version_check"))
            response = self.client.get(reverse("aso:download_dmg"))
        self.assertEqual(urlopen.call_count, 1)
        self.assertEqual(response["Location"], DMG)



class NoUpdateSettingTest(_Fresh):
    """The check is part of the app, not a choice: no setting, no page."""

    def test_no_updates_page_and_no_card(self):
        from django.urls import NoReverseMatch
        with self.assertRaises(NoReverseMatch):
            reverse("aso:settings_updates")
        self.assertEqual(self.client.get("/settings/updates/").status_code, 404)
        desktop_bridge.register_backend(FakeBackend())
        try:
            response = self.client.get(reverse("aso:settings_mac_app"))
        finally:
            desktop_bridge.unregister_backend()
        self.assertNotContains(response, "Check for updates")
        self.assertNotContains(response, 'id="update-checks"')
