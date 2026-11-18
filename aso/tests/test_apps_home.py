"""The Apps page (aso/apps_home.py, aso/templates/aso/apps.html,
docs/development/DAILY_USE_PLAN.md step 3).

Without a Pro license an app's row is today's: its name and keyword count
(D12). With one it has its numbers, its To do lines or "Nothing to do.".
"""

import json
import re
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse

from aso import apps_home, rank_change, setup_checklist, todo
from aso.apple_ads import storage
from aso.models import App, Keyword, SearchResult
from aso.tests.test_todo import FakeSource


def line(key, app, tone="warning", counts=True):
    return todo.Line(app_id=app.pk, key=key, tone=tone, text="Something to look at.", label="Open", url="/",
                     counts=counts)


def section_of(html, element_id, end="</ul>"):
    start = html.index(f'id="{element_id}"')
    return html[start:html.index(end, start)]


class AppsHomeTestBase(TestCase):
    def setUp(self):
        todo.reset()
        rank_change.reset()
        self.addCleanup(todo.reset)
        self.addCleanup(rank_change.reset)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        override = override_settings(DATA_DIR=Path(tmp.name), DEBUG_SKIP_LICENSE=False)
        override.enable()
        self.addCleanup(override.disable)
        storage.reset_cache()
        self.addCleanup(storage.reset_cache)
        self.use()

    def use(self, *sources):
        """Run with exactly these To do sources and no refiner, number source or open hook."""
        for target, name, value in ((todo, "sources", list(sources)), (todo, "refiners", []),
                                    (apps_home, "number_sources", []), (apps_home, "open_hooks", [])):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        todo.reset()

    def page(self, **query):
        return self.client.get(reverse("aso:apps"), query).content.decode()

    def track(self, app, keyword, rank=5):
        return SearchResult.objects.create(keyword=Keyword.objects.create(keyword=keyword, app=app), country="us",
                                           difficulty_score=40, popularity_score=50, app_rank=rank)


class FirstMeetingTest(AppsHomeTestBase):
    def test_with_no_app_the_first_meeting_and_its_add_button(self):
        html = self.page()
        self.assertIn('id="apps-first"', html)
        self.assertIn('data-add-app>Add your app…</button>', section_of(html, "apps-first", "</section>"))
        self.assertNotIn('id="apps-add"', html)                         # no add card in the page (owner, 2026-11-18)
        self.assertNotIn('id="apps-list"', html)
        self.assertIn("order-last", re.search(r'<div id="apps-setup"[^>]*class="([^"]*)"', html).group(1))
        # The first meeting's picture is the page in small: no second sample card under it.
        self.assertNotIn('id="apps-pro-sample"', html)

    def test_a_data_folder_with_an_app_never_shows_it(self):
        """The 3.2.0 shape: an update keeps the person's apps (D15)."""
        App.objects.create(name="Moonpond")
        html = self.page()
        self.assertNotIn('id="apps-first"', html)
        self.assertIn("order-first", re.search(r'<div id="apps-setup"[^>]*class="([^"]*)"', html).group(1))

    def test_add_app_opens_the_one_dialog(self):
        App.objects.create(name="Moonpond")
        html = self.page(add="1")       # add-app.js opens the dialog on arrival
        self.assertIn('id="apps-add-button" data-add-app>Add app…</button>', html)
        self.assertEqual(html.count('id="add-app-dialog"'), 1)
        self.assertNotIn('id="apps-add"', html)


    def test_a_refresh_that_found_nothing_leaves_the_add_form_folded(self):
        """The refresh answers with the Apps page and an error: before, that error opened the add
        form under the person's rows, as if an add had failed."""
        app = App.objects.create(name="Moonpond", track_id=7)
        html = self.page(refresh="missing", app=app.pk, **{"in": "us"})
        self.assertIn("Apple has no listing for Moonpond", html)


