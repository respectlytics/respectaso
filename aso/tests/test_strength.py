"""One strength yardstick, for the competitors and for your app.

aso/strength.py holds every factor curve and weight. DifficultyCalculator
aggregates them over the field; AppProfile.strength() applies them to one
app. These tests pin that moving the curves there moved no difficulty, and
that every curve, and strength itself, changes smoothly with its input.
"""

import json
import os
from datetime import datetime
from unittest import mock

from django.test import SimpleTestCase

from aso import strength
from aso.services import DifficultyCalculator
from aso.strength import AppProfile, NEW_APP
from aso.tests.difficulty_fixture import FIELDS, FIXED_NOW, build


class _FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW


class DifficultyUnchangedTest(SimpleTestCase):
    """The refactor moved the curves, not the numbers."""

    def test_every_fixture_field_scores_as_before(self):
        path = os.path.join(os.path.dirname(__file__), "difficulty_before_strength.json")
        before = json.load(open(path))
        with mock.patch("aso.services.datetime", _FrozenDatetime), \
                mock.patch("aso.strength._utcnow", lambda: FIXED_NOW):
            for keyword, apps, publishers in FIELDS:
                with self.subTest(keyword=keyword):
                    score, breakdown = DifficultyCalculator().calculate(
                        build(keyword, apps, publishers), keyword=keyword,
                    )
                    self.assertEqual(score, before[keyword]["score"])
                    for name, value in before[keyword]["subs"].items():
                        self.assertEqual(breakdown.get(name), value, name)
                    tiers = {n: t.get("score") for n, t in
                             (breakdown.get("ranking_tiers") or {}).items() if isinstance(t, dict)}
                    self.assertEqual(tiers, before[keyword]["tiers"])


def _worst_step(values):
    return max(abs(b - a) for a, b in zip(values, values[1:]))


def _geometric(a, b, f=1.02):
    x = a
    while x <= b:
        yield x
        x *= f


class CurvesAreSmoothTest(SimpleTestCase):
    def test_volume(self):
        self.assertLess(_worst_step([strength.volume_score(r) for r in _geometric(1, 5_000_000)]), 1.0)

    def test_momentum(self):
        self.assertLess(_worst_step([strength.momentum_score(r) for r in _geometric(1, 2_000_000)]), 1.0)

    def test_rating(self):
        self.assertLess(_worst_step([strength.rating_score(i / 100) for i in range(1, 501)]), 1.0)

    def test_age(self):
        self.assertLess(_worst_step([strength.age_score(i / 100) for i in range(1, 1501)]), 1.0)

    def test_every_curve_rises(self):
        for curve, xs in ((strength.volume_score, _geometric(1, 5_000_000)),
                          (strength.momentum_score, _geometric(1, 2_000_000)),
                          (strength.rating_score, [i / 100 for i in range(1, 501)]),
                          (strength.age_score, [i / 100 for i in range(1, 1501)])):
            values = [curve(x) for x in xs]
            self.assertEqual(values, sorted(values), curve.__name__)


