"""A wait in words, written one way in Python and in the browser.

Two Python functions worded a wait differently ("about 1 h 7 min" on the
cleanup banner, "about 3 minutes" on the Opportunity page), and three
scripts carried their own copies. Now aso.opportunity_scans.duration_text
is the only Python copy and durationText() in static/js/country-picker.js
the only script copy (CLEANUPS_PLAN.md, item 2).
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from aso.opportunity_scans import duration_text

BASE_DIR = Path(settings.BASE_DIR)


class DurationTextTest(SimpleTestCase):
    def test_words(self):
        cases = {
            0: "", None: "", -5: "",
            4: "10 seconds", 25: "30 seconds", 44: "40 seconds", 54: "50 seconds",
            55: "about 1 minute", 60: "about 1 minute", 61: "about 2 minutes",
            48 * 60: "about 48 minutes", 3599: "about 1 hour",
            3600: "about 1 hour", 3601: "about 1 hour 1 minute", 4020: "about 1 hour 7 minutes",
            2 * 3600 + 10 * 60: "about 2 hours 10 minutes", 3 * 3600: "about 3 hours",
        }
        for seconds, words in cases.items():
            with self.subTest(seconds=seconds):
                self.assertEqual(duration_text(seconds), words)

    def test_the_script_writes_what_python_writes(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        values = sorted({*range(0, 121, 5), *(i * 7.3 for i in range(60)), *range(55, 20000, 53),
                         3599.5, 3600, 3601, 7199, 7200, 86400})
        script = (
            "global.window = {};"
            "global.document = {readyState: 'loading', addEventListener: function () {},"
            " getElementById: function () { return null; }, querySelectorAll: function () { return []; }};"
            f"require({json.dumps(str(BASE_DIR / 'static/js/country-picker.js'))});"
            "const values = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
            "process.stdout.write(JSON.stringify(values.map(v => window.CountryPicker.durationText(v))));"
        )
        out = subprocess.run([node, "-e", script], input=json.dumps(values), capture_output=True,
                             text=True, check=True, timeout=30).stdout
        for seconds, words in zip(values, json.loads(out)):
            with self.subTest(seconds=seconds):
                self.assertEqual(words, duration_text(seconds))

    def test_one_copy_each(self):
        python = []
        for folder in ("aso", "aso_pro"):
            for path in (BASE_DIR / folder).rglob("*.py"):
                if "tests" not in path.parts and "def duration_text" in path.read_text(encoding="utf-8"):
                    python.append(os.path.relpath(path, BASE_DIR))
        self.assertEqual(python, ["aso/opportunity_scans.py"])
        scripts = []
        for path in (BASE_DIR / "static/js").glob("*.js"):
            text = path.read_text(encoding="utf-8")
            body = re.search(r"function durationText\(seconds\) \{(.*?)\n    \}", text, re.DOTALL)
            if body and "CountryPicker.durationText" not in body.group(1):
                scripts.append(path.name)
        self.assertEqual(scripts, ["country-picker.js"])
