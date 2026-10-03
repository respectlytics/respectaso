"""The Keywords page (docs/development/KEYWORDS_PAGE_PLAN.md,
UI_REDESIGN_PLAN.md 6): the table's columns, one detail per keyword, the
table's menu, the glance card, the first run card, the remembered app and
the Countries prefill."""

import re

from django.apps import apps as django_apps
from django.test import TestCase
from django.urls import reverse

from aso import ui_memory
from aso.models import App, Keyword, SearchResult

PRO = django_apps.is_installed("aso_pro")


def _row(app, keyword, country="us", rank=12):
    return SearchResult.objects.create(
        keyword=Keyword.objects.get_or_create(keyword=keyword, app=app)[0],
        country=country, difficulty_score=40, popularity_score=50, app_rank=rank,
    )


def _headers(html):
    body = html.index('<tbody id="history-body">')
    head = html[html.rindex("<thead>", 0, body):body]
    cells = re.findall(r"<th\b[^>]*>(.*?)</th>", head, re.DOTALL)
    out = []
    for cell in cells:
        # Drop the sub lines under a header (the source, whose score it is).
        text = re.sub(r"<span class=\"block[^>]*>.*?</span>", "", cell, flags=re.DOTALL)
        text = re.sub(r"<(p|span) class=\"[^\"]*(?:normal-case|font-normal)[^\"]*\"[^>]*>.*?</\1>", "", text, flags=re.DOTALL)
        text = re.sub(r"<[^>]+>|\{%.*?%\}|[▲▼]", " ", text)
        out.append(" ".join(text.split()))
    return out


class TableTest(TestCase):
    def setUp(self):
        self.app = App.objects.create(name="Moonpond: Sleep Sounds")
        _row(self.app, "sleep sounds")
        _row(self.app, "white noise")

    def page(self, **query):
        return self.client.get(reverse("aso:dashboard"), query).content.decode()

    def test_the_columns_are_the_plan_and_nothing_else(self):
        headers = _headers(self.page(app=self.app.pk))
        self.assertEqual(headers[1:8], ["Keyword", "Rank", "Popularity", "Difficulty", "Opportunity",
                                        "Downloads at #1", "Insight"])
        self.assertEqual(headers[8], "Row actions")
        for gone in ("Ranking Difficulty", "Country", "Competitors", "Date", "Overall Difficulty", "Actions"):
            self.assertNotIn(gone, headers)

    def test_a_country_column_only_when_rows_span_countries(self):
        _row(self.app, "sleep sounds", country="gb")
        self.assertIn("Country", _headers(self.page(app=self.app.pk)))
        self.assertNotIn("Country", _headers(self.page(app=self.app.pk, country="gb")))

    def test_every_row_has_its_detail(self):
        html = self.page(app=self.app.pk)
        self.assertEqual(html.count('tabindex="0" data-detail-toggle'), 2)
        self.assertEqual(html.count('class="detail-row hidden border-b border-white/10"'), 2)
        detail = html[html.index('class="detail-row hidden border-b border-white/10"'):]
        detail = detail[:detail.index("</tr>")]
        for words in ("Checked", "Trend", "Check other countries", "Find related keywords with AI",
                      "Follow rivals on this keyword"):
            self.assertIn(words, detail)
        self.assertIn(f'{reverse("aso:opportunity")}?keyword=', detail)

    def test_the_menu_holds_the_table_actions(self):
        html = self.page(app=self.app.pk)
        menu = html[html.index('id="history-more"'):]
        menu = menu[:menu.index("</div>\n                </div>")]
        items = [re.sub(r"<[^>]+>", "", m).strip() for m in re.findall(r'role="menuitem"[^>]*>(.*?)</button>', menu)]
        self.assertEqual(items, ["Update all now", "Export CSV", "Export CSV with the top 5 apps",
                                 "Export CSV with the top 10 apps", "How to read this table",
                                 "Delete all keywords…"])
        for gone in ('onclick="refreshHistorySection()"', ">Delete All<", "Refresh Rankings"):
            self.assertNotIn(gone, html)

    def test_how_to_read_holds_the_guide(self):
        html = self.page(app=self.app.pk)
        dialog = html[html.index('id="how-to-read"'):]
        self.assertIn("How to read this table", dialog)
        self.assertIn("EST is RespectASO", dialog)
        self.assertNotIn("Scoring guide: what the numbers mean", html)

    def test_no_result_cards(self):
        html = self.page()
        self.assertNotIn("createResultCard", html)
        self.assertNotIn("Country Opportunity Comparison", html)


