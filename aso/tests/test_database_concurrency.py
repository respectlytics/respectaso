"""Several writers share one SQLite database without "database is locked"
(core/database.py, aso/db_writes.py).

On 2026-10-08 a keyword missed its daily update with "database is locked":
the hourly cleanup held the database for seconds while the refresh wanted to
write, and SQLite refuses to wait for a transaction that started by reading.
The test suite's own database lives in memory, where none of this applies, so
these tests open a real database file with the production settings, through
Django, from two threads, as the app's threads and the MCP server do.

Free-tier test: no aso_pro import.
"""

import copy
import os
import tempfile
import threading
import time
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.db import OperationalError, connections, transaction
from django.db.backends.sqlite3.base import DatabaseWrapper
from django.test import SimpleTestCase, TestCase

from aso import db_writes, disk_space, scheduler
from aso.models import Keyword, KeywordDayRead
from core.database import SQLITE_OPTIONS

ALIAS = "concurrency_probe"


class _Probe:
    """A Django connection to one database file with the production options,
    registered under ALIAS in the calling thread only."""

    def __init__(self, path, options=SQLITE_OPTIONS):
        params = copy.deepcopy(connections["default"].settings_dict)
        params.update(NAME=str(path), OPTIONS=dict(options))
        self.wrapper = DatabaseWrapper(params, alias=ALIAS)
        connections[ALIAS] = self.wrapper

    def run(self, sql, params=()):
        with self.wrapper.cursor() as cursor:
            cursor.execute(sql, params)
            return cursor.fetchall()

    def close(self):
        self.wrapper.close()


