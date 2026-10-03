"""The refresh is always current and never runs twice (aso/scheduler.py).

A nudge (the Mac app sends one a minute after the Mac wakes) ends the
hourly wait at once. Every refresh claims one running flag first, so the
daily check, the Dashboard's Refresh Rankings button and the menu bar's
Update All Tracked Keywords can never overlap. The menu bar's status line says, in
the reader's own day, when rankings were last read.
"""

import time
from datetime import datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils.html import escape

from aso import run_queue, scheduler
from aso.models import App, Keyword, KeywordSearchJob, SearchResult
from aso.tests.helpers import _SyncThread

STOCKHOLM = ZoneInfo("Europe/Stockholm")


class NudgeTest(SimpleTestCase):
    def setUp(self):
        scheduler._wake.clear()
        self.addCleanup(scheduler._wake.clear)

    def test_a_nudge_ends_the_wait_at_once(self):
        scheduler.nudge()
        started = time.monotonic()
        self.assertTrue(scheduler._wait_for_next_check(5))
        self.assertLess(time.monotonic() - started, 0.5)

    def test_without_a_nudge_the_wait_runs_its_course(self):
        self.assertFalse(scheduler._wait_for_next_check(0.01))

    def test_a_nudge_is_used_once(self):
        scheduler.nudge()
        self.assertTrue(scheduler._wait_for_next_check(0.01))
        self.assertFalse(scheduler._wait_for_next_check(0.01))


class OneRefreshAtATimeTest(TestCase):
    def setUp(self):
        self.addCleanup(run_queue._active.clear)
        self.addCleanup(lambda: scheduler._update_status(running=False, error=None))

    def test_a_claim_is_refused_while_a_refresh_runs(self):
        self.assertTrue(scheduler._claim_refresh(3))
        self.assertTrue(scheduler.get_status()["running"])
        self.assertEqual(scheduler.get_status()["total"], 3)
        self.assertFalse(scheduler._claim_refresh(5))
        self.assertEqual(scheduler.get_status()["total"], 3)

    def test_the_daily_refresh_skips_while_a_manual_one_runs(self):
        scheduler._claim_refresh(1)
        with mock.patch("aso.scheduler._get_pairs_to_refresh", return_value=[(1, "us")]), \
             mock.patch("aso.scheduler._refresh_pair") as pair, \
             self.assertLogs("aso.scheduler", level="INFO") as logs:
            scheduler._run_daily_refresh()
        pair.assert_not_called()
        self.assertTrue(any("skipped: another refresh is running" in line for line in logs.output))

    def test_a_second_manual_refresh_is_refused(self):
        keyword = Keyword.objects.create(keyword="a")
        SearchResult.objects.create(keyword=keyword, country="us", difficulty_score=10)
        with mock.patch("aso.scheduler.threading.Thread") as thread:
            self.assertTrue(scheduler.run_manual_refresh([(keyword.pk, "us")]))
            self.assertFalse(scheduler.run_manual_refresh([(keyword.pk, "us")]))
        thread.assert_called_once()

    def test_a_claimed_manual_refresh_runs_and_releases(self):
        keyword = Keyword.objects.create(keyword="a")
        SearchResult.objects.create(keyword=keyword, country="us", difficulty_score=10)
        with mock.patch("aso.scheduler.threading.Thread", _SyncThread), \
             mock.patch("aso.scheduler._refresh_pair") as pair:
            self.assertTrue(scheduler.run_manual_refresh([(keyword.pk, "us")]))
        pair.assert_called_once()
        self.assertFalse(scheduler.get_status()["running"])


