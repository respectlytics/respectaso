"""The app shell (docs/development/APP_SHELL_PLAN.md, UI_REDESIGN_PLAN.md 3):
four sections in the top bar, The Creator Behind always in it, a Help menu,
a Settings gear, nothing above the bar and no footer, in both editions.
"""

import re

from django.apps import apps as django_apps
from django.test import TestCase
from django.urls import reverse

from aso import links

PRO = django_apps.is_installed("aso_pro")


def _sections(html):
    group = re.search(r"data-top-sections>(.*?)</div>", html, re.DOTALL).group(1)
    return re.findall(r"<a [^>]*>([^<]+)</a>", group)


def _current(html):
    group = re.search(r"data-top-sections>(.*?)</div>", html, re.DOTALL).group(1)
    return re.findall(r'aria-current="page">([^<]+)</a>', group)


class TopBarTest(TestCase):
    def page(self, url):
        return self.client.get(url).content.decode()

    def test_top_bar_has_exactly_the_four_sections(self):
        html = self.page(reverse("aso:dashboard"))
        self.assertEqual(_sections(html), ["Keywords", "Discover", "Metadata", "Rivals"])
        nav = html[html.index("<nav"):html.index("</nav>")]
        for gone in ("Dashboard", ">Apps<", "Opportunity", "Top Terms", "Rival Tracker", "AI Researcher",
                     "AI Competitor", "ASO Simulator", "Methodology"):
            self.assertNotIn(gone, re.sub(r'<div data-menu.*?</div>', "", nav, flags=re.DOTALL), gone)

    def test_the_creator_behind_is_always_in_the_bar(self):
        for url in (reverse("aso:dashboard"), reverse("aso:apps"), reverse("aso:methodology"),
                    reverse("aso:settings_popularity")):
            with self.subTest(page=url):
                nav = self.page(url)
                nav = nav[nav.index("<nav"):nav.index("</nav>")]
                self.assertIn(f'href="{links.YOUTUBE_URL}"', nav)
                self.assertIn("data-youtube-link", nav)
                self.assertIn("The Creator Behind", nav)

    def test_section_lights_for_its_pages(self):
        cases = [(reverse("aso:dashboard"), "Keywords"), (reverse("aso:opportunity"), "Discover")]
        if PRO:
            cases += [(reverse("aso_pro:ai_researcher"), "Discover"), (reverse("aso_pro:top_terms"), "Discover"),
                      (reverse("aso_pro:simulator"), "Metadata"), (reverse("aso_pro:rival_tracker"), "Rivals")]
        else:
            cases += [(reverse("aso:pro_promo_researcher"), "Discover"),
                      (reverse("aso:pro_promo_simulator"), "Metadata"),
                      (reverse("aso:pro_promo_rival_tracker"), "Rivals")]
        for url, section in cases:
            with self.subTest(page=url):
                response = self.client.get(url, follow=True)
                self.assertEqual(_current(response.content.decode()), [section])

    def test_apps_and_help_pages_light_no_section(self):
        for url in (reverse("aso:apps"), reverse("aso:methodology"), reverse("aso:whats_new")):
            with self.subTest(page=url):
                self.assertEqual(_current(self.page(url)), [])

    def test_discover_remembers_the_last_tab(self):
        self.page(reverse("aso:opportunity"))
        html = self.page(reverse("aso:dashboard"))
        group = re.search(r"data-top-sections>(.*?)</div>", html, re.DOTALL).group(1)
        self.assertIn(f'href="{reverse("aso:opportunity")}"', group)

    def test_discover_tabs_on_discover_pages(self):
        html = self.page(reverse("aso:opportunity"))
        # A nav element: switching tabs is navigation, never a jump (aso/back_link.py).
        bar = re.search(r'<nav [^>]*data-section-tabs="discover"[^>]*>(.*?)</nav>', html, re.DOTALL).group(1)
        self.assertEqual(re.findall(r">([^<]+)</a>", bar), ["AI Researcher", "AI Competitor", "Top Terms", "Countries"])
        self.assertIn('aria-current="page">Countries</a>', bar)
        self.assertLess(html.index('data-section-tabs="discover"'), html.index("<h1"))

    def test_nothing_above_the_top_bar(self):
        html = self.page(reverse("aso:dashboard"))
        body = html[html.index("<body"):]
        body = body[body.index(">") + 1:]
        body = re.sub(r"<script\b.*?</script>", "", body, flags=re.DOTALL)
        body = re.sub(r"\{%.*?%\}|<!--.*?-->", "", body, flags=re.DOTALL)
        self.assertTrue(body.lstrip().startswith("<nav"), body.lstrip()[:200])

    def test_no_footer_anywhere(self):
        urls = [reverse(n) for n in ("aso:dashboard", "aso:apps", "aso:opportunity", "aso:methodology",
                                      "aso:whats_new", "aso:settings_popularity")]
        for url in urls:
            with self.subTest(page=url):
                self.assertNotIn("<footer", self.page(url))

    def test_help_menu_rows(self):
        html = self.page(reverse("aso:dashboard"))
        menu = re.search(r'<div data-menu role="menu" class="menu hidden[^"]*">(.*?)</div>\s*</div>', html, re.DOTALL).group(1)
        rows = [re.sub(r"<[^>]+>", "", r).strip() for r in re.findall(r"<a [^>]*>(.*?)</a>", menu, re.DOTALL)]
        self.assertEqual(rows, ["How the scores work", "What's new", "Install and update", "Follow on X",
                                "Source code on GitHub", "Contact us", "Respectlytics: privacy-first app analytics"])
        self.assertIn(f'href="{reverse("aso:methodology")}"', menu)
        self.assertIn(f'href="{links.X_URL}"', menu)
        self.assertIn(f'href="{links.GITHUB_URL}"', menu)
        self.assertIn(f'href="mailto:{links.CONTACT_EMAIL}"', menu)

    def test_the_gear_opens_settings(self):
        html = self.page(reverse("aso:dashboard"))
        gear = re.search(r'<a href="([^"]+)" title="Settings"', html).group(1)
        self.assertEqual(gear, reverse("aso_pro:settings_ai") if PRO else reverse("aso:settings_popularity"))
