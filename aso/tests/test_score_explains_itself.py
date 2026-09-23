"""Every number beside the score says which question it answers.

Two contradictions sat on the dashboard after the score was rebuilt on
downloads. A row showed "3.2K-12.7K/day" next to an opportunity of 0, because
the downloads column assumed position 1 and the score assumed the rank a new
app could actually reach, and neither said so. And the scoring guide above
the table graded popularity on its own, "30 to 49, good search volume", about
a number that is seven searches a day in Argentina, directly above a row
labelled Low Volume.

The fix was words, not arithmetic: the column is named for what it is, the
score carries its rank on the line underneath with the reasoning behind it,
and the guide is built from the scoring code. These tests hold each of those
in place.
"""

import csv
import io

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from aso.models import App, Keyword, SearchResult
from aso.scoring import (
    WORST_POSITION,
    calc_opportunity,
    daily_searches,
    expected_downloads,
    opportunity_reach,
    reachable_position,
    rank_text,
    scoring_guide,
    typical_landing,
)

DASHES = ("—", "–")


class ReachSaysWhoseRankAndWhyTest(SimpleTestCase):
    """The line under the score must be readable without the tooltip, and the
    tooltip must answer the questions the line raises."""

    def test_the_short_line_says_where_apps_typically_land(self):
        """Where the middle new app lands, never the effective rank: "#17"
        read as a promise the middle app misses by fifty places."""
        reach = opportunity_reach(48, 38, "ar")
        typical = typical_landing(38)[0]
        self.assertEqual(reach["label"], f"new app: typically ~#{typical}")
        self.assertGreater(typical, reachable_position(38))

    def test_the_explanation_answers_what_where_and_why(self):
        text = opportunity_reach(48, 38, "ar")["explanation"]
        self.assertIn("Brand new apps", text)           # whose rank
        self.assertIn(f"the middle one lands around #{typical_landing(38)[0]}", text)
        self.assertNotIn("top 10", text)                # odds the owner found meaningless
        self.assertIn("land all over the search results", text)
        self.assertIn("averaged over all of them", text)  # what the score is
        self.assertNotIn("taps as #", text)             # never the effective rank as a place
        self.assertIn("search results", text)           # rank in what
        self.assertIn("Argentina App Store", text)      # where
        self.assertIn("difficulty of 38", text)         # based on what
        self.assertIn("how strong the apps already ranking", text)
        self.assertIn("where new apps actually rank", text)
        self.assertIn("6.7 searches a day", text)       # what it is worth
        self.assertIn("every 5 years", text)

    def test_the_rank_and_downloads_are_the_scores_own(self):
        """The line cannot disagree with the number above it: same inputs,
        same functions."""
        for pop, diff, code in ((48, 38, "ar"), (63, 74, "us"), (80, 20, "us")):
            with self.subTest(code=code, pop=pop):
                reach = opportunity_reach(pop, diff, code)
                self.assertEqual(reach["position"], reachable_position(diff))
                self.assertEqual(
                    reach["downloads"], expected_downloads(pop, diff, code),
                )

    def test_a_hard_keyword_says_where_apps_land_and_what_that_averages(self):
        """No edge at #20 any more: the downloads are small but real, and the
        sentence says where the middle app lands and that the few near the
        top bring most of them."""
        reach = opportunity_reach(97, 84, "us")
        self.assertEqual(reach["position"], 93)
        self.assertGreater(reach["downloads"], 0.0)
        self.assertEqual(reach["label"], f"new app: typically ~#{typical_landing(84)[0]}")
        self.assertIn("the few near the top get most of the downloads", reach["explanation"])
        self.assertGreater(calc_opportunity(97, 84, "us"), 0)

    def test_the_deepest_rank_named_is_the_models_floor(self):
        """The model stops at DEEPEST_RANK, and a real rank is used only when
        it is better, so the line never quotes a rank past it."""
        from aso.scoring import DEEPEST_RANK
        from aso.strength import AppProfile

        app = AppProfile(name="Calm Minutes", ratings=0)
        deepest = round(DEEPEST_RANK)
        self.assertEqual(opportunity_reach(90, 100, "us", app=app, app_rank=400)["position"], deepest)
        self.assertEqual(opportunity_reach(90, 100, "us")["label"], "new app: typically past #200")

    def test_a_real_rank_is_stated_as_a_fact(self):
        from aso.strength import AppProfile

        app = AppProfile(name="Calm Minutes: Sleep", ratings=900, average=4.5,
                         released="2023-01-01T00:00:00Z")
        reach = opportunity_reach(63, 74, "us", app=app, app_rank=3)
        self.assertEqual(reach["label"], "Calm Minutes ranks #3")
        self.assertIn("already ranks #3", reach["explanation"])

    def test_an_empty_market_names_the_real_reason(self):
        """Antigua: the rank is reachable, the searches are not there."""
        text = opportunity_reach(43, 31, "ag")["explanation"]
        self.assertIn("under one search a day", text)
        self.assertIn("little to win at any rank", text)

    def test_every_sentence_is_dash_free(self):
        for pop, diff, code in ((48, 38, "ar"), (97, 84, "us"), (43, 31, "ag"),
                                (63, 74, "us"), (80, 20, "us")):
            reach = opportunity_reach(pop, diff, code)
            for dash in DASHES:
                self.assertNotIn(dash, reach["label"])
                self.assertNotIn(dash, reach["explanation"])


