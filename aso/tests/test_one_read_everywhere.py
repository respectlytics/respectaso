"""Every free-tier path reads a keyword through the day's one read, and a
row's rank always matches its own competitor list.

Free-tier test: no aso_pro import.
"""

import os
import re
from typing import ClassVar
from unittest import mock

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from aso import day_reads, history_revision, keyword_scoring, scheduler
from aso.models import App, Keyword, SearchResult
from aso.services import DifficultyCalculator, DownloadEstimator

from .helpers import ranked_search

PAUSELY = 6751782511


def apps(n=40, first_id=1000, place=None):
    """``n`` apps in Apple's order; ``place`` puts Pausely at that 1-based rank."""
    listed = [{"trackId": first_id + i, "trackName": f"App {i}", "userRatingCount": 100 * (i + 1),
               "sellerName": f"Seller {i}"} for i in range(n)]
    if place is not None:
        listed.insert(place - 1, {"trackId": PAUSELY, "trackName": "Pausely", "userRatingCount": 900,
                                  "sellerName": "Loheden"})
    return listed


def fake_itunes(result):
    itunes = mock.MagicMock()
    itunes.search_ranked.return_value = result
    itunes.lookup_by_id.return_value = {"userRatingCount": 120, "averageUserRating": 4.5}
    return itunes


def latest(keyword, country="us"):
    return SearchResult.objects.filter(keyword=keyword, country=country).order_by("-searched_at", "-id").first()


