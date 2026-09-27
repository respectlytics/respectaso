"""No dash stands between words in anything RespectASO writes for a person.

The owner's rule (2026-09-15): not an em dash, not an en dash, not a hyphen
doing a dash's job, anywhere a person reads. aso/copy_rules.py holds the one
predicate that both fixes and checks; this module is the one place that
drives every writer with it and reads back what the writer hands over:

- the rule itself, and the dashes it must leave alone;
- the difficulty breakdown's sentences, new and stored;
- the release notes (What's New, the GitHub release body, the update dialog);
- every page the app renders, licensed and not, with data on the Dashboard;
- every template and script, including the branches no fixture renders.

The Pro writers (the AI's answers, the results, the exports, MCP) are driven
in aso_pro/tests/test_no_dashes_pro.py, since this repository's free edition
ships without aso_pro.
"""

import html
import io
import json
import re
import tokenize
from pathlib import Path
from unittest.mock import MagicMock, patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import URLPattern, URLResolver, get_resolver

from aso.copy_rules import (
    dash_punctuation_in,
    no_dash_in_markup,
    no_dash_in_name,
    no_dash_punctuation,
)

# ---- the rule itself ------------------------------------------------------

GOES = [
    # (what was written, what the reader gets)
    ("not a feature—it is the foundation", "not a feature, it is the foundation"),
    ("Low competition — worth a try", "Low competition, worth a try"),
    ("Every app here has 1K+ ratings - no easy targets.", "Every app here has 1K+ ratings, no easy targets."),
    ("Top 5 – the most visible", "Top 5, the most visible"),
    ("positions 1-5 (Top 5) — the most visible", "positions 1-5 (Top 5), the most visible"),
    ("The rule — which nobody reads — is old.", "The rule, which nobody reads, is old."),
    ("Question: — what now?", "Question: what now?"),
    ("End of the line —", "End of the line"),
    ("**Title** — carries the most weight", "**Title**: carries the most weight"),
    ('"Savings" — each one counts', '"Savings", each one counts'),
    ("The title “Pausely - Digital Wellbeing” wastes space.", "The title “Pausely: Digital Wellbeing” wastes space."),
    ("We test -- two dashes -- here.", "We test, two dashes, here."),
]

STAYS = [
    "long-tail keywords, Covid-19 and B2B-apps",       # a hyphen inside a word
    "0.9-3.8/day in 2025-2026",                        # a number range
    "Opportunity fell -12 points",                     # the minus sign on a number
    "pre- and post-launch",                            # the hanging hyphen of a compound
    "- first point\n- second point",                   # the list marker
    "| keyword | — |",                                 # the empty cell of a table
    'a rank of "—" means not ranked',                  # the glyph named in quotes
    "run docker compose up -d --build",                # a command's options
    'order_by("-created_at")',                         # code
    "step one -> step two",                            # an arrow
    "---",                                             # a Markdown rule
    "| --- | --- |",                                   # a Markdown table rule
    "https://respectaso.com/docs/ai-features/",
]


class TheRuleTest(TestCase):
    def test_a_dash_between_words_is_replaced(self):
        for wrote, reads in GOES:
            with self.subTest(wrote=wrote):
                self.assertEqual(no_dash_punctuation(wrote), reads)
                self.assertTrue(dash_punctuation_in(wrote))
                self.assertEqual(dash_punctuation_in(reads), "")

    def test_the_dashes_that_are_not_punctuation_stay(self):
        for text in STAYS:
            with self.subTest(text=text):
                self.assertEqual(no_dash_punctuation(text), text)
                self.assertEqual(dash_punctuation_in(text), "")

    def test_the_fix_holds_on_its_own_output(self):
        for wrote, _reads in GOES:
            once = no_dash_punctuation(wrote)
            self.assertEqual(no_dash_punctuation(once), once)

    def test_a_title_separates_the_name_with_a_colon(self):
        self.assertEqual(no_dash_in_name("Pausely - Digital Wellbeing"), "Pausely: Digital Wellbeing")
        self.assertEqual(no_dash_in_name("Pausely — Screen Time"), "Pausely: Screen Time")
        self.assertEqual(no_dash_in_name("Long-Tail Keyword Finder"), "Long-Tail Keyword Finder")
        self.assertLessEqual(len(no_dash_in_name("Pausely - Focus")), len("Pausely - Focus"))

    def test_markup_keeps_its_tags_and_code(self):
        self.assertEqual(no_dash_in_markup("<strong>Faster</strong> — searches finish sooner"),
                         "<strong>Faster</strong>: searches finish sooner")
        self.assertEqual(no_dash_in_markup('Run <code>up -d --build</code>, then <a href="/a-b">open it</a> — done'),
                         'Run <code>up -d --build</code>, then <a href="/a-b">open it</a>, done')


