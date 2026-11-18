"""Icons come from the product's own set, never emoji, except a country's
flag (the loheden-design skill; DAILY_USE_PLAN.md step 10.2).

Scans every template and every script for emoji: U+1F000 to U+1FAFF, the
star U+2B50, the warning sign U+26A0 and the emoji presentation selector
U+FE0F. A country's flag (a pair of regional indicators, U+1F1E6 to
U+1F1FF) stays. The classification icons that come from Python
(aso/scoring.py, aso/services.py) are a later release (D11).
"""

from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

ROOTS = (("aso/templates", "*.html"), ("aso_pro/templates", "*.html"), ("static/js", "*.js"))
SINGLE = {0x2B50, 0x26A0, 0xFE0F}


def is_emoji(char: str) -> bool:
    code = ord(char)
    if 0x1F1E6 <= code <= 0x1F1FF:     # half of a country's flag
        return False
    return 0x1F000 <= code <= 0x1FAFF or code in SINGLE


def emoji_in(text: str) -> list[str]:
    return sorted({char for char in text if is_emoji(char)})


class NoEmojiTest(SimpleTestCase):
    def test_no_template_or_script_draws_an_emoji(self):
        base = Path(settings.BASE_DIR)
        hits = []
        for root, pattern in ROOTS:
            for path in sorted((base / root).rglob(pattern)):
                if "vendor" in path.parts:
                    continue
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    found = emoji_in(line)
                    if found:
                        hits.append(f"{path.relative_to(base)}:{number}: {' '.join(found)}")
        self.assertEqual(hits, [], "Use the product's own icons or words:\n" + "\n".join(hits))

    def test_the_scan_finds_an_emoji_and_spares_a_flag(self):
        self.assertEqual(emoji_in("\U0001F4A1 Keywords from previous analysis"), ["\U0001F4A1"])
        self.assertEqual(emoji_in("⭐ 4.8"), ["⭐"])
        self.assertEqual(emoji_in("⚠ Check the key"), ["⚠"])
        self.assertEqual(emoji_in("\U0001F1FA\U0001F1F8 United States"), [])
        self.assertEqual(emoji_in("★ 4.8"), [])
