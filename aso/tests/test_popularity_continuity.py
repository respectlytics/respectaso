"""The popularity estimate moves with its inputs: a small change never makes a big jump.

scoring-principles.instructions.md requires continuous curves. The estimate
used to break that twice. It read the ratings of the strongest exact title
match in the top five, so one app's title gaining or losing the keyword, or
that app moving from #5 to #6, moved the estimate by about ten points. And it
took the leader from the top half of the results only, so a big app moving
from #12 to #13 of 25 removed the leader outright.

apple_estimator_study measured both (plan, D7 to D7e): one title can move
the shipped estimate by up to 17 points on real searches, and two
neighbours swapping by up to 38 when a search has only two or three
results. Two studies and nine smoother candidates found none that passed
the pre-registered gates, so the shipped estimator stays. Every bound below is therefore the
shipped estimator's measured steepest step with a small margin, as the plan
prescribes (D8): a change can make a jump smaller, never larger. The title
jump is named, with its study figure, so it is never mistaken for fine.
"""

from django.test import SimpleTestCase

from aso.management.commands.apple_estimator_study import _flip_title
from aso.services import PopularityEstimator
from aso.tests.difficulty_fixture import FIELDS, build

# A full first page of 25, built to sit on the old edges: an exact-title app
# at #5 with more ratings than the four above it, and the biggest app of all
# at #13, just past the old top-half cut.
FULL_PAGE = ("sleep sounds", [
    (90_000, 4.7, 2500, True), (60_000, 4.6, 2200, False), (40_000, 4.6, 1900, True),
    (30_000, 4.5, 1600, False), (250_000, 4.8, 3000, True), (20_000, 4.5, 1400, True),
    (15_000, 4.4, 1200, False), (12_000, 4.4, 1000, True), (9_000, 4.3, 900, False),
    (7_000, 4.3, 800, True), (5_000, 4.2, 700, False), (4_000, 4.2, 600, True),
    (2_000_000, 4.8, 3500, False), (3_000, 4.1, 500, True), (2_500, 4.1, 450, False),
    (2_000, 4.0, 400, True), (1_500, 4.0, 350, False), (1_200, 3.9, 300, True),
    (1_000, 3.9, 250, False), (800, 3.8, 200, True), (600, 3.8, 150, False),
    (400, 3.7, 120, True), (300, 3.7, 90, False), (200, 3.6, 60, True), (100, 3.5, 30, False),
], 20)


# The shape of the largest title jump the study found ("capcut pro", 16
# results): the #2 app is by far the biggest and does not carry the keyword.
# Give it the keyword and it becomes the strongest exact match in the top
# five, which is what the brand signal x_top1_exact reads.
BRAND_JUMP = ("video cutter", [
    (1_100_000, 4.7, 2500, False), (29_500_000, 4.7, 4000, False),
    (86_000, 4.6, 700, False), (317_000, 4.7, 2000, False),
    (18_000_000, 4.7, 3500, False), (9_000, 4.5, 900, True),
    (4_000, 4.4, 600, False), (2_000, 4.3, 500, True), (1_000, 4.2, 400, False),
    (700, 4.1, 300, True), (500, 4.0, 250, False), (300, 4.0, 200, False),
    (200, 3.9, 150, True), (100, 3.9, 100, False), (60, 3.8, 60, False),
    (30, 3.7, 30, False),
], 14)

# The shape of the largest neighbour swap the second study found
# ("documentos for seniors" in Mexico): two results, a giant and an app with
# no ratings. The leader is read from the top half of the results, which
# here is one app, so swapping the two replaces the leader outright.
TWO_RESULTS = ("document scanner seniors", [
    (6_000_000, 4.8, 3500, False), (0, 0.0, 30, False),
], 2)

# The known jumps of the shipped estimator. The title flip, an exact match
# crossing #5 and the fifth result arriving come from x_top1_exact (the
# rating count of the strongest exact-title match in the top five); the big
# swap comes from the leader cut at the middle of a very short result list.
# The open items in STRENGTH_AND_RANK_CALIBRATION_PLAN.md, D7c and D7e; each
# bound is the largest step measured (the studies where they found more than
# these fixtures), so a fix lowers them and nothing can raise them.
KNOWN_TITLE_FLIP = 17.0     # study: 16.7 on "capcut pro"; BRAND_JUMP: 14.1
KNOWN_SWAP = 38.0           # second study: 38 with two results; TWO_RESULTS
KNOWN_ONE_MORE_RESULT = 4.5  # FULL_PAGE, the fifth result: 4.3


