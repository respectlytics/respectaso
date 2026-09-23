"""Country Opportunity Finder scans as background jobs.

A scan is one keyword across the storefronts the user picked, executed by the
run queue one country at a time. It can be paused, resumed, discarded and
continued after a restart, and it deliberately keeps its results out of the
search history until the user saves them.

Free-tier only (no aso_pro models), so these run in the public repo too.
No real background work: threading.Thread is patched out except where a test
swaps in the inline _SyncThread to run the worker.
"""

import json
from unittest import mock

from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from aso import opportunity_scans, run_queue
from aso.models import App, OpportunityScan, OpportunityScanResult, SearchResult
from aso.services import ITunesRateLimited, SearchAPIUnavailableError
from aso.tests.helpers import _SyncThread


def fake_competitors(n=10):
    return [
        {"trackId": i, "trackName": f"Competitor {i}", "userRatingCount": 1000 * (i + 1),
         "sellerName": f"Seller {i}", "primaryGenreName": "Health & Fitness"}
        for i in range(n)
    ]


def free_tier():
    return mock.patch(
        "aso.opportunity_views.has_pro_license", mock.Mock(return_value=False),
    )


@override_settings(DEBUG_SKIP_LICENSE=True)
class ScanTestBase(TestCase):
    """Apple, sleeps and threads patched; the lane is left clean."""

    def setUp(self):
        thread_patch = mock.patch("aso.run_queue.threading.Thread")
        self.mock_thread = thread_patch.start()
        self.addCleanup(thread_patch.stop)
        self.addCleanup(run_queue._active.clear)
        for target in ("aso.throttle.time.sleep", "aso.opportunity_scans.time.sleep"):
            patcher = mock.patch(target)
            patcher.start()
            self.addCleanup(patcher.stop)
        itunes_patch = mock.patch("aso.opportunity_scans.ITunesSearchService")
        self.itunes = itunes_patch.start().return_value
        self.addCleanup(itunes_patch.stop)
        self.itunes.search_apps.return_value = fake_competitors()
        self.itunes.find_app_rank.return_value = None

    def scan(self, keyword="fitness tracker", countries=("us", "de", "bg"), **fields):
        defaults = dict(keyword=keyword, countries=list(countries), status="queued")
        defaults.update(fields)
        return OpportunityScan.objects.create(**defaults)

    def run_inline(self):
        return mock.patch("aso.run_queue.threading.Thread", _SyncThread)

    def hold_lane(self):
        """Stop the queue claiming the row, so the queued state is observable.

        kick() claims a run synchronously and only then starts the worker
        thread, which these tests patch out. Without this the row would read
        "running" before the assertion.
        """
        return mock.patch("aso.run_queue.kick")

    def scanned_countries(self):
        return [call.kwargs["country"] for call in self.itunes.search_apps.call_args_list]

    def start(self, keyword="fitness tracker", countries="us,de", **extra):
        return self.client.post(
            reverse("aso:opportunity_start"),
            {"keyword": keyword, "countries": countries, **extra},
        )


class StartTest(ScanTestBase):
    def test_start_creates_a_queued_scan(self):
        with self.hold_lane():
            response = self.start(countries="us,de,bg")
        self.assertEqual(response.status_code, 200)
        scan = OpportunityScan.objects.get()
        self.assertEqual(scan.status, "queued")
        self.assertEqual(scan.countries, ["us", "de", "bg"])
        self.assertEqual(scan.keyword, "fitness tracker")

    def test_start_rejects_an_empty_keyword(self):
        response = self.start(keyword="   ")
        self.assertEqual(response.status_code, 400)
        self.assertFalse(OpportunityScan.objects.exists())

    def test_start_rejects_an_empty_country_selection(self):
        response = self.start(countries="")
        self.assertEqual(response.status_code, 400)
        self.assertIn("at least one country", response.json()["error"])

    def test_start_drops_unknown_country_codes(self):
        self.start(countries="us,zz,qq,de")
        self.assertEqual(OpportunityScan.objects.get().countries, ["us", "de"])

    def test_no_cap_on_the_number_of_countries(self):
        from aso import countries as registry

        codes = ",".join(sorted(registry.CODES))
        self.start(countries=codes)
        scan = OpportunityScan.objects.get()
        self.assertEqual(len(scan.countries), len(registry.CODES))

    def test_a_free_user_may_only_have_one_active_scan(self):
        self.scan(status="running")
        with free_tier():
            response = self.start()
        self.assertEqual(response.status_code, 400)
        self.assertIn("still running", response.json()["error"])


