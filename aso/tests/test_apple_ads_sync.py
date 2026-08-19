"""Tests for the weekly dataset sync engine (aso.apple_ads.sync +
aso.apple_ads.impressions)."""

import datetime as dt
from unittest import mock

from django.test import override_settings
from django.utils import timezone

from aso.apple_ads import api, impressions, storage, sync
from aso.models import (
    App,
    AppleImpressionShare,
    AppleSearchPopularity,
    AppleTopTerm,
    Keyword,
    SearchResult,
)
from aso.tests.test_apple_ads_storage import StorageTestBase

WEEK = dt.date(2026, 8, 9)
PREV_WEEK = dt.date(2026, 8, 2)


def _api_row(term, popularity=70, genre="BUSINESS", rank=1, tier=4,
             in_genre=90):
    return {
        "week": WEEK.isoformat(), "countryOrRegion": "US", "genre": genre,
        "searchTerm": term, "rankInGenre": rank,
        "searchPopularityInGenre": in_genre,
        "searchPopularity1to100": popularity, "searchPopularity1to5": tier,
    }


def _sane_week_rows(count=None, popularity=None):
    """Enough valid rows across enough genres to pass the sanity gates."""
    count = count or sync.MIN_WEEK_ROWS
    rows = []
    genres = ["BUSINESS", "GAMES", "HEALTH_FITNESS", "TRAVEL"]
    for i in range(count):
        rows.append(_api_row(
            f"term {i}",
            popularity=popularity if popularity else 40 + (i % 60),
            genre=genres[i % len(genres)],
            rank=(i // len(genres)) + 1,
        ))
    return rows


class SyncTestBase(StorageTestBase):
    """Temp DATA_DIR + a connected v1 configuration + patched network."""

    databases = "__all__"

    def setUp(self):
        super().setUp()
        storage.save_apple_settings(apple_ads={
            "tested_ok": True,
            "client_id": "SEARCHADS.c", "team_id": "SEARCHADS.t",
            "key_id": "k", "ad_account_id": "1",
        })
        self._patches = [
            mock.patch("aso.apple_ads.keys.has_private_key",
                       return_value=True),
            mock.patch("aso.apple_ads.keys.load_private_key_pem",
                       return_value="PEM"),
            mock.patch.object(api, "latest_available_week",
                              return_value=WEEK),
            mock.patch.object(sync, "_pace", lambda *a, **k: None),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _track(self, term, country="us"):
        keyword = Keyword.objects.create(keyword=term)
        SearchResult.objects.create(
            keyword=keyword, country=country, difficulty_score=30,
            popularity_score=50,
        )
        return keyword


class WeeklyIngestTest(SyncTestBase):
    def test_full_run_ingests_activates_and_refreshes(self):
        self._track("term 1", "us")
        self._track("obscure tail keyword", "us")
        rows = _sane_week_rows()
        with mock.patch.object(
            api, "query_search_term_popularity",
            return_value=(rows, -1),
        ) as query:
            sync._run_sync()

        # Ingested and activated.
        self.assertEqual(
            AppleTopTerm.objects.filter(country="us", week=WEEK).count(),
            len(rows),
        )
        block = storage.load_apple_settings()["apple_ads"]
        self.assertEqual(block["active_weeks"]["us"], WEEK.isoformat())
        self.assertEqual(block["last_sync_status"], "completed")
        # Tracked term in the dataset -> real value; absent -> explicit null.
        self.assertIsNotNone(AppleSearchPopularity.lookup("term 1", "us"))
        rows_absent = AppleSearchPopularity.objects.get(
            term="obscure tail keyword", country="us"
        )
        self.assertIsNone(rows_absent.popularity)
        # Coverage includes the active week.
        self.assertEqual(block["coverage"]["week"], WEEK.isoformat())
        self.assertEqual(block["coverage"]["tracked_total"], 2)
        self.assertEqual(block["coverage"]["tracked_matched"], 1)
        query.assert_called()

    def test_today_rows_patched_and_classification_recomputed(self):
        keyword = self._track("term 1", "us")
        result = keyword.results.first()
        before = result.classification
        rows = _sane_week_rows()
        for row in rows:
            if row["searchTerm"] == "term 1":
                row["searchPopularity1to100"] = 95
        with mock.patch.object(
            api, "query_search_term_popularity", return_value=(rows, -1)
        ), mock.patch.object(
            storage, "get_popularity_source", return_value="apple"
        ):
            with mock.patch(
                "aso.popularity.get_popularity_source", return_value="apple"
            ):
                sync._run_sync()
        result.refresh_from_db()
        self.assertEqual(result.apple_popularity_score, 95)
        self.assertIsInstance(before, str)  # sanity

    def test_invalid_rows_dropped(self):
        rows = _sane_week_rows()
        rows += [
            {"searchTerm": "", "genre": "X"},                # empty term
            {"searchTerm": "ok", "genre": "X", "rankInGenre": 999,
             "searchPopularityInGenre": 5, "searchPopularity1to100": 5,
             "searchPopularity1to5": 1},                      # rank out of range
            {"searchTerm": "ok2", "genre": "X", "rankInGenre": 1,
             "searchPopularityInGenre": 5, "searchPopularity1to100": 500,
             "searchPopularity1to5": 1},                      # pop out of range
        ]
        with mock.patch.object(
            api, "query_search_term_popularity", return_value=(rows, -1)
        ):
            sync._run_sync()
        stored_terms = set(
            AppleTopTerm.objects.values_list("term", flat=True)
        )
        self.assertNotIn("", stored_terms)
        self.assertNotIn("ok", stored_terms)
        self.assertNotIn("ok2", stored_terms)

    def test_missing_weeks_catch_up(self):
        """After the app was closed for two weeks, both are ingested."""
        self.assertEqual(
            sync._missing_weeks(PREV_WEEK.isoformat(), WEEK, force=False),
            [WEEK],
        )
        self.assertEqual(
            sync._missing_weeks(
                (PREV_WEEK - dt.timedelta(days=7)).isoformat(), WEEK, False
            ),
            [PREV_WEEK, WEEK],
        )
        self.assertEqual(sync._missing_weeks("", WEEK, False), [WEEK])
        # Force re-fetches the current week even when already active.
        self.assertEqual(
            sync._missing_weeks(WEEK.isoformat(), WEEK, force=True), [WEEK]
        )
        self.assertEqual(
            sync._missing_weeks(WEEK.isoformat(), WEEK, force=False), []
        )


class QuarantineTest(SyncTestBase):
    def test_tiny_week_quarantined(self):
        with mock.patch.object(
            api, "query_search_term_popularity",
            return_value=([_api_row("only row")], -1),
        ):
            sync._run_sync()
        self.assertEqual(AppleTopTerm.objects.count(), 0)
        block = storage.load_apple_settings()["apple_ads"]
        self.assertNotIn("us", block["active_weeks"])
        self.assertEqual(block["last_sync_status"], "partial")
        self.assertIn("looked incomplete", block["last_sync_error"])

    def test_near_constant_week_quarantined(self):
        rows = _sane_week_rows(popularity=50)  # every value identical
        reason = sync._week_sane("us", WEEK, sync._clean_rows(rows, "us", WEEK))
        self.assertIn("near-constant", reason)

    def test_suspicious_shrink_quarantined(self):
        # Previous active week with a full dataset. The ratio gate only
        # bites above the MIN_WEEK_ROWS floor, so make the previous week
        # large enough that 30% of it exceeds the floor.
        sync._persist_rows(sync._clean_rows(
            [dict(r, week=PREV_WEEK.isoformat()) for r in _sane_week_rows(2000)],
            "us", PREV_WEEK,
        ))
        storage.save_apple_settings(apple_ads={
            "active_weeks": {"us": PREV_WEEK.isoformat()},
        })
        small = sync._clean_rows(_sane_week_rows(sync.MIN_WEEK_ROWS), "us", WEEK)
        reason = sync._week_sane("us", WEEK, small)
        self.assertIn("suspicious shrink", reason)

    def test_previous_good_week_keeps_serving(self):
        """A quarantined week never touches the active pointer or cache."""
        self._track("term 1", "us")
        good = _sane_week_rows()
        with mock.patch.object(
            api, "query_search_term_popularity", return_value=(good, -1)
        ):
            sync._run_sync()
        value_before = AppleSearchPopularity.lookup("term 1", "us")
        with mock.patch.object(
            api, "query_search_term_popularity",
            return_value=([_api_row("broken")], -1),
        ), mock.patch.object(
            api, "latest_available_week",
            return_value=WEEK + dt.timedelta(days=7),
        ), mock.patch.object(sync, "_recently_attempted", return_value=False):
            sync._run_sync()
        block = storage.load_apple_settings()["apple_ads"]
        self.assertEqual(block["active_weeks"]["us"], WEEK.isoformat())
        self.assertEqual(
            AppleSearchPopularity.lookup("term 1", "us"), value_before
        )


class FailureModesTest(SyncTestBase):
    def test_auth_error_marks_credentials_rejected(self):
        with mock.patch.object(
            api, "query_search_term_popularity",
            side_effect=api.AppleAdsAuthError("rejected"),
        ):
            sync._run_sync()
        block = storage.load_apple_settings()["apple_ads"]
        self.assertTrue(block["credentials_rejected"])
        self.assertTrue(block["credentials_rejected_at"])
        self.assertFalse(block["tested_ok"])
        self.assertEqual(block["last_sync_status"], "error")
        self.assertFalse(storage.apple_source_ready())

    def test_rate_limit_aborts_gracefully(self):
        with mock.patch.object(
            api, "query_search_term_popularity",
            side_effect=api.AppleAdsRateLimitedError("slow down"),
        ):
            sync._run_sync()
        block = storage.load_apple_settings()["apple_ads"]
        self.assertEqual(block["last_sync_status"], "rate_limited")
        # Nothing activated; the next tick resumes.
        self.assertEqual(block["active_weeks"], {})

    def test_per_run_ceiling_partial(self):
        with mock.patch.object(sync, "MAX_REQUESTS_PER_RUN", 0), \
                mock.patch.object(
                    api, "query_search_term_popularity",
                    return_value=(_sane_week_rows(), -1),
                ):
            sync._run_sync()
        block = storage.load_apple_settings()["apple_ads"]
        self.assertEqual(block["last_sync_status"], "partial")
        self.assertIn("ceiling", block["last_sync_error"])

    def test_daily_ceiling_partial(self):
        now = timezone.now().isoformat()
        storage.save_apple_settings(apple_ads={
            "request_log": [now] * sync.MAX_REQUESTS_PER_DAY,
        })
        with mock.patch.object(
            api, "query_search_term_popularity",
            return_value=(_sane_week_rows(), -1),
        ):
            sync._run_sync()
        block = storage.load_apple_settings()["apple_ads"]
        self.assertEqual(block["last_sync_status"], "partial")

    def test_no_op_without_credentials(self):
        storage.save_apple_settings(apple_ads={"client_id": ""})
        sync._run_sync()
        block = storage.load_apple_settings()["apple_ads"]
        self.assertEqual(block["last_sync_status"], "error")
        self.assertIn("not connected", block["last_sync_error"])


class SchedulerGatingTest(SyncTestBase):
    def test_not_ready_never_starts(self):
        storage.save_apple_settings(apple_ads={"tested_ok": False})
        with mock.patch.object(sync, "_start_worker") as start:
            sync.maybe_run_sync()
        start.assert_not_called()

    def test_pending_week_starts_worker(self):
        with mock.patch.object(sync, "_start_worker") as start:
            sync.maybe_run_sync()
        start.assert_called_once()

    def test_recent_attempt_backs_off(self):
        storage.save_apple_settings(apple_ads={
            "last_sync_at": timezone.now().isoformat(),
            "last_sync_status": "partial",
        })
        with mock.patch.object(sync, "_start_worker") as start:
            sync.maybe_run_sync()
        start.assert_not_called()

    def test_nothing_pending_no_worker(self):
        storage.save_apple_settings(apple_ads={
            "active_weeks": {"us": WEEK.isoformat()},
            "backfill": {"us": {"done": True}},
        })
        with mock.patch.object(sync, "_start_worker") as start:
            sync.maybe_run_sync()
        start.assert_not_called()

    def test_manual_sync_requires_ready(self):
        storage.save_apple_settings(apple_ads={"tested_ok": False})
        self.assertFalse(sync.run_manual_sync())


class BackfillTest(SyncTestBase):
    def _run_with_pages(self, run_state=None):
        """Run only the backfill stage with a synthetic full-week response."""
        run_state = run_state or {"requests": 0, "pacing": 0}
        credentials = storage.api_credentials()
        with mock.patch.object(
            api, "query_search_term_popularity",
            side_effect=lambda *a, **k: (
                [dict(_api_row("backfill term"),
                      week=k["week_start"].isoformat())], -1
            ),
        ):
            sync._run_backfill(credentials, "1", run_state)
        return run_state

    def test_backfill_walks_to_cutoff_and_marks_done(self):
        self._track("backfill term", "us")
        storage.save_apple_settings(apple_ads={
            "active_weeks": {"us": WEEK.isoformat()},
        })
        # Persist the active week so the cursor starts from it.
        sync._persist_rows(
            sync._clean_rows(_sane_week_rows(), "us", WEEK)
        )
        state = self._run_with_pages()
        block = storage.load_apple_settings()["apple_ads"]
        self.assertTrue(block["backfill"]["us"]["done"])
        # 65 weekly requests (one page each).
        self.assertEqual(state["requests"], sync.BACKFILL_WEEKS)
        # Old weeks only kept the tracked term rows (insert-time filter).
        old_week = api.weeks_back(WEEK, sync.FULL_WEEKS_RETAINED + 2)
        old_terms = set(
            AppleTopTerm.objects.filter(country="us", week=old_week)
            .values_list("term", flat=True)
        )
        self.assertEqual(old_terms, {"backfill term"})

    def test_backfill_resumes_from_cursor_after_ceiling(self):
        self._track("backfill term", "us")
        storage.save_apple_settings(apple_ads={
            "active_weeks": {"us": WEEK.isoformat()},
        })
        sync._persist_rows(sync._clean_rows(_sane_week_rows(), "us", WEEK))
        # First run with a tiny budget parks mid-way.
        with mock.patch.object(sync, "MAX_REQUESTS_PER_RUN", 5):
            with self.assertRaises(sync._CeilingReached):
                self._run_with_pages(run_state={"requests": 0, "pacing": 0})
        cursor_after = storage.load_apple_settings()["apple_ads"][
            "backfill"]["us"]["cursor"]
        self.assertFalse(
            storage.load_apple_settings()["apple_ads"]["backfill"]["us"].get("done")
        )
        # Second run resumes from the saved cursor and completes.
        state = self._run_with_pages()
        self.assertTrue(
            storage.load_apple_settings()["apple_ads"]["backfill"]["us"]["done"]
        )
        self.assertLess(state["requests"], sync.BACKFILL_WEEKS)
        self.assertGreater(cursor_after, api.weeks_back(WEEK, 65).isoformat())

    def test_no_tracked_keywords_no_backfill(self):
        """Backfill only serves tracked countries: with no tracked
        keywords there is no history to accrue, so no requests happen.
        It starts automatically once keywords exist (next tick)."""
        storage.save_apple_settings(apple_ads={
            "active_weeks": {"us": WEEK.isoformat()},
        })
        sync._persist_rows(sync._clean_rows(_sane_week_rows(), "us", WEEK))
        state = self._run_with_pages()
        self.assertEqual(state["requests"], 0)
        self.assertEqual(
            storage.load_apple_settings()["apple_ads"]["backfill"], {}
        )


class PruneTest(SyncTestBase):
    def test_retention_policy(self):
        self._track("kept term", "us")
        old_full = api.weeks_back(WEEK, sync.FULL_WEEKS_RETAINED + 1)
        ancient = api.weeks_back(WEEK, sync.TRACKED_WEEKS_RETAINED + 1)
        recent = api.weeks_back(WEEK, 1)
        for week, term in [
            (recent, "any term"),          # recent: kept
            (old_full, "kept term"),       # old but tracked: kept
            (old_full, "dropped term"),    # old and untracked: pruned
            (ancient, "kept term"),        # beyond 65 weeks: pruned
        ]:
            AppleTopTerm.objects.create(
                term=term, country="us", genre="BUSINESS", week=week,
                rank_in_genre=1, popularity_in_genre=50, popularity=50,
                popularity_tier=3,
            )
        sync.prune_top_terms()
        remaining = set(
            AppleTopTerm.objects.values_list("term", "week")
        )
        self.assertEqual(remaining, {
            ("any term", recent), ("kept term", old_full),
        })


class EnsureCountryDatasetTest(SyncTestBase):
    def setUp(self):
        super().setUp()
        sync._inline_backoff_until = 0.0

    def test_downloads_activates_and_is_bounded(self):
        with mock.patch.object(
            api, "query_search_term_popularity",
            return_value=(_sane_week_rows(), -1),
        ) as query:
            sync.ensure_country_dataset("de")
        self.assertTrue(
            AppleTopTerm.objects.filter(country="de", week=WEEK).exists()
        )
        block = storage.load_apple_settings()["apple_ads"]
        self.assertEqual(block["active_weeks"]["de"], WEEK.isoformat())
        self.assertEqual(query.call_count, 1)

    def test_no_op_when_dataset_exists(self):
        AppleTopTerm.objects.create(
            term="x", country="de", genre="G", week=WEEK, rank_in_genre=1,
            popularity_in_genre=50, popularity=50, popularity_tier=3,
        )
        with mock.patch.object(api, "query_search_term_popularity") as query:
            sync.ensure_country_dataset("de")
        query.assert_not_called()

    def test_no_op_when_not_ready(self):
        storage.save_apple_settings(apple_ads={"tested_ok": False})
        with mock.patch.object(api, "query_search_term_popularity") as query:
            sync.ensure_country_dataset("de")
        query.assert_not_called()

    def test_failure_backs_off_and_never_raises(self):
        with mock.patch.object(
            api, "query_search_term_popularity",
            side_effect=api.AppleAdsAPIError("boom"),
        ):
            sync.ensure_country_dataset("de")  # must not raise
        self.assertGreater(sync._inline_backoff_until, 0)
        # Backoff active: the next call skips the network entirely.
        with mock.patch.object(api, "query_search_term_popularity") as query:
            sync.ensure_country_dataset("de")
        query.assert_not_called()

    def test_auth_failure_marks_rejected(self):
        with mock.patch.object(
            api, "query_search_term_popularity",
            side_effect=api.AppleAdsAuthError("no"),
        ):
            sync.ensure_country_dataset("de")
        self.assertTrue(
            storage.load_apple_settings()["apple_ads"]["credentials_rejected"]
        )


class ImpressionShareTest(SyncTestBase):
    def _row(self, term="travel app", week=WEEK, low=0.18, high=0.18):
        return {
            "week": week.isoformat(), "appName": "X",
            "promotedObjectId": "123", "countryOrRegion": "US",
            "searchTerm": term, "lowImpressionShare": low,
            "highImpressionShare": high, "rank": 2,
            "searchPopularity1to5": 4,
        }

    def test_rows_stored_and_state_updated(self):
        app = App.objects.create(name="My App", track_id=123)
        with mock.patch.object(
            api, "query_impression_share",
            return_value=([self._row()], 1),
        ):
            impressions.run_weekly(
                storage.api_credentials(), "1",
                spend_request=lambda: True, pace=lambda: None,
            )
        row = AppleImpressionShare.objects.get()
        self.assertEqual(row.app, app)
        self.assertEqual(row.country, "us")
        self.assertEqual(row.search_term, "travel app")
        self.assertEqual(row.week, WEEK)
        state = storage.load_apple_settings()["apple_ads"]["impression_share"]
        self.assertEqual(state["status"], "completed")
        self.assertTrue(state["has_data"])
        self.assertEqual(state["last_week"], WEEK.isoformat())

    def test_zero_rows_is_success_without_data(self):
        App.objects.create(name="My App", track_id=123)
        with mock.patch.object(
            api, "query_impression_share", return_value=([], 0)
        ):
            impressions.run_weekly(
                storage.api_credentials(), "1",
                spend_request=lambda: True, pace=lambda: None,
            )
        state = storage.load_apple_settings()["apple_ads"]["impression_share"]
        self.assertEqual(state["status"], "completed")
        self.assertFalse(state["has_data"])

    def test_skips_when_week_already_synced(self):
        storage.save_apple_settings(apple_ads={"impression_share": {
            "last_week": WEEK.isoformat(),
        }})
        with mock.patch.object(api, "query_impression_share") as query:
            impressions.run_weekly(
                storage.api_credentials(), "1",
                spend_request=lambda: True, pace=lambda: None,
            )
        query.assert_not_called()

    def test_budget_stop_is_partial(self):
        App.objects.create(name="My App", track_id=123)
        with mock.patch.object(api, "query_impression_share") as query:
            impressions.run_weekly(
                storage.api_credentials(), "1",
                spend_request=lambda: False, pace=lambda: None,
            )
        query.assert_not_called()
        state = storage.load_apple_settings()["apple_ads"]["impression_share"]
        self.assertEqual(state["status"], "partial")
        self.assertNotEqual(state.get("last_week"), WEEK.isoformat())

    def test_per_app_error_isolated(self):
        App.objects.create(name="App A", track_id=1)
        App.objects.create(name="App B", track_id=2)
        responses = [api.AppleAdsAccessError("no access"),
                     ([self._row()], 1)]

        def side_effect(*args, **kwargs):
            result = responses.pop(0)
            if isinstance(result, Exception):
                raise result
            return result

        with mock.patch.object(
            api, "query_impression_share", side_effect=side_effect
        ):
            impressions.run_weekly(
                storage.api_credentials(), "1",
                spend_request=lambda: True, pace=lambda: None,
            )
        self.assertEqual(AppleImpressionShare.objects.count(), 1)
        state = storage.load_apple_settings()["apple_ads"]["impression_share"]
        self.assertEqual(state["status"], "partial")
        self.assertIn("no access", state["error"])

    def test_isolated_from_dataset_sync(self):
        """An impression-share crash never fails the dataset sync."""
        with mock.patch.object(
            api, "query_search_term_popularity",
            return_value=(_sane_week_rows(), -1),
        ), mock.patch.object(
            impressions, "run_weekly", side_effect=RuntimeError("boom")
        ):
            sync._run_sync()
        block = storage.load_apple_settings()["apple_ads"]
        self.assertEqual(block["last_sync_status"], "completed")
        self.assertEqual(block["impression_share"]["status"], "error")
