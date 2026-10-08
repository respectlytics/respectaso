"""Every heading takes one role of the text hierarchy
(docs/development/PRO_AND_CLICKABLE_TEXT_PLAN.md 4).

The owner found no hierarchy on any page (2026-10-02): a card's title, the
names of the things in it and its conclusion were all the same white bold,
and card headings came in five sizes. Now a heading is a page-title, a
card-title (serif, one step below the page), a section-label (small
capitals in grey) or an item-title (Inter semibold), each defined once in
static/css/tailwind.source.css, so no template sets a heading's size,
weight or colour by hand. The one exception is a notice's heading, red or
amber by its state, and the preview's benefit line, which is set large on
purpose.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BASE = Path(settings.BASE_DIR)
ROOTS = ("aso/templates", "aso_pro/templates", "_public_overrides/aso/templates", "static/js")
ROLES = {"page-title", "run-title", "card-title", "section-label", "item-title"}
HEADING = re.compile(r"<(h[1-4])\b([^>]*)>", re.DOTALL)
CLASS = re.compile(r'class=\\?"([^"\\]*)')
# Pages with their own inline design: the standalone error pages and the
# Mac app's loading page, which no stylesheet serves.
OWN_DESIGN = {"aso/templates/500.html", "aso/templates/error_standalone.html", "aso/templates/loading_standalone.html"}


def _headings():
    for root in ROOTS:
        for path in sorted((BASE / root).rglob("*")):
            if path.suffix not in (".html", ".js") or not path.is_file():
                continue
            rel = str(path.relative_to(BASE))
            if rel in OWN_DESIGN:
                continue
            text = path.read_text(encoding="utf-8")
            for match in HEADING.finditer(text):
                found = CLASS.search(match.group(2))
                yield rel, text.count("\n", 0, match.start()) + 1, match.group(1), match.group(2), (found.group(1) if found else "")


def _css_rule(name):
    css = (BASE / "static/css/tailwind.source.css").read_text(encoding="utf-8")
    rule = re.search(rf"\.{name} {{ @apply ([^;]*);", css)
    return set(rule.group(1).split()) if rule else set()


class EveryHeadingTakesARoleTest(SimpleTestCase):
    def test_every_heading_is_one_role(self):
        bad = []
        for rel, line, tag, attrs, cls in _headings():
            words = set(cls.split())
            if words & ROLES:
                continue
            if "data-preview-benefit" in attrs:
                continue
            if any(re.match(r"^text-(red|amber)-\d+$", w) for w in words):   # a notice, coloured by its state
                continue
            if not cls.strip() and tag == "h3":   # filled and styled by a script at run time
                continue
            bad.append(f"{rel}:{line}: <{tag} class=\"{' '.join(cls.split())[:70]}\">")
        self.assertEqual(bad, [])

    def test_a_role_is_never_resized_or_reweighted(self):
        """A role sets the size and the weight; a heading adds spacing or a
        state colour, never its own size or weight."""
        bad = []
        for rel, line, _tag, _attrs, cls in _headings():
            words = cls.split()
            if set(words) & ROLES and any(re.match(r"^(text-(2xs|xs|sm|base|lg|xl|2xl)|font-(semibold|medium|bold|normal))$", w)
                                           for w in words):
                bad.append(f"{rel}:{line}: {cls}")
        self.assertEqual(bad, [])

    def test_each_role_is_a_clear_step_from_the_next(self):
        """One typeface (the owner, 2026-10-02), so size, weight and colour
        make the steps, and the page title is the largest text on a page."""
        page, card = _css_rule("page-title"), _css_rule("card-title")
        label, item = _css_rule("section-label"), _css_rule("item-title")
        for rule in (page, card, label, item):
            self.assertNotIn("font-serif", rule)
        self.assertTrue({"text-xl", "font-semibold", "text-white"} <= page)
        self.assertTrue({"text-lg", "font-semibold", "text-white"} <= _css_rule("run-title"))
        self.assertTrue({"text-base", "font-semibold", "text-white"} <= card)
        self.assertTrue({"text-sm", "font-medium"} <= item)
        self.assertTrue({"text-2xs", "uppercase", "text-slate-400"} <= label)
        self.assertTrue(_css_rule("takeaway"))

    def test_nothing_on_a_page_is_set_larger_than_its_title(self):
        """The page title is text-xl: no template or script sets text-2xl or
        larger (the preview's benefit was text-2xl and outgrew the title)."""
        bad = []
        for root in ROOTS:
            for path in sorted((BASE / root).rglob("*")):
                if path.suffix not in (".html", ".js") or not path.is_file():
                    continue
                rel = str(path.relative_to(BASE))
                if rel in OWN_DESIGN:
                    continue
                text = path.read_text(encoding="utf-8")
                for match in re.finditer(r"(?<![\w-])text-(2xl|3xl|4xl|5xl)(?![\w-])", text):
                    line = text.count("\n", 0, match.start()) + 1
                    snippet = text.splitlines()[line - 1]
                    if "leading-none" in snippet and ("data-close" in snippet or "×" in snippet):
                        continue   # the × of a dialog, a glyph not text
                    if "📱" in snippet:
                        continue
                    bad.append(f"{rel}:{line}")
        self.assertEqual(bad, [])
