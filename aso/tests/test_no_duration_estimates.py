"""RespectASO never says how long something will take.

The owner's rule (2026-11-18, .github/instructions/user-facing-docs.instructions.md),
after a Simulator run said "Takes a few minutes" and ran for more than twelve:
no estimate, remaining time or ETA, and no "quick", "fast", "soon" or "almost
done" about a run, on any screen, in any message, in the newest What's New
entry or in the MCP texts an AI assistant repeats to the person. A screen may
say the person can keep working while it runs and that it carries on by
itself, and may show the elapsed time and how long a finished run took.

``aso.copy_rules.duration_in`` is the one predicate; its ``DURATION_KEEPS``
names the kinds that are never an expectation (elapsed time, how long a
finished run took, retry advice after a refusal, the person's own deadline).
This module reads every template, script and Python string a person can see,
the newest release notes, the MCP server's instructions and every tool's
published description and parameters, and fails on any expectation. A
sentence that is right where it stands goes in ALLOWED with the reason; the
last test fails when an allowance stops matching.
"""

import json
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from aso.copy_rules import duration_in
from aso.tests.surfaces import (
    flat_text,
    on_a_screen,
    python_strings,
    relative,
    script_strings,
    sources,
    template_texts,
)

HAS_PRO = (Path(settings.BASE_DIR) / "aso_pro").is_dir()   # the free edition ships without aso_pro

# (file, the words that matched, lower case): why they stay.
ALLOWED = {
    ("aso/opportunity_scans.py", "x seconds"): "duration_text words only the cool-down the app itself sets",
    ("aso/opportunity_scans.py", "x minute"): "duration_text words only the cool-down the app itself sets",
    ("aso/opportunity_scans.py", "x hour"): "duration_text words only the cool-down the app itself sets",
}

# Old What's New entries are history and stay as they were written.
RELEASE_NOTES = "aso/release_notes.py"


def _found(where: str, texts) -> list[str]:
    hits = []
    for text in texts:
        flat = flat_text(text)
        if " " not in flat:          # a key or a code, never a sentence
            continue
        rest = flat
        while words := duration_in(rest):
            if (where, words.lower()) not in ALLOWED:
                hits.append(f"{words!r} in {flat[:140]!r}")
            rest = rest.replace(words, " ", 1)
    return hits


def _newest_release_texts():
    from aso.release_notes import RELEASES

    newest = RELEASES[0]
    yield newest["title"]
    for section in newest["sections"]:
        yield section["heading"]
        yield from section["intro"]
        yield from section["items"]


def _mcp_texts():
    """What an assistant reads: the server's instructions, and each tool's
    published description and parameter schema, as the client lists them."""
    from aso_pro.mcp import INSTRUCTIONS
    from aso_pro.mcp_catalog import register_all

    yield INSTRUCTIONS
    for tool in register_all().values():
        yield tool.description or ""
        for name, schema in (tool.parameters or {}).get("properties", {}).items():
            yield f"{name}: {json.dumps(schema)}"


# ---- the rule itself ----------------------------------------------------------

EXPECTATIONS = [
    "Takes a few minutes. You can keep working.",
    "It takes about 5 minutes; until then your scores keep the last Apple values.",
    "The research runs in the background (2-5 minutes).",
    "Wait 1-2 minutes, then call get_session_result.",
    "Scan 62 countries, about 3 minutes",
    "Not checked yet: the first check starts within the hour.",
    "12 of 50 keywords · about 3 minutes left",
    "Usually under a minute.",
    "This may take a moment...",
    "Score ONE keyword in ONE country. Fast (2-5s).",
    "Almost done",
    "Your draft is ready in 4 minutes.",
    "Results arrive shortly.",
]

NO_EXPECTATION = [
    "You can keep working while it runs.",
    "It runs in the background and carries on by itself.",
    "Checked 12 minutes ago",
    "Try again in a few minutes.",
    "Apple's App Store is still not answering. Wait a few minutes, then press Resume.",
    "Finished in 14 min",
    "12:17",
    "This step has run over 20 minutes: local generation is slow, but nothing is frozen.",
    "The model 'x' did not answer within your x-minute AI response deadline.",
    "Only one week of history so far. The chart appears once a second week is synced.",
    "How fast those apps gain new ratings.",
    "Each keyword of an AI run shows up as soon as it is scored.",
    "Keep waiting, or cancel and pick a faster model in Settings.",
]


class TheRuleTest(SimpleTestCase):
    def test_an_expectation_of_how_long_is_found(self):
        for text in EXPECTATIONS:
            with self.subTest(text=text):
                self.assertTrue(duration_in(text))

    def test_what_is_no_expectation_passes(self):
        for text in NO_EXPECTATION:
            with self.subTest(text=text):
                self.assertEqual(duration_in(text), "")

    def test_the_guard_reads_the_old_ai_tab_line_in_a_template(self):
        """The defect that started the rule, as the three AI tabs had it."""
        old = '<p class="text-sm text-slate-400">Takes a few minutes. You can keep working.</p>'
        self.assertTrue(_found("aso_pro/templates/aso_pro/simulator.html", template_texts(old)))


# ---- every surface --------------------------------------------------------------

class NoDurationEstimateTest(SimpleTestCase):
    maxDiff = None

    def test_no_template_says_how_long(self):
        """Every screen, the help pages (Methodology, setup guides) included."""
        hits = {}
        for path in sources(".html"):
            if found := _found(relative(path), template_texts(path.read_text(encoding="utf-8"))):
                hits[relative(path)] = found
        self.assertEqual(hits, {})

    def test_no_script_says_how_long(self):
        """Progress lines, notes, confirm dialogs and every other string a script draws."""
        hits = {}
        for path in sources(".js"):
            if found := _found(relative(path), script_strings(path.read_text(encoding="utf-8"))):
                hits[relative(path)] = found
        self.assertEqual(hits, {})

    def test_no_string_in_the_code_says_how_long(self):
        """Messages, progress lines, To do lines, notifications and errors.
        Log lines are read by the people who run the app."""
        hits = {}
        for path in sources(".py"):
            if not on_a_screen(path) or relative(path) == RELEASE_NOTES:
                continue
            texts = python_strings(path.read_text(encoding="utf-8"), skip_logs=True, join=True)
            if found := _found(relative(path), texts):
                hits[relative(path)] = found
        self.assertEqual(hits, {})

    def test_the_newest_whats_new_says_nothing_about_how_long(self):
        self.assertEqual(_found(RELEASE_NOTES, _newest_release_texts()), [])

    def test_no_mcp_text_says_how_long(self):
        """An assistant repeats these to the person."""
        if not HAS_PRO:
            self.skipTest("the free edition has no MCP server")
        hits = []
        for path in sources(".py"):
            if relative(path).startswith("aso_pro/mcp/"):
                texts = python_strings(path.read_text(encoding="utf-8"), skip_logs=True, join=True)
                hits += _found(relative(path), texts)
        hits += _found("aso_pro/mcp", _mcp_texts())
        self.assertEqual(hits, [])

    def test_every_allowance_is_still_needed(self):
        """An allowance whose words are gone from its file is a hole for the next one."""
        stale = []
        for where, words in ALLOWED:
            path = Path(settings.BASE_DIR) / where
            if where.startswith("aso_pro/") and not HAS_PRO:
                continue
            pattern = re.escape(words).replace("x", r"(?:x|\{[^}]*\})")
            if not path.is_file() or not re.search(pattern, re.sub(r"\s+", " ", path.read_text(encoding="utf-8")),
                                                    re.IGNORECASE):
                stale.append((where, words))
        self.assertEqual(stale, [])
