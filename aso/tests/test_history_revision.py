"""The Dashboard follows every change to its rows without a reload.

The page used to fetch its table again only when its poll happened to see
the ranking refresh running and then stop: a refresh during a Mac's sleep,
or one shorter than a poll, never reached the screen (reproduced in a
browser on 2026-09-29). Now SQLite counts every change to search results,
keywords and apps (aso/history_revision.py), the History section carries
the count it was drawn from, and the page fetches its sections when the
server's count differs. These tests pin the count and what the page reads;
the browser side was driven end to end with Playwright (see
docs/development/HISTORY_LIVE_REFRESH_PLAN.md).

Free-tier test: no aso_pro import.
"""

import datetime as dt
import json
import os
import shutil
import subprocess
from typing import ClassVar
from unittest import mock

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, TransactionTestCase
from django.urls import reverse
from django.utils.timesince import timesince

from aso import history_revision, scheduler
from aso.models import App, Keyword, SearchResult


def _result(keyword, country="us", **fields):
    values = {
        "popularity_score": 40, "difficulty_score": 30,
        "difficulty_breakdown": {}, "competitors_data": [],
    }
    values.update(fields)
    return SearchResult.objects.create(keyword=keyword, country=country, **values)


def _triggers():
    with connection.cursor() as cursor:
        cursor.execute("SELECT name FROM sqlite_master WHERE type = 'trigger'")
        return {row[0] for row in cursor.fetchall()}


def _trigger_count():
    """Three triggers per watched table: the Dashboard's four tables, plus
    the tables another app registers through watch() (the Pro build's Rival
    Tracker registers eight; the free edition none)."""
    return len(history_revision.watched_tables()) * len(history_revision.OPERATIONS)


class TriggersTest(TestCase):
    def test_every_watched_table_counts_every_kind_of_write(self):
        expected = {
            history_revision.trigger_name(table, operation)
            for table in history_revision.watched_tables()
            for operation in history_revision.OPERATIONS
        }
        self.assertEqual(len(expected), _trigger_count())
        self.assertGreaterEqual(len(expected), 12)
        self.assertLessEqual(expected, _triggers())

    def test_a_dropped_trigger_comes_back_with_the_next_migrate(self):
        """A migration that rebuilds a table drops its triggers; the
        post_migrate hook in aso/apps.py puts them back."""
        name = history_revision.trigger_name(SearchResult._meta.db_table, "UPDATE")
        with connection.cursor() as cursor:
            cursor.execute(f"DROP TRIGGER {name}")
        self.assertNotIn(name, _triggers())
        call_command("migrate", verbosity=0)
        self.assertIn(name, _triggers())

    def test_ensuring_twice_changes_nothing(self):
        before = _triggers()
        self.assertEqual(history_revision.ensure_triggers(), _trigger_count())
        self.assertEqual(_triggers(), before)


class RollbackTest(TransactionTestCase):
    """Rolling the count's migration back leaves every table writable.

    A trigger left behind writes to a table that no longer exists, and every
    write to the table it sits on fails ("no such table:
    main.aso_historyrevision", seen on 2026-09-29 before 0019 dropped its
    triggers itself). A table a rollback drops takes its triggers with it.
    """

    def test_writes_work_after_a_rollback_and_counting_resumes(self):
        try:
            call_command("migrate", "aso", "0018", verbosity=0)
            self.assertEqual(_triggers(), set())
            app = App.objects.create(name="Calm Minutes")
            keyword = Keyword.objects.create(keyword="habit tracker", app=app)
            _result(keyword)
            SearchResult.objects.filter(keyword=keyword).update(app_rank=3)
        finally:
            call_command("migrate", verbosity=0)
        self.assertEqual(len(_triggers()), _trigger_count())
        before = history_revision.count()
        _result(keyword, country="gb")
        self.assertGreater(history_revision.count(), before)


