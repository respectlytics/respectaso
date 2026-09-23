"""The catalog behind every country picker.

One payload, emitted once per page from the base template, read by
static/js/country-picker.js. It carries two absence markers, and the rule that
matters is that an absence is only ever marked when it was observed.
"""

from unittest import mock

from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from aso import countries, country_picker
from aso.models import App, Keyword, SearchResult


class CatalogTest(SimpleTestCase):
    def test_carries_every_storefront(self):
        catalog = country_picker.catalog()
        self.assertEqual(len(catalog["countries"]), len(countries.CODES))
        self.assertEqual(catalog["regions"], list(countries.REGIONS))

    def test_every_entry_has_what_a_row_needs(self):
        for entry in country_picker.catalog()["countries"]:
            with self.subTest(code=entry["code"]):
                for key in ("code", "name", "flag", "region", "search",
                            "has_language", "apple_ads"):
                    self.assertIn(key, entry)

    def test_the_catalog_is_json_serialisable(self):
        import json

        json.dumps(country_picker.catalog())

    def test_search_text_is_folded_and_carries_the_aliases(self):
        by_code = {c["code"]: c for c in country_picker.catalog()["countries"]}
        self.assertIn("turkiye", by_code["tr"]["search"])   # diacritics stripped
        self.assertIn("turkey", by_code["tr"]["search"])    # the old name
        self.assertIn("uk", by_code["gb"]["search"])
        self.assertIn("bulgaria", by_code["bg"]["search"])

    def test_aliases_only_name_real_storefronts(self):
        self.assertTrue(set(country_picker.ALIASES) <= countries.CODES)

    def test_presets_ship_their_resolved_codes(self):
        for preset in country_picker.presets(tracked=["us", "de"]):
            with self.subTest(preset=preset["id"]):
                self.assertTrue(preset["codes"])
                self.assertTrue(set(preset["codes"]) <= countries.CODES)

    def test_an_empty_preset_is_dropped_rather_than_shown(self):
        """A preset that resolves to nothing would be a dead control."""
        ids = {p["id"] for p in country_picker.presets()}
        self.assertNotIn("tracked", ids)
        ids = {p["id"] for p in country_picker.presets(tracked=["de"])}
        self.assertIn("tracked", ids)


class LanguageMarkerTest(SimpleTestCase):
    def test_marks_exactly_the_storefronts_with_no_listing_language(self):
        marked = {
            c["code"] for c in country_picker.catalog()["countries"]
            if not c["has_language"]
        }
        self.assertEqual(marked, set(countries.without_app_store_language()))
        self.assertIn("bg", marked)     # Apple has no Bulgarian
        self.assertNotIn("de", marked)  # it has German


class AppleMarkerTest(SimpleTestCase):
    def test_silent_on_the_internal_source(self):
        """On the estimate every storefront is equal, so the marker would say
        nothing at all."""
        catalog = country_picker.catalog(apple_source=False)
        self.assertTrue(all(c["apple_ads"] for c in catalog["countries"]))

    def test_silent_while_coverage_is_unknown(self):
        """Not having asked Apple is not the same as knowing there is no
        data, and the UI must never render the first as the second."""
        with mock.patch.object(countries, "APPLE_ADS_STOREFRONTS", frozenset()):
            catalog = country_picker.catalog(apple_source=True)
        self.assertTrue(all(c["apple_ads"] for c in catalog["countries"]))

    def test_marks_an_uncovered_storefront_once_coverage_is_known(self):
        covered = frozenset({"us", "de"})
        with mock.patch.object(countries, "APPLE_ADS_STOREFRONTS", covered):
            catalog = country_picker.catalog(apple_source=True)
        marked = {c["code"] for c in catalog["countries"] if not c["apple_ads"]}
        self.assertNotIn("us", marked)
        self.assertIn("bg", marked)


class TrackedCountriesTest(TestCase):
    def test_reads_what_the_install_actually_tracks(self):
        app = App.objects.create(name="My App")
        keyword = Keyword.objects.create(keyword="fitness", app=app)
        for country in ("de", "de", "us"):
            SearchResult.objects.create(
                keyword=keyword, country=country, difficulty_score=30,
            )
        # Most used first, so the picker offers the countries that matter.
        self.assertEqual(country_picker.tracked_countries(), ["de", "us"])

    def test_nothing_tracked_is_an_empty_list(self):
        self.assertEqual(country_picker.tracked_countries(), [])


class PageTest(TestCase):
    def test_the_base_template_emits_the_catalog_once(self):
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertEqual(html.count('id="country-catalog-data"'), 1)
        self.assertIn("country-picker.js", html)

    def test_the_dashboard_picker_reaches_the_search_form(self):
        """Regression: the picker sits outside <form id="search-form"> on the
        dashboard, so without form= its value never reaches the server and the
        search silently fell back to the United States."""
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertIn('class="cp-value" name="countries" form="search-form"', html)

    def test_the_dashboard_picker_is_capped_and_the_scan_picker_is_not(self):
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertIn('data-max="5"', html)
        html = self.client.get(reverse("aso:opportunity")).content.decode()
        self.assertIn('id="scan-countries"', html)
        self.assertIn('data-max=""', html)
