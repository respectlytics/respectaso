"""Keyword searches as background jobs (GitHub respectlytics/respectaso#23).

A search from the Keyword Research tab is a KeywordSearchJob the run queue
executes one pair at a time; it can be paused, resumed, discarded and
continued after a restart. These tests cover parsing and the limits, the
search endpoint, the worker (order, skips, failures, pause, throttling), the
startup rules and the job endpoints. Free-tier only (no aso_pro models), so
they run in the public repo too.

No real background work: threading.Thread is patched out, except where a
test swaps in the inline _SyncThread to run the worker.
"""

from unittest import mock

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aso import run_queue, search_jobs
from aso.keyword_scoring import result_payload
from aso.models import App, Keyword, KeywordSearchJob, SearchResult
from aso.services import ITunesRateLimited, SearchAPIUnavailableError
from aso.tests.helpers import _SyncThread


def fake_competitors(n=10):
    return [
        {"trackId": i, "trackName": f"Competitor {i}", "userRatingCount": 1000 * (i + 1),
         "sellerName": f"Seller {i}"}
        for i in range(n)
    ]


def free_tier():
    """Patch both places that ask whether Pro is unlocked."""
    return mock.patch.multiple(
        "aso.search_jobs", has_pro_license=mock.Mock(return_value=False),
    )


@override_settings(DEBUG_SKIP_LICENSE=True)
class JobTestBase(TestCase):
    """Apple, sleeps and threads patched; the lane is left clean."""

    def setUp(self):
        thread_patch = mock.patch("aso.run_queue.threading.Thread")
        self.mock_thread = thread_patch.start()
        self.addCleanup(thread_patch.stop)
        self.addCleanup(run_queue._active.clear)
        for target in ("aso.throttle.time.sleep", "aso.search_jobs.time.sleep"):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        itunes_patch = mock.patch("aso.search_jobs.ITunesSearchService")
        self.itunes = itunes_patch.start().return_value
        self.addCleanup(itunes_patch.stop)
        self.itunes.search_apps.return_value = fake_competitors()
        self.itunes.find_app_rank.return_value = None

    def job(self, keywords=("alpha", "beta"), countries=("us",), **fields):
        defaults = dict(keywords=list(keywords), countries=list(countries), status="queued")
        defaults.update(fields)
        return KeywordSearchJob.objects.create(**defaults)

    def run_inline(self):
        return mock.patch("aso.run_queue.threading.Thread", _SyncThread)

    def search(self, keywords, countries="us", **extra):
        return self.client.post(reverse("aso:search"),
                                {"keywords": keywords, "countries": countries, **extra})

    def searched_pairs(self):
        return [(call.args[0], call.kwargs["country"]) for call in self.itunes.search_apps.call_args_list]


class ParseAndLimitTest(JobTestBase):
    def test_parse_splits_on_commas_and_newlines_and_dedupes(self):
        raw = "Meditation App,  fitness tracker\n\nsleep   sounds\r\nmeditation app, Fitness Tracker ,"
        self.assertEqual(search_jobs.parse_keywords(raw),
                         ["Meditation App", "fitness tracker", "sleep sounds"])

    def test_limit_is_1000_with_pro_and_3_without(self):
        self.assertEqual(search_jobs.keyword_limit(), 1000)
        with override_settings(DEBUG_SKIP_LICENSE=False), \
             mock.patch("aso.pro_access.django_apps.is_installed", return_value=False):
            self.assertEqual(search_jobs.keyword_limit(), 3)

    def test_limit_context_names_where_to_get_pro(self):
        self.assertIsNone(search_jobs.limit_context()["upgrade_url"])
        with free_tier():
            context = search_jobs.limit_context()
        self.assertEqual(context["limit"], 3)
        self.assertIn(context["upgrade_label"], ("Activate Pro", "Get Pro"))
        self.assertTrue(context["upgrade_url"])


