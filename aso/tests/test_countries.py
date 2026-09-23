"""The storefront registry: the one place a country may be defined.

aso/countries.py replaced six hand kept copies of the country list that had
already drifted apart. These tests hold the table itself to its contract, and
test_no_hardcoded_countries.py stops a seventh copy appearing.
"""

import os
import re

from django.test import SimpleTestCase

from aso import countries
from aso.locale_data import LOCALE_LABELS

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# The 48 market sizes that were fitted against observed App Store data, frozen
# here on purpose. Everything else in the table is derived and may be
# regenerated; these may not, and a script that recomputes them is a bug.
MEASURED = {
    "us": 1.0, "cn": 0.45, "jp": 0.35, "gb": 0.3, "de": 0.25, "fr": 0.22,
    "kr": 0.2, "br": 0.18, "in": 0.15, "ca": 0.15, "au": 0.12, "ru": 0.12,
    "it": 0.12, "es": 0.1, "mx": 0.1, "tw": 0.08, "nl": 0.07, "se": 0.06,
    "ch": 0.06, "pl": 0.05, "tr": 0.05, "th": 0.05, "id": 0.05, "be": 0.04,
    "at": 0.04, "no": 0.04, "dk": 0.04, "sg": 0.04, "il": 0.04, "ae": 0.04,
    "sa": 0.04, "ph": 0.04, "my": 0.04, "za": 0.03, "ie": 0.03, "fi": 0.03,
    "pt": 0.03, "nz": 0.03, "cl": 0.03, "ar": 0.03, "co": 0.03, "ng": 0.03,
    "eg": 0.03, "pk": 0.02, "ke": 0.02, "gh": 0.02, "tz": 0.02, "ug": 0.02,
}


class RegistryShapeTest(SimpleTestCase):
    def test_every_country_is_well_formed(self):
        for country in countries.COUNTRIES.values():
            with self.subTest(code=country.code):
                self.assertRegex(country.code, r"^[a-z]{2}$")
                self.assertTrue(country.name.strip())
                self.assertIn(country.region, countries.REGIONS)
                self.assertGreater(country.market, 0)
                self.assertIn(country.market_source, ("measured", "derived"))

    def test_the_table_and_the_lookup_agree(self):
        self.assertEqual(len(countries.CODES), len(countries.COUNTRIES))
        for code, country in countries.COUNTRIES.items():
            self.assertEqual(code, country.code)

    def test_every_region_has_countries(self):
        by_region = countries.by_region()
        self.assertEqual(set(by_region), set(countries.REGIONS))
        for region, entries in by_region.items():
            self.assertTrue(entries, f"{region} is empty")

    def test_locales_are_real_apple_localizations(self):
        """The test that stops an invented locale reaching a user.

        Apple offers App Store listing languages for about forty languages.
        A storefront may name only those, and an empty tuple is the honest
        answer everywhere else.
        """
        for country in countries.COUNTRIES.values():
            for locale in country.locales:
                with self.subTest(code=country.code, locale=locale):
                    self.assertIn(locale, LOCALE_LABELS)

    def test_measured_market_sizes_are_unchanged(self):
        """These were fitted against observed data and must never be
        recomputed by a script. The derived ones may be."""
        measured = {
            c.code: c.market for c in countries.COUNTRIES.values()
            if c.market_source == "measured"
        }
        self.assertEqual(measured, MEASURED)

    def test_flags_are_computed_not_stored(self):
        self.assertEqual(countries.get("bg").flag, "\U0001F1E7\U0001F1EC")
        self.assertEqual(countries.flag("il"), "\U0001F1EE\U0001F1F1")
        with open(os.path.join(BASE_DIR, "aso", "countries.py")) as f:
            source = f.read()
        self.assertNotIn("\U0001F1E6", source, "a flag literal crept into the table")

    def test_unknown_codes_answer_safely(self):
        self.assertIsNone(countries.get("zz"))
        self.assertEqual(countries.name("zz"), "ZZ")
        self.assertFalse(countries.is_valid("zz"))
        self.assertEqual(countries.flag("zz"), "\U0001F1FF\U0001F1FF")
        self.assertEqual(countries.flag("nonsense"), "")


