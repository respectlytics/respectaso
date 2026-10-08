"""The Mac app's window opens at once on a loading page while the data is
prepared behind it (desktop/main.py start_app).

On 2026-10-08 the first launch after an update on a 2 GB database showed
nothing but a bouncing Dock icon for 12 seconds: the one-time database
conversion ran before the window existed. Now the window shows "Getting your
data ready" with a moving bar until the app replaces it, says so when the
start fails, and the menu bar never reads the database meanwhile.

Imports desktop/main.py, which runs on Linux too: nothing here needs AppKit.
"""

import inspect
import re
import tempfile
from pathlib import Path
from unittest import mock
from urllib.parse import parse_qs, unquote, urlsplit

from django.test import SimpleTestCase, TestCase

from aso import desktop_bridge, scheduler
from aso.links import CONTACT_EMAIL
from desktop import main


def _mail_of(page):
    """The Email us button's link, decoded: (address, subject, body)."""
    link = re.search(r'data-mail="([^"]+)"', page).group(1).replace("&amp;", "&")
    parts = urlsplit(link)
    query = parse_qs(parts.query)
    return unquote(parts.path), query["subject"][0], query["body"][0]


class _StartupState:
    def setUp(self):
        super().setUp()
        self.addCleanup(desktop_bridge.set_startup_state, desktop_bridge.READY)


class MenuBarWhileStartingTest(_StartupState, TestCase):
    def test_while_starting_it_says_so_and_reads_nothing(self):
        desktop_bridge.set_startup_state(desktop_bridge.STARTING)
        with self.assertNumQueries(0):
            line, can_refresh, _title = scheduler.menu_status()
        self.assertEqual((line, can_refresh), (desktop_bridge.COPY["menu_starting"], False))

    def test_a_failed_start_says_so(self):
        desktop_bridge.set_startup_state(desktop_bridge.FAILED)
        with self.assertNumQueries(0):
            line, can_refresh, _title = scheduler.menu_status()
        self.assertEqual((line, can_refresh), (desktop_bridge.COPY["menu_start_failed"], False))

    def test_once_ready_it_reads_the_keywords(self):
        line, _can_refresh, _title = scheduler.menu_status()
        self.assertEqual(line, "No tracked keywords yet")

    def test_any_other_process_is_ready_from_the_start(self):
        self.assertEqual(desktop_bridge.startup_state(), desktop_bridge.READY)
        with self.assertRaises(ValueError):
            desktop_bridge.set_startup_state("almost")


class LoadingPageTest(SimpleTestCase):
    def test_the_starting_page(self):
        page = main.startup_page("starting")
        self.assertIn(desktop_bridge.COPY["starting_title"], page)
        self.assertIn(desktop_bridge.COPY["starting_hint"], page)
        self.assertIn('class="bar"', page)
        self.assertIn("prefers-reduced-motion", page, "the bar stands still for people who ask for less motion")
        self.assertNotIn(desktop_bridge.COPY["start_failed_title"], page)

    def _failed(self, log=None):
        logging = {"handlers": {"file": {"filename": str(log)}}} if log else {}
        with mock.patch("platform.mac_ver", return_value=("26.6", ("", "", ""), "arm64")), \
             self.settings(VERSION="9.9.9", LOGGING=logging):
            return main.startup_page("failed", error="DatabaseError: file is not a database")

    def test_the_failed_page_shows_exactly_what_to_send_and_how(self):
        log = Path.home() / "Library" / "Application Support" / "RespectASO" / "respectaso.log"
        page = self._failed(log)
        self.assertIn(desktop_bridge.COPY["start_failed_title"], page)
        self.assertIn(desktop_bridge.COPY["start_failed_text"], page)
        self.assertIn(CONTACT_EMAIL, page, "the address is on the screen for anyone without a mail app")
        self.assertNotIn('class="bar"', page)
        # 1: the details themselves, on the screen, with a Copy button.
        for line in ("RespectASO version: 9.9.9", "macOS version: 26.6",
                     "What went wrong: DatabaseError: file is not a database"):
            self.assertIn(line, page)
        self.assertIn('onclick="copyDetails(this)"', page)
        self.assertIn('ask("copy_to_clipboard"', page)
        # 2: where the log file is, with a button that shows it in Finder.
        self.assertIn("~/Library/Application Support/RespectASO/respectaso.log", page)
        self.assertIn("ask('show_log')", page)
        self.assertIn("def show_log(self)", inspect.getsource(main.main))
        # Email us, which fills the details in.
        self.assertIn("ask('open_external', this.dataset.mail)", page)
        # A button with nothing to send hands the bridge no argument: with one
        # undefined argument, show_log() failed with a TypeError when clicked.
        self.assertIn("apply(null, args)", page)

    def test_the_email_already_holds_the_details(self):
        address, subject, body = _mail_of(self._failed())
        self.assertEqual(address, CONTACT_EMAIL)
        self.assertEqual(subject, desktop_bridge.COPY["start_failed_title"])
        self.assertIn(desktop_bridge.start_failed_details("9.9.9", "26.6", "DatabaseError: file is not a database"), body)
        self.assertIn("respectaso.log", body)

    def test_it_needs_nothing_from_any_server(self):
        """No server runs yet: the typeface and the logo travel inside the page."""
        page = main.startup_page("starting")
        self.assertEqual(re.findall(r"(?:src|href)=[\"'](?!data:)", page), [])
        self.assertEqual(re.findall(r"url\((?!data:)", page), [])
        self.assertEqual(page.count("data:font/woff2;base64,"), 3)
        self.assertIn("data:image/png;base64,", page)
        self.assertEqual(set(re.findall(r'font-family:\s*"([^"]+)"', page)), {"Schibsted Grotesk"})

    def test_its_background_is_the_windows(self):
        self.assertIn(f"background: {main.STARTUP_BACKGROUND.lower()}", main.startup_page("starting").lower())


