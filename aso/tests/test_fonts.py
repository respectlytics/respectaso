"""The app's one typeface, bundled with it as its authors ship it.

Schibsted Grotesk 400, 500 and 600 for everything, titles included, from
static/fonts/schibsted-grotesk/ as the authors' unmodified release 1.100 WOFF2
files with the SIL OFL beside them, @font-face with font-display: swap, one
preload, and only the weights the templates use. One typeface since
2026-10-02 ("follow our font everywhere so that it is consistent"), Schibsted
Grotesk since 2026-10-07, the owner's font for every Loheden website and this
app, so no serif is shipped or asked for (docs/development/
PRO_AND_CLICKABLE_TEXT_PLAN.md 6). The hashes are the release zip's, so a
replaced or edited font fails here. The Mac app bundles static/ whole and the free
edition copies it, so both carry the files and their licences.

Free-tier test: no aso_pro import; it runs in the public repository too.
"""

import hashlib
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from aso.tests.surfaces import sources
from aso.tests.test_no_browser_storage import app_files

BASE = Path(settings.BASE_DIR)
FONTS = BASE / "static" / "fonts"

# Schibsted Grotesk 1.100 (the release zip's fonts/webfonts/) and its licence.
SHIPPED = {
    "schibsted-grotesk/SchibstedGrotesk-Regular.woff2": "c38221f93223ee27c2db9617451505ac17ee2ce2b60a3e58731f03c1c4510372",
    "schibsted-grotesk/SchibstedGrotesk-Medium.woff2": "f3de15c1552d07f74087128c9bb070ad66974b1cbc97b12ebe1272aea765b8c5",
    "schibsted-grotesk/SchibstedGrotesk-SemiBold.woff2": "fbeb503f955ebf40860ed3d1766937d2b8222f31ec08b571b0186d80dfb1bf6e",
    "schibsted-grotesk/OFL.txt": "3b4f3063b6ac7c1e403e2c4a5e8ef3a58190ff83ed7b15af66511858699139ce",
}
FACES = (
    ("Schibsted Grotesk", 400, "../fonts/schibsted-grotesk/SchibstedGrotesk-Regular.woff2"),
    ("Schibsted Grotesk", 500, "../fonts/schibsted-grotesk/SchibstedGrotesk-Medium.woff2"),
    ("Schibsted Grotesk", 600, "../fonts/schibsted-grotesk/SchibstedGrotesk-SemiBold.woff2"),
)
PRELOADS = ("fonts/schibsted-grotesk/SchibstedGrotesk-Regular.woff2",)
PRELOAD = re.compile(r'<link rel="preload" href="([^"]+)" as="font" type="font/woff2" crossorigin>')
BASE_TEMPLATES = ("aso/templates/aso/base.html", "_public_overrides/aso/templates/aso/base.html")

_NOT_SHIPPED_CLASS = re.compile(r"(?<![\w-])(?:[a-z0-9]+:)*font-(?:thin|extralight|light|bold|extrabold|black)(?![\w-])")
_HEAVY_WEIGHT = re.compile(r"font-weight\s*[:=]\s*[\"']?\s*(?:[1-3]00|[7-9]00|bold(?:er)?|lighter)\b")
_PAGE_TITLE = re.compile(r'<h1\b[^>]*class="([^"]*\b(?:text-(?:xl|2xl|3xl)|page-title)\b[^"]*)"')
# A serif asked for: the class, or a font-family that names one (not sans-serif).
_SERIF = re.compile(r"(?<![\w-])font-serif(?![\w-])"
                    r"|font-family\s*:[^;{}]*(?:Source Serif|Georgia|Palatino|(?<![\w-])serif(?![\w-]))")


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