# ---- the difficulty breakdown ----------------------------------------------

def _prose_dashes(obj, names=("weakest_app", "brand_name")):
    """Every dash standing between words in ``obj``'s strings, skipping the
    fields that hold an App Store name and the names quoted in brackets."""
    found = []

    def walk(value, key=None):
        if isinstance(value, str):
            hit = dash_punctuation_in(re.sub(r"\([^()]*\)", "()", value))
            if hit and key not in names:
                found.append(f"{key}: {hit}")
        elif isinstance(value, dict):
            for k, v in value.items():
                walk(v, k)
        elif isinstance(value, list):
            for v in value:
                walk(v, key)

    walk(obj)
    return found


class DifficultyBreakdownTest(TestCase):
    def test_new_breakdowns_carry_no_dash(self):
        from aso.services import DifficultyCalculator
        from aso.tests.difficulty_fixture import FIELDS, build

        calc = DifficultyCalculator()
        for keyword, apps, publishers in FIELDS:
            for n in (len(apps), 1, 3):      # full fields and the thin ones with open spots
                _score, breakdown = calc.calculate(build(keyword, apps[:n], publishers), keyword=keyword)
                self.assertEqual(_prose_dashes(breakdown), [], keyword)

    def test_stored_breakdowns_are_rewritten_once_and_names_stay(self):
        from aso.models import App, Keyword, SearchResult
        from aso.popularity import recalculate_stored_difficulty
        from aso.tests.difficulty_fixture import FIELDS, build

        keyword, apps, publishers = FIELDS[0]
        competitors = build(keyword, apps, publishers)
        competitors[0]["trackName"] = "Focus Timer - Pomodoro"
        app = App.objects.create(name="Pausely", track_id=1)
        kw = Keyword.objects.create(keyword=keyword, app=app)
        row = SearchResult.objects.create(
            keyword=kw, country="us", popularity_score=50, difficulty_score=40,
            competitors_data=competitors,
            difficulty_breakdown={"insights": [{"type": "info", "icon": "i",
                                                "text": "Every app here has 1K+ ratings - no easy targets."}]},
        )
        recalculate_stored_difficulty()
        row.refresh_from_db()
        self.assertEqual(_prose_dashes(row.difficulty_breakdown), [])

    def test_an_ai_runs_snapshot_is_read_through_the_rule(self):
        from aso.services import readable_breakdown

        stored = {"insights": [{"text": "The #1 app (Focus Timer - Pomodoro) has only 12 ratings - beatable."}],
                  "ranking_tiers": {"top_5": {"weakest_app": "Focus Timer - Pomodoro",
                                              "highlights": ["Only 3 apps rank here - 2 open spots."]}}}
        read = readable_breakdown(stored)
        self.assertEqual(read["insights"][0]["text"],
                         "The #1 app (Focus Timer - Pomodoro) has only 12 ratings, beatable.")
        self.assertEqual(read["ranking_tiers"]["top_5"]["weakest_app"], "Focus Timer - Pomodoro")
        self.assertEqual(read["ranking_tiers"]["top_5"]["highlights"], ["Only 3 apps rank here, 2 open spots."])


# ---- the release notes ------------------------------------------------------

class ReleaseNotesTest(TestCase):
    """The notes are the release history and are never rewritten; everything
    that shows them reads them through the rule."""

    def test_whats_new_page(self):
        text = _visible_text(self.client.get("/whats-new/").content.decode())
        self.assertEqual(_line_dashes(text), [])

    def test_github_release_body(self):
        from aso.management.commands.release_notes import latest_markdown

        self.assertEqual(_line_dashes(latest_markdown()), [])

    @override_settings(VERSION="2.0.0", IS_NATIVE_APP=True)
    def test_update_dialog(self):
        from aso import update_check

        payload = {"tag_name": "v2.1.0", "html_url": "https://github.com/x", "assets": [],
                   "body": "## What's New\n- **Faster** — searches finish sooner\n- Fixed a bug - finally"}
        response = MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(payload).encode()
        with patch.object(update_check, "_last_result", None), \
                patch.object(update_check, "_last_attempt", None), \
                patch("aso.update_check.urllib.request.urlopen", return_value=response):
            notes = update_check.check_for_update()["release_notes"]
        self.assertEqual(notes, "## What's New\n- **Faster**: searches finish sooner\n- Fixed a bug, finally")