class CountTest(TestCase):
    """Whoever writes, and however: the count moves."""

    def setUp(self):
        self.app = App.objects.create(name="Calm Minutes")
        self.keyword = Keyword.objects.create(keyword="habit tracker", app=self.app)
        self.result = _result(self.keyword)

    def assertCounts(self, write):
        before = history_revision.count()
        write()
        self.assertGreater(history_revision.count(), before)

    def test_a_new_snapshot(self):
        self.assertCounts(lambda: _result(self.keyword, country="gb"))

    def test_the_daily_upsert(self):
        self.assertCounts(lambda: SearchResult.upsert_today(
            keyword=self.keyword, country="us", popularity_score=50, difficulty_score=20,
            difficulty_breakdown={}, competitors_data=[]))

    def test_a_save(self):
        self.result.apple_popularity_score = 61
        self.assertCounts(self.result.save)

    def test_a_queryset_update(self):
        self.assertCounts(lambda: SearchResult.objects.filter(pk=self.result.pk).update(app_rank=4))

    def test_a_bulk_update(self):
        self.result.classification = "Hidden Gem"
        self.assertCounts(lambda: SearchResult.objects.bulk_update([self.result], ["classification"]))

    def test_a_deleted_row(self):
        self.assertCounts(self.result.delete)

    def test_a_deleted_keyword_and_its_rows(self):
        self.assertCounts(self.keyword.delete)

    def test_an_app_profile_read(self):
        self.assertCounts(lambda: App.objects.filter(pk=self.app.pk).update(store_profiles={"us": {"count": 9}}))

    def test_apple_popularity_arriving_later(self):
        from aso.apple_ads import sync

        with mock.patch("aso.models.AppleSearchPopularity.lookup", return_value=72):
            self.assertCounts(sync._patch_today_rows)
        self.result.refresh_from_db()
        self.assertEqual(self.result.apple_popularity_score, 72)

    def test_a_read_does_not_count(self):
        before = history_revision.count()
        list(SearchResult.objects.all())
        self.client.get(reverse("aso:dashboard"))
        self.assertEqual(history_revision.count(), before)


class TokenTest(TestCase):
    def test_the_token_moves_with_the_count(self):
        before = history_revision.current()
        _result(Keyword.objects.create(keyword="focus timer"))
        self.assertNotEqual(history_revision.current(), before)

    def test_the_popularity_source_changes_how_the_same_rows_read(self):
        with mock.patch("aso.apple_ads.storage.load_apple_settings",
                        return_value={"popularity_source": "internal", "apple_ads": {"active_weeks": {}}}):
            internal = history_revision.current()
        with mock.patch("aso.apple_ads.storage.load_apple_settings",
                        return_value={"popularity_source": "apple", "apple_ads": {"active_weeks": {}}}):
            apple = history_revision.current()
        self.assertNotEqual(internal, apple)

    def test_a_new_apple_week_changes_how_the_same_rows_read(self):
        def settings_with(weeks):
            return {"popularity_source": "apple", "apple_ads": {"active_weeks": weeks}}

        with mock.patch("aso.apple_ads.storage.load_apple_settings",
                        return_value=settings_with({"us": "2026-09-13"})):
            old_week = history_revision.current()
        with mock.patch("aso.apple_ads.storage.load_apple_settings",
                        return_value=settings_with({"us": "2026-09-20"})):
            new_week = history_revision.current()
        self.assertNotEqual(old_week, new_week)