class AppStrengthTest(SimpleTestCase):
    """Your app, on the same scale as the competitors' factors."""

    def test_a_new_app_has_only_its_title(self):
        # Title factor 100 at a weight of 0.10 out of 0.70: 14.3.
        self.assertAlmostEqual(NEW_APP.strength(), 100 * 0.10 / 0.70, places=6)

    def test_more_of_anything_is_never_weaker(self):
        base = dict(name="A", ratings=1_000, average=4.2, released="2023-09-22T00:00:00Z")
        with mock.patch("aso.strength._utcnow", lambda: FIXED_NOW):
            reference = AppProfile(**base).strength()
            self.assertGreater(AppProfile(**{**base, "ratings": 5_000}).strength(), reference)
            self.assertGreater(AppProfile(**{**base, "average": 4.7}).strength(), reference)
            self.assertGreater(
                AppProfile(**{**base, "released": "2018-09-22T00:00:00Z"}).strength(), reference,
            )

    def test_strength_moves_smoothly_with_each_factor(self):
        with mock.patch("aso.strength._utcnow", lambda: FIXED_NOW):
            by_ratings = [AppProfile(ratings=int(r), average=4.5, released="2022-01-01T00:00:00Z").strength()
                          for r in _geometric(1, 5_000_000)]
            by_stars = [AppProfile(ratings=2_000, average=i / 100, released="2022-01-01T00:00:00Z").strength()
                        for i in range(100, 501)]
            by_age = [AppProfile(ratings=2_000, average=4.5,
                                 released=f"{2026 - y // 12}-{12 - y % 12:02d}-01T00:00:00Z").strength()
                      for y in range(1, 120)]
        self.assertLess(_worst_step(by_ratings), 0.5)
        self.assertLess(_worst_step(by_stars), 0.5)
        self.assertLess(_worst_step(by_age), 1.0)

    def test_a_search_result_becomes_a_profile(self):
        profile = AppProfile.from_result({
            "trackName": "Calm", "userRatingCount": 1234, "averageUserRating": 4.6,
            "releaseDate": "2020-01-01T00:00:00Z",
        })
        self.assertEqual((profile.ratings, profile.average), (1234, 4.6))


class RankModelIsSmoothTest(SimpleTestCase):
    """The measured rank table is a smooth, monotone curve of the gap.

    Log-linear between measured points and flat beyond the ends, so a tenth
    of a point of gap moves the rank by at most a tenth of the steepest
    segment's slope: 0.124 in log rank per point, between #1.58 and #3.09.
    """

    STEEPEST_LOG_RANK_PER_POINT = 0.125

    def test_the_table_is_ordered(self):
        from aso.scoring import RANK_BY_GAP

        gaps = [g for g, _ in RANK_BY_GAP]
        ranks = [r for _, r in RANK_BY_GAP]
        self.assertEqual(gaps, sorted(gaps))
        self.assertEqual(len(set(gaps)), len(gaps))
        self.assertEqual(ranks, sorted(ranks))
        self.assertGreaterEqual(ranks[0], 1.0)

    def test_no_step_anywhere_across_the_gap(self):
        import math

        from aso.scoring import rank_from_gap

        previous = rank_from_gap(-100.0)
        for tenth in range(-999, 1501):
            current = rank_from_gap(tenth / 10)
            self.assertGreaterEqual(current, previous, tenth / 10)
            self.assertLessEqual(
                math.log(current) - math.log(previous),
                self.STEEPEST_LOG_RANK_PER_POINT / 10,
                f"gap {tenth / 10}: #{previous:.2f} -> #{current:.2f}",
            )
            previous = current

    def test_the_ends(self):
        """Flat below the strongest measured point; past the deepest one the
        last slope continues to DEEPEST_RANK and stops there."""
        from aso.scoring import DEEPEST_RANK, RANK_BY_GAP, rank_from_gap

        self.assertEqual(rank_from_gap(-500), RANK_BY_GAP[0][1])
        self.assertGreater(rank_from_gap(RANK_BY_GAP[-1][0] + 5), RANK_BY_GAP[-1][1])
        self.assertEqual(rank_from_gap(500), DEEPEST_RANK)

    def test_the_steepest_segment_is_the_one_the_bound_names(self):
        import math

        from aso.scoring import RANK_BY_GAP

        slopes = [
            (math.log(r1) - math.log(r0)) / (g1 - g0)
            for (g0, r0), (g1, r1) in zip(RANK_BY_GAP, RANK_BY_GAP[1:])
        ]
        self.assertLessEqual(max(slopes), self.STEEPEST_LOG_RANK_PER_POINT)

    def test_a_stronger_app_never_ranks_worse(self):
        from aso.scoring import reachable_rank

        for difficulty in (10, 40, 74, 95):
            ranks = [
                reachable_rank(difficulty, AppProfile(ratings=r, average=4.5,
                                                      released="2022-01-01T00:00:00Z"))
                for r in (0, 10, 100, 1_000, 10_000, 100_000, 1_000_000)
            ]
            self.assertEqual(ranks, sorted(ranks, reverse=True), difficulty)