class ConcurrencyTest(SimpleTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "db.sqlite3"
        probe = _Probe(self.path)
        probe.run("CREATE TABLE rows (id INTEGER PRIMARY KEY, data BLOB)")
        probe.run("INSERT INTO rows (data) VALUES ('first')")
        probe.close()

    def _in_thread(self, work, options=SQLITE_OPTIONS):
        """Run ``work(probe)`` on its own thread and connection; returns the
        thread and a dict holding its result or error."""
        out = {}

        def run():
            probe = _Probe(self.path, options)
            try:
                out["result"] = work(probe)
            except Exception as e:  # noqa: BLE001 (the test reads it)
                out["error"] = e
            finally:
                probe.close()

        thread = threading.Thread(target=run)
        thread.start()
        return thread, out

    def _hold_the_write_lock(self, seconds, options=SQLITE_OPTIONS):
        holding = threading.Event()

        def work(probe):
            with transaction.atomic(using=ALIAS):
                probe.run("INSERT INTO rows (data) VALUES ('long write')")
                holding.set()
                time.sleep(seconds)

        thread, out = self._in_thread(work, options)
        holding.wait(5)
        return thread, out

    def _read_then_write(self, probe):
        """The refresh's shape: an atomic block that reads, then writes."""
        started = time.monotonic()
        with transaction.atomic(using=ALIAS):
            probe.run("SELECT COUNT(*) FROM rows")
            probe.run("INSERT INTO rows (data) VALUES ('second writer')")
        return time.monotonic() - started

    def test_the_database_is_in_wal_mode(self):
        probe = _Probe(self.path)
        self.addCleanup(probe.close)
        self.assertEqual(probe.run("PRAGMA journal_mode")[0][0], "wal")

    def test_a_second_writer_waits_its_turn_instead_of_failing(self):
        holder, held = self._hold_the_write_lock(1.0)
        writer, wrote = self._in_thread(self._read_then_write)
        holder.join()
        writer.join()
        self.assertNotIn("error", held)
        self.assertNotIn("error", wrote, "the second writer failed instead of waiting")
        self.assertGreater(wrote["result"], 0.5, "it waited for the first writer")

    def test_with_sqlites_defaults_the_same_writer_failed(self):
        """The defect these settings fix, kept as proof the test above can fail."""
        defaults = {"timeout": 5}
        holder, _held = self._hold_the_write_lock(1.0, defaults)
        writer, wrote = self._in_thread(self._read_then_write, defaults)
        holder.join()
        writer.join()
        self.assertIn("locked", str(wrote.get("error")))

    def test_a_page_reads_while_a_writer_writes(self):
        holder, held = self._hold_the_write_lock(1.0)
        reader, read = self._in_thread(lambda probe: probe.run("SELECT data FROM rows"))
        reader.join()
        holder.join()
        self.assertNotIn("error", held)
        self.assertNotIn("error", read)
        self.assertEqual(read["result"], [("first",)], "the reader sees what was committed")

    def _write_while_reading(self, rows_of):
        """The rescoring's shape: read rows in a loop and write as it goes,
        while another connection commits a write after the loop's read
        began. Returns the error the loop's write met, or None."""
        probe = _Probe(self.path)
        self.addCleanup(probe.close)
        with probe.wrapper.schema_editor() as editor:
            editor.create_model(KeywordDayRead)
        KeywordDayRead.objects.using(ALIAS).bulk_create([
            KeywordDayRead(term=f"term {i}", country="us", day="2026-01-01", ranked_ids=[],
                           competitors=[], fetched_at="2026-01-01T00:00:00Z")
            for i in range(300)
        ])
        reading, written = threading.Event(), threading.Event()

        def other_writer(other):
            reading.wait(5)
            other.run("UPDATE aso_keyworddayread SET result_count = 2 WHERE term = 'term 299'")
            written.set()

        thread, _out = self._in_thread(other_writer)
        try:
            for row in rows_of(KeywordDayRead.objects.using(ALIAS).order_by("pk")):
                if not reading.is_set():
                    reading.set()
                    written.wait(5)
                    KeywordDayRead.objects.using(ALIAS).filter(pk=row.pk).update(result_count=1)
            return None
        except OperationalError as e:
            return e
        finally:
            reading.set()
            thread.join()

    def test_a_loop_that_writes_as_it_reads_never_meets_a_lock(self):
        self.assertIsNone(self._write_while_reading(lambda qs: db_writes.rows_in_batches(qs, 100)))

    def test_with_one_open_read_the_same_loop_failed(self):
        """What the history rescoring did with queryset.iterator()."""
        error = self._write_while_reading(lambda qs: qs.iterator(chunk_size=100))
        self.assertIn("locked", str(error))

    def test_a_deletion_leaves_the_disk_with_the_wal_cut_back(self):
        probe = _Probe(self.path)
        self.addCleanup(probe.close)
        with probe.wrapper.cursor() as cursor:
            disk_space.ensure_full_auto_vacuum(cursor)
        for _ in range(200):
            probe.run("INSERT INTO rows (data) VALUES (?)", (os.urandom(50_000),))
        probe.run("DELETE FROM rows WHERE data != 'first'")
        disk_space.checkpoint(using=ALIAS)
        on_disk = sum(os.path.getsize(p) for p in self.path.parent.glob("db.sqlite3*"))
        self.assertLess(on_disk, 500_000, f"{on_disk} bytes left for one small row")


class BothEditionsUseTheSettingsTest(SimpleTestCase):
    def test_the_pro_settings(self):
        self.assertEqual(settings.DATABASES["default"]["OPTIONS"], SQLITE_OPTIONS)

    def test_the_free_editions_settings(self):
        """In the Pro repository. The free edition gets this file as its
        core/settings.py, which test_the_pro_settings checks there."""
        override = Path(settings.BASE_DIR) / "_public_overrides" / "core" / "settings.py"
        if not override.exists():
            self.skipTest("the free edition: its settings are the ones running")
        free = override.read_text()
        self.assertIn('"OPTIONS": SQLITE_OPTIONS', free)
        self.assertIn("from core.database import SQLITE_OPTIONS", free)


class BatchesTest(TestCase):
    def setUp(self):
        for i in range(7):
            KeywordDayRead.objects.create(term=f"term {i}", country="us", day="2026-01-01", ranked_ids=[],
                                          competitors=[{"trackId": 1}], fetched_at="2026-01-01T00:00:00Z")

    def test_delete_in_batches_deletes_only_the_queryset_one_batch_at_a_time(self):
        with mock.patch("aso.db_writes._rows", wraps=db_writes._rows) as calls:
            deleted = db_writes.delete_in_batches(
                KeywordDayRead.objects.exclude(term="term 0"), batch=2)
        self.assertEqual(deleted, 6)
        self.assertEqual(calls.call_count, 3, "6 rows in batches of 2")
        self.assertEqual(list(KeywordDayRead.objects.values_list("term", flat=True)), ["term 0"])

    def test_update_in_batches(self):
        updated = db_writes.update_in_batches(KeywordDayRead.objects.all(), batch=3, competitors=[])
        self.assertEqual(updated, 7)
        self.assertFalse(KeywordDayRead.objects.exclude(competitors=[]).exists())


class RefreshRetriesABusyDatabaseTest(TestCase):
    """If a writer ever holds the database past the 30 s wait, the keyword
    is tried again a moment later instead of missing its update."""

    def setUp(self):
        self.keyword = Keyword.objects.create(keyword="habit tracker")
        patcher = mock.patch("aso.scheduler.time.sleep")
        self.sleep = patcher.start()
        self.addCleanup(patcher.stop)

    def _refresh(self, *outcomes):
        with mock.patch("aso.keyword_scoring.score_keyword_pair", side_effect=list(outcomes)) as score:
            return scheduler._refresh_pair(self.keyword, "us"), score

    def test_a_busy_database_is_tried_again_once(self):
        result, score = self._refresh(OperationalError("database is locked"), "row")
        self.assertEqual(result, "row")
        self.assertEqual(score.call_count, 2)
        self.sleep.assert_called_once_with(scheduler.DATABASE_BUSY_RETRY_SECONDS)

    def test_a_second_busy_answer_or_another_error_is_raised(self):
        with self.assertRaises(OperationalError):
            self._refresh(OperationalError("database is locked"), OperationalError("database is locked"))
        with self.assertRaises(OperationalError):
            self._refresh(OperationalError("no such table: aso_keyword"))
