"""No emoji in page titles, card titles or section headings
(docs/development/UI_FOUNDATIONS_PLAN.md, UI_REDESIGN_PLAN.md 12.4). The
Insight tags keep theirs: they are data glyphs, not headings."""

import re
from pathlib import Path

from django.test import SimpleTestCase

BASE = Path(__file__).resolve().parents[2]
ROOTS = ("aso", "aso_pro", "static/js", "_public_overrides")
EMOJI = re.compile("[\U0001F000-\U0001FAFF⌀-⏿①-➿⬀-⯿]")
HEADING = re.compile(r"<h([1-4])\b[^>]*>(.*?)</h\1>", re.DOTALL)
CARD_TITLE = re.compile(r'<([a-z0-9]+)\b[^>]*class="[^"]*\bcard-title\b[^"]*"[^>]*>(.*?)</\1>', re.DOTALL)


class NoEmojiHeadingsTest(SimpleTestCase):
    def test_headings_carry_no_emoji(self):
        found = []
        for root in ROOTS:
            folder = BASE / root
            if not folder.exists():
                continue
            for path in folder.rglob("*"):
                if path.suffix not in {".html", ".js"} or "/tests/" in str(path):
                    continue
                text = path.read_text(encoding="utf-8")
                for pattern in (HEADING, CARD_TITLE):
                    for match in pattern.finditer(text):
                        if EMOJI.search(match.group(2)):
                            line = text.count("\n", 0, match.start()) + 1
                            found.append(f"{path.relative_to(BASE)}:{line}: {match.group(2).strip()[:80]}")
        self.assertFalse(found, "Headings without emoji:\n" + "\n".join(found))


class HeadingCountersKeptTest(SimpleTestCase):
    """The emoji sweep of 2026-10-02 also dropped empty spans that scripts
    fill in (a count, a heading). These ids must exist in the templates."""

    FILLED_BY_SCRIPT = (
        ("labels-dialog-count", "aso/templates/aso/dashboard.html"),
        ("queue-count", "aso/templates/aso/partials/_queue_section.html"),
        ("rival-add-count", "aso_pro/templates/aso_pro/rival_tracker/_dashboard_dialog.html"),
    )

    def test_script_filled_spans_are_there(self):
        for element_id, rel in self.FILLED_BY_SCRIPT:
            path = BASE / rel
            if not path.exists():
                continue   # the free edition has no aso_pro templates
            with self.subTest(element=element_id):
                self.assertIn(f'id="{element_id}"', path.read_text(encoding="utf-8"))
