"""Tests for the popularity source-resolution choke point (aso.popularity).

The core guarantee: exactly one effective popularity value per keyword,
derived from the user's source selection, with automatic fallback to the
internal estimate when Apple has no value.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

from django.test import TestCase, override_settings

from aso.apple_ads import storage
from aso.models import App, AppleSearchPopularity, Keyword, SearchResult
from aso.popularity import (
    SOURCE_APPLE,
    SOURCE_INTERNAL,
    annotate_effective_popularity,
    effective_from_pair,
    popularity_fields,
    prompt_source_note,
    recompute_all_classifications,
    resolve_popularity,
)
from aso.scoring import classify_keyword


class TempDataDirMixin:
    """Isolate settings.json in a temp DATA_DIR for each test."""

    def setUp(self):
        super().setUp()
        self._tmp = tempfile.TemporaryDirectory()
        self._override = override_settings(DATA_DIR=Path(self._tmp.name))
        self._override.enable()
        storage.reset_cache()
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        self._override.disable()
        storage.reset_cache()
        self._tmp.cleanup()

    def set_source(self, source):
        storage.save_apple_settings(popularity_source=source)


class EffectiveFromPairTest(TestCase):
    """Pure-function resolution matrix."""

    def test_internal_source_uses_internal(self):
        self.assertEqual(
            effective_from_pair(62, 48, SOURCE_INTERNAL), (62, "internal", False)
        )

    def test_unset_source_behaves_as_internal(self):
        self.assertEqual(effective_from_pair(62, 48, ""), (62, "internal", False))

    def test_apple_source_uses_apple(self):
        self.assertEqual(
            effective_from_pair(62, 48, SOURCE_APPLE), (48, "apple", False)
        )

    def test_apple_source_falls_back_when_no_apple_value(self):
        self.assertEqual(
            effective_from_pair(62, None, SOURCE_APPLE), (62, "internal", True)
        )

    def test_apple_source_with_neither_value(self):
        self.assertEqual(
            effective_from_pair(None, None, SOURCE_APPLE), (None, "internal", True)
        )

    def test_internal_none_with_apple_selected_uses_apple(self):
        self.assertEqual(
            effective_from_pair(None, 30, SOURCE_APPLE), (30, "apple", False)
        )


class ResolvePopularityTest(TempDataDirMixin, TestCase):
    """resolve_popularity() combines estimator + Apple lookup + setting."""

    FAKE_APPS = [
        {
            "trackName": f"App {i}",
            "userRatingCount": 1000 * (i + 1),
            "sellerName": f"Seller {i}",
        }
        for i in range(25)
    ]

    def test_internal_only_when_unset(self):
        res = resolve_popularity(self.FAKE_APPS, "fitness", "us")
        self.assertEqual(res.source, "internal")
        self.assertIsNotNone(res.internal)
        self.assertEqual(res.effective, res.internal)
        self.assertIsNone(res.apple)
        self.assertFalse(res.is_fallback)

    def test_apple_value_used_when_selected(self):
        AppleSearchPopularity.objects.create(term="fitness", country="us", popularity=71)
        self.set_source(SOURCE_APPLE)
        res = resolve_popularity(self.FAKE_APPS, "Fitness ", "us")  # normalization
        self.assertEqual(res.apple, 71)
        self.assertEqual(res.effective, 71)
        self.assertEqual(res.source, "apple")
        self.assertFalse(res.is_fallback)

    def test_fallback_when_apple_has_no_value(self):
        self.set_source(SOURCE_APPLE)
        res = resolve_popularity(self.FAKE_APPS, "obscure keyword", "us")
        self.assertTrue(res.is_fallback)
        self.assertEqual(res.source, "internal")
        self.assertEqual(res.effective, res.internal)

    def test_null_apple_row_means_fallback(self):
        AppleSearchPopularity.objects.create(term="tiny", country="us", popularity=None)
        self.set_source(SOURCE_APPLE)
        res = resolve_popularity(self.FAKE_APPS, "tiny", "us")
        self.assertTrue(res.is_fallback)

    def test_fallback_has_no_side_effects_when_not_connected(self):
        """No enrichment queue exists; an unconnected install's fallback
        is a pure local decision."""
        self.set_source(SOURCE_APPLE)
        with patch("aso.apple_ads.sync.ensure_country_dataset") as mock_ensure:
            res = resolve_popularity(self.FAKE_APPS, "New Keyword", "de")
        mock_ensure.assert_not_called()  # not connected -> no download
        self.assertTrue(res.is_fallback)

    def test_apple_lookup_dict_bypasses_db(self):
        self.set_source(SOURCE_APPLE)
        res = resolve_popularity(
            self.FAKE_APPS, "fitness", "us", apple_lookup={"fitness": 55}
        )
        self.assertEqual(res.effective, 55)
        self.assertEqual(res.source, "apple")

    def _connect_apple(self, country="us", week="2026-08-09"):
        """Simulate a verified v1 connection with an active dataset week."""
        from aso.apple_ads import storage as apple_storage

        apple_storage.save_apple_settings(apple_ads={
            "tested_ok": True,
            "client_id": "SEARCHADS.c", "team_id": "SEARCHADS.t",
            "key_id": "k", "ad_account_id": "1",
            "active_weeks": {country: week},
        })
        patcher = patch("aso.apple_ads.keys.has_private_key", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _dataset_row(self, term, popularity, country="us", week="2026-08-09"):
        from datetime import date

        from aso.models import AppleTopTerm

        AppleTopTerm.objects.create(
            term=term, country=country, genre="BUSINESS",
            week=date.fromisoformat(week), rank_in_genre=1,
            popularity_in_genre=90, popularity=popularity,
            popularity_tier=4,
        )

    def test_missing_term_materialized_from_local_dataset(self):
        """A term with no cache row is materialized from the local weekly
        dataset (a table read), so the first score of a new keyword
        already carries the real Apple value - no network involved."""
        self.set_source(SOURCE_APPLE)
        self._connect_apple()
        self._dataset_row("trading", 91)
        with patch("aso.apple_ads.sync.ensure_country_dataset") as mock_ensure:
            res = resolve_popularity(self.FAKE_APPS, "Trading", "us")
        mock_ensure.assert_not_called()  # dataset present -> pure local
        self.assertEqual(res.effective, 91)
        self.assertEqual(res.source, "apple")
        self.assertFalse(res.is_fallback)
        # The cache row was materialized for future lookups.
        self.assertEqual(
            AppleSearchPopularity.lookup("trading", "us"), 91
        )

    def test_materialization_regardless_of_selected_source(self):
        """Both values are displayed everywhere, so the Apple value is
        materialized even while the internal source is selected."""
        self._connect_apple()
        self._dataset_row("trading", 88)
        res = resolve_popularity(self.FAKE_APPS, "trading", "us")
        self.assertEqual(res.source, "internal")
        self.assertEqual(res.apple, 88)

    def test_absent_term_materializes_definitive_null(self):
        """A term missing from the active week is below Apple's reporting
        threshold - an explicit null row records that definitively."""
        self.set_source(SOURCE_APPLE)
        self._connect_apple()
        self._dataset_row("other term", 55)
        res = resolve_popularity(self.FAKE_APPS, "obscure tail", "us")
        self.assertTrue(res.is_fallback)
        rows = AppleSearchPopularity.objects.filter(
            term="obscure tail", country="us"
        )
        self.assertEqual(rows.count(), 1)
        self.assertIsNone(rows.first().popularity)

    def test_first_use_of_country_triggers_bounded_download(self):
        """A country with no dataset at all triggers the single bounded
        synchronous download path."""
        self.set_source(SOURCE_APPLE)
        self._connect_apple()  # active week for us only
        with patch("aso.apple_ads.sync.ensure_country_dataset") as mock_ensure:
            resolve_popularity(self.FAKE_APPS, "trading", "de")
        mock_ensure.assert_called_once_with("de")

    def test_known_term_never_rematerialized(self):
        """Null cache rows mean the dataset was already consulted."""
        self._connect_apple()
        AppleSearchPopularity.objects.create(
            term="fitness", country="us", popularity=None
        )
        with patch("aso.apple_ads.sync.ensure_country_dataset") as mock_ensure:
            resolve_popularity(self.FAKE_APPS, "fitness", "us")
        mock_ensure.assert_not_called()

    def test_apple_lookup_dict_never_triggers_materialization(self):
        """Batch paths prefetch up front; per-term resolution must not
        do its own work when a lookup dict is supplied."""
        self._connect_apple()
        with patch("aso.apple_ads.sync.ensure_country_dataset") as mock_ensure:
            resolve_popularity(self.FAKE_APPS, "fitness", "us", apple_lookup={})
        mock_ensure.assert_not_called()

    def test_materialization_failure_never_breaks_scoring(self):
        with patch(
            "aso.popularity._materialize_apple_rows",
            side_effect=RuntimeError("boom"),
        ):
            res = resolve_popularity(self.FAKE_APPS, "trading", "us")
        self.assertIsNotNone(res.effective)
        self.assertEqual(res.source, "internal")

    def test_popularity_fields_shape(self):
        res = resolve_popularity(self.FAKE_APPS, "fitness", "us")
        fields = popularity_fields(res)
        self.assertEqual(
            set(fields),
            {
                "popularity_internal",
                "popularity_apple",
                "popularity_source",
                "popularity_fallback",
                "popularity_cap",
                "popularity_genre",
            },
        )
        # Non-fallback rows carry no cap context.
        self.assertFalse(res.is_fallback)
        self.assertIsNone(fields["popularity_cap"])
        self.assertEqual(fields["popularity_genre"], "")

    def test_popularity_fields_carry_cap_context_on_fallback(self):
        """Fallback rows feed the badge popover the applied cap and the
        display label of the category it came from."""
        from aso.models import AppleTopTerm

        AppleTopTerm.clear_floor_cache()
        self.set_source(SOURCE_APPLE)
        self._connect_apple()
        self._dataset_row("other term", 55)  # BUSINESS floor 55 -> cap 54
        business_apps = [
            {**app, "primaryGenreName": "Business"} for app in self.FAKE_APPS
        ]
        res = resolve_popularity(business_apps, "obscure tail", "us")
        self.assertTrue(res.is_fallback)
        self.assertEqual(res.genre_hint, "BUSINESS")
        fields = popularity_fields(res)
        self.assertEqual(fields["popularity_cap"], 54)
        self.assertEqual(fields["popularity_genre"], "Business")

    def test_no_network_in_resolution_module(self):
        """aso.popularity must never import HTTP clients - DB lookups only."""
        import inspect

        import aso.popularity as mod

        source = inspect.getsource(mod)
        for banned in ("import requests", "urllib", "http.client"):
            self.assertNotIn(banned, source)


class SearchResultEffectiveTest(TempDataDirMixin, TestCase):
    """Model properties and stored classification follow the source setting."""

    def _make_result(self, internal=62, apple=25, difficulty=30):
        app = App.objects.create(name="Test App")
        kw = Keyword.objects.create(keyword="fitness", app=app)
        return SearchResult.objects.create(
            keyword=kw,
            popularity_score=internal,
            apple_popularity_score=apple,
            difficulty_score=difficulty,
            country="us",
        )

    def test_effective_properties_internal(self):
        r = self._make_result()
        self.assertEqual(r.effective_popularity, 62)
        self.assertEqual(r.popularity_source_used, "internal")
        self.assertFalse(r.popularity_is_fallback)

    def test_effective_properties_apple(self):
        r = self._make_result()
        self.set_source(SOURCE_APPLE)
        self.assertEqual(r.effective_popularity, 25)
        self.assertEqual(r.popularity_source_used, "apple")

    def test_classification_follows_active_source_on_save(self):
        self.set_source(SOURCE_APPLE)
        r = self._make_result(internal=62, apple=8, difficulty=30)
        # Apple value 8 → Low Volume regardless of the internal 62.
        self.assertEqual(r.classification, classify_keyword(8, 30))
        self.assertEqual(r.classification, "Low Volume")

    def test_opportunity_uses_effective(self):
        r = self._make_result(internal=62, apple=25, difficulty=30)
        internal_opp = r.opportunity_score
        self.set_source(SOURCE_APPLE)
        self.assertNotEqual(r.opportunity_score, internal_opp)

    def test_recompute_all_classifications_on_switch(self):
        r = self._make_result(internal=62, apple=8, difficulty=30)
        internal_label = r.classification
        self.set_source(SOURCE_APPLE)
        updated = recompute_all_classifications()
        r.refresh_from_db()
        self.assertEqual(updated, 1)
        self.assertNotEqual(r.classification, internal_label)
        self.assertEqual(r.classification, "Low Volume")

    def test_annotate_effective_popularity_matches_property(self):
        r = self._make_result(internal=62, apple=25)
        for source in ("", SOURCE_INTERNAL, SOURCE_APPLE):
            if source:
                self.set_source(source)
            annotated = annotate_effective_popularity(
                SearchResult.objects.filter(pk=r.pk)
            ).first()
            self.assertEqual(annotated.effective_pop, r.effective_popularity)

    def test_annotate_coalesces_missing_apple(self):
        r = self._make_result(internal=62, apple=None)
        self.set_source(SOURCE_APPLE)
        annotated = annotate_effective_popularity(
            SearchResult.objects.filter(pk=r.pk)
        ).first()
        self.assertEqual(annotated.effective_pop, 62)


class RefreshRowsEffectiveTest(TempDataDirMixin, TestCase):
    """Stored AI-session rows re-resolve under the current source setting
    when reused as refinement/re-simulate inputs (never mixing sources)."""

    def _row(self):
        return {
            "keyword": "fitness",
            "source": "ai_generated",  # keyword provenance - must not change
            "popularity": 62,
            "popularity_internal": 62,
            "popularity_apple": 25,
            "popularity_source": "internal",
            "popularity_fallback": False,
            "difficulty": 30,
            "opportunity": 55,
            "classification": "Sweet Spot",
            "downloads": {"stale": True},
        }

    def test_switch_to_apple_re_resolves_scores(self):
        from aso.popularity import refresh_rows_effective
        from aso.scoring import calc_opportunity, classify_keyword

        original = self._row()
        self.set_source(SOURCE_APPLE)
        row = refresh_rows_effective([original], "us")[0]
        self.assertEqual(row["popularity"], 25)
        self.assertEqual(row["popularity_source"], "apple")
        self.assertEqual(row["opportunity"], calc_opportunity(25, 30))
        self.assertEqual(row["classification"], classify_keyword(25, 30))
        self.assertIn("positions", row["downloads"])
        self.assertEqual(row["source"], "ai_generated")
        # The stored session row itself is untouched
        self.assertEqual(original["popularity"], 62)

    def test_missing_apple_value_falls_back(self):
        from aso.popularity import refresh_rows_effective

        source_row = self._row()
        source_row["popularity_apple"] = None
        self.set_source(SOURCE_APPLE)
        row = refresh_rows_effective([source_row], "us")[0]
        self.assertEqual(row["popularity"], 62)
        self.assertTrue(row["popularity_fallback"])

    def test_legacy_rows_without_dual_fields(self):
        from aso.popularity import refresh_rows_effective

        legacy = {"keyword": "old", "popularity": 40, "difficulty": 20,
                  "opportunity": 1, "classification": "?"}
        row = refresh_rows_effective([legacy], "us")[0]
        self.assertEqual(row["popularity"], 40)
        self.assertEqual(row["popularity_source"], "internal")

    def test_local_apple_table_overrides_stale_snapshot(self):
        """A refinement uses the freshest local Apple value, covering rows
        whose parent run predates the Apple connection."""
        from aso.popularity import refresh_rows_effective

        AppleSearchPopularity.objects.create(term="fitness", country="us", popularity=71)
        stale = self._row()
        stale["popularity_apple"] = None  # parent ran before Apple connected
        self.set_source(SOURCE_APPLE)
        row = refresh_rows_effective([stale], "us")[0]
        self.assertEqual(row["popularity"], 71)
        self.assertEqual(row["popularity_source"], "apple")
        self.assertFalse(row["popularity_fallback"])

    def test_missing_apple_terms_fall_back_without_side_effects(self):
        """Absence from the dataset is definitive for the active week -
        there is no enrichment queue to feed anymore."""
        from aso.popularity import refresh_rows_effective

        source_row = self._row()
        source_row["popularity_apple"] = None
        self.set_source(SOURCE_APPLE)
        refreshed = refresh_rows_effective([source_row], "us")
        self.assertTrue(refreshed[0]["popularity_fallback"])
        self.assertEqual(refreshed[0]["popularity_source"], "internal")


class RowsSourceUsedTest(TestCase):
    """Server-side derivation of the source a stored run used (mirrors
    formatRunSourceNote in popularity-display.js). Powers the source badge
    on the AI tabs' saved-run lists."""

    def test_apple_row_marks_run_as_apple(self):
        from aso.popularity import rows_source_used

        rows = [
            {"keyword": "a", "popularity_source": "internal"},
            {"keyword": "b", "popularity_source": "apple"},
        ]
        self.assertEqual(rows_source_used(rows), SOURCE_APPLE)

    def test_fallback_row_marks_run_as_apple(self):
        from aso.popularity import rows_source_used

        rows = [{"keyword": "a", "popularity_source": "internal", "popularity_fallback": True}]
        self.assertEqual(rows_source_used(rows), SOURCE_APPLE)

    def test_internal_and_legacy_rows_derive_internal(self):
        from aso.popularity import rows_source_used

        self.assertEqual(rows_source_used([{"keyword": "old"}]), SOURCE_INTERNAL)
        self.assertEqual(rows_source_used([]), SOURCE_INTERNAL)
        self.assertEqual(rows_source_used(None), SOURCE_INTERNAL)


class PromptSourceNoteTest(TempDataDirMixin, TestCase):
    def test_internal_note(self):
        self.assertIn("internal estimate", prompt_source_note())

    def test_apple_note(self):
        storage.save_apple_settings(popularity_source=SOURCE_APPLE)
        note = prompt_source_note()
        self.assertIn("Apple Ads", note)
        self.assertIn("estimate", note)  # fallback rule declared
        # Threshold semantics: the model must not read below-threshold
        # keywords as proof of irrelevance, and must know banded values
        # are low by design.
        self.assertIn("top 500 terms", note)
        self.assertIn("capped just below", note)
        self.assertIn("does not make the", note)
