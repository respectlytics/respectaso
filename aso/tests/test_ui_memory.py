"""What the app remembers for a visitor between pages (aso/ui_memory.py).

The Dashboard's view, the folded App Summary and the country pickers' last
selection used to live in the browser's storage, which desktop-compat keeps
out of the app. They live in the Django session now
(docs/development/NO_BROWSER_STORAGE_PLAN.md).

Free-tier test: no aso_pro import.
"""

import json
import os
import re
from urllib.parse import parse_qs, urlsplit

from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse

from aso import history_filters
from aso.models import App, Keyword, SearchResult

IN_PLACE = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest"}
BACKGROUND = {"HTTP_X_REQUESTED_WITH": "XMLHttpRequest", "HTTP_X_IN_PLACE": "background"}


def _row(app, country, n=0):
    keyword = Keyword.objects.create(keyword=f"word {n} {country} {app.pk}", app=app)
    return SearchResult.objects.create(
        keyword=keyword, country=country, popularity_score=40, difficulty_score=30,
        difficulty_breakdown={}, competitors_data=[], app_rank=3,
    )


class DashboardViewMemoryTest(TestCase):
    def setUp(self):
        self.calm = App.objects.create(name="Calm Minutes")
        self.tide = App.objects.create(name="Tide Notes")
        for n in range(3):
            _row(self.calm, "us", n)
            _row(self.calm, "gb", n)
            _row(self.tide, "us", n)
        self.url = reverse("aso:dashboard")

    def _redirect_query(self, response):
        self.assertEqual(response.status_code, 302)
        parts = urlsplit(response["Location"])
        self.assertEqual(parts.path, self.url)
        return parse_qs(parts.query)

    def test_view_is_remembered_and_restored(self):
        shown = {"app": self.calm.pk, "country": "us", "per_page": 50,
                 "insight": "Hidden Gem", "q": "word"}
        self.assertEqual(self.client.get(self.url, shown).status_code, 200)
        response = self.client.get(self.url)
        self.assertEqual(self._redirect_query(response), {
            "app": [str(self.calm.pk)], "country": ["us"], "per_page": ["50"],
            "insight": ["Hidden Gem"], "q": ["word"],
        })
        followed = self.client.get(response["Location"])
        self.assertEqual(followed.status_code, 200, "the restored address must not redirect again")
        self.assertEqual(followed.context["per_page"], 50)

    def test_in_place_load_remembers_and_background_does_not(self):
        self.client.get(self.url, {"country": "gb"}, **IN_PLACE)
        self.client.get(self.url, {"country": "us"}, **BACKGROUND)
        self.assertEqual(self._redirect_query(self.client.get(self.url)), {"country": ["gb"]})

    def test_an_in_place_load_is_never_redirected(self):
        self.client.get(self.url, {"country": "gb"})
        self.assertEqual(self.client.get(self.url, **IN_PLACE).status_code, 200)

    def test_clearing_forgets(self):
        self.client.get(self.url, {"app": self.calm.pk, "per_page": 50, "q": "word"}, **IN_PLACE)
        self.client.get(self.url, {"app": self.calm.pk}, **IN_PLACE)
        self.assertEqual(self._redirect_query(self.client.get(self.url)),
                         {"app": [str(self.calm.pk)], "per_page": ["50"]})
        self.client.get(self.url, **IN_PLACE)
        self.assertEqual(self._redirect_query(self.client.get(self.url)), {"per_page": ["50"]})

    def test_the_address_wins_per_part(self):
        self.client.get(self.url, {"country": "gb", "q": "word"})
        query = self._redirect_query(self.client.get(self.url, {"country": "us"}))
        self.assertEqual(query, {"country": ["us"], "q": ["word"]})
        self.client.get(self.url, {"country": "gb", "q": "word"})
        self.assertEqual(self.client.get(self.url, {"country": "gb", "q": "calm"}).status_code, 200)

    def test_page_is_dropped_and_sort_is_kept(self):
        self.client.get(self.url, {"country": "gb"})
        query = self._redirect_query(self.client.get(self.url, {"sort": "keyword", "page": "2"}))
        self.assertEqual(query, {"sort": ["keyword"], "country": ["gb"]})

    def test_a_country_the_table_does_not_offer_is_not_restored(self):
        """Tide Notes has no row in gb, so its table offers no gb."""
        self.client.get(self.url, {"app": self.calm.pk, "country": "gb"})
        self.assertEqual(self.client.get(self.url, {"app": self.tide.pk}).status_code, 200)

    def test_a_deleted_app_is_not_restored(self):
        self.client.get(self.url, {"app": self.calm.pk, "country": "gb"})
        self.calm.delete()   # its keywords stay, without an app
        self.assertEqual(self._redirect_query(self.client.get(self.url)), {"country": ["gb"]})
        SearchResult.objects.filter(country="gb").delete()
        self.client.get(self.url, {"app": self.tide.pk}, **IN_PLACE)
        self.tide.delete()
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_default_page_size_is_not_added(self):
        self.client.get(self.url, {"per_page": 25})
        self.assertEqual(self.client.get(self.url).status_code, 200)

    def test_two_visitors_do_not_share(self):
        other = Client()
        self.client.get(self.url, {"app": self.calm.pk})
        other.get(self.url, {"app": self.tide.pk})
        self.assertEqual(self._redirect_query(self.client.get(self.url)), {"app": [str(self.calm.pk)]})
        self.assertEqual(self._redirect_query(other.get(self.url)), {"app": [str(self.tide.pk)]})

    def test_unchanged_view_writes_nothing(self):
        first = self.client.get(self.url, {"country": "gb"}, **IN_PLACE)
        self.assertIn(settings.SESSION_COOKIE_NAME, first.cookies)
        second = self.client.get(self.url, {"country": "gb"}, **IN_PLACE)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, second.cookies)

    def test_a_visitor_who_chose_nothing_gets_no_session(self):
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(settings.SESSION_COOKIE_NAME, response.cookies)