class RowsTest(AppsHomeTestBase):
    def setUp(self):
        super().setUp()
        self.a = App.objects.create(name="Alpha", track_id=1)
        self.b = App.objects.create(name="Beta", track_id=2)
        self.track(self.a, "sleep sounds", rank=4)
        self.track(self.b, "white noise", rank=30)

    def test_licensed_each_row_has_three_numbers_and_the_app_that_needs_most_first(self):
        self.use(FakeSource([line("x:1", self.b)]))
        with override_settings(DEBUG_SKIP_LICENSE=True):
            html = self.page()
        rows = section_of(html, "apps-list")
        for label in ("Keywords", "In the top 10", "Moved"):
            self.assertEqual(rows.count(f">{label}</"), 2, label)
        self.assertLess(rows.index(">Beta<"), rows.index(">Alpha<"))
        self.assertIn('data-todo="x:1"', rows)

    def test_without_a_license_each_row_is_todays(self):
        App.objects.create(name="Gamma")    # no keyword: the link to track some
        self.use(FakeSource([line("x:1", self.b)]))
        html = self.page()
        rows = section_of(html, "apps-list")
        self.assertEqual(rows.count(">Keywords</a>"), 2)
        self.assertIn(">Track keywords</a>", rows)
        for absent in ("In the top 10", "Moved", "data-todo", "Nothing to do."):
            with self.subTest(absent=absent):
                self.assertNotIn(absent, rows)
        self.assertNotIn('data-todo="', html)
        self.assertNotIn("Nothing to do.", html)
        self.assertNotIn("Your routine", html)
        self.assertNotIn("data-todo-count class=\"rounded", html)   # the number beside Apps stays hidden

    def test_a_fourth_line_folds_behind_n_more(self):
        self.use(FakeSource([line(f"x:{n}", self.a) for n in range(4)]))
        with override_settings(DEBUG_SKIP_LICENSE=True):
            html = self.page()
        fold = html[html.index(f'id="apps-more-{self.a.pk}"'):]
        fold = fold[:fold.index("</details>")]
        self.assertIn(">1 more</summary>", fold)
        self.assertIn('data-todo="x:3"', fold)
        self.assertNotIn('data-todo="x:0"', fold)

    def test_licensed_an_app_with_no_line_shows_nothing_to_do(self):
        with override_settings(DEBUG_SKIP_LICENSE=True):
            html = self.page()
        rows = section_of(html, "apps-list")
        self.assertEqual(rows.count("Nothing to do."), 2)
        self.assertNotIn("data-todo", rows)

    def test_a_closed_line_goes_and_the_number_beside_apps_falls(self):
        self.use(FakeSource([line("x:1", self.a), line("y:1", self.b)]))
        with override_settings(DEBUG_SKIP_LICENSE=True):
            html = self.page()
            self.assertIn('aria-label="things to do">2</span>', html)
            response = self.client.post(reverse("aso:notice_dismiss"), json.dumps({"key": "x:1"}),
                                        content_type="application/json")
            self.assertEqual(response.status_code, 200)
            html = self.page()
        self.assertNotIn('data-todo="x:1"', html)
        self.assertIn('data-todo="y:1"', html)
        self.assertIn('aria-label="things to do">1</span>', html)

    def test_the_number_counts_only_lines_that_ask_for_something(self):
        self.use(FakeSource([line("advice:1", self.a, tone="info", counts=False),
                             line("live:1", self.b, tone="success", counts=False)]))
        with override_settings(DEBUG_SKIP_LICENSE=True):
            html = self.page()
        self.assertIn('data-todo="advice:1"', html)
        self.assertIn('class="hidden rounded-full', html[html.index("data-todo-count"):][:80])

    def test_the_state_changes_after_a_keyword_and_after_a_closed_line(self):
        self.use(FakeSource([line("x:1", self.a)]))
        with override_settings(DEBUG_SKIP_LICENSE=True):
            first = self.client.get(reverse("aso:apps_state")).json()
            self.assertEqual(first["count"], 1)
            self.track(self.a, "rain sounds")
            todo.reset()
            second = self.client.get(reverse("aso:apps_state")).json()
            self.assertNotEqual(first["stamp"], second["stamp"])
            self.client.post(reverse("aso:notice_dismiss"), json.dumps({"key": "x:1"}),
                             content_type="application/json")
            third = self.client.get(reverse("aso:apps_state")).json()
        self.assertNotEqual(second["stamp"], third["stamp"])
        self.assertEqual(third["count"], 0)


class ProSampleTest(AppsHomeTestBase):
    def setUp(self):
        super().setUp()
        App.objects.create(name="Moonpond")

    def test_without_a_license_a_sample_card_with_no_button(self):
        html = self.page()
        sample = section_of(html, "apps-pro-sample", "</section>")
        self.assertIn("See each app's downloads, proceeds and what to do next", sample.replace("&#x27;", "'"))
        self.assertIn(">Sample</span>", sample)
        self.assertNotIn("<a ", sample)
        self.assertNotIn("<button", sample)
        self.assertIn("pointer-events-none", sample)
        self.assertLess(html.index('id="apps-list"'), html.index('id="apps-pro-sample"'))   # under the apps

    def test_with_a_license_no_sample(self):
        with override_settings(DEBUG_SKIP_LICENSE=True):
            html = self.page()
        self.assertNotIn('id="apps-pro-sample"', html)
        self.assertNotIn("Habito", html)


