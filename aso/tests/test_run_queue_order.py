"""The run queue's ordering: ranks, Move up / down / Run next, Run now and
the automatic return of a search that stepped aside.

Keyword jobs only (no aso_pro models), so this runs in the Free build too;
the cross-feature cases live in aso_pro/tests/test_search_limits.py.
"""

from unittest import mock

from django.test import TestCase, override_settings

from aso import run_queue
from aso.models import KeywordSearchJob


@override_settings(DEBUG_SKIP_LICENSE=True)
class QueueOrderTest(TestCase):
    def setUp(self):
        thread_patch = mock.patch("aso.run_queue.threading.Thread")
        self.mock_thread = thread_patch.start()
        self.addCleanup(thread_patch.stop)
        self.addCleanup(run_queue._active.clear)

    def job(self, **fields):
        defaults = dict(keywords=["a", "b"], countries=["us"], status="queued")
        defaults.update(fields)
        return KeywordSearchJob.objects.create(**defaults)

    def order(self):
        return [row.pk for _f, row in run_queue.queued_runs()]

    def ranks(self):
        return {row.pk: row.queue_rank for _f, row in run_queue.queued_runs()}

    def test_unranked_rows_get_ranks_in_creation_order_at_kick(self):
        self.job(status="running")
        first, second, third = self.job(), self.job(), self.job()
        self.assertEqual(self.order(), [first.pk, second.pk, third.pk])
        run_queue.kick()
        self.assertEqual(self.ranks(), {first.pk: 1, second.pk: 2, third.pk: 3})

    def test_move_renumbers_and_returns_the_position(self):
        self.job(status="running")
        a, b, c = self.job(queue_rank=1), self.job(queue_rank=2), self.job(queue_rank=3)
        self.assertEqual(run_queue.move("keyword_search", c.pk, "up"), 2)
        self.assertEqual(self.order(), [a.pk, c.pk, b.pk])
        self.assertEqual(run_queue.move("keyword_search", a.pk, "down"), 2)
        self.assertEqual(self.order(), [c.pk, a.pk, b.pk])
        self.assertEqual(run_queue.move("keyword_search", b.pk, "top"), 1)
        self.assertEqual(self.order(), [b.pk, c.pk, a.pk])
        self.assertEqual(self.ranks(), {b.pk: 1, c.pk: 2, a.pk: 3})
        self.assertEqual(run_queue.move("keyword_search", b.pk, "up"), 1)     # already first
        self.assertIsNone(run_queue.move("keyword_search", 999, "up"))
        self.assertIsNone(run_queue.move("keyword_search", b.pk, "sideways"))

    def test_run_now_pauses_the_running_search_and_brings_it_back_first(self):
        long = self.job(status="running", keywords=["a", "b", "c"], next_index=1)
        other = self.job(queue_rank=1)
        quick = self.job(queue_rank=2, keywords=["z"])

        result = run_queue.run_now("keyword_search", quick.pk)
        self.assertEqual(result, {"position": 1, "yielded": True})
        long.refresh_from_db()
        quick.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(long.status, "paused")
        self.assertTrue(long.auto_resume)
        self.assertEqual(long.progress_message, run_queue.YIELDED_MESSAGE)
        self.assertEqual((long.yielded_for_feature, long.yielded_for_id), ("keyword_search", quick.pk))
        self.assertEqual(quick.status, "running")      # claimed at once: no worker thread holds the lane
        self.assertEqual(other.status, "queued")

        # While the quick search runs, the long one stays aside.
        run_queue.kick()
        long.refresh_from_db()
        self.assertEqual(long.status, "paused")

        # The quick search ends: the long one comes back ahead of everything.
        KeywordSearchJob.objects.filter(pk=quick.pk).update(status="completed")
        run_queue.kick()
        long.refresh_from_db()
        other.refresh_from_db()
        self.assertEqual(long.status, "running")
        self.assertFalse(long.auto_resume)
        self.assertEqual(long.next_index, 1)           # progress kept
        self.assertEqual(other.status, "queued")

    def test_a_removed_quick_search_releases_the_long_one(self):
        long = self.job(status="running")
        quick = self.job(queue_rank=1)
        run_queue.run_now("keyword_search", quick.pk)
        # The quick one is running now; discard it before it does anything.
        KeywordSearchJob.objects.filter(pk=quick.pk).delete()
        run_queue.kick()
        long.refresh_from_db()
        self.assertEqual(long.status, "running")

    def test_run_now_needs_a_queued_row(self):
        running = self.job(status="running")
        self.assertIsNone(run_queue.run_now("keyword_search", running.pk))
        self.assertIsNone(run_queue.run_now("keyword_search", 999))
        self.assertIsNone(run_queue.run_now("nope", running.pk))

    def test_a_manual_resume_of_a_yielded_search_goes_to_the_back(self):
        yielded = self.job(status="paused", auto_resume=True,
                           yielded_for_feature="keyword_search", yielded_for_id=1)
        self.job(status="running")
        waiting = self.job(queue_rank=1)
        KeywordSearchJob.objects.filter(pk=yielded.pk).update(
            status="queued", auto_resume=False, queue_rank=None)
        self.assertEqual(self.order(), [waiting.pk, yielded.pk])

    def test_clear_queue_keeps_half_done_work(self):
        self.job(status="running")
        fresh = self.job()
        continued = self.job(next_index=1)
        self.assertEqual(run_queue.clear_queued(), 2)
        self.assertFalse(KeywordSearchJob.objects.filter(pk=fresh.pk).exists())
        continued.refresh_from_db()
        self.assertEqual(continued.status, "paused")

    def test_a_busy_probe_holds_the_lane(self):
        self.job()
        with mock.patch("aso.scheduler.get_status", return_value={"running": True}):
            self.assertIsNone(run_queue.kick())
            self.assertEqual(run_queue.busy_reason(), "the ranking refresh")
        self.assertIsNotNone(run_queue.kick())
