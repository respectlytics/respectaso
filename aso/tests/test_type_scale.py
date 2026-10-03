"""The type scale is set once and nothing uses an arbitrary font size
(docs/development/UI_FOUNDATIONS_PLAN.md, UI_REDESIGN_PLAN.md 12.1)."""

import re
from pathlib import Path

from django.test import SimpleTestCase

BASE = Path(__file__).resolve().parents[2]
ROOTS = ("aso", "aso_pro", "licensing", "llm_providers", "static/js", "_public_overrides")
ARBITRARY = re.compile(r"\btext-\[\d+(?:\.\d+)?(?:px|rem|em)\]")


def _files():
    for root in ROOTS:
        folder = BASE / root
        if not folder.exists():
            continue
        for path in folder.rglob("*"):
            text = str(path)
            if path.suffix in {".html", ".js", ".py"} and "/migrations/" not in text and "/tests/" not in text:
                yield path


class TypeScaleTest(SimpleTestCase):
    def test_no_arbitrary_font_size(self):
        found = []
        for path in _files():
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "<html" in line:
                    continue  # the root size, text-[14px], is the scale's anchor
                if ARBITRARY.search(line):
                    found.append(f"{path.relative_to(BASE)}:{number}: {line.strip()[:120]}")
        self.assertFalse(found, "Use text-2xs, text-xs, text-sm and the rest; never text-[Npx]:\n" + "\n".join(found))

    def test_the_scale_is_the_one_in_the_plan(self):
        config = (BASE / "tailwind.config.js").read_text()
        for size, value in (("2xs", "0.7857rem"), ("xs", "0.8571rem"), ("sm", "0.9286rem"),
                            ("base", "1rem"), ("lg", "1.1429rem"), ("xl", "1.4286rem"),
                            ("2xl", "1.7143rem")):
            self.assertRegex(config, rf'"?{re.escape(size)}"?: \["{re.escape(value)}"')

    def test_the_built_stylesheet_has_the_scale(self):
        built = (BASE / "static/css/tailwind.css").read_text().replace(" ", "")
        self.assertIn(".text-2xs{font-size:.7857rem", built)
        self.assertIn(".text-xs{font-size:.8571rem", built)