class SearchViewTest(JobTestBase):
    def test_over_the_pro_limit_is_an_error_with_the_number_never_a_cut(self):
        keywords = ", ".join(f"kw{i}" for i in range(1204))
        resp = self.search(keywords)
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["error"],
                         "That is 1,204 keywords. A search holds up to 1,000 - start a second search for the rest.")
        self.assertEqual((data["count"], data["limit"]), (1204, 1000))
        self.assertEqual(KeywordSearchJob.objects.count(), 0)

    def test_over_the_free_limit_points_at_pro(self):
        with free_tier():
            resp = self.search("a, b, c, d")
        self.assertEqual(resp.status_code, 400)
        data = resp.json()
        self.assertEqual(data["error"], "That is 4 keywords. The free version runs up to 3 per search.")
        self.assertTrue(data["upgrade_url"])
        self.assertEqual(KeywordSearchJob.objects.count(), 0)

    def test_free_runs_one_search_at_a_time(self):
        self.job(status="running")
        with free_tier():
            resp = self.search("a")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], search_jobs.FREE_BUSY_MESSAGE)

    def test_free_can_search_three(self):
        with free_tier():
            resp = self.search("a, b, c")
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(resp.json()["job"]["status"], "running")

    def test_success_creates_a_job_and_starts_it_when_the_lane_is_idle(self):
        app = App.objects.create(name="My App", track_id=42)
        resp = self.search("Meditation App\nfitness tracker", countries="us,de", app_id=str(app.pk))
        self.assertEqual(resp.status_code, 200, resp.content)
        data = resp.json()
        job = KeywordSearchJob.objects.get(pk=data["job"]["id"])
        self.assertEqual(job.keywords, ["Meditation App", "fitness tracker"])
        self.assertEqual(job.countries, ["us", "de"])
        self.assertEqual(job.app, app)
        self.assertEqual(job.status, "running")
        self.assertIsNone(data["queued_behind"])
        self.mock_thread.return_value.start.assert_called_once()

    def test_a_second_search_queues_behind_the_running_one(self):
        self.job(status="running", seconds_per_pair=4.0, keywords=["a"] * 1, countries=["us"])
        resp = self.search("zeta")
        data = resp.json()
        self.assertEqual(data["job"]["status"], "queued")
        self.assertEqual(data["job"]["queue_position"], 1)
        self.assertEqual(data["queued_behind"], "the current search")
        self.assertTrue(data["job"]["can_run_now"])
        self.assertEqual(data["job"]["waiting_for"], "the current search")

    def test_a_search_waits_for_the_ranking_refresh(self):
        with mock.patch("aso.scheduler.get_status", return_value={"running": True}):
            resp = self.search("zeta")
            data = resp.json()
        self.assertEqual(data["job"]["status"], "queued")
        self.assertEqual(data["queued_behind"], "the ranking refresh")
        self.assertEqual(data["job"]["waiting_for"], "the ranking refresh")
        self.assertFalse(data["job"]["can_run_now"])

    def test_run_now_puts_a_quick_search_first(self):
        long = self.job(status="running", keywords=["a", "b"], next_index=1)
        resp = self.search("zeta", run_now="1")
        self.assertEqual(resp.status_code, 200, resp.content)
        long.refresh_from_db()
        self.assertEqual(long.status, "paused")
        self.assertTrue(long.auto_resume)
        quick = KeywordSearchJob.objects.get(pk=resp.json()["job"]["id"])
        self.assertEqual(quick.status, "running")
        self.assertEqual(long.yielded_for_id, quick.pk)
        self.assertEqual(long.yielded_for_label, "1 keyword (US)")


