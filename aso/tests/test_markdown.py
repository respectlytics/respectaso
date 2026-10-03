"""Markdown is drawn by one renderer, static/js/markdown.js.

The AI tabs once carried three copies of a renderer that escaped nothing and
drew no tables, and both base templates a fourth for the update banner's
release notes. Now AsoMarkdown.toHtml() draws every piece of markdown the
app shows, in both editions, and this module runs it under node (skipped
without node, like test_duration_text.py) and fails on a second renderer
anywhere in the templates or scripts. docs/development/DEDUPE_RENDERERS_PLAN.md
"""

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BASE_DIR = Path(settings.BASE_DIR)
SCRIPT = BASE_DIR / "static/js/markdown.js"

TABLE = """| Keyword | Popularity | Difficulty | Why it fits |
|---|---:|---:|---|
| habit streaks | 38 | 41 | Few strong apps, clear intent |
| daily routine planner | 45 | 52 | Matches the subtitle you have |
| morning routine | 51 | 67 | Popular, but **crowded** at the top |"""

HOSTILE = [
    "<script>alert(1)</script>",
    "<img src=x onerror=alert(1)>",
    '"><svg onload=alert(1)>',
    "## <script>alert(1)</script>",
    "- <iframe src=javascript:alert(1)></iframe>",
    "| <b>a</b> | b |\n|---|---|\n| <script>x</script> | `<i>` |",
    "`</code><script>alert(1)</script>`",
    "```\n</pre><script>alert(1)</script>\n```",
    "> <a href=javascript:alert(1)>x</a>",
    "[x](javascript:alert(1)) ![y](https://example.com/a.png)",
    "**<u>bold</u>** *<s>em</s>* ~~<q>del</q>~~",
    "| a |\n|:-:|\n| \" onmouseover=alert(1) x=\" |",
    "1. <style>body{display:none}</style>\n   - <object data=x>",
    "\u0000<script>\u0000",
]

ALLOWED_TAGS = {
    "h2", "h3", "p", "br", "ul", "ol", "li", "strong", "em", "del", "code", "pre",
    "blockquote", "hr", "div", "table", "thead", "tbody", "tr", "th", "td",
}
ALLOWED_ATTRIBUTES = {
    ("div", "class"): re.compile(r"overflow-x-auto"),
    ("th", "style"): re.compile(r"text-align:(left|center|right)"),
    ("td", "style"): re.compile(r"text-align:(left|center|right)"),
    ("ol", "start"): re.compile(r"\d+"),
}


def render(*texts):
    """AsoMarkdown.toHtml() of each text, run by node."""
    node = shutil.which("node")
    if not node:
        raise RuntimeError("node is not installed")
    script = (
        "global.window = {};"
        f"require({json.dumps(str(SCRIPT))});"
        "const texts = JSON.parse(require('fs').readFileSync(0, 'utf8'));"
        "process.stdout.write(JSON.stringify(texts.map(t => window.AsoMarkdown.toHtml(t))));"
    )
    out = subprocess.run([node, "-e", script], input=json.dumps(list(texts)), capture_output=True,
                         text=True, check=True, timeout=30).stdout
    return json.loads(out)


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, attrs))

    def handle_startendtag(self, tag, attrs):
        self.tags.append((tag, attrs))