class WorkerTest(ScanTestBase):
    def test_scans_countries_in_order(self):
        scan = self.scan(countries=("us", "de", "bg"), status="running")
        opportunity_scans._execute(scan.pk)
        self.assertEqual(self.scanned_countries(), ["us", "de", "bg"])

    def test_writes_one_result_row_per_country(self):
        scan = self.scan(countries=("us", "de", "bg"), status="running")
        opportunity_scans._execute(scan.pk)
        self.assertEqual(scan.results.count(), 3)
        self.assertEqual(
            list(scan.results.values_list("country", flat=True)), ["us", "de", "bg"],
        )
        scan.refresh_from_db()
        self.assertEqual(scan.status, "completed")
        self.assertEqual(scan.done_count, 3)

    def test_never_writes_search_results(self):
        """The whole point of the feature: a scan does not fill the history."""
        scan = self.scan(countries=("us", "de", "bg"), status="running")
        opportunity_scans._execute(scan.pk)
        self.assertEqual(SearchResult.objects.count(), 0)

    def test_a_failed_country_is_recorded_and_the_scan_continues(self):
        self.itunes.search_apps.side_effect = [
            fake_competitors(),
            SearchAPIUnavailableError("Apple is not answering"),
            fake_competitors(),
        ]
        scan = self.scan(countries=("us", "de", "bg"), status="running")
        opportunity_scans._execute(scan.pk)
        scan.refresh_from_db()
        self.assertEqual(scan.failed_count, 1)
        self.assertEqual(scan.failed_items[0]["country"], "de")
        self.assertEqual(scan.done_count, 2)
        self.assertEqual(scan.status, "completed")
        self.assertEqual(
            list(scan.results.values_list("country", flat=True)), ["us", "bg"],
        )

    def test_a_rate_limit_counts_as_a_failure_not_a_crash(self):
        self.itunes.search_apps.side_effect = [
            ITunesRateLimited("slow down"), fake_competitors(),
        ]
        scan = self.scan(countries=("us", "de"), status="running")
        opportunity_scans._execute(scan.pk)
        scan.refresh_from_db()
        self.assertEqual(scan.failed_count, 1)
        self.assertEqual(scan.failed_items[0]["error"], "Apple rate limit")

    def test_rescanning_a_country_replaces_its_row(self):
        scan = self.scan(countries=("us", "de"), status="running")
        opportunity_scans._execute(scan.pk)
        OpportunityScan.objects.filter(pk=scan.pk).update(next_index=0, status="running")
        opportunity_scans._execute(scan.pk)
        self.assertEqual(scan.results.count(), 2)   # unique_together holds

    def test_pause_stops_at_the_next_country_boundary(self):
        scan = self.scan(countries=("us", "de", "bg"), status="running")
        original = self.itunes.search_apps.side_effect

        def pause_after_first(*args, **kwargs):
            OpportunityScan.objects.filter(pk=scan.pk).update(status="paused")
            self.itunes.search_apps.side_effect = original
            return fake_competitors()

        self.itunes.search_apps.side_effect = pause_after_first
        opportunity_scans._execute(scan.pk)
        scan.refresh_from_db()
        self.assertEqual(scan.status, "paused")
        self.assertEqual(scan.next_index, 1)        # the finished country is recorded
        self.assertEqual(scan.results.count(), 1)

    def test_resume_continues_from_the_cursor(self):
        scan = self.scan(countries=("us", "de", "bg"), status="running", next_index=2)
        opportunity_scans._execute(scan.pk)
        self.assertEqual(self.scanned_countries(), ["bg"])

    def test_the_app_rank_is_looked_up_when_an_app_is_tracked(self):
        app = App.objects.create(name="My App", track_id=12345)
        self.itunes.find_app_rank.return_value = 7
        scan = self.scan(countries=("us",), status="running", app=app)
        opportunity_scans._execute(scan.pk)
        self.assertEqual(scan.results.get().app_rank, 7)


