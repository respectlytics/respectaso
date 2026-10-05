"""The website's measured keywords come from public App Store results, never from Apple Ads.

Free-tier test: no aso_pro import.

aso/management/commands/export_site_measurements.py writes the numbers
respectaso.com shows on its guide pages. Apple's popularity is confidential
under the Apple Ads terms, so the command must refuse a data folder that has
ever held Apple Ads data, compute the estimate the app shows as EST, and write
nothing but the fields the website accepts.
"""

import ast
import json
import tempfile
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.core.management import CommandError, call_command
from django.test import TestCase, override_settings

from aso.data_folders import REAL_DATA_DIRS
from aso.management.commands import export_site_measurements as exporter
from aso.models import AppleSearchPopularity, AppleTopTerm
from aso.popularity import resolve_popularity
from aso.services import PopularityEstimator

from .helpers import ranked_search

COMMAND = Path(exporter.__file__)


def apps(n=25, name="Habit Tracker"):
    return [{"trackId": 1000 + i, "trackName": f"{name} {i}" if i % 2 else f"Other app {i}",
             "userRatingCount": 1000 * (n - i), "averageUserRating": 4.5, "primaryGenreName": "Productivity",
             "sellerName": f"Seller {i}", "releaseDate": "2020-01-01T00:00:00Z"} for i in range(n)]


class MeasureTests(TestCase):
    def test_a_keyword_carries_only_the_published_fields(self):
        row = exporter.measure("habit tracker", "head", apps(), popularity=True)
        self.assertEqual(set(row), set(exporter.FIELDS))
        row = exporter.measure("habit tracker", "head", apps(), popularity=False)
        self.assertEqual(set(row), set(exporter.FIELDS) - {"popularity_estimate"})

    def test_the_estimate_is_the_one_the_app_shows_as_est(self):
        competitors = apps()
        row = exporter.measure("habit tracker", "head", competitors, popularity=True)
        self.assertEqual(row["popularity_estimate"], PopularityEstimator().estimate(competitors, "habit tracker"))
        self.assertEqual(row["popularity_estimate"], resolve_popularity(competitors, "habit tracker", "us").internal)

    def test_the_top_ten_figures(self):
        row = exporter.measure("habit tracker", "head", apps(), popularity=False)
        self.assertEqual(row["top10_title_matches"], 5)
        self.assertEqual(row["top10_median_ratings"], 20500)

    def test_no_results_is_said_not_scored(self):
        row = exporter.measure("zzqx", "long_tail", [], popularity=True)
        self.assertEqual((row["difficulty"], row["popularity_estimate"], row["difficulty_label"]),
                         (None, None, "No results"))


class RefusalTests(TestCase):
    def test_a_folder_with_apple_ads_credentials(self):
        with mock.patch.object(exporter, "has_credentials", return_value=True), \
                self.assertRaisesMessage(CommandError, "credentials"):
            exporter.refuse_unless_clean()

    def test_a_folder_with_apple_popularity_or_top_terms(self):
        for model, fields in ((AppleSearchPopularity, {"term": "habit tracker", "country": "us", "popularity": 50}),
                              (AppleTopTerm, None)):
            with self.subTest(model=model.__name__):
                if fields is None:
                    with mock.patch.object(AppleTopTerm.objects, "exists", return_value=True), \
                            self.assertRaisesMessage(CommandError, "Apple Ads data"):
                        exporter.refuse_unless_clean()
                    continue
                row = model.objects.create(**fields)
                with self.assertRaisesMessage(CommandError, "Apple Ads data"):
                    exporter.refuse_unless_clean()
                row.delete()

    def test_the_owners_real_folders(self):
        for real in REAL_DATA_DIRS:
            with self.subTest(folder=str(real)), override_settings(DATA_DIR=real), \
                    self.assertRaisesMessage(CommandError, "real data folder"):
                exporter.refuse_unless_clean()