class WorkerTest(JobTestBase):
    def test_pairs_run_keyword_major_and_finish_cleanly(self):
        job = self.job(keywords=["a", "b"], countries=["us", "de"])
        with self.run_inline():
            run_queue.kick()
        self.assertEqual(self.searched_pairs(), [("a", "us"), ("a", "de"), ("b", "us"), ("b", "de")])
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")
        self.assertEqual((job.next_index, job.done_count, job.failed_count, job.skipped_count), (4, 4, 0, 0))
        self.assertEqual(job.progress_message, "Done")
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(job.current_pair, "")
        self.assertEqual(SearchResult.objects.count(), 4)
        self.assertEqual(Keyword.objects.filter(keyword="a").count(), 1)

    def test_a_pair_already_searched_today_is_skipped_and_reported(self):
        keyword = Keyword.objects.create(keyword="a")
        SearchResult.objects.create(keyword=keyword, country="us", difficulty_score=30)
        job = self.job(keywords=["a", "b"], countries=["us"])
        with self.run_inline():
            run_queue.kick()
        job.refresh_from_db()
        self.assertEqual(self.searched_pairs(), [("b", "us")])
        self.assertEqual(job.skipped_items, ["a (US)"])
        self.assertEqual((job.done_count, job.skipped_count), (1, 1))
        payload = search_jobs.job_payload(job)
        self.assertEqual(payload["warning"],
                         "Skipped 1 keyword already in your list today: a (US). Use Refresh to update them.")

    def test_one_failing_pair_never_ends_the_search(self):
        self.itunes.search_apps.side_effect = [
            fake_competitors(), SearchAPIUnavailableError("down"), fake_competitors(),
        ]
        job = self.job(keywords=["a", "b", "c"], countries=["us"])
        with self.run_inline():
            run_queue.kick()
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")
        self.assertEqual((job.done_count, job.failed_count), (2, 1))
        self.assertEqual(job.failed_items, [{"keyword": "b", "country": "us", "error": "down"}])
        payload = search_jobs.job_payload(job, include_results=True)
        self.assertEqual(payload["failed_text"], "Could not check 1 keyword: b (US).")
        self.assertEqual(payload["results_total"], 2)

    def test_every_pair_writes_the_cursor_and_the_ticker(self):
        seen = []

        def search(keyword, country, limit):
            row = KeywordSearchJob.objects.get()
            seen.append((row.next_index, row.current_pair))
            return fake_competitors()

        self.itunes.search_apps.side_effect = search
        self.job(keywords=["a", "b"], countries=["us"])
        with self.run_inline():
            run_queue.kick()
        self.assertEqual(seen, [(0, "a (US)"), (1, "b (US)")])

    def test_the_app_can_disappear_mid_run(self):
        app = App.objects.create(name="Gone", track_id=7)
        job = self.job(keywords=["a", "b"], countries=["us"], app=app)

        def search(keyword, country, limit):
            if keyword == "a":
                App.objects.filter(pk=app.pk).delete()
            return fake_competitors()

        self.itunes.search_apps.side_effect = search
        with self.run_inline():
            run_queue.kick()
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")
        self.assertIsNone(Keyword.objects.get(keyword="b").app_id)