class FreeEditionShapeTest(AppsHomeTestBase):
    def test_the_page_renders_with_every_pro_hook_empty(self):
        App.objects.create(name="Moonpond")
        with mock.patch.dict(setup_checklist.pro_states, {}, clear=True):
            for licensed in (False, True):
                with self.subTest(licensed=licensed), override_settings(DEBUG_SKIP_LICENSE=licensed):
                    response = self.client.get(reverse("aso:apps"))
                    self.assertEqual(response.status_code, 200)
                    self.assertContains(response, 'id="apps-list"')
                    self.assertContains(response, 'data-setup-tile="asc" data-state="locked"')

    def test_a_failing_number_source_or_hook_never_stops_the_page(self):
        App.objects.create(name="Moonpond")

        def broken(*args):
            raise RuntimeError("Apple is down")

        with mock.patch.object(apps_home, "number_sources", [broken]), \
                mock.patch.object(apps_home, "open_hooks", [broken]), \
                override_settings(DEBUG_SKIP_LICENSE=True), \
                self.assertLogs("aso.apps_home", level="ERROR"):
            response = self.client.get(reverse("aso:apps"))
        self.assertEqual(response.status_code, 200)

    def test_the_open_hooks_run_on_a_full_load_only(self):
        calls = []
        with mock.patch.object(apps_home, "open_hooks", [lambda: calls.append(1)]):
            self.client.get(reverse("aso:apps"))
            self.client.get(reverse("aso:apps"), HTTP_X_REQUESTED_WITH="XMLHttpRequest")
        self.assertEqual(calls, [1])


class PeriodTest(AppsHomeTestBase):
    def test_days_30_is_remembered(self):
        App.objects.create(name="Moonpond")
        self.assertEqual(self.client.get(reverse("aso:apps")).context["home"]["days"], 7)
        self.assertEqual(self.client.get(reverse("aso:apps"), {"days": "30"}).context["home"]["days"], 30)
        self.assertEqual(self.client.get(reverse("aso:apps")).context["home"]["days"], 30)
        self.assertEqual(self.client.get(reverse("aso:apps"), {"days": "9"}).context["home"]["days"], 30)

    def test_the_switch_shows_only_with_store_numbers(self):
        app = App.objects.create(name="Moonpond")
        self.assertNotIn('aria-label="Period"', self.page())

        def numbers(apps, days, request):
            return {"apps": {app.pk: {"ready": True, "downloads": 612, "downloads_change": -0.31,
                                      "proceeds_text": "USD 288.00", "proceeds_change": 0.12, "url": "/"}},
                    "totals": None, "currency": "USD"}

        with mock.patch.object(apps_home, "number_sources", [numbers]), override_settings(DEBUG_SKIP_LICENSE=True):
            html = self.page()
        self.assertIn('aria-label="Period"', html)
        self.assertIn(">-31%</span>", html)
        self.assertIn(">+12%</span>", html)


class LiveUpdateMarkupTest(TestCase):
    """What the page's refresh relies on (DAILY_USE_PLAN.md 3.5): the Add an
    app dialog sits outside the swapped part, and nothing swapped binds its
    own listeners."""

    def test_the_add_dialog_is_outside_apps_home(self):
        html = self.client.get(reverse("aso:apps")).content.decode()
        home = html[html.index('id="apps-home"'):html.index("</main>")]
        self.assertNotIn('id="app-search-input"', home)
        self.assertNotIn('id="add-app-dialog"', home)

    def test_the_swapped_partials_hold_no_script(self):
        root = Path(settings.BASE_DIR, "aso/templates/aso/partials")
        for name in ("apps_row.html", "todo_line.html", "apps_first.html", "apps_pro_sample.html",
                     "setup_checklist.html"):
            with self.subTest(name=name):
                self.assertNotIn("<script", (root / name).read_text(encoding="utf-8"))

    def test_the_scripts_listen_on_document(self):
        for name in ("apps-home.js", "setup-strip.js"):
            text = Path(settings.BASE_DIR, "static/js", name).read_text(encoding="utf-8")
            with self.subTest(name=name):
                self.assertIn("document.addEventListener('click'", text)
                self.assertNotIn("localStorage", text)

    def test_the_badge_script_is_on_every_page(self):
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertIn(f'js/todo-badge.js" data-url="{reverse("aso:apps_state")}"', html)
