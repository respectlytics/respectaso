"""The same thing is said the same way everywhere
(docs/development/TRACKED_KEYWORDS_RENAME_PLAN.md).

The keyword table is Tracked Keywords and its page is Keywords. The old
names (Search History, the Dashboard, the Keyword Analysis tab, the
Countries tab's "Save ... to history" buttons) must not come back on a
screen, in a script's words or in what the MCP server tells an AI
assistant. Code comments, identifiers and past release notes may keep them.
"""

import re
from pathlib import Path

from django.apps import apps as django_apps
from django.conf import settings
from django.test import SimpleTestCase

BASE = Path(settings.BASE_DIR)

OLD_WORDS = (
    "Search History", "search history", "Back to Dashboard", "the Dashboard",
    "Keyword Analysis", "Save selected to history", "Save all to history",
)

TEMPLATE_ROOTS = ("aso/templates", "aso_pro/templates", "_public_overrides")
SCRIPT_ROOT = "static/js"

_DJANGO_COMMENT = re.compile(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}", re.DOTALL)
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_TAG_COMMENT = re.compile(r"{#.*?#}", re.DOTALL)
_JS_BLOCK_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_JS_LINE_COMMENT = re.compile(r"(?<![:\\'\"])//.*$", re.MULTILINE)


def _blank(match) -> str:
    """A removed comment keeps its line breaks, so line numbers stay true."""
    return "\n" * match.group(0).count("\n")


def strip_comments(text: str) -> str:
    for pattern in (_DJANGO_COMMENT, _HTML_COMMENT, _TAG_COMMENT, _JS_BLOCK_COMMENT):
        text = pattern.sub(_blank, text)
    return _JS_LINE_COMMENT.sub("", text)


def old_words_in(root: str, suffix: str) -> list[str]:
    found = []
    for path in sorted((BASE / root).rglob(f"*{suffix}")):
        if not path.is_file():
            continue
        for number, line in enumerate(strip_comments(path.read_text(encoding="utf-8")).splitlines(), 1):
            found += [f"{path.relative_to(BASE)}:{number}: {words!r}" for words in OLD_WORDS if words in line]
    return found


class ScreensSayTheNewWordsTest(SimpleTestCase):
    def test_no_template_says_the_old_words(self):
        found = []
        for root in TEMPLATE_ROOTS:
            found += old_words_in(root, ".html")
        self.assertEqual(found, [])

    def test_no_script_says_the_old_words(self):
        self.assertEqual(old_words_in(SCRIPT_ROOT, ".js"), [])

    def test_the_guard_finds_a_planted_word(self):
        """Proved once: an old word on a screen is caught, a comment is not."""
        planted = "<!-- Search History -->\n{# Search History #}\n<h2>Search History</h2>\n"
        stripped = strip_comments(planted)
        self.assertEqual(stripped.count("Search History"), 1)
        self.assertEqual(stripped.splitlines()[2], "<h2>Search History</h2>")
        script = "// the Dashboard\nvar a = 'Back to Dashboard';\n/* Keyword Analysis */\n"
        self.assertEqual(strip_comments(script).count("Dashboard"), 1)


class McpSaysTheNewWordsTest(SimpleTestCase):
    """What the MCP server publishes: every tool description and the server
    instructions an AI assistant reads before it calls a tool."""

    def test_no_description_says_the_old_words(self):
        if not django_apps.is_installed("aso_pro"):
            self.skipTest("the free edition has no MCP server")
        from aso_pro.mcp import INSTRUCTIONS
        from aso_pro.mcp_catalog import register_all

        texts = {"instructions": INSTRUCTIONS}
        texts.update({name: tool.description or "" for name, tool in register_all().items()})
        for name, text in texts.items():
            with self.subTest(tool=name):
                self.assertNotIn("search history", text.lower())
                self.assertNotIn("the dashboard", text.lower())