class DashboardReadsTheTokenTest(TestCase):
    def test_the_history_section_carries_what_it_was_drawn_from(self):
        _result(Keyword.objects.create(keyword="habit tracker"))
        response = self.client.get(reverse("aso:dashboard"))
        self.assertContains(response, f'data-revision="{history_revision.current()}"')

    def test_an_empty_table_carries_it_too(self):
        """A first search made in another window must still appear."""
        response = self.client.get(reverse("aso:dashboard"))
        self.assertContains(response, f'data-revision="{history_revision.current()}"')

    def test_the_status_answers_with_the_same_token_until_a_row_changes(self):
        keyword = Keyword.objects.create(keyword="habit tracker")
        _result(keyword)
        page = self.client.get(reverse("aso:dashboard")).content.decode()
        drawn = history_revision.current()
        self.assertIn(f'data-revision="{drawn}"', page)

        status = self.client.get(reverse("aso:auto_refresh_status")).json()
        self.assertEqual(status["history_revision"], drawn)
        self.assertIn("running", status)

        SearchResult.upsert_today(keyword=keyword, country="us", popularity_score=55,
                                  difficulty_score=25, difficulty_breakdown={}, competitors_data=[])
        status = self.client.get(reverse("aso:auto_refresh_status")).json()
        self.assertNotEqual(status["history_revision"], drawn)

    def test_the_page_compares_instead_of_waiting_to_see_a_refresh_run(self):
        with open(os.path.join(settings.BASE_DIR, "aso/templates/aso/dashboard.html")) as f:
            page = f.read()
        self.assertIn("section.dataset.revision !== data.history_revision", page)
        self.assertIn("visibilitychange", page)
        self.assertNotIn("wasRunning", page)
        with open(os.path.join(settings.BASE_DIR, "static/js/keyword-search-job.js")) as f:
            self.assertNotIn("onProgress", f.read(), "one mechanism keeps the history current, not two")


class RefreshLoopTest(TestCase):
    def setUp(self):
        self.addCleanup(lambda: scheduler._update_status(running=False, error=None))

    def test_each_refreshed_keyword_moves_the_count_as_it_is_written(self):
        keywords = [Keyword.objects.create(keyword=f"kw {i}") for i in range(3)]
        seen = []

        def refresh(keyword, country, force=False):
            seen.append(history_revision.count())
            return SearchResult.upsert_today(keyword=keyword, country=country, popularity_score=50,
                                             difficulty_score=20, difficulty_breakdown={},
                                             competitors_data=[])

        with mock.patch("aso.scheduler._refresh_pair", side_effect=refresh), \
             mock.patch("aso.scheduler.time.sleep"), \
             mock.patch("aso.scheduler.run_queue.kick"):
            scheduler._refresh_pairs([(k.pk, "us") for k in keywords], "Auto-refresh")
        self.assertEqual(len(seen), 3)
        self.assertLess(seen[0], seen[1])
        self.assertLess(seen[1], seen[2])

    def test_the_log_says_what_the_refresh_did(self):
        keywords = [Keyword.objects.create(keyword=f"kw {i}") for i in range(3)]
        outcomes = [mock.Mock(), None, RuntimeError("boom")]
        with mock.patch("aso.scheduler._refresh_pair", side_effect=outcomes), \
             mock.patch("aso.scheduler.time.sleep"), \
             mock.patch("aso.scheduler.run_queue.kick"), \
             self.assertLogs("aso.scheduler", level="INFO") as logs:
            refreshed = scheduler._refresh_pairs([(k.pk, "us") for k in keywords], "Auto-refresh")
        self.assertEqual(refreshed, 1)
        self.assertTrue(any(
            "1 of 3 pairs refreshed, 1 skipped (App Store unavailable), 0 busy, 1 failed" in line
            for line in logs.output))

    def test_a_run_the_database_stops_ends_as_finished(self):
        """The status used to stay "running" until a restart, refusing every
        later refresh and keeping the progress bar up."""
        with mock.patch("aso.scheduler.run_queue.kick"), \
             mock.patch("aso.models.Keyword.objects.select_related", side_effect=RuntimeError("database is locked")), \
             self.assertLogs("aso.scheduler", level="ERROR"):
            scheduler._refresh_pairs([(1, "us")], "Manual bulk refresh")
        status = scheduler.get_status()
        self.assertFalse(status["running"])
        self.assertEqual(status["error"], "database is locked")

    def test_the_log_lines_reach_the_mac_apps_log_file(self):
        from core import settings as core_settings

        with open(core_settings.__file__) as f:
            text = f.read()
        self.assertIn('"aso.scheduler": {', text)