class GlanceTest(TestCase):
    def test_three_tiles_in_words(self):
        app = App.objects.create(name="Moonpond: Sleep Sounds")
        _row(app, "sleep sounds", rank=8)
        html = self.client.get(reverse("aso:dashboard"), {"app": app.pk}).content.decode()
        card = html[html.index('id="app-summary-section"'):html.index("data-summary-body")]
        self.assertIn("Moonpond at a glance", card)
        for label in ("From search today", "With every keyword at #1", "Biggest chance"):
            self.assertIn(label, card)
        self.assertNotIn("~", card)
        self.assertNotIn("+~", card)


class FirstRunTest(TestCase):
    def test_nothing_yet_asks_which_app(self):
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertIn("Which app do you want to grow?", html)
        self.assertNotIn('id="setup-checklist"', html)
        self.assertIn('scroll-mt-20 hidden"', html)    # the empty table waits

    def test_an_app_brings_the_checklist_not_the_welcome(self):
        App.objects.create(name="Moonpond")
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertNotIn("Which app do you want to grow?", html)
        self.assertIn('id="setup-checklist"', html)


class RememberedAppTest(TestCase):
    def setUp(self):
        self.a = App.objects.create(name="Moonpond")
        self.b = App.objects.create(name="Tallyo")
        _row(self.a, "sleep sounds")
        _row(self.b, "habit streaks")

    def test_picking_an_app_on_keywords_is_remembered(self):
        self.client.get(reverse("aso:dashboard"), {"app": self.b.pk})
        self.assertEqual(self.client.session.get(ui_memory.CURRENT_APP), self.b.pk)

    def test_all_apps_leaves_the_memory(self):
        self.client.get(reverse("aso:dashboard"), {"app": self.b.pk})
        self.client.get(reverse("aso:dashboard"), {"app": ""})
        self.assertEqual(self.client.session.get(ui_memory.CURRENT_APP), self.b.pk)

    def test_countries_starts_on_the_remembered_app(self):
        self.client.get(reverse("aso:dashboard"), {"app": self.b.pk})
        html = self.client.get(reverse("aso:opportunity")).content.decode()
        self.assertRegex(html, rf'<option value="{self.b.pk}"[^>]* selected>')

    def test_a_deleted_app_is_forgotten(self):
        self.client.get(reverse("aso:dashboard"), {"app": self.b.pk})
        self.b.delete()
        session = self.client.session

        class Req:
            pass
        req = Req()
        req.session = session
        self.assertIsNone(ui_memory.current_app_id(req))

    def test_the_picker_offers_to_manage_apps(self):
        response = self.client.get(reverse("aso:dashboard"), {"app": self.a.pk})
        menu = response.context["app_menu"]
        self.assertEqual(menu[-1], {"value": "__manage__", "label": "Add or remove apps…", "glyph": "manage"})
        self.assertIn(f'id="manage-apps-link" href="{reverse("aso:apps")}"', response.content.decode())

    def test_the_switcher_shows_each_apps_logo(self):
        """The owner, 2026-10-02: the app switcher beside the title shows each
        app with its logo, through the one shared picker."""
        self.a.icon_url = "https://is1-ssl.mzstatic.com/a.png"
        self.a.save()
        response = self.client.get(reverse("aso:dashboard"), {"app": self.a.pk})
        html = response.content.decode()
        self.assertIn('class="cp-root relative" id="global-app"', html)
        self.assertIn('data-items="global-app-items"', html)
        self.assertNotIn('<select id="global-app"', html)
        menu = response.context["app_menu"]
        self.assertEqual(menu[0], {"value": "", "label": "All apps", "glyph": "all", "selected": False})
        chosen = [item for item in menu if item.get("selected")]
        self.assertEqual(chosen, [{"value": str(self.a.pk), "label": self.a.name,
                                   "icon": "https://is1-ssl.mzstatic.com/a.png", "selected": True}])


class CountriesPrefillTest(TestCase):
    def test_a_keyword_fills_the_form(self):
        html = self.client.get(reverse("aso:opportunity"), {"keyword": "sleep sounds"}).content.decode()
        self.assertIn('value="sleep sounds"', html)


