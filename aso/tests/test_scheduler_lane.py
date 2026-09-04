"""The ranking refresh and the run lane share Apple's request budget: neither
starts while the other runs, and a finished refresh starts what waited."""

from unittest import mock

from django.test import TestCase

from aso import run_queue, scheduler
from aso.models import Keyword, KeywordSearchJob, SearchResult
from aso.tests.helpers import _SyncThread


class SchedulerLaneTest(TestCase):
    def setUp(self):
        self.addCleanup(run_queue._active.clear)
        self.addCleanup(lambda: scheduler._update_status(running=False))

    def test_the_daily_tick_is_postponed_while_the_lane_is_busy(self):
        KeywordSearchJob.objects.create(keywords=["a"], countries=["us"], status="running")
        with mock.patch("aso.scheduler._needs_refresh_today", return_value=True), \
             mock.patch("aso.scheduler._run_daily_refresh") as refresh, \
             mock.patch("aso.apple_ads.sync.maybe_run_sync"), \
             self.assertLogs("aso.scheduler", level="INFO") as logs:
            scheduler._tick()
        refresh.assert_not_called()
        self.assertTrue(any("postponed" in line for line in logs.output))

    def test_the_daily_tick_runs_when_the_lane_is_idle(self):
        with mock.patch("aso.scheduler._needs_refresh_today", return_value=True), \
             mock.patch("aso.scheduler._run_daily_refresh") as refresh, \
             mock.patch("aso.apple_ads.sync.maybe_run_sync"):
            scheduler._tick()
        refresh.assert_called_once()

    def test_a_manual_refresh_is_refused_while_the_lane_is_busy(self):
        KeywordSearchJob.objects.create(keywords=["a"], countries=["us"], status="running")
        self.assertFalse(scheduler.run_manual_refresh([(1, "us")]))

    def test_a_finished_refresh_kicks_the_lane(self):
        keyword = Keyword.objects.create(keyword="a")
        SearchResult.objects.create(keyword=keyword, country="us", difficulty_score=10)
        with mock.patch("aso.scheduler.threading.Thread", _SyncThread), \
             mock.patch("aso.scheduler._refresh_pair") as pair, \
             mock.patch("aso.scheduler.run_queue.kick") as kick:
            self.assertTrue(scheduler.run_manual_refresh([(keyword.pk, "us")]))
        pair.assert_called_once()
        kick.assert_called_once()
        self.assertFalse(scheduler.get_status()["running"])
