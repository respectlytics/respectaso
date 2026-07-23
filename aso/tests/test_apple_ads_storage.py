"""Tests for Apple Ads settings storage (shared settings.json handling)."""

import json
import stat
import tempfile
from pathlib import Path

from django.test import TestCase, override_settings

from aso.apple_ads import storage
from aso.apple_ads.auth import cookie_header


class StorageTestBase(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.data_dir = Path(self._tmp.name)
        self._override = override_settings(DATA_DIR=self.data_dir)
        self._override.enable()
        storage.reset_cache()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self._override.disable()
        storage.reset_cache()
        self._tmp.cleanup()

    @property
    def settings_path(self):
        return self.data_dir / "settings.json"


class ApplSettingsRoundTripTest(StorageTestBase):
    def test_defaults_when_missing(self):
        data = storage.load_apple_settings()
        self.assertEqual(data["popularity_source"], "")
        self.assertFalse(data["apple_ads"]["tested_ok"])

    def test_preserves_foreign_owner_keys(self):
        """aso_pro's LLM keys must survive our writes (shared file)."""
        self.settings_path.write_text(json.dumps({
            "llm_provider": "anthropic",
            "api_keys": {"anthropic": "sk-ant-secret"},
        }))
        storage.reset_cache()
        storage.save_apple_settings(
            popularity_source="internal",
            apple_ads={"primary_app_id": "42"},
        )
        raw = json.loads(self.settings_path.read_text())
        self.assertEqual(raw["llm_provider"], "anthropic")
        self.assertEqual(raw["api_keys"]["anthropic"], "sk-ant-secret")
        self.assertEqual(raw["popularity_source"], "internal")
        self.assertEqual(raw["apple_ads"]["primary_app_id"], "42")

    def test_partial_apple_update_keeps_other_apple_keys(self):
        storage.save_apple_settings(apple_ads={"primary_app_id": "42"})
        storage.save_apple_settings(apple_ads={"tested_ok": True})
        block = storage.load_apple_settings()["apple_ads"]
        self.assertEqual(block["primary_app_id"], "42")
        self.assertTrue(block["tested_ok"])

    def test_file_permissions_600(self):
        storage.save_apple_settings(popularity_source="internal")
        mode = stat.S_IMODE(self.settings_path.stat().st_mode)
        self.assertEqual(mode, 0o600)

    def test_invalid_source_rejected(self):
        with self.assertRaises(ValueError):
            storage.save_apple_settings(popularity_source="banana")

    def test_corrupt_file_yields_defaults(self):
        self.settings_path.write_text("{not json")
        storage.reset_cache()
        data = storage.load_apple_settings()
        self.assertEqual(data["popularity_source"], "")

    def test_mtime_cache_invalidated_on_save(self):
        storage.save_apple_settings(popularity_source="internal")
        self.assertEqual(storage.get_popularity_source(), "internal")
        storage.save_apple_settings(popularity_source="apple")
        self.assertEqual(storage.get_popularity_source(), "apple")


class CookieHeaderTest(StorageTestBase):
    def test_expired_cookies_dropped(self):
        storage.save_apple_settings(apple_ads={"cookies": [
            {"name": "good", "value": "1", "domain": "", "path": "/", "expires": 0},
            {"name": "fresh", "value": "2", "domain": "", "path": "/", "expires": 4102444800.0},
            {"name": "stale", "value": "3", "domain": "", "path": "/", "expires": 1.0},
        ]})
        header = cookie_header()
        self.assertIn("good=1", header)
        self.assertIn("fresh=2", header)
        self.assertNotIn("stale", header)

    def test_empty_when_no_cookies(self):
        self.assertEqual(cookie_header(), "")

    def test_apple_source_ready_gating(self):
        self.assertFalse(storage.apple_source_ready())
        storage.save_apple_settings(apple_ads={"tested_ok": True})
        self.assertTrue(storage.apple_source_ready())
        storage.save_apple_settings(apple_ads={"session_expired": True})
        self.assertFalse(storage.apple_source_ready())


class SerializeCookiesTest(TestCase):
    """Harvested webview cookies must serialize on every pywebview backend."""

    @staticmethod
    def _jar(name, value, expires):
        from http.cookies import SimpleCookie

        jar = SimpleCookie()
        jar[name] = value
        jar[name]["path"] = "/"
        jar[name]["domain"] = ".apple.com"
        jar[name]["expires"] = expires
        return jar

    def test_nsdate_expires_object(self):
        """The macOS backend stores a native NSDate on the morsel, not a
        string. parsedate_to_datetime() raises AttributeError on it - the
        exact failure behind 'Signed in, but reading the session failed'."""
        from aso.apple_ads.auth import _serialize_cookies

        class FakeNSDate:
            def timeIntervalSince1970(self):
                return 4102444800.0

            def split(self, *args):  # pragma: no cover - must never be called
                raise AttributeError(
                    "'__NSTaggedDate' object has no attribute 'split'"
                )

        cookies = _serialize_cookies([self._jar("myacinfo", "tok", FakeNSDate())])
        self.assertEqual(len(cookies), 1)
        self.assertEqual(cookies[0]["name"], "myacinfo")
        self.assertEqual(cookies[0]["expires"], 4102444800.0)

    def test_http_date_string_expires(self):
        from aso.apple_ads.auth import _serialize_cookies

        cookies = _serialize_cookies(
            [self._jar("aasp", "v", "Fri, 01 Jan 2100 00:00:00 GMT")]
        )
        self.assertEqual(cookies[0]["expires"], 4102444800.0)

    def test_unparseable_expires_never_breaks_harvest(self):
        from aso.apple_ads.auth import _serialize_cookies

        cookies = _serialize_cookies([self._jar("site", "v", object())])
        self.assertEqual(len(cookies), 1)
        self.assertEqual(cookies[0]["expires"], 0.0)

    def test_session_cookie_without_expires(self):
        from aso.apple_ads.auth import _serialize_cookies

        cookies = _serialize_cookies([self._jar("dslang", "v", "")])
        self.assertEqual(cookies[0]["expires"], 0.0)

    def test_cookies_complete_requires_essential_cookie(self):
        """The harvest must not accept a cookie set until the essential
        session cookie is present - Apple sets cookies progressively and
        an early grab stores a broken session (KWS_NO_ORG_CONTENT_PROVIDERS
        on every later request)."""
        from aso.apple_ads.auth import _cookies_complete

        partial = [{"name": "dslang", "value": "x"}, {"name": "geo", "value": "y"}]
        self.assertFalse(_cookies_complete(partial))
        self.assertFalse(_cookies_complete([]))
        complete = partial + [{"name": "myacinfo", "value": "token"}]
        self.assertTrue(_cookies_complete(complete))


class SignInFlowTest(StorageTestBase):
    """Drive the embedded sign-in state machine with a stubbed webview.

    Regression coverage for three real-world breakages:
    1. A finished sign-in must be SAVED even if the user closes the window
       during verification (closing can never lose the session).
    2. Successful verification sets tested_ok (it IS the connection test).
    3. Cancelling before login, then clicking again, must start a fresh
       attempt (the button can never dead-end).
    """

    class _Handlers(list):
        def __iadd__(self, fn):
            self.append(fn)
            return self

    def _make_window(self):
        import types
        from http.cookies import SimpleCookie

        jar = SimpleCookie()
        jar["myacinfo"] = "tok"
        jar["myacinfo"]["path"] = "/"
        jar["myacinfo"]["domain"] = ".apple.com"

        window = types.SimpleNamespace()
        window.events = types.SimpleNamespace(
            loaded=self._Handlers(), closed=self._Handlers()
        )
        window.destroyed = False
        window.get_cookies = lambda: [jar]

        from aso.apple_ads import auth

        window.get_current_url = lambda: auth.DASHBOARD_URL

        def destroy():
            window.destroyed = True
            for fn in list(window.events.closed):
                fn()

        window.destroy = destroy
        return window

    def _run_flow(self, fetch_mock, primary_app_id="42", fire_loaded=True,
                  close_after_start=False):
        import sys
        import time as _time
        import types
        from unittest import mock

        from aso.apple_ads import auth

        if primary_app_id:
            storage.save_apple_settings(
                apple_ads={"primary_app_id": primary_app_id}
            )

        window = self._make_window()
        webview_stub = types.SimpleNamespace(
            create_window=lambda *a, **k: window
        )

        with mock.patch.dict(sys.modules, {"webview": webview_stub}), \
                mock.patch.object(auth, "HARVEST_SETTLE_SECONDS", 0), \
                mock.patch.object(auth, "HARVEST_RETRY_SECONDS", 0), \
                mock.patch.object(auth, "VERIFY_RETRY_SECONDS", 0), \
                mock.patch.object(auth, "SPA_BOOT_GRACE_SECONDS", 0.1), \
                mock.patch.object(auth.settings, "IS_NATIVE_APP", True, create=True), \
                mock.patch("aso.apple_ads.client.fetch_popularities", fetch_mock):
            state = auth.start_signin()
            self.assertEqual(state["status"], "active")
            if fire_loaded:
                for fn in list(window.events.loaded):
                    fn()
            if close_after_start:
                for fn in list(window.events.closed):
                    fn()
            # Wait for a terminal state.
            deadline = _time.time() + 5
            while _time.time() < deadline:
                status = auth.get_signin_status()["status"]
                if status in ("success", "cancelled", "error"):
                    break
                _time.sleep(0.05)
        return window, auth.get_signin_status()

    def test_verified_signin_sets_tested_ok(self):
        from unittest import mock

        window, state = self._run_flow(mock.MagicMock(return_value={"fitness": 50}))
        self.assertEqual(state["status"], "success")
        block = storage.load_apple_settings()["apple_ads"]
        self.assertTrue(block["tested_ok"])
        self.assertTrue(block["cookies"])
        self.assertFalse(block["session_expired"])
        self.assertTrue(window.destroyed)

    def test_manual_close_during_verification_keeps_session(self):
        """The user closing the window mid-verification must not cancel:
        the session is already saved and verification finishes headless."""
        from unittest import mock

        from aso.apple_ads.client import AppleAdsAPIError

        calls = {"n": 0}
        window_holder = {}

        def fetch(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                # Simulate the user closing the window during the first
                # (failing) verification attempt.
                for fn in list(window_holder["w"].events.closed):
                    fn()
                raise AppleAdsAPIError("KWS_NO_ORG_CONTENT_PROVIDERS")
            return {"fitness": 50}

        # Need the window reference inside the fetch mock: wrap _run_flow.
        orig_make = self._make_window

        def make_and_hold():
            window_holder["w"] = orig_make()
            return window_holder["w"]

        self._make_window = make_and_hold
        try:
            window, state = self._run_flow(fetch)
        finally:
            self._make_window = orig_make

        self.assertEqual(state["status"], "success")
        block = storage.load_apple_settings()["apple_ads"]
        self.assertTrue(block["tested_ok"])
        self.assertTrue(block["cookies"])

    def test_cancel_before_login_then_retry_works(self):
        from unittest import mock

        # First attempt: never reaches the dashboard; user closes.
        window, state = self._run_flow(
            mock.MagicMock(return_value={}), fire_loaded=False,
            close_after_start=True,
        )
        self.assertEqual(state["status"], "cancelled")
        # Second click must start a fresh active attempt.
        window2, state2 = self._run_flow(mock.MagicMock(return_value={"fitness": 50}))
        self.assertEqual(state2["status"], "success")

    def test_verification_exhaustion_saves_session_with_guidance(self):
        from unittest import mock

        from aso.apple_ads.client import AppleAdsAPIError

        window, state = self._run_flow(
            mock.MagicMock(side_effect=AppleAdsAPIError("KWS_NO_ORG_CONTENT_PROVIDERS"))
        )
        self.assertEqual(state["status"], "error")
        self.assertIn("Test connection", state["error"])
        block = storage.load_apple_settings()["apple_ads"]
        self.assertTrue(block["cookies"])  # session banked despite failure
        self.assertFalse(block["tested_ok"])
