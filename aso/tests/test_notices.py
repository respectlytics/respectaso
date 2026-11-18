"""One look for one kind of thing: every banner is a notice (owner, 2026-11-17).

What happened (added, applied, failed) and where something stands (with
Apple, connected) is said in a .notice (static/css/tailwind.source.css):
the app-added banner's green for success, sky for waiting, amber for a
warning, red for an error, an icon first and an X last. A short warning
inside a card is a .hint-warning. The owner found the Store tabs saying
"Applied to App Store Connect" in a plain grey card while the Apps page
said "App added" in green, and asked for one design everywhere.

This reads every template and script, so a hand-tinted banner fails here
whichever page it is on; the few tinted boxes that are content, not a
banner, are listed with why.

Free-tier test: no aso_pro import; it runs in the public repository too.
"""

import json
import re
from pathlib import Path
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from aso import ui_state

BASE = Path(settings.BASE_DIR)
DIRS = ("aso/templates", "aso_pro/templates", "_public_overrides", "static/js")

TINTS = "green|emerald|red|amber|blue|sky|indigo|yellow|lime|cyan"
TINTED_BG = re.compile(rf"\bbg-({TINTS})-(500|600|900)/\d+")
TINTED_BORDER = re.compile(rf"\bborder(-[a-z])?-({TINTS})-")
PADDED = re.compile(r"\b(p|px)-[2-4]")
ROUNDED = re.compile(r"\brounded-(lg|xl)\b")
CLASS = re.compile(r"""class(?:Name)?\s*=\s*(["'])(.*?)\1""")

# Tinted boxes that are content, not a banner: (file, text on the line, why).
CONTENT = (
    ("aso/templates/aso/opportunity.html", 'id="opp-best"', "the best opportunity, a result"),
    ("static/js/opportunity-scan.js", "rounded-lg p-3 mb-4", "a country's signal, a result"),
    ("static/js/opportunity-scan.js", "rounded-lg p-2.5", "a country's signal, a result"),
    ("static/js/download-chart.js", "rounded-lg p-2.5", "a figure under the chart"),
    ("aso/templates/aso/methodology.html", "bg-yellow-900/20", "a note in the methodology page's text"),
    ("aso_pro/templates/aso_pro/simulator.html", "inline-flex items-center gap-1 text-xs", "a keyword's tag"),
    ("aso_pro/templates/aso_pro/_includes/track_keywords.html", "-translate-x-1/2 z-40", "a toast, floating"),
)


def files():
    for folder in DIRS:
        root = BASE / folder
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix in (".html", ".js") and ".min." not in path.name and "vendor" not in path.parts:
                yield path


class NoHandTintedBanners(SimpleTestCase):
    def test_every_banner_is_a_notice(self):
        found = []
        for path in files():
            rel = str(path.relative_to(BASE))
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                for match in CLASS.finditer(line):
                    classes = match.group(2)
                    if not (TINTED_BG.search(classes) and TINTED_BORDER.search(classes) and PADDED.search(classes)
                            and ROUNDED.search(classes)) or "hover:" in classes:
                        continue
                    if any(rel == f and text in line for f, text, _why in CONTENT):
                        continue
                    found.append(f"{rel}:{number}: {classes[:90]}")
        self.assertEqual(found, [], "Use .notice (with aso/partials/notice_icon.html) or .hint-warning:\n"
                         + "\n".join(found))

    def test_every_content_exception_still_matches(self):
        for rel, text, why in CONTENT:
            path = BASE / rel
            if path.exists():
                self.assertIn(text, path.read_text(encoding="utf-8"), f"{rel}: {why} is gone; drop the entry")

    def test_every_notice_has_its_icon(self):
        notice = re.compile(r"""class=["']notice notice-""")
        missing = []
        for path in files():
            if path.suffix != ".html":
                continue
            text = path.read_text(encoding="utf-8")
            for match in notice.finditer(text):
                after = text[match.end():match.end() + 700]
                if "notice_icon" not in after and "notice-icon" not in after and "data-tone-icon" not in after:
                    missing.append(f"{path.relative_to(BASE)}:{text[:match.start()].count(chr(10)) + 1}")
        self.assertEqual(missing, [])