class ScoringGuideComesFromTheCodeTest(SimpleTestCase):
    def test_no_verdict_is_passed_on_popularity(self):
        guide = scoring_guide()
        self.assertNotIn("popularity", guide)       # no graded popularity table
        text = str(guide)
        for verdict in ("Good search volume", "High demand", "Excellent"):
            self.assertNotIn(verdict, text)

    def test_the_popularity_example_is_the_arithmetic(self):
        example = scoring_guide()["popularity_example"]
        pop = example["popularity"]
        self.assertEqual(example["us"], f"{round(daily_searches(pop, 'us'), -1):,.0f}")
        self.assertEqual(example["there"], f"{daily_searches(pop, 'ar'):.0f}")

    def test_difficulty_bands_state_the_rank_the_scorer_uses(self):
        for row in scoring_guide()["difficulty"]:
            low, high = (int(x) for x in row["range"].split("-"))
            first, last = typical_landing(low)[0], typical_landing(high)[0]
            with self.subTest(band=row["range"]):
                expected = (rank_text(first) if first == last
                            else f"{rank_text(first)} to {rank_text(last)}")
                self.assertEqual(row["where"], expected)

    def test_the_opportunity_scale_reads_as_downloads(self):
        rows = {row["range"]: row["meaning"] for row in scoring_guide()["opportunity"]}
        self.assertEqual(rows["50"], "About one download a day")
        self.assertIn("every 10 days", rows["30"])


class DashboardShowsTheWorkingTest(TestCase):
    def setUp(self):
        app = App.objects.create(name="Calm Minutes")
        kw = Keyword.objects.create(keyword="meditation", app=app)
        SearchResult.objects.create(
            keyword=kw, country="ar", popularity_score=48, difficulty_score=38,
        )
        kw2 = Keyword.objects.create(keyword="period tracker", app=app)
        SearchResult.objects.create(
            keyword=kw2, country="us", popularity_score=97, difficulty_score=84,
        )

    def test_the_downloads_column_is_named_for_position_one(self):
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertIn("Downloads at #1", html)
        self.assertNotIn("Est. Downloads", html)

    def test_each_score_carries_its_reason_on_hover(self):
        """One number per row; where the app lands and why is the hover on
        it, not a second line the owner and a reviewer could not connect to
        the number (KEYWORD_DECISIONS_PLAN.md, round 4)."""
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertIn(f"the middle one lands around #{typical_landing(38)[0]}", html)
        self.assertIn(f"the middle one lands around #{typical_landing(84)[0]}", html)
        self.assertNotIn(": typically ~#", html)
        self.assertNotIn("on average", html.split("<main")[-1].split("</main>")[0])

    def test_the_guide_no_longer_grades_popularity(self):
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertNotIn("Good search volume", html)
        self.assertIn("compare countries by Opportunity, not by Popularity", html)

    def test_the_csv_carries_the_same_working(self):
        response = self.client.get(reverse("aso:export_history_csv"))
        rows = list(csv.DictReader(io.StringIO(response.content.decode())))
        by_keyword = {row["Keyword"]: row for row in rows}
        # The app's ratings have never been read, so both rows are for a new app.
        self.assertEqual(by_keyword["meditation"]["Scored For"], "new app")
        self.assertEqual(by_keyword["meditation"]["App Ratings Used"], "")
        self.assertEqual(by_keyword["meditation"]["Expected Rank"], "17")
        self.assertEqual(by_keyword["period tracker"]["Expected Rank"], "93")
        self.assertGreater(
            float(by_keyword["period tracker"]["Downloads/Day at Expected Rank"]), 0,
        )
        self.assertTrue(by_keyword["period tracker"]["Downloads/Day at #1"])


