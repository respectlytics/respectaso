"""The rules the opportunity score must obey in every storefront.

Written before the score that satisfies them. A user compares countries, so
the promise is not "the number is roughly right" but "if you compare any two
of the 175 storefronts, the comparison means something".

These run across the WHOLE registry against a grid of inputs rather than a
handful of examples, because a rule with a country-shaped exception in it is
not a rule.
"""

import itertools

from django.test import SimpleTestCase

from aso import countries
from aso.scoring import calc_opportunity, expected_downloads
from aso.strength import AppProfile

# A grid wide enough to cover what the app actually produces.
POPULARITIES = (5, 20, 40, 50, 63, 80, 95)
DIFFICULTIES = (10, 31, 45, 60, 74, 90)
ALL_CODES = sorted(countries.CODES)


def _spread(count: int = 8) -> tuple[str, ...]:
    """Storefronts evenly spaced across the market sizes, smallest to largest.

    Taken from the registry rather than typed out, so the sample follows the
    table if it ever changes and there is no second country list to drift.
    """
    ordered = sorted(countries.COUNTRIES.values(), key=lambda c: (c.market, c.code))
    step = (len(ordered) - 1) / (count - 1)
    return tuple(ordered[round(i * step)].code for i in range(count))


SPREAD = _spread()
# Enough spread to catch a market-size-shaped bug without 175 x 42 x 175 pairs.
SAMPLE_PAIRS = tuple(itertools.combinations(SPREAD, 2))

# A fixed "now" is not needed: every comparison below is between two apps
# scored at the same moment, and the sweeps measure steps, not levels.
THREE_YEARS_AGO = "2023-09-01T00:00:00Z"


def app_with(ratings, average=4.5, released=THREE_YEARS_AGO):
    """A typical app of yours: the given rating count, 4.5 stars, three years
    on the store. None for a brand new app."""
    if ratings is None:
        return None
    return AppProfile(name="Test App", ratings=int(ratings), average=average,
                      released=released)


class EveryStorefrontTest(SimpleTestCase):
    """Rule 4: one arithmetic, no country-shaped special cases."""

    def test_every_storefront_produces_a_score_in_range(self):
        for code in ALL_CODES:
            for popularity, difficulty in itertools.product(POPULARITIES, DIFFICULTIES):
                score = calc_opportunity(popularity, difficulty, code)
                if not 0 <= score <= 100:
                    self.fail(f"{code} pop={popularity} diff={difficulty} -> {score}")

    def test_every_storefront_produces_expected_downloads(self):
        for code in ALL_CODES:
            value = expected_downloads(50, 45, code)
            self.assertGreaterEqual(value, 0, code)

    def test_no_storefront_is_special_cased(self):
        """Two countries with the same market size and the same inputs must
        score identically, whatever their names."""
        by_market = {}
        for country in countries.COUNTRIES.values():
            by_market.setdefault(round(country.market, 4), []).append(country.code)
        twins = [codes for codes in by_market.values() if len(codes) > 1]
        self.assertTrue(twins, "expected some storefronts to share a market size")
        for codes in twins[:20]:
            scores = {calc_opportunity(50, 45, code) for code in codes}
            self.assertEqual(len(scores), 1, f"{codes} disagreed: {scores}")


