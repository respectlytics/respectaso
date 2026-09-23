"""Tests for centralized ASO scoring functions."""

import math

from django.test import TestCase

from aso.scoring import GOOD_TARGET_SCORE, calc_opportunity, classify_keyword, _pop_to_searches


class PopToSearchesTest(TestCase):
    """Test the popularity → daily searches interpolation."""

    def test_zero_popularity(self):
        self.assertEqual(_pop_to_searches(0), 0)

    def test_negative_popularity(self):
        self.assertEqual(_pop_to_searches(-5), 0)

    def test_none_popularity(self):
        self.assertEqual(_pop_to_searches(None), 0)

    def test_exact_table_points(self):
        # Threshold-anchored curve: the dataset floor (40) maps to the
        # ~500-searches/week eligibility bar (~70/day).
        self.assertEqual(_pop_to_searches(40), 70)
        self.assertEqual(_pop_to_searches(50), 280)
        self.assertEqual(_pop_to_searches(100), 300_000)
        self.assertEqual(_pop_to_searches(1), 1)

    def test_interpolation_between_points(self):
        val = _pop_to_searches(55)
        self.assertEqual(val, 570)
        # Midpoint between 50→280 and 55→570 should be between them
        mid = _pop_to_searches(52)
        self.assertGreater(mid, 280)
        self.assertLess(mid, 570)

    def test_below_top_terms_region_stays_under_anchor(self):
        for value in (1, 20, 39):
            self.assertLess(_pop_to_searches(value), 70)

    def test_above_last_point(self):
        self.assertEqual(_pop_to_searches(110), 300_000)


