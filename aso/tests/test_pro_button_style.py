"""Every way to Pro looks the same, and nothing else looks like it
(docs/development/PRO_AND_CLICKABLE_TEXT_PLAN.md).

The owner did not see the top bar's small purple Get Pro (2026-10-02) and
asked that every button that buys Pro, whatever it says (Get Pro, Get Pro for
Mac, Get RespectASO Pro, Renew Pro), share one distinct look. That look is
.btn-pro, metallic green. These tests render the real pages in each buying
state and check every link to the pricing, Pro or renewal page, every button
with buying words, and the reverse: no btn-pro leads anywhere else. They also
pin the lock and the benefit line on each Pro tab, and the Pro tint on the
buttons that open a Pro feature.
"""

import re
from contextlib import ExitStack
from html.parser import HTMLParser
from types import SimpleNamespace
from unittest import mock

from django.apps import apps as django_apps
from django.test import TestCase, override_settings
from django.urls import reverse

from aso.links import PRICING_URL, PRO_PAGE_URL, RENEW_URL
from aso.models import App, Keyword, SearchResult

PURCHASE_URLS = {PRICING_URL, PRO_PAGE_URL, RENEW_URL}
BUYING_WORDS = re.compile(r"^(Get (RespectASO )?Pro\b|Renew Pro\b|Buy\b|Upgrade\b)")


class _Clickables(HTMLParser):
    """Every link and button on a page: its attributes and its visible text."""

    def __init__(self):
        super().__init__()
        self.items, self._open = [], []

    def handle_starttag(self, tag, attrs):
        if tag in ("a", "button"):
            self._open.append({"tag": tag, "attrs": dict(attrs), "text": ""})

    def handle_data(self, data):
        for item in self._open:
            item["text"] += data

    def handle_endtag(self, tag):
        if tag in ("a", "button") and self._open:
            item = self._open.pop()
            item["text"] = " ".join(item["text"].split())
            item["classes"] = set((item["attrs"].get("class") or "").split())
            self.items.append(item)


def clickables(html):
    parser = _Clickables()
    parser.feed(html)
    return parser.items


def _states():
    """(name, settings, patches): the three ways a reader is without Pro."""
    expired = SimpleNamespace(is_valid=False, is_expired=True, is_refunded=False,
                              is_revoked=False, months=12, days_until_expiry=-3)
    states = [
        ("docker", {"IS_NATIVE_APP": False, "DEBUG_SKIP_LICENSE": False}, []),
        ("mac without a license", {"IS_NATIVE_APP": True, "DEBUG_SKIP_LICENSE": False}, []),
    ]
    if django_apps.is_installed("licensing"):
        states.append(("mac with an ended license", {"IS_NATIVE_APP": True, "DEBUG_SKIP_LICENSE": False},
                       [mock.patch("licensing.decorators.get_license_info", return_value=expired),
                        mock.patch("licensing.middleware.get_license_info", return_value=expired)]))
    return states


def _pages():
    names = ["aso:dashboard", "aso:apps", "aso:opportunity", "aso:settings_popularity", "aso:methodology"]
    if django_apps.is_installed("aso_pro"):
        names += ["aso_pro:ai_researcher", "aso_pro:ai_competitor", "aso_pro:simulator",
                  "aso_pro:top_terms", "aso_pro:rival_tracker", "aso_pro:settings_license"]
    else:
        names += ["aso:pro_promo_researcher", "aso:pro_promo_competitor", "aso:pro_promo_simulator",
                  "aso:pro_promo_top_terms", "aso:pro_promo_rival_tracker"]
    return names


