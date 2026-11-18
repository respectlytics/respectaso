"""One rule for a rank arrow (DAILY_USE_PLAN.md step 10.1): a rank's move
compares with the newest earlier read at most MAX_GAP_DAYS older, on
Keywords, in its summary and in the Apps page's Moved counts, as the Rival
Tracker always did. With nothing that recent there is no arrow."""

from datetime import timedelta

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aso import rank_change
from aso.dashboard_summary import _net_movement
from aso.models import App, Keyword, SearchResult

ARROW = 'title="Change since the check before"'


class RankArrowTest(TestCase):
    def setUp(self):
        self.app = App.objects.create(name="Moonpond: Sleep Sounds")
        self.keyword = Keyword.objects.create(keyword="sleep sounds", app=self.app)
        rank_change.reset()
        self.addCleanup(rank_change.reset)

    def read(self, days_ago, rank):
        row = SearchResult.objects.create(keyword=self.keyword, country="us", difficulty_score=40,
                                          popularity_score=50, app_rank=rank)
        SearchResult.objects.filter(pk=row.pk).update(searched_at=timezone.now() - timedelta(days=days_ago))
        row.refresh_from_db()
        return row

    def rank_cell(self):
        html = self.client.get(reverse("aso:dashboard"), {"app": self.app.pk}).content.decode()
        start = html.index('<span class="text-white">#8</span>')
        return html[start:start + 200]

    def test_a_read_ten_days_old_gives_no_arrow(self):
        self.read(10, 12)
        newest = self.read(0, 8)
        self.assertNotIn(ARROW, self.rank_cell())
        brief = rank_change.brief(self.app)
        self.assertEqual((brief["up"], brief["down"]), (0, 0))
        self.assertEqual(_net_movement([newest])["up"], 0)

    def test_yesterdays_read_gives_an_arrow(self):
        self.read(1, 12)
        newest = self.read(0, 8)
        cell = self.rank_cell()
        self.assertIn(ARROW, cell)
        self.assertIn("(↑4)", cell)
        self.assertEqual(rank_change.brief(self.app)["up"], 1)
        self.assertEqual(_net_movement([newest])["up"], 1)

    def test_the_newest_recent_read_counts_not_an_older_one(self):
        self.read(10, 30)
        self.read(2, 9)
        self.read(0, 8)
        self.assertIn("(↑1)", self.rank_cell())