class CalcOpportunityTest(TestCase):
    """Test the new opportunity formula."""

    def test_zero_popularity_always_zero(self):
        self.assertEqual(calc_opportunity(0, 0), 0)
        self.assertEqual(calc_opportunity(0, 50), 0)
        self.assertEqual(calc_opportunity(0, 100), 0)

    def test_max_difficulty_is_worth_little_to_a_new_app(self):
        """Even the biggest keyword at the top of the difficulty scale is no
        more than a Hidden Gem's worth to a brand new app, and a middling one
        is worth nothing. Not exactly zero for the biggest: new apps do reach
        the first page of hard keywords now and then, and the measured rank
        counts that."""
        self.assertLess(calc_opportunity(100, 100), GOOD_TARGET_SCORE)
        self.assertEqual(calc_opportunity(50, 100), 0)

    def test_ideal_keyword(self):
        # pop=100, diff=0 → should be 100
        self.assertEqual(calc_opportunity(100, 0), 100)

    def test_the_scale_is_anchored_to_downloads(self):
        """The whole point of the scale: it can be said in one sentence.

        One download a day is 50, and every tenfold change moves it 20
        points, so the scale runs from about one download a year at the
        bottom to a few hundred a day at the top. That is what makes the
        number comparable between two storefronts.
        """
        from aso.scoring import (
            OPPORTUNITY_MIDPOINT_DOWNLOADS,
            OPPORTUNITY_POINTS_PER_DECADE,
        )

        self.assertEqual(OPPORTUNITY_MIDPOINT_DOWNLOADS, 1.0)
        cases = [
            (0.0001, 0), (0.00316, 0), (0.01, 10), (0.1, 30),
            (1.0, 50), (10.0, 70), (100.0, 90), (316.0, 100),
        ]
        for downloads, expected in cases:
            with self.subTest(downloads=downloads):
                score = 50 + OPPORTUNITY_POINTS_PER_DECADE * math.log10(downloads)
                self.assertEqual(round(max(0, min(100, score))), expected)

    def test_the_bottom_of_the_scale_is_about_one_download_a_year(self):
        """Zero is a claim, so it has to mean something sayable."""
        from aso.scoring import OPPORTUNITY_POINTS_PER_DECADE

        floor = 10 ** (-50 / OPPORTUNITY_POINTS_PER_DECADE)
        self.assertLess(1 / floor, 400)      # under about a year per download
        self.assertGreater(1 / floor, 250)

    def test_a_score_is_the_downloads_behind_it(self):
        """No input may reach the score except through expected downloads."""
        from aso.scoring import (
            OPPORTUNITY_POINTS_PER_DECADE,
            calc_opportunity,
            expected_downloads,
        )

        for popularity, difficulty, country in (
            (100, 30, "us"), (63, 74, "us"), (50, 43, "bg"), (57, 48, "il"),
        ):
            with self.subTest(country=country, popularity=popularity):
                downloads = expected_downloads(popularity, difficulty, country)
                score = calc_opportunity(popularity, difficulty, country)
                if downloads <= 0:
                    self.assertEqual(score, 0)
                else:
                    raw = 50 + OPPORTUNITY_POINTS_PER_DECADE * math.log10(downloads)
                    self.assertEqual(score, max(0, min(100, int(round(raw)))))

    def test_a_brutal_keyword_is_worth_little_to_a_new_app(self):
        """"period tracker", difficulty 84: brand new apps get the taps of
        about #93 on average, and the keyword is worth little to one. Little,
        not nothing: the score follows the downloads down a curve instead of
        falling off an edge at #20."""
        from aso.scoring import expected_downloads, reachable_position

        self.assertGreater(reachable_position(84), 60)
        self.assertGreater(expected_downloads(97, 84, "us"), 0.0)
        self.assertLess(calc_opportunity(97, 84, "us"), GOOD_TARGET_SCORE)
        self.assertEqual(classify_keyword(97, 84, "us"), "Worth Climbing")
        # And the same keyword is within reach once the app has weight.
        from aso.strength import AppProfile

        established = AppProfile(ratings=100_000, average=4.7,
                                 released="2016-01-01T00:00:00Z")
        self.assertLess(reachable_position(84, app=established), reachable_position(84) / 4)

    def test_the_reachable_position_falls_as_the_keyword_hardens(self):
        from aso.scoring import reachable_position

        positions = [reachable_position(d) for d in range(0, 101)]
        self.assertEqual(positions, sorted(positions))
        # Brand new apps land at different places even on the easiest
        # keyword, so the rank whose taps they can expect is not #1.
        self.assertGreater(reachable_position(0), 1)
        self.assertLess(reachable_position(0), 10)

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
    """The five tags on real keywords (KEYWORD_DECISIONS_PLAN.md, round 4)."""

    def test_sweet_spot_is_ten_downloads_a_day_or_more(self):
        self.assertEqual(classify_keyword(80, 5, "us"), "Sweet Spot")

    def test_good_target_is_one_to_ten_a_day(self):
        self.assertEqual(classify_keyword(80, 25, "us"), "Good Target")

    def test_supporting_is_one_every_ten_days_to_one_a_day(self):
        """"limits" in the owner's AI Researcher run: 43 for a new app."""
        self.assertEqual(classify_keyword(61, 16, "us"), "Supporting")

    def test_the_owners_two_rows_with_the_same_demand_get_the_same_tag(self):
        """"social media monitor" (difficulty 41) and "social media monitoring"
        (difficulty 9) both pay 0.9 to 3.5 a day at #1 in the United States.
        They read Low Volume and Moderate, because the ceiling was only asked
        when the app's own value was low (2026-09-23)."""
        self.assertEqual(classify_keyword(35, 41, "us"), "Low Volume")
        self.assertEqual(classify_keyword(35, 9, "us"), "Low Volume")

    def test_the_demand_bar_is_where_the_column_reads_one_a_day(self):
        """"school mode" pays 1.0 to 3.9 a day at #1: real demand, and a new
        app gets little today."""
        self.assertEqual(classify_keyword(39, 10, "us"), "Worth Climbing")

    def test_low_volume_when_nobody_searches(self):
        self.assertEqual(classify_keyword(50, 43, "bg"), "Low Volume")
        self.assertEqual(classify_keyword(10, 50, "us"), "Low Volume")
        self.assertEqual(classify_keyword(20, 80, "ag"), "Low Volume")

    def test_real_demand_out_of_reach_today_is_worth_climbing(self):
        """"period tracker", popularity 97 at difficulty 84, and "fasting":
        enormous demand, little for a new app today."""
        self.assertEqual(classify_keyword(97, 84, "us"), "Worth Climbing")
        self.assertEqual(classify_keyword(63, 74, "us"), "Worth Climbing")

    def test_zero_popularity(self):
        self.assertEqual(classify_keyword(0, 50), "Low Volume")


