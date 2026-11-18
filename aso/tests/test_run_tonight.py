"""Run tonight (STORE_AUTOPILOT_PLAN.md, step 13): a run started with "Run
tonight" waits in the queue until the next 02:00 in the user's time zone.

The dispatcher passes over it until then and starts every other run as
before, the daily refresh does not wait for it, the scheduler wakes at its
hour, "Run next" brings it forward, and in the Mac app it says "Your report
is ready" when it ends. Free-edition models only, so these run in the public
repository too; the Pro starts are in aso_pro/tests/test_run_tonight_pro.py.
"""

from datetime import UTC, datetime, timedelta
from unittest import mock
from zoneinfo import ZoneInfo

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aso import desktop_bridge, run_queue, scheduler
from aso.models import KeywordSearchJob, OpportunityScan
from aso.tests.test_desktop_bridge import FakeBackend
from aso.tests.test_search_jobs import JobTestBase, free_tier

STOCKHOLM = "Europe/Stockholm"


def stockholm(*args):
    return datetime(*args, tzinfo=ZoneInfo(STOCKHOLM))


class WhenTest(SimpleTestCase):
    def test_now_and_tonight_in_any_case_and_spacing(self):
        for value, expected in ((None, "now"), ("", "now"), ("now", "now"), (" Tonight ", "tonight"),
                                ("TONIGHT", "tonight")):
            with self.subTest(value=value):
                self.assertEqual(run_queue.parse_when(value), expected)

    def test_anything_else_is_refused(self):
        for value in ("later", "2am", "tomorrow"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                run_queue.parse_when(value)

    def test_a_start_that_meant_nothing_starts_now(self):
        self.assertIsNone(run_queue.not_before_for("later"))
        self.assertIsNone(run_queue.not_before_for(None))


@override_settings(LOCAL_DAY_FALLBACK_ZONE=STOCKHOLM)
class TonightTest(SimpleTestCase):
    def test_the_next_two_oclock_in_the_users_zone(self):
        cases = (
            (stockholm(2026, 11, 13, 14, 0), stockholm(2026, 11, 14, 2, 0)),   # afternoon: tonight
            (stockholm(2026, 11, 14, 0, 30), stockholm(2026, 11, 14, 2, 0)),   # after midnight: in 90 minutes
            (stockholm(2026, 11, 14, 2, 0), stockholm(2026, 11, 15, 2, 0)),    # at 02:00: the next night
            (stockholm(2026, 11, 14, 23, 59), stockholm(2026, 11, 15, 2, 0)),
        )
        with mock.patch("aso.local_day.ui_state.time_zone", return_value=""):
            for now, expected in cases:
                with self.subTest(now=now):
                    self.assertEqual(run_queue.tonight(now=now), expected)

    def test_a_reported_browser_zone_wins(self):
        with mock.patch("aso.local_day.ui_state.time_zone", return_value="America/Los_Angeles"), \
                mock.patch("aso.local_day.ui_state.stamp", return_value=object()):
            moment = run_queue.tonight(now=datetime(2026, 11, 13, 12, 0, tzinfo=UTC))
        self.assertEqual(moment, datetime(2026, 11, 14, 2, 0, tzinfo=ZoneInfo("America/Los_Angeles")))


class TheLaneTest(JobTestBase):
    def later(self, hours=6):
        return timezone.now() + timedelta(hours=hours)

    def claim(self):
        with run_queue._lock:
            return run_queue._claim_next()

    def test_a_run_for_tonight_is_not_claimed_before_its_hour(self):
        waiting = self.job(not_before=self.later())
        self.assertIsNone(self.claim())
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, "queued")

    def test_a_run_started_now_goes_ahead_of_one_for_tonight(self):
        tonight = self.job(not_before=self.later(), queue_rank=1)
        now = self.job(queue_rank=2)
        self.assertEqual([row.pk for _f, row in run_queue.queued_runs()], [now.pk, tonight.pk])
        _feature, pk = self.claim()
        self.assertEqual(pk, now.pk)

    def test_at_its_hour_it_is_claimed(self):
        waiting = self.job(not_before=timezone.now() - timedelta(seconds=1))
        self.assertEqual(self.claim()[1], waiting.pk)

    def test_it_is_no_work_yet_for_the_daily_refresh(self):
        self.job(not_before=self.later())
        self.assertFalse(run_queue.local_work())
        self.job()
        self.assertTrue(run_queue.local_work())

    def test_run_next_brings_it_forward(self):
        waiting = self.job(not_before=self.later())
        self.assertEqual(run_queue.move("keyword_search", waiting.pk, "top"), 1)
        waiting.refresh_from_db()
        self.assertIsNone(waiting.not_before)
        self.assertEqual(self.claim()[1], waiting.pk)

    def test_the_queue_panel_marks_it(self):
        waiting = self.job(not_before=self.later())
        queued = self.client.get(reverse("aso:queue_status")).json()["queued"]
        self.assertEqual([(q["id"], q["tonight"]) for q in queued], [(waiting.pk, True)])

    def test_moving_it_to_the_top_from_the_panel_starts_it_when_the_lane_is_free(self):
        waiting = self.job(not_before=self.later())
        answer = self.client.post(reverse("aso:queue_move"),
                                  {"feature": "keyword_search", "session_id": waiting.pk, "direction": "top"})
        self.assertEqual(answer.status_code, 200, answer.content)
        waiting.refresh_from_db()
        self.assertEqual(waiting.status, "running")

    def test_the_activity_panel_does_not_keep_asking_for_it(self):
        from aso import job_strip

        self.job(not_before=self.later())
        self.assertIsNone(job_strip.strip_state())
        self.job()
        self.assertEqual(job_strip.strip_state(), {"shown": False, "active": True})

    def test_the_wake_up_time_and_the_due_check(self):
        self.assertIsNone(run_queue.next_due_in())
        self.assertFalse(run_queue.night_runs_due())
        waiting = self.job(not_before=timezone.now() + timedelta(seconds=90))
        self.assertAlmostEqual(run_queue.next_due_in(), 90, delta=2)
        KeywordSearchJob.objects.filter(pk=waiting.pk).update(not_before=timezone.now() - timedelta(seconds=1))
        self.assertIsNone(run_queue.next_due_in())
        self.assertTrue(run_queue.night_runs_due())

    def test_the_scheduler_starts_it_at_its_hour(self):
        # A nudge left by an earlier test (connecting App Store Connect, opening Apps) would end the wait at once.
        scheduler._wake.clear()
        self.addCleanup(scheduler._wake.clear)
        self.job(not_before=timezone.now() - timedelta(seconds=1))
        with mock.patch.object(scheduler, "NIGHT_RUN_POLL_SECONDS", 0.01), \
                mock.patch("aso.scheduler.run_queue.kick") as kick:
            self.assertFalse(scheduler._wait_for_next_check(0.05))
        kick.assert_called()

    def test_the_scheduler_waits_no_longer_than_its_hour(self):
        self.job(not_before=timezone.now() + timedelta(seconds=5))
        self.assertLessEqual(scheduler._until_a_night_run(), 5.6)
        KeywordSearchJob.objects.all().delete()
        self.assertEqual(scheduler._until_a_night_run(), scheduler.NIGHT_RUN_POLL_SECONDS)


