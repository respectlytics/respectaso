"""Days are the reader's own, wherever they are (aso/local_day.py).

The day used to end at midnight UTC: a click at 01:30 in Sweden counted for
the day before while the Date column showed the new date (2026-09-30), and
a user in California had their day roll over at 17:00. The browser now
reports its time zone, every "today" and every rendered date follows it, and
stored history keeps one read per local day. Tests count in UTC unless they
set a zone (core/settings.py, LOCAL_DAY_FALLBACK_ZONE).

Free-tier test: no aso_pro import.
"""

import json
import os
from datetime import UTC, datetime
from unittest import mock
from zoneinfo import ZoneInfo

from django.conf import settings
from django.test import TestCase
from django.urls import reverse

from aso import keyword_scoring, local_day, scheduler, ui_state
from aso.models import App, Keyword, SearchResult


def utc(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def at(moment):
    """Freeze Django's clock (it also stamps searched_at)."""
    return mock.patch("django.utils.timezone.now", return_value=moment)


def stored(keyword, country, moment, **fields):
    values = {"popularity_score": 40, "difficulty_score": 30, "difficulty_breakdown": {},
              "competitors_data": [], "app_rank": None}
    values.update(fields)
    row = SearchResult.objects.create(keyword=keyword, country=country, **values)
    SearchResult.objects.filter(pk=row.pk).update(searched_at=moment)
    return row.pk


class ZoneTest(TestCase):
    def test_two_quick_changes_are_both_seen(self):
        """Docker's filesystem keeps a coarse modification time: the cache
        must not depend on it alone."""
        self.addCleanup(ui_state.set_time_zone, None)
        with mock.patch("aso.ui_state._mtime", return_value=1_700_000_000_000_000_000):
            ui_state.set_time_zone("America/Los_Angeles")
            self.assertEqual(local_day.zone_name(), "America/Los_Angeles")
            ui_state.set_time_zone("Asia/Tokyo")
            self.assertEqual(local_day.zone_name(), "Asia/Tokyo")

    def setUp(self):
        self.addCleanup(ui_state.set_time_zone, None)

    def use(self, name):
        ui_state.set_time_zone(name)

    def test_the_zone_a_browser_reported_wins_over_the_machines(self):
        self.assertEqual(local_day.zone_name(), "UTC")
        self.use("America/Los_Angeles")
        self.assertEqual(local_day.zone_name(), "America/Los_Angeles")

    def test_remember_says_whether_the_days_changed(self):
        self.assertTrue(local_day.remember("Asia/Tokyo"))
        self.assertFalse(local_day.remember("Asia/Tokyo"))
        with self.assertRaises(ValueError):
            local_day.remember("Mars/Olympus_Mons")
        self.assertEqual(local_day.zone_name(), "Asia/Tokyo")

    def test_today_starts_at_the_readers_midnight(self):
        self.use("America/Los_Angeles")
        start, end = local_day.today_bounds(now=utc(9, 30, 6))   # 23:00 on Sep 29 in California
        self.assertEqual(start, utc(9, 29, 7))
        self.assertEqual(end, utc(9, 30, 7))
        self.use("Europe/Stockholm")
        self.assertEqual(local_day.today_start(now=utc(9, 29, 23, 30)), utc(9, 29, 22))   # 01:30 on Sep 30

    def test_the_machines_zone_is_read_from_the_system_not_from_tz(self):
        """Django sets TZ to TIME_ZONE ("UTC") when it starts, so TZ never
        names the machine's zone inside the app; /etc/localtime does."""
        with self.settings(LOCAL_DAY_FALLBACK_ZONE=None), \
             mock.patch.dict(os.environ, {"TZ": "UTC"}), \
             mock.patch("os.path.realpath", return_value="/var/db/timezone/zoneinfo/Europe/Stockholm"):
            self.assertEqual(local_day._machine_zone().key, "Europe/Stockholm")
        with self.settings(LOCAL_DAY_FALLBACK_ZONE=None), \
             mock.patch("os.path.realpath", return_value="/etc/localtime"):
            self.assertEqual(local_day._machine_zone(), UTC)

    def test_the_time_zone_database_is_there(self):
        """The Docker image and the Mac app must both carry it."""
        for name in ("Europe/Stockholm", "America/Los_Angeles", "Asia/Tokyo", "Australia/Sydney"):
            with self.subTest(zone=name):
                self.assertEqual(ZoneInfo(name).key, name)


class TodayTest(TestCase):
    def setUp(self):
        self.addCleanup(ui_state.set_time_zone, None)
        self.keyword = Keyword.objects.create(keyword="reduce screen time")

    def upsert(self, moment, difficulty):
        with at(moment):
            return SearchResult.upsert_today(
                keyword=self.keyword, country="us", popularity_score=40, difficulty_score=difficulty,
                difficulty_breakdown={}, competitors_data=[],
            )

    def test_one_row_per_local_day_across_the_utc_midnight(self):
        ui_state.set_time_zone("Europe/Stockholm")
        self.upsert(utc(9, 29, 23, 30), 53)     # 01:30 on Sep 30 in Stockholm
        last = self.upsert(utc(9, 30, 8, 0), 52)   # 10:00 on Sep 30
        self.assertEqual(list(SearchResult.objects.values_list("pk", flat=True)), [last.pk])

    def test_two_local_days_inside_one_utc_day_keep_two_rows(self):
        ui_state.set_time_zone("America/Los_Angeles")
        self.upsert(utc(9, 30, 5, 0), 53)       # 22:00 on Sep 29 in California
        self.upsert(utc(9, 30, 20, 0), 52)      # 13:00 on Sep 30
        self.assertEqual(SearchResult.objects.count(), 2)

    def test_the_daily_refresh_is_due_after_local_midnight(self):
        ui_state.set_time_zone("America/Los_Angeles")
        stored(self.keyword, "us", utc(9, 30, 6, 0))    # 23:00 on Sep 29 in California
        with at(utc(9, 30, 8, 0)):                      # 01:00 on Sep 30 there
            self.assertTrue(scheduler._needs_refresh_today())
        ui_state.set_time_zone("UTC")
        with at(utc(9, 30, 8, 0)):
            self.assertFalse(scheduler._needs_refresh_today(), "the same UTC day")


class HistoryDaysTest(TestCase):
    def setUp(self):
        self.addCleanup(ui_state.set_time_zone, None)
        ui_state.set_time_zone("Europe/Stockholm")

    def test_earlier_reads_of_a_local_day_go_and_the_last_stays(self):
        keyword = Keyword.objects.create(keyword="reduce screen time")
        early = stored(keyword, "us", utc(9, 29, 23, 30), difficulty_score=53)   # Sep 30, 01:30
        late = stored(keyword, "us", utc(9, 30, 8, 0), difficulty_score=52)      # Sep 30, 10:00
        before = stored(keyword, "us", utc(9, 29, 8, 0), difficulty_score=56)    # Sep 29
        other_country = stored(keyword, "gb", utc(9, 29, 23, 45))
        self.assertEqual(keyword_scoring.keep_last_read_per_day(), 1)
        self.assertEqual(set(SearchResult.objects.values_list("pk", flat=True)), {late, before, other_country})
        self.assertFalse(SearchResult.objects.filter(pk=early).exists())
        self.assertEqual(keyword_scoring.keep_last_read_per_day(), 0)

    def test_twins_align_on_the_local_day(self):
        pausely = Keyword.objects.create(keyword="reduce screen time", app=App.objects.create(name="Pausely"))
        none = Keyword.objects.create(keyword="reduce screen time")
        a = stored(none, "us", utc(9, 29, 23, 30), difficulty_score=53)    # Sep 30, 01:30 local
        b = stored(pausely, "us", utc(9, 30, 8, 0), difficulty_score=52)   # Sep 30, 10:00 local
        self.assertEqual(keyword_scoring.align_twin_history(), 1)
        self.assertEqual(SearchResult.objects.get(pk=a).difficulty_score, 52)
        self.assertEqual(SearchResult.objects.get(pk=b).difficulty_score, 52)


class RenderTest(TestCase):
    """What the server writes is in the reader's zone, as the page's script is."""

    def setUp(self):
        self.addCleanup(ui_state.set_time_zone, None)
        ui_state.set_time_zone("Europe/Stockholm")
        self.keyword = Keyword.objects.create(keyword="reduce screen time")
        stored(self.keyword, "us", utc(9, 29, 23, 30))    # 01:30 on Sep 30 in Stockholm

    def test_the_dashboard_writes_local_times(self):
        response = self.client.get(reverse("aso:dashboard"))
        self.assertContains(response, "data-read-date>Sep 30, 2026</span>")
        self.assertContains(response, "data-read-time>01:30</span>")

    def test_the_trend_chart_counts_local_days(self):
        points = self.client.get(reverse("aso:keyword_trend", args=[self.keyword.pk])).json()["data_points"]
        self.assertEqual(points[0]["date"], "2026-09-30")

    def test_the_csv_export_writes_local_times(self):
        export = self.client.get(reverse("aso:export_history_csv")).content.decode()
        self.assertIn("2026-09-30 01:30", export)


class BrowserReportsItsZoneTest(TestCase):
    def setUp(self):
        self.addCleanup(ui_state.set_time_zone, None)

    def post(self, zone):
        return self.client.post(reverse("aso:time_zone"), data=json.dumps({"time_zone": zone}),
                                content_type="application/json")

    def test_a_new_zone_is_stored_and_the_history_regrouped(self):
        with mock.patch("aso.popularity.tidy_history_days") as tidy, \
             mock.patch("threading.Thread", side_effect=lambda target, **kw: mock.Mock(start=target)):
            first = self.post("America/New_York").json()
            again = self.post("America/New_York").json()
        self.assertEqual(first, {"success": True, "changed": True, "time_zone": "America/New_York"})
        self.assertFalse(again["changed"])
        tidy.assert_called_once()

    def test_a_name_that_is_not_a_zone_is_refused(self):
        response = self.post("Nowhere/Special")
        self.assertEqual(response.status_code, 400)
        self.assertEqual(local_day.zone_name(), "UTC")

    def test_every_page_reports_the_browsers_zone(self):
        for rel in ("aso/templates/aso/base.html", "_public_overrides/aso/templates/aso/base.html"):
            path = os.path.join(settings.BASE_DIR, rel)
            if not os.path.exists(path):   # the public build ships the override as base.html
                continue
            with self.subTest(template=rel):
                with open(path) as f:
                    text = f.read()
                self.assertIn("Intl.DateTimeFormat().resolvedOptions().timeZone", text)
                self.assertIn('{% url "aso:time_zone" %}', text)
        page = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertIn("zone === 'UTC'", page)