class EveryTagIsOneRangeTest(TestCase):
    """The audit of 2026-09-23, kept as tests: every tag is one range of one
    number on its row, for every storefront size, app strength and rank. A
    tag can then never contradict the columns beside it, and two rows that
    agree on a number never disagree about it."""

    COUNTRIES = ("us", "ar", "bg", "ad")
    POPULARITIES = range(1, 101, 3)
    DIFFICULTIES = range(0, 101, 3)
    ORDER = {"Low Volume": 0, "Worth Climbing": 1, "Supporting": 2, "Good Target": 3, "Sweet Spot": 4}

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        from aso.strength import AppProfile

        cls.apps = [
            None,
            AppProfile(name="Calm Minutes", ratings=20, average=4.2, released="2025-06-01"),
            AppProfile(name="Calm Minutes", ratings=3000, average=4.6, released="2022-01-01"),
            AppProfile(name="Calm Minutes", ratings=200000, average=4.8, released="2015-01-01"),
        ]

    def _rows(self):
        for country in self.COUNTRIES:
            for app in self.apps:
                for rank in ((None,) if app is None else (None, 2, 30)):
                    keyword = "calm minutes" if rank == 2 else "sleep sounds"
                    yield country, app, rank, keyword

    def test_low_volume_is_decided_by_demand_alone(self):
        """The same keyword in the same storefront is Low Volume for every app
        and at every difficulty, or for none."""
        from aso.scoring import has_real_demand

        for country, app, rank, keyword in self._rows():
            for popularity in self.POPULARITIES:
                demand = has_real_demand(popularity, country)
                for difficulty in self.DIFFICULTIES:
                    label = classify_keyword(popularity, difficulty, country, app, rank, keyword)
                    self.assertEqual(label == "Low Volume", not demand,
                                     (country, popularity, difficulty, rank))

    def test_every_other_tag_is_a_range_of_the_score(self):
        from aso.scoring import GOOD_TARGET_SCORE, SUPPORTING_SCORE, SWEET_SPOT_SCORE

        for country, app, rank, keyword in self._rows():
            for popularity in self.POPULARITIES:
                for difficulty in self.DIFFICULTIES:
                    label = classify_keyword(popularity, difficulty, country, app, rank, keyword)
                    if label == "Low Volume":
                        continue
                    score = calc_opportunity(popularity, difficulty, country, app, rank, keyword)
                    expected = ("Sweet Spot" if score >= SWEET_SPOT_SCORE else
                                "Good Target" if score >= GOOD_TARGET_SCORE else
                                "Supporting" if score >= SUPPORTING_SCORE else "Worth Climbing")
                    self.assertEqual(label, expected, (country, popularity, difficulty, rank, score))

    def test_the_bars_are_round_numbers_of_downloads_a_day(self):
        from aso.scoring import (
            GOOD_TARGET_SCORE,
            OPPORTUNITY_POINTS_PER_DECADE,
            REAL_DEMAND_PER_DAY,
            SUPPORTING_SCORE,
            SWEET_SPOT_SCORE,
        )

        per_day = [10 ** ((bar - 50) / OPPORTUNITY_POINTS_PER_DECADE)
                   for bar in (SWEET_SPOT_SCORE, GOOD_TARGET_SCORE, SUPPORTING_SCORE)]
        self.assertEqual([round(x, 6) for x in per_day], [10.0, 1.0, 0.1])
        self.assertEqual(REAL_DEMAND_PER_DAY, 1.0)

    def test_the_demand_bar_reads_the_column_as_printed(self):
        """Low Volume exactly when the low end of Downloads at #1, as the
        table prints it, is under 1.0."""
        from aso.scoring import fmt_downloads, has_real_demand, top_spot_range

        for country in self.COUNTRIES:
            for popularity in range(1, 101):
                shown = fmt_downloads(top_spot_range(popularity, country)[0])
                shown = float(shown[:-1]) * 1000 if shown.endswith("K") else float(shown)
                self.assertEqual(has_real_demand(popularity, country), shown >= 1.0,
                                 (country, popularity, shown))

    def test_a_harder_keyword_never_gets_a_better_tag(self):
        for country, app, rank, keyword in self._rows():
            for popularity in self.POPULARITIES:
                tags = [self.ORDER[classify_keyword(popularity, d, country, app, rank, keyword)]
                        for d in self.DIFFICULTIES]
                self.assertEqual(tags, sorted(tags, reverse=True), (country, popularity, rank))

    def test_more_searches_never_get_a_worse_tag(self):
        for country, app, rank, keyword in self._rows():
            for difficulty in self.DIFFICULTIES:
                tags = [self.ORDER[classify_keyword(p, difficulty, country, app, rank, keyword)]
                        for p in self.POPULARITIES]
                self.assertEqual(tags, sorted(tags), (country, difficulty, rank))

    def test_a_stronger_app_never_gets_a_worse_tag(self):
        for country in self.COUNTRIES:
            for popularity in self.POPULARITIES:
                for difficulty in self.DIFFICULTIES:
                    tags = [self.ORDER[classify_keyword(popularity, difficulty, country, app)]
                            for app in self.apps]
                    self.assertEqual(tags, sorted(tags), (country, popularity, difficulty))

    def test_each_summary_names_its_range(self):
        from aso.scoring import CLASSIFICATION_LABELS, CLASSIFICATION_SUMMARY

        self.assertEqual(list(CLASSIFICATION_SUMMARY), CLASSIFICATION_LABELS)
        self.assertIn("10 or more", CLASSIFICATION_SUMMARY["Sweet Spot"])
        self.assertIn("1 to 10", CLASSIFICATION_SUMMARY["Good Target"])
        self.assertIn("One every 10 days to 1 a day", CLASSIFICATION_SUMMARY["Supporting"])
        self.assertIn("1+ a day at #1", CLASSIFICATION_SUMMARY["Worth Climbing"])
        self.assertIn("Under 1 a day even at #1", CLASSIFICATION_SUMMARY["Low Volume"])