class TheFilesTest(SimpleTestCase):
    def test_the_shipped_files_are_the_release_files_with_their_licences(self):
        found = sorted(p.relative_to(FONTS).as_posix() for p in FONTS.rglob("*") if p.is_file())
        self.assertEqual(found, sorted(SHIPPED))
        for name, digest in SHIPPED.items():
            with self.subTest(name=name):
                self.assertEqual(hashlib.sha256((FONTS / name).read_bytes()).hexdigest(), digest)
        self.assertIn("SIL Open Font License", _text(FONTS / "schibsted-grotesk" / "OFL.txt"))

    def test_no_font_file_lives_anywhere_else_in_static(self):
        strays = [p.relative_to(BASE).as_posix() for p in (BASE / "static").rglob("*")
                  if p.suffix.lower() in {".woff2", ".woff", ".ttf", ".otf", ".eot"} and FONTS not in p.parents]
        self.assertEqual(strays, [])

    def test_the_mac_build_bundles_static_whole(self):
        self.assertIn('(str(BASE_DIR / "static"), "static")', _text(BASE / "desktop" / "RespectASO.spec"))


class TheStylesheetTest(SimpleTestCase):
    def test_the_source_declares_the_three_faces_and_no_other(self):
        css = _text(BASE / "static" / "css" / "tailwind.source.css")
        faces = re.findall(r"@font-face\s*{([^}]*)}", css)
        self.assertEqual(len(faces), len(FACES))
        for (family, weight, url), face in zip(FACES, faces, strict=True):
            with self.subTest(family=family, weight=weight):
                self.assertIn(f'font-family: "{family}"', face)
                self.assertIn(f"font-weight: {weight};", face)
                self.assertIn("font-display: swap;", face)
                self.assertIn(f'src: url("{url}") format("woff2")', face)
        self.assertIn("html { font-synthesis: style; }", css)       # never a faked bold
        self.assertIn("b, strong, th { font-weight: 600; }", css)

    def test_the_config_names_schibsted_grotesk_and_no_serif(self):
        config = _text(BASE / "tailwind.config.js")
        self.assertRegex(config, r"""sans: \['"Schibsted Grotesk"', "ui-sans-serif", "system-ui\"""")
        self.assertNotRegex(config, r"""\bserif: \[""")

    def test_the_committed_stylesheet_was_rebuilt_with_them(self):
        built = _text(BASE / "static" / "css" / "tailwind.css")
        for _family, _weight, url in FACES:
            self.assertIn(f"src:url({url}) format(\"woff2\")", built)
        self.assertRegex(built, r"html\{[^}]*font-family:Schibsted Grotesk,ui-sans-serif")
        self.assertNotIn(".font-serif", built)
        self.assertNotIn("Source Serif", built)


class TheHeadTest(TestCase):
    def test_every_base_template_preloads_the_one_file(self):
        present = [name for name in BASE_TEMPLATES if (BASE / name).is_file()]
        self.assertTrue(present)
        for name in present:
            with self.subTest(template=name):
                self.assertEqual(PRELOAD.findall(_text(BASE / name)),
                                 [f"{{% static '{f}' %}}" for f in PRELOADS])

    def test_a_rendered_page_preloads_them_from_this_server(self):
        page = self.client.get("/methodology/").content.decode()
        self.assertEqual(PRELOAD.findall(page), [f"/static/{f}" for f in PRELOADS])


