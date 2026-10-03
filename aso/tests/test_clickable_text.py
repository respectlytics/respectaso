"""Everything a reader can click looks clickable, in one colour
(docs/development/PRO_AND_CLICKABLE_TEXT_PLAN.md).

The owner could not tell "Show why" or "Which should I choose?" from ordinary
text (2026-10-02): links were purple on one page and sky on the next, and the
sections that open on a click were grey. Now every text link carries .link,
every text button .btn-quiet, every section that opens .disclosure, all three
in the same sky blue, and text that deletes or stops something .link-danger.
Anything else a person clicks is a button by its shape (a background or a
border), a menu item, a tab, or an icon with a label for screen readers.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BASE = Path(settings.BASE_DIR)
ROOTS = ("aso/templates", "aso_pro/templates", "_public_overrides/aso/templates", "static/js")

# Pages with their own design: the standalone error pages carry inline CSS so
# they render when the stylesheet is what broke, and the top bar is the
# navigation itself.
OWN_DESIGN = {
    "aso/templates/500.html",
    "aso/templates/error_standalone.html",
    "aso/templates/aso/partials/top_bar.html",
}

SHARED = re.compile(
    r"\b(link|link-danger|disclosure|btn-primary|btn-secondary|btn-quiet|btn-pro|btn-locked"
    r"|menu-item|section-tab|section-tab-active)\b"
)
# A button by its shape: a background, a border, a pill; a row of a list or
# menu (full width, lit on hover); a tab of the AI results.
BUTTON_SHAPE = re.compile(r"(^|\s)(bg-\S+|border|rounded-full|ai-tab-btn)(\s|$)")
ROW = re.compile(r"(^|\s)w-full(\s|$).*hover:bg-|hover:bg-.*(^|\s)w-full(\s|$)")
TAG = re.compile(r"<(a|button|summary)\b([^>]*)>", re.DOTALL)
CLASS = re.compile(r'class=\\?"([^"\\]*)')
TEMPLATE_CODE = re.compile(r"{%.*?%}|{{.*?}}", re.DOTALL)


def _sources():
    for root in ROOTS:
        for path in sorted((BASE / root).rglob("*")):
            if path.suffix in (".html", ".js") and "vendor" not in path.parts and path.is_file():
                yield path


def _clickables():
    """(file, line, tag, attributes, class) for every link, button and
    summary written in a template or a script."""
    for path in _sources():
        rel = str(path.relative_to(BASE))
        if rel in OWN_DESIGN:
            continue
        text = path.read_text(encoding="utf-8")
        for match in TAG.finditer(text):
            tag, attrs = match.group(1), match.group(2)
            found = CLASS.search(attrs)
            cls = found.group(1) if found else ""
            yield rel, text.count("\n", 0, match.start()) + 1, tag, attrs, cls


def _exempt(tag, attrs, cls):
    if "' +" in cls or '" +' in cls:          # a script picks the class at run time
        return True
    if cls.strip() == "hidden":                 # never shown, clicked by a script
        return True
    if "aria-label=" in attrs or "title=" in attrs:   # an icon with its name
        return tag != "summary"
    return "youtube" in attrs or "video_url" in attrs or "links.youtube" in attrs   # The Creator Behind, in YouTube red


class EveryClickableLooksClickableTest(SimpleTestCase):
    def test_every_section_that_opens_is_a_disclosure(self):
        bad = [f"{rel}:{line}" for rel, line, tag, _attrs, cls in _clickables()
               if tag == "summary" and "disclosure" not in cls.split()]
        self.assertEqual(bad, [])

    def test_every_link_and_button_uses_a_shared_style(self):
        bad = []
        for rel, line, tag, attrs, cls in _clickables():
            if tag == "summary" or _exempt(tag, attrs, cls):
                continue
            plain = TEMPLATE_CODE.sub(" ", cls)
            if SHARED.search(plain) or BUTTON_SHAPE.search(plain) or ROW.search(plain):
                continue
            bad.append(f"{rel}:{line}: {' '.join(plain.split())[:80]}")
        self.assertEqual(bad, [])

    def test_text_links_are_never_given_a_colour_of_their_own(self):
        """A link with .link takes its colour from it: a second text colour
        beside it is the drift this rule ends."""
        bad = []
        for rel, line, tag, _attrs, cls in _clickables():
            words = TEMPLATE_CODE.sub(" ", cls).split()
            if {"link", "disclosure", "btn-quiet"} & set(words):
                colours = [w for w in words if re.match(r"^(hover:)?text-(purple|sky|indigo|slate|white|amber)", w)]
                if colours:
                    bad.append(f"{rel}:{line}: {colours}")
        self.assertEqual(bad, [])

    def test_a_solid_purple_button_is_the_primary_button(self):
        """The main action looks the same everywhere: 27 buttons had copied
        its purple by hand in three sizes and two weights (2026-10-03)."""
        solid = re.compile(r"(^|\s)bg-purple-[5-7]00(\s|$)")
        bad = [f"{rel}:{line}" for rel, line, _tag, _attrs, cls in _clickables()
               if solid.search(TEMPLATE_CODE.sub(" ", cls)) and "btn-primary" not in cls.split()]
        self.assertEqual(bad, [])

    def test_a_solid_grey_button_is_the_secondary_button(self):
        """A solid grey fill that lights up on hover is the secondary button:
        Add manually and nine others had their own copies in four greys
        (2026-10-03). The toolbar's translucent buttons (Filters, Labels,
        Copy, Export) and the icon copy buttons are their own kind."""
        fill = re.compile(r"(^|\s)bg-slate-[5-8]00(\s|$)")
        hover = re.compile(r"(^|\s)hover:bg-slate-[5-8]00(\s|$)")
        bad = [f"{rel}:{line}" for rel, line, _tag, _attrs, cls in _clickables()
               if fill.search(plain := TEMPLATE_CODE.sub(" ", cls)) and hover.search(plain)
               and not any(word.startswith("btn-") for word in cls.split())]
        self.assertEqual(bad, [])

    def test_the_three_share_one_colour(self):
        css = (BASE / "static/css/tailwind.source.css").read_text(encoding="utf-8")
        for name in ("link", "disclosure", "btn-quiet"):
            with self.subTest(style=name):
                rule = re.search(rf"\.{name} {{ @apply ([^;]*);", css)
                self.assertIsNotNone(rule)
                self.assertIn("text-sky-400", rule.group(1).split())
                self.assertIn("hover:text-sky-300", rule.group(1).split())

    def test_the_guard_catches_a_grey_link(self):
        """Proved once: a link with its own grey colour fails."""
        attrs = ' href="/" class="text-slate-400 hover:text-white"'
        cls = CLASS.search(attrs).group(1)
        self.assertFalse(_exempt("a", attrs, cls))
        self.assertIsNone(SHARED.search(cls))
        self.assertIsNone(BUTTON_SHAPE.search(cls))


class DisclosureChevronsTest(SimpleTestCase):
    def test_a_chevron_in_a_disclosure_takes_its_colour(self):
        """A card's heading stays white; its chevron carries the click colour."""
        block = re.compile(r"<summary\b.*?</summary>", re.DOTALL)
        bad = []
        for path in _sources():
            text = path.read_text(encoding="utf-8")
            for match in block.finditer(text):
                for svg in re.findall(r"<svg\b[^>]*>", match.group(0)):
                    if re.search(r"\btext-slate-\d+", svg):
                        bad.append(f"{path.relative_to(BASE)}:{text.count(chr(10), 0, match.start()) + 1}")
        self.assertEqual(bad, [])


class EveryMenuRowHasItsIconTest(SimpleTestCase):
    """The Help menu and the ⋯ menus draw an icon on every row, from the one
    set in aso_tags.MENU_ICONS (the owner, 2026-10-02), so no menu mixes rows
    with and without one."""

    ROW = re.compile(r'<(a|button)\b[^>]*class="menu-item[^"]*"[^>]*>(.*?)</\1>', re.DOTALL)

    def test_every_menu_row_starts_with_an_icon(self):
        bad = []
        for path in _sources():
            if path.suffix != ".html":
                continue
            text = path.read_text(encoding="utf-8")
            for match in self.ROW.finditer(text):
                if not match.group(2).lstrip().startswith("{% menu_icon"):
                    bad.append(f"{path.relative_to(BASE)}:{text.count(chr(10), 0, match.start()) + 1}")
        self.assertEqual(bad, [])

    def test_the_icons_render(self):
        from aso.templatetags.aso_tags import MENU_ICONS, menu_icon

        for name in MENU_ICONS:
            with self.subTest(icon=name):
                svg = str(menu_icon(name))
                self.assertTrue(svg.startswith("<svg class=\"menu-icon"))
                self.assertIn('aria-hidden="true"', svg)
