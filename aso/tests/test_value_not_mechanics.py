"""Screens show what a person needs for their app, never how the tool works, and help text stays short.

The owner's rules of 2026-10-01 (.github/instructions/user-facing-docs.instructions.md):

- A person reads what a number means for their app, what to do next, how
  long they will wait, what went wrong and how to fix it, and what they must
  know to trust the product (what leaves their Mac and where, that the
  numbers come from Apple). Never how often a keyword is read, that a read is
  reused or costs no App Store search, counts of AI requests, searches or
  calls, caching, pacing, throttling, deduplication or storage.
  ``aso.copy_rules.mechanics_in`` is the one list of those phrasings.
- Help text is as short as possible, because a long one is never read: an
  (i) tip or a tooltip stays within one short sentence (TIP_LIMIT), and the
  line under a page title or a card heading within one line (LEAD_LIMIT).

This module reads every template, script and Python string a person can
see, including the release notes, and fails on any new occurrence. The few
legitimate uses are listed in ALLOWED with the reason each one stays.
Plan and reasoning: docs/development/VALUE_NOT_MECHANICS_PLAN.md.
"""

import html
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from aso.copy_rules import MECHANICS, mechanics_in
from aso.tests.surfaces import (
    INFO_TIP,
    PROTECT,
    SCRIPT,
    python_strings,
    relative,
    script_strings,
    sources,
    template_texts,
)

# An (i) tip or a tooltip: one short sentence. The tooltip box is 18rem wide
# at 12px, about 40 characters a line, so 120 characters is three lines.
TIP_LIMIT = 120
# The line under a page title or a card heading: one line at the app's
# desktop width (a card's text-sm lead fits about 100 characters).
LEAD_LIMIT = 100

# Pages a person opens on purpose to read at length. Their sections'
# paragraphs are the body, not leads; their tips are still tips.
LONG_FORM = {
    "aso/templates/aso/methodology.html",
    "aso/templates/aso/setup.html",
    "aso/templates/aso/apple_ads_setup.html",
    "aso/templates/aso/whats_new.html",
}

# Modules whose strings no person reads on a screen: what the AI model or
# the AI assistant reads, and the command line tools of the people who
# build RespectASO.
NOT_ON_A_SCREEN = (
    "aso_pro/prompts.py",
    "aso_pro/schemas.py",
    "aso_pro/mcp/",
    "aso/management/",
    "aso_pro/management/",
    "scripts/",
    "core/settings.py",
    "_public_overrides/core/settings.py",
)

# (file, the words that matched): why they stay.
ALLOWED = {
    ("aso/templates/aso/setup.html", "cache"): "the command the reader types: docker compose build --no-cache",
    ("aso/release_notes.py", "cache"): "1.0.2 quotes the Docker command the reader types (build --no-cache)",
    ("aso/templates/aso/methodology.html", "43 searches"): "demand: a popularity of 43 does not mean 43 searches",
    ("aso/templates/aso/methodology.html", "app store searches"):
        "the size of the rank study behind where apps land, which is the scoring method",
    ("aso/models.py", "throttling"): "a throttle state's label that no screen shows; renaming it only makes a migration",
    ("aso_pro/keyword_pipeline.py", "quota"): "prompt text the AI model reads, never a screen",
}

_INLINE = re.compile(r"</?(?:strong|b|em|i|code|a|span|abbr|kbd|br)\b[^>]*>", re.IGNORECASE)


def _flat(text: str) -> str:
    """A piece of markup as a person reads it: inline tags gone, block tags
    a break no phrase is read across."""
    text = _INLINE.sub(" ", text)
    text = re.sub(r"<[^>]+>", " ¦ ", text)
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


def _mechanics(where: str, texts) -> list[str]:
    found = []
    for text in texts:
        flat = _flat(text)
        if " " not in flat:          # a key or a code, never a sentence
            continue
        for pattern in MECHANICS:
            for m in pattern.finditer(flat):
                if (where, m.group().lower()) not in ALLOWED:
                    found.append(f"{m.group()!r} in {flat[:120]!r}")
    return found


def _on_a_screen(path: Path) -> bool:
    rel = relative(path)
    return not rel.startswith(NOT_ON_A_SCREEN)


_BRANCH = re.compile(r"{%\s*(?:else|elif\b[^%]*|empty)\s*%}")


def _shown_length(text: str) -> int:
    """The longest a piece of template text can read: each ``{{ value }}``
    counts as one character, and of two branches only the longer counts."""
    longest = 0
    for branch in _BRANCH.split(text):
        shown = re.sub(r"{{.*?}}", "X", branch, flags=re.DOTALL)
        shown = re.sub(r"{%.*?%}|{#.*?#}", "", shown, flags=re.DOTALL)
        shown = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", shown))).strip()
        longest = max(longest, len(shown))
    return longest


_TOOLTIP_ATTR = re.compile(r'\b(?:data-tip|title)="([^"]*)"')
_LEAD = re.compile(
    r"</h[1-4]>\s*(?:{%\s*info_tip\s+\"[^\"]*\"\s*%}\s*)?(?:</div>\s*)?<p\b[^>]*>(.*?)</p>", re.DOTALL)


def _tips(src: str) -> list[str]:
    body = PROTECT.sub("\n", SCRIPT.sub("\n", src))
    return INFO_TIP.findall(body) + _TOOLTIP_ATTR.findall(body)


def _leads(src: str) -> list[str]:
    return _LEAD.findall(PROTECT.sub("\n", SCRIPT.sub("\n", src)))


# ---- the rule itself ----------------------------------------------------------