# ---- every rendered page ----------------------------------------------------

def _visible_text(markup: str) -> str:
    """What a person reads on a page: its text and the attributes a browser
    shows (tooltips, placeholders, labels), without scripts and styles."""
    body = re.sub(r"<script\b.*?</script>|<style\b.*?</style>|<!--.*?-->|<code\b.*?</code>|<pre\b.*?</pre>",
                  "\n", markup, flags=re.S)
    attrs = re.findall(r'(?:title|placeholder|aria-label|alt|data-tip|content)="([^"]*)"', body)
    return "\n".join([html.unescape(t) for t in re.sub(r"<[^>]+>", "\n", body).split("\n")]
                     + [html.unescape(a) for a in attrs])


def _line_dashes(text: str) -> list[str]:
    return [hit for line in text.split("\n") if (hit := dash_punctuation_in(line.strip()))]


def _pages():
    """Every page the app serves at a fixed address."""
    def walk(patterns, prefix=""):
        for p in patterns:
            if isinstance(p, URLResolver):
                yield from walk(p.url_patterns, prefix + str(p.pattern))
            elif isinstance(p, URLPattern):
                yield prefix + str(p.pattern)

    for route in walk(get_resolver().url_patterns):
        if "<" in route or "(?P" in route or route.startswith(("admin", "static", "media", "^")):
            continue
        yield "/" + route


class RenderedPagesTest(TestCase):
    maxDiff = None
    def setUp(self):
        from aso.models import App, Keyword, SearchResult
        from aso.services import DifficultyCalculator
        from aso.tests.difficulty_fixture import FIELDS, build

        self.app = App.objects.create(name="Pausely: Reduce Screen Time", track_id=6751782511)
        calc = DifficultyCalculator()
        for keyword, apps, publishers in FIELDS[:6]:
            competitors = build(keyword, apps, publishers)
            difficulty, breakdown = calc.calculate(competitors, keyword=keyword)
            kw = Keyword.objects.create(keyword=keyword, app=self.app)
            SearchResult.objects.create(keyword=kw, country="us", popularity_score=45,
                                        difficulty_score=difficulty, difficulty_breakdown=breakdown,
                                        competitors_data=competitors, app_rank=12)

    @patch("aso.update_check._fetch_latest_release", return_value={"update_available": False})
    def _scan(self, _github):
        hits = {}
        urls = list(_pages()) + [f"/?app={self.app.pk}", f"/?app={self.app.pk}&country=us"]
        for url in urls:
            response = self.client.get(url)
            if response.status_code != 200 or "text/html" not in response.get("Content-Type", ""):
                continue
            found = _line_dashes(_visible_text(response.content.decode()))
            if found:
                hits[url] = found
        return hits

    def test_no_page_carries_a_dash(self):
        self.assertEqual(self._scan(), {})

    @override_settings(DEBUG_SKIP_LICENSE=True)
    def test_no_page_carries_a_dash_with_a_licence(self):
        self.assertEqual(self._scan(), {})


# ---- every template, script and string, every branch ------------------------

_SKIP_DIRS = {".venv", "venv", "node_modules", "staticfiles", "data", ".git", "docs", "dist", "build",
              "tests", "migrations", "vendor", "__pycache__"}
_PROTECT = re.compile(
    r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}|<!--.*?-->|<style\b.*?</style>"
    r"|<code\b[^>]*>.*?</code>|<pre\b.*?</pre>|{#.*?#}", re.S)
_SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script>", re.S)
_JS_STRING = re.compile(r"""'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`""", re.S)
_TEMPLATE_EXPR = re.compile(r"\$\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}")
_HUMAN_ATTR = re.compile(r'(?:title|placeholder|aria-label|alt|data-tip|content)="([^"]*)"')
# A dash opening or closing a piece of text, with a word on its other side:
# "</span> — text", or a string ending "… —" before the next piece.
_EDGE_DASH = re.compile(r"^\s*[—–]\s+\w|\w\s+[—–]\s*$")