class PauseResumeDiscardTest(JobTestBase):
    def _pause_after(self, job, calls):
        def search(keyword, country, limit):
            if self.itunes.search_apps.call_count == calls:
                KeywordSearchJob.objects.filter(pk=job.pk).update(status="paused")
            return fake_competitors()
        self.itunes.search_apps.side_effect = search

    def test_pause_stops_at_the_next_keyword_boundary_and_resume_continues_there(self):
        job = self.job(keywords=["a", "b", "c", "d"], countries=["us"])
        self._pause_after(job, 2)
        with self.run_inline():
            run_queue.kick()
        job.refresh_from_db()
        self.assertEqual(job.status, "paused")
        self.assertEqual(job.next_index, 2)          # the pair that finished still counted
        self.assertEqual(job.done_count, 2)
        self.assertEqual(search_jobs.job_payload(job, include_results=True)["remaining_keywords"], ["c", "d"])

        self.itunes.search_apps.side_effect = None
        with self.run_inline():
            resp = self.client.post(reverse("aso:search_job_resume", args=[job.pk]))
        self.assertEqual(resp.status_code, 200, resp.content)
        self.assertEqual(self.searched_pairs()[2], ("c", "us"))
        job.refresh_from_db()
        self.assertEqual(job.status, "completed")
        self.assertEqual(job.done_count, 4)

    def test_a_pair_finishing_after_the_pause_keeps_the_pause_message(self):
        job = self.job(keywords=["a", "b", "c"], countries=["us"])

        def search(keyword, country, limit):
            if keyword == "b":   # the user presses Pause while Apple answers for "b"
                self.client.post(reverse("aso:search_job_pause", args=[job.pk]))
            return fake_competitors()

        self.itunes.search_apps.side_effect = search
        with self.run_inline():
            run_queue.kick()
        job.refresh_from_db()
        self.assertEqual(job.status, "paused")
        self.assertEqual(job.next_index, 2)          # "b" counted
        self.assertEqual(job.progress_message, "Paused")
        self.assertEqual(job.current_pair, "")

    def test_pause_endpoint_refuses_a_search_that_is_not_running(self):
        job = self.job(status="paused")
        resp = self.client.post(reverse("aso:search_job_pause", args=[job.pk]))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "This search is not running.")

    def test_pause_endpoint_flips_a_running_search(self):
        job = self.job(status="running", next_index=1, keywords=["a", "b"])
        resp = self.client.post(reverse("aso:search_job_pause", args=[job.pk]))
        self.assertEqual(resp.status_code, 200)
        job.refresh_from_db()
        self.assertEqual(job.status, "paused")
        self.assertEqual(job.next_index, 1)

    def test_discard_ends_a_paused_search_and_keeps_what_was_researched(self):
        job = self.job(status="paused", keywords=["a", "b", "c"], next_index=1, done_count=1)
        resp = self.client.post(reverse("aso:search_job_discard", args=[job.pk]))
        self.assertEqual(resp.status_code, 200, resp.content)
        job.refresh_from_db()
        self.assertEqual(job.status, "cancelled")
        self.assertIsNotNone(job.finished_at)
        self.assertEqual(job.next_index, 1)
        self.assertEqual(resp.json()["job"]["remaining_count"], 2)

    def test_discard_is_refused_while_running(self):
        job = self.job(status="running")
        resp = self.client.post(reverse("aso:search_job_discard", args=[job.pk]))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Pause the search before discarding the rest.")
        job.refresh_from_db()
        self.assertEqual(job.status, "running")

    def test_removing_a_queued_search_deletes_it_only_when_nothing_ran(self):
        self.job(status="running")
        fresh = self.job()
        continued = self.job(next_index=1, done_count=1, keywords=["a", "b"])
        resp = self.client.post(reverse("aso:search_job_discard", args=[fresh.pk]))
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(KeywordSearchJob.objects.filter(pk=fresh.pk).exists())
        self.client.post(reverse("aso:search_job_discard", args=[continued.pk]))
        continued.refresh_from_db()
        self.assertEqual(continued.status, "paused")

    def test_an_error_outside_the_pair_handling_pauses_instead_of_failing(self):
        job = self.job()
        with self.run_inline(), \
             mock.patch("aso.search_jobs.prefetch_apple_values", side_effect=RuntimeError("disk full")), \
             self.assertLogs("aso.search_jobs", level="ERROR"):
            run_queue.kick()
        job.refresh_from_db()
        self.assertEqual(job.status, "paused")
        self.assertEqual(job.error_message, "disk full")
        self.assertEqual(job.progress_message, "Paused after an error")
        payload = search_jobs.job_payload(job)
        self.assertEqual(payload["status"], "paused")

    def test_retry_failed_creates_a_search_with_exactly_the_failed_keywords(self):
        job = self.job(status="completed", keywords=["a", "b", "c"], countries=["us", "de"],
                       next_index=6, finished_at=timezone.now(), failed_count=3,
                       failed_items=[{"keyword": "b", "country": "us", "error": "x"},
                                     {"keyword": "b", "country": "de", "error": "x"},
                                     {"keyword": "c", "country": "de", "error": "x"}])
        resp = self.client.post(reverse("aso:search_job_retry_failed", args=[job.pk]))
        self.assertEqual(resp.status_code, 200, resp.content)
        new = KeywordSearchJob.objects.get(pk=resp.json()["job"]["id"])
        self.assertEqual(new.keywords, ["b", "c"])
        self.assertEqual(new.countries, ["us", "de"])
        job.refresh_from_db()
        self.assertTrue(job.acknowledged)

    def test_retry_failed_needs_something_to_retry(self):
        job = self.job(status="completed", finished_at=timezone.now())
        resp = self.client.post(reverse("aso:search_job_retry_failed", args=[job.pk]))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Nothing to search again.")


