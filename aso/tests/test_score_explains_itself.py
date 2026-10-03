"""Every number beside the score says which question it answers.

Two contradictions sat on the dashboard after the score was rebuilt on
downloads. A row showed "3.2K-12.7K/day" next to an opportunity of 0, because
the downloads column assumed position 1 and the score assumed the rank a new
app could actually reach, and neither said so. And the scoring guide above
the table graded popularity on its own, "30 to 49, good search volume", about
a number that is seven searches a day in Argentina, directly above a row
labelled Low Volume.

The fix was words, not arithmetic: the column is named for what it is, the
score says on hover, in one short sentence, what it means for the row's app
(since 2026-10-01; it used to be a 300 to 900 character account of the
method, which the Methodology page explains), and the guide is built from
the scoring code. These tests hold each of those in place.
"""

import csv
import io
from datetime import UTC
from pathlib import Path

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils.html import escape

from aso.copy_rules import dash_punctuation_in
from aso.models import App, Keyword, SearchResult
from aso.scoring import (
    calc_opportunity,
    estimate_range_phrase,
    expected_downloads,
    get_targeting_advice,
    opportunity_reach,
    rank_text,
    reachable_position,
    scoring_guide,
    sentence_app_name,
    typical_landing,
)
from aso.tests.test_value_not_mechanics import TIP_LIMIT


class ReachSaysWhoseRankAndWhyTest(SimpleTestCase):
    """The line under the score must be readable without the tooltip, and the
    tooltip must say what the number means for the app, in one sentence."""

    def test_the_short_line_says_where_apps_typically_land(self):
        """Where the middle new app lands, never the effective rank: "#17"
        read as a promise the middle app misses by fifty places."""
        reach = opportunity_reach(48, 38, "ar")
        typical = typical_landing(38)[0]
        self.assertEqual(reach["label"], f"new app: typically ~#{typical}")
        self.assertGreater(typical, reachable_position(38))

    def test_the_hover_says_what_the_number_means(self):
        """Whose number it is, what it is worth in downloads a day, and where
        apps like it typically land; nothing about how it is worked out."""
        reach = opportunity_reach(48, 38, "ar")
        self.assertEqual(
            reach["tip"],
            f"A new app can expect {estimate_range_phrase(reach['downloads'])} here, "
            f"typically landing around #{typical_landing(38)[0]}.",
        )
        self.assertIn("every 5 years", reach["tip"])     # what it is worth
        for internal in ("top 10", "difficulty of", "searches a day", "Opportunity score",
                         f"#{reachable_position(38)}"):  # never the effective rank as a place
            self.assertNotIn(internal, reach["tip"])

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
        self.assertTrue(reach["tip"].endswith(f"typically landing around #{typical_landing(84)[0]}."))
        self.assertIn(estimate_range_phrase(reach["downloads"]), reach["tip"])
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
        self.assertEqual(reach["tip"],
                         f"Calm Minutes ranks #3 here, worth {estimate_range_phrase(reach['downloads'])}.")

    def test_an_empty_market_says_how_little_it_brings(self):
        """Antigua: the rank is reachable, the searches are not there. The
        hover says what that leaves; the tag beside it says why."""
        tip = opportunity_reach(43, 31, "ag")["tip"]
        self.assertTrue(tip.startswith("A new app can expect effectively nothing here, "), tip)
        self.assertIn("under one search a day", get_targeting_advice(43, 31, "ag")[3])

    def test_every_sentence_is_dash_free(self):
        for pop, diff, code in ((48, 38, "ar"), (97, 84, "us"), (43, 31, "ag"),
                                (63, 74, "us"), (80, 20, "us")):
            reach = opportunity_reach(pop, diff, code)
            self.assertEqual(dash_punctuation_in(reach["label"]), "")
            self.assertEqual(dash_punctuation_in(reach["tip"]), "")