class OrderingTest(SimpleTestCase):
    """Rule 1: the comparison a user makes between two countries must hold."""

    def test_more_reachable_downloads_always_means_a_higher_score(self):
        for a, b in SAMPLE_PAIRS:
            for popularity, difficulty in itertools.product(POPULARITIES, DIFFICULTIES):
                down_a = expected_downloads(popularity, difficulty, a)
                down_b = expected_downloads(popularity, difficulty, b)
                score_a = calc_opportunity(popularity, difficulty, a)
                score_b = calc_opportunity(popularity, difficulty, b)
                if down_a > down_b and not score_a >= score_b:
                    self.fail(
                        f"{a} reaches {down_a:.3f}/day and scores {score_a}, "
                        f"{b} reaches {down_b:.3f}/day and scores {score_b}"
                    )

    def test_the_case_that_started_this(self):
        """"fasting", scored in three storefronts on the same day.

        The scores were Bulgaria 29, Israel 40, United States 26, while the
        chart underneath showed the United States paying a thousand times
        more. The ordering must follow the downloads, not the difficulty.
        """
        cases = {
            "us": (63, 74),   # 1,840 searches a day, brutal competition
            "il": (57, 48),   # 32 searches a day, moderate
            "bg": (50, 43),   # 2.4 searches a day, moderate
        }
        downloads = {c: expected_downloads(p, d, c) for c, (p, d) in cases.items()}
        scores = {c: calc_opportunity(p, d, c) for c, (p, d) in cases.items()}
        self.assertEqual(
            sorted(scores, key=scores.get, reverse=True),
            sorted(downloads, key=downloads.get, reverse=True),
            f"scores {scores} did not follow downloads {downloads}",
        )
        # For a brand new app Israel edges the United States: difficulty 74
        # leaves a new app far down the US results and 48 much higher in
        # Israel, and the rank line under each score says so. An established
        # app reaches far enough up the US results for the size of that
        # market to win, which is the comparison the user made.
        for ratings in (2_000, 20_000):
            with self.subTest(ratings=ratings):
                app_scores = {
                    c: calc_opportunity(p, d, c, app=app_with(ratings), app_rank=None)
                    for c, (p, d) in cases.items()
                }
                self.assertGreater(app_scores["us"], app_scores["il"])
                self.assertGreater(app_scores["il"], app_scores["bg"])

    def test_a_small_market_wins_only_when_it_actually_pays_more(self):
        """The rule is about downloads, not about the size of the flag.

        Holding position 1 in a small storefront can genuinely beat scraping
        position 20 in a large one, and when it does the score must say so.
        """
        top_of_bulgaria = expected_downloads(50, 10, "bg")
        bottom_of_the_us = expected_downloads(50, 95, "us")
        self.assertGreater(top_of_bulgaria, bottom_of_the_us)
        self.assertGreaterEqual(
            calc_opportunity(50, 10, "bg"), calc_opportunity(50, 95, "us"),
        )


class MonotonicTest(SimpleTestCase):
    """Rule 3: the score moves the way the inputs move, always."""

    def test_harder_is_never_better(self):
        for code in SPREAD:
            for popularity in POPULARITIES:
                scores = [calc_opportunity(popularity, d, code) for d in sorted(DIFFICULTIES)]
                self.assertEqual(
                    scores, sorted(scores, reverse=True),
                    f"{code} pop={popularity}: {scores}",
                )

    def test_more_sought_after_is_never_worse(self):
        for code in SPREAD:
            for difficulty in DIFFICULTIES:
                scores = [calc_opportunity(p, difficulty, code) for p in sorted(POPULARITIES)]
                self.assertEqual(
                    scores, sorted(scores),
                    f"{code} diff={difficulty}: {scores}",
                )

    def test_a_bigger_market_is_never_worse(self):
        ordered = sorted(ALL_CODES, key=lambda c: countries.get(c).market)
        for popularity, difficulty in ((50, 45), (63, 74), (40, 30)):
            scores = [calc_opportunity(popularity, difficulty, c) for c in ordered]
            self.assertEqual(
                scores, sorted(scores),
                f"a smaller market outscored a larger one at pop={popularity}",
            )


class NoContradictionTest(SimpleTestCase):
    """Rule 2: a row can never argue with itself."""

    def test_under_one_search_a_day_is_never_above_low_volume(self):
        """A continuous score cannot force an empty market to exactly zero:
        one search a day at #1 is worth a little, and snapping it to zero
        would be a jump. What must hold is that it never looks like more:
        under 25, and tagged Low Volume or Avoid, for any app."""
        from aso.scoring import MIN_DAILY_SEARCHES, classify_keyword, daily_searches

        for code in ALL_CODES:
            if daily_searches(50, code) >= MIN_DAILY_SEARCHES:
                continue
            for difficulty, ratings in itertools.product((5, 20, 60, 90), (None, 1_000_000)):
                score = calc_opportunity(50, difficulty, code, app=app_with(ratings), app_rank=None)
                tag = classify_keyword(50, difficulty, code, app=app_with(ratings), app_rank=None)
                self.assertLess(score, 25, f"{code} d={difficulty} r={ratings}")
                self.assertIn(tag, ("Low Volume", "Avoid"), f"{code} d={difficulty}")

    def test_nothing_reachable_means_no_opportunity(self):
        for code in SPREAD:
            self.assertEqual(expected_downloads(0, 50, code), 0)
            self.assertEqual(calc_opportunity(0, 50, code), 0)

    def test_a_positive_score_always_has_downloads_behind_it(self):
        for code in ALL_CODES:
            for popularity, difficulty in itertools.product(POPULARITIES, DIFFICULTIES):
                if calc_opportunity(popularity, difficulty, code) > 0:
                    self.assertGreater(
                        expected_downloads(popularity, difficulty, code), 0,
                        f"{code} pop={popularity} diff={difficulty} scored above "
                        "zero with no downloads behind it",
                    )