class TheRealRankWhenTheTitleCarriesTheKeywordTest(TestCase):
    """KEYWORD_DECISIONS_PLAN.md, D1: when the app's title already carries the
    keyword, its real rank is what carrying it does for this app."""

    def app(self, name="Soccer Betting Tips & Odds", ratings=5_000):
        from aso.strength import AppProfile

        return AppProfile(name=name, ratings=ratings, average=4.6,
                          released="2021-01-01T00:00:00Z")

    def test_a_worse_real_rank_is_used_when_the_title_carries_it(self):
        from aso.scoring import reachable_position

        app = self.app()
        model = reachable_position(40, app)
        self.assertLess(model, 71)
        self.assertEqual(reachable_position(40, app, 71, keyword="betting tips"), 71)

    def test_without_it_in_the_title_the_better_of_the_two_stays(self):
        from aso.scoring import reachable_position

        app = self.app(name="Score Predictor")
        model = reachable_position(40, app)
        self.assertEqual(reachable_position(40, app, 71, keyword="betting tips"), model)
        self.assertEqual(reachable_position(40, app, 2, keyword="betting tips"), 2)

    def test_the_score_and_the_sentence_follow(self):
        from aso.scoring import opportunity_reach

        app = self.app()
        with_rank = calc_opportunity(52, 40, "us", app=app, app_rank=71, keyword="betting tips")
        self.assertLess(with_rank, calc_opportunity(52, 40, "us", app=app, app_rank=None,
                                                    keyword="betting tips"))
        reach = opportunity_reach(52, 40, "us", app=app, app_rank=71, keyword="betting tips")
        from aso.scoring import short_app_name

        self.assertEqual(reach["label"], f"{short_app_name('Soccer Betting Tips & Odds')} ranks #71")
        self.assertIn("has \u201cbetting tips\u201d in its title and ranks #71", reach["explanation"])
        self.assertIn("Holding #1 here would bring", reach["explanation"])


