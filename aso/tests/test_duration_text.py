"""A wait the app itself sets, in words, written one way.

aso.opportunity_scans.duration_text is the only copy (CLEANUPS_PLAN.md,
item 2). Since 2026-11-18 it words only a wait the app sets, such as the
cool-down after Apple turns requests away ("carries on by itself in 3
minutes"): the app never estimates how long a run will take, so no script
words a duration at all (.github/instructions/user-facing-docs.instructions.md).
"""

import os
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

    def test_one_copy_each(self):
        python = []
        for folder in ("aso", "aso_pro"):
            for path in (BASE_DIR / folder).rglob("*.py"):
                if "tests" not in path.parts and "def duration_text" in path.read_text(encoding="utf-8"):
                    python.append(os.path.relpath(path, BASE_DIR))
        self.assertEqual(python, ["aso/opportunity_scans.py"])
        scripts = [path.name for path in (BASE_DIR / "static/js").glob("*.js")
                   if "durationText" in path.read_text(encoding="utf-8")]
        self.assertEqual(scripts, [])