class AppStrengthTest(SimpleTestCase):
    """A stronger app reaches higher, so the same keyword is worth more."""

    def test_a_stronger_app_never_scores_lower(self):
        for code in SPREAD:
            by_ratings = [
                calc_opportunity(63, 74, code, app=app_with(r), app_rank=None)
                for r in (0, 100, 1000, 10000, 100000)
            ]
            self.assertEqual(by_ratings, sorted(by_ratings), f"{code}: {by_ratings}")
            by_stars = [
                calc_opportunity(63, 74, code, app=app_with(5_000, average=a), app_rank=None)
                for a in (2.5, 3.5, 4.0, 4.5, 4.9)
            ]
            self.assertEqual(by_stars, sorted(by_stars), f"{code}: {by_stars}")
            by_age = [
                calc_opportunity(63, 74, code, app_rank=None, app=app_with(
                    5_000, released=f"{year}-01-01T00:00:00Z"))
                for year in (2026, 2024, 2021, 2016)
            ]
            self.assertEqual(by_age, sorted(by_age), f"{code}: {by_age}")

    def test_no_app_means_the_hardest_case(self):
        """The documented default: assume a new app with no ratings."""
        for code in SPREAD:
            self.assertEqual(
                calc_opportunity(63, 74, code, app=None),
                calc_opportunity(63, 74, code, app=AppProfile(ratings=0), app_rank=None),
                code,
            )

    def test_a_real_rank_only_ever_helps(self):
        """Where the app already ranks is used when it is better than the
        model's estimate, and ignored when it is worse."""
        from aso.scoring import reachable_rank

        app = app_with(500)
        model = reachable_rank(74, app)
        for code in SPREAD:
            # Downloads, not the integer score: a tiny storefront can sit at 0
            # either way.
            base = expected_downloads(63, 74, code, app=app, app_rank=None)
            self.assertGreater(expected_downloads(63, 74, code, app=app, app_rank=1), base)
            self.assertEqual(
                expected_downloads(63, 74, code, app=app, app_rank=int(model) + 40), base,
            )


class OneNumberEverywhereTest(SimpleTestCase):
    """Rule 5: the same inputs give the same score on every screen."""

    def test_the_stored_row_and_the_scan_row_agree(self):
        from aso.models import OpportunityScanResult, SearchResult

        stored = SearchResult(country="de", popularity_score=63, difficulty_score=74)
        scanned = OpportunityScanResult(
            country="de", popularity_score=63, difficulty_score=74,
        )
        self.assertEqual(stored.opportunity_score, scanned.opportunity_score)

    def test_the_score_is_a_pure_function_of_its_inputs(self):
        first = [calc_opportunity(50, 45, c) for c in ALL_CODES]
        second = [calc_opportunity(50, 45, c) for c in ALL_CODES]
        self.assertEqual(first, second)


