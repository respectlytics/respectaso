"""Tests for the genre-aware fallback cap - the numeric consistency
guarantee under the Apple source (Apple Ads Platform API v1 migration).

Design (docs/development plan): Apple reports each category's top 500
terms per storefront; a keyword ABSENT from the dataset is only provably
less popular than ITS OWN category's least-reported term. The calibrated
estimate therefore keeps its value, capped just below that category's
genre floor (the country's global floor when the category is
uninferred). The EST source itself stays pure (EST invariance).
"""

import datetime as dt

from django.test import TestCase

from aso.apple_ads import storage
from aso.models import AppleTopTerm, Keyword, SearchResult
from aso.popularity import (
    SOURCE_APPLE,
    SOURCE_INTERNAL,
    absent_cap,
    annotate_effective_popularity,
    effective_from_pair,
)
from aso.services import DownloadEstimator
from aso.tests.test_apple_ads_storage import StorageTestBase

WEEK = dt.date(2026, 8, 9)


def _dataset(country="us"):
    """Seed an active week with two genres and distinct floors
    (BUSINESS floor 47, GAMES floor 56 - mirrors live US data)."""
    for genre, values in (
        ("BUSINESS", (47, 55, 79)),
        ("GAMES", (56, 70, 86)),
    ):
        for rank, value in enumerate(values, start=1):
            AppleTopTerm.objects.create(
                term=f"{genre.lower()} covered {rank}", country=country,
                genre=genre, week=WEEK, rank_in_genre=rank,
                popularity_in_genre=100 - rank, popularity=value,
                popularity_tier=4,
            )
    active = dict(storage.load_apple_settings()["apple_ads"]["active_weeks"])
    active[country] = WEEK.isoformat()
    storage.save_apple_settings(apple_ads={"active_weeks": active})


class EffectiveFromPairCapTest(TestCase):
    def test_capped_fallback(self):
        # Estimate above the cap is capped; below passes through.
        self.assertEqual(
            effective_from_pair(70, None, SOURCE_APPLE, absent_ceiling=46),
            (46, SOURCE_INTERNAL, True),
        )
        self.assertEqual(
            effective_from_pair(24, None, SOURCE_APPLE, absent_ceiling=46),
            (24, SOURCE_INTERNAL, True),
        )

    def test_real_apple_value_never_capped(self):
        self.assertEqual(
            effective_from_pair(80, 47, SOURCE_APPLE, absent_ceiling=46),
            (47, SOURCE_APPLE, False),
        )

    def test_tier_a_no_ceiling_keeps_raw_estimate(self):
        self.assertEqual(
            effective_from_pair(80, None, SOURCE_APPLE, absent_ceiling=None),
            (80, SOURCE_INTERNAL, True),
        )

    def test_est_source_purity_ignores_ceiling(self):
        """EST invariance: capping never touches the internal source."""
        self.assertEqual(
            effective_from_pair(80, 47, SOURCE_INTERNAL, absent_ceiling=46),
            (80, SOURCE_INTERNAL, False),
        )
        self.assertEqual(
            effective_from_pair(80, None, "", absent_ceiling=46)[0], 80
        )


class AbsentCapTest(StorageTestBase):
    databases = "__all__"

    def test_genre_aware_caps(self):
        _dataset()
        # BUSINESS floor 47 -> cap 46; GAMES floor 56 -> cap 55.
        self.assertEqual(absent_cap("us", "BUSINESS"), 46)
        self.assertEqual(absent_cap("us", "GAMES"), 55)
        # Uninferred/unknown genre -> global floor 47 -> cap 46.
        self.assertEqual(absent_cap("us"), 46)
        self.assertEqual(absent_cap("us", "NO_SUCH_GENRE"), 46)

    def test_no_dataset_returns_none(self):
        self.assertIsNone(absent_cap("de"))
        self.assertIsNone(absent_cap(""))