class ThrottleTest(JobTestBase):
    def test_repeated_rejections_cool_down_and_finally_pause_with_a_message(self):
        self.itunes.search_apps.side_effect = ITunesRateLimited("slow down", retry_after=5)
        job = self.job(keywords=[f"kw{i}" for i in range(30)], countries=["us"])
        with self.run_inline(), mock.patch("aso.search_jobs._cooldown", return_value=True) as cooldown:
            run_queue.kick()
        job.refresh_from_db()
        self.assertEqual(cooldown.call_count, search_jobs.MAX_COOLDOWNS)
        self.assertEqual(job.status, "paused")
        self.assertEqual(job.throttle_state, "paused")
        self.assertTrue(job.progress_message.startswith("Apple rejected "), job.progress_message)
        self.assertTrue(job.progress_message.endswith("Wait a few minutes, then press Resume."))
        self.assertEqual(job.failed_count, job.next_index)
        self.assertLess(job.next_index, 30)

    def test_a_cool_down_stops_when_the_search_is_paused(self):
        job = self.job(status="running")
        with mock.patch("aso.search_jobs._status_and_app", return_value=("paused", None)):
            self.assertFalse(search_jobs._cooldown(job.pk))
        with mock.patch("aso.search_jobs._status_and_app", return_value=("running", None)):
            self.assertTrue(search_jobs._cooldown(job.pk))


class StartupTest(JobTestBase):
    def test_a_search_left_running_continues_from_where_it_was(self):
        waiting = self.job(queue_rank=1)
        interrupted = self.job(status="running", next_index=3, done_count=3,
                               keywords=["a", "b", "c", "d"], countries=["us"])
        paused = self.job(status="paused", next_index=1)
        run_queue.resume_after_startup()
        interrupted.refresh_from_db()
        waiting.refresh_from_db()
        paused.refresh_from_db()
        self.assertEqual(interrupted.status, "running")     # claimed first: it was executing
        self.assertEqual(interrupted.next_index, 3)
        self.assertEqual(interrupted.restart_resumes, 1)
        self.assertEqual(waiting.status, "queued")
        self.assertEqual(paused.status, "paused")
        self.mock_thread.return_value.start.assert_called_once()
        self.assertTrue(search_jobs.job_payload(interrupted)["restart_resumes"])

    def test_the_gunicorn_worker_resumes_and_the_test_runner_does_not(self):
        should = run_queue.should_resume_on_ready
        self.assertTrue(should(["/usr/local/bin/gunicorn", "core.wsgi:application"], {}, False))
        self.assertFalse(should(["manage.py", "test"], {}, False))
        self.assertFalse(should(["/usr/local/bin/gunicorn"], {}, True))

    def test_remove_from_queue_deletes_or_pauses(self):
        fresh = self.job()
        continued = self.job(next_index=1, keywords=["a", "b"])
        self.assertTrue(run_queue.remove_queued("keyword_search", fresh.pk))
        self.assertFalse(KeywordSearchJob.objects.filter(pk=fresh.pk).exists())
        self.assertTrue(run_queue.remove_queued("keyword_search", continued.pk))
        continued.refresh_from_db()
        self.assertEqual(continued.status, "paused")
        self.assertFalse(run_queue.remove_queued("keyword_search", continued.pk))


