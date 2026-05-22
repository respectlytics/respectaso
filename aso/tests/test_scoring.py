"""Tests for centralized ASO scoring functions."""

from django.test import TestCase

from aso.scoring import calc_opportunity, classify_keyword, _pop_to_searches


class PopToSearchesTest(TestCase):
    """Test the popularity → daily searches interpolation."""

    def test_zero_popularity(self):
        self.assertEqual(_pop_to_searches(0), 0)

    def test_negative_popularity(self):
        self.assertEqual(_pop_to_searches(-5), 0)

    def test_none_popularity(self):
        self.assertEqual(_pop_to_searches(None), 0)

    def test_exact_table_points(self):
        self.assertEqual(_pop_to_searches(50), 200)
        self.assertEqual(_pop_to_searches(100), 32_000)
        self.assertEqual(_pop_to_searches(5), 1)

    def test_interpolation_between_points(self):
        val = _pop_to_searches(55)
        self.assertEqual(val, 290)
        # Midpoint between 50→200 and 55→290 should be between them
        mid = _pop_to_searches(52)
        self.assertGreater(mid, 200)
        self.assertLess(mid, 290)

    def test_below_first_point(self):
        val = _pop_to_searches(2)
        self.assertGreater(val, 0)
        self.assertLess(val, 1)

    def test_above_last_point(self):
        self.assertEqual(_pop_to_searches(110), 32_000)


class CalcOpportunityTest(TestCase):
    """Test the new opportunity formula."""

    def test_zero_popularity_always_zero(self):
        self.assertEqual(calc_opportunity(0, 0), 0)
        self.assertEqual(calc_opportunity(0, 50), 0)
        self.assertEqual(calc_opportunity(0, 100), 0)

    def test_max_difficulty_always_zero(self):
        self.assertEqual(calc_opportunity(100, 100), 0)
        self.assertEqual(calc_opportunity(50, 100), 0)

    def test_ideal_keyword(self):
        # pop=100, diff=0 → should be 100
        self.assertEqual(calc_opportunity(100, 0), 100)

    def test_key_scenarios(self):
        # Verify the score table from the plan
        self.assertEqual(calc_opportunity(100, 30), 91)
        self.assertEqual(calc_opportunity(100, 50), 75)
        self.assertEqual(calc_opportunity(100, 70), 51)
        self.assertEqual(calc_opportunity(100, 90), 18)
        self.assertEqual(calc_opportunity(80, 30), 66)
        self.assertEqual(calc_opportunity(50, 0), 51)
        self.assertEqual(calc_opportunity(50, 50), 38)
        self.assertEqual(calc_opportunity(30, 20), 33)
        self.assertEqual(calc_opportunity(10, 10), 13)

    def test_monotonic_popularity(self):
        """Higher popularity → higher opportunity (same difficulty)."""
        prev = 0
        for pop in range(10, 101, 10):
            opp = calc_opportunity(pop, 30)
            self.assertGreaterEqual(opp, prev)
            prev = opp

    def test_monotonic_difficulty(self):
        """Higher difficulty → lower opportunity (same popularity)."""
        prev = 100
        for diff in range(0, 101, 10):
            opp = calc_opportunity(50, diff)
            self.assertLessEqual(opp, prev)
            prev = opp

    def test_clamped_to_0_100(self):
        self.assertGreaterEqual(calc_opportunity(1, 99), 0)
        self.assertLessEqual(calc_opportunity(100, 0), 100)

    def test_negative_popularity_returns_zero(self):
        self.assertEqual(calc_opportunity(-10, 50), 0)


class ClassifyKeywordTest(TestCase):
    """Test keyword classification."""

    def test_sweet_spot_direct(self):
        self.assertEqual(classify_keyword(50, 30), "Sweet Spot")

    def test_good_target(self):
        # pop=85, diff=50 → opp=59 → Good Target
        self.assertEqual(classify_keyword(85, 50), "Good Target")

    def test_hidden_gem(self):
        # pop=30 → 35 daily searches, diff=20 → easy to rank
        self.assertEqual(classify_keyword(30, 20), "Hidden Gem")

    def test_hidden_gem_requires_real_volume(self):
        """pop=20 → only 10 daily searches — NOT a Hidden Gem."""
        self.assertNotEqual(classify_keyword(20, 20), "Hidden Gem")

    def test_hidden_gem_requires_minimum_opportunity(self):
        """pop=25, diff=29 → opp≈6 — too low to be a Hidden Gem."""
        self.assertNotEqual(classify_keyword(25, 29), "Hidden Gem")

    def test_hidden_gem_with_good_opportunity(self):
        """pop=35, diff=15 → high opp → still Hidden Gem."""
        self.assertEqual(classify_keyword(35, 15), "Hidden Gem")

    def test_low_volume(self):
        self.assertEqual(classify_keyword(10, 50), "Low Volume")

    def test_high_competition(self):
        self.assertEqual(classify_keyword(60, 70), "High Competition")

    def test_high_competition_extreme(self):
        """period tracker case: pop=97, diff=84 → High Competition."""
        self.assertEqual(classify_keyword(97, 84), "High Competition")

    def test_avoid(self):
        self.assertEqual(classify_keyword(20, 60), "Avoid")

    def test_moderate(self):
        self.assertEqual(classify_keyword(35, 45), "Moderate")

    def test_zero_popularity(self):
        self.assertEqual(classify_keyword(0, 50), "Low Volume")
