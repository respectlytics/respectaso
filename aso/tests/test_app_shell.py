"""The app shell (docs/development/APP_SHELL_PLAN.md, UI_REDESIGN_PLAN.md 3,
DAILY_USE_PLAN.md step 1): five sections in the top bar (Apps, Keywords,
Discover, Store, Rivals), The Creator Behind always in it, a Help menu,
a Settings gear, nothing above the bar and no footer, in both editions.
"""

import re

from django.apps import apps as django_apps
from django.test import TestCase, override_settings
from django.urls import reverse

from aso import links

PRO = django_apps.is_installed("aso_pro")


def _group(html):
    group = re.search(r"data-top-sections>(.*?)</div>", html, re.DOTALL).group(1)
    # The number beside Apps is a span inside its link, not part of its name.
    return re.sub(r"<span\b[^>]*>[^<]*</span>", "", group)


def _sections(html):
    return re.findall(r"<a [^>]*>([^<]+)</a>", _group(html))


def _current(html):
    return re.findall(r'aria-current="page">([^<]+)</a>', _group(html))


class TopBarTest(TestCase):
    def page(self, url):
        return self.client.get(url).content.decode()

    def test_top_bar_has_exactly_the_five_sections(self):
        html = self.page(reverse("aso:dashboard"))
        self.assertEqual(_sections(html), ["Apps", "Keywords", "Discover", "Store", "Rivals"])
        nav = html[html.index("<nav"):html.index("</nav>")]
        for gone in ("Dashboard", "Metadata", "Opportunity", "Top Terms", "Rival Tracker", "AI Researcher",
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
        cases = [(reverse("aso:apps"), "Apps"), (reverse("aso:dashboard"), "Keywords"),
                 (reverse("aso:opportunity"), "Discover")]
        if PRO:
            cases += [(reverse("aso_pro:ai_researcher"), "Discover"), (reverse("aso_pro:top_terms"), "Discover"),
                      (reverse("aso_pro:simulator"), "Store"), (reverse("aso_pro:store_autopilot"), "Store"),
                      (reverse("aso_pro:rival_tracker"), "Rivals")]
        else:
            cases += [(reverse("aso:pro_promo_researcher"), "Discover"),
                      (reverse("aso:pro_promo_simulator"), "Store"), (reverse("aso:pro_promo_store"), "Store"),
                      (reverse("aso:pro_promo_rival_tracker"), "Rivals")]
        for url, section in cases:
            with self.subTest(page=url):
                response = self.client.get(url, follow=True)
                self.assertEqual(_current(response.content.decode()), [section])

    def test_help_pages_light_no_section(self):
        for url in (reverse("aso:methodology"), reverse("aso:whats_new")):
            with self.subTest(page=url):
                self.assertEqual(_current(self.page(url)), [])

    def test_apps_is_home(self):
        self.assertEqual(reverse("aso:apps"), "/")
        self.assertEqual(reverse("aso:dashboard"), "/keywords/")
        self.assertEqual(self.client.get("/apps/").status_code, 404)

    def test_the_logo_opens_apps(self):
        html = self.page(reverse("aso:dashboard"))
        logo = re.search(r'<a href="([^"]+)"[^>]*>\s*<picture', html)
        self.assertEqual(logo.group(1), reverse("aso:apps"))

    def test_no_metadata_section_in_the_top_bar(self):
        for url in (reverse("aso:apps"), reverse("aso:dashboard"), reverse("aso:methodology")):
            with self.subTest(page=url):
                self.assertNotIn("Metadata", _sections(self.page(url)))

    def test_the_count_beside_apps_is_hidden_at_zero(self):
        group = re.search(r"data-top-sections>(.*?)</div>", self.page(reverse("aso:dashboard")), re.DOTALL).group(1)
        count = re.search(r'<span data-todo-count class="([^"]*)"', group)
        self.assertIsNotNone(count)
        self.assertIn("hidden", count.group(1).split())

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
        rows = [re.sub(r"<[^>]+>", " ", r).split() for r in re.findall(r"<a [^>]*>(.*?)</a>", menu, re.DOTALL)]
        rows = [" ".join(words) for words in rows]
        self.assertEqual(rows, ["How the scores work", "What's new", "Install and update", "Follow on X",
                                "Source code on GitHub", "Contact us",
                                "Respectlytics Privacy-first app analytics. See why users convert."])
        self.assertIn(f'href="{reverse("aso:methodology")}"', menu)
        self.assertIn(f'href="{links.X_URL}"', menu)
        self.assertIn(f'href="{links.GITHUB_URL}"', menu)
        self.assertIn(f'href="mailto:{links.CONTACT_EMAIL}"', menu)
        self.assertNotIn("Your routine", menu)   # a Pro page (DAILY_USE_PLAN.md step 9)

    def test_help_menu_opens_your_routine_with_a_license(self):
        if not PRO:
            self.skipTest("Your routine is a page of the Pro build")
        with override_settings(DEBUG_SKIP_LICENSE=True):
            html = self.page(reverse("aso:dashboard"))
        menu = re.search(r'<div data-menu role="menu" class="menu hidden[^"]*">(.*?)</div>\s*</div>', html, re.DOTALL).group(1)
        rows = [re.sub(r"<[^>]+>", "", r).strip() for r in re.findall(r"<a [^>]*>(.*?)</a>", menu, re.DOTALL)]
        self.assertEqual(rows[:2], ["Your routine", "How the scores work"])
        self.assertIn(f'href="{reverse("aso_pro:routine")}"', menu)

    def test_the_gear_opens_settings(self):
        html = self.page(reverse("aso:dashboard"))
        gear = re.search(r'<a href="([^"]+)" title="Settings"', html).group(1)
        self.assertEqual(gear, reverse("aso_pro:settings_ai") if PRO else reverse("aso:settings_popularity"))


class OutsideLinksTest(TestCase):
    """The Help menu's links that leave the app open where they should in the
    Mac app's window too. Its WebKit view hands a target="_blank" link to the
    default browser (pywebview), and cannot open a mailto: link at all: there
    a click went nowhere (Contact us, 2026-11-18), so static/js/mail-links.js,
    on every page, hands mail links to the window's open_external bridge."""

    def menu(self):
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        return html, re.search(r'<div data-menu role="menu" class="menu hidden[^"]*">(.*?)</div>\s*</div>',
                               html, re.DOTALL).group(1)

    def test_every_page_hands_mail_links_to_the_mac_app(self):
        from pathlib import Path

        from django.conf import settings

        html, menu = self.menu()
        self.assertIn(f'href="mailto:{links.CONTACT_EMAIL}"', menu)
        self.assertIn("js/mail-links.js", html)
        script = (Path(settings.BASE_DIR) / "static" / "js" / "mail-links.js").read_text()
        self.assertIn('a[href^="mailto:"]', script)
        self.assertIn("api.open_external(link.getAttribute('href'))", script)
        self.assertIn("event.preventDefault()", script)

    def test_every_link_to_a_website_opens_in_the_browser(self):
        _html, menu = self.menu()
        outside = re.findall(r'<a [^>]*href="https?://[^"]*"[^>]*>', menu)
        self.assertEqual(len(outside), 3)   # X, GitHub, Respectlytics
        for tag in outside:
            with self.subTest(tag=tag):
                self.assertIn('target="_blank"', tag)
        self.assertIn(f'href="{links.RESPECTLYTICS_URL}"', menu)
        self.assertIn("ref=respectaso-app", links.RESPECTLYTICS_URL)

    def test_a_page_outside_the_shell_hands_its_mail_link_over_itself(self):
        """A template drawn without aso/base.html (an error page, the window's
        loading page) has no mail-links.js, so its mail link calls the bridge."""
        from pathlib import Path

        from django.conf import settings

        base = Path(settings.BASE_DIR)
        for path in sorted(base.glob("*/templates/**/*.html")):
            text = path.read_text(encoding="utf-8")
            standalone = "<html" in text or re.search(r'{%\s*extends\s+"[^"]*standalone', text)
            if "mailto:" in text and standalone:
                with self.subTest(template=str(path.relative_to(base))):
                    self.assertIn("open_external", text)