class EndpointTest(JobTestBase):
    def test_current_reports_nothing_the_active_job_and_the_finished_one(self):
        url = reverse("aso:search_job_current")
        self.assertEqual(self.client.get(url).json(), {"job": None, "finished": None, "others": []})
        finished = self.job(status="completed", finished_at=timezone.now(), next_index=2,
                            skipped_count=1, skipped_items=["alpha (US)"])
        running = self.job(status="running")
        other = self.job(status="paused", next_index=1)
        data = self.client.get(url).json()
        self.assertEqual(data["job"]["id"], running.pk)
        self.assertEqual(data["finished"]["id"], finished.pk)
        self.assertEqual(data["finished"]["warning"],
                         "Skipped 1 keyword already in your list today: alpha (US). Use Refresh to update them.")
        self.assertEqual([o["id"] for o in data["others"]], [other.pk])
        self.client.post(reverse("aso:search_job_dismiss", args=[finished.pk]))
        self.assertIsNone(self.client.get(url).json()["finished"])

    def test_the_panel_prefers_running_over_paused_over_queued(self):
        queued = self.job()
        self.assertEqual(search_jobs.panel_job(), queued)
        paused = self.job(status="paused")
        self.assertEqual(search_jobs.panel_job(), paused)
        running = self.job(status="running")
        self.assertEqual(search_jobs.panel_job(), running)
        self.assertEqual(search_jobs.other_paused_jobs(running), [paused])

    def test_results_are_capped_at_fifty_with_the_exact_total(self):
        keywords = [f"kw{i:03d}" for i in range(60)]
        for text in keywords:
            SearchResult.objects.create(keyword=Keyword.objects.create(keyword=text),
                                        country="us", difficulty_score=40, popularity_score=50)
        job = self.job(status="completed", keywords=keywords, countries=["us"], next_index=60,
                       finished_at=timezone.now())
        resp = self.client.get(reverse("aso:search_job_detail", args=[job.pk]))
        data = resp.json()["job"]
        self.assertEqual(len(data["results"]), search_jobs.RESULT_CARD_CAP)
        self.assertEqual(data["results_total"], 60)
        self.assertEqual(data["results"][0]["keyword"], "kw000")
        self.assertEqual(data["opportunity_ranking"], [])

    def test_multi_country_results_carry_the_opportunity_ranking(self):
        for country, pop in (("us", 80), ("de", 20)):
            SearchResult.objects.create(keyword=Keyword.objects.get_or_create(keyword="a")[0],
                                        country=country, difficulty_score=40, popularity_score=pop)
        job = self.job(status="completed", keywords=["a"], countries=["us", "de"], next_index=2,
                       finished_at=timezone.now())
        data = search_jobs.job_payload(job, include_results=True)
        self.assertEqual(len(data["results"]), 2)
        self.assertEqual(data["opportunity_ranking"][0]["best_country"], "us")

    def test_resume_refuses_a_search_that_is_not_paused(self):
        job = self.job(status="running")
        resp = self.client.post(reverse("aso:search_job_resume", args=[job.pk]))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "This search is not paused.")

    def test_discard_refuses_a_finished_search(self):
        job = self.job(status="completed", finished_at=timezone.now())
        resp = self.client.post(reverse("aso:search_job_discard", args=[job.pk]))
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "This search already finished.")

    def test_result_payload_matches_what_the_cards_read(self):
        app = App.objects.create(name="My App", icon_url="https://x/icon.png")
        keyword = Keyword.objects.create(keyword="alpha", app=app)
        row = SearchResult.objects.create(
            keyword=keyword, country="us", difficulty_score=42, popularity_score=55,
            difficulty_breakdown={"x": 1}, competitors_data=fake_competitors(3), app_rank=7,
        )
        payload = result_payload(row)
        self.assertEqual(set(payload), {
            "keyword", "country", "popularity_score", "popularity_internal", "popularity_apple",
            "popularity_source", "popularity_fallback", "popularity_cap", "popularity_genre",
            "difficulty_score", "opportunity_score", "difficulty_label", "difficulty_color",
            "difficulty_breakdown", "competitors", "result_id", "app_rank", "app_name",
            "app_icon", "classification",
        })
        self.assertEqual(payload["keyword"], "alpha")
        self.assertEqual(payload["popularity_score"], 55)
        self.assertEqual(payload["popularity_internal"], 55)
        self.assertEqual(payload["difficulty_score"], 42)
        self.assertEqual(payload["difficulty_label"], row.difficulty_label)
        self.assertEqual(payload["app_rank"], 7)
        self.assertEqual(payload["app_name"], "My App")
        self.assertEqual(payload["app_icon"], "https://x/icon.png")
        self.assertEqual(payload["classification"], row.classification)
        self.assertEqual(len(payload["competitors"]), 3)

    def test_bulk_refresh_waits_for_the_search(self):
        SearchResult.objects.create(keyword=Keyword.objects.create(keyword="a"), country="us",
                                    difficulty_score=10)
        self.job(status="running")
        resp = self.client.post(reverse("aso:keywords_bulk_refresh"), {"app_id": None},
                                content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(resp.json()["error"], "Keyword Research is running. Refresh when it finishes.")


class DashboardTest(JobTestBase):
    def test_context_carries_the_job_the_limit_and_the_cleanup(self):
        job = self.job(status="running")
        resp = self.client.get(reverse("aso:dashboard"))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.context["search_job"]["job"]["id"], job.pk)
        self.assertEqual(resp.context["keyword_limit_context"]["limit"], 1000)
        self.assertIsNone(resp.context["cleanup"])
        self.assertContains(resp, 'id="search-job-panel"')
        self.assertContains(resp, 'id="keyword-counter"')
        self.assertNotContains(resp, 'id="keyword-limit-nudge"')

    def test_free_form_carries_the_nudge(self):
        with free_tier():
            resp = self.client.get(reverse("aso:dashboard"))
        self.assertContains(resp, 'id="keyword-limit-nudge"')
        self.assertContains(resp, "Pro researches up to 1,000 keywords per search")
        self.assertNotContains(resp, 'id="queue-section"')

    def test_the_keep_open_line_matches_the_edition(self):
        with override_settings(IS_NATIVE_APP=True):
            resp = self.client.get(reverse("aso:dashboard"))
        self.assertContains(resp, "Keep RespectASO open and your Mac awake: rankings refresh once a day")
        with override_settings(IS_NATIVE_APP=False):
            resp = self.client.get(reverse("aso:dashboard"))
        self.assertContains(resp, "Rankings refresh once a day while the container runs")

    def test_the_nav_guard_no_longer_fires_for_keyword_searches(self):
        from pathlib import Path

        import aso

        html = (Path(aso.__file__).parent / "templates" / "aso" / "dashboard.html").read_text()
        self.assertNotIn("searchInProgress = true", html)
        self.assertNotIn("loading-state", html)

    def test_the_strip_shows_on_other_pages_only(self):
        self.job(status="running", keywords=["a"] * 4)
        resp = self.client.get(reverse("aso:methodology"))
        self.assertContains(resp, 'id="search-job-strip"')
        self.assertContains(resp, "search-job-strip-data")
        resp = self.client.get(reverse("aso:dashboard"))
        self.assertNotContains(resp, 'id="search-job-strip"')