class SearchResultCapParityTest(StorageTestBase):
    databases = "__all__"

    def _result(self, internal, apple, country="us", genre=""):
        keyword = Keyword.objects.create(
            keyword=f"kw {internal} {apple} {country} {genre}"
        )
        return SearchResult.objects.create(
            keyword=keyword, country=country, difficulty_score=30,
            popularity_score=internal, apple_popularity_score=apple,
            inferred_genre=genre,
        )

    def test_property_caps_by_inferred_genre(self):
        _dataset()
        storage.save_apple_settings(popularity_source="apple")
        covered = self._result(internal=90, apple=47)
        games_tail = self._result(internal=70, apple=None, genre="GAMES")
        business_tail = self._result(internal=70, apple=None, genre="BUSINESS")
        uninferred_tail = self._result(internal=70, apple=None)
        low_tail = self._result(internal=24, apple=None, genre="GAMES")
        uncovered_country = self._result(internal=90, apple=None, country="de")
        self.assertEqual(covered.effective_popularity, 47)
        self.assertEqual(games_tail.effective_popularity, 55)      # cap 55
        self.assertEqual(business_tail.effective_popularity, 46)   # cap 46
        self.assertEqual(uninferred_tail.effective_popularity, 46)  # global
        self.assertEqual(low_tail.effective_popularity, 24)        # below cap
        self.assertEqual(uncovered_country.effective_popularity, 90)  # Tier A
        self.assertTrue(games_tail.popularity_is_fallback)

    def test_annotate_matches_property_including_caps(self):
        _dataset()
        storage.save_apple_settings(popularity_source="apple")
        for internal, apple, country, genre in [
            (90, 47, "us", "BUSINESS"), (70, None, "us", "GAMES"),
            (70, None, "us", "BUSINESS"), (70, None, "us", ""),
            (24, None, "us", "GAMES"), (90, None, "de", ""),
            (63, 55, "de", ""),
        ]:
            self._result(internal, apple, country, genre)
        annotated = annotate_effective_popularity(SearchResult.objects.all())
        for row in annotated:
            self.assertEqual(
                row.effective_pop, row.effective_popularity,
                f"SQL/property mismatch: internal={row.popularity_score} "
                f"apple={row.apple_popularity_score} country={row.country} "
                f"genre={row.inferred_genre!r}",
            )

    def test_annotate_under_internal_source_unchanged(self):
        _dataset()
        storage.save_apple_settings(popularity_source="internal")
        self._result(90, 47)
        row = annotate_effective_popularity(SearchResult.objects.all()).get()
        self.assertEqual(row.effective_pop, 90)


class DownloadCurveTest(StorageTestBase):
    databases = "__all__"

    def test_single_curve_serves_both_sources(self):
        """One threshold-anchored curve (aso.scoring.POP_TO_SEARCHES) -
        both sources speak the calibrated 1-100 scale since estimate v2."""
        from aso.scoring import POP_TO_SEARCHES, _pop_to_searches

        est = DownloadEstimator()
        for source in ("internal", "apple"):
            storage.save_apple_settings(popularity_source=source)
            for value in (1, 25, 40, 79, 100):
                self.assertEqual(
                    est._daily_searches(value), _pop_to_searches(value),
                    f"curve mismatch at {value} under {source}",
                )
        self.assertEqual(POP_TO_SEARCHES[0], (1, 1))

    def test_threshold_anchor(self):
        """Covered values (40+) sit at/above the ~70/day anchor; the
        below-top-terms region stays under it."""
        est = DownloadEstimator()
        self.assertEqual(est._daily_searches(40), 70)
        for below in (1, 20, 39):
            self.assertLess(est._daily_searches(below), 70)

    def test_curve_monotonic(self):
        est = DownloadEstimator()
        values = [est._daily_searches(v) for v in range(1, 101)]
        self.assertEqual(values, sorted(values))