class TwoAppsOneKeywordTest(TestCase):
    """The same keyword tracked for two apps shows two numbers, and the row
    itself says why: each line names its app, and the tooltip compares it
    with a brand new app and says the scores differ per app on purpose."""

    def setUp(self):
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc).isoformat()
        self.strong = App.objects.create(
            name="Calm Minutes: Sleep", track_id=111,
            store_profiles={"us": {"count": 50_000, "average": 4.6,
                                   "released": "2021-03-01T08:00:00Z", "checked_at": now}},
        )
        self.small = App.objects.create(
            name="Sleepy", track_id=222,
            store_profiles={"us": {"count": 40, "average": 4.1,
                                   "released": "2025-11-01T08:00:00Z", "checked_at": now}},
        )
        for app in (self.strong, self.small):
            kw = Keyword.objects.create(keyword="meditation", app=app)
            SearchResult.objects.create(
                keyword=kw, country="us", popularity_score=63, difficulty_score=74,
            )

    def test_each_row_names_its_app_and_the_reason(self):
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        from aso.app_profiles import cached_profile

        strong = calc_opportunity(63, 74, "us", app=cached_profile(self.strong, "us"), app_rank=None)
        small = calc_opportunity(63, 74, "us", app=cached_profile(self.small, "us"), app_rank=None)
        self.assertNotEqual(strong, small)
        self.assertIn(">Calm Minutes: Sleep</span>", html)   # the app, under the keyword
        self.assertIn(">Sleepy</span>", html)
        self.assertIn("Calm Minutes has 50,000 ratings averaging 4.6 stars over", html)
        self.assertIn("Sleepy has 40 ratings", html)
        self.assertIn("The same keyword scores differently for each of your apps", html)
        self.assertIn(f"A brand new app typically lands around #{typical_landing(74)[0]}", html)

    def test_the_csv_says_whose_score_each_row_is(self):
        response = self.client.get(reverse("aso:export_history_csv"))
        rows = list(csv.DictReader(io.StringIO(response.content.decode())))
        by_app = {row["App"]: row for row in rows}
        self.assertEqual(by_app["Calm Minutes: Sleep"]["Scored For"], "Calm Minutes: Sleep")
        self.assertEqual(by_app["Calm Minutes: Sleep"]["App Ratings Used"], "50000")
        self.assertEqual(by_app["Sleepy"]["App Ratings Used"], "40")