class AppSummaryFoldedTest(TestCase):
    """The server draws the panel folded or open, as this visitor left it."""

    def setUp(self):
        self.app = App.objects.create(name="Calm Minutes")
        _row(self.app, "us")
        self.url = reverse("aso:dashboard")

    def _panel(self, client=None):
        html = (client or self.client).get(self.url, {"app": self.app.pk}).content.decode()
        start = html.index('id="app-summary-section"')
        return html[start:html.index("data-summary-body", start) + 80]

    def _fold(self, value):
        response = self.client.post("/ui-memory/", json.dumps({"name": "app_summary_folded", "value": value}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)

    def test_details_folded_until_opened(self):
        # The glance card's details (by country, rank spread, keyword mix)
        # start folded (KEYWORDS_PAGE_PLAN.md M2.4).
        panel = self._panel()
        self.assertIn('data-folded="true"', panel)
        self.assertIn('aria-expanded="false"', panel)
        self.assertIn("rotate(-90deg)", panel)
        self.assertIn("pb-4 hidden", panel)

    def test_opened_is_drawn_open(self):
        self._fold(False)
        panel = self._panel()
        self.assertIn('data-folded="false"', panel)
        self.assertIn('aria-expanded="true"', panel)
        self.assertNotIn("pb-4 hidden", panel)
        self.assertIn('data-folded="true"', self._panel(Client()), "another visitor keeps them folded")

    def test_fold_again(self):
        self._fold(False)
        self._fold(True)
        self.assertIn('data-folded="true"', self._panel())

    def test_every_page_loads_the_memory_script(self):
        for rel in ("aso/templates/aso/base.html", "_public_overrides/aso/templates/aso/base.html"):
            path = os.path.join(settings.BASE_DIR, rel)
            if os.path.exists(path):
                with open(path) as f, self.subTest(template=rel):
                    self.assertIn("js/ui-memory.js' %}\" data-url=\"{% url 'aso:ui_memory' %}\"", f.read())


class CountryPicksTest(TestCase):
    """Each picker shows the countries this visitor last picked in it."""

    def _pick(self, picker, codes):
        response = self.client.post("/ui-memory/", json.dumps({"name": f"countries.{picker}", "value": codes}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        return response.json()["value"]

    def _root(self, url, picker_id, client=None):
        html = (client or self.client).get(url).content.decode()
        match = re.search(rf'<div class="cp-root relative" id="{picker_id}"(.*?)>', html, re.DOTALL)
        self.assertIsNotNone(match, picker_id)
        return match.group(1)

    def test_each_picker_remembers_its_own(self):
        self.assertEqual(self._pick("search", ["DE", "us", "zz", "us"]), ["de", "us"])
        self._pick("opportunity", ["jp"])
        search = self._root(reverse("aso:dashboard"), "search-countries")
        self.assertIn('data-memory-key="search"', search)
        self.assertIn('data-remembered="[&quot;de&quot;, &quot;us&quot;]"', search)
        scan = self._root(reverse("aso:opportunity"), "scan-countries")
        self.assertIn('data-memory-key="opportunity"', scan)
        self.assertIn('data-remembered="[&quot;jp&quot;]"', scan)

    def test_nothing_remembered_is_an_empty_list(self):
        root = self._root(reverse("aso:dashboard"), "search-countries", Client())
        self.assertIn('data-remembered="[]"', root)

    def test_a_cleared_picker_is_remembered_empty(self):
        self._pick("search", ["de"])
        self.assertEqual(self._pick("search", []), [])
        self.assertIn('data-remembered="[]"', self._root(reverse("aso:dashboard"), "search-countries"))

    def test_every_remembering_picker_has_a_known_key(self):
        """A picker include with a memory_key the server does not know would
        send saves the endpoint refuses."""
        from aso import ui_memory

        keys = []
        for top in ("aso", "aso_pro", "_public_overrides"):
            for root, _dirs, files in os.walk(os.path.join(settings.BASE_DIR, top)):
                for name in (n for n in files if n.endswith(".html")):
                    with open(os.path.join(root, name)) as f:
                        found = re.findall(r'country_picker\.html" with [^%]*memory_key="([^"]*)"', f.read())
                    keys += [(name, key) for key in found]
        self.assertTrue(keys, "the scan found no remembering picker")
        for name, key in keys:
            with self.subTest(template=name, key=key):
                self.assertIn(key, ui_memory.COUNTRY_PICKERS)


class MemoryEndpointTest(TestCase):
    url = "/ui-memory/"

    def _post(self, body, raw=False):
        return self.client.post(self.url, body if raw else json.dumps(body), content_type="application/json")

    def test_url_name(self):
        self.assertEqual(reverse("aso:ui_memory"), self.url)

    def test_bad_input(self):
        for body in ("not json", "[1, 2]"):
            with self.subTest(body=body):
                response = self._post(body, raw=True)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json())
        for body in (
            {"name": "theme", "value": "dark"},
            {"name": "app_summary_folded", "value": "yes"},
            {"name": "countries.search", "value": "us"},
            {"name": "countries.search", "value": ["us", 5]},
            {"name": "countries.rivals", "value": ["us"]},
            {"name": ["countries.search"], "value": ["us"]},
        ):
            with self.subTest(body=body):
                response = self._post(body)
                self.assertEqual(response.status_code, 400)
                self.assertIn("error", response.json())
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_countries_are_cleaned(self):
        response = self._post({"name": "countries.search", "value": ["DE", "us", "zz", "us"]})
        self.assertEqual(response.json(), {"ok": True, "value": ["de", "us"]})

    def test_folded_answers_what_it_stored(self):
        self.assertEqual(self._post({"name": "app_summary_folded", "value": True}).json(),
                         {"ok": True, "value": True})


class SharedRulesTest(TestCase):
    def test_filter_keys_match_the_page_script(self):
        with open(os.path.join(settings.BASE_DIR, "aso/templates/aso/dashboard.html")) as f:
            page = f.read()
        lists = re.search(r"const HISTORY_FILTER_LISTS = \[([^\]]*)\]", page).group(1)
        values = re.search(r"const HISTORY_FILTER_VALUES = \[([^\]]*)\]", page).group(1)
        self.assertEqual(tuple(re.findall(r"'([a-z_]+)'", lists)), history_filters.FILTER_LIST_KEYS)
        self.assertEqual(tuple(re.findall(r"'([a-z_]+)'", values)), history_filters.FILTER_VALUE_KEYS)

    def test_session_lasts_a_year(self):
        self.assertEqual(settings.SESSION_COOKIE_AGE, 31_536_000)
        for rel in ("core/settings.py", "_public_overrides/core/settings.py"):
            path = os.path.join(settings.BASE_DIR, rel)
            if os.path.exists(path):   # the public repository has no overrides
                with open(path) as f, self.subTest(settings=rel):
                    self.assertIn("SESSION_COOKIE_AGE = 365 * 24 * 60 * 60", f.read())
