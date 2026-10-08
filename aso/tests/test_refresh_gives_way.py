"""The ranking refresh gives way to whatever the user starts, and to the
Rival Tracker.

With hundreds or thousands of tracked keywords, the daily refresh (and a
Refresh Rankings press, and the Rival Tracker's check) used to hold Apple's
request budget until the last keyword: a keyword search, a country scan or
an AI run started meanwhile waited behind it. Now the run starts at once,
the refresh pauses before its next keyword while the run queue has a run
going or waiting, and carries on from the same keyword once it has none,
as often as the user starts something (aso/scheduler.py give_way).

The Rival Tracker's check goes ahead of a refresh of tracked keywords the
same way (going_ahead): few keywords, and they are what its page shows.
"""

import threading
from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aso import run_queue, scheduler, search_jobs
from aso.models import Keyword, KeywordSearchJob, SearchResult

PAIRS = 3


class TheRefreshGivesWayTest(TestCase):
    def setUp(self):
        run_queue._claimed.clear()
        self.addCleanup(run_queue._claimed.clear)
        self.addCleanup(run_queue._active.clear)
        self.addCleanup(lambda: scheduler._update_status(running=False, paused=False, error=None))
        yesterday = timezone.now() - timedelta(days=1, hours=1)
        for n in range(PAIRS):
            keyword = Keyword.objects.create(keyword=f"sleep sounds {n}")
            row = SearchResult.objects.create(keyword=keyword, country="us", difficulty_score=50)
            SearchResult.objects.filter(pk=row.pk).update(searched_at=yesterday)
        self.pairs = scheduler._get_pairs_to_refresh()
        self.events = []
        self.searches = 0

    def start_search(self):
        """What the user does: start a keyword search, which kicks the lane."""
        self.searches += 1
        job = search_jobs.create_job(None, ["us"], [f"zeta {self.searches}"])
        job.refresh_from_db()
        # The ranking refresh holds no busy probe: the search starts at once.
        self.events.append(f"search {self.searches} {job.status}")

    def the_search_finishes(self, _seconds):
        """Each poll of a paused refresh: the running search ends."""
        status = scheduler.get_status()
        self.events.append(f"paused at {status['completed']}" if status["paused"] else "not paused")
        KeywordSearchJob.objects.filter(status="running").update(status="completed")

    def read(self, keyword_obj, country, *, force=False):
        self.events.append(f"read {keyword_obj.keyword}")
        return mock.Mock()

    def refresh(self, *, wait=None):
        with mock.patch("aso.run_queue.threading.Thread"), \
                mock.patch("aso.scheduler._refresh_pair", side_effect=self.read), \
                mock.patch("aso.throttle.AdaptiveITunesRateLimiter.wait", side_effect=wait), \
                mock.patch("aso.scheduler.time.sleep", side_effect=self.the_search_finishes), \
                self.assertLogs("aso.scheduler", level="INFO") as logs:
            refreshed = scheduler._refresh_pairs(self.pairs, "Auto-refresh")
        return refreshed, "\n".join(logs.output)

    def test_a_search_started_during_the_refresh_goes_first_and_the_refresh_carries_on(self):
        read = self.read

        def read_then_start_a_search(keyword_obj, country, *, force=False):
            result = read(keyword_obj, country, force=force)
            if keyword_obj.keyword != "sleep sounds 2":
                self.start_search()      # after the first and the second keyword
            return result

        self.read = read_then_start_a_search
        refreshed, log = self.refresh()
        self.assertEqual(refreshed, PAIRS)
        self.assertEqual(self.events, [
            "read sleep sounds 0",
            "search 1 running",
            "paused at 1",
            "read sleep sounds 1",
            "search 2 running",
            "paused at 2",
            "read sleep sounds 2",
        ])
        self.assertIn("Ranking refresh paused", log)
        self.assertIn("Ranking refresh carries on", log)
        status = scheduler.get_status()
        self.assertFalse(status["running"])
        self.assertFalse(status["paused"])
        self.assertIsNotNone(status["last_completed_at"])

    def test_a_search_started_during_the_pace_wait_goes_before_the_next_read(self):
        def wait():
            if not self.searches:
                self.start_search()

        self.refresh(wait=wait)
        self.assertEqual(self.events, [
            "read sleep sounds 0",
            "search 1 running",
            "paused at 1",
            "read sleep sounds 1",
            "read sleep sounds 2",
        ])

    def test_a_search_left_waiting_is_started_by_the_paused_refresh(self):
        """A run that waited for another holder of the lane (the Rival
        Tracker's own steps) must never leave the refresh waiting for it."""
        def queue_without_a_kick():
            KeywordSearchJob.objects.create(keywords=["zeta"], countries=["us"])

        with mock.patch("aso.run_queue.threading.Thread"), \
                mock.patch("aso.scheduler.time.sleep", side_effect=self.the_search_finishes):
            queue_without_a_kick()
            self.assertTrue(scheduler.give_way(done=0))
        self.assertEqual(KeywordSearchJob.objects.get().status, "completed")

    def test_a_run_the_mcp_server_left_running_never_holds_the_refresh(self):
        """Claude Desktop closed mid-run: the MCP server's row stays "running"
        until RespectASO restarts. The refresh carries on next to it, as it
        always did next to MCP runs, and never waits for the runs queued
        behind it."""
        KeywordSearchJob.objects.create(keywords=["zeta"], countries=["us"], status="running")   # not claimed here
        KeywordSearchJob.objects.create(keywords=["queued"], countries=["us"])
        refreshed, _log = self.refresh()
        self.assertEqual(refreshed, PAIRS)
        self.assertNotIn("paused at", " ".join(self.events))

    def test_nothing_to_give_way_to_costs_nothing(self):
        with mock.patch("aso.scheduler.time.sleep") as sleep:
            self.assertFalse(scheduler.give_way(done=0))
        sleep.assert_not_called()
        self.assertFalse(scheduler.get_status()["paused"])


