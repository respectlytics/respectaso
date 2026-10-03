"""Expired sessions are deleted by the scheduler's hourly tick.

What the app remembers for a visitor lives in the Django session for a year
(SESSION_COOKIE_AGE, aso/ui_memory.py), and Django deletes an expired row
only when clearsessions runs (docs/development/CI_AND_LINT_PLAN.md, section 6).

Free-tier test: no aso_pro import.
"""

import json
from datetime import timedelta
from unittest import mock

from django.conf import settings
from django.contrib.sessions.backends.db import SessionStore
from django.contrib.sessions.models import Session
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from aso import scheduler


def _tick():
    """One hourly check with nothing else to do: no Apple sync, no refresh due."""
    with mock.patch("aso.apple_ads.sync.maybe_run_sync"), \
         mock.patch("aso.scheduler._needs_refresh_today", return_value=False):
        scheduler._tick()


def _session(expires_in):
    store = SessionStore()
    store["anything"] = 1
    store.create()
    Session.objects.filter(pk=store.session_key).update(expire_date=timezone.now() + expires_in)
    return store.session_key


class ExpiredSessionsTest(TestCase):
    def test_a_visitors_memory_lasts_a_year(self):
        response = self.client.post(
            reverse("aso:ui_memory"),
            data=json.dumps({"name": "app_summary_folded", "value": True}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        session = Session.objects.get()
        lasts = session.expire_date - timezone.now()
        self.assertEqual(settings.SESSION_COOKIE_AGE, 365 * 24 * 60 * 60)
        self.assertGreater(lasts, timedelta(days=364))
        self.assertLessEqual(lasts, timedelta(days=365))

    def test_the_tick_deletes_expired_sessions_and_keeps_live_ones(self):
        expired = _session(timedelta(seconds=-1))
        live = _session(timedelta(days=200))
        _tick()
        self.assertFalse(Session.objects.filter(pk=expired).exists())
        self.assertTrue(Session.objects.filter(pk=live).exists())

    def test_a_visitor_whose_year_ran_out_is_forgotten(self):
        self.client.post(
            reverse("aso:ui_memory"),
            data=json.dumps({"name": "app_summary_folded", "value": True}),
            content_type="application/json",
        )
        Session.objects.update(expire_date=timezone.now() - timedelta(minutes=1))
        _tick()
        self.assertFalse(Session.objects.exists())

    def test_a_failed_cleanup_is_logged_and_the_tick_goes_on(self):
        with mock.patch("aso.scheduler._clear_expired_sessions", side_effect=RuntimeError("disk")), \
             mock.patch("aso.apple_ads.sync.maybe_run_sync"), \
             mock.patch("aso.scheduler._needs_refresh_today", return_value=True), \
             mock.patch("aso.scheduler.run_queue.lane_state", return_value="idle"), \
             mock.patch("aso.scheduler._run_daily_refresh") as refresh, \
             self.assertLogs("aso.scheduler", level="ERROR") as logs:
            scheduler._tick()
        refresh.assert_called_once()
        self.assertIn("Session cleanup error: disk", "\n".join(logs.output))
