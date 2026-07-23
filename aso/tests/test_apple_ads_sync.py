"""Tests for the Apple popularity background sync (mocked client)."""

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings

from aso.apple_ads import storage, sync
from aso.apple_ads.client import (
    AppleAdsAuthError,
    AppleAdsRateLimitedError,
)
from aso.models import App, AppleSearchPopularity, Keyword, SearchResult


class SyncTestBase(TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._override = override_settings(DATA_DIR=Path(self._tmp.name))
        self._override.enable()
        storage.reset_cache()
        with sync._queue_lock:
            sync._enrichment_queue.clear()
        sync._inline_backoff_until = 0.0
        self.addCleanup(self._cleanup)

        storage.save_apple_settings(apple_ads={
            "cookies": [{"name": "myacinfo", "value": "x", "domain": "", "path": "/", "expires": 0}],
            "primary_app_id": "123",
            "tested_ok": True,
        })

        app = App.objects.create(name="Test App")
        self.kw = Keyword.objects.create(keyword="fitness", app=app)
        self.result = SearchResult.objects.create(
            keyword=self.kw,
            popularity_score=62,
            difficulty_score=30,
            country="us",
        )

    def _cleanup(self):
        self._override.disable()
        storage.reset_cache()
        with sync._queue_lock:
            sync._enrichment_queue.clear()
        sync._inline_backoff_until = 0.0

    def run_sync(self, pairs=None):
        sync._run_sync(pairs if pairs is not None else [("fitness", "us")])


class SyncUpsertTest(SyncTestBase):
    @patch("aso.apple_ads.sync.time.sleep")
    @patch("aso.apple_ads.sync.fetch_popularities")
    def test_values_upserted_and_today_rows_patched(self, mock_fetch, _sleep):
        mock_fetch.return_value = {"fitness": 71}
        self.run_sync()

        row = AppleSearchPopularity.objects.get(term="fitness", country="us")
        self.assertEqual(row.popularity, 71)

        self.result.refresh_from_db()
        self.assertEqual(self.result.apple_popularity_score, 71)

        status = storage.load_apple_settings()["apple_ads"]
        self.assertEqual(status["last_sync_status"], "completed")
        self.assertEqual(status["coverage"]["tracked_matched"], 1)
        self.assertEqual(status["coverage"]["tracked_total"], 1)

    @patch("aso.apple_ads.sync.time.sleep")
    @patch("aso.apple_ads.sync.fetch_popularities")
    def test_null_values_stored_as_known_empty(self, mock_fetch, _sleep):
        mock_fetch.return_value = {}  # Apple returned nothing for the term
        self.run_sync()
        row = AppleSearchPopularity.objects.get(term="fitness", country="us")
        self.assertIsNone(row.popularity)
        # Known-empty rows are not re-fetched the same day
        self.assertEqual(sync._pairs_needing_fetch([("fitness", "us")]), [])

    @patch("aso.apple_ads.sync.time.sleep")
    @patch("aso.apple_ads.sync.fetch_popularities")
    def test_classification_recomputed_on_patch(self, mock_fetch, _sleep):
        storage.save_apple_settings(popularity_source="apple")
        mock_fetch.return_value = {"fitness": 8}
        self.run_sync()
        self.result.refresh_from_db()
        self.assertEqual(self.result.classification, "Low Volume")


class SyncFailureTest(SyncTestBase):
    @patch("aso.apple_ads.sync.time.sleep")
    @patch("aso.apple_ads.sync.fetch_popularities")
    def test_auth_error_marks_session_expired_and_requeues(self, mock_fetch, _sleep):
        mock_fetch.side_effect = AppleAdsAuthError("expired")
        self.run_sync()
        block = storage.load_apple_settings()["apple_ads"]
        self.assertTrue(block["session_expired"])
        self.assertEqual(block["last_sync_status"], "error")
        with sync._queue_lock:
            self.assertIn(("fitness", "us"), sync._enrichment_queue)

    @patch("aso.apple_ads.sync.time.sleep")
    @patch("aso.apple_ads.sync.fetch_popularities")
    def test_rate_limit_aborts_gracefully_and_requeues_rest(self, mock_fetch, _sleep):
        # First batch OK, second batch rate-limited → partial with queue intact.
        # Batches run sorted by country, so "au" is fetched before "us".
        mock_fetch.side_effect = [
            {"a": 10},
            AppleAdsRateLimitedError("slow down"),
        ]
        pairs = [("a", "au")] + [(f"kw{i}", "us") for i in range(150)]
        self.run_sync(pairs)
        block = storage.load_apple_settings()["apple_ads"]
        self.assertEqual(block["last_sync_status"], "rate_limited")
        # First batch committed, remainder requeued
        self.assertEqual(
            AppleSearchPopularity.objects.filter(country="au").count(), 1
        )
        with sync._queue_lock:
            self.assertGreaterEqual(len(sync._enrichment_queue), 100)

    @patch("aso.apple_ads.sync.time.sleep")
    @patch("aso.apple_ads.sync.fetch_popularities")
    def test_per_run_ceiling_aborts_partial(self, mock_fetch, _sleep):
        mock_fetch.return_value = {}
        with patch.object(sync, "MAX_REQUESTS_PER_RUN", 2):
            pairs = [(f"kw{i}", "us") for i in range(500)]  # 5 batches of 100
            self.run_sync(pairs)
        self.assertEqual(mock_fetch.call_count, 2)
        block = storage.load_apple_settings()["apple_ads"]
        self.assertEqual(block["last_sync_status"], "partial")

    def test_no_op_without_credentials(self):
        storage.save_apple_settings(apple_ads={"tested_ok": False})
        self.assertFalse(sync._sync_ready())
        self.assertFalse(sync.run_manual_sync())


class EnrichmentQueueTest(SyncTestBase):
    def test_enqueue_dedupes_and_normalizes_country(self):
        sync.enqueue_term("yoga", "US")
        sync.enqueue_term("yoga", "us")
        with sync._queue_lock:
            self.assertEqual(sync._enrichment_queue, {("yoga", "us")})

    @patch("aso.apple_ads.sync.time.sleep")
    @patch("aso.apple_ads.sync.fetch_popularities")
    def test_queued_terms_included_in_sync(self, mock_fetch, _sleep):
        mock_fetch.return_value = {"fitness": 71, "yoga": 44}
        sync.enqueue_term("yoga", "us")
        pairs = sync._dedupe(sync._tracked_pairs() + sync._drain_queue())
        self.run_sync(pairs)
        self.assertEqual(
            AppleSearchPopularity.objects.get(term="yoga", country="us").popularity,
            44,
        )


class EnsureAppleValuesTest(SyncTestBase):
    """Synchronous fetch-on-miss: new keywords get their Apple value in the
    same request that scores them (batched, budget-capped, failure-safe)."""

    @patch("aso.apple_ads.sync.time.sleep")
    @patch("aso.apple_ads.sync.fetch_popularities")
    def test_fetches_only_unknown_terms(self, mock_fetch, _sleep):
        AppleSearchPopularity.objects.create(term="known", country="us", popularity=50)
        AppleSearchPopularity.objects.create(term="empty", country="us", popularity=None)
        mock_fetch.return_value = {"trading": 88}
        sync.ensure_apple_values(["known", "empty", "trading"], "us")
        mock_fetch.assert_called_once()
        self.assertEqual(mock_fetch.call_args[0][0], ["trading"])
        self.assertEqual(
            AppleSearchPopularity.objects.get(term="trading", country="us").popularity,
            88,
        )

    @patch("aso.apple_ads.sync.fetch_popularities")
    def test_no_op_when_all_terms_known(self, mock_fetch):
        AppleSearchPopularity.objects.create(term="known", country="us", popularity=50)
        sync.ensure_apple_values(["known", "Known "], "us")
        mock_fetch.assert_not_called()

    @patch("aso.apple_ads.sync.fetch_popularities")
    def test_no_op_when_not_ready(self, mock_fetch):
        storage.save_apple_settings(apple_ads={"tested_ok": False})
        sync.ensure_apple_values(["trading"], "us")
        mock_fetch.assert_not_called()

    @patch("aso.apple_ads.sync.fetch_popularities")
    def test_no_op_when_session_expired(self, mock_fetch):
        storage.save_apple_settings(apple_ads={"session_expired": True})
        sync.ensure_apple_values(["trading"], "us")
        mock_fetch.assert_not_called()

    @patch("aso.apple_ads.sync.time.sleep")
    @patch("aso.apple_ads.sync.fetch_popularities")
    def test_unechoed_terms_stored_null_not_refetched(self, mock_fetch, _sleep):
        mock_fetch.return_value = {}  # Apple has no value for the term
        sync.ensure_apple_values(["tiny"], "us")
        row = AppleSearchPopularity.objects.get(term="tiny", country="us")
        self.assertIsNone(row.popularity)
        sync.ensure_apple_values(["tiny"], "us")
        mock_fetch.assert_called_once()

    @patch("aso.apple_ads.sync.time.sleep")
    @patch("aso.apple_ads.sync.fetch_popularities")
    def test_failure_backs_off_and_enqueues(self, mock_fetch, _sleep):
        mock_fetch.side_effect = AppleAdsRateLimitedError("slow down")
        sync.ensure_apple_values(["trading"], "us")
        with sync._queue_lock:
            self.assertIn(("trading", "us"), sync._enrichment_queue)
        # Backoff active: the next call must not hit the network again.
        sync.ensure_apple_values(["stocks"], "us")
        mock_fetch.assert_called_once()
        with sync._queue_lock:
            self.assertIn(("stocks", "us"), sync._enrichment_queue)

    @patch("aso.apple_ads.sync.time.sleep")
    @patch("aso.apple_ads.sync.fetch_popularities")
    def test_auth_error_marks_session_expired(self, mock_fetch, _sleep):
        mock_fetch.side_effect = AppleAdsAuthError("expired")
        sync.ensure_apple_values(["trading"], "us")
        block = storage.load_apple_settings()["apple_ads"]
        self.assertTrue(block["session_expired"])
        with sync._queue_lock:
            self.assertIn(("trading", "us"), sync._enrichment_queue)

    @patch("aso.apple_ads.sync.time.sleep")
    @patch("aso.apple_ads.sync.fetch_popularities")
    def test_inline_request_ceiling_enqueues_remainder(self, mock_fetch, _sleep):
        mock_fetch.return_value = {}
        terms = [f"kw{i:03d}" for i in range(250)]  # 3 batches of <=100
        with patch.object(sync, "INLINE_MAX_REQUESTS", 2):
            sync.ensure_apple_values(terms, "us")
        self.assertEqual(mock_fetch.call_count, 2)
        with sync._queue_lock:
            self.assertEqual(len(sync._enrichment_queue), 50)

    @patch("aso.apple_ads.sync.time.sleep")
    @patch("aso.apple_ads.sync.fetch_popularities")
    def test_batches_at_100_terms(self, mock_fetch, _sleep):
        mock_fetch.return_value = {}
        sync.ensure_apple_values([f"kw{i:03d}" for i in range(150)], "us")
        self.assertEqual(mock_fetch.call_count, 2)
        sizes = sorted(len(c[0][0]) for c in mock_fetch.call_args_list)
        self.assertEqual(sizes, [50, 100])