class BulkRefreshTest(TestCase):
    def setUp(self):
        self.addCleanup(run_queue._active.clear)
        self.addCleanup(lambda: scheduler._update_status(running=False, error=None))
        self.app = App.objects.create(name="Habito")
        self.mine = Keyword.objects.create(keyword="habit tracker", app=self.app)
        self.other = Keyword.objects.create(keyword="daily planner")
        SearchResult.objects.create(keyword=self.mine, country="us", difficulty_score=10)
        SearchResult.objects.create(keyword=self.mine, country="se", difficulty_score=10)
        SearchResult.objects.create(keyword=self.other, country="us", difficulty_score=10)

    def test_pairs_for_everything_and_for_a_filter(self):
        everything = set(scheduler.bulk_refresh_pairs())
        self.assertEqual(everything, {(self.mine.pk, "us"), (self.mine.pk, "se"), (self.other.pk, "us")})
        self.assertEqual(set(scheduler.bulk_refresh_pairs(app_id=self.app.pk)),
                         {(self.mine.pk, "us"), (self.mine.pk, "se")})
        self.assertEqual(set(scheduler.bulk_refresh_pairs(country="se")), {(self.mine.pk, "se")})

    def test_start_outcomes(self):
        self.assertEqual(scheduler.start_bulk_refresh([]), ("nothing", "No tracked keywords to refresh yet."))
        scheduler._claim_refresh(1)
        self.assertEqual(scheduler.start_bulk_refresh([(self.mine.pk, "us")]),
                         ("refreshing", "A refresh is already in progress."))
        scheduler._update_status(running=False)
        KeywordSearchJob.objects.create(keywords=["a"], countries=["us"], status="running")
        outcome, message = scheduler.start_bulk_refresh([(self.mine.pk, "us")])
        self.assertEqual(outcome, "run_busy")
        self.assertTrue(message.endswith("is running. Refresh when it finishes."))

    def test_start_and_the_dashboard_button_answer_alike(self):
        with mock.patch("aso.scheduler.threading.Thread") as thread:
            self.assertEqual(scheduler.start_bulk_refresh(scheduler.bulk_refresh_pairs()), ("started", ""))
        thread.assert_called_once()
        response = self.client.post(reverse("aso:keywords_bulk_refresh"), {"app_id": None},
                                    content_type="application/json")
        self.assertEqual(response.json(), {"success": False, "error": "A refresh is already in progress."})

    def test_the_dashboard_button_with_nothing_to_refresh(self):
        response = self.client.post(reverse("aso:keywords_bulk_refresh"), {"app_id": 999999},
                                    content_type="application/json")
        self.assertEqual(response.json(), {"success": True, "started": False, "total": 0})


class RefreshRankingsSaysWhatItRefreshesTest(TestCase):
    """The Dashboard's Refresh Rankings asked "Refresh all tracked keywords
    across all countries?" in every view, while the button refreshed only the
    app and the country on screen (bulk_refresh_pairs). The confirm now
    names both, composed on the server from the same view."""

    def setUp(self):
        self.app = App.objects.create(name="Habito: Daily Habit Tracker")
        mine = Keyword.objects.create(keyword="habit tracker", app=self.app)
        other = Keyword.objects.create(keyword="daily planner")
        SearchResult.objects.create(keyword=mine, country="us", difficulty_score=10)
        SearchResult.objects.create(keyword=mine, country="se", difficulty_score=10)
        SearchResult.objects.create(keyword=other, country="us", difficulty_score=10)

    def test_one_sentence_naming_the_app_and_the_country(self):
        ask = scheduler.bulk_refresh_question
        self.assertEqual(ask(self.app, "se", ["se", "us"]),
                         "Update every keyword of Habito in the App Store in Sweden now?")
        self.assertEqual(ask(self.app, "", ["se", "us"]),
                         "Update every keyword of Habito in every country now?")
        # A view with one storefront refreshes only that one, so it is named.
        self.assertEqual(ask(self.app, "", ["us"]),
                         "Update every keyword of Habito in the App Store in the United States now?")
        # All Apps also refreshes the keywords that belong to no app.
        self.assertEqual(ask(None, "us", ["se", "us"]),
                         "Update every tracked keyword, for all apps, in the App Store "
                         "in the United States now?")
        self.assertEqual(ask(None, "", ["se", "us"]),
                         "Update every tracked keyword, for all apps, in every country now?")

    def test_the_button_asks_about_the_view_it_refreshes(self):
        """The question rides on the button, rendered with the same app
        (data-app) and country (the country menu) the request sends."""
        for query, app, country, question in (
            (f"?app={self.app.pk}&country=se", str(self.app.pk), "se",
             "Update every keyword of Habito in the App Store in Sweden now?"),
            (f"?app={self.app.pk}&country=", str(self.app.pk), "",
             "Update every keyword of Habito in every country now?"),
            ("?app=&country=us", "", "us",
             "Update every tracked keyword, for all apps, in the App Store in the United States now?"),
        ):
            with self.subTest(query=query):
                html = self.client.get(reverse("aso:dashboard") + query).content.decode()
                self.assertIn(f'data-confirm="{escape(question)}"', html)
                self.assertIn(f'data-app="{app}"', html)
                if country:
                    self.assertIn(f'<option value="{country}" selected>', html)
                self.assertIn("item.dataset.confirm", html)
                self.assertNotIn("Refresh all tracked keywords", html)
                pairs = scheduler.bulk_refresh_pairs(app_id=int(app) if app else None, country=country)
                self.assertTrue(pairs)
                if app:
                    self.assertEqual({k.app_id for k in Keyword.objects.filter(
                        pk__in=[kw for kw, _ in pairs])}, {self.app.pk})
                if country:
                    self.assertEqual({c for _, c in pairs}, {country})


