"""The Dashboard's multi-select, kept on the server (aso/history_selection.py).

It lived in the browser's sessionStorage with a copy of each keyword's text,
which desktop-compat forbids, and a row deleted elsewhere stayed counted in
the bar. The selection now lives in the Django session and survives paging;
rows that are gone leave it (CLEANUPS_PLAN.md, item 2).
"""

import json
import re

from django.conf import settings
from django.test import Client, TestCase
from django.urls import reverse

from aso.history_selection import parse_pair
from aso.models import Keyword, SearchResult

URL = reverse("aso:history_selection")


def post(client, **change):
    return client.post(URL, json.dumps(change), content_type="application/json")


class HistorySelectionTest(TestCase):
    def setUp(self):
        self.k1 = Keyword.objects.create(keyword="habit tracker")
        self.k2 = Keyword.objects.create(keyword="screen time")
        for keyword, country in ((self.k1, "us"), (self.k1, "gb"), (self.k2, "us")):
            SearchResult.objects.create(keyword=keyword, country=country, difficulty_score=30)

    def pairs(self, client=None):
        return [e["pair"] for e in (client or self.client).get(URL).json()["entries"]]

    def test_select_and_read_back_in_order_with_the_database_text(self):
        answer = post(self.client, add=[f"{self.k2.pk}:us", f"{self.k1.pk}:gb"]).json()
        self.assertEqual(answer["entries"], [
            {"pair": f"{self.k2.pk}:us", "kw": "screen time"},
            {"pair": f"{self.k1.pk}:gb", "kw": "habit tracker"},
        ])
        self.assertEqual(answer["seq"], 1)
        post(self.client, add=[f"{self.k2.pk}:us"])
        self.assertEqual(self.pairs(), [f"{self.k2.pk}:us", f"{self.k1.pk}:gb"])

    def test_remove_and_clear(self):
        post(self.client, add=[f"{self.k1.pk}:us", f"{self.k2.pk}:us"])
        self.assertEqual(post(self.client, remove=[f"{self.k1.pk}:us"]).json()["entries"][0]["pair"], f"{self.k2.pk}:us")
        answer = post(self.client, clear=True).json()
        self.assertEqual(answer, {"seq": 3, "entries": []})

    def test_deleted_rows_leave_the_selection(self):
        post(self.client, add=[f"{self.k1.pk}:us", f"{self.k1.pk}:gb", f"{self.k2.pk}:us"])
        self.client.post(reverse("aso:results_bulk_delete"),
                         json.dumps({"entries": [{"keyword_id": self.k1.pk, "country": "gb"}]}),
                         content_type="application/json")
        row = SearchResult.objects.get(keyword=self.k2, country="us")
        self.client.post(reverse("aso:result_delete", args=[row.pk]))
        self.assertEqual(self.pairs(), [f"{self.k1.pk}:us"])

    def test_bad_input(self):
        self.assertEqual(self.client.post(URL, "not json", content_type="application/json").status_code, 400)
        self.assertEqual(post(self.client, add="12:us").status_code, 400)
        self.assertEqual(post(self.client, add=[12]).status_code, 400)
        answer = post(self.client, add=["abc", "0:us", f"{self.k1.pk}:zz", "5", f"{self.k1.pk}:US"]).json()
        self.assertEqual([e["pair"] for e in answer["entries"]], [f"{self.k1.pk}:us"])
        for text in ("abc", "0:us", "5:zz", "5", "1:2:us", None, 7):
            self.assertIsNone(parse_pair(text), text)

    def test_two_sessions_do_not_share(self):
        other = Client()
        post(self.client, add=[f"{self.k1.pk}:us"])
        post(other, add=[f"{self.k2.pk}:us"])
        self.assertEqual(self.pairs(), [f"{self.k1.pk}:us"])
        self.assertEqual(self.pairs(other), [f"{self.k2.pk}:us"])

    def test_a_visitor_who_selected_nothing_gets_no_session(self):
        self.client.get(reverse("aso:dashboard"))
        self.assertNotIn(settings.SESSION_COOKIE_NAME, self.client.cookies)


class TheSelectionSurvivesPagingTest(TestCase):
    def test_page_two_carries_what_page_one_ticked(self):
        keywords = [Keyword.objects.create(keyword=f"keyword {i:02d}") for i in range(30)]
        for keyword in keywords:
            SearchResult.objects.create(keyword=keyword, country="us", difficulty_score=30)
        chosen = [f"{keywords[0].pk}:us", f"{keywords[1].pk}:us"]
        post(self.client, add=chosen)
        html = self.client.get(reverse("aso:dashboard"), {"page": 2, "per_page": 25}).content.decode()
        self.assertIn("Page 2 of 2", html)
        data = re.search(r'<script id="history-selection-data" type="application/json">(.*?)</script>', html, re.DOTALL)
        self.assertEqual([e["pair"] for e in json.loads(data.group(1))["entries"]], chosen)


# That the page keeps nothing in the browser's storage, the selection
# included, is aso/tests/test_no_browser_storage.py.
