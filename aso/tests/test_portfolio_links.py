"""Every link the app gives to one of our sites names the app as where the visit came from.

A browser opened by an app sends no Referer, so without ?ref=respectaso-app the
site counts the visit as direct. The tag is the app's one entry in the register
of ref tags (the loheden-legal skill, The ref tags), the same at every site, and
utm_* parameters are never used: they split one source into several rows.
aso/links.py is the one place the addresses live; this reads every rendered
page, the CSV export, and every template, module and script for a branch no
page renders.
"""

import re
from unittest.mock import patch
from urllib.parse import parse_qsl, urlsplit

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from aso import links
from aso.links import REF, is_ours, tagged
from aso.tests.surfaces import pages, relative, sources

URL = re.compile(r"""https?://[^\s"'<>`)\\]+""")
OUR_HOST = "|".join(re.escape(host) for host in links.HOSTS)
# A template or script never writes an address of ours by hand: it reads aso/links.py.
HAND_WRITTEN = re.compile(rf"https?://(?:[a-z0-9-]+\.)*(?:{OUR_HOST})", re.IGNORECASE)
# Outside aso/links.py a module may name our site only as a server it talks to:
# an /api/ address, or the bare host in a User-Agent or HTTP-Referer header.
NOT_A_LINK = re.compile(rf"^https?://(?:www\.)?(?:{OUR_HOST})(?:/api/[^\s\"']*)?/?$", re.IGNORECASE)


def names_the_app(url: str) -> bool:
    query = parse_qsl(urlsplit(url.replace("&amp;", "&")).query)
    return ("ref", REF) in query and not any(key.startswith("utm_") for key, _value in query)


def untagged(text: str) -> list[str]:
    return [url for url in URL.findall(text) if is_ours(url) and not names_the_app(url)]


class TaggedTest(SimpleTestCase):
    def test_a_link_gets_the_one_tag_and_loses_utm(self):
        self.assertEqual(tagged("https://respectaso.com/pricing/"), "https://respectaso.com/pricing/?ref=respectaso-app")
        self.assertEqual(tagged("https://respectlytics.com/?utm_source=respectaso&utm_medium=help_menu&a=1#top"),
                         "https://respectlytics.com/?a=1&ref=respectaso-app#top")
        self.assertEqual(tagged("https://respectaso.com/?ref=app"), "https://respectaso.com/?ref=respectaso-app")

    def test_our_sites_are_known_by_host(self):
        for url in ("https://respectaso.com/", "https://www.respectlytics.com/", "https://prokoll.com/en/",
                    "https://wiseprediction.com/", "https://sharepredictions.com/"):
            with self.subTest(url=url):
                self.assertTrue(is_ours(url))
        for url in ("https://github.com/respectlytics/respectaso", "https://notrespectaso.com/",
                    "mailto:respectaso@loheden.com", "/pricing/"):
            with self.subTest(url=url):
                self.assertFalse(is_ours(url))

    def test_every_address_in_the_links_module_names_the_app(self):
        found = [name for name, value in vars(links).items()
                 if name.endswith("_URL") and is_ours(value) and not names_the_app(value)]
        self.assertEqual(found, [])

    def test_the_guard_catches_what_it_guards(self):
        self.assertEqual(untagged('<a href="https://respectlytics.com/?utm_source=respectaso">'),
                         ["https://respectlytics.com/?utm_source=respectaso"])
        self.assertEqual(untagged('<a href="https://respectaso.com/pricing/?ref=app">'), ["https://respectaso.com/pricing/?ref=app"])
        self.assertEqual(untagged(f'<a href="{links.PRICING_URL}">'), [])


class RenderedPagesTest(TestCase):
    def setUp(self):
        from aso.models import App, Keyword, SearchResult

        self.app = App.objects.create(name="Pausely: Reduce Screen Time", track_id=6751782511)
        keyword = Keyword.objects.create(keyword="screen time", app=self.app)
        SearchResult.objects.create(keyword=keyword, country="us", popularity_score=45, difficulty_score=40,
                                    difficulty_breakdown={}, competitors_data=[], app_rank=12)

    @patch("aso.update_check._fetch_latest_release", return_value={"update_available": False})
    def _scan(self, _github):
        found, seen = {}, 0
        for url in [*pages(), f"/?app={self.app.pk}", f"/?app={self.app.pk}&country=us"]:
            response = self.client.get(url)
            if response.status_code != 200 or "text/html" not in response.get("Content-Type", ""):
                continue
            markup = response.content.decode()
            seen += sum(1 for link in URL.findall(markup) if is_ours(link))
            if bad := untagged(markup):
                found[url] = bad
        self.assertGreater(seen, 0, "the Help menu links Respectlytics on every page, so a scan that finds none reads nothing")
        return found

    def test_every_page_names_the_app_in_its_links_to_our_sites(self):
        self.assertEqual(self._scan(), {})

    @override_settings(DEBUG_SKIP_LICENSE=True)
    def test_every_page_names_the_app_with_a_licence(self):
        self.assertEqual(self._scan(), {})

    def test_the_csv_export_names_the_app(self):
        export = self.client.get(reverse("aso:export_history_csv")).content.decode()
        self.assertIn(links.RESPECTLYTICS_URL, export)
        self.assertEqual(untagged(export), [])


class SourcesTest(SimpleTestCase):
    def test_no_template_or_script_writes_an_address_of_ours_by_hand(self):
        found = [f"{relative(path)}: {match}" for suffix in (".html", ".js")
                 for path in sources(suffix) if "/static/css/" not in path.as_posix()
                 for match in HAND_WRITTEN.findall(re.sub(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}|{#.*?#}|<!--.*?-->",
                                                          "", path.read_text(encoding="utf-8"), flags=re.DOTALL))]
        self.assertEqual(found, [])

    def test_no_module_but_the_links_module_writes_a_link_to_our_sites(self):
        found = []
        for path in sources(".py"):
            if relative(path) == "aso/links.py":
                continue
            for url in URL.findall(path.read_text(encoding="utf-8")):
                if is_ours(url) and not NOT_A_LINK.match(url.rstrip(".,;")):
                    found.append(f"{relative(path)}: {url}")
        self.assertEqual(found, [])
