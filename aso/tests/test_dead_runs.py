"""A run whose process ended never holds the lane (aso/run_queue.py
release_dead_runs, aso/run_owner.py).

The owner, 2026-10-07: an AI assistant closed during an MCP run left its row
"running", and every search and AI run started in the app waited behind it
until RespectASO restarted (reproduced with the real MCP server over stdio:
a keyword search stayed "waiting for Rival Tracker keyword suggestions").
Runs started by the MCP server, or claimed by it from the queue, now name
their process; when that process has ended its runs are released the way a
restart releases them.
"""

from unittest import mock

from django.test import TestCase

from aso import opportunity_scans, run_owner, run_queue, search_jobs
from aso.models import KeywordSearchJob, OpportunityScan
from aso.tests.test_run_owner import OtherProcess


class DeadRunsTest(TestCase):
    def setUp(self):
        self.addCleanup(run_queue._active.clear)
        self.addCleanup(run_queue._claimed.clear)
        self.ended = OtherProcess("mcp-700001-0badf00d")
        self.ended.end()

    def search(self, **fields):
        defaults = {"keywords": ["habit tracker"], "countries": ["us"]}
        return KeywordSearchJob.objects.create(**{**defaults, **fields})

    def test_a_search_an_ended_mcp_server_held_goes_back_to_the_front(self):
        held = self.search(status="running", run_owner=self.ended.owner, next_index=1)
        waiting = self.search(queue_rank=1)
        self.assertEqual(run_queue.release_dead_runs(), 1)
        held.refresh_from_db()
        self.assertEqual((held.status, held.next_index, held.restart_resumes), ("queued", 1, 1))
        self.assertEqual([row.pk for _f, row in run_queue.queued_runs()], [held.pk, waiting.pk])

    def test_the_lane_opens_and_the_app_takes_the_work_over(self):
        held = self.search(status="running", run_owner=self.ended.owner)
        with mock.patch("aso.run_queue.threading.Thread"):
            self.assertEqual(run_queue.kick(), held.pk)
        held.refresh_from_db()
        self.assertEqual((held.status, held.run_owner), ("running", run_owner.token()))

    def test_no_screen_shows_it_as_running(self):
        self.search(status="running", run_owner=self.ended.owner)
        self.assertIsNone(run_queue.running_run())
        self.assertEqual(search_jobs.panel_job().status, "queued")

    def test_a_country_scan_too(self):
        scan = OpportunityScan.objects.create(keyword="habit tracker", countries=["us", "de"],
                                              status="running", run_owner=self.ended.owner, next_index=1)
        self.assertEqual(opportunity_scans.panel_scan().pk, scan.pk)
        scan.refresh_from_db()
        self.assertEqual((scan.status, scan.next_index), ("queued", 1))

    def test_a_live_mcp_run_is_left_to_it(self):
        live = OtherProcess("mcp-700002-c0ffee00")
        self.addCleanup(live.end)
        held = self.search(status="running", run_owner=live.owner)
        self.assertEqual(run_queue.release_dead_runs(), 0)
        self.assertEqual(run_queue.running_run()[1].pk, held.pk)

    def test_a_run_from_before_owners_waits_for_the_restart(self):
        held = self.search(status="running")
        self.assertEqual(run_queue.release_dead_runs(), 0)
        held.refresh_from_db()
        self.assertEqual(held.status, "running")


class AppStartTest(TestCase):
    """resume_after_startup(): the app opening while an MCP run is at work
    used to mark that run failed while it went on."""

    def setUp(self):
        self.addCleanup(run_queue._claimed.clear)

    def test_a_live_mcp_run_survives_the_app_starting(self):
        live = OtherProcess("mcp-700003-5eed5eed")
        self.addCleanup(live.end)
        held = KeywordSearchJob.objects.create(keywords=["a"], countries=["us"], status="running",
                                               run_owner=live.owner)
        with mock.patch("aso.run_queue.kick"):
            run_queue.resume_after_startup()
        held.refresh_from_db()
        self.assertEqual(held.status, "running")

    def test_what_ended_processes_left_is_resumed(self):
        ended = OtherProcess("mcp-700004-0ddba11")
        ended.end()
        rows = [KeywordSearchJob.objects.create(keywords=["a"], countries=["us"], status="running",
                                                run_owner=owner) for owner in ("", ended.owner)]
        with mock.patch("aso.run_queue.kick"):
            run_queue.resume_after_startup()
        self.assertEqual({KeywordSearchJob.objects.get(pk=row.pk).status for row in rows}, {"queued"})