class CleanTest(SimpleTestCase):
    def test_clean_lowercases_drops_unknown_and_keeps_order(self):
        self.assertEqual(
            countries.clean([" US ", "zz", "de", "US", "bg"]),
            ["us", "de", "bg"],
        )

    def test_clean_handles_nothing(self):
        self.assertEqual(countries.clean([]), [])
        self.assertEqual(countries.clean(None), [])


class PresetTest(SimpleTestCase):
    def test_every_preset_resolves_to_known_codes(self):
        for key, _label in countries.PRESETS:
            with self.subTest(preset=key):
                codes = countries.preset_codes(key, tracked=["us", "de"])
                self.assertTrue(set(codes) <= countries.CODES)

    def test_only_the_data_driven_presets_may_be_empty(self):
        """"Countries I track" is empty until something is tracked, and
        "Where Apple reports data" until the coverage probe has run. The
        picker drops an empty preset rather than rendering a dead control;
        every other preset must always resolve to something."""
        for key, _label in countries.PRESETS:
            codes = countries.preset_codes(key)
            if key in ("tracked", "apple"):
                continue
            self.assertTrue(codes, f"{key} resolved to nothing")

    def test_the_tracked_preset_comes_from_the_caller(self):
        self.assertEqual(countries.preset_codes("tracked"), [])
        self.assertEqual(
            countries.preset_codes("tracked", tracked=["de", "zz", "us"]),
            ["de", "us"],
        )

    def test_top_markets_is_computed_from_the_table(self):
        top = countries.top_markets(5)
        self.assertEqual(top[0], "us")
        self.assertEqual(len(top), 5)
        markets = [countries.get(c).market for c in top]
        self.assertEqual(markets, sorted(markets, reverse=True))

    def test_regions_resolve_to_their_own_countries(self):
        for code in countries.preset_codes("europe"):
            self.assertEqual(countries.get(code).region, "Europe")


class AppleCoverageTest(SimpleTestCase):
    def test_unknown_coverage_is_not_an_absence(self):
        """Nobody has probed Apple on a fresh install, and not knowing is not
        the same as knowing there is no data."""
        if countries.APPLE_ADS_STOREFRONTS:
            self.skipTest("this install has probed Apple Ads coverage")
        self.assertIsNone(countries.apple_reports_data("us"))

    def test_coverage_only_ever_names_real_storefronts(self):
        self.assertTrue(countries.APPLE_ADS_STOREFRONTS <= countries.CODES)


class ProbeLogTest(SimpleTestCase):
    def test_the_table_records_when_it_was_checked_against_apple(self):
        """The list is observed, not assumed, and the file says when."""
        with open(os.path.join(BASE_DIR, "aso", "countries.py")) as f:
            source = f.read()
        self.assertIn("probe_storefronts", source)
        self.assertRegex(source, r"Probed \d{4}-\d{2}-\d{2}")


