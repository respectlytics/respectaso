"""Tests for the per-app "refresh from App Store" action.

App name/icon/seller are a snapshot taken when an app is first added. When the
developer renames the app on the App Store, the per-app refresh button pulls
the current values from iTunes (via track_id) and writes them back to the App
row — the single source of truth every screen reads from.

Verifies:
- A renamed app gets its name/icon/seller updated and a "renamed" message.
- An unchanged app reports "current" without spurious edits.
- An app Apple has no listing for leaves the row untouched and says so.
- Manual apps (no track_id) are a no-op (nothing to look up).
"""

import re
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from aso import app_profiles, countries
from aso.copy_rules import dash_punctuation_in, mechanics_in
from aso.models import App
from aso.tests.helpers import AppleLookup


class AppRefreshViewTest(TestCase):
    def setUp(self):
        self.app = App.objects.create(
            name="Old Title",
            track_id=123456789,
            icon_url="https://example.com/old.png",
            seller_name="Old Seller",
        )

    def _fresh(self, **overrides):
        data = {
            "trackName": "New Title",
            "artworkUrl100": "https://example.com/new.png",
            "sellerName": "New Seller",
        }
        data.update(overrides)
        return data

    @patch("aso.views.ITunesSearchService.lookup_by_id")
    def test_renamed_app_is_updated(self, mock_lookup):
        mock_lookup.return_value = self._fresh()

        resp = self.client.post(reverse("aso:app_refresh", args=[self.app.id]))

        mock_lookup.assert_called_once_with(123456789, country="us", retry=False)
        self.assertRedirects(resp, reverse("aso:apps") + "?refresh=renamed")
        self.app.refresh_from_db()
        self.assertEqual(self.app.name, "New Title")
        self.assertEqual(self.app.icon_url, "https://example.com/new.png")
        self.assertEqual(self.app.seller_name, "New Seller")

    @patch("aso.views.ITunesSearchService.lookup_by_id")
    def test_unchanged_app_reports_current(self, mock_lookup):
        mock_lookup.return_value = self._fresh(trackName="Old Title")

        resp = self.client.post(reverse("aso:app_refresh", args=[self.app.id]))

        self.assertRedirects(resp, reverse("aso:apps") + "?refresh=current")
        self.app.refresh_from_db()
        self.assertEqual(self.app.name, "Old Title")

    @patch("aso.views.ITunesSearchService.lookup_by_id")
    def test_no_listing_leaves_row_untouched(self, mock_lookup):
        mock_lookup.return_value = None

        resp = self.client.post(reverse("aso:app_refresh", args=[self.app.id]))

        self.assertRedirects(resp, reverse("aso:apps") + f"?refresh=missing&app={self.app.id}&in=us")
        self.app.refresh_from_db()
        self.assertEqual(self.app.name, "Old Title")
        self.assertEqual(self.app.icon_url, "https://example.com/old.png")

    @patch("aso.views.ITunesSearchService.lookup_by_id")
    def test_manual_app_is_a_noop(self, mock_lookup):
        manual = App.objects.create(name="Manual App")

        resp = self.client.post(reverse("aso:app_refresh", args=[manual.id]))

        mock_lookup.assert_not_called()
        self.assertRedirects(resp, reverse("aso:apps"))
        manual.refresh_from_db()
        self.assertEqual(manual.name, "Manual App")

    def test_get_is_rejected(self):
        resp = self.client.get(reverse("aso:app_refresh", args=[self.app.id]))
        self.assertEqual(resp.status_code, 405)

    @patch("aso.views.ITunesSearchService.lookup_by_id")
    def test_refresh_message_renders_on_apps_page(self, mock_lookup):
        mock_lookup.return_value = self._fresh()
        self.client.post(reverse("aso:app_refresh", args=[self.app.id]))

        resp = self.client.get(reverse("aso:apps") + "?refresh=renamed")
        self.assertContains(resp, "App details updated from the App Store.")


FOREST_ID = 866450515
FOREST = {"trackId": FOREST_ID, "trackName": "Forest: Focus for Productivity",
          "artworkUrl100": "https://example.com/forest.png", "sellerName": "SEEKRTECH CO., LTD."}
SWEDISH_LINK = "https://apps.apple.com/se/app/forest-focus-for-productivity/id866450515"
US_LINK = "https://apps.apple.com/us/app/forest-focus-for-productivity/id866450515"


def _profiles(*codes):
    """Profiles read in these storefronts, the first one most recently."""
    now = datetime.now(UTC)
    return {code: {"count": 100, "average": 4.5, "released": None, "genre": "Productivity",
                   "checked_at": (now - timedelta(hours=index)).isoformat()}
            for index, code in enumerate(codes)}


