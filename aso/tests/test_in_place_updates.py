"""Filtering, sorting, paging and row actions update the page in place.

They used to set window.location or submit a form, which reloads the page
and throws the reader back to the top (static/js/in-place.js has the
behaviour; these tests pin the pieces it depends on). The browser side was
driven end to end with Playwright; see docs/development/IN_PLACE_UPDATES_PLAN.md.

Free-tier test: no aso_pro import. Top Search Terms is covered in
aso_pro/tests/test_top_terms.py.
"""

import os
import re

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from aso.models import App, Keyword, SearchResult

# The templates whose lists must never reload the page to filter, sort, page
# or act on a row. A navigation there would bring the jump to the top back.
IN_PLACE_TEMPLATES = (
    "aso/templates/aso/dashboard.html",
    "aso/templates/aso/_app_summary.html",
    "aso/templates/aso/apps.html",
    "aso_pro/templates/aso_pro/top_terms.html",
)
RELOADS = (
    r"location\.reload\(",
    r"window\.location\.href\s*=",
    r"location\.replace\(",
    r"location\.assign\(",
    r"\.form\.submit\(\)",
    r"\bform\.submit\(\)",
)


def _read(rel):
    with open(os.path.join(settings.BASE_DIR, rel)) as f:
        return f.read()


class NoListActionReloadsThePageTest(TestCase):
    def test_no_template_reloads_the_page(self):
        for rel in IN_PLACE_TEMPLATES:
            path = os.path.join(settings.BASE_DIR, rel)
            if not os.path.exists(path):   # the public build has no aso_pro
                continue
            text = _read(rel)
            for pattern in RELOADS:
                with self.subTest(template=rel, pattern=pattern):
                    self.assertIsNone(re.search(pattern, text), f"{rel} still reloads: {pattern}")

    def test_every_page_loads_the_in_place_helper(self):
        for rel in ("aso/templates/aso/base.html", "_public_overrides/aso/templates/aso/base.html"):
            path = os.path.join(settings.BASE_DIR, rel)
            if os.path.exists(path):
                with self.subTest(template=rel):
                    self.assertIn("js/in-place.js", _read(rel))


class DashboardHistoryTest(TestCase):
    def _rows(self, n, country="us", app=None):
        rows = []
        for i in range(n):
            keyword = Keyword.objects.create(keyword=f"kw {i:03d} {country}", app=app)
            rows.append(SearchResult.objects.create(
                keyword=keyword, country=country, popularity_score=40, difficulty_score=30,
                difficulty_breakdown={}, competitors_data=[],
            ))
        return rows

    def test_the_table_carries_its_own_sort_and_app(self):
        """The sort and the app are read from the table on screen, which an
        in-place update replaces; they used to be constants frozen at load."""
        app = App.objects.create(name="Calm Minutes")
        self._rows(1, app=app)
        response = self.client.get(reverse("aso:dashboard"), {"app": app.pk, "sort": "popularity", "dir": "asc"})
        self.assertContains(response, 'data-sort="popularity"')
        self.assertContains(response, 'data-dir="asc"')
        self.assertContains(response, f'data-app="{app.pk}"')

    def test_pages_load_in_place(self):
        self._rows(30)
        response = self.client.get(reverse("aso:dashboard"))
        self.assertContains(response, 'data-in-place="page"')

    def test_a_row_refreshes_in_its_own_storefront(self):
        """Refreshing a France row used to research the keyword in the first
        country of the search form's picker, the United States."""
        row = self._rows(1, country="fr")[0]
        response = self.client.get(reverse("aso:dashboard"))
        self.assertContains(response, f"refreshKeyword({row.keyword_id}, 'fr', this)")

    def test_the_summary_country_filter_lives_in_the_dashboard_script(self):
        """The App Summary arrives by in-place update when an app is picked,
        and a script inside swapped-in HTML never runs."""
        self.assertNotIn("<script", _read("aso/templates/aso/_app_summary.html"))
        self.assertIn("function filterHistoryToCountry(", _read("aso/templates/aso/dashboard.html"))


class AppsPageTest(TestCase):
    def test_the_sections_an_action_swaps_are_marked(self):
        App.objects.create(name="Calm Minutes", track_id=123)
        response = self.client.get(reverse("aso:apps"))
        for marker in ('id="apps-add"', 'id="apps-manual-form"', 'id="apps-list"',
                       'data-app-id=', 'class="app-refresh-form"', 'app-delete-form'):
            with self.subTest(marker=marker):
                self.assertContains(response, marker)

    def test_adding_answers_with_the_message_and_the_list(self):
        response = self.client.post(
            reverse("aso:apps"), {"name": "Zeta", "bundle_id": ""},
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )
        self.assertContains(response, 'id="apps-message"')
        self.assertContains(response, "Zeta")