class FinderAndTabsSayWhoseScoreTest(TestCase):
    def test_the_finder_rows_carry_the_reason(self):
        from aso.models import OpportunityScan, OpportunityScanResult
        from aso.opportunity_scans import country_payload, scan_payload

        scan = OpportunityScan.objects.create(keyword="meditation", countries=["ar"])
        row = OpportunityScanResult.objects.create(
            scan=scan, country="ar", order_index=0, keyword_text="meditation",
            popularity_score=48, difficulty_score=38,
        )
        payload = country_payload(row)
        self.assertNotIn("reach", payload)
        self.assertIn(f"the middle one lands around #{typical_landing(38)[0]}",
                      payload["opportunity_tip"])
        self.assertIn("brand new app", scan_payload(scan)["scored_for"])

    def test_a_finder_scan_for_an_app_says_so(self):
        from aso.models import OpportunityScan
        from aso.opportunity_scans import scan_payload

        app = App.objects.create(name="Calm Minutes", track_id=111)
        scan = OpportunityScan.objects.create(keyword="meditation", countries=["ar"], app=app)
        self.assertEqual(
            scan_payload(scan)["scored_for"],
            "Opportunity is scored for Calm Minutes, from its ratings, average "
            "stars and age in each storefront.",
        )

    def test_a_finder_scan_for_an_app_added_by_hand_scores_it_as_new(self):
        """A manual app has no App Store listing, so there are no ratings to
        score it from in any storefront, and the sentence must not claim so."""
        from aso.models import OpportunityScan, OpportunityScanResult
        from aso.opportunity_scans import country_payload, scan_payload

        app = App.objects.create(name="Moonpond", bundle_id="com.example.moonpond")
        scan = OpportunityScan.objects.create(keyword="sleep sounds", countries=["us"], app=app)
        self.assertEqual(
            scan_payload(scan)["scored_for"],
            "Opportunity is scored for a brand new app, because Moonpond was "
            "added by hand and has no App Store ratings to read.",
        )
        row = OpportunityScanResult.objects.create(
            scan=scan, country="us", order_index=0, keyword_text="sleep sounds",
            popularity_score=48, difficulty_score=38,
        )
        new_app = OpportunityScan.objects.create(keyword="sleep sounds", countries=["us"])
        plain = OpportunityScanResult.objects.create(
            scan=new_app, country="us", order_index=0, keyword_text="sleep sounds",
            popularity_score=48, difficulty_score=38,
        )
        self.assertEqual(country_payload(row)["opportunity"], country_payload(plain)["opportunity"])

    def test_the_ai_tabs_without_your_app_say_so(self):
        from aso.templatetags.aso_tags import scored_for_note

        self.assertIn("competitor's, not yours", scored_for_note("competitor"))
        self.assertIn("not tied to one of your apps", scored_for_note("none"))


class NoColumnCalledEstDownloadsTest(SimpleTestCase):
    """The name that let a #1 figure pass for a general estimate is gone
    from every screen, and it must not come back on one of them."""

    def test_no_template_uses_the_old_name(self):
        import os

        from django.conf import settings

        offenders = []
        for folder in ("aso/templates", "aso_pro/templates", "static/js"):
            for root, _dirs, files in os.walk(os.path.join(settings.BASE_DIR, folder)):
                for name in files:
                    if not name.endswith((".html", ".js")):
                        continue
                    path = os.path.join(root, name)
                    if "Est. Downloads" in open(path).read():
                        offenders.append(os.path.relpath(path, settings.BASE_DIR))
        self.assertEqual(offenders, [], "Call it Downloads at #1: " + ", ".join(offenders))


class DifficultyFactorsComeFromTheScoreTest(TestCase):
    """The breakdown tiles show exactly the factors and weights the score
    uses, from one list the server publishes (static/js/difficulty-factors.js
    draws them). Two JavaScript copies of the list used to carry their own
    weights."""

    def test_the_legend_is_the_yardstick(self):
        from aso.scoring import difficulty_factor_legend
        from aso.services import DifficultyCalculator
        from aso.strength import WEIGHTS

        legend = difficulty_factor_legend()
        self.assertEqual(sum(f["weight"] for f in legend), 100)
        self.assertEqual(len(legend), len(WEIGHTS))
        _, breakdown = DifficultyCalculator().calculate(
            [{"trackName": "Focus Timer", "userRatingCount": 900, "averageUserRating": 4.5,
              "releaseDate": "2023-01-01T00:00:00Z", "sellerName": "A"}], keyword="focus timer",
        )
        for factor in legend:
            self.assertIn(factor["key"], breakdown, factor["key"])
            for dash in DASHES:
                self.assertNotIn(dash, factor["tip"])

    def test_every_page_publishes_it_once(self):
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertEqual(html.count('id="difficulty-factors-data"'), 1)
        self.assertIn("js/difficulty-factors.js", html)

    def test_no_renderer_keeps_its_own_copy(self):
        import os

        from django.conf import settings

        for folder in ("aso/templates", "aso_pro/templates", "static/js"):
            for root, _dirs, files in os.walk(os.path.join(settings.BASE_DIR, folder)):
                for name in files:
                    if name == "difficulty-factors.js":
                        continue
                    text = open(os.path.join(root, name), encoding="utf-8", errors="ignore").read()
                    self.assertNotIn("k:'rating_quality'", text, name)
                    self.assertNotIn("k:'rating_volume'", text, name)
