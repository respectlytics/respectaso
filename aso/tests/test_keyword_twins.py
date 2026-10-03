"""The same keyword in the same storefront shows the same search on every row.

A keyword tracked for two apps, or for an app and for none, is one App Store
search. Each row used to be read at its own time, so they disagreed: on
2026-09-30 the owner saw difficulty 52 on "reduce screen time" for Pausely,
refreshed by hand at 10:22, and 53 on the same keyword without an app, read
at 04:50 (reproduced in a browser against a scratch database, then fixed).
One read now writes every twin (aso.keyword_scoring.store_read); each keeps
its own app's rank.

Free-tier test: no aso_pro import.
"""

from datetime import UTC
from unittest import mock

from django.test import TestCase

from aso import keyword_scoring, scheduler
from aso.models import App, Keyword, OpportunityScan, SearchResult
from aso.services import DifficultyCalculator, DownloadEstimator

from .helpers import ranked_search


def fake_competitors(n=10):
    return [
        {"trackId": 1000 + i, "trackName": f"Competitor {i}", "userRatingCount": 1000 * (i + 1),
         "sellerName": f"Seller {i}"}
        for i in range(n)
    ]


def fake_itunes(ranks=None):
    """App Store stand in: one search of 40 apps, each app of ``ranks``
    placed at its rank (inserted in rank order, so an earlier insert never
    moves a later one)."""
    ranks = ranks or {}
    listed = fake_competitors(40)
    for track_id, rank in sorted(ranks.items(), key=lambda item: item[1]):
        listed.insert(rank - 1, {"trackId": track_id, "trackName": f"App {track_id}",
                                 "userRatingCount": 500, "sellerName": "Tracked"})
    service = mock.MagicMock()
    service.search_ranked.return_value = ranked_search(listed)
    service.lookup_by_id.return_value = {"userRatingCount": 120, "averageUserRating": 4.5}
    return service


def old_row(keyword, country="us", **fields):
    values = {"popularity_score": 45, "difficulty_score": 56, "difficulty_breakdown": {},
              "competitors_data": [], "app_rank": None}
    values.update(fields)
    return SearchResult.objects.create(keyword=keyword, country=country, **values)


def latest(keyword, country="us"):
    return SearchResult.objects.filter(keyword=keyword, country=country).order_by("-searched_at", "-id").first()