class StartingTest(JobTestBase):
    def test_pro_keyword_search_waits_for_tonight(self):
        answer = self.search("alpha\nbeta", when="tonight").json()
        job = KeywordSearchJob.objects.get(pk=answer["job"]["id"])
        self.assertEqual(job.status, "queued")
        self.assertIsNotNone(job.not_before)
        self.assertTrue(answer["job"]["tonight"])
        self.assertIsNone(answer["queued_behind"])

    def test_the_free_edition_starts_now(self):
        with free_tier():
            answer = self.search("alpha", when="tonight").json()
        job = KeywordSearchJob.objects.get(pk=answer["job"]["id"])
        self.assertIsNone(job.not_before)
        self.assertFalse(answer["job"]["tonight"])

    def test_pro_country_scan_waits_for_tonight(self):
        answer = self.client.post(reverse("aso:opportunity_start"),
                                  {"keyword": "habit tracker", "countries": "us,de", "when": "tonight"}).json()
        scan = OpportunityScan.objects.get(pk=answer["scan"]["id"])
        self.assertEqual(scan.status, "queued")
        self.assertIsNotNone(scan.not_before)
        self.assertTrue(answer["scan"]["tonight"])

    def test_the_forms_offer_it_to_pro_only(self):
        for name in ("aso:dashboard", "aso:opportunity"):
            with self.subTest(page=name):
                self.assertIn('data-when="tonight"', self.client.get(reverse(name)).content.decode())
                with override_settings(DEBUG_SKIP_LICENSE=False), \
                        mock.patch("aso.pro_access.has_pro_license", return_value=False), \
                        mock.patch("aso.opportunity_views.has_pro_license", return_value=False), \
                        mock.patch("aso.search_jobs.has_pro_license", return_value=False):
                    self.assertNotIn('data-when="tonight"', self.client.get(reverse(name)).content.decode())


class ReadyNoticeTest(TestCase):
    def setUp(self):
        self.backend = FakeBackend()
        desktop_bridge.register_backend(self.backend)
        self.addCleanup(desktop_bridge.unregister_backend)
        self.feature = run_queue.get_feature("keyword_search")

    def job(self, **fields):
        return KeywordSearchJob.objects.create(keywords=["alpha"], countries=["us"], **fields)

    def test_a_night_run_that_ended_well_says_so(self):
        job = self.job(status="completed", not_before=timezone.now())
        self.assertTrue(run_queue.report_ready(self.feature, job.pk))
        self.assertEqual(self.backend.sent, [("Your report is ready", self.feature.describe(job)["label"], "")])

    def test_quiet_for_a_run_started_now_a_failed_run_and_outside_the_mac_app(self):
        self.assertFalse(run_queue.report_ready(self.feature, self.job(status="completed").pk))
        self.assertFalse(run_queue.report_ready(self.feature, self.job(status="failed",
                                                                       not_before=timezone.now()).pk))
        desktop_bridge.unregister_backend()
        self.assertFalse(run_queue.report_ready(self.feature, self.job(status="completed",
                                                                       not_before=timezone.now()).pk))
        self.assertEqual(self.backend.sent, [])

    def test_the_worker_sends_it_when_the_run_ends(self):
        job = self.job(status="running", not_before=timezone.now())

        def execute(pk):
            KeywordSearchJob.objects.filter(pk=pk).update(status="completed")

        import dataclasses
        with mock.patch("aso.run_queue.kick"):
            run_queue._worker(dataclasses.replace(self.feature, execute=execute), job.pk)
        self.assertEqual([title for title, _body, _path in self.backend.sent], ["Your report is ready"])

    def test_the_first_night_run_asks_macos_once_for_permission(self):
        self.backend.permission = "not_determined"
        self.assertIsNotNone(run_queue.not_before_for("tonight"))
        self.assertEqual(self.backend.requested, 1)
        self.backend.permission = "granted"
        run_queue.not_before_for("tonight")
        self.assertEqual(self.backend.requested, 1)