class MenuStatusTest(TestCase):
    NOW = datetime(2026, 9, 30, 12, 0, tzinfo=STOCKHOLM)

    def setUp(self):
        self.addCleanup(run_queue._active.clear)
        self.addCleanup(lambda: scheduler._update_status(running=False, error=None))
        zone = mock.patch("aso.local_day.user_timezone", return_value=STOCKHOLM)
        zone.start()
        self.addCleanup(zone.stop)

    def _read_at(self, moment):
        keyword = Keyword.objects.create(keyword=f"k{moment.isoformat()}")
        row = SearchResult.objects.create(keyword=keyword, country="us", difficulty_score=10)
        SearchResult.objects.filter(pk=row.pk).update(searched_at=moment)

    def test_nothing_tracked(self):
        self.assertEqual(scheduler.menu_status(self.NOW), ("No tracked keywords yet", False, "Update All Tracked Keywords"))

    def test_today_yesterday_and_older(self):
        self._read_at(datetime(2026, 9, 28, 7, 10, tzinfo=STOCKHOLM))
        self.assertEqual(scheduler.menu_status(self.NOW)[0], "Tracked keywords updated on 28 Sep at 07:10")
        self._read_at(datetime(2026, 9, 29, 22, 5, tzinfo=STOCKHOLM))
        self.assertEqual(scheduler.menu_status(self.NOW)[0], "Tracked keywords updated yesterday at 22:05")
        self._read_at(datetime(2026, 9, 30, 7, 10, tzinfo=STOCKHOLM))
        self.assertEqual(scheduler.menu_status(self.NOW), ("Tracked keywords updated today at 07:10", True, "Update All Tracked Keywords"))

    def test_while_a_refresh_runs(self):
        self._read_at(datetime(2026, 9, 30, 7, 10, tzinfo=STOCKHOLM))
        scheduler._claim_refresh(10)
        scheduler._update_status(completed=2)
        self.assertEqual(scheduler.menu_status(self.NOW), ("Updating tracked keywords: 3 of 10", False, "Update All Tracked Keywords"))

    def test_while_a_run_holds_the_lane(self):
        self._read_at(datetime(2026, 9, 30, 7, 10, tzinfo=STOCKHOLM))
        KeywordSearchJob.objects.create(keywords=["a"], countries=["us"], status="running")
        line, can_refresh, title = scheduler.menu_status(self.NOW)
        self.assertEqual(line, "Tracked keywords updated today at 07:10")
        self.assertFalse(can_refresh)
        self.assertTrue(title.startswith("Update after ") and title.endswith(" finishes"), title)