def _sources(suffix):
    """This repository's own files of one kind: no dependencies, build
    output, tests or migrations."""
    root = Path(settings.BASE_DIR)
    for path in sorted(root.rglob(f"*{suffix}")):
        if not _SKIP_DIRS & set(path.relative_to(root).parts):
            yield path


def _text_dashes(text: str) -> list[str]:
    """The dashes standing between words in a piece of text, which may
    carry markup: each run of text between two tags is judged on its own,
    so the lone empty-cell glyph "<span>—</span>" stays."""
    found = []
    for node in re.split(r"<[^>]+>", text):
        hit = dash_punctuation_in(node) or (_EDGE_DASH.search(node) and node.strip())
        if hit:
            found.append(hit)
    return found


def _script_dashes(code: str) -> list[str]:
    found = []
    for line in code.split("\n"):
        if line.lstrip().startswith(("//", "*", "/*")):
            continue
        for literal in _JS_STRING.findall(re.split(r"(?<![:\\'\"])//\s", line)[0]):
            found += _text_dashes(_TEMPLATE_EXPR.sub(" x ", literal[1:-1]))
    return found


def _python_dashes(src: str) -> list[str]:
    """The dashes in a module's strings. Docstrings document code for the
    people who change it, and a raw string is a pattern, so both are left
    out; an f-string is judged whole, each placeholder standing as a word."""
    found = []
    tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    starts = (tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING)
    fstring_start = getattr(tokenize, "FSTRING_START", None)
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok.type == tokenize.STRING:
            docstring = (i == 0 or tokens[i - 1].type in starts) and tokens[i + 1].type == tokenize.NEWLINE
            m = re.match(r"^([rbuRBU]*)('\'\'|\"\"\"|'|\")(.*)\2$", tok.string, re.S)
            if m and not docstring and "r" not in m.group(1).lower():
                found += _text_dashes(m.group(3))
        elif tok.type == fstring_start:
            text, depth = "", 1
            while depth:
                i += 1
                kind = tokens[i].type
                if kind == fstring_start:
                    depth += 1
                elif kind == tokenize.FSTRING_END:
                    depth -= 1
                elif kind == tokenize.FSTRING_MIDDLE and depth == 1:
                    text += tokens[i].string
                elif kind == tokenize.OP and tokens[i].string == "{" and depth == 1:
                    text += "x"
            if "r" not in tok.string.lower():
                found += _text_dashes(text)
        i += 1
    return found


class SourcesTest(TestCase):
    maxDiff = None

    def test_no_template_carries_a_dash(self):
        hits = {}
        for path in _sources(".html"):
            src = path.read_text(encoding="utf-8")
            found = []
            for script in _SCRIPT.findall(src):
                found += _script_dashes(script)
            body = _PROTECT.sub("\n", _SCRIPT.sub("\n", src))
            for value in _HUMAN_ATTR.findall(body):
                found += _text_dashes(re.sub(r"{%.*?%}|{{.*?}}", "", value))
            for node in re.split(r"{%.*?%}", body):
                found += _text_dashes(re.sub(r"{{.*?}}", "x", node))
            if found:
                hits[str(path.relative_to(settings.BASE_DIR))] = found
        self.assertEqual(hits, {})

    def test_no_script_carries_a_dash(self):
        hits = {}
        for path in _sources(".js"):
            if found := _script_dashes(path.read_text(encoding="utf-8")):
                hits[str(path.relative_to(settings.BASE_DIR))] = found
        self.assertEqual(hits, {})

    def test_no_string_in_the_code_carries_a_dash(self):
        """Messages, labels, prompts and tooltips written in Python. The
        release history (aso/release_notes.py) is never rewritten; the pages
        that show it read it through the rule (ReleaseNotesTest)."""
        hits = {}
        for path in _sources(".py"):
            if path.name == "release_notes.py" and path.parent.name == "aso":
                continue
            if found := _python_dashes(path.read_text(encoding="utf-8")):
                hits[str(path.relative_to(settings.BASE_DIR))] = found
        self.assertEqual(hits, {})
