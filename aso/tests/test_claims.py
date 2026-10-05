"""Nothing RespectASO shows a person promises what the terms disclaim.

The owner's rule (2026-10-02): everything the product says to a person must be
something we can stand behind in court, and the terms (respectaso.com/terms/,
section 10) are the measure: no warranty that keyword data, rankings, scores
or AI output are accurate, reliable or complete, none that RespectASO will
improve an app's rankings or downloads, and none that it runs uninterrupted
or error free. ``aso.copy_rules.OVERCLAIMS`` holds the known shapes of a
sentence that promises one of those anyway (the same list as respectaso.com's
core/copy_rules.py, which its core/test_no_dashes.py proves).

This module reads every template, script and Python string a person can see,
the release notes included, and the MCP tools' descriptions, which an AI
assistant reads and may repeat. A sentence that is right where it stands goes
in ALLOWED with the reason it stays; the last test fails when an allowance
stops matching. Whether a sentence is something we can stand behind is still a
person's reading against the terms at every copy change; this makes the known
shapes impossible to ship again.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from aso.copy_rules import OVERCLAIMS, overclaim_in
from aso.tests.surfaces import (
    flat_text,
    on_a_screen,
    python_strings,
    relative,
    script_strings,
    sources,
    template_texts,
)

# (file, the words that matched, lower case): why they stay.
ALLOWED: dict[tuple[str, str], str] = {
    ("aso_pro/views.py", "guarantee"): "says a successful AI test does not guarantee a fast or complete run",
}

# The shapes themselves and the sentences that prove them.
_OWN = ("aso/copy_rules.py", "aso/tests/test_claims.py")


def _overclaims(where: str, texts) -> list[str]:
    found = []
    for text in texts:
        flat = flat_text(text)
        if " " not in flat:          # a key or a code, never a sentence
            continue
        for name, pattern in OVERCLAIMS.items():
            for m in pattern.finditer(flat):
                if (where, m.group().lower()) not in ALLOWED:
                    found.append(f"{name}: {m.group()!r} in {flat[max(0, m.start() - 60):m.end() + 60]!r}")
    return found


def _read_by_a_person(path: Path) -> bool:
    """On a screen, or in an MCP tool's description an assistant may repeat."""
    return on_a_screen(path) or relative(path).startswith("aso_pro/mcp/")


# ---- the rule itself ----------------------------------------------------------

PROMISES = [
    "Self-correcting AI that guarantees App Store-compliant output.",
    "Get 100% accurate search volumes for every keyword.",
    "RespectASO boosts your downloads in a week.",
    "Our AI never hallucinates keywords.",
    "Your metadata will pass App Review.",
    "Nothing leaves your Mac.",
    "Saves you 10 hours a week.",
    "Never miss a keyword your rivals rank for.",
]

STANDS_UP = [
    "Every title, subtitle and keyword field stays within Apple's character limits.",
    "Download figures are wide ranges, from a tenth of the estimate up to the estimate.",
    "Your keywords go straight from your Mac to the AI provider you choose.",
    "See which keywords your app can win, and what each could bring.",
    "Popularity comes from Apple Ads when you connect it, or from the RespectASO estimate.",
]


class TheRuleTest(SimpleTestCase):
    def test_a_promise_the_terms_take_back_is_found(self):
        for text in PROMISES:
            with self.subTest(text=text):
                self.assertTrue(overclaim_in(text))

    def test_what_we_can_stand_behind_passes(self):
        for text in STANDS_UP:
            with self.subTest(text=text):
                self.assertEqual(overclaim_in(text), "")


# ---- every template, script and string ------------------------------------------

class NoOverclaimTest(SimpleTestCase):
    maxDiff = None

    def test_no_template_overclaims(self):
        hits = {}
        for path in sources(".html"):
            if found := _overclaims(relative(path), template_texts(path.read_text(encoding="utf-8"))):
                hits[relative(path)] = found
        self.assertEqual(hits, {})

    def test_no_script_overclaims(self):
        hits = {}
        for path in sources(".js"):
            if found := _overclaims(relative(path), script_strings(path.read_text(encoding="utf-8"))):
                hits[relative(path)] = found
        self.assertEqual(hits, {})

    def test_no_string_in_the_code_overclaims(self):
        """Messages, labels, tooltips, notifications, the release notes (the
        What's New page shows the whole history) and the MCP tools' words."""
        hits = {}
        for path in sources(".py"):
            if relative(path) in _OWN or not _read_by_a_person(path):
                continue
            texts = python_strings(path.read_text(encoding="utf-8"), skip_logs=True, join=True)
            if found := _overclaims(relative(path), texts):
                hits[relative(path)] = found
        self.assertEqual(hits, {})

    def test_the_public_readme_overclaims_nothing(self):
        """The free edition's README is what GitHub shows a visitor
        (_public_files/README.md here, README.md in the free edition)."""
        root = Path(settings.BASE_DIR)
        readme = root / "_public_files" / "README.md"
        if not readme.is_file():
            readme = root / "README.md"
        prose = re.sub(r"```.*?```", "\n", readme.read_text(encoding="utf-8"), flags=re.DOTALL)
        self.assertEqual(_overclaims(relative(readme), prose.split("\n")), [])

    def test_every_allowance_is_still_needed(self):
        """An allowance whose words are gone from its file, or that excuses no
        shape, is a hole for the next one."""
        stale = []
        for where, words in ALLOWED:
            if not any(pattern.fullmatch(words) for pattern in OVERCLAIMS.values()):
                stale.append((where, words, "excuses no shape"))
                continue
            path = Path(settings.BASE_DIR) / where
            if where.startswith("aso_pro/") and not (Path(settings.BASE_DIR) / "aso_pro").is_dir():
                continue              # the free edition ships without aso_pro
            if not path.is_file() or words not in re.sub(r"\s+", " ", path.read_text(encoding="utf-8")).lower():
                stale.append((where, words, "no longer there"))
        self.assertEqual(stale, [])