class EstimatorV2Test(TestCase):
    """The calibrated estimator: components/weights contract and brand
    separation via observable signals (no lists, no hardcoding)."""

    @staticmethod
    def _app(name, reviews, genre="Business"):
        return {"trackName": name, "userRatingCount": reviews,
                "primaryGenreName": genre}

    def test_estimate_is_weighted_sum_of_components(self):
        from aso.services import PopularityEstimator

        estimator = PopularityEstimator()
        competitors = [self._app(f"Indeed Job Search {i}", 1_000_000 // (i + 1))
                       for i in range(10)]
        components = estimator.signal_components(competitors, "indeed")
        expected = estimator.V2_WEIGHTS["intercept"] + sum(
            weight * components[name]
            for name, weight in estimator.V2_WEIGHTS.items()
            if name != "intercept"
        )
        expected = int(round(max(1, min(100, expected))))
        self.assertEqual(estimator.estimate(competitors, "indeed"), expected)

    def test_popular_brand_outranks_unknown_brand(self):
        """Same shape, different observable magnitude: a dominant
        exact-match leader with huge review counts must outrank an
        unknown name - learned, not hardcoded."""
        from aso.services import PopularityEstimator

        estimator = PopularityEstimator()
        popular = [self._app("Indeed Job Search", 2_500_000)] + [
            self._app(f"Job Finder {i}", 5_000) for i in range(24)
        ]
        unknown = [self._app("Zblorb", 40)] + [
            self._app(f"Unrelated App {i}", 3_000) for i in range(5)
        ]
        self.assertGreater(
            estimator.estimate(popular, "indeed"),
            estimator.estimate(unknown, "zblorb") + 20,
        )

    def test_no_competitors_returns_none(self):
        from aso.services import PopularityEstimator

        self.assertIsNone(PopularityEstimator().estimate([], "anything"))
        self.assertIsNone(
            PopularityEstimator().signal_components([], "anything")
        )

    def test_output_clipped_to_1_100(self):
        from aso.services import PopularityEstimator

        estimator = PopularityEstimator()
        tiny = [self._app("x", 0)]
        huge = [self._app("mega app exact", 10_000_000, "Games")] * 25
        self.assertGreaterEqual(estimator.estimate(tiny, "zz"), 1)
        self.assertLessEqual(estimator.estimate(huge, "mega app exact"), 100)


class RecalculateStoredPopularityTest(StorageTestBase):
    databases = "__all__"

    def test_recompute_rewrites_from_frozen_competitors(self):
        from aso.popularity import recalculate_stored_popularity
        from aso.services import PopularityEstimator

        keyword = Keyword.objects.create(keyword="indeed")
        competitors = [
            {"trackName": "Indeed Job Search", "userRatingCount": 2_500_000,
             "primaryGenreName": "Business"},
            {"trackName": "Job Finder", "userRatingCount": 5_000,
             "primaryGenreName": "Business"},
        ]
        result = SearchResult.objects.create(
            keyword=keyword, country="us", difficulty_score=30,
            popularity_score=28,  # old-scale value
            competitors_data=competitors,
        )
        no_data = SearchResult.objects.create(
            keyword=Keyword.objects.create(keyword="bare"), country="us",
            difficulty_score=30, popularity_score=50, competitors_data=[],
        )
        stats = recalculate_stored_popularity()
        result.refresh_from_db()
        no_data.refresh_from_db()
        expected = PopularityEstimator().estimate(competitors, "indeed")
        self.assertEqual(result.popularity_score, expected)
        self.assertEqual(result.inferred_genre, "BUSINESS")
        self.assertEqual(no_data.popularity_score, 50)  # untouched
        self.assertEqual(stats["rewritten"], 1)
        self.assertEqual(stats["skipped_no_competitors"], 1)

    def test_version_upgrade_runs_once(self):
        from unittest import mock

        from aso.popularity import maybe_upgrade_estimator_version

        with mock.patch(
            "aso.popularity.recalculate_stored_popularity",
            return_value={"total": 0, "rewritten": 0,
                          "skipped_no_competitors": 0, "reclassified": 0},
        ) as recompute:
            maybe_upgrade_estimator_version()
            maybe_upgrade_estimator_version()  # marker set: no second run
        self.assertEqual(recompute.call_count, 1)
        block = storage.load_apple_settings()["apple_ads"]
        self.assertEqual(block["est_version"], 2)


class PopularityCellPopoverTest(StorageTestBase):
    """The server-side twin renders ONE number per cell plus a badge
    popover that explains the row with its own numbers (mirrors
    resolveTip in popularity-display.js)."""

    databases = "__all__"

    def _result(self, internal, apple, country="us", genre=""):
        keyword = Keyword.objects.create(
            keyword=f"cell {internal} {apple} {country} {genre}"
        )
        return SearchResult.objects.create(
            keyword=keyword, country=country, difficulty_score=30,
            popularity_score=internal, apple_popularity_score=apple,
            inferred_genre=genre,
        )

    def _render(self, result):
        from aso.templatetags.aso_tags import popularity_cell

        return str(popularity_cell(result))

    def test_capped_fallback_popover_states_the_numbers(self):
        _dataset()  # GAMES floor 56 -> cap 55
        storage.save_apple_settings(popularity_source="apple")
        html = self._render(self._result(internal=70, apple=None, genre="GAMES"))
        self.assertIn(">55<", html)                 # capped effective value
        self.assertIn("EST*", html)
        self.assertIn("Not in Apple&#x27;s top terms - capped", html)
        self.assertIn("the Games category", html)
        self.assertIn("lowest reported value there (56)", html)
        self.assertIn("estimate of 70 is scored as 55", html)
        # ONE number per cell: the old secondary line markup is gone.
        self.assertNotIn("block text-[10px] mt-0.5", html)

    def test_below_bar_fallback_popover(self):
        _dataset()  # BUSINESS floor 47 -> cap 46
        storage.save_apple_settings(popularity_source="apple")
        html = self._render(
            self._result(internal=30, apple=None, genre="BUSINESS")
        )
        self.assertIn(">30<", html)                 # estimate unchanged
        self.assertIn("powers the score unchanged", html)
        self.assertNotIn("capped", html)

    def test_uncovered_storefront_popover(self):
        storage.save_apple_settings(popularity_source="apple")
        html = self._render(self._result(internal=80, apple=None, country="de"))
        self.assertIn(">80<", html)                 # raw estimate, uncapped
        self.assertIn("No Apple data for this storefront", html)

    def test_asa_popover_offers_estimate_comparison(self):
        _dataset()
        storage.save_apple_settings(popularity_source="apple")
        html = self._render(self._result(internal=70, apple=47))
        self.assertIn(">47<", html)
        self.assertIn(">ASA<", html)
        self.assertIn("RespectASO estimate for comparison: 70", html)

    def test_est_badge_is_quiet_not_amber(self):
        """EST* is styled as quietly as EST by design - fallback is the
        normal case, not a warning."""
        _dataset()
        storage.save_apple_settings(popularity_source="apple")
        html = self._render(self._result(internal=70, apple=None, genre="GAMES"))
        self.assertNotIn("amber", html)