class RestartAndQueueTest(ScanTestBase):
    def test_an_interrupted_scan_returns_to_the_front_of_the_queue(self):
        scan = self.scan(status="running", next_index=2, done_count=2)
        OpportunityScanResult.objects.create(
            scan=scan, country="us", order_index=0, keyword_text=scan.keyword,
            difficulty_score=40,
        )
        with self.hold_lane():
            run_queue.resume_after_startup()
        scan.refresh_from_db()
        self.assertEqual(scan.status, "queued")
        self.assertEqual(scan.next_index, 2)        # the cursor survives
        self.assertEqual(scan.restart_resumes, 1)
        self.assertEqual(scan.results.count(), 1)   # so do the rows

    def test_a_stale_heartbeat_is_reclaimed(self):
        stale = timezone.now() - timezone.timedelta(seconds=600)
        scan = self.scan(status="running", heartbeat_at=stale, next_index=1)
        with self.hold_lane():
            opportunity_scans.reclaim_stale()
        scan.refresh_from_db()
        self.assertEqual(scan.status, "queued")
        self.assertEqual(scan.next_index, 1)

    def test_a_fresh_heartbeat_is_left_alone(self):
        scan = self.scan(status="running", heartbeat_at=timezone.now())
        opportunity_scans.reclaim_stale()
        scan.refresh_from_db()
        self.assertEqual(scan.status, "running")

    def test_creating_a_scan_prunes_old_finished_ones(self):
        for i in range(12):
            self.scan(keyword=f"kw{i}", status="completed", finished_at=timezone.now())
        opportunity_scans.create_scan("new one", ["us"])
        self.assertEqual(
            OpportunityScan.objects.filter(status="completed").count(),
            opportunity_scans.SCAN_HISTORY_KEEP,
        )

    def test_pruning_keeps_active_scans(self):
        for i in range(12):
            self.scan(keyword=f"kw{i}", status="completed", finished_at=timezone.now())
        paused = self.scan(keyword="paused one", status="paused")
        opportunity_scans.create_scan("new one", ["us"])
        self.assertTrue(OpportunityScan.objects.filter(pk=paused.pk).exists())


