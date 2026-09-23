"""The app's store profile per storefront: read once a day, shared by every score.

The opportunity score is for the app a keyword is tracked for, measured on
the competitors' yardstick: its rating count, average stars, momentum and
age in that storefront. The profile is cached on the app so every row, tab
and scan for the same app and storefront reads the same values.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from django.test import TestCase

from aso import app_profiles
from aso.models import App, Keyword, SearchResult
from aso.scoring import calc_opportunity, classify_keyword
from aso.strength import AppProfile

RELEASED = "2021-03-01T08:00:00Z"


def lookup_returning(count, average=4.6, released=RELEASED, name="Calm Minutes: Sleep"):
    service = MagicMock()
    service.lookup_by_id.return_value = {
        "trackId": 111, "trackName": name, "userRatingCount": count,
        "averageUserRating": average, "releaseDate": released,
    }
    return service


def stale():
    return (datetime.now(timezone.utc) - timedelta(hours=30)).isoformat()


class CacheTest(TestCase):
    def setUp(self):
        self.app = App.objects.create(name="Calm Minutes: Sleep", track_id=111)

    def test_a_fresh_profile_needs_no_lookup(self):
        service = lookup_returning(1_234)
        first = app_profiles.profile_in_storefront(self.app, "ar", itunes_service=service)
        second = app_profiles.profile_in_storefront(self.app, "ar", itunes_service=service)
        self.assertEqual(first, second)
        self.assertEqual(service.lookup_by_id.call_count, 1)
        cached = app_profiles.cached_profile(self.app, "ar")
        self.assertEqual((cached.ratings, cached.average, cached.released),
                         (1_234, 4.6, RELEASED))
        self.assertTrue(cached.known)

    def test_a_stale_profile_is_read_again(self):
        self.app.store_profiles = {"ar": {"count": 10, "checked_at": stale()}}
        self.app.save()
        service = lookup_returning(50)
        profile = app_profiles.profile_in_storefront(self.app, "ar", itunes_service=service)
        self.assertEqual(profile.ratings, 50)
        self.assertEqual(service.lookup_by_id.call_count, 1)

    def test_a_failed_lookup_keeps_the_last_known_profile(self):
        self.app.store_profiles = {"ar": {"count": 10, "average": 4.1, "checked_at": stale()}}
        self.app.save()
        service = MagicMock()
        service.lookup_by_id.side_effect = RuntimeError("offline")
        profile = app_profiles.profile_in_storefront(self.app, "ar", itunes_service=service)
        self.assertEqual((profile.ratings, profile.average), (10, 4.1))

    def test_storefronts_are_separate(self):
        app_profiles.profile_in_storefront(self.app, "us", itunes_service=lookup_returning(12_000))
        app_profiles.profile_in_storefront(self.app, "bg", itunes_service=lookup_returning(40, 3.9))
        us = app_profiles.cached_profile(self.app, "us")
        bg = app_profiles.cached_profile(self.app, "bg")
        self.assertEqual((us.ratings, us.average), (12_000, 4.6))
        self.assertEqual((bg.ratings, bg.average), (40, 3.9))

    def test_a_storefront_never_read_is_marked_unknown(self):
        profile = app_profiles.cached_profile(self.app, "fr")
        self.assertFalse(profile.known)
        self.assertEqual(profile.name, "Calm Minutes: Sleep")
        # It scores exactly as a brand new app.
        self.assertEqual(calc_opportunity(63, 74, "fr", app=profile, app_rank=None),
                         calc_opportunity(63, 74, "fr", app=None))

    def test_no_app_has_no_profile(self):
        self.assertIsNone(app_profiles.cached_profile(None, "us"))

    def test_an_untracked_app_is_looked_up_named_and_not_stored(self):
        service = lookup_returning(900, name="Someone Else")
        profile = app_profiles.profile_for_track(999, "us", itunes_service=service)
        self.assertEqual((profile.name, profile.ratings), ("Someone Else", 900))
        self.assertFalse(App.objects.filter(track_id=999).exists())

    def test_a_tracked_track_id_uses_the_cache(self):
        app_profiles.profile_in_storefront(self.app, "us", itunes_service=lookup_returning(5))
        service = lookup_returning(7)
        profile = app_profiles.profile_for_track(111, "us", itunes_service=service)
        self.assertEqual(profile.ratings, 5)
        service.lookup_by_id.assert_not_called()

    def test_a_deleted_app_does_not_fail_the_lookup(self):
        App.objects.filter(pk=self.app.pk).delete()
        profile = app_profiles.profile_in_storefront(
            self.app, "us", itunes_service=lookup_returning(5),
        )
        self.assertIsNotNone(profile)


class RowsFollowTheirAppTest(TestCase):
    """Two apps tracking the same keyword in the same storefront: each row
    scores for its own app, and the stored label follows its profile."""

    def setUp(self):
        self.strong = App.objects.create(name="Calm Minutes", track_id=111)
        self.new = App.objects.create(name="Sleepy", track_id=222)
        for app in (self.strong, self.new):
            kw = Keyword.objects.create(keyword="meditation", app=app)
            SearchResult.objects.create(
                keyword=kw, country="us", popularity_score=63, difficulty_score=74,
            )

    def row(self, app):
        return SearchResult.objects.select_related("keyword__app").get(keyword__app=app)

    def test_a_change_in_the_profile_relabels_only_that_app_there(self):
        before_new = self.row(self.new).classification
        app_profiles.profile_in_storefront(
            self.strong, "us", itunes_service=lookup_returning(50_000),
        )
        strong_row = self.row(self.strong)
        self.assertEqual(
            strong_row.classification,
            classify_keyword(63, 74, "us", app=strong_row.app_profile, app_rank=None),
        )
        self.assertEqual(self.row(self.new).classification, before_new)

    def test_the_rating_alone_moves_the_score(self):
        """Stars count, not only the rating count: the same count with a
        lower average makes a weaker app."""
        app_profiles.profile_in_storefront(
            self.strong, "us", itunes_service=lookup_returning(5_000, average=4.8),
        )
        loved = self.row(self.strong).opportunity_score
        self.strong.store_profiles["us"]["checked_at"] = stale()
        App.objects.filter(pk=self.strong.pk).update(store_profiles=self.strong.store_profiles)
        self.strong.refresh_from_db()
        app_profiles.profile_in_storefront(
            self.strong, "us", itunes_service=lookup_returning(5_000, average=3.2),
        )
        self.assertGreater(loved, self.row(self.strong).opportunity_score)

    def test_the_same_keyword_scores_differently_for_each_app(self):
        app_profiles.profile_in_storefront(
            self.strong, "us", itunes_service=lookup_returning(50_000),
        )
        strong_row, new_row = self.row(self.strong), self.row(self.new)
        self.assertEqual(
            strong_row.opportunity_score,
            calc_opportunity(63, 74, "us", app=AppProfile(
                name="Calm Minutes", ratings=50_000, average=4.6, released=RELEASED,
            ), app_rank=None),
        )
        self.assertEqual(new_row.opportunity_score, calc_opportunity(63, 74, "us", app=None))
        self.assertGreater(strong_row.opportunity_score, new_row.opportunity_score)
        self.assertTrue(strong_row.opportunity_reach["label"].startswith("Calm Minutes: typically ~#"))
        self.assertTrue(new_row.opportunity_reach["label"].startswith("new app: typically ~#"))
        self.assertIn("RespectASO has not read Sleepy's ratings",
                      new_row.opportunity_reach["explanation"])

    def test_a_real_rank_that_is_better_is_the_rank_used(self):
        row = self.row(self.new)
        SearchResult.objects.filter(pk=row.pk).update(app_rank=2)
        row = self.row(self.new)
        self.assertEqual(row.opportunity_reach["position"], 2)
        self.assertGreater(row.opportunity_score, calc_opportunity(63, 74, "us", app=None))


class BackfillTest(TestCase):
    def test_the_newest_row_containing_the_app_seeds_the_cache(self):
        app = App.objects.create(name="Calm Minutes", track_id=111)
        kw = Keyword.objects.create(keyword="meditation", app=app)
        older = SearchResult.objects.create(
            keyword=kw, country="us", popularity_score=50, difficulty_score=40,
            competitors_data=[{"trackId": 111, "userRatingCount": 100}],
        )
        newer = SearchResult.objects.create(
            keyword=kw, country="us", popularity_score=50, difficulty_score=40,
            competitors_data=[{"trackId": 111, "userRatingCount": 300,
                               "averageUserRating": 4.4, "releaseDate": RELEASED}],
        )
        SearchResult.objects.filter(pk=older.pk).update(
            searched_at=datetime.now(timezone.utc) - timedelta(days=5))
        SearchResult.objects.filter(pk=newer.pk).update(
            searched_at=datetime.now(timezone.utc) - timedelta(days=1))
        self.assertEqual(app_profiles.backfill_from_history(), 1)
        app.refresh_from_db()
        profile = app_profiles.cached_profile(app, "us")
        self.assertEqual((profile.ratings, profile.average, profile.released),
                         (300, 4.4, RELEASED))