class EveryLowScoreShowsItsCeilingTest(TestCase):
    def test_the_explanation_ends_with_what_the_top_spot_pays(self):
        from aso.scoring import opportunity_reach

        self.assertIn("Holding #1 here would bring", opportunity_reach(63, 74, "us")["explanation"])

    def test_an_empty_market_does_not_promise_a_ceiling(self):
        from aso.scoring import opportunity_reach

        self.assertNotIn("Holding #1", opportunity_reach(43, 31, "ag")["explanation"])


class EveryBadgeSentenceKnowsWhoseItIsTest(TestCase):
    """KEYWORD_DECISIONS_PLAN.md, round 2, D6: the badge sentence is composed
    for this row, for the app by name or for a new app, never "your app"."""

    def app(self, name="Soccer Betting Tips & Odds", ratings=5_000):
        from aso.strength import AppProfile

        return AppProfile(name=name, ratings=ratings, average=4.6,
                          released="2021-01-01T00:00:00Z")

    def advice(self, *args, **kwargs):
        from aso.scoring import get_targeting_advice

        return get_targeting_advice(*args, **kwargs)

    def test_a_new_app_is_spoken_of_as_a_new_app(self):
        _, label, _, text = self.advice(52, 40, "us")
        self.assertEqual(label, "Worth Climbing")
        self.assertTrue(text.startswith("Worth targeting."))
        self.assertIn("but a new app can expect only", text)
        self.assertIn("for now", text)
        self.assertIn("a title with it also ranks for longer searches that contain it", text)

    def test_an_app_that_already_ranks_is_never_told_it_lands_lower(self):
        """The owner's "volatility trading" row: Rank #23, and the line under
        the score said "typically ~#52" (2026-09-23)."""
        from aso.scoring import opportunity_reach, typical_landing

        app = self.app(name="Options Trading AI : OpPreds", ratings=173)
        self.assertGreater(typical_landing(44, app)[0], 23)
        reach = opportunity_reach(48, 44, "us", app=app, app_rank=23, keyword="volatility trading")
        self.assertIn("ranks #23 for \u201cvolatility trading\u201d without it in its title", reach["explanation"])
        self.assertIn("already does better than most of them", reach["explanation"])
        label = self.advice(48, 44, "us", app, 23, "volatility trading")[1]
        text = self.advice(48, 44, "us", app, 23, "volatility trading")[3]
        self.assertNotIn("typically land", text, label)

    def test_a_low_volume_keyword_the_app_ranks_for_is_kept(self):
        text = self.advice(30, 20, "us", self.app(name="Calm Minutes"), 2, "tiny")[3]
        self.assertTrue(text.startswith("Skip."))
        self.assertIn("Calm Minutes already ranks #2 for it: keep it where it costs nothing", text)

    def test_no_row_sentence_says_your_app_when_there_is_no_app(self):
        from aso.scoring import opportunity_reach

        for pop in (20, 35, 45, 52, 63, 75, 90):
            for diff in (5, 20, 40, 60, 80, 95):
                for country in ("us", "de", "ar", "ag"):
                    with self.subTest(pop=pop, diff=diff, country=country):
                        text = self.advice(pop, diff, country)[3]
                        self.assertNotIn("your app", text.lower())
                        self.assertNotIn("your app",
                                         opportunity_reach(pop, diff, country)["explanation"].lower())

    def test_the_app_by_name_when_its_title_carries_it(self):
        text = self.advice(52, 40, "us", self.app(), 71, "betting tips")[3]
        self.assertIn("Soccer Betting Tips & Odds ranks #71 with \u201cbetting tips\u201d in its title", text)
        self.assertIn("pays more as the app gains ratings and climbs", text)
        self.assertNotIn("new app", text)

    def test_the_app_by_name_when_its_real_rank_is_better_without_the_title(self):
        text = self.advice(63, 80, "us", self.app(name="Score Predictor"), 3, "betting tips")[3]
        self.assertIn("Score Predictor already ranks #3", text)

    def test_the_app_by_name_at_the_rank_apps_like_it_get(self):
        text = self.advice(63, 80, "us", self.app(name="Score Predictor"), None, "betting tips")[3]
        self.assertIn("apps as strong as score predictor, with \u201cbetting tips\u201d in their title, "
                      "typically land around #", text.lower())

    def test_target_now_says_where_the_app_stands(self):
        _, label, _, text = self.advice(80, 10, "us", keyword="meditation")
        self.assertIn(label, ("Sweet Spot", "Good Target", "Supporting"))
        self.assertTrue(text.startswith(
            "Target now. A new app with \u201cmeditation\u201d in its title typically lands around #"))

    def test_skip_names_the_keyword_the_store_and_the_range(self):
        _, label, _, text = self.advice(39, 27, "fr", keyword="minuteur")
        self.assertEqual(label, "Low Volume")
        self.assertTrue(text.startswith(
            "Skip. Even #1 for \u201cminuteur\u201d in the France App Store brings only "))
        self.assertIn(" to ", text)

    def test_every_sentence_names_the_keyword_it_is_about(self):
        """A tooltip over a filtered table must say which row it belongs to:
        the owner read "ranks #2" for the row that ranks #14 (2026-09-23)."""
        from aso.scoring import CLASSIFICATION_LABELS, opportunity_reach

        seen = set()
        for app, rank in ((None, None), (self.app(name="Options Trading AI"), 14),
                          (self.app(name="Score Predictor"), None), (self.app(name="Score Predictor"), 3)):
            for pop, diff in ((80, 10), (63, 80), (52, 40), (39, 27), (61, 16)):
                keyword = "options trading"
                reach = opportunity_reach(pop, diff, "us", app=app, app_rank=rank, keyword=keyword)
                label = self.advice(pop, diff, "us", app, rank, keyword)[1]
                text = self.advice(pop, diff, "us", app, rank, keyword)[3]
                seen.add(label)
                with self.subTest(pop=pop, diff=diff, rank=rank, label=label):
                    self.assertIn("\u201coptions trading\u201d", reach["explanation"])
                    self.assertIn("\u201coptions trading\u201d", text)
        self.assertLessEqual(len(set(CLASSIFICATION_LABELS) - seen), 1)

    def test_under_one_search_a_day_says_so(self):
        _, label, _, text = self.advice(43, 31, "ag")
        self.assertEqual(text, "Skip. This keyword gets under one search a day in the "
                               "Antigua & Barbuda App Store.")

    def test_the_legend_speaks_to_you_never_to_your_app(self):
        from aso.scoring import classification_legend

        for item in classification_legend():
            self.assertNotIn("your app", item["description"].lower(), item["label"])
            self.assertNotIn("your app", item["summary"].lower(), item["label"])


