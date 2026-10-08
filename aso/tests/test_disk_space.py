"""Deleted data gives its disk space back (aso/disk_space.py).

Public issue #27: a user tracked 1,600 keywords, deleted them all, and the
database stayed 1.6 GB. These tests measure the database itself, the way the
user saw it: its pages before and after a deletion through the page they use.

Free-tier test: no aso_pro import.
"""

import json
import os
import sqlite3
import tempfile
from datetime import timedelta
from unittest import mock

from django.db import OperationalError, connection, transaction
from django.test import SimpleTestCase, TestCase, TransactionTestCase
from django.urls import reverse
from django.utils import timezone

from aso import day_reads, disk_space
from aso.local_day import local_date
from aso.models import App, Keyword, KeywordDayRead, SearchResult

BIG = [{"trackId": i, "trackName": f"App {i}", "description": "x" * 1800} for i in range(25)]  # about 50 KB


def _today():
    return local_date(timezone.now())


def _pages():
    with connection.cursor() as cursor:
        cursor.execute("PRAGMA page_count")
        pages = cursor.fetchone()[0]
        cursor.execute("PRAGMA freelist_count")
        return pages, cursor.fetchone()[0]


def _read(term, day, country="us"):
    return KeywordDayRead.objects.create(
        term=term, country=country, day=day, ranked_ids=list(range(200)), result_count=200,
        competitors=[], fetched_at=timezone.now(),
    )


class NewDatabaseTest(TestCase):
    def test_a_new_database_gives_space_back_once_migrated(self):
        with connection.cursor() as cursor:
            cursor.execute("PRAGMA auto_vacuum")
            self.assertEqual(cursor.fetchone()[0], disk_space.FULL)


