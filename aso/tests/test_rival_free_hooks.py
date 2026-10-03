"""The free-tier hooks the Rival Tracker plugs into: Search History removals
(aso/pair_hooks.py), the scheduler's extra pairs and daily hooks, the raw
Lookup call and app search (aso/services.py), and a profile stored from a
lookup another feature made (aso/app_profiles.py).

Free-tier test: no aso_pro import.
"""

from unittest import mock

from django.test import TestCase

from aso import app_profiles, pair_hooks, run_queue, scheduler
from aso.models import App, Keyword, SearchResult
from aso.services import ITunesRateLimited, ITunesSearchService


def _row(keyword, country="us"):
    return SearchResult.objects.create(keyword=keyword, country=country, popularity_score=40,
                                       difficulty_score=40, difficulty_breakdown={},
                                       competitors_data=[], app_rank=None)


class PairHooksTest(TestCase):
    def setUp(self):
        self.heard = []
        self.listener = lambda pairs: self.heard.append(set(pairs))
        pair_hooks.removed_listeners.append(self.listener)

    def tearDown(self):
        pair_hooks.removed_listeners.remove(self.listener)

    def test_a_deleted_search_history_row_is_announced(self):
        from aso.views import _delete_tracking_entries

        keyword = Keyword.objects.create(keyword="habit tracker")
        _row(keyword, "us")
        _row(keyword, "se")
        _delete_tracking_entries([(keyword.pk, "us")])
        self.assertEqual(self.heard, [{(keyword.pk, "us")}])

    def test_a_failing_listener_never_fails_the_deletion(self):
        def broken(pairs):
            raise RuntimeError("boom")

        pair_hooks.removed_listeners.append(broken)
        try:
            pair_hooks.pairs_removed({(1, "us")})
        finally:
            pair_hooks.removed_listeners.remove(broken)


class SchedulerHooksTest(TestCase):
    def setUp(self):
        self.keyword = Keyword.objects.create(keyword="daily routine")
        self.source = lambda: [(self.keyword.pk, "us")]
        scheduler.extra_pair_sources.append(self.source)

    def tearDown(self):
        scheduler.extra_pair_sources.remove(self.source)

    def test_an_extra_pair_is_due_and_listed_once(self):
        self.assertTrue(scheduler._needs_refresh_today())
        self.assertEqual(scheduler._get_pairs_to_refresh().count((self.keyword.pk, "us")), 1)

    def test_a_daily_hook_runs_after_the_check_when_the_lane_is_idle(self):
        calls = []

        def hook():
            calls.append("ran")

        scheduler.daily_hooks.append(hook)
        try:
            with mock.patch("aso.scheduler._run_daily_refresh"), \
                    mock.patch("aso.apple_ads.sync.maybe_run_sync"), \
                    mock.patch.object(run_queue, "lane_state", return_value="idle"):
                scheduler._tick()
            self.assertEqual(calls, ["ran"])
            calls.clear()
            with mock.patch("aso.scheduler._run_daily_refresh"), \
                    mock.patch("aso.apple_ads.sync.maybe_run_sync"), \
                    mock.patch.object(run_queue, "lane_state", return_value="running"):
                scheduler._tick()
            self.assertEqual(calls, [])
        finally:
            scheduler.daily_hooks.remove(hook)

    def test_refresh_pairs_now_refuses_while_another_refresh_holds_the_flag(self):
        self.assertTrue(scheduler._claim_refresh(1))
        try:
            self.assertIsNone(scheduler.refresh_pairs_now([(self.keyword.pk, "us")], "Test"))
            with scheduler.refresh_claimed("Test", 1) as claimed:
                self.assertFalse(claimed)
        finally:
            scheduler._refresh_finished()

    def test_refresh_claimed_holds_and_releases_the_flag(self):
        with scheduler.refresh_claimed("Rival Tracker: test", 2) as claimed:
            self.assertTrue(claimed)
            self.assertTrue(scheduler.get_status()["running"])
        self.assertFalse(scheduler.get_status()["running"])


class LookupRawChunkTest(TestCase):
    def _response(self, status=200, results=()):
        response = mock.MagicMock(status_code=status, headers={})
        response.json.return_value = {"results": list(results)}
        response.raise_for_status.side_effect = None if status == 200 else Exception(status)
        return response

    def test_every_field_apple_sends_comes_back(self):
        raw = {"trackId": 7, "trackName": "X", "version": "2.0", "releaseNotes": "Faster.",
               "screenshotUrls": ["a"], "description": "d" * 3000}
        with mock.patch("aso.services.requests.get", return_value=self._response(results=[raw])) as get:
            found = ITunesSearchService().lookup_raw_chunk([7, 8], country="se")
        self.assertEqual(found, {7: raw})
        self.assertEqual(get.call_args.kwargs["params"], {"id": "7,8", "country": "se"})

    def test_a_rate_limit_is_raised_for_the_caller_to_pace(self):
        with mock.patch("aso.services.requests.get", return_value=self._response(status=429)), \
                self.assertRaises(ITunesRateLimited):
            ITunesSearchService().lookup_raw_chunk([7])

    def test_batch_lookup_still_parses_and_never_raises(self):
        with mock.patch("aso.services.requests.get", return_value=self._response(status=429)):
            self.assertEqual(ITunesSearchService()._batch_lookup([7]), {})


class FindAppsTest(TestCase):
    def test_a_bare_id_and_a_link_look_the_app_up(self):
        service = ITunesSearchService()
        with mock.patch.object(service, "lookup_by_id", return_value={"trackId": 5}) as lookup:
            self.assertEqual(service.find_apps("389801252"), [{"trackId": 5}])
            service.find_apps("https://apps.apple.com/se/app/x/id42")
        self.assertEqual(lookup.call_args_list[0].args, (389801252,))
        self.assertEqual(lookup.call_args_list[1].kwargs, {"country": "se", "retry": False})

    def test_a_name_searches(self):
        service = ITunesSearchService()
        with mock.patch.object(service, "search_apps", return_value=[{"trackId": 1}]) as search:
            self.assertEqual(service.find_apps("streakly"), [{"trackId": 1}])
        search.assert_called_once_with("streakly", country="us", limit=5)


class RecordLookupTest(TestCase):
    def test_a_lookup_made_elsewhere_is_the_days_profile(self):
        app = App.objects.create(name="Habito", track_id=111)
        app_profiles.record_lookup(app, "us", {"userRatingCount": 1840, "averageUserRating": 4.6,
                                               "releaseDate": "2020-01-01T00:00:00Z",
                                               "primaryGenreName": "Health & Fitness"})
        app.refresh_from_db()
        self.assertEqual(app.store_profiles["us"]["count"], 1840)
        itunes = mock.MagicMock()
        app_profiles.profile_in_storefront(app, "us", itunes_service=itunes)
        itunes.lookup_by_id.assert_not_called()