class TheCeilingQuotesTheColumnTest(TestCase):
    """Round 2, D8: the sentence says the range the Downloads at #1 column
    shows, not only its low end."""

    def test_the_explanation_quotes_the_range_of_the_column(self):
        from aso.scoring import fmt_downloads, opportunity_reach
        from aso.services import DownloadEstimator

        first = DownloadEstimator().estimate(52, country="us")["positions"][0]
        column = f"{fmt_downloads(first['downloads_low'])} to {fmt_downloads(first['downloads_high'])}"
        self.assertIn(f"Holding #1 here would bring {column} downloads a day.",
                      opportunity_reach(52, 40, "us")["explanation"])

    def test_a_range_too_small_to_print_says_the_most_it_can_be(self):
        from aso.scoring import downloads_range_phrase

        self.assertEqual(downloads_range_phrase(0.02, 0.08), "at most one download every 12 days")
        self.assertEqual(downloads_range_phrase(5.94, 23.76), "5.9 to 24 downloads a day")
        self.assertEqual(downloads_range_phrase(0, 0), "no downloads")


class OneDownloadFormatterTest(TestCase):
    """The column and the App Summary print one figure one way (they were
    separate copies; the Pro markdown export is checked in aso_pro)."""

    def test_every_copy_is_the_one_formatter(self):
        from aso.dashboard_summary import _format_dl_number
        from aso.scoring import fmt_downloads
        from aso.templatetags.aso_tags import _fmt_dl

        for n in (0.04, 0.4, 1, 5.94, 9.96, 23.76, 150.4, 1000, 1234):
            with self.subTest(n=n):
                self.assertEqual(_fmt_dl(n), fmt_downloads(n))
                self.assertEqual(_format_dl_number(n), fmt_downloads(n))
        self.assertEqual(_format_dl_number(0), "0")