class TimeAgoTwinTest(TestCase):
    """static/js/time-ago.js redraws "refreshed X ago" as time passes. Its
    words must be Django's timesince words, or the text changes its shape
    when the script takes over from the server."""

    CASES: ClassVar[list[dt.timedelta]] = [
        dt.timedelta(seconds=0), dt.timedelta(seconds=59), dt.timedelta(minutes=1),
        dt.timedelta(minutes=2, seconds=30), dt.timedelta(hours=1), dt.timedelta(hours=1, minutes=1),
        dt.timedelta(hours=13, minutes=47), dt.timedelta(days=1), dt.timedelta(days=1, hours=3),
        dt.timedelta(days=6, hours=23, minutes=59), dt.timedelta(days=7), dt.timedelta(days=9, hours=5),
        dt.timedelta(days=13), dt.timedelta(days=31), dt.timedelta(days=45, hours=2),
        dt.timedelta(days=89, minutes=5), dt.timedelta(days=400),
    ]
    NOWS: ClassVar[list[dt.datetime]] = [
        dt.datetime(2026, 9, 29, 20, 13, 29, 512000, tzinfo=dt.UTC),
        dt.datetime(2026, 3, 1, 0, 0, 5, tzinfo=dt.UTC),
        dt.datetime(2026, 1, 31, 23, 59, 59, 999000, tzinfo=dt.UTC),
    ]

    def test_the_script_writes_what_django_writes(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed; the browser check covers this")
        pairs = []
        for now in self.NOWS:
            for delta in self.CASES:
                then = now - delta
                pairs.append({
                    "then": then.isoformat(timespec="milliseconds"),
                    "now": now.isoformat(timespec="milliseconds"),
                    "django": timesince(then, now),
                    # What the ago filter writes (aso_tags.ago).
                    "ago": "just now" if delta < dt.timedelta(minutes=1) else f"{timesince(then, now)} ago",
                })
        script = (
            "global.window = {};"
            f"require({json.dumps(os.path.join(settings.BASE_DIR, 'static/js/time-ago.js'))});"
            "const pairs = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
            "process.stdout.write(JSON.stringify(pairs.map(p => ["
            "window.TimeAgo.since(new Date(p.then), new Date(p.now)),"
            "window.TimeAgo.ago(new Date(p.then), new Date(p.now))])));"
        )
        out = subprocess.run([node, "-e", script], input=json.dumps(pairs), capture_output=True,
                             text=True, check=True, timeout=30).stdout
        for pair, (words, phrase) in zip(pairs, json.loads(out)):
            with self.subTest(then=pair["then"], now=pair["now"]):
                self.assertEqual(words, pair["django"])
                self.assertEqual(phrase, pair["ago"])

    def test_the_filter_says_just_now_under_a_minute(self):
        """"0 minutes ago" read like a fault (2026-10-03)."""
        from django.utils import timezone

        from aso.templatetags.aso_tags import ago

        now = timezone.now()
        self.assertEqual(ago(now - dt.timedelta(seconds=20)), "just now")
        self.assertEqual(ago(now - dt.timedelta(hours=2)), "2\u00a0hours ago")
        self.assertEqual(ago(None), "")

    def test_every_time_ago_is_marked_and_whole(self):
        """Every "ago" is the ago filter inside a <time data-time-ago>, which
        the script redraws; a bare timesince would freeze where it was drawn,
        and an " ago" outside the element could never become "just now"."""
        from pathlib import Path

        for path in sorted(Path(settings.BASE_DIR).glob("aso*/templates/**/*.html")):
            for line in path.read_text(encoding="utf-8").splitlines():
                with self.subTest(template=str(path.relative_to(settings.BASE_DIR)), line=line.strip()[:80]):
                    self.assertNotIn("|timesince", line)
                    if "|ago" in line:
                        self.assertIn("data-time-ago", line)
