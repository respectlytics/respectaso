"""The Search History toolbar fits one line from 900 px up.

At the Mac window's minimum width the toolbar's buttons broke their labels
onto two lines ("Refresh / Rankings"). The footer it also pinned is gone
(docs/development/APP_SHELL_PLAN.md: a Mac app has none).
The browser measurement in CLEANUPS_PLAN.md (item 5) is the proof; these
tests pin the causes it removed: a toolbar that gave up width to the title,
labels that were allowed to wrap, and footer chips too wide below 1024 px.
"""

import os

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from aso.models import App, Keyword, SearchResult

# The Pro base template, and the free edition's, which the public
# repository gets as its own aso/templates/aso/base.html.
BASE_TEMPLATES = tuple(
    rel for rel in ("aso/templates/aso/base.html", "_public_overrides/aso/templates/aso/base.html")
    if os.path.exists(os.path.join(settings.BASE_DIR, rel))
)


def _tag(html, marker):
    """The opening tag that contains ``marker``."""
    at = html.index(marker)
    return html[html.rindex("<", 0, at):html.index(">", at) + 1]


class ToolbarTest(TestCase):
    def setUp(self):
        app = App.objects.create(name="Habito")
        keyword = Keyword.objects.create(keyword="habit tracker", app=app)
        for country in ("us", "gb"):
            SearchResult.objects.create(keyword=keyword, country=country, difficulty_score=40, popularity_score=50)
        self.html = self.client.get(reverse("aso:dashboard")).content.decode()

    def test_the_table_actions_live_in_one_menu(self):
        # Reload, Export, Refresh Rankings and Delete All used to fight for
        # the toolbar's width at 900 px; they are items of one menu now
        # (KEYWORDS_PAGE_PLAN.md M1.3).
        more = self.html[self.html.index('id="history-more"'):]
        more = more[:more.index("</div>\n                </div>")]
        for item in ("Update all now", "Export CSV", "Export CSV with the top 5 apps",
                     "Export CSV with the top 10 apps", "How to read this table", "Delete all keywords…"):
            self.assertIn(item, more)
        self.assertNotIn('onclick="refreshHistorySection()"', self.html)

    def test_no_toolbar_label_wraps(self):
        self.assertIn("whitespace-nowrap", _tag(self.html, 'id="history-country-filter"'))