class MarkdownRendererTest(SimpleTestCase):
    def setUp(self):
        if not shutil.which("node"):
            self.skipTest("node is not installed")

    def one(self, text):
        return render(text)[0]

    def test_empty(self):
        self.assertEqual(render("", None), ["", ""])

    def test_headings(self):
        self.assertEqual(
            render("# A", "## A", "### B", "#### B", "###### B ##", "#nospace", "## C# tips"),
            ["<h2>A</h2>", "<h2>A</h2>", "<h3>B</h3>", "<h3>B</h3>", "<h3>B</h3>",
             "<p>#nospace</p>", "<h2>C# tips</h2>"],
        )
        # A heading is found at the start of a line only.
        self.assertEqual(self.one("Rank ## 3 today"), "<p>Rank ## 3 today</p>")

    def test_paragraphs_and_line_breaks(self):
        self.assertEqual(self.one("a\nb\n\nc"), "<p>a<br>b</p><p>c</p>")
        self.assertEqual(self.one("a\r\nb"), "<p>a<br>b</p>")
        self.assertEqual(self.one("## Title\nText under it"), "<h2>Title</h2><p>Text under it</p>")

    def test_lists(self):
        self.assertEqual(self.one("- a\n* b\n+ c"), "<ul><li>a</li><li>b</li><li>c</li></ul>")
        self.assertEqual(self.one("1. a\n2. b"), "<ol><li>a</li><li>b</li></ol>")
        self.assertEqual(self.one("3. a\n4) b"), '<ol start="3"><li>a</li><li>b</li></ol>')
        # A blank line between items keeps one list, as a model often writes it.
        self.assertEqual(self.one("- a\n\n- b"), "<ul><li>a</li><li>b</li></ul>")
        self.assertEqual(self.one("- a\n  - b\n  - c\n- d"),
                         "<ul><li>a<ul><li>b</li><li>c</li></ul></li><li>d</li></ul>")
        self.assertEqual(self.one("1. a\n   - b"), "<ol><li>a<ul><li>b</li></ul></li></ol>")
        self.assertEqual(self.one("- a\n  more"), "<ul><li>a<br>more</li></ul>")
        self.assertEqual(self.one("Intro:\n- a"), "<p>Intro:</p><ul><li>a</li></ul>")
        self.assertEqual(self.one("- a\nAfter"), "<ul><li>a</li></ul><p>After</p>")
        # Not a list: no space after the marker, or a negative number.
        self.assertEqual(self.one("-5% this week"), "<p>-5% this week</p>")

    def test_emphasis(self):
        self.assertEqual(
            self.one("**b** __b__ *i* _i_ ~~d~~"),
            "<p><strong>b</strong> <strong>b</strong> <em>i</em> <em>i</em> <del>d</del></p>",
        )
        self.assertEqual(self.one("***both***"), "<p><strong><em>both</em></strong></p>")
        self.assertEqual(self.one("snake_case_word and keyword_field"), "<p>snake_case_word and keyword_field</p>")
        self.assertEqual(self.one("2 * 3 * 4"), "<p>2 * 3 * 4</p>")
        self.assertEqual(self.one("**a\nb**"), "<p>**a<br>b**</p>")

    def test_code(self):
        self.assertEqual(self.one("Use `**not bold** <b>` here"),
                         "<p>Use <code>**not bold** &lt;b&gt;</code> here</p>")
        self.assertEqual(self.one("``a `b` c``"), "<p><code>a `b` c</code></p>")
        self.assertEqual(self.one("it`s"), "<p>it`s</p>")
        self.assertEqual(self.one("```python\n**x** <b>\n\n# not a heading\n```\nafter"),
                         "<pre><code>**x** &lt;b&gt;\n\n# not a heading</code></pre><p>after</p>")
        self.assertEqual(self.one("```\nnever closed"), "<pre><code>never closed</code></pre>")

    def test_links_stay_text(self):
        for text in ("[a](https://example.com)", "[a](javascript:alert(1))", "<https://example.com>"):
            with self.subTest(text=text):
                html = self.one(text)
                self.assertNotIn("<a", html)
                self.assertNotIn("href", html)
        self.assertEqual(self.one("[a](https://example.com)"), "<p>[a](https://example.com)</p>")

    def test_tables(self):
        html = self.one(f"Intro\n\n{TABLE}\n\nAfter")
        self.assertTrue(html.startswith('<p>Intro</p><div class="overflow-x-auto"><table><thead><tr>'), html)
        self.assertIn(
            '<th>Keyword</th><th style="text-align:right">Popularity</th>'
            '<th style="text-align:right">Difficulty</th><th>Why it fits</th>', html)
        self.assertEqual(html.count("<tr>"), 4)
        self.assertIn('<td>habit streaks</td><td style="text-align:right">38</td>', html)
        self.assertIn("<td>Popular, but <strong>crowded</strong> at the top</td>", html)
        self.assertTrue(html.endswith("</tbody></table></div><p>After</p>"), html)
        # Without outer pipes, centred, a short row padded, an escaped pipe kept.
        self.assertEqual(
            self.one("a | b\n:-: | --\nx \\| y | 1\nonly |"),
            '<div class="overflow-x-auto"><table><thead><tr><th style="text-align:center">a</th><th>b</th>'
            '</tr></thead><tbody><tr><td style="text-align:center">x | y</td><td>1</td></tr>'
            '<tr><td style="text-align:center">only</td><td></td></tr></tbody></table></div>',
        )
        # A header with no rows yet.
        self.assertEqual(self.one("| a |\n|---|"),
                         '<div class="overflow-x-auto"><table><thead><tr><th>a</th></tr></thead></table></div>')
        # Pipes without a delimiter row, or a delimiter row of another width, are text.
        self.assertEqual(self.one("a | b\nc | d"), "<p>a | b<br>c | d</p>")
        self.assertNotIn("<table", self.one("| a | b |\n|---|"))

    def test_rules_and_quotes(self):
        self.assertEqual(self.one("a\n\n---\n\nb"), "<p>a</p><hr><p>b</p>")
        self.assertEqual(self.one("> said\n> - item"), "<blockquote><p>said</p><ul><li>item</li></ul></blockquote>")
        self.assertNotIn("<blockquote><blockquote><blockquote><blockquote>", self.one(">>>>>> deep"))

    def test_escaping(self):
        cases = {
            "<script>alert(1)</script>": "<p>&lt;script&gt;alert(1)&lt;/script&gt;</p>",
            "<img src=x onerror=alert(1)>": "<p>&lt;img src=x onerror=alert(1)&gt;</p>",
            "\"quoted\" and 'single'": "<p>&quot;quoted&quot; and &#39;single&#39;</p>",
            "&amp; &lt;": "<p>&amp;amp; &amp;lt;</p>",
            "## <script>x</script>": "<h2>&lt;script&gt;x&lt;/script&gt;</h2>",
            "- <script>x</script>": "<ul><li>&lt;script&gt;x&lt;/script&gt;</li></ul>",
            "`<script>x</script>`": "<p><code>&lt;script&gt;x&lt;/script&gt;</code></p>",
            "| <script>x</script> |\n|---|\n| <img src=x onerror=y> |":
                '<div class="overflow-x-auto"><table><thead><tr><th>&lt;script&gt;x&lt;/script&gt;</th>'
                "</tr></thead><tbody><tr><td>&lt;img src=x onerror=y&gt;</td></tr></tbody></table></div>",
        }
        for text, html in cases.items():
            with self.subTest(text=text):
                self.assertEqual(self.one(text), html)

    def test_only_known_tags_and_attributes(self):
        for text, html in zip(HOSTILE, render(*HOSTILE)):
            with self.subTest(text=text):
                parser = _Tags()
                parser.feed(html)
                for tag, attrs in parser.tags:
                    self.assertIn(tag, ALLOWED_TAGS, html)
                    for name, value in attrs:
                        allowed = ALLOWED_ATTRIBUTES.get((tag, name))
                        self.assertIsNotNone(allowed, f"{tag} {name} in {html}")
                        self.assertTrue(allowed.fullmatch(value), html)

    def test_release_notes(self):
        """The update banner shows the newest release entry as GitHub's
        release body carries it."""
        from aso.management.commands.release_notes import latest_markdown

        text = latest_markdown()
        html = self.one(text)
        headings = re.findall(r"^## (.+)$", text, re.MULTILINE)
        self.assertTrue(headings)
        self.assertEqual(html.count("<h2>"), len(headings))
        self.assertNotIn("**", html)
        self.assertIn("<ul><li>", html)