class TheRefreshLooksWhereTheAppIsKnownTest(TestCase):
    """A user's app listed only outside the United States: the refresh
    looked it up in the United States and said "Couldn't reach the App
    Store to refresh" while Apple had answered that it has no listing
    there. Apple's Lookup is faked at the HTTP layer."""

    def _app(self, store_url="", profiles=None):
        return App.objects.create(name=FOREST["trackName"], track_id=FOREST_ID, store_url=store_url,
                                  store_profiles=profiles or {}, icon_url="https://example.com/old.png",
                                  seller_name=FOREST["sellerName"])

    def _refresh(self, app, apple):
        with patch("aso.services.requests.get", side_effect=apple), \
                patch("aso.services.time.sleep") as sleep:
            response = self.client.post(reverse("aso:app_refresh", args=[app.pk]), follow=True)
        sleep.assert_not_called()  # a person waits: no pauses, no second ask
        self.assertEqual(response.status_code, 200)
        return response

    def _asked(self, apple):
        return [call["country"] for call in apple.calls]

    def test_an_app_added_from_another_storefront_is_looked_up_there(self):
        app = self._app(store_url=SWEDISH_LINK, profiles=_profiles("se"))
        apple = AppleLookup(dict(FOREST, artworkUrl100="https://example.com/new.png"))
        response = self._refresh(app, apple)
        self.assertEqual(self._asked(apple), ["se"])
        self.assertContains(response, "App is already up to date.")
        app.refresh_from_db()
        self.assertEqual(app.icon_url, "https://example.com/new.png")

    def test_the_next_storefront_is_asked_when_one_has_no_listing(self):
        app = self._app(store_url=US_LINK, profiles=_profiles("se", "de"))
        apple = AppleLookup("empty", dict(FOREST, trackName="Forest: Stay Focused"))
        response = self._refresh(app, apple)
        self.assertEqual(self._asked(apple), ["us", "se"])
        self.assertContains(response, "App details updated from the App Store.")
        app.refresh_from_db()
        self.assertEqual(app.name, "Forest: Stay Focused")

    def test_no_listing_names_the_app_and_where_it_looked(self):
        app = self._app(store_url=SWEDISH_LINK, profiles=_profiles("se", "us"))
        apple = AppleLookup("empty")
        response = self._refresh(app, apple)
        self.assertEqual(self._asked(apple), ["se", "us"])
        self.assertContains(response, "Apple has no listing for Forest in the App Store in Sweden "
                                      "or the United States.")
        self.assertNotContains(response, "reach the App Store")
        app.refresh_from_db()
        self.assertEqual(app.icon_url, "https://example.com/old.png")

    def test_apple_not_answering_is_said_as_such(self):
        app = self._app(store_url=SWEDISH_LINK, profiles=_profiles("se", "us"))
        for outcome in ("timeout", "connection", 403, 429, 503, 500, "garbage"):
            apple = AppleLookup(outcome, FOREST)
            with self.subTest(outcome=outcome):
                response = self._refresh(app, apple)
                self.assertEqual(self._asked(apple), ["se"])
                self.assertContains(response, "Apple did not answer about Forest; try again in a few minutes.")
                self.assertNotContains(response, "no listing")

    def test_a_storefront_with_no_listing_then_no_answer_is_no_answer(self):
        app = self._app(store_url=SWEDISH_LINK, profiles=_profiles("se", "us"))
        apple = AppleLookup("empty", "timeout")
        response = self._refresh(app, apple)
        self.assertEqual(self._asked(apple), ["se", "us"])
        self.assertContains(response, "Apple did not answer about Forest")

    def test_at_most_three_storefronts_are_asked(self):
        app = self._app(store_url=SWEDISH_LINK, profiles=_profiles("se", "de", "fr", "it", "es"))
        apple = AppleLookup("empty")
        response = self._refresh(app, apple)
        self.assertEqual(self._asked(apple), ["se", "de", "fr"])
        self.assertContains(response, "in the App Store in Sweden, Germany or France.")

    def test_an_app_known_nowhere_is_looked_up_in_the_united_states(self):
        app = self._app()
        apple = AppleLookup(FOREST)
        self._refresh(app, apple)
        self.assertEqual(self._asked(apple), ["us"])


class KnownStorefrontsTest(TestCase):
    def test_the_link_first_then_the_profiles_newest_first(self):
        app = App(name="Forest", store_url=US_LINK, store_profiles=_profiles("de", "se", "us"))
        self.assertEqual(app_profiles.known_storefronts(app), ["us", "de", "se"])

    def test_profiles_alone(self):
        app = App(name="Forest", store_profiles=_profiles("no", "se"))
        self.assertEqual(app_profiles.known_storefronts(app), ["no", "se"])

    def test_nothing_known_is_the_united_states(self):
        self.assertEqual(app_profiles.known_storefronts(App(name="Forest")), ["us"])

    def test_a_link_without_a_storefront_and_unknown_codes_are_left_out(self):
        app = App(name="Forest", store_url="https://apps.apple.com/app/id866450515",
                  store_profiles={"zz": {"checked_at": "2026-01-01T00:00:00+00:00"}})
        self.assertEqual(app_profiles.known_storefronts(app), ["us"])


class TheRefreshMessagesTest(TestCase):
    def test_storefronts_are_named_in_words(self):
        self.assertEqual(countries.stores_phrase(["se"]), "the App Store in Sweden")
        self.assertEqual(countries.stores_phrase(["se", "us"]),
                         "the App Store in Sweden or the United States")
        self.assertEqual(countries.stores_phrase(["se", "de", "fr"]),
                         "the App Store in Sweden, Germany or France")
        self.assertEqual(countries.stores_phrase([]), countries.store_phrase("us"))

    def test_each_is_one_short_sentence_that_reads_as_value(self):
        from aso.views import _refresh_problem

        app = App.objects.create(name=FOREST["trackName"], track_id=FOREST_ID)
        for status, query in (("missing", {"app": app.pk, "in": "se,us,de"}), ("unanswered", {"app": app.pk})):
            with self.subTest(status=status):
                text = _refresh_problem(status, query)
                self.assertLessEqual(len(text), 100, text)
                self.assertEqual(len(re.findall(r"[.!?](?:\s|$)", text)), 1, text)
                self.assertEqual(dash_punctuation_in(text), "")
                self.assertEqual(mechanics_in(text), "")

    def test_an_address_typed_by_hand_never_breaks_the_page(self):
        for query in ("?refresh=missing&app=abc&in=xx,se", "?refresh=unanswered&app=", "?refresh=missing"):
            with self.subTest(query=query):
                response = self.client.get(reverse("aso:apps") + query)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "this app")