EXPLAINS_THE_TOOL = [
    # The owner's own example, the Rival Tracker setup's Keywords tip.
    ("They are Search History keywords of the app, so they also show on the Dashboard. "
     "A keyword already read that day costs no App Store search."),
    "About 4 AI requests and 35 to 40 searches in the App Store in Argentina, about 4 minutes.",
    "Each keyword is read from Apple once a day in each country.",
    "Both come from the same App Store search.",
    "Apple's App Store API is responding slowly. Pacing requests to avoid being rate-limited (current: 3s/keyword).",
    "Scoring keyword 4/20: habit tracker (cached)",
    "Research uses ~50–80 iTunes API calls and 3–7 LLM calls.",
    "One short request to your AI provider per country and week.",
    "Refreshing: every keyword, listing and rating is read again.",
    "Saved in your browser's localStorage.",
]

TELLS_THE_USER = [
    "They also show in this app's Search History on the Dashboard.",
    "Suggestions for the App Store in Argentina take about 4 minutes.",
    "Popularity 43 is about 110 searches a day in the United States.",
    "This storefront sees under one search a day for this keyword.",
    "Apple lists terms with roughly 500 or more searches a week.",
    "Research up to 1,000 keywords in one search.",
    "Apple's App Store is answering slowly, so this takes a little longer.",
    "Results stay on your Mac; the keywords go only to the AI provider you choose.",
]


class TheRuleTest(SimpleTestCase):
    def test_an_account_of_the_tools_internals_is_found(self):
        for text in EXPLAINS_THE_TOOL:
            with self.subTest(text=text):
                self.assertTrue(mechanics_in(text))

    def test_what_a_person_needs_passes(self):
        for text in TELLS_THE_USER:
            with self.subTest(text=text):
                self.assertEqual(mechanics_in(text), "")

    def test_the_guard_reads_the_old_tooltip_in_a_template(self):
        """The defect that started the rule, as the setup screen had it,
        fails the template scan."""
        old = ('<h2>Keywords</h2>\n{% info_tip "They are Search History keywords of the app, so they also '
               'show on the Dashboard. A keyword already read that day costs no App Store search." %}')
        found = _mechanics("aso_pro/templates/aso_pro/rival_tracker/setup.html", template_texts(old))
        self.assertTrue(any("already read" in hit for hit in found), found)
        self.assertGreater(max(_shown_length(t) for t in _tips(old)), TIP_LIMIT)


# ---- every surface --------------------------------------------------------------

class NoMechanicsTest(SimpleTestCase):
    maxDiff = None

    def test_no_template_explains_the_tool(self):
        hits = {}
        for path in sources(".html"):
            if found := _mechanics(relative(path), template_texts(path.read_text(encoding="utf-8"))):
                hits[relative(path)] = found
        self.assertEqual(hits, {})

    def test_no_script_explains_the_tool(self):
        hits = {}
        for path in sources(".js"):
            if found := _mechanics(relative(path), script_strings(path.read_text(encoding="utf-8"))):
                hits[relative(path)] = found
        self.assertEqual(hits, {})

    def test_no_string_in_the_code_explains_the_tool(self):
        """Messages, progress lines, labels, tooltips, notifications and the
        release notes (aso/release_notes.py: the What's New page shows the
        whole history). Log lines are read by the people who run the app."""
        hits = {}
        for path in sources(".py"):
            if not _on_a_screen(path):
                continue
            texts = python_strings(path.read_text(encoding="utf-8"), skip_logs=True, join=True)
            if found := _mechanics(relative(path), texts):
                hits[relative(path)] = found
        self.assertEqual(hits, {})

    def test_every_allowance_is_still_needed(self):
        """An allowance whose words are gone from its file is a hole for the
        next one; it goes when the text it covered goes."""
        stale = []
        for where, words in ALLOWED:
            path = Path(settings.BASE_DIR) / where
            if where.startswith("aso_pro/") and not (Path(settings.BASE_DIR) / "aso_pro").is_dir():
                continue              # the free edition ships without aso_pro
            if not path.is_file() or words not in re.sub(r"\s+", " ", path.read_text(encoding="utf-8")).lower():
                stale.append((where, words))
        self.assertEqual(stale, [])


class HelpTextIsShortTest(SimpleTestCase):
    maxDiff = None

    def test_every_tip_is_one_short_sentence(self):
        """Every (i) tip and every tooltip written in a template."""
        hits = {}
        for path in sources(".html"):
            long = [f"{_shown_length(t)}: {t[:90]}" for t in _tips(path.read_text(encoding="utf-8"))
                    if _shown_length(t) > TIP_LIMIT]
            if long:
                hits[relative(path)] = long
        self.assertEqual(hits, {})

    def test_every_column_tip_is_one_short_sentence(self):
        from aso import column_tips

        long = {f"{name}.{key}": len(text)
                for name in ("OPPORTUNITY_TIPS", "COLUMN_TIPS")
                for key, text in getattr(column_tips, name).items() if len(text) > TIP_LIMIT}
        self.assertEqual(long, {})

    def test_every_lead_is_one_line(self):
        """The paragraph right under a page title or a card heading, on
        every screen but the long-form pages."""
        hits = {}
        for path in sources(".html"):
            if relative(path) in LONG_FORM:
                continue
            long = [f"{_shown_length(t)}: {_flat(t)[:90]}" for t in _leads(path.read_text(encoding="utf-8"))
                    if _shown_length(t) > LEAD_LIMIT]
            if long:
                hits[relative(path)] = long
        self.assertEqual(hits, {})

    def test_the_long_form_pages_exist(self):
        for rel in LONG_FORM:
            with self.subTest(page=rel):
                if rel.startswith("aso_pro/") and not (Path(settings.BASE_DIR) / "aso_pro").is_dir():
                    continue          # the free edition ships without aso_pro
                self.assertTrue((Path(settings.BASE_DIR) / rel).is_file())