def fields():
    for keyword, apps, publishers in FIELDS + [FULL_PAGE, BRAND_JUMP, TWO_RESULTS]:
        yield keyword, build(keyword, apps, publishers)


def raw_estimate(competitors, keyword) -> float:
    """The estimate before rounding, clamped exactly as estimate() clamps:
    the rounded one moves in steps of one by design."""
    estimator = PopularityEstimator()
    components = estimator.signal_components(competitors, keyword)
    raw = estimator.V2_WEIGHTS["intercept"] + sum(
        weight * components[name]
        for name, weight in estimator.V2_WEIGHTS.items()
        if name != "intercept"
    )
    return max(1.0, min(100.0, raw))


def with_ratings(competitor, ratings):
    return {**competitor, "userRatingCount": int(ratings)}


def geometric(start, stop, step=1.02):
    value = start
    while value <= stop:
        yield value
        value *= step


class RatingsMoveItSmoothlyTest(SimpleTestCase):
    """Two percent more ratings is a sliver, never a step. The bound is one
    point; the curves are log-interpolated, so a 2% change is 0.009 of a
    decade and the steepest weight moves well under a point for it (0.14 at
    most, measured). The sweep starts at 50 ratings, where two percent is at
    least one rating: below that a count moves in whole ratings."""

    def test_the_leader_gaining_ratings(self):
        for keyword, competitors in fields():
            previous = None
            for ratings in geometric(50, 3_000_000):
                current = raw_estimate(
                    [with_ratings(competitors[0], ratings)] + competitors[1:], keyword,
                )
                if previous is not None:
                    self.assertLessEqual(abs(current - previous), 1.0,
                                         f"{keyword} leader at {ratings:.0f}")
                previous = current

    def test_every_app_gaining_ratings_together(self):
        for keyword, competitors in fields():
            previous = None
            for factor in geometric(0.01, 50):
                current = raw_estimate(
                    [with_ratings(c, c["userRatingCount"] * factor) for c in competitors],
                    keyword,
                )
                if previous is not None:
                    self.assertLessEqual(abs(current - previous), 1.0,
                                         f"{keyword} field x{factor:.2f}")
                previous = current


class OneAppMovesItLittleTest(SimpleTestCase):
    """What one app does moves the estimate by its own share, never by the
    whole leader or the whole brand signal."""

    def test_one_title_gaining_or_losing_the_keyword(self):
        """The known jump: never above what the study measured."""
        worst = 0.0
        for keyword, competitors in fields():
            base = raw_estimate(competitors, keyword)
            for index in range(min(10, len(competitors))):
                flipped = list(competitors)
                flipped[index] = _flip_title(flipped[index], keyword)
                moved = raw_estimate(flipped, keyword)
                worst = max(worst, abs(moved - base))
                self.assertLessEqual(abs(moved - base), KNOWN_TITLE_FLIP,
                                     f"{keyword}: flipping #{index + 1}")
        # The fixture reproduces the jump, so a fix shows up here as a
        # failure of this line, and the bound above then comes down with it.
        self.assertGreater(worst, 12.0)

    def test_two_neighbours_swapping_places(self):
        """The known jump: never above what the study measured."""
        worst = 0.0
        for keyword, competitors in fields():
            base = raw_estimate(competitors, keyword)
            for index in range(len(competitors) - 1):
                swapped = list(competitors)
                swapped[index], swapped[index + 1] = swapped[index + 1], swapped[index]
                moved = raw_estimate(swapped, keyword)
                worst = max(worst, abs(moved - base))
                self.assertLessEqual(abs(moved - base), KNOWN_SWAP,
                                     f"{keyword}: #{index + 1} and #{index + 2} swap")
        # TWO_RESULTS reproduces it, so a fix shows up here first.
        self.assertGreater(worst, 25.0)

    def test_one_more_result(self):
        """A result count is a count: one more result is worth its own small
        step (2.5 points of the result signal before its weight, 3.3 at most
        from one result to two, where each app is half the field). The
        steepest, 4.3, is the fifth result arriving as an exact-title app:
        x_top1_exact again."""
        keyword, apps, publishers = FULL_PAGE
        competitors = build(keyword, apps, publishers)
        previous = None
        for n in range(1, len(competitors) + 1):
            current = raw_estimate(competitors[:n], keyword)
            if previous is not None:
                self.assertLessEqual(abs(current - previous), KNOWN_ONE_MORE_RESULT, f"{n} results")
            previous = current
