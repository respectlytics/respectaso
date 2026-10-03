"""The app keeps nothing in the browser's storage.

desktop-compat forbids localStorage and sessionStorage: the Mac app's
window starts every launch without them (pywebview's private mode), and
what the app remembers must behave the same in the Mac app, Docker and a
browser. Per visitor memory lives in the Django session (aso/ui_memory.py,
aso/history_selection.py), dismissed notices per install in aso/ui_state.py,
user data in the database (docs/development/NO_BROWSER_STORAGE_PLAN.md).

This reads every template and every script of the app, comments included,
so a new use fails here whichever page it is on.

Free-tier test: no aso_pro import; it runs in the public repository too.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BASE = Path(settings.BASE_DIR)
TEMPLATE_DIRS = ("aso/templates", "aso_pro/templates", "_public_overrides")
SCRIPT_DIRS = ("static/js",)
BROWSER_STORAGE = re.compile(r"\b(localStorage|sessionStorage|indexedDB)\b")

# The keys the app used to keep there, so a copy of the old code cannot come
# back under another API either.
OLD_KEYS = (
    "'aso_app'", "'aso_history_country'", "'aso_history_per_page'", "'dash_filters'",
    "'aso_app_summary_collapsed'", '"aso_countries"', '"aso_opportunity_countries"',
    "aso_dismiss_apple_expired_", "opp_results", "aso_history_selected_entries",
)


def app_files():
    """Every template (under the app's template folders that exist here) and
    every script of the app."""
    files = []
    for folder in TEMPLATE_DIRS:
        files += sorted((BASE / folder).rglob("*.html")) if (BASE / folder).is_dir() else []
    for folder in SCRIPT_DIRS:
        files += sorted((BASE / folder).rglob("*.js"))
    return files


def uses(text):
    """(line number, line) of each mention of the browser's storage."""
    return [(n, line.strip()) for n, line in enumerate(text.splitlines(), 1) if BROWSER_STORAGE.search(line)]


class NoBrowserStorageTest(SimpleTestCase):
    def test_no_browser_storage_in_templates_or_scripts(self):
        found = []
        for path in app_files():
            found += [f"{path.relative_to(BASE)}:{n}: {line}" for n, line in uses(path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [], "Keep this in the session, ui_state or the database:\n" + "\n".join(found))

    def test_the_guard_reads_the_app(self):
        names = {path.relative_to(BASE).as_posix() for path in app_files()}
        for expected in ("aso/templates/aso/dashboard.html", "aso/templates/aso/opportunity.html",
                         "static/js/country-picker.js", "static/js/popularity-banner.js",
                         "static/js/ui-memory.js"):
            with self.subTest(file=expected):
                self.assertIn(expected, names)
        self.assertEqual(uses("x\ntry { localStorage.getItem('k') } catch (e) {}\n"),
                         [(2, "try { localStorage.getItem('k') } catch (e) {}")])
        self.assertEqual(len(uses("window.sessionStorage.clear(); indexedDB.open('x')")), 1)
        self.assertEqual(uses("const storageKey = 1; // the session keeps it"), [])

    def test_no_old_storage_key_remains(self):
        found = [
            f"{path.relative_to(BASE)}: {key}"
            for path in app_files()
            for key in OLD_KEYS
            if key in path.read_text(encoding="utf-8")
        ]
        self.assertEqual(found, [])
