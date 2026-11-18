"""The Apps page's "Get set up" strip (aso/setup_checklist.py,
docs/development/DAILY_USE_PLAN.md step 3.7a): every tile that applies is
always drawn, in one order, with words that never change; the strip hides
with its X into a chip and comes back by itself when a tile turns amber.

The Pro tiles' own states (App Store Connect, AI provider, Notifications)
and the six cases of screen 14 are in aso_pro/tests/test_setup_tiles.py;
this file holds what the free edition has too.
"""

import re
import tempfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from django.apps import apps as django_apps
from django.test import RequestFactory, TestCase, override_settings
from django.urls import reverse

from aso import copy_rules, desktop_bridge, setup_checklist
from aso.apple_ads import storage
from aso.context_processors import pro_button
from aso.models import App

EXPIRED = SimpleNamespace(is_valid=False, is_expired=True, is_refunded=False,
                          is_revoked=False, months=12, days_until_expiry=-3)


def expired_license():
    """Patches that make this process hold a license that has ended (the
    app's own "license has ended" notice reads the middleware's copy)."""
    stack = ExitStack()
    stack.enter_context(mock.patch("licensing.decorators.get_license_info", return_value=EXPIRED))
    stack.enter_context(mock.patch("licensing.middleware.get_license_info", return_value=EXPIRED))
    return stack


def tiles_in(html):
    """[(key, state)] of the strip's tiles, in the order they are drawn."""
    return re.findall(r'data-setup-tile="(\w+)" data-state="(\w+)"', html)


def main_of(html):
    return html[html.index("<main"):html.index("</main>")]


def section(html, element_id):
    start = html.index(f'id="{element_id}"')
    return html[start:html.index("</section>", start)]


