"""One place for everything that runs (docs/development/ACTIVITY_CENTER_PLAN.md,
UI_REDESIGN_PLAN.md 5): the Activity panel's words, the note that flies into
the pill when something starts, the daily update in the pill, and the
Keywords page's one-line search status."""

from pathlib import Path

from django.test import TestCase
from django.urls import reverse

from aso.copy_rules import dash_punctuation_in, mechanics_in

BASE = Path(__file__).resolve().parents[2]
FOOTNOTE = "One task at a time, so every result is fresh and complete."


class ActivityPanelTest(TestCase):
    def test_the_panel_says_activity_and_why_one_at_a_time(self):
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        root = html[html.index('id="activity-root"'):]
        self.assertIn(f'data-daily-url="{reverse("aso:auto_refresh_status")}"', root[:300])
        self.assertIn(">Activity</p>", html)
        self.assertIn(FOOTNOTE, html)
        self.assertNotIn("Running in the background", html)

    def test_no_bottom_bar_and_no_queued_note_on_keywords(self):
        html = self.client.get(reverse("aso:dashboard")).content.decode()
        self.assertNotIn('id="auto-refresh-bar"', html)
        self.assertNotIn('id="search-queued-note"', html)


class TheRunningTaskComesFirstTest(TestCase):
    """The owner, 2026-10-02: while a Simulator run was going, a country scan
    waiting in the queue sat at the top of the Activity panel with a spinner
    and named the pill, and on the Simulator page the running simulation was
    not in the panel at all, so the queue looked as if it had dropped it.
    A search or scan that waits is in Up next only; the running run is the
    first row on every page, its own included."""

    def make(self, model, **fields):
        from aso.models import KeywordSearchJob, OpportunityScan

        if model == "scan":
            return OpportunityScan.objects.create(keyword="trading alerts", countries=["us", "gb"], **fields)
        return KeywordSearchJob.objects.create(countries=["us"], keywords=["a", "b"], **fields)

    def test_a_waiting_search_or_scan_is_not_drawn_as_running(self):
        from aso import job_strip

        self.make("scan", status="queued", queue_rank=1)
        self.make("job", status="queued", queue_rank=2)
        self.assertEqual(job_strip.strip_state(), {"shown": False, "active": True})
        html = self.client.get(reverse("aso:apps")).content.decode()
        self.assertIn('id="search-job-strip" class="hidden ', html)
        self.assertNotIn("queued:", html)
        queued = self.client.get(reverse("aso:queue_status")).json()["queued"]
        self.assertEqual([row["feature"] for row in queued], ["opportunity_scan", "keyword_search"])

    def test_one_that_waits_keeps_the_page_asking_until_it_starts(self):
        from django.utils import timezone

        from aso import job_strip

        self.make("job", status="completed", finished_at=timezone.now())
        scan = self.make("scan", status="queued")
        state = job_strip.strip_state()
        self.assertTrue(state["shown"])
        self.assertEqual(state["kind"], "keyword_search")
        self.assertTrue(state["active"])
        scan.status = "running"
        scan.save()
        state = job_strip.strip_state()
        self.assertEqual((state["kind"], state["icon"]), ("opportunity_scan", "spinner"))
        self.assertTrue(state["text"].startswith("Country scan running"))

    def test_the_panel_draws_the_run_of_the_page_it_is_on(self):
        script = (BASE / "static/js/run-queue.js").read_text()
        self.assertIn("var runningRun = elsewhereRun || (payload && payload.running_here);", script)
        self.assertIn("toggle('queue-running', !!shownRunning);", script)
        self.assertIn("reportActivity(runningRun, queued.length);", script)
        self.assertNotIn("queue-running-elsewhere", script)
        strip = (BASE / "static/js/job-strip.js").read_text()
        self.assertEqual(strip.count("!state || !state.shown"), 2)
        section = (BASE / "aso/templates/aso/partials/_queue_section.html").read_text()
        self.assertIn('id="queue-running"', section)


class NoteWordsTest(TestCase):
    def setUp(self):
        self.script = (BASE / "static/js/activity-indicator.js").read_text()

    def test_the_four_forms_and_the_footnote_are_written_once(self):
        for words in ("'Queued'", "'Started'", "'You can keep working.'",
                      "'Starts after '", "'. You can keep working.'", FOOTNOTE):
            self.assertIn(words, self.script)

    def test_the_words_obey_the_copy_rules(self):
        for words in ("Queued", "Started", "You can keep working.",
                      "Starts after the current search, in about 3 minutes.", FOOTNOTE,
                      "Updating tracked keywords"):
            self.assertEqual(mechanics_in(words), "", words)
            self.assertEqual(dash_punctuation_in(words), "", words)

    def test_every_starter_announces(self):
        for path in ("static/js/keyword-search-job.js", "static/js/opportunity-scan.js",
                     "static/js/rival-tracker.js", "static/js/rival-setup.js"):
            self.assertIn("ActivityIndicator.announce(", (BASE / path).read_text(), path)


class SearchStatusLineTest(TestCase):
    def test_one_line_without_the_old_note(self):
        panel = (BASE / "aso/templates/aso/partials/search_job_panel.html").read_text()
        self.assertNotIn("sjp-note", panel)
        self.assertNotIn("sjp-ticker", panel)
        script = (BASE / "static/js/keyword-search-job.js").read_text()
        self.assertNotIn("Feel free to use other tabs", script)
        self.assertIn("'Waiting to start'", script)
        self.assertIn("'Checking '", script)