class SmallMarketClassificationTest(SimpleTestCase):
    """A keyword cannot be "high search volume" where there are no searches.

    Popularity is a relative 1-100 scale, the same one Apple publishes. It
    says how sought after a keyword is compared with other keywords, not how
    many searches it gets: the absolute number is popularity times the
    storefront's size. Across 175 storefronts the two can disagree wildly, and
    the classifier used to see only the relative number. A real row read
    "43, Under 1 search a day, Sweet Spot: high search volume + low
    competition", which is a contradiction inside one row.
    """

    def test_a_tiny_market_is_low_volume_at_an_ordinary_score(self):
        from aso.scoring import classify_keyword

        # Antigua & Barbuda: popularity 43 comes to 0.02 searches a day.
        self.assertEqual(classify_keyword(43, 31, "ag"), "Low Volume")

    def test_the_rule_keys_on_volume_not_on_country_size(self):
        """A genuinely top keyword in a small storefront is worth something:
        popularity 90 in Antigua is about 15 searches a day, which is real.
        The rule is about searches, not about the flag: it gets the label a
        United States keyword with the same searches a day gets."""
        from aso.scoring import calc_opportunity, classify_keyword, daily_searches

        searches = daily_searches(90, "ag")
        self.assertGreater(searches, 1.0)
        self.assertGreater(calc_opportunity(90, 10, "ag"), 0)
        # And it beats the same keyword where there is nothing to win.
        self.assertGreater(
            calc_opportunity(90, 10, "ag"), calc_opportunity(43, 31, "ag"),
        )
        twin = min(range(1, 101), key=lambda p: abs(daily_searches(p, "us") - searches))
        self.assertLess(abs(daily_searches(twin, "us") - searches) / searches, 0.25)
        self.assertEqual(classify_keyword(90, 10, "ag"), classify_keyword(twin, 10, "us"))

    def test_a_real_market_earns_a_real_label(self):
        """The same inputs read differently in two real markets, and both
        readings are true. Popularity 43 is 112 searches a day in the United
        States and 28 in Germany, so on a very easy keyword the first is worth
        carrying and the second is thin. The label follows the searches, not
        the flag."""
        from aso.scoring import calc_opportunity, classify_keyword, daily_searches

        self.assertNotIn(classify_keyword(43, 5, "us"), ("Low Volume", "Avoid"))
        self.assertGreater(daily_searches(43, "de"), 1.0)
        self.assertGreater(calc_opportunity(43, 5, "de"), 0)
        self.assertGreater(
            calc_opportunity(43, 5, "us"), calc_opportunity(43, 5, "de"),
        )

    def test_no_storefront_given_means_the_united_states(self):
        """Callers that genuinely have no country (a simulated keyword, a
        metadata term) get the scale everything is calibrated to."""
        from aso.scoring import calc_opportunity, classify_keyword

        self.assertEqual(classify_keyword(43, 31), classify_keyword(43, 31, "us"))
        self.assertEqual(calc_opportunity(43, 31), calc_opportunity(43, 31, "us"))

    def test_the_targeting_advice_follows(self):
        from aso.scoring import classify_keyword, get_targeting_advice

        self.assertEqual(get_targeting_advice(43, 31, "ag")[1], "Low Volume")
        for code in ("ag", "us", "de", "bg"):
            with self.subTest(code=code):
                self.assertEqual(
                    get_targeting_advice(43, 31, code)[1],
                    classify_keyword(43, 31, code),
                )

    def test_the_classifier_and_the_download_estimate_use_one_calculation(self):
        """The number behind the label and the number behind the download
        range must be the same number, or the row contradicts itself again."""
        from aso.scoring import MIN_DAILY_SEARCHES, classify_keyword, daily_searches
        from aso.services import DownloadEstimator

        estimator = DownloadEstimator()
        for code in ("us", "de", "bg", "ag", "vu"):
            with self.subTest(code=code):
                estimate = estimator.estimate(43, country=code)
                self.assertAlmostEqual(
                    estimate["daily_searches"], round(daily_searches(43, code), 2),
                    places=2,
                )
                below = estimate["daily_searches"] < MIN_DAILY_SEARCHES
                self.assertEqual(estimate["below_threshold"], below)
                if below:
                    self.assertEqual(classify_keyword(43, 31, code), "Low Volume")

    def test_a_stored_row_classifies_by_its_own_storefront(self):
        from aso.models import SearchResult

        row = SearchResult(country="ag", popularity_score=43, difficulty_score=31)
        self.assertEqual(
            classify_for(row.effective_popularity or 0, row.difficulty_score, "ag"),
            "Low Volume",
        )