class StartAppTest(_StartupState, SimpleTestCase):
    def setUp(self):
        super().setUp()
        desktop_bridge.set_startup_state(desktop_bridge.STARTING)
        self.window = mock.Mock()
        self.seen = []
        self.window.load_url.side_effect = lambda url: self.seen.append(desktop_bridge.startup_state())

    def test_the_app_replaces_the_loading_page_once_the_data_is_ready(self):
        with mock.patch.object(main, "prepare_and_serve", return_value=8123):
            main.start_app(self.window)
        self.window.load_url.assert_called_once_with("http://127.0.0.1:8123")
        self.assertEqual(self.seen, [desktop_bridge.READY], "the menu bar may read the database by then")
        self.window.load_html.assert_not_called()

    def test_a_failed_start_says_so_in_the_window_and_the_log(self):
        with mock.patch.object(main, "prepare_and_serve", side_effect=RuntimeError("disk full")), \
             self.assertLogs("desktop.main", "ERROR") as logs:
            main.start_app(self.window)
        self.assertIn("disk full", "\n".join(logs.output))
        self.assertEqual(desktop_bridge.startup_state(), desktop_bridge.FAILED)
        self.window.load_url.assert_not_called()
        page = self.window.load_html.call_args.args[0]
        self.assertIn(desktop_bridge.COPY["start_failed_title"], page)
        self.assertIn("What went wrong: RuntimeError: disk full", _mail_of(page)[2])


class ShowLogTest(SimpleTestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.data = Path(folder.name)
        self.log = self.data / "respectaso.log"

    def _show(self):
        # LOGGING only: overriding DATA_DIR here let another part of the app
        # put its lock file in this temporary folder (test_run_owner flaked).
        logging = {"handlers": {"file": {"filename": str(self.log)}}}
        with self.settings(LOGGING=logging), \
             mock.patch("subprocess.run", return_value=mock.Mock(returncode=0)) as run:
            self.assertTrue(main.show_log_in_finder())
        return run.call_args.args[0]

    def test_finder_opens_with_the_log_file_selected(self):
        self.log.write_text("log")
        self.assertEqual(self._show(), ["/usr/bin/open", "-R", str(self.log)])

    def test_without_a_log_yet_finder_opens_its_folder(self):
        self.assertEqual(self._show(), ["/usr/bin/open", str(self.data)])


class TheWindowComesFirstTest(SimpleTestCase):
    def test_main_opens_the_window_on_the_loading_page_and_leaves_the_slow_work_to_start_app(self):
        source = inspect.getsource(main.main)
        self.assertIn('html=startup_page("starting")', source)
        self.assertIn("webview.start(start_app, (window,))", source)
        for slow in ('call_command("migrate"', "start_history_upgrade()", "resume_after_startup()", "run_server"):
            self.assertNotIn(slow, source, f"{slow} runs before the window exists")
        self.assertLess(source.index("set_startup_state(desktop_bridge.STARTING)"), source.index("webview.create_window("))