class KeywordDetailStandsApartTest(TestCase):
    """The owner, 2026-10-02: an open row's detail had the table's own
    background, so its end was hard to see, and after a search for several
    countries one detail opened by itself while the other rows sat far below."""

    def setUp(self):
        app = App.objects.create(name="Moonpond")
        apps = [{"trackName": f"App {i}", "sellerName": "Seller", "trackViewUrl": "https://apps.apple.com/app/id1",
                 "averageUserRating": 4.0, "userRatingCount": 100 * i} for i in range(1, 26)]
        row = _row(app, "parental control")
        row.competitors_data = apps
        row.save()
        self.html = self.client.get(reverse("aso:dashboard"), {"app": app.pk}).content.decode()

    def test_the_detail_is_its_own_panel_with_a_clear_end(self):
        detail = re.search(r'<tr class="detail-row hidden([^"]*)"[^>]*>\s*(?:{#.*?#}\s*)?<td colspan="\d+" class="([^"]*)"', self.html)
        self.assertIsNotNone(detail)
        self.assertIn("border-b", detail.group(1))
        self.assertIn("bg-slate-900/70", detail.group(2))
        self.assertIn("border-l-2", detail.group(2))
        self.assertIn('onclick="closeDetail(this)">Close</button>', self.html)

    def test_ten_top_apps_and_the_rest_on_request(self):
        self.assertEqual(self.html.count("detail-more-app hidden"), 15)
        self.assertIn(">Show all 25 apps</button>", self.html)

    def test_a_finished_search_opens_no_row(self):
        script = self.html[self.html.index("function showNewRows(job)"):]
        script = script[:script.index("SearchJob.init(")]
        self.assertNotIn("toggleDetail(", script)
        self.assertIn("bg-purple-500/10", script)
        # And the line about the search does not promise an open row: it
        # said "The first one is open below." until 2026-10-02.
        self.assertNotIn("open below", self.html)
        self.assertIn("to the table.`", self.html)


class TheTableFollowsEveryChangeTest(TestCase):
    """The owner, 2026-10-02: the daily update (and anything else that changes
    a tracked keyword) must reach the table without a reload, and a row that
    lands or changes blinks twice so the reader sees it. Proved in a browser
    on 2026-10-02: a keyword added and one read again both appeared within
    three seconds and blinked."""

    def setUp(self):
        App.objects.create(name="Moonpond")
        _row(App.objects.get(), "sleep sounds")
        self.html = self.client.get(reverse("aso:dashboard")).content.decode()

    def test_the_page_follows_the_change_count(self):
        self.assertIn('data-revision="', self.html)
        self.assertIn(reverse("aso:auto_refresh_status"), self.html)
        self.assertIn("setInterval(check, 5000)", self.html)

    def test_a_background_refresh_blinks_what_changed(self):
        refresh = self.html[self.html.index("function refreshHistorySection()"):]
        refresh = refresh[:refresh.index("// Swap the App Summary panel")]
        self.assertIn("historyRowStates()", refresh)
        self.assertIn("blinkChangedRows(before)", refresh)
        self.assertIn("motion-safe:animate-row-blink", refresh)

    def test_the_blink_is_built(self):
        from pathlib import Path

        from django.conf import settings

        css = (Path(settings.BASE_DIR) / "static/css/tailwind.css").read_text(encoding="utf-8")
        self.assertIn("animate-row-blink", css)
        self.assertIn("@keyframes row-blink", css)


class NoAppNoRankTest(TestCase):
    """A rank is an app's place: a keyword tracked for no app shows none,
    even if a stored row carries one (the owner, 2026-10-02)."""

    def test_the_rank_cell_is_empty_without_an_app(self):
        _row(App.objects.create(name="Moonpond"), "sleep sounds", rank=12)   # shows the Rank column
        SearchResult.objects.create(keyword=Keyword.objects.create(keyword="habit tracker"),
                                    country="us", difficulty_score=23, popularity_score=41, app_rank=34)
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertIn(">#12</span>", html)
        self.assertNotIn(">#34</span>", html)


class TheTableFitsTheSmallestWindowTest(TestCase):
    """The Mac app's window is at least 900 px wide (desktop/main.py). There
    the table ran 41 px past its card, every keyword broke onto two lines and
    the hidden copy button dropped to a line of its own, lifting its keyword
    (2026-10-02). Measured in a browser at 900, 1024 and 1280 px after the
    fix: the table fits its card at each."""

    def test_the_columns_give_way_where_they_can(self):
        from pathlib import Path

        from django.conf import settings

        from aso.templatetags.aso_tags import OPPORTUNITY_SUBLINE_CLASS

        self.assertNotIn("whitespace-nowrap", OPPORTUNITY_SUBLINE_CLASS)   # the subline wraps
        page = (Path(settings.BASE_DIR) / "aso/templates/aso/dashboard.html").read_text()
        self.assertIn('<th class="min-w-32 text-left', page)                # a keyword keeps its line
        self.assertIn('class="kw-copy-btn absolute left-full', page)        # the button floats beside it
        self.assertIn("whitespace-nowrap\">Downloads at #1", page)          # "#1" never on a line alone