def classify_for(popularity, difficulty, country):
    from aso.scoring import classify_keyword

    return classify_keyword(popularity, difficulty, country)


class OneDownloadChartTest(SimpleTestCase):
    """The chart is rendered in one place.

    dashboard.html and opportunity-scan.js carried byte-identical copies, and
    the copies drifted: the honest "under 1 search a day" answer reached one
    of them and not the other, so the dashboard kept drawing twenty bars of
    0.0 for storefronts with no searches.
    """

    def test_only_the_shared_module_defines_it(self):
        import os

        definitions = []
        for directory in ("aso", "aso_pro", "static/js"):
            for dirpath, dirnames, filenames in os.walk(os.path.join(BASE_DIR, directory)):
                dirnames[:] = [d for d in dirnames if d != "__pycache__"]
                for filename in filenames:
                    if not filename.endswith((".html", ".js")):
                        continue
                    full = os.path.join(dirpath, filename)
                    rel = os.path.relpath(full, BASE_DIR)
                    if rel == "static/js/download-chart.js":
                        continue
                    with open(full, encoding="utf-8") as f:
                        content = f.read()
                    if re.search(r"function renderDownloadChart|renderDownloadChart\s*=\s*function",
                                 content):
                        definitions.append(rel)
        self.assertEqual(
            definitions, [],
            "renderDownloadChart is defined outside static/js/download-chart.js: "
            + ", ".join(definitions),
        )

    def test_both_pages_load_it(self):
        import os

        for rel in ("aso/templates/aso/dashboard.html",
                    "aso/templates/aso/opportunity.html"):
            with open(os.path.join(BASE_DIR, rel)) as f:
                self.assertIn("js/download-chart.js", f.read(), rel)

    def test_the_shared_chart_answers_a_tiny_market_honestly(self):
        import os

        with open(os.path.join(BASE_DIR, "static/js/download-chart.js")) as f:
            source = f.read()
        self.assertIn("below_threshold", source)
        self.assertIn("Under 1 search a day in this storefront", source)


# The viability floor this class covered was superseded by the full
# rebuild of the score on expected downloads. Its rules, and many more,
# live in aso/tests/test_opportunity_invariants.py and run across all
# 175 storefronts rather than a handful of examples.

class ScoreColumnsExplainThemselvesTest(SimpleTestCase):
    """Popularity is the score people read wrongly, so it carries a tooltip,
    and Opportunity is the one to act on, so it is marked as such."""

    def _read(self, rel):
        import os

        with open(os.path.join(BASE_DIR, rel)) as f:
            return f.read()

    def test_popularity_says_it_is_an_index_not_a_count(self):
        from aso.column_tips import column_tip

        self.assertIn("It is an index, not a number of searches", column_tip("popularity"))
        for rel in ("aso/templates/aso/dashboard.html",
                    "aso/templates/aso/opportunity.html"):
            with self.subTest(rel=rel):
                self.assertIn('{% column_info "popularity" %}', self._read(rel))

    def test_opportunity_is_marked_as_the_one_to_act_on(self):
        from aso.column_tips import column_tip

        for rel, subject in (("aso/templates/aso/dashboard.html", "tracked"),
                             ("aso/templates/aso/opportunity.html", "finder")):
            with self.subTest(rel=rel):
                self.assertIn(f'{{% column_info "opportunity" "{subject}" %}}', self._read(rel))
                self.assertIn("The one to act on", column_tip("opportunity", subject))

    def test_the_tooltip_engine_is_loaded_for_every_page(self):
        base = self._read("aso/templates/aso/base.html")
        self.assertIn("js/tooltip.js", base)
        # and no page ships its own copy of it
        dashboard = self._read("aso/templates/aso/dashboard.html")
        self.assertNotIn("data-tip]", dashboard.split("data-tip=")[0])
