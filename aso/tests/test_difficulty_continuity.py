"""Difficulty moves with its inputs: a small change never makes a big jump.

scoring-principles.instructions.md requires continuous curves. An audit on
2026-09-22 found difficulty breaking that in five places: the weak leader cap
vanished at exactly 1,000 reviews, a 20% title match ratio flipped the cap
from full to proportional, the rating volume and review velocity curves
jumped from 95 to 100 at their last point, and the small result set cap
ended at four apps. These sweeps walk each input in small steps and fail on
any step larger than the bound, so a new switch cannot slip in unnoticed.
"""

import inspect
import itertools

from django.test import SimpleTestCase, TestCase

from aso import services
from aso.services import DifficultyCalculator, rating_volume_score

KEYWORD = "focus timer"


def app(name, ratings, *, in_title=False, released="2021-01-01T00:00:00Z",
        seller=None, stars=4.5):
    title = f"{name} {KEYWORD.title()}" if in_title else name
    return {
        "trackId": abs(hash(name)) % 10_000_000,
        "trackName": title,
        "userRatingCount": int(ratings),
        "averageUserRating": stars,
        "releaseDate": released,
        "currentVersionReleaseDate": released,
        "primaryGenreName": "Productivity",
        "sellerName": seller or f"{name} Inc",
    }


def field(leader_reviews, *, matches=0, n=10, others=20_000):
    """A #1 app with ``leader_reviews`` and ``n - 1`` others behind it, the
    first ``matches`` of all of them carrying the keyword in their title."""
    apps = [app("Leader", leader_reviews, in_title=matches > 0)]
    for i in range(1, n):
        apps.append(app(f"App {i}", others, in_title=i < matches))
    return apps


def difficulty(competitors):
    return DifficultyCalculator().calculate(competitors, keyword=KEYWORD)[0]


def geometric(start, stop, step=1.02):
    value = start
    while value <= stop:
        yield value
        value *= step


class CurveEndsTest(SimpleTestCase):
    def test_rating_volume_has_no_step_at_its_old_clamp(self):
        self.assertLess(abs(rating_volume_score(100_001) - rating_volume_score(99_999)), 0.1)

    def test_rating_volume_keeps_rising_to_a_million(self):
        self.assertLess(rating_volume_score(500_000), rating_volume_score(1_000_000))
        self.assertEqual(rating_volume_score(1_000_000), 100)

    def test_review_velocity_has_no_step_at_its_old_clamp(self):
        calc = DifficultyCalculator()

        def velocity(per_year):
            # One app, released two years ago, with twice the yearly reviews.
            return calc._review_velocity_score([
                app("A", per_year * 2, released="2024-09-22T00:00:00Z"),
            ])

        self.assertLess(abs(velocity(50_500) - velocity(49_500)), 1.0)


class LeaderCapSweepTest(SimpleTestCase):
    def test_no_jump_as_the_leader_gains_reviews(self):
        """The cap used to vanish at 1,000 reviews: up to 20 points in one
        review. Across 100 to 100,000, in 2% steps, at several match ratios."""
        for matches in (0, 1, 3, 6):
            with self.subTest(matches=matches):
                previous = None
                for reviews in geometric(100, 100_000):
                    score = difficulty(field(reviews, matches=matches))
                    if previous is not None:
                        self.assertLessEqual(
                            abs(score - previous), 3,
                            f"{reviews:.0f} reviews, {matches} title matches: "
                            f"{previous} -> {score}",
                        )
                    previous = score

    def test_no_jump_as_title_matches_grow(self):
        """One more competitor with the keyword in its title moves the
        match ratio by a tenth. The old 20% switch made that a big step."""
        for reviews in (50, 300, 900):
            with self.subTest(leader=reviews):
                scores = [difficulty(field(reviews, matches=m)) for m in range(0, 11)]
                steps = [abs(b - a) for a, b in zip(scores, scores[1:])]
                self.assertLessEqual(max(steps), 10, scores)


class FieldStrengthSweepTest(SimpleTestCase):
    def test_no_jump_as_the_whole_field_grows(self):
        previous = None
        for ratings in geometric(10_000, 5_000_000):
            competitors = [app(f"App {i}", ratings, in_title=True) for i in range(10)]
            score = difficulty(competitors)
            if previous is not None:
                self.assertLessEqual(abs(score - previous), 3, f"{ratings:.0f}")
            previous = score


class ResultCountSweepTest(SimpleTestCase):
    def test_each_extra_result_raises_the_ceiling_by_ten_at_most(self):
        """Four apps capped difficulty at 40 and five removed the cap."""
        scores = []
        for n in range(1, 26):
            competitors = [app(f"App {i}", 500_000, in_title=True) for i in range(n)]
            scores.append(difficulty(competitors))
        steps = [b - a for a, b in zip(scores, scores[1:])]
        self.assertLessEqual(max(steps), 10, scores)


class OneLeaderRuleTest(SimpleTestCase):
    def test_the_cap_formula_lives_in_one_place(self):
        """The overall score and the tiers used to carry their own copies."""
        source = inspect.getsource(services)
        self.assertEqual(source.count("15 + 35 * math.log10("), 1)

    def test_tiers_follow_the_same_leader_rule(self):
        for reviews, matches in itertools.product((200, 1_500, 5_000), (0, 4)):
            with self.subTest(reviews=reviews, matches=matches):
                _, breakdown = DifficultyCalculator().calculate(
                    field(reviews, matches=matches, n=25), keyword=KEYWORD,
                )
                for tier in (breakdown.get("ranking_tiers") or {}).values():
                    if isinstance(tier, dict) and "score" in tier:
                        self.assertGreaterEqual(tier["score"], 1)
                        self.assertLessEqual(tier["score"], 100)


class StoredDifficultyRescoreTest(TestCase):
    """DIFFICULTY_VERSION re-scores history from each row's own competitors,
    for Search History rows and Country Opportunity Finder rows alike."""

    def test_both_kinds_of_row_are_rescored_and_keep_their_downloads(self):
        from aso.models import (
            App, Keyword, OpportunityScan, OpportunityScanResult, SearchResult,
        )
        from aso.popularity import recalculate_stored_difficulty

        competitors = field(1_500, matches=0)
        expected = difficulty(competitors)
        kw = Keyword.objects.create(keyword=KEYWORD, app=App.objects.create(name="A"))
        stored = SearchResult.objects.create(
            keyword=kw, country="us", popularity_score=50, difficulty_score=99,
            competitors_data=competitors,
            difficulty_breakdown={"download_estimates": {"marker": 1}},
        )
        scan = OpportunityScan.objects.create(keyword=KEYWORD, countries=["us"])
        scanned = OpportunityScanResult.objects.create(
            scan=scan, country="us", order_index=0, keyword_text=KEYWORD,
            difficulty_score=99, competitors_data=competitors,
            difficulty_breakdown={"download_estimates": {"marker": 2}},
        )

        stats = recalculate_stored_difficulty()

        stored.refresh_from_db()
        scanned.refresh_from_db()
        self.assertEqual(stored.difficulty_score, expected)
        self.assertEqual(scanned.difficulty_score, expected)
        self.assertEqual(stored.difficulty_breakdown["download_estimates"], {"marker": 1})
        self.assertEqual(scanned.difficulty_breakdown["download_estimates"], {"marker": 2})
        self.assertEqual(stats["rewritten"], 2)