class EveryOpportunityHoverIsOneShortSentenceTest(SimpleTestCase):
    """The hover on every Opportunity number in every keyword table (the
    Dashboard, the Opportunity Finder, the AI tabs) is one short sentence:
    what the number means for that row's app on that keyword (the owner,
    2026-10-01; it was 300 to 900 characters).

    At most TIP_LIMIT (120) characters, the bound every tooltip in the app
    keeps (aso/tests/test_value_not_mechanics.py): three lines of the tooltip
    box, short enough to be read whole at a glance. It holds for any App
    Store name, which Apple caps at 30 characters, so the sweep uses one
    that long. Each sentence names the app (its name without the subtitle)
    or says a new app, never "your app", and quotes the figures the row's
    Insight tag quotes.
    """

    LONGEST_NAME = "Soccer Betting Tips & Odds Pro"   # 30 characters, Apple's limit

    def apps(self):
        from aso.strength import AppProfile

        yield None
        for name in ("Pausely: Screen Time Limits", self.LONGEST_NAME):
            yield AppProfile(name=name, known=False)        # ratings not read yet
            for ratings in (0, 40, 5_000, 900_000):
                yield AppProfile(name=name, ratings=ratings, average=4.5,
                                 released="2023-01-01T00:00:00Z")

    def test_one_sentence_that_names_the_app_or_a_new_app(self):
        import re

        self.assertEqual(len(self.LONGEST_NAME), 30)
        seen = set()
        for app in self.apps():
            for keyword in ("screen time", "betting tips"):
                for app_rank in ((None,) if app is None else (None, 2, 23, 60, 150)):
                    for pop in (5, 30, 45, 52, 63, 75, 90):
                        for diff in (0, 20, 45, 70, 100):
                            for country in ("us", "de", "ag"):
                                reach = opportunity_reach(pop, diff, country, app=app,
                                                          app_rank=app_rank, keyword=keyword)
                                tip = reach["tip"]
                                with self.subTest(tip=tip, rank=app_rank):
                                    self.assertLessEqual(len(tip), TIP_LIMIT)
                                    self.assertEqual(len(re.findall(r"[.?!](?:\s|$)", tip)), 1)
                                    self.assertTrue(tip.endswith("."))
                                    who = sentence_app_name(app.name) if app else "A new app"
                                    self.assertTrue(tip.startswith(who + " "))
                                    self.assertNotIn("your app", tip.lower())
                                    self.assertEqual(dash_punctuation_in(tip), "")
                                    # The downloads a day, as the Insight tag says them.
                                    self.assertIn(estimate_range_phrase(reach["downloads"]), tip)
                                    if reach["real_rank"]:
                                        self.assertIn(f"ranks #{app_rank} here", tip)
                                    elif app_rank:
                                        # Never a typical rank worse than the one it holds.
                                        self.assertNotIn("#", tip)
                                seen.update(kind for kind, words in (
                                    ("real rank", " ranks #"),
                                    ("put it in the title", " with the keyword in its title."),
                                    ("where apps like it land", " typically landing "),
                                    ("ratings not read", " scored as a new app."),
                                ) if words in tip)
        # Every kind of sentence came up.
        self.assertEqual(seen, {"real rank", "put it in the title", "where apps like it land",
                                "ratings not read"})


class ScoringGuideComesFromTheCodeTest(SimpleTestCase):
    def test_no_verdict_is_passed_on_popularity(self):
        guide = scoring_guide()
        self.assertNotIn("popularity", guide)       # no graded popularity table
        text = str(guide)
        for verdict in ("Good search volume", "High demand", "Excellent"):
            self.assertNotIn(verdict, text)

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
        # A range from a tenth of the estimate to the estimate, like every
        # download figure (DownloadEstimator.RANGE_LOW_SHARE).
        self.assertEqual(rows["50"], "0.1 to 1 downloads a day")
        self.assertEqual(rows["30"], "At most one download every 10 days")


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

    def test_each_score_carries_its_meaning_on_hover(self):
        """One number per row; what it means for the app is the hover on it,
        not a second line the owner and a reviewer could not connect to the
        number (KEYWORD_DECISIONS_PLAN.md, round 4)."""
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        # The app's ratings have never been read, so both rows say so.
        for row in SearchResult.objects.all():
            tip = row.opportunity_reach["tip"]
            self.assertTrue(tip.startswith("Calm Minutes can expect "), tip)
            self.assertTrue(tip.endswith(" here, scored as a new app."), tip)
            self.assertIn(f'data-tip="{escape(tip)}"', html)
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
    """The same keyword tracked for two apps shows two numbers, and each
    row's hover names its app and what the keyword is worth to that app.
    Why a stronger app scores higher is the Methodology page's to explain."""

    def setUp(self):
        from datetime import datetime

        now = datetime.now(UTC).isoformat()
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
        tips = {}
        for row in SearchResult.objects.select_related("keyword__app"):
            tips[row.keyword.app.name] = row.opportunity_reach["tip"]
            self.assertIn(f'data-tip="{escape(tips[row.keyword.app.name])}"', html)
        # Neither title carries "meditation": each hover says what putting it
        # there brings that app, by name, and the two figures differ.
        self.assertTrue(tips["Calm Minutes: Sleep"].startswith("Calm Minutes can expect "))
        self.assertTrue(tips["Sleepy"].startswith("Sleepy can expect "))
        for tip in tips.values():
            self.assertTrue(tip.endswith(" with the keyword in its title."), tip)
        self.assertNotEqual(tips["Calm Minutes: Sleep"].split(" can expect ")[1],
                            tips["Sleepy"].split(" can expect ")[1])

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
        self.assertEqual(payload["opportunity_tip"], opportunity_reach(48, 38, "ar", keyword="meditation")["tip"])
        self.assertTrue(payload["opportunity_tip"].endswith(
            f"typically landing around #{typical_landing(38)[0]}."))
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
                    if "Est. Downloads" in Path(path).read_text():
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
            self.assertEqual(dash_punctuation_in(factor["tip"]), "")

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
                    text = Path(os.path.join(root, name)).read_text(encoding="utf-8", errors="ignore")
                    self.assertNotIn("k:'rating_quality'", text, name)
                    self.assertNotIn("k:'rating_volume'", text, name)