class OnlyTheShippedWeightsTest(SimpleTestCase):
    def test_no_class_asks_for_a_weight_that_is_not_shipped(self):
        files = app_files() + list(sources(".py"))
        self.assertGreater(len(files), 40)
        found = [f"{p.relative_to(BASE)}: {m.group(0)}" for p in files
                 for m in _NOT_SHIPPED_CLASS.finditer(_text(p))]
        self.assertEqual(found, [])

    def test_no_style_asks_for_a_weight_that_is_not_shipped(self):
        """Every <style> block, inline style and script of the app, the
        standalone error page and the stylesheet source included; SVG text
        too."""
        files = app_files() + [BASE / "static" / "css" / "tailwind.source.css", BASE / "aso" / "templates" / "error_standalone.html"]
        found = [f"{p.relative_to(BASE)}: {m.group(0)}" for p in files for m in _HEAVY_WEIGHT.finditer(_text(p))]
        self.assertEqual(found, [])

    def test_every_page_title_is_the_page_title_role(self):
        titles = [(p, classes) for p in app_files() if p.suffix == ".html"
                  for classes in _PAGE_TITLE.findall(_text(p))]
        self.assertGreater(len(titles), 10)      # 15 in the free edition, 29 with Pro
        missing = [f"{p.relative_to(BASE)}: {c}" for p, c in titles if "page-title" not in c.split()]
        self.assertEqual(missing, [])
        built = (BASE / "static" / "css" / "tailwind.css").read_text()
        rule = re.search(r"\.page-title\{([^}]*)\}", built)
        self.assertIsNotNone(rule)
        self.assertIn("font-weight:600", rule.group(1))
        self.assertNotIn("font-family", rule.group(1))     # Schibsted Grotesk, from html

    def test_no_screen_asks_for_a_serif(self):
        """Templates, scripts, Python strings, the stylesheet source and the
        standalone error page: one typeface everywhere."""
        files = app_files() + list(sources(".py")) + [BASE / "static" / "css" / "tailwind.source.css",
                                                      BASE / "aso" / "templates" / "error_standalone.html"]
        found = [f"{p.relative_to(BASE)}: {m.group(0)}" for p in files
                 if not p.name.startswith("test_") for m in _SERIF.finditer(_text(p))]
        self.assertEqual(found, [])


# Schibsted Grotesk's tabular figures (the tnum feature) set the period and
# the comma at the width of a figure, so "1.5" read "1 . 5" and "1,234" read
# "1 , 234" on the Keywords page of 3.2.0 (owner, 2026-11-18). Numbers use the
# font's own figures; a numeric column that must line up is right-aligned.
_TABULAR = re.compile(r"tabular-nums|(?<![\w-])tnum(?![\w-])|font-variant-numeric")


class NumbersReadNormallyTest(SimpleTestCase):
    def test_nothing_asks_for_tabular_figures(self):
        """Templates, scripts, Python strings, the stylesheet source, the
        built stylesheet and the standalone error page."""
        files = (app_files() + [p for p in sources(".py") if not p.name.startswith("test_")]
                 + list(sources(".css")) + [BASE / "static" / "css" / "tailwind.css",
                                            BASE / "aso" / "templates" / "error_standalone.html"])
        self.assertIn(BASE / "static" / "css" / "tailwind.source.css", files)
        found = [f"{p.relative_to(BASE)}: {m.group(0)}" for p in files for m in _TABULAR.finditer(_text(p))]
        self.assertEqual(found, [])


# Monospace is for what a person copies or reads character by character, and
# for nothing else (owner, 2026-11-18): the AI tabs drew their score chips,
# counters and timers in the system monospace while the Keywords page drew the
# same scores in Schibsted Grotesk. Numbers, scores, chips, counters ("116
# keywords scored"), timers, percentages and labels use the product font; a
# numeric column that must line up is right-aligned or centred in a fixed width.
_MONO = re.compile(r"(?<![\w-])font-mono(?![\w-])")
# Elements that hold code or what a person types: monospace needs no reason there.
_MONO_TAGS = ("input", "textarea", "pre", "code")
# Every other use, file by file, with how many there are and what they show.
# The count is exact, so a new use has to be named here (and a removed one
# taken out). A file missing from this checkout (the free edition has no
# aso_pro/) is skipped.
MONO_ALLOWED = {
    "aso/templates/aso/ai_tool_preview.html": (1, "the sample keyword field, shown as the field"),
    "aso/templates/aso/apple_ads_setup.html": (3, "Apple's clientId, teamId and keyId, named as Apple shows them"),
    "aso/templates/aso/settings_popularity.html": (1, "the Apple Ads key ID"),
    "aso/templates/aso/setup.html": (8, "the data folder path, the localhost address, terminal commands and the hosts file"),
    "aso_pro/templates/aso_pro/_includes/suggested_metadata.html": (1, "the suggested keyword field (a comma list)"),
    "aso_pro/templates/aso_pro/settings_ai.html": (1, "an example model ID to type"),
    "aso_pro/templates/aso_pro/settings_asc.html": (6, "the issuer ID example, key IDs and key file names"),
    "aso_pro/templates/aso_pro/settings_license.html": (1, "the license ID"),
    "aso_pro/templates/aso_pro/simulator.html": (2, "the scored and the source keyword field (comma lists)"),
    "static/js/ai-tabs-shared.js": (1, "a failure's code location"),
    "static/css/tailwind.source.css": (1, "code inside AI prose"),
}
# What never takes monospace, wherever it is: an element coloured as a score,
# or whose content is a number, a percentage, a time or a value that names one.
_SCORE_COLOUR = re.compile(r"getScoreColor|getPopColor|popColor|diffColor|oppColor|rankColor|"
                           r"difficulty_chip|difficulty_color|opportunity_css|opportunity_color")