class SourceTests(TestCase):
    def test_nothing_that_can_return_an_apple_value_is_imported(self):
        tree = ast.parse(COMMAND.read_text())
        imported = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                imported += [(node.module, alias.name) for alias in node.names]
            elif isinstance(node, ast.Import):
                imported += [(alias.name, None) for alias in node.names]
        names = {name for _, name in imported}
        for banned in ("resolve_popularity", "read_keyword", "effective_from_pair", "absent_cap"):
            self.assertNotIn(banned, names)
        modules = {module for module, _ in imported}
        self.assertNotIn("aso.keyword_scoring", modules)
        apple = [(module, name) for module, name in imported if module and module.startswith("aso.apple_ads")]
        self.assertEqual(apple, [("aso.apple_ads.storage", "has_credentials")])


class CommandTests(TestCase):
    def test_it_writes_every_page_with_its_provenance(self):
        keywords = {"pages": {
            "category/productivity": {"storefront": "us", "popularity": True,
                                      "keywords": [{"keyword": "habit tracker", "bucket": "head"}]},
            "country/japan": {"storefront": "jp", "popularity": False,
                              "keywords": [{"keyword": "習慣", "bucket": "head"}]},
        }}
        itunes = mock.MagicMock()
        itunes.search_ranked.return_value = ranked_search(apps())
        with tempfile.TemporaryDirectory() as folder:
            source, out = Path(folder) / "keywords.json", Path(folder) / "measured.json"
            source.write_text(json.dumps(keywords), encoding="utf-8")
            with mock.patch.object(exporter, "ITunesSearchService", return_value=itunes), \
                    mock.patch("aso.throttle.time.sleep"):
                call_command("export_site_measurements", str(source), str(out), stdout=mock.MagicMock())
            data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(data["popularity_kind"], "respectaso_estimate")
        self.assertEqual(data["app_version"], settings.VERSION)
        self.assertIn("estimator_version", data)
        self.assertEqual(sorted(data["pages"]), ["category/productivity", "country/japan"])
        self.assertIn("popularity_estimate", data["pages"]["category/productivity"]["keywords"][0])
        self.assertNotIn("popularity_estimate", data["pages"]["country/japan"]["keywords"][0])
        self.assertEqual(data["pages"]["country/japan"]["storefront"], "jp")
        self.assertEqual([c.args[0] for c in itunes.search_ranked.call_args_list], ["habit tracker", "習慣"])

    def test_a_throttled_search_waits_and_tries_again(self):
        from aso.services import ITunesRateLimited

        keywords = {"pages": {"category/productivity": {"storefront": "us", "popularity": True,
                                                        "keywords": [{"keyword": "habit tracker", "bucket": "head"}]}}}
        itunes = mock.MagicMock()
        itunes.search_ranked.side_effect = [ITunesRateLimited("slow down"), ranked_search(apps())]
        with tempfile.TemporaryDirectory() as folder:
            source, out = Path(folder) / "keywords.json", Path(folder) / "measured.json"
            source.write_text(json.dumps(keywords), encoding="utf-8")
            with mock.patch.object(exporter, "ITunesSearchService", return_value=itunes), \
                    mock.patch("aso.throttle.time.sleep"):
                call_command("export_site_measurements", str(source), str(out),
                             stdout=mock.MagicMock(), stderr=mock.MagicMock())
            data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(itunes.search_ranked.call_count, 2)
        self.assertEqual(len(data["pages"]["category/productivity"]["keywords"]), 1)

    def test_only_measures_the_pages_asked_for(self):
        keywords = {"pages": {page: {"storefront": "us", "popularity": True,
                                     "keywords": [{"keyword": page.split("/")[1], "bucket": "head"}]}
                              for page in ("category/games", "category/music")}}
        itunes = mock.MagicMock()
        itunes.search_ranked.return_value = ranked_search(apps())
        with tempfile.TemporaryDirectory() as folder:
            source, out = Path(folder) / "keywords.json", Path(folder) / "measured.json"
            source.write_text(json.dumps(keywords), encoding="utf-8")
            with mock.patch.object(exporter, "ITunesSearchService", return_value=itunes), \
                    mock.patch("aso.throttle.time.sleep"):
                call_command("export_site_measurements", str(source), str(out), "--only", "category/music",
                             stdout=mock.MagicMock())
                with self.assertRaisesMessage(CommandError, "no such pages"):
                    call_command("export_site_measurements", str(source), str(out), "--only", "category/news",
                                 stdout=mock.MagicMock())
            data = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(list(data["pages"]), ["category/music"])