class ContinuityTest(SimpleTestCase):
    """Rule 6: no jumps. scoring-principles.instructions.md, enforced.

    The first version of this score dropped "period tracker" from 67 to 0
    between difficulty 77 and 78, because downloads ended at #20. The rank
    is now a smooth curve and downloads follow one continuous tap-through
    curve to any depth, so the score can only move in small steps. The
    bounds sit just above what the model produces (3.2 points per difficulty
    point at its steepest), so a new edge anywhere fails here.
    """

    RATINGS = (None, 100, 5_000, 200_000)

    def _score(self, popularity, difficulty, code, ratings=None, *, app=None, app_rank=None):
        from aso.scoring import expected_downloads, OPPORTUNITY_POINTS_PER_DECADE
        import math

        if app is None:
            app = app_with(ratings)
        # The unrounded score: the integer one moves in steps of one by design.
        downloads = expected_downloads(popularity, difficulty, code, app=app, app_rank=app_rank)
        if downloads <= 0:
            return 0.0
        return max(0.0, min(100.0, 50 + OPPORTUNITY_POINTS_PER_DECADE * math.log10(downloads)))

    def test_no_jump_as_difficulty_rises(self):
        for code, ratings in itertools.product(SPREAD, self.RATINGS):
            for popularity in range(5, 101, 5):
                previous = self._score(popularity, 0, code, ratings)
                for i in range(1, 401):
                    current = self._score(popularity, i / 4, code, ratings)
                    self.assertLessEqual(
                        abs(current - previous), 1.0,
                        f"{code} pop={popularity} ratings={ratings} "
                        f"difficulty {i / 4}: {previous:.1f} -> {current:.1f}",
                    )
                    previous = current

    def test_no_jump_as_popularity_rises(self):
        for code, ratings in itertools.product(SPREAD, self.RATINGS):
            for difficulty in (0, 20, 40, 60, 80, 100):
                previous = self._score(5, difficulty, code, ratings)
                for i in range(21, 401):
                    current = self._score(i / 4, difficulty, code, ratings)
                    self.assertLessEqual(abs(current - previous), 0.75)
                    previous = current

    def test_no_jump_as_the_app_gains_ratings(self):
        for code in SPREAD:
            for popularity, difficulty in ((48, 38), (80, 60), (97, 84)):
                previous = self._score(popularity, difficulty, code, 1)
                ratings = 1.0
                while ratings < 2_000_000:
                    ratings *= 1.02
                    current = self._score(popularity, difficulty, code, ratings)
                    self.assertLessEqual(abs(current - previous), 1.5)
                    previous = current

    def test_no_jump_as_the_average_rating_moves(self):
        """A hundredth of a star is a hundredth of a star, anywhere on the
        scale: the rating curve is linear between its points."""
        for code in SPREAD:
            for popularity, difficulty in ((48, 38), (80, 60), (97, 84)):
                previous = None
                for hundredths in range(0, 501):
                    app = app_with(5_000, average=hundredths / 100)
                    current = self._score(popularity, difficulty, code, app=app)
                    if previous is not None:
                        self.assertLessEqual(
                            abs(current - previous), 0.5,
                            f"{code} pop={popularity} diff={difficulty} "
                            f"stars {hundredths / 100}: {previous:.2f} -> {current:.2f}",
                        )
                    previous = current

    def test_no_jump_as_the_app_ages(self):
        """A week on the store changes the score by a sliver, never a step,
        including at the half year where momentum stops being floored."""
        from datetime import datetime, timedelta, timezone

        now = datetime.now(timezone.utc)
        for code in SPREAD:
            for popularity, difficulty in ((48, 38), (97, 84)):
                previous = None
                for weeks in range(0, 52 * 12):
                    released = (now - timedelta(weeks=weeks)).isoformat()
                    app = app_with(2_000, released=released)
                    current = self._score(popularity, difficulty, code, app=app)
                    if previous is not None:
                        self.assertLessEqual(
                            abs(current - previous), 0.5,
                            f"{code} {weeks} weeks: {previous:.2f} -> {current:.2f}",
                        )
                    previous = current

    def test_a_real_rank_moves_the_score_by_what_that_rank_pays(self):
        """One place in the real results moves the score by exactly what the
        tap-through curve says one place is worth, and never more: the
        steepest step is #1 to #2, 20 x log10(0.30 / 0.18) = 4.4 points.
        Where the real rank crosses the model's estimate, the better of the
        two takes over without a step."""
        import math

        from aso.scoring import OPPORTUNITY_POINTS_PER_DECADE
        from aso.services import DownloadEstimator

        for code in SPREAD:
            for ratings in (None, 500, 50_000):
                app = app_with(ratings)
                previous = self._score(80, 60, code, app=app, app_rank=1)
                for rank in range(2, 261):
                    current = self._score(80, 60, code, app=app, app_rank=rank)
                    worth = OPPORTUNITY_POINTS_PER_DECADE * math.log10(
                        DownloadEstimator.ttr_at(rank - 1) / DownloadEstimator.ttr_at(rank)
                    )
                    self.assertLessEqual(previous - current, worth + 1e-9,
                                         f"{code} r={ratings} #{rank - 1} -> #{rank}")
                    self.assertLessEqual(abs(current - previous), 4.5)
                    self.assertGreaterEqual(previous, current)
                    previous = current

    def test_the_old_cliff_is_gone(self):
        """67 to 0 in one difficulty point, now a slope."""
        self.assertLess(
            abs(calc_opportunity(97, 77, "us") - calc_opportunity(97, 78, "us")), 3,
        )

    def test_the_download_curve_is_the_table_at_whole_ranks(self):
        """The chart must not move: whole ranks 1 to 20 are the table."""
        from aso.services import DownloadEstimator

        for rank, rate in DownloadEstimator._TTR.items():
            self.assertAlmostEqual(DownloadEstimator.ttr_at(rank), rate)
        self.assertLess(DownloadEstimator.ttr_at(21), DownloadEstimator.ttr_at(20))
        self.assertGreater(DownloadEstimator.ttr_at(250), 0)

