"""Tests for the dashboard history table's per-page selector.

The dashboard paginates the history table with a user-selectable page size
(?per_page=), validated against HISTORY_PER_PAGE_CHOICES with a safe fallback
to the default. Covers both slicing paths (DB-level and the in-memory sort
path) and the batched trend-annotation query that replaced the per-row
lookups.
"""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aso.models import Keyword, SearchResult
from aso.views import HISTORY_PER_PAGE_CHOICES, HISTORY_PER_PAGE_DEFAULT


def create_results(count, country="us"):
    """Create `count` keywords, each with one search result."""
    for i in range(count):
        kw = Keyword.objects.create(keyword=f"keyword {i:03d}")
        SearchResult.objects.create(
            keyword=kw,
            country=country,
            popularity_score=50,
            difficulty_score=40,
        )


class DashboardPaginationTest(TestCase):
    def get_dashboard(self, **params):
        return self.client.get(reverse("aso:dashboard"), params)

    def test_default_page_size(self):
        create_results(30)
        resp = self.get_dashboard()
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["per_page"], HISTORY_PER_PAGE_DEFAULT)
        self.assertEqual(len(resp.context["history_results"]), 25)
        self.assertEqual(resp.context["total_pages"], 2)
        self.assertEqual(resp.context["total_count"], 30)

    def test_larger_page_size_shows_all(self):
        create_results(30)
        resp = self.get_dashboard(per_page="50")
        self.assertEqual(resp.context["per_page"], 50)
        self.assertEqual(len(resp.context["history_results"]), 30)
        self.assertEqual(resp.context["total_pages"], 1)

    def test_invalid_per_page_falls_back_to_default(self):
        create_results(30)
        for bad in ("abc", "0", "-5", "37", "1000", ""):
            resp = self.get_dashboard(per_page=bad)
            self.assertEqual(
                resp.context["per_page"],
                HISTORY_PER_PAGE_DEFAULT,
                f"per_page={bad!r} should fall back to the default",
            )
            self.assertEqual(len(resp.context["history_results"]), 25)

    def test_per_page_choices_in_context(self):
        create_results(1)
        resp = self.get_dashboard()
        self.assertEqual(resp.context["per_page_choices"], HISTORY_PER_PAGE_CHOICES)

    def test_second_page_with_custom_size(self):
        create_results(60)
        resp = self.get_dashboard(per_page="50", page="2")
        self.assertEqual(len(resp.context["history_results"]), 10)
        self.assertEqual(resp.context["page"], 2)
        self.assertEqual(resp.context["total_pages"], 2)

    def test_in_memory_sort_path_respects_per_page(self):
        # The opportunity sort is computed in Python and sliced as a list —
        # the other slicing path from the queryset one.
        create_results(30)
        resp = self.get_dashboard(sort="opportunity", per_page="50")
        self.assertEqual(len(resp.context["history_results"]), 30)
        resp = self.get_dashboard(sort="opportunity")
        self.assertEqual(len(resp.context["history_results"]), 25)

    def test_trend_annotations_survive_batched_lookup(self):
        # Two snapshots for the same keyword+country: the dashboard shows the
        # latest one, annotated with deltas against the older one.
        kw = Keyword.objects.create(keyword="trend keyword")
        old = SearchResult.objects.create(
            keyword=kw,
            country="us",
            popularity_score=40,
            difficulty_score=50,
            app_rank=20,
        )
        SearchResult.objects.filter(pk=old.pk).update(
            searched_at=timezone.now() - timedelta(days=3)
        )
        SearchResult.objects.create(
            keyword=kw,
            country="us",
            popularity_score=55,
            difficulty_score=45,
            app_rank=12,
        )

        resp = self.get_dashboard()
        rows = resp.context["history_results"]
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertTrue(row.has_history)
        self.assertEqual(row.prev_popularity, 40)
        self.assertEqual(row.prev_difficulty, 50)
        self.assertEqual(row.prev_rank, 20)
        self.assertEqual(row.popularity_delta, 15)
        self.assertEqual(row.difficulty_delta, -5)
        self.assertEqual(row.rank_delta, 8)  # rank improved 20 -> 12

    def test_single_snapshot_has_no_trend(self):
        create_results(1)
        resp = self.get_dashboard()
        row = resp.context["history_results"][0]
        self.assertFalse(row.has_history)
        self.assertIsNone(row.popularity_delta)
        self.assertIsNone(row.rank_delta)
