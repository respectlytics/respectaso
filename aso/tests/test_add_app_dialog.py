"""The one Add an app dialog (aso/add_app.py, aso/partials/add_app_dialog.html,
static/js/add-app.js; owner, 2026-11-18): every page draws it once and every
"Add app" opens it, the Apps page has no add card of its own, and adding
the same app twice adds it once. Runs in both editions: the account's apps
come from a registered source, which the free edition never has."""

import json
import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from aso import add_app
from aso.models import App

ROOT = Path(settings.BASE_DIR)


def dialog_of(html):
    start = html.index('<dialog id="add-app-dialog"')
    return html[start:html.index("</dialog>", start)]


STORE = {"trackId": 4242, "trackName": "Nightjar: Sleep Sounds", "bundleId": "com.example.nightjar",
         "artworkUrl100": "https://is1.example/icon.png", "sellerName": "Example"}


class AddEndpointTest(TestCase):
    def post(self, body):
        return self.client.post(reverse("aso:app_add"), data=json.dumps(body), content_type="application/json")

    def test_an_app_store_app_is_added_once_however_often_it_is_clicked(self):
        first = self.post({"store": STORE}).json()
        self.assertEqual(first, {"added": ["Nightjar: Sleep Sounds"], "already": [],
                                 "message": "Added Nightjar: Sleep Sounds."})
        app = App.objects.get(track_id=4242)
        self.assertEqual((app.bundle_id, app.icon_url, app.seller_name),
                         ("com.example.nightjar", "https://is1.example/icon.png", "Example"))
        second = self.post({"store": STORE}).json()
        self.assertEqual(second["added"], [])
        self.assertEqual(second["message"], "Nightjar: Sleep Sounds was already in your apps.")
        self.assertEqual(App.objects.filter(track_id=4242).count(), 1)

    def test_an_app_added_by_hand_is_added_once(self):
        self.assertEqual(self.post({"manual": {"name": "Pocket Plan", "bundle_id": ""}}).json()["added"],
                         ["Pocket Plan"])
        again = self.post({"manual": {"name": "pocket plan", "bundle_id": ""}}).json()
        self.assertEqual(again["already"], ["Pocket Plan"])
        self.assertEqual(App.objects.filter(name__iexact="pocket plan").count(), 1)
        # Another bundle ID is another app.
        self.assertEqual(self.post({"manual": {"name": "Pocket Plan", "bundle_id": "com.example.pp"}}).json()["added"],
                         ["Pocket Plan"])

    def test_what_cannot_be_added_says_what_to_do(self):
        for body, words in (({"manual": {"name": "  "}}, "Type the app's name"),
                            ({"store": {"trackId": "abc"}}, "Search for it again"),
                            ({}, "Pick an app")):
            with self.subTest(body=body):
                response = self.post(body)
                self.assertEqual(response.status_code, 400)
                self.assertIn(words, response.json()["error"])
        self.assertEqual(self.client.get(reverse("aso:app_add")).status_code, 405)
        self.assertFalse(App.objects.exists())

    def test_the_search_marks_apps_already_added(self):
        App.objects.create(name="Nightjar", track_id=4242)
        found = [{"trackId": 4242, "trackName": "Nightjar", "artworkUrl100": "", "bundleId": "", "sellerName": ""},
                 {"trackId": 4343, "trackName": "Kestrel", "artworkUrl100": "", "bundleId": "", "sellerName": ""}]
        with mock.patch("aso.services.ITunesSearchService.find_apps", return_value=found):
            apps = self.client.get(reverse("aso:app_lookup"), {"q": "bird"}).json()["apps"]
        self.assertEqual([(a["trackName"], a["tracked"]) for a in apps], [("Nightjar", True), ("Kestrel", False)])

    def test_the_apps_page_no_longer_adds_on_a_post(self):
        self.client.post(reverse("aso:apps"), {"name": "Zeta", "bundle_id": ""})
        self.assertFalse(App.objects.exists())

    def test_names_in_a_sentence(self):
        self.assertEqual(add_app.result_message(["A", "B", "C"], []), "Added A, B and C.")
        self.assertEqual(add_app.result_message(["A"], ["B", "C"]), "Added A. B and C were already in your apps.")
        self.assertEqual(add_app.result_message([], []), "Nothing to add.")