class OneReadTest(TestCase):
    def setUp(self):
        self.pausely = App.objects.create(name="Pausely: Reduce Screen Time", track_id=PAUSELY)
        self.keyword = Keyword.objects.create(keyword="reduce screen time", app=self.pausely)

    def score(self, itunes, keyword=None, force=False):
        return keyword_scoring.score_keyword_pair(
            keyword or self.keyword, "us", itunes_service=itunes,
            difficulty_calc=DifficultyCalculator(), download_est=DownloadEstimator(), force=force,
        )

    def test_the_rank_is_the_apps_place_in_its_own_competitor_list(self):
        row = self.score(fake_itunes(ranked_search(apps(place=3))))
        self.assertEqual(row.app_rank, 3)
        self.assertEqual(row.competitors_data[2]["trackId"], PAUSELY)

    def test_a_rank_beyond_the_competitor_list_still_counts(self):
        row = self.score(fake_itunes(ranked_search(apps(place=60, n=100))))
        self.assertEqual(row.app_rank, 60)
        self.assertEqual(len(row.competitors_data), 25)

    def test_twins_share_one_search_and_each_keeps_its_rank(self):
        other = App.objects.create(name="Screen Coach", track_id=1004)
        twin = Keyword.objects.create(keyword="reduce screen time", app=other)
        SearchResult.objects.create(keyword=twin, country="us", popularity_score=45, difficulty_score=56,
                                    difficulty_breakdown={}, competitors_data=[], app_rank=None)
        itunes = fake_itunes(ranked_search(apps(place=3)))
        self.score(itunes)
        self.assertEqual(itunes.search_ranked.call_count, 1)
        self.assertEqual(latest(twin).app_rank, 6, "App 4 (id 1004) sits 6th once Pausely is placed 3rd")

    def test_a_keyword_read_today_elsewhere_costs_apple_nothing(self):
        itunes = fake_itunes(ranked_search(apps(place=3)))
        day_reads.get_or_fetch("reduce screen time", "us", itunes_service=itunes)
        self.score(itunes)
        self.assertEqual(itunes.search_ranked.call_count, 1)

    def test_the_daily_refresh_writes_from_todays_read_without_waiting(self):
        itunes = fake_itunes(ranked_search(apps(place=3)))
        day_reads.get_or_fetch("reduce screen time", "us", itunes_service=itunes)
        self.addCleanup(lambda: scheduler._update_status(running=False, error=None))
        with mock.patch("aso.services.ITunesSearchService", return_value=itunes), \
                mock.patch("aso.scheduler.time.sleep") as sleep, \
                mock.patch("aso.scheduler.run_queue.kick"):
            scheduler._refresh_pairs([(self.keyword.pk, "us")], "Auto-refresh")
        self.assertEqual(itunes.search_ranked.call_count, 1)
        sleep.assert_not_called()
        self.assertEqual(latest(self.keyword).app_rank, 3)

    def test_the_bulk_refresh_asks_apple_again(self):
        itunes = fake_itunes(ranked_search(apps(place=3)))
        day_reads.get_or_fetch("reduce screen time", "us", itunes_service=itunes)
        self.addCleanup(lambda: scheduler._update_status(running=False, error=None))
        with mock.patch("aso.services.ITunesSearchService", return_value=itunes), \
                mock.patch("aso.scheduler.time.sleep"), \
                mock.patch("aso.scheduler.run_queue.kick"):
            scheduler._refresh_pairs([(self.keyword.pk, "us")], "Manual bulk refresh", force=True)
        self.assertEqual(itunes.search_ranked.call_count, 2)

    def test_the_row_refresh_asks_apple_again_updates_twins_and_the_page_token(self):
        itunes = fake_itunes(ranked_search(apps(place=3)))
        self.score(itunes)
        twin = Keyword.objects.create(keyword="reduce screen time", app=None)
        SearchResult.objects.create(keyword=twin, country="us", popularity_score=45, difficulty_score=56,
                                    difficulty_breakdown={}, competitors_data=[], app_rank=None)
        before = history_revision.count()
        itunes.search_ranked.return_value = ranked_search(apps(place=7, first_id=5000))
        with mock.patch("aso.views.ITunesSearchService", return_value=itunes):
            response = self.client.post(reverse("aso:keyword_refresh", args=[self.keyword.pk]),
                                        {"country": "us"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(itunes.search_ranked.call_count, 2)
        self.assertEqual(latest(self.keyword).app_rank, 7)
        self.assertEqual(latest(twin).competitors_data[0]["trackId"], 5000)
        self.assertGreater(history_revision.count(), before)


class NoPathSearchesAppleItselfTest(TestCase):
    """Static guard: only aso/services.py and aso/day_reads.py search Apple
    for a keyword; the named exceptions search for something else (D13)."""

    CALLS = re.compile(r"\.(search_apps|search_ranked|_search_itunes|_search_ssr|_fetch_ssr_page)\(")
    ALLOWED: ClassVar[dict[str, set[str]]] = {
        "aso/services.py": {"search_apps", "search_ranked", "_search_itunes", "_search_ssr", "_fetch_ssr_page"},
        "aso/day_reads.py": {"search_ranked"},
        "aso/management/commands/apple_estimator_study.py": {"search_ranked"},
        "aso/management/commands/rank_calibration_study.py": {"search_apps"},
        "aso/management/commands/probe_storefronts.py": {"search_apps"},
    }
    ROOTS = ("aso", "aso_pro", "core", "desktop", "licensing", "llm_providers")

    def test_no_keyword_search_outside_the_day_reads(self):
        violations = []
        for root in self.ROOTS:
            for folder, _dirs, files in os.walk(os.path.join(settings.BASE_DIR, root)):
                rel_folder = os.path.relpath(folder, settings.BASE_DIR)
                if "/tests" in f"/{rel_folder}" or "migrations" in rel_folder or "__pycache__" in rel_folder:
                    continue
                for name in files:
                    if not name.endswith(".py"):
                        continue
                    rel = os.path.join(rel_folder, name)
                    with open(os.path.join(folder, name), encoding="utf-8") as handle:
                        for number, line in enumerate(handle, 1):
                            if "find_app_rank" in line:
                                violations.append(f"{rel}:{number} find_app_rank")
                            for match in self.CALLS.finditer(line):
                                if match.group(1) not in self.ALLOWED.get(rel, set()):
                                    violations.append(f"{rel}:{number} {match.group(1)}")
        self.assertEqual(violations, [], "Read keywords through aso.day_reads.get_or_fetch:\n" + "\n".join(violations))

    def test_the_app_name_search_is_the_only_app_search_in_views(self):
        # The Apps page's name search goes through
        # ITunesSearchService.find_apps (aso/services.py), which the Rival
        # Tracker's rival search shares; views.py searches nothing itself.
        with open(os.path.join(settings.BASE_DIR, "aso/views.py"), encoding="utf-8") as handle:
            source = handle.read()
        self.assertEqual(re.findall(r"search_apps\(([^)]*)\)", source), [])
        self.assertEqual(re.findall(r"find_apps\(([^)]*)\)", source), ["query"])
