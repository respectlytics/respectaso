"""One Apple read per keyword, per storefront, per day (aso/day_reads.py).

Free-tier test: no aso_pro import.
"""

from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone

from aso import day_reads
from aso.models import Keyword, KeywordDayRead

from .helpers import ranked_search


def apps(n=30, first_id=1000):
    return [{"trackId": first_id + i, "trackName": f"App {i}", "userRatingCount": 100 * (i + 1)}
            for i in range(n)]


def service(result=None):
    itunes = mock.MagicMock()
    itunes.search_ranked.return_value = result or ranked_search(apps())
    return itunes


class OneReadPerDayTest(TestCase):
    def test_a_second_ask_the_same_day_reuses_the_read(self):
        itunes = service()
        first = day_reads.get_or_fetch("habit tracker", "us", itunes_service=itunes)
        second = day_reads.get_or_fetch("habit tracker", "us", itunes_service=itunes)
        self.assertEqual(itunes.search_ranked.call_count, 1)
        self.assertEqual(first.pk, second.pk)
        self.assertEqual(KeywordDayRead.objects.count(), 1)

    def test_the_term_is_one_keyword_in_any_case_and_spacing(self):
        itunes = service()
        day_reads.get_or_fetch("  Habit Tracker ", "US", itunes_service=itunes)
        day_reads.get_or_fetch("habit tracker", "us", itunes_service=itunes)
        self.assertEqual(itunes.search_ranked.call_count, 1)
        itunes.search_ranked.assert_called_once_with("habit tracker", country="us")

    def test_each_storefront_is_its_own_read(self):
        itunes = service()
        day_reads.get_or_fetch("habit tracker", "us", itunes_service=itunes)
        day_reads.get_or_fetch("habit tracker", "se", itunes_service=itunes)
        self.assertEqual(itunes.search_ranked.call_count, 2)

    def test_force_asks_apple_again_and_replaces_the_day(self):
        itunes = service(ranked_search(apps(first_id=1000)))
        day_reads.get_or_fetch("habit tracker", "us", itunes_service=itunes)
        itunes.search_ranked.return_value = ranked_search(apps(first_id=5000))
        read = day_reads.get_or_fetch("habit tracker", "us", itunes_service=itunes, force=True)
        self.assertEqual(itunes.search_ranked.call_count, 2)
        self.assertEqual(KeywordDayRead.objects.count(), 1)
        self.assertEqual(read.ranked_ids[0], 5000)
        self.assertEqual(day_reads.stored_today("habit tracker", "us").ranked_ids[0], 5000)

    def test_a_new_day_asks_apple_again(self):
        itunes = service()
        day_reads.get_or_fetch("habit tracker", "us", itunes_service=itunes)
        tomorrow = timezone.now() + timedelta(days=1)
        with mock.patch("aso.day_reads.timezone.now", return_value=tomorrow):
            day_reads.get_or_fetch("habit tracker", "us", itunes_service=itunes)
        self.assertEqual(itunes.search_ranked.call_count, 2)
        self.assertEqual(KeywordDayRead.objects.count(), 2)

    def test_stored_today_never_asks_apple(self):
        self.assertIsNone(day_reads.stored_today("habit tracker", "us"))
        itunes = service()
        day_reads.get_or_fetch("habit tracker", "us", itunes_service=itunes)
        self.assertIsNotNone(day_reads.stored_today("Habit Tracker", "us"))
        self.assertEqual(itunes.search_ranked.call_count, 1)

    def test_the_read_keeps_the_order_the_first_25_and_the_count(self):
        read = day_reads.get_or_fetch("habit tracker", "us", itunes_service=service(ranked_search(apps(30))))
        self.assertEqual(len(read.ranked_ids), 30)
        self.assertEqual(read.result_count, 30)
        self.assertEqual([app["trackId"] for app in read.competitors], read.ranked_ids[:25])


class RankOfTest(TestCase):
    def setUp(self):
        self.read = day_reads.get_or_fetch("habit tracker", "us", itunes_service=service(ranked_search(apps(30))))

    def test_rank_is_the_position_in_apples_order(self):
        self.assertEqual(day_reads.rank_of(self.read, 1000), 1)
        self.assertEqual(day_reads.rank_of(self.read, 1029), 30)
        self.assertEqual(day_reads.rank_of(self.read, "1004"), 5)

    def test_absent_app_no_app_and_no_read_have_no_rank(self):
        self.assertIsNone(day_reads.rank_of(self.read, 42))
        self.assertIsNone(day_reads.rank_of(self.read, None))
        self.assertIsNone(day_reads.rank_of(None, 1000))


class PacerTest(TestCase):
    def test_waits_between_real_reads_but_not_before_the_first(self):
        wait = mock.Mock()
        pacer = day_reads.Pacer(wait=wait)
        self.assertTrue(pacer.before("one", "us"))
        wait.assert_not_called()
        self.assertTrue(pacer.before("two", "us"))
        wait.assert_called_once()

    def test_never_waits_before_a_read_stored_today(self):
        day_reads.get_or_fetch("stored", "us", itunes_service=service())
        wait = mock.Mock()
        pacer = day_reads.Pacer(wait=wait)
        self.assertTrue(pacer.before("one", "us"))
        self.assertFalse(pacer.before("stored", "us"))
        wait.assert_not_called()

    def test_force_waits_even_for_a_stored_read(self):
        day_reads.get_or_fetch("stored", "us", itunes_service=service())
        wait = mock.Mock()
        pacer = day_reads.Pacer(wait=wait)
        pacer.before("one", "us")
        self.assertTrue(pacer.before("stored", "us", force=True))
        wait.assert_called_once()


class TidyTest(TestCase):
    def _read_on(self, days_ago):
        # A tracked keyword: a finished day of one no longer tracked goes at
        # once (test_disk_space.ForgetUntrackedReadsTest).
        Keyword.objects.create(keyword=f"term {days_ago}")
        read = day_reads.get_or_fetch(f"term {days_ago}", "us", itunes_service=service())
        KeywordDayRead.objects.filter(pk=read.pk).update(day=read.day - timedelta(days=days_ago))
        return read.pk

    def test_finished_days_lose_their_competitors_and_old_reads_go(self):
        today, yesterday, old = self._read_on(0), self._read_on(1), self._read_on(91)
        emptied, deleted = day_reads.tidy(90)
        self.assertEqual((emptied, deleted), (2, 1))
        self.assertTrue(KeywordDayRead.objects.get(pk=today).competitors)
        yesterday_row = KeywordDayRead.objects.get(pk=yesterday)
        self.assertEqual(yesterday_row.competitors, [])
        self.assertEqual(len(yesterday_row.ranked_ids), 30, "ranks stay for the whole retention")
        self.assertFalse(KeywordDayRead.objects.filter(pk=old).exists())

    def test_a_second_tidy_changes_nothing(self):
        self._read_on(1)
        day_reads.tidy(90)
        self.assertEqual(day_reads.tidy(90), (0, 0))