class DialogContentTest(TestCase):
    def page(self, name="aso:apps"):
        return self.client.get(reverse(name)).content.decode()

    def test_without_an_account_source_search_and_manual_only(self):
        with mock.patch.object(add_app, "account_sources", []):
            dialog = dialog_of(self.page())
        self.assertNotIn('id="add-app-account"', dialog)
        self.assertIn('id="app-search-input"', dialog)
        self.assertIn(f'data-lookup-url="{reverse("aso:app_lookup")}"', dialog)
        self.assertIn(f'data-add-url="{reverse("aso:app_add")}"', dialog)
        self.assertIn("Add one that is not in the App Store yet", dialog)
        self.assertIn("Manual apps can't track keyword rankings.", dialog)
        self.assertIn('name="add-app-name"', dialog)

    def test_the_account_apps_with_one_main_button_and_select_all_past_three(self):
        apps = [{"id": str(9000 + n), "name": f"App {n}", "bundle_id": f"com.example.{n}"} for n in range(4)]
        with mock.patch.object(add_app, "account_sources", [lambda: {"apps": apps, "url": "/add-these/"}]):
            dialog = dialog_of(self.page())
        self.assertIn('data-url="/add-these/"', dialog)
        self.assertEqual(dialog.count('name="add-app-account"'), 4)
        self.assertIn('id="add-app-account-all"', dialog)
        self.assertRegex(dialog, r'id="add-app-account-button" disabled>')
        self.assertLess(dialog.index('id="add-app-account"'), dialog.index('id="app-search-input"'))
        with mock.patch.object(add_app, "account_sources", [lambda: {"apps": apps[:3], "url": "/x/"}]):
            self.assertNotIn('id="add-app-account-all"', dialog_of(self.page()))

    def test_a_failing_source_leaves_search_and_never_breaks_the_page(self):
        def broken():
            raise RuntimeError("no")

        with mock.patch.object(add_app, "account_sources", [broken]), self.assertLogs("aso.templatetags.aso_tags"):
            response = self.client.get(reverse("aso:apps"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('id="add-app-account"', dialog_of(response.content.decode()))


class EntryPointsTest(TestCase):
    """Every "Add app" opens the one dialog; no page has an add form of its own."""

    def test_every_page_draws_the_dialog_once(self):
        App.objects.create(name="Moonpond", track_id=7)
        for name in ("aso:apps", "aso:dashboard", "aso:methodology"):
            with self.subTest(name=name):
                html = self.client.get(reverse(name)).content.decode()
                self.assertEqual(html.count('id="add-app-dialog"'), 1)
                self.assertIn("js/add-app.js", html)

    def test_the_apps_and_keywords_buttons_open_it(self):
        html = self.client.get(reverse("aso:apps")).content.decode()
        self.assertIn('data-add-app>Add your app…</button>', html)            # the first meeting
        self.assertNotIn('id="apps-add"', html)
        self.assertNotIn('id="apps-manual-form"', html)
        keywords = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertIn('id="add-app-button" data-add-app>', keywords)
        App.objects.create(name="Moonpond", track_id=7)
        html = self.client.get(reverse("aso:apps")).content.decode()
        self.assertIn('id="apps-add-button" data-add-app>Add app…</button>', html)

    def test_both_base_templates_draw_it(self):
        # The public repo has no _public_overrides folder: its base.html is the one in aso/.
        for rel in ("aso/templates/aso/base.html", "_public_overrides/aso/templates/aso/base.html"):
            if not (ROOT / rel).is_file():
                continue
            text = (ROOT / rel).read_text(encoding="utf-8")
            with self.subTest(rel=rel):
                self.assertIn("{% add_app_dialog %}", text)
                self.assertIn("{% static 'js/add-app.js' %}", text)

    def test_every_link_to_add_an_app_opens_the_dialog(self):
        """A link to the Apps page's ?add=1 is only a fallback: the tag carries data-add-app."""
        for folder in ("aso/templates", "aso_pro/templates"):
            for path in (ROOT / folder).rglob("*.html"):
                text = path.read_text(encoding="utf-8")
                for tag in re.findall(r"<a\b[^>]*\?add=1[^>]*>", text):
                    with self.subTest(path=str(path.relative_to(ROOT)), tag=tag):
                        self.assertIn("data-add-app", tag)
        # The one dialog: no second add-app dialog anywhere.
        for folder in ("aso/templates", "aso_pro/templates"):
            for path in (ROOT / folder).rglob("*.html"):
                if path.name == "add_app_dialog.html":
                    continue
                with self.subTest(path=str(path.relative_to(ROOT))):
                    self.assertNotIn("Find it by name or App Store link", path.read_text(encoding="utf-8"))
                    self.assertNotIn('id="app-search-input"', path.read_text(encoding="utf-8"))