class TwinsTest(TestCase):
    def setUp(self):
        self.pausely = App.objects.create(name="Pausely: Reduce Screen Time", track_id=6751782511)
        self.other = App.objects.create(name="Screen Coach", track_id=42)
        self.for_pausely = Keyword.objects.create(keyword="reduce screen time", app=self.pausely)
        self.for_none = Keyword.objects.create(keyword="reduce screen time", app=None)
        self.for_other = Keyword.objects.create(keyword="reduce screen time", app=self.other)
        old_row(self.for_pausely, app_rank=14)
        old_row(self.for_none)
        old_row(self.for_other, app_rank=30)

    def refresh(self, keyword, itunes, country="us"):
        return keyword_scoring.score_keyword_pair(
            keyword, country, itunes_service=itunes,
            difficulty_calc=DifficultyCalculator(), download_est=DownloadEstimator(),
        )

    def test_one_read_writes_every_twin_with_its_own_rank(self):
        itunes = fake_itunes(ranks={6751782511: 12, 42: 27})
        row = self.refresh(self.for_pausely, itunes)
        rows = {k: latest(k) for k in (self.for_pausely, self.for_none, self.for_other)}
        self.assertEqual(row.pk, rows[self.for_pausely].pk)
        self.assertEqual(itunes.search_ranked.call_count, 1, "one App Store search serves every twin")
        self.assertEqual({r.difficulty_score for r in rows.values()}, {row.difficulty_score})
        self.assertEqual({r.popularity_score for r in rows.values()}, {row.popularity_score})
        self.assertEqual(len({str(r.competitors_data) for r in rows.values()}), 1)
        self.assertEqual(rows[self.for_pausely].app_rank, 12)
        self.assertEqual(rows[self.for_other].app_rank, 27)
        self.assertIsNone(rows[self.for_none].app_rank)
        self.assertNotEqual(row.difficulty_score, 56)

    def test_a_twin_is_written_only_where_it_is_tracked(self):
        only_gb = Keyword.objects.create(keyword="reduce screen time", app=App.objects.create(name="GB App"))
        old_row(only_gb, country="gb")
        self.refresh(self.for_pausely, fake_itunes())
        self.assertFalse(SearchResult.objects.filter(keyword=only_gb, country="us").exists())
        self.assertEqual(latest(only_gb, "gb").difficulty_score, 56)

    def test_a_twin_in_other_letters_case_is_still_a_twin(self):
        """The MCP server used to keep keywords as typed."""
        typed = Keyword.objects.create(keyword="Reduce Screen Time", app=App.objects.create(name="Typed"))
        old_row(typed)
        row = self.refresh(self.for_none, fake_itunes())
        self.assertEqual(latest(typed).difficulty_score, row.difficulty_score)

    def test_the_row_button_updates_the_twins(self):
        """What the owner did: the refresh button on the Pausely row."""
        from django.urls import reverse

        with mock.patch("aso.views.ITunesSearchService", return_value=fake_itunes({6751782511: 13})):
            response = self.client.post(reverse("aso:keyword_refresh", args=[self.for_pausely.pk]),
                                        {"country": "us"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(latest(self.for_none).difficulty_score, latest(self.for_pausely).difficulty_score)
        self.assertEqual(latest(self.for_pausely).app_rank, 13)

    def test_the_daily_refresh_reads_a_twin_search_once(self):
        pairs = [(self.for_pausely.pk, "us"), (self.for_none.pk, "us"), (self.for_other.pk, "us")]
        itunes = fake_itunes()
        self.addCleanup(lambda: scheduler._update_status(running=False, error=None))
        with mock.patch("aso.services.ITunesSearchService", return_value=itunes), \
             mock.patch("aso.scheduler.time.sleep") as sleep, \
             mock.patch("aso.scheduler.run_queue.kick"), \
             self.assertLogs("aso.scheduler", level="INFO") as logs:
            scheduler._refresh_pairs(pairs, "Auto-refresh")
        self.assertEqual(itunes.search_ranked.call_count, 1)
        sleep.assert_not_called()
        self.assertTrue(any("3 of 3 pairs refreshed" in line for line in logs.output))
        self.assertFalse(scheduler._needs_refresh_today())

    def test_saving_a_scan_gives_twins_the_same_search_and_keeps_their_rank(self):
        from aso import opportunity_scans

        scan = OpportunityScan.objects.create(keyword="reduce screen time", app=self.pausely, countries=["us"])
        scan.results.create(
            country="us", order_index=0, keyword_text="reduce screen time", popularity_score=47, apple_popularity_score=None,
            inferred_genre="", difficulty_score=51, difficulty_breakdown={},
            competitors_data=fake_competitors(3), app_rank=11,
        )
        self.assertEqual(opportunity_scans.save_to_history(scan), 1)
        self.assertEqual(latest(self.for_pausely).difficulty_score, 51)
        self.assertEqual(latest(self.for_pausely).app_rank, 11)
        self.assertEqual(latest(self.for_none).difficulty_score, 51)
        self.assertEqual(latest(self.for_other).difficulty_score, 51)
        self.assertEqual(latest(self.for_other).app_rank, 30, "a save makes no App Store request")


class ReadTimeTest(TestCase):
    """Two rows of the same day can come from different reads, and the day
    alone could not tell them apart: each row also shows, small, the time it
    was read, in the reader's own time zone (the script converts from UTC)."""

    def test_each_row_says_when_it_was_read(self):
        import os
        from datetime import datetime

        from django.conf import settings
        from django.urls import reverse

        row = old_row(Keyword.objects.create(keyword="reduce screen time"))
        read_at = datetime(2026, 9, 30, 2, 50, tzinfo=UTC)
        SearchResult.objects.filter(pk=row.pk).update(searched_at=read_at)
        response = self.client.get(reverse("aso:dashboard"))
        self.assertContains(response, 'data-read-date>Sep 30, 2026</span>')
        self.assertContains(response, 'data-read-time>02:50</span>')
        with open(os.path.join(settings.BASE_DIR, "aso/templates/aso/dashboard.html")) as f:
            page = f.read()
        self.assertIn("TimeAgo.parse(el.getAttribute('datetime'))", page)


class AlignHistoryTest(TestCase):
    """Rows stored before one read wrote every twin still disagreed after the
    fix: "reduce screen time" showed 52 at 10:22 and 53 at 04:50, and the
    trend charts differed day by day. At every start, twin rows of the same
    day now take the newest read of that day, each keeping its own rank."""

    def setUp(self):
        from datetime import datetime

        self.pausely = App.objects.create(name="Pausely: Reduce Screen Time", track_id=6751782511)
        self.for_pausely = Keyword.objects.create(keyword="reduce screen time", app=self.pausely)
        self.for_none = Keyword.objects.create(keyword="reduce screen time", app=None)

        def at(keyword, day, hour, minute, pop, diff, rank=None):
            row = old_row(keyword, popularity_score=pop, difficulty_score=diff, app_rank=rank,
                          competitors_data=[{"trackId": diff}])
            when = datetime(2026, 9, day, hour, minute, tzinfo=UTC)
            SearchResult.objects.filter(pk=row.pk).update(searched_at=when)
            return row.pk

        # The owner's rows, as stored (UTC times).
        self.p29 = at(self.for_pausely, 29, 18, 17, 42, 53, rank=14)
        self.n29 = at(self.for_none, 29, 5, 47, 45, 56)
        self.p30 = at(self.for_pausely, 30, 8, 22, 42, 52, rank=14)
        self.n30 = at(self.for_none, 30, 2, 50, 42, 53)
        self.n28 = at(self.for_none, 28, 14, 48, 45, 56)   # no twin row that day

    def row(self, pk):
        return SearchResult.objects.get(pk=pk)

    def test_each_day_takes_its_newest_read_and_keeps_each_rank(self):
        self.assertEqual(keyword_scoring.align_twin_history(), 2)
        for mine, twin in ((self.p29, self.n29), (self.p30, self.n30)):
            a, b = self.row(mine), self.row(twin)
            self.assertEqual((a.popularity_score, a.difficulty_score), (b.popularity_score, b.difficulty_score))
            self.assertEqual(a.competitors_data, b.competitors_data)
            self.assertEqual(a.searched_at, b.searched_at, "the time shown is the time of the read shown")
            self.assertEqual(a.app_rank, 14)
            self.assertIsNone(b.app_rank)
        self.assertEqual(self.row(self.n30).difficulty_score, 52)
        self.assertEqual(self.row(self.n29).difficulty_score, 53)

    def test_a_day_without_a_twin_row_is_left_alone(self):
        keyword_scoring.align_twin_history()
        self.assertEqual(self.row(self.n28).difficulty_score, 56)

    def test_the_label_follows_the_new_values(self):
        keyword_scoring.align_twin_history()
        row = self.row(self.n30)
        label = row.classification
        row.save()
        self.assertEqual(row.classification, label)

    def test_twice_changes_nothing(self):
        keyword_scoring.align_twin_history()
        self.assertEqual(keyword_scoring.align_twin_history(), 0)

    def test_it_runs_at_every_start(self):
        from aso import popularity

        with mock.patch("aso.keyword_scoring.align_twin_history", return_value=0) as align, \
             mock.patch("aso.popularity.maybe_upgrade_estimator_version"), \
             mock.patch("aso.popularity.maybe_upgrade_difficulty_version"), \
             mock.patch("aso.popularity.maybe_upgrade_classification_version"):
            popularity.upgrade_stored_history()
        align.assert_called_once()