_NUMBER_TEXT = re.compile(r"^[\s#~]*[\d][\d.,:%+\-/\s]*$")
_NUMBER_VALUE = re.compile(r"(?i)(?:\{\{|'\s*\+)[^}<]*(?:popularity|difficulty|opportunity|rank|score|count|"
                           r"elapsed|pct|percent|range|total)")


def _mono_files():
    return (app_files() + [p for p in sources(".py") if not p.name.startswith("test_")]
            + [p for p in sources(".css") if p.name != "tailwind.css"])


def _mono_uses(text: str):
    """(tag name, the tag, the content after it up to the next tag) of each
    font-mono, the tag being the one whose class carries it."""
    for match in _MONO.finditer(text):
        start = text.rfind("<", 0, match.start())
        name = re.match(r"<\s*([a-zA-Z]+)", text[start:]) if start >= 0 else None
        end = text.find(">", match.end())
        content = text[end + 1:text.find("<", end + 1)] if end >= 0 else ""
        yield (name.group(1).lower() if name else ""), text[start:end + 1], content


class MonospaceOnlyForCharactersTest(SimpleTestCase):
    def test_monospace_only_where_people_copy_characters(self):
        found, counts = [], {}
        for path in _mono_files():
            rel = path.relative_to(BASE).as_posix()
            for name, _tag, _content in _mono_uses(_text(path)):
                if name not in _MONO_TAGS:
                    counts[rel] = counts.get(rel, 0) + 1
        for rel, count in sorted(counts.items()):
            allowed = MONO_ALLOWED.get(rel, (0, ""))[0]
            if count != allowed:
                found.append(f"{rel}: {count} font-mono outside input, textarea, pre and code, {allowed} allowed")
        for rel, (allowed, _why) in MONO_ALLOWED.items():
            if (BASE / rel).is_file() and rel not in counts:
                found.append(f"{rel}: listed in MONO_ALLOWED but uses no font-mono any more")
        self.assertEqual(found, [], "Numbers, scores, chips, counters and labels use the product font:\n"
                         + "\n".join(found))

    def test_no_score_chip_or_number_in_monospace(self):
        found = []
        for path in _mono_files():
            for _name, tag, content in _mono_uses(_text(path)):
                if (_SCORE_COLOUR.search(tag) or _NUMBER_TEXT.match(content)
                        or _NUMBER_VALUE.search(content)):
                    found.append(f"{path.relative_to(BASE)}: {tag[:120]} {content[:60]}")
        self.assertEqual(found, [])

    def test_the_guard_catches_a_monospace_score_chip(self):
        chip = ("'<span class=\"inline-block w-16 rounded px-1.5 py-0.5 text-xs font-mono ' + popColor + '\">'"
                " + pop + '</span>'")
        timer = '<span id="progress-elapsed" class="text-slate-300 font-mono">0:00</span>'
        counter = '<span class="font-mono">{{ scored_count }}</span>'
        for sample in (chip, timer, counter):
            (_name, tag, content), = _mono_uses(sample)
            self.assertTrue(_SCORE_COLOUR.search(tag) or _NUMBER_TEXT.match(content)
                            or _NUMBER_VALUE.search(content), sample)