class OneOpportunityNumberTest(TestCase):
    """KEYWORD_DECISIONS_PLAN.md, round 4: every table shows one Opportunity
    number. The second, "→ 76", was Downloads at #1 again on another scale,
    and the owner and a reviewer could not tell what the pair meant."""

    def test_no_screen_draws_a_second_number(self):
        import os
        import re

        from django.conf import settings

        for root in ("aso/templates", "aso_pro/templates", "static/js"):
            for folder, _dirs, files in os.walk(os.path.join(settings.BASE_DIR, root)):
                for name in files:
                    if not name.endswith((".html", ".js")):
                        continue
                    text = open(os.path.join(folder, name), encoding="utf-8").read()
                    with self.subTest(file=name):
                        self.assertNotRegex(text, r"atFirstHtml|opportunity_at_first|reachHtml|opportunity_reach_line")


class WhereAppsTypicallyLandTest(TestCase):
    """The screens say where apps land (TYPICAL_BY_GAP), never the effective
    rank as a place: at a gap where it is #14 the middle app lands near #68
    (the owner's "options trading" rows, 2026-09-23)."""

    def test_the_middle_app_never_lands_better_than_the_average(self):
        from aso.scoring import reachable_position, typical_landing

        for difficulty in range(0, 101, 5):
            with self.subTest(difficulty=difficulty):
                self.assertGreaterEqual(typical_landing(difficulty)[0], reachable_position(difficulty))

    def test_harder_never_lands_better_or_reaches_the_top_more_often(self):
        from aso.scoring import typical_landing

        previous = typical_landing(0)
        for difficulty in range(1, 101):
            current = typical_landing(difficulty)
            with self.subTest(difficulty=difficulty):
                self.assertGreaterEqual(current[0], previous[0])
                self.assertLessEqual(current[1], previous[1] + 1e-9)
            previous = current

    def test_a_stronger_app_lands_higher(self):
        from aso.scoring import typical_landing
        from aso.strength import AppProfile

        strong = AppProfile(name="Calm Minutes", ratings=50_000, average=4.7,
                            released="2020-01-01T00:00:00Z")
        self.assertLess(typical_landing(60, strong)[0], typical_landing(60)[0])

    def test_how_many_reach_the_top_ten_reads_as_words(self):
        from aso.scoring import top_ten_phrase

        self.assertEqual(top_ten_phrase(0.9), "most reach")
        self.assertEqual(top_ten_phrase(0.5), "about half reach")
        self.assertEqual(top_ten_phrase(0.11), "about 1 in 9 reaches")
        self.assertEqual(top_ten_phrase(0.0303), "about 1 in 35 reaches")
        self.assertEqual(top_ten_phrase(0.0101), "about 1 in 100 reaches")
        self.assertEqual(top_ten_phrase(0.004), "fewer than 1 in 100 reach")

    def test_no_row_sentence_quotes_an_average_rank(self):
        from aso.scoring import get_targeting_advice, opportunity_reach

        for pop in (25, 45, 60, 80):
            for diff in (5, 25, 45, 65, 85):
                with self.subTest(pop=pop, diff=diff):
                    reach = opportunity_reach(pop, diff, "us")
                    for text in (reach["label"], reach["explanation"], get_targeting_advice(pop, diff, "us")[3]):
                        self.assertNotIn("on average", text)
                        self.assertNotIn("taps as #", text)
                        self.assertNotIn("can expect about #", text)