class OneRendererTest(SimpleTestCase):
    """No template or script draws markdown by itself."""

    FOLDERS = ("aso", "aso_pro", "_public_overrides", "static/js")
    SECOND_RENDERER = re.compile(
        r"function\s+(markdownToHtml|formatReleaseNotes|formatInline)\b"
        r"|replace\(/\\\*\\\*|replace\(/###|replace\(/## |match\(/\^#\{"
    )

    def _files(self):
        for folder in self.FOLDERS:
            root = BASE_DIR / folder
            if not root.is_dir():
                continue  # the free edition has no aso_pro and no overrides
            for path in root.rglob("*"):
                if path.suffix in (".html", ".js") and path != SCRIPT:
                    yield path

    def test_no_second_renderer(self):
        found = []
        for path in self._files():
            for match in self.SECOND_RENDERER.finditer(path.read_text(encoding="utf-8")):
                found.append(f"{path.relative_to(BASE_DIR)}: {match.group(0)}")
        self.assertEqual(found, [], "Draw markdown with AsoMarkdown.toHtml() from static/js/markdown.js.")

    def test_every_base_template_loads_the_renderer(self):
        bases = [BASE_DIR / "aso/templates/aso/base.html", BASE_DIR / "_public_overrides/aso/templates/aso/base.html"]
        for base in bases:
            if base.exists():
                with self.subTest(base=str(base.relative_to(BASE_DIR))):
                    text = base.read_text(encoding="utf-8")
                    self.assertIn("{% static 'js/markdown.js' %}", text)
                    self.assertIn("AsoMarkdown.toHtml(data.release_notes)", text)

    def test_every_caller_extends_the_base(self):
        for path in self._files():
            text = path.read_text(encoding="utf-8")
            if path.suffix == ".html" and "AsoMarkdown.toHtml(" in text and path.name != "base.html":
                with self.subTest(path=str(path.relative_to(BASE_DIR))):
                    self.assertIn('{% extends "aso/base.html" %}', text)