class ConvertOlderDatabaseTest(SimpleTestCase):
    """An older database, made before this change, is converted once and
    hands back what earlier deletions left inside it."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = os.path.join(folder.name, "db.sqlite3")
        db = sqlite3.connect(self.path, isolation_level=None)
        self.addCleanup(db.close)
        self.cursor = db.cursor()
        self.cursor.execute("CREATE TABLE history (data BLOB)")
        self.cursor.executemany("INSERT INTO history VALUES (?)", [(b"x" * 50_000,)] * 200)
        self.cursor.execute("DELETE FROM history")

    def test_it_is_converted_once_and_shrinks_to_what_it_holds(self):
        self.assertGreater(os.path.getsize(self.path), 9_000_000, "the deletion left the file as big as before")
        self.assertTrue(disk_space.ensure_full_auto_vacuum(self.cursor))
        self.cursor.execute("PRAGMA auto_vacuum")
        self.assertEqual(self.cursor.fetchone()[0], disk_space.FULL)
        self.assertLess(os.path.getsize(self.path), 100_000)
        self.assertFalse(disk_space.ensure_full_auto_vacuum(self.cursor), "a second launch changes nothing")


class BusyDatabaseTest(TransactionTestCase):
    """After migrate, outside any transaction, as the Mac app and Docker run it."""

    def test_a_busy_database_is_left_for_the_next_launch(self):
        with mock.patch.object(disk_space, "ensure_full_auto_vacuum", side_effect=OperationalError("database is locked")), \
                self.assertLogs("aso.disk_space", "WARNING") as logs:
            disk_space.convert_after_migrate(sender=None, using="default")
        self.assertIn("the next launch tries again", logs.output[0])


class AfterCommitOnceTest(TestCase):
    def test_one_cleanup_per_transaction_however_many_rows_ask(self):
        calls = []
        cleanup = lambda: calls.append(1)  # noqa: E731 (a named stand-in for a cleanup)
        with self.captureOnCommitCallbacks(execute=True) as callbacks, transaction.atomic():
            for _ in range(3):
                disk_space.after_commit_once(cleanup)
        self.assertEqual((len(callbacks), len(calls)), (1, 1))

    def test_a_rolled_back_transaction_does_not_stop_the_next_one(self):
        calls = []
        cleanup = lambda: calls.append(1)  # noqa: E731 (a named stand-in for a cleanup)
        with self.captureOnCommitCallbacks(execute=True):
            try:
                with transaction.atomic():
                    disk_space.after_commit_once(cleanup)
                    raise ValueError("the deletion failed")
            except ValueError:
                pass
            with transaction.atomic():
                disk_space.after_commit_once(cleanup)
        self.assertEqual(len(calls), 1)

    def test_a_failing_cleanup_never_fails_the_deletion(self):
        def broken():
            raise RuntimeError("boom")

        with self.assertLogs("aso.disk_space", "ERROR"), self.captureOnCommitCallbacks(execute=True), \
                transaction.atomic():
            disk_space.after_commit_once(broken)


class ForgetUntrackedReadsTest(TestCase):
    def setUp(self):
        self.yesterday = _today() - timedelta(days=1)

    def test_finished_days_of_a_keyword_no_longer_tracked_go_and_today_stays(self):
        Keyword.objects.create(keyword="Habit Tracker")
        kept = _read("habit tracker", self.yesterday)
        gone = _read("old term", self.yesterday)
        today = _read("old term", _today())
        self.assertEqual(day_reads.forget_untracked(), 1)
        self.assertTrue(KeywordDayRead.objects.filter(pk=kept.pk).exists(), "any case or spacing is the same keyword")
        self.assertFalse(KeywordDayRead.objects.filter(pk=gone.pk).exists())
        self.assertTrue(KeywordDayRead.objects.filter(pk=today.pk).exists(), "today's read is reused all day")

    def test_the_hourly_tidy_forgets_them_too(self):
        _read("old term", self.yesterday)
        self.assertEqual(day_reads.tidy(90), (0, 1))
        self.assertEqual(day_reads.tidy(90), (0, 0))

    def test_deleting_a_keyword_forgets_its_finished_days_once_the_deletion_commits(self):
        app, other = App.objects.create(name="Mine", track_id=1), App.objects.create(name="Other", track_id=2)
        mine = Keyword.objects.create(keyword="habit tracker", app=app)
        Keyword.objects.create(keyword="streak app", app=app)
        Keyword.objects.create(keyword="streak app", app=other)
        _read("habit tracker", self.yesterday)
        shared = _read("streak app", self.yesterday)
        with self.captureOnCommitCallbacks(execute=True):
            mine.delete()
            Keyword.objects.filter(keyword="streak app", app=app).delete()
        self.assertEqual(list(KeywordDayRead.objects.values_list("pk", flat=True)), [shared.pk],
                         "another app still tracks streak app")


class DeletingKeywordsGivesTheSpaceBackTest(TransactionTestCase):
    """Through the pages the user deletes with, measured on the database
    itself. A TransactionTestCase: the file only shrinks when the deletion
    really commits."""

    def setUp(self):
        self.app = App.objects.create(name="Mine", track_id=1)
        for i in range(40):
            keyword = Keyword.objects.create(keyword=f"keyword {i}", app=self.app)
            for _ in range(5):
                SearchResult.objects.create(
                    keyword=keyword, country="us", popularity_score=40, difficulty_score=50,
                    difficulty_breakdown={}, competitors_data=BIG,
                )
            _read(f"keyword {i}", _today() - timedelta(days=1))
        _read("keyword 0", _today())

    def _assert_space_given_back(self, before):
        after, free = _pages()
        self.assertEqual(free, 0, "no freed page stays inside the file")
        self.assertLess(after, before * 0.3, f"{before} pages before, {after} after")

    def test_deleting_all_of_an_apps_keywords(self):
        before, _free = _pages()
        response = self.client.post(reverse("aso:keywords_bulk_delete"), json.dumps({"app_id": self.app.pk}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self._assert_space_given_back(before)
        self.assertEqual(list(KeywordDayRead.objects.values_list("term", "day")), [("keyword 0", _today())],
                         "only today's read stays")

    def test_deleting_the_rows_on_the_keywords_page(self):
        before, _free = _pages()
        entries = [{"keyword_id": keyword.pk, "country": "us"} for keyword in Keyword.objects.all()]
        response = self.client.post(reverse("aso:results_bulk_delete"), json.dumps({"entries": entries}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Keyword.objects.exists())
        self._assert_space_given_back(before)
        self.assertEqual(KeywordDayRead.objects.filter(day__lt=_today()).count(), 0)