class EveryWayToProIsTheProButtonTest(TestCase):
    def setUp(self):
        self.app = App.objects.create(name="Moonpond: Sleep Sounds")
        SearchResult.objects.create(keyword=Keyword.objects.create(keyword="sleep sounds", app=self.app),
                                    country="us", difficulty_score=40, popularity_score=50, app_rank=12)

    def _render_all(self, state):
        _name, overrides, patches = state
        pages = {}
        with ExitStack() as stack:
            stack.enter_context(override_settings(**overrides))
            for patch in patches:
                stack.enter_context(patch)
            for name in _pages():
                query = {"app": self.app.pk} if name == "aso:dashboard" else {}
                response = self.client.get(reverse(name), query)
                self.assertEqual(response.status_code, 200, name)
                pages[name] = response.content.decode()
        return pages

    def test_every_purchase_link_is_the_pro_button(self):
        for state in _states():
            seen = set()
            for name, html in self._render_all(state).items():
                seen |= {i["attrs"].get("href") for i in clickables(html)} & PURCHASE_URLS
                for item in clickables(html):
                    href = item["attrs"].get("href", "")
                    with self.subTest(state=state[0], page=name, text=item["text"]):
                        if href in PURCHASE_URLS:
                            self.assertIn("btn-pro", item["classes"])
                        if "btn-pro" in item["classes"]:
                            self.assertIn(href, PURCHASE_URLS)
                        if BUYING_WORDS.match(item["text"]):
                            self.assertIn("btn-pro", item["classes"])
            # Not vacuous: each state shows its own way to Pro.
            expected = {"docker": PRO_PAGE_URL, "mac without a license": PRICING_URL,
                        "mac with an ended license": RENEW_URL}[state[0]]
            self.assertIn(expected, seen, state[0])

    def test_the_top_bar_button_is_on_every_page_without_pro(self):
        for state in _states():
            for name, html in self._render_all(state).items():
                with self.subTest(state=state[0], page=name):
                    buttons = [i for i in clickables(html) if "data-pro-button" in i["attrs"]]
                    self.assertEqual(len(buttons), 1)
                    self.assertIn("btn-pro", buttons[0]["classes"])

    @override_settings(IS_NATIVE_APP=True, DEBUG=True, DEBUG_SKIP_LICENSE=True)
    def test_with_pro_there_is_no_pro_button_lock_or_tint(self):
        if not django_apps.is_installed("aso_pro"):
            self.skipTest("the free edition has no license")
        for name in _pages():
            html = self.client.get(reverse(name), {"app": self.app.pk} if name == "aso:dashboard" else {}).content.decode()
            with self.subTest(page=name):
                self.assertNotIn("data-pro-button", html)
                self.assertNotIn("pro-lock", html)
                self.assertNotIn("btn-locked", html)

    def test_one_definition_of_the_look(self):
        from pathlib import Path

        from django.conf import settings

        css = (Path(settings.BASE_DIR) / "static/css/tailwind.source.css").read_text(encoding="utf-8")
        self.assertEqual(css.count(".btn-pro {"), 1)
        self.assertIn("linear-gradient", css[css.index(".btn-pro {"):css.index(".btn-pro::after")])


@override_settings(IS_NATIVE_APP=True, DEBUG_SKIP_LICENSE=False)
class ProTabsSayWhatTheyDoTest(TestCase):
    """Without Pro, each Pro tab carries the lock and, on hover, one line of
    what it does for the reader: the headline of its preview."""

    def _tab(self, html, label):
        for item in clickables(html):
            if item["text"] == label and item["tag"] == "a":
                return item
        self.fail(f"no {label} tab")

    def test_locked_sections_and_tabs(self):
        from aso.pro_preview_words import HEADLINES

        url = reverse("aso_pro:ai_researcher") if django_apps.is_installed("aso_pro") else reverse("aso:pro_promo_researcher")
        html = self.client.get(url).content.decode()
        for label, feature in (("Metadata", "simulator"), ("Rivals", "rival_tracker"),
                               ("AI Researcher", "researcher"), ("AI Competitor", "competitor"),
                               ("Top Terms", "top_terms")):
            with self.subTest(tab=label):
                tab = self._tab(html, label)
                self.assertIn("pro-lock", tab["classes"])
                self.assertEqual(tab["attrs"].get("data-tip"), f"Pro: {HEADLINES[feature]}.")
        for label in ("Keywords", "Discover", "Countries"):
            with self.subTest(free=label):
                tab = self._tab(html, label)
                self.assertNotIn("pro-lock", tab["classes"])
                self.assertNotIn("data-tip", tab["attrs"])

    def test_the_preview_sets_the_benefit_below_the_page_title(self):
        url = reverse("aso_pro:simulator") if django_apps.is_installed("aso_pro") else reverse("aso:pro_promo_simulator")
        html = self.client.get(url).content.decode()
        benefit = re.search(r'<h2 class="([^"]*)" data-preview-benefit>(.*?)</h2>', html)
        self.assertIsNotNone(benefit)
        # A card title (14px), well below the page title (20px): nothing on a
        # page is as big as its title (the owner, 2026-10-02).
        self.assertIn("card-title", benefit.group(1).split())
        self.assertEqual(benefit.group(2), "Know what a new title will bring before you ship it")


@override_settings(IS_NATIVE_APP=True, DEBUG_SKIP_LICENSE=False)
class ProFeatureButtonsStandOutTest(TestCase):
    """The Keywords page's buttons into a Pro feature carry the Pro tint and
    the lock without Pro, and lead to the feature's preview."""

    def test_glance_and_keyword_detail(self):
        app = App.objects.create(name="Moonpond: Sleep Sounds")
        SearchResult.objects.create(keyword=Keyword.objects.create(keyword="sleep sounds", app=app),
                                    country="us", difficulty_score=40, popularity_score=50, app_rank=12)
        html = self.client.get(reverse("aso:dashboard"), {"app": app.pk}).content.decode()
        locked = [i for i in clickables(html) if "btn-locked" in i["classes"]]
        texts = {i["text"] for i in locked}
        self.assertIn("Find related keywords with AI", texts)
        self.assertIn("Follow rivals on this keyword", texts)
        for item in locked:
            with self.subTest(button=item["text"]):
                self.assertIn("pro-lock", item["classes"])
                self.assertNotIn("PRO", item["text"].split())