class WritesAtOnce(SimpleTestCase):
    """Two closes at the same moment once wrote over each other and left a file
    nobody could read, so every dismissal was lost (2026-11-17)."""

    def test_writes_from_many_threads_keep_a_whole_file(self):
        import tempfile
        import threading

        with tempfile.TemporaryDirectory() as folder, self.settings(DATA_DIR=folder):
            ui_state.set_time_zone("Europe/Stockholm")
            threads = [threading.Thread(target=ui_state.dismiss_notice, args=(f"store-run:{n}",)) for n in range(40)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            data = json.loads((Path(folder) / "ui_state.json").read_text(encoding="utf-8"))
            self.assertEqual(sum(k.startswith("notice:") for k in data["dismissed"]), 40)
            self.assertEqual(data["time_zone"], "Europe/Stockholm")
            self.assertEqual([p.name for p in Path(folder).iterdir()], ["ui_state.json"])   # no file left behind


class NoticeDismissals(TestCase):
    def setUp(self):
        self.data = {}
        patcher_load = mock.patch.object(ui_state, "_load", side_effect=lambda: json.loads(json.dumps(self.data)))
        patcher_save = mock.patch.object(ui_state, "_save", side_effect=lambda d: self.data.update(d))
        patcher_load.start()
        patcher_save.start()
        self.addCleanup(patcher_load.stop)
        self.addCleanup(patcher_save.stop)

    def test_closed_stays_closed_and_a_new_state_shows(self):
        self.assertFalse(ui_state.notice_dismissed("store-run:12"))
        response = self.client.post(reverse("aso:notice_dismiss"), json.dumps({"key": "store-run:12"}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ui_state.notice_dismissed("store-run:12"))
        self.assertFalse(ui_state.notice_dismissed("store-run:13"))

    def test_a_name_no_notice_uses_is_refused(self):
        for key in ("", "no colon", "x" * 200, "store-run:<script>", None, 12):
            response = self.client.post(reverse("aso:notice_dismiss"), json.dumps({"key": key}),
                                        content_type="application/json")
            self.assertEqual(response.status_code, 400, key)
        self.assertEqual(self.data, {})

    def test_only_the_newest_are_kept(self):
        for n in range(ui_state.NOTICE_KEEP + 5):
            ui_state.dismiss_notice(f"store-run:{n}")
        self.assertFalse(ui_state.notice_dismissed("store-run:4"))
        self.assertTrue(ui_state.notice_dismissed("store-run:5"))
        self.assertTrue(ui_state.notice_dismissed(f"store-run:{ui_state.NOTICE_KEEP + 4}"))

    def test_other_dismissals_are_kept(self):
        ui_state.dismiss(ui_state.KEYWORD_CLEANUP_BANNER)
        for n in range(ui_state.NOTICE_KEEP + 5):
            ui_state.dismiss_notice(f"store-run:{n}")
        self.assertTrue(ui_state.is_dismissed(ui_state.KEYWORD_CLEANUP_BANNER))

    def test_one_x_closes_several_in_one_request(self):
        response = self.client.post(reverse("aso:notice_dismiss"),
                                    json.dumps({"keys": ["store-run:3", "store-outcome:1:submit:2.3"]}),
                                    content_type="application/json")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(ui_state.notice_dismissed("store-run:3"))
        self.assertTrue(ui_state.notice_dismissed("store-outcome:1:submit:2.3"))
        bad = self.client.post(reverse("aso:notice_dismiss"), json.dumps({"keys": ["store-run:4", "bad key"]}),
                               content_type="application/json")
        self.assertEqual(bad.status_code, 400)
        self.assertFalse(ui_state.notice_dismissed("store-run:4"))        # all or none

    def test_the_x_works_on_every_page(self):
        for rel in ("aso/templates/aso/base.html", "_public_overrides/aso/templates/aso/base.html"):
            path = BASE / rel
            if path.exists():
                self.assertIn("js/notice.js", path.read_text(encoding="utf-8"), rel)