class APausedRefreshSaysSoTest(TestCase):
    def setUp(self):
        self.addCleanup(lambda: scheduler._update_status(running=False, paused=False))
        self.assertTrue(scheduler._claim_refresh(10))
        scheduler._update_status(paused=True, completed=3)

    def test_the_menu_bar(self):
        self.assertEqual(scheduler.menu_status(),
                         ("Updating tracked keywords: paused at 3 of 10", False, "Update All Tracked Keywords"))

    def test_the_activity_panel_reads_it(self):
        data = self.client.get(reverse("aso:auto_refresh_status")).json()
        self.assertTrue(data["running"])
        self.assertTrue(data["paused"])

    def test_a_new_claim_waits_for_it(self):
        """Paused, it still holds its place: one refresh at a time."""
        self.assertFalse(scheduler._claim_refresh(5))
        self.assertEqual(scheduler.start_bulk_refresh([(1, "us")]),
                         ("refreshing", "A refresh is already in progress."))


class TheRivalTrackerGoesFirstTest(TestCase):
    def setUp(self):
        self.addCleanup(run_queue._active.clear)
        self.addCleanup(scheduler._set_aside.clear)
        self.addCleanup(lambda: setattr(scheduler, "_ahead_thread", None))
        self.addCleanup(lambda: scheduler._update_status(running=False, paused=False, error=None))
        yesterday = timezone.now() - timedelta(days=1, hours=1)
        for n in range(PAIRS):
            keyword = Keyword.objects.create(keyword=f"sleep sounds {n}")
            row = SearchResult.objects.create(keyword=keyword, country="us", difficulty_score=50)
            SearchResult.objects.filter(pk=row.pk).update(searched_at=yesterday)
        self.pairs = scheduler._get_pairs_to_refresh()
        self.events = []

    def read(self, keyword_obj, country, *, force=False):
        self.events.append(f"read {keyword_obj.keyword}")
        if keyword_obj.keyword == "sleep sounds 0":
            scheduler._ahead_thread = -1     # the Rival Tracker's check, in its own thread, asks to go ahead
        return mock.Mock()

    def the_pass_runs(self, _seconds):
        """What the pass's thread does while the refresh waits: take the
        flag from the paused refresh, read, and give the flag back."""
        status = scheduler.get_status()
        self.events.append(f"paused at {status['completed']} of {status['total']}")
        scheduler._ahead_thread = threading.get_ident()
        self.assertTrue(scheduler._claim_refresh(2))
        self.events.append(f"rival check reads {scheduler.get_status()['total']}")
        scheduler._refresh_finished(completed=2)
        status = scheduler.get_status()
        self.events.append(f"given back: {status['completed']} of {status['total']}, paused={status['paused']}")
        scheduler._ahead_thread = None

    def test_a_refresh_pauses_for_the_rival_tracker_and_carries_on(self):
        with mock.patch("aso.run_queue.threading.Thread"), \
                mock.patch("aso.scheduler._refresh_pair", side_effect=self.read), \
                mock.patch("aso.throttle.AdaptiveITunesRateLimiter.wait"), \
                mock.patch("aso.scheduler.time.sleep", side_effect=self.the_pass_runs):
            self.assertEqual(scheduler._refresh_pairs(self.pairs, "Auto-refresh"), PAIRS)
        self.assertEqual(self.events, [
            "read sleep sounds 0",
            "paused at 1 of 3",
            "rival check reads 2",
            "given back: 1 of 3, paused=True",
            "read sleep sounds 1",
            "read sleep sounds 2",
        ])
        self.assertFalse(scheduler.get_status()["running"])

    def test_a_refresh_still_reading_is_not_taken(self):
        """The pass waits until the refresh pauses at its next keyword."""
        self.assertTrue(scheduler._claim_refresh(10))
        with scheduler.going_ahead():
            self.assertFalse(scheduler._claim_refresh(2))
            scheduler._update_status(paused=True)
            self.assertTrue(scheduler._claim_refresh(2))
            scheduler._refresh_finished()
        self.assertEqual(scheduler.get_status()["total"], 10)

    def test_only_the_pass_going_ahead_takes_the_flag(self):
        self.assertTrue(scheduler._claim_refresh(10))
        scheduler._update_status(paused=True)
        self.assertFalse(scheduler._claim_refresh(2))

    def test_the_pass_going_ahead_never_waits_for_itself(self):
        with scheduler.going_ahead(), mock.patch("aso.scheduler.time.sleep") as sleep:
            self.assertFalse(scheduler.give_way(done=0))
        sleep.assert_not_called()

    def test_a_set_aside_refresh_that_fails_leaves_the_flag_with_the_pass(self):
        self.assertTrue(scheduler._claim_refresh(10))
        scheduler._update_status(paused=True)
        with scheduler.going_ahead():
            self.assertTrue(scheduler._claim_refresh(2))
        scheduler._ahead_thread = -1     # the pass still holds the flag, in its own thread
        scheduler._refresh_finished(error="database is locked")      # the refresh set aside ends
        status = scheduler.get_status()
        self.assertEqual((status["running"], status["total"], status["error"]), (True, 2, None))
        self.assertEqual(scheduler._set_aside, [])

    def test_a_first_check_reads_through_the_queue(self):
        """gives_way=False: the first check of a new Rival Tracker holds its
        place; a search started meanwhile waits for it."""
        KeywordSearchJob.objects.create(keywords=["zeta"], countries=["us"], status="running")
        with mock.patch("aso.scheduler._refresh_pair", side_effect=lambda *a, **kw: mock.Mock()) as read, \
                mock.patch("aso.throttle.AdaptiveITunesRateLimiter.wait"), \
                mock.patch("aso.scheduler.run_queue.kick"), \
                mock.patch("aso.scheduler.time.sleep") as sleep, \
                scheduler.going_ahead():
            self.assertEqual(scheduler.refresh_pairs_now(self.pairs, "Rival Tracker", gives_way=False), PAIRS)
        self.assertEqual(read.call_count, PAIRS)
        sleep.assert_not_called()

    def test_the_hourly_check_runs_the_rival_tracker_first(self):
        order = []

        def hook():
            order.append("rival tracker")

        scheduler.daily_hooks.append(hook)
        self.addCleanup(scheduler.daily_hooks.remove, hook)
        with mock.patch("aso.scheduler._needs_refresh_today", return_value=True), \
                mock.patch("aso.scheduler._run_daily_refresh", side_effect=lambda: order.append("tracked keywords")), \
                mock.patch("aso.apple_ads.sync.maybe_run_sync"), \
                mock.patch.object(run_queue, "lane_state", return_value="idle"):
            scheduler._tick()
        self.assertEqual(order[-2:], ["rival tracker", "tracked keywords"])
        self.assertEqual(order.count("tracked keywords"), 1)