class StripTestBase(TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        override = override_settings(DATA_DIR=Path(tmp.name), DEBUG_SKIP_LICENSE=False, IS_NATIVE_APP=True)
        override.enable()
        self.addCleanup(override.disable)
        storage.reset_cache()
        self.addCleanup(storage.reset_cache)
        desktop_bridge.unregister_backend()
        self.addCleanup(desktop_bridge.unregister_backend)

    def request(self):
        request = RequestFactory().get("/")
        request.session = self.client.session
        return request

    def tiles(self):
        return {tile["key"]: tile for tile in setup_checklist.tiles(self.request())}

    def page(self):
        return self.client.get(reverse("aso:apps")).content.decode()

    def all_done(self):
        """Every tile done: a license, the Pro tiles settled, Apple Ads on the estimate."""
        stack = ExitStack()
        stack.enter_context(override_settings(DEBUG_SKIP_LICENSE=True))
        stack.enter_context(mock.patch.dict(setup_checklist.pro_states, {
            "asc": lambda request: {"state": "done", "word": "Connected"},
            "ai": lambda request: {"state": "done", "word": "Ready"},
            "notify": lambda request: None}, clear=True))
        storage.save_apple_settings(apple_ads={"estimate_opt_out": True})
        return stack


class ProTileTest(StripTestBase):
    def test_licensed_it_is_done_and_says_active(self):
        with override_settings(DEBUG_SKIP_LICENSE=True):
            tile = self.tiles()["pro"]
        self.assertEqual((tile["state"], tile["word"], tile["action"]), ("done", "Active", None))

    def test_without_a_license_it_holds_the_top_bar_button(self):
        for native in (True, False):
            with self.subTest(native=native), override_settings(IS_NATIVE_APP=native):
                tile = self.tiles()["pro"]
                top = pro_button(None)["pro_button"]
                self.assertEqual(tile["state"], "open")
                self.assertEqual((tile["action"]["label"], tile["action"]["url"]), (top["label"], top["url"]))
                self.assertEqual(tile["action"]["tone"], "pro")
                self.assertTrue(tile["action"]["external"])
        with override_settings(IS_NATIVE_APP=False):
            self.assertEqual(self.tiles()["pro"]["action"]["label"], "Get Pro for Mac")
        with override_settings(IS_NATIVE_APP=True):
            self.assertEqual(self.tiles()["pro"]["action"]["label"], "Get Pro")

    def test_its_quiet_choice_opens_the_license_settings(self):
        if not django_apps.is_installed("aso_pro"):
            self.assertIsNone(self.tiles()["pro"]["quiet"])
            return
        quiet = self.tiles()["pro"]["quiet"]
        self.assertEqual((quiet["label"], quiet["url"]), ("I have a license key", reverse("aso_pro:settings_license")))

    def test_an_ended_license_is_amber_with_renew_pro(self):
        if not django_apps.is_installed("licensing"):
            self.skipTest("the free edition has no license")
        with expired_license():
            tile = self.tiles()["pro"]
            top = pro_button(None)["pro_button"]
        self.assertEqual((tile["state"], tile["word"]), ("warn", "Ended"))
        self.assertEqual((tile["action"]["label"], tile["action"]["url"]), ("Renew Pro", top["url"]))
        self.assertEqual(tile["quiet"]["label"], "I renewed: refresh my key")


class AppleTileTest(StripTestBase):
    def test_a_fresh_install_offers_connect_and_the_estimate(self):
        tile = self.tiles()["apple"]
        self.assertEqual(tile["state"], "open")
        self.assertEqual((tile["action"]["label"], tile["action"]["url"]),
                         ("Connect", reverse("aso:settings_popularity") + "#apple-connection"))
        self.assertEqual(tile["quiet"]["label"], "Stay on the estimate")
        self.assertEqual(tile["quiet"]["url"], reverse("aso:settings_popularity"))
        self.assertEqual(tile["quiet"]["fields"], {"action": "estimate_opt_out", "opt_out": "1"})

    def test_staying_on_the_estimate_settles_it(self):
        response = self.client.post(reverse("aso:settings_popularity"), {"action": "estimate_opt_out", "opt_out": "1"})
        self.assertEqual(response.status_code, 200)
        tile = self.tiles()["apple"]
        self.assertEqual((tile["state"], tile["word"], tile["action"]), ("done", "Estimate", None))

    def test_the_strip_script_posts_the_quiet_choice_to_its_form_address(self):
        """The quiet choice's form carries a field named "action", which shadows the form's own
        action property in the browser: the click posted to "/[object HTMLInputElement]" (404)
        and settled nothing, so the script reads the attribute."""
        script = (Path(__file__).resolve().parents[2] / "static" / "js" / "setup-strip.js").read_text()
        self.assertIn("form.getAttribute('action')", script)
        self.assertNotRegex(script, r"form\.action\b")

    def test_connected_says_so(self):
        with mock.patch("aso.apple_ads.storage.apple_source_ready", return_value=True):
            tile = self.tiles()["apple"]
        self.assertEqual((tile["state"], tile["word"]), ("done", "Connected"))

    def test_a_broken_connection_is_amber(self):
        storage.save_apple_settings(popularity_source=storage.SOURCE_APPLE)   # chosen, but not connected
        tile = self.tiles()["apple"]
        self.assertEqual((tile["state"], tile["word"]), ("warn", "Needs you"))
        self.assertEqual((tile["action"]["label"], tile["action"]["url"]),
                         ("Open", reverse("aso:settings_popularity") + "#apple-connection"))


class LockedTilesTest(StripTestBase):
    def test_without_a_license_the_pro_tiles_are_locked(self):
        tiles = self.tiles()
        for key in ("asc", "ai"):
            with self.subTest(key=key):
                self.assertEqual((tiles[key]["state"], tiles[key]["word"], tiles[key]["action"]),
                                 ("locked", "With Pro", None))

    def test_the_free_edition_shape_locks_them_even_with_a_license(self):
        with override_settings(DEBUG_SKIP_LICENSE=True), mock.patch.dict(setup_checklist.pro_states, {}, clear=True):
            tiles = self.tiles()
        self.assertEqual((tiles["asc"]["state"], tiles["ai"]["state"]), ("locked", "locked"))

    def test_a_failing_tile_is_still_drawn(self):
        def broken(request):
            raise RuntimeError("this tile broke")

        with override_settings(DEBUG_SKIP_LICENSE=True), \
                mock.patch.dict(setup_checklist.pro_states, {"asc": broken}), \
                self.assertLogs("aso.setup_checklist", level="ERROR"):
            tiles = self.tiles()
        self.assertEqual(tiles["asc"]["state"], "locked")
        self.assertEqual(list(tiles), ["pro", "asc", "ai", "apple"])


class OutsideTheMacAppTest(StripTestBase):
    def test_no_notifications_tile_and_four_tiles(self):
        App.objects.create(name="Moonpond")
        html = self.page()
        self.assertEqual([key for key, _state in tiles_in(html)], ["pro", "asc", "ai", "apple"])
        self.assertIn("0 of 4 done", html)


class EverythingDoneTest(StripTestBase):
    def test_no_strip_and_no_chip(self):
        App.objects.create(name="Moonpond")
        with self.all_done():
            self.assertIsNone(setup_checklist.strip(self.request()))
            html = self.page()
        self.assertNotIn('id="setup-strip"', html)
        self.assertNotIn("Set up:", html)
        self.assertIn('id="apps-setup"', html)        # the wrappers stay, so a swap finds them
        self.assertIn('id="apps-setup-chip"', html)


class HidingTest(StripTestBase):
    def setUp(self):
        super().setUp()
        App.objects.create(name="Moonpond")

    def post(self, **data):
        return self.client.post(reverse("aso:setup_strip"), data)

    def test_its_x_hides_it_into_a_chip_and_the_chip_brings_it_back(self):
        self.assertIn('id="setup-strip"', self.page())
        self.assertEqual(self.post(do="hide").status_code, 200)
        html = self.page()
        self.assertNotIn('id="setup-strip"', html)
        # Pro license open, App Store Connect and AI provider locked, Apple Ads open.
        self.assertIn('data-setup-do="show"', html)
        self.assertIn(">Set up: 4 left</button>", html)
        self.assertEqual(self.post(do="show").status_code, 200)
        html = self.page()
        self.assertIn('id="setup-strip"', html)
        self.assertNotIn("Set up:", html)

    def test_an_ended_license_brings_it_back_by_itself(self):
        if not django_apps.is_installed("licensing"):
            self.skipTest("the free edition has no license")
        self.post(do="hide")
        with expired_license():
            self.assertIn('id="setup-strip"', self.page())

    def test_hidden_while_already_ended_it_stays_hidden(self):
        if not django_apps.is_installed("licensing"):
            self.skipTest("the free edition has no license")
        with expired_license():
            self.post(do="hide")
            html = self.page()
        self.assertNotIn('id="setup-strip"', html)
        self.assertIn("Set up:", html)

    def test_a_card_closed_for_good_in_3_2_0_starts_hidden(self):
        storage.save_block("ui", {setup_checklist.OLD_CLOSED: True})
        html = self.page()
        self.assertNotIn('id="setup-strip"', html)
        self.assertIn("Set up:", html)
        self.post(do="show")
        self.assertIn('id="setup-strip"', self.page())

    def test_an_unknown_action_is_refused(self):
        self.assertEqual(self.post(do="explode").status_code, 400)
        self.assertEqual(self.client.get(reverse("aso:setup_strip")).status_code, 405)


class SkipTest(StripTestBase):
    def test_only_notifications_can_be_skipped(self):
        response = self.client.post(reverse("aso:setup_strip"), {"do": "skip", "tile": "notify"})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(setup_checklist.skipped("notify"))
        for tile in ("apple", "asc", "pro", ""):
            with self.subTest(tile=tile):
                response = self.client.post(reverse("aso:setup_strip"), {"do": "skip", "tile": tile})
                self.assertEqual(response.status_code, 400)
                self.assertFalse(setup_checklist.skipped(tile))


class PlaceTest(StripTestBase):
    def wrapper(self, html):
        return re.search(r'<div id="apps-setup"[^>]*class="([^"]*)"', html).group(1)

    def test_with_no_app_it_comes_last_and_with_an_app_first(self):
        self.assertIn("order-last", self.wrapper(self.page()))
        App.objects.create(name="Moonpond")
        self.assertIn("order-first", self.wrapper(self.page()))

    def test_it_is_the_first_child_in_the_source_either_way(self):
        html = self.page()
        self.assertLess(html.index('id="apps-setup"'), html.index('id="apps-home"'))


class OneProButtonTest(StripTestBase):
    def test_the_page_content_has_one_pro_button_and_the_sample_none(self):
        App.objects.create(name="Moonpond")
        main = main_of(self.page())
        self.assertEqual(main.count('class="btn-pro'), 1)
        self.assertIn('class="btn-pro', section(main, "setup-strip"))
        self.assertNotIn("btn-pro", section(main, "apps-pro-sample"))
        self.client.post(reverse("aso:setup_strip"), {"do": "hide"})
        self.assertEqual(main_of(self.page()).count('class="btn-pro'), 0)


class WordsTest(TestCase):
    def test_every_word_of_the_strip_follows_the_copy_rules(self):
        words = [text for _key, name, gives in setup_checklist.TILES for text in (name, gives)]
        words += ["Active", "Ended", "With Pro", "Connected", "Estimate", "Needs you", "Ready", "On", "Off",
                  "Blocked in macOS", "Vendor number missing", "Sales numbers missing", "Store page key missing",
                  "Get Pro", "Get Pro for Mac", "Renew Pro", "I have a license key", "I renewed: refresh my key",
                  "Connect", "Finish", "Choose", "Open", "Turn on", "Turn on in macOS", "Not now", "Stay on the estimate",
                  "Get set up", "Set up: 2 left", "This tile cannot be skipped.", "Unknown action."]
        for text in words:
            with self.subTest(text=text):
                self.assertEqual(copy_rules.mechanics_in(text), "")
                self.assertEqual(copy_rules.dash_punctuation_in(text), "")


class KeywordsPageTest(TestCase):
    def test_the_keywords_page_has_no_setup_card(self):
        App.objects.create(name="Moonpond")
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertNotIn('id="setup-strip"', html)
        self.assertNotIn('id="setup-checklist"', html)