class PayloadTest(ScanTestBase):
    def test_eta_is_remaining_countries_times_seconds_each(self):
        scan = self.scan(
            countries=("us", "de", "bg", "fr"), status="running",
            next_index=1, seconds_per_country=4.0,
        )
        self.assertEqual(opportunity_scans.scan_payload(scan)["eta_seconds"], 12)

    def test_no_eta_when_the_scan_is_not_running(self):
        scan = self.scan(status="paused", seconds_per_country=4.0)
        self.assertIsNone(opportunity_scans.scan_payload(scan)["eta_seconds"])

    def test_country_payload_carries_the_classification(self):
        """Regression: the old per-country endpoint never sent this, so every
        expanded row read "Moderate" whatever the data said."""
        scan = self.scan(countries=("us",), status="running")
        opportunity_scans._execute(scan.pk)
        payload = opportunity_scans.country_payload(scan.results.get())
        self.assertIn("classification", payload)
        self.assertTrue(payload["classification"])
        self.assertIn("difficulty_label", payload)

    def test_country_payload_carries_what_the_row_draws(self):
        """KEYWORD_DECISIONS_PLAN.md round 4: one Opportunity number, with
        its reason on hover, and the Downloads at #1 range and the tag in
        their own columns, as on every other table."""
        from aso.scoring import classify_keyword, opportunity_css, top_spot_range

        scan = self.scan(countries=("us",), status="running")
        opportunity_scans._execute(scan.pk)
        row = scan.results.get()
        payload = opportunity_scans.country_payload(row)
        popularity = payload["popularity"] or 0
        self.assertNotIn("opportunity_at_first", payload)
        self.assertEqual(payload["downloads_at_first"], list(top_spot_range(popularity, "us")))
        self.assertEqual(payload["classification"],
                         classify_keyword(popularity, row.difficulty_score, "us",
                                          keyword=row.keyword_text))
        self.assertEqual(payload["opportunity_css"], opportunity_css(payload["opportunity"]))
        self.assertTrue(payload["opportunity_tip"])
        self.assertTrue(payload["classification_tip"])

    def test_light_payload_omits_the_heavy_columns(self):
        scan = self.scan(countries=("us",), status="running")
        opportunity_scans._execute(scan.pk)
        light = opportunity_scans.country_payload(scan.results.get())
        self.assertNotIn("competitors", light)
        self.assertNotIn("difficulty_breakdown", light)
        heavy = opportunity_scans.country_payload(scan.results.get(), heavy=True)
        self.assertIn("competitors", heavy)
        self.assertIn("difficulty_breakdown", heavy)

    def test_results_are_ranked_by_opportunity(self):
        scan = self.scan(countries=("us", "de"), status="running")
        opportunity_scans._execute(scan.pk)
        results = opportunity_scans.scan_payload(scan, include_results=True)["results"]
        scores = [r["opportunity"] for r in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_cost_text_states_the_countries_and_the_time(self):
        text = opportunity_scans.cost_text(62)
        self.assertIn("62 countries", text)
        self.assertIn("minute", text)
        self.assertEqual(opportunity_scans.cost_text(0), "Pick at least one country")

    def test_scan_payload_shares_its_key_names_with_the_search_job(self):
        """One JavaScript core drives both, so the shared keys must match."""
        from aso import search_jobs
        from aso.models import KeywordSearchJob

        job = KeywordSearchJob.objects.create(keywords=["a"], countries=["us"])
        scan = self.scan()
        shared = {
            "id", "status", "progress_percent", "progress_message", "eta_seconds",
            "throttle_state", "auto_resume", "queue_position", "waiting_for",
            "can_run_now", "yielded_for", "failed_items", "failed_text",
            "error_message", "acknowledged", "restart_resumes", "is_native",
            "countries", "countries_text", "done_count", "failed_count",
            "remaining_count", "created_at", "finished_at",
        }
        job_keys = set(search_jobs.job_payload(job))
        scan_keys = set(opportunity_scans.scan_payload(scan))
        self.assertEqual(shared - job_keys, set())
        self.assertEqual(shared - scan_keys, set())


class EndpointTest(ScanTestBase):
    def test_current_returns_the_active_scan(self):
        scan = self.scan(status="running")
        data = self.client.get(reverse("aso:opportunity_current")).json()
        self.assertEqual(data["scan"]["id"], scan.pk)

    def test_detail_returns_ranked_results(self):
        scan = self.scan(countries=("us", "de"), status="running")
        opportunity_scans._execute(scan.pk)
        data = self.client.get(
            reverse("aso:opportunity_detail", args=[scan.pk])
        ).json()
        self.assertEqual(len(data["scan"]["results"]), 2)
        self.assertNotIn("competitors", data["scan"]["results"][0])

    def test_country_endpoint_returns_the_breakdown_and_competitors(self):
        scan = self.scan(countries=("us",), status="running")
        opportunity_scans._execute(scan.pk)
        data = self.client.get(
            reverse("aso:opportunity_country", args=[scan.pk, "us"])
        ).json()
        self.assertIn("competitors", data["country"])
        self.assertIn("difficulty_breakdown", data["country"])

    def test_country_endpoint_404s_for_a_country_not_in_the_scan(self):
        scan = self.scan(countries=("us",), status="completed")
        response = self.client.get(
            reverse("aso:opportunity_country", args=[scan.pk, "de"])
        )
        self.assertEqual(response.status_code, 404)

    def test_pause_and_resume(self):
        scan = self.scan(status="running")
        response = self.client.post(reverse("aso:opportunity_pause", args=[scan.pk]))
        self.assertEqual(response.status_code, 200)
        scan.refresh_from_db()
        self.assertEqual(scan.status, "paused")

        with self.hold_lane():
            response = self.client.post(reverse("aso:opportunity_resume", args=[scan.pk]))
        self.assertEqual(response.status_code, 200)
        scan.refresh_from_db()
        self.assertEqual(scan.status, "queued")

    def test_pause_refuses_a_scan_that_is_not_running(self):
        scan = self.scan(status="paused")
        response = self.client.post(reverse("aso:opportunity_pause", args=[scan.pk]))
        self.assertEqual(response.status_code, 400)

    def test_discard_refuses_while_running(self):
        scan = self.scan(status="running")
        response = self.client.post(reverse("aso:opportunity_discard", args=[scan.pk]))
        self.assertEqual(response.status_code, 400)

    def test_discard_cancels_a_paused_scan_and_keeps_its_rows(self):
        scan = self.scan(countries=("us", "de"), status="running")
        opportunity_scans._execute(scan.pk)
        OpportunityScan.objects.filter(pk=scan.pk).update(status="paused")
        response = self.client.post(reverse("aso:opportunity_discard", args=[scan.pk]))
        self.assertEqual(response.status_code, 200)
        scan.refresh_from_db()
        self.assertEqual(scan.status, "cancelled")
        self.assertEqual(scan.results.count(), 2)

    def test_retry_failed_scans_only_the_failed_countries(self):
        scan = self.scan(
            countries=("us", "de", "bg"), status="completed",
            failed_count=2, failed_items=[{"country": "de", "error": "x"},
                                          {"country": "bg", "error": "x"}],
        )
        response = self.client.post(
            reverse("aso:opportunity_retry_failed", args=[scan.pk])
        )
        self.assertEqual(response.status_code, 200)
        new_scan = OpportunityScan.objects.exclude(pk=scan.pk).get()
        self.assertEqual(new_scan.countries, ["de", "bg"])

    def test_dismiss_marks_a_finished_scan_acknowledged(self):
        scan = self.scan(status="completed", finished_at=timezone.now())
        self.client.post(reverse("aso:opportunity_dismiss", args=[scan.pk]))
        scan.refresh_from_db()
        self.assertTrue(scan.acknowledged)

    def test_closing_the_newest_scan_never_brings_up_an_older_one(self):
        now = timezone.now()
        self.scan(status="completed", finished_at=now - timezone.timedelta(minutes=2))
        newest = self.scan(status="completed", finished_at=now)
        self.assertEqual(opportunity_scans.finished_scan(), newest)
        self.client.post(reverse("aso:opportunity_dismiss", args=[newest.pk]))
        self.assertIsNone(opportunity_scans.finished_scan())
        self.assertIsNone(opportunity_scans.strip_scan())
        current = self.client.get(reverse("aso:opportunity_current")).json()
        self.assertIsNone(current["finished"])


class SaveTest(ScanTestBase):
    def _finished_scan(self):
        scan = self.scan(countries=("us", "de", "bg"), status="running")
        opportunity_scans._execute(scan.pk)
        return scan

    def save(self, scan, **body):
        return self.client.post(
            reverse("aso:opportunity_save"),
            data=json.dumps({"scan_id": scan.pk, **body}),
            content_type="application/json",
        )

    def test_save_writes_one_result_per_selected_country(self):
        scan = self._finished_scan()
        response = self.save(scan, countries=["us", "bg"])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["saved"], 2)
        self.assertEqual(
            sorted(SearchResult.objects.values_list("country", flat=True)),
            ["bg", "us"],
        )

    def test_save_all_writes_every_country(self):
        scan = self._finished_scan()
        self.save(scan, all=True)
        self.assertEqual(SearchResult.objects.count(), 3)

    def test_save_carries_the_inferred_genre(self):
        """Regression: the old save never passed it, so the Apple fallback cap
        fell back to the country-wide floor instead of the keyword's own
        category for every row saved from this tab."""
        scan = self._finished_scan()
        OpportunityScanResult.objects.filter(scan=scan).update(
            inferred_genre="Health & Fitness",
        )
        self.save(scan, all=True)
        genres = set(SearchResult.objects.values_list("inferred_genre", flat=True))
        self.assertEqual(genres, {"Health & Fitness"})

    def test_saving_twice_in_a_day_keeps_one_row_per_country(self):
        scan = self._finished_scan()
        self.save(scan, all=True)
        self.save(scan, all=True)
        self.assertEqual(SearchResult.objects.count(), 3)

    def test_save_counts_what_it_saved_on_the_scan(self):
        scan = self._finished_scan()
        self.save(scan, countries=["us"])
        scan.refresh_from_db()
        self.assertEqual(scan.saved_count, 1)

    def test_save_rejects_an_empty_selection(self):
        scan = self._finished_scan()
        response = self.save(scan, countries=[])
        self.assertEqual(response.status_code, 400)


class TheResultsTableSaysOnlyWhatIsTrueTest(SimpleTestCase):
    """The ranked table after a scan: its lead describes the one Opportunity
    number, and "Your rank" is a column only when the scan names an app."""

    @staticmethod
    def _read(path):
        from django.conf import settings

        with open(settings.BASE_DIR / path, encoding="utf-8") as f:
            return f.read()

    def test_the_lead_describes_one_number(self):
        template = self._read("aso/templates/aso/opportunity.html")
        lead = template.split("Ranked by opportunity</h2>", 1)[1].split("</p>", 1)[0]
        self.assertNotIn("\u2192", lead)
        self.assertNotIn("line under it", lead)
        self.assertIn("Downloads at #1 is what the top spot pays there", lead)

    def test_the_rank_column_needs_an_app(self):
        template = self._read("aso/templates/aso/opportunity.html")
        self.assertIn('<th id="opp-rank-head" class="hidden ', template)
        script = self._read("static/js/opportunity-scan.js")
        self.assertIn("hasApp = !!scan.app_id;", script)
        self.assertIn("JP.toggle('opp-rank-head', hasApp);", script)
        self.assertIn("(hasApp ? '<td", script)
        self.assertIn("td.colSpan = hasApp ? 11 : 10;", script)
