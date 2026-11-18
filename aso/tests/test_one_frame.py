"""One content width across the whole app.

Until 2026-11-18 Apps and Discover used the full frame while the Store
section (Get found, Simulator, Custom product pages, Sales & growth,
Reviews), Settings and the help pages each sat in a narrower centred column
of their own, so the content edges jumped from section to section. The
loheden-design skill (Use the width): one frame per product, set once, and
every page shares its edges; running text keeps a readable line by sitting
beside something, never by centring a narrow column in empty space.

The frame is the ``frame`` component in static/css/tailwind.source.css. The
top bar, the notices under it and every page's <main> use it, and no page
narrows itself: a centred max-w container on a page is only allowed for a
state that stands alone in its room (an error, an empty page), named below.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

BASE = Path(settings.BASE_DIR)
ROOTS = ("aso/templates", "aso_pro/templates", "_public_overrides")
CSS = BASE / "static/css/tailwind.source.css"
# A centred, narrow block that is a state or a picture, not a page column (file: why).
CENTRED_ALLOWED = {
    "aso/templates/aso/partials/error_card.html": "an error stands alone, centred in its room",
    "aso/templates/aso/dashboard.html": "the first-run welcome card, centred in the empty page",
    "aso/templates/aso/store_preview.html": "the sample picture inside the preview card",
    "aso/templates/aso/partials/store_pages_picture.html": "a drawing keeps its own size",
    "aso_pro/templates/aso_pro/store/results.html": "the one line of an empty state",
    "aso_pro/templates/aso_pro/store/results_all.html": "the one line of an empty state",
    "aso_pro/templates/aso_pro/store/how.html": "the step's sentence under its drawing",
}
CENTRED = re.compile(r'class="[^"]*(?:\bmx-auto\b[^"]*\bmax-w-[\w\[\]]+|\bmax-w-[\w\[\]]+[^"]*\bmx-auto\b)[^"]*"')


def _templates():
    for root in ROOTS:
        for path in sorted((BASE / root).rglob("*.html")):
            yield str(path.relative_to(BASE)), path.read_text(encoding="utf-8")


class OneFrameTest(SimpleTestCase):
    def test_the_frame_is_defined_once(self):
        css = CSS.read_text(encoding="utf-8")
        self.assertEqual(len(re.findall(r"^\s*\.frame \{ @apply ", css, re.MULTILINE)), 1)
        self.assertIn("max-w-[1600px]", re.search(r"\.frame \{ @apply ([^;]*);", css).group(1))

    def test_the_shell_uses_the_frame(self):
        # The free edition's tree has its base template in place of the Pro one
        # and no _public_overrides folder, so a file absent here is not checked.
        for rel in ("aso/templates/aso/base.html", "_public_overrides/aso/templates/aso/base.html"):
            if not (BASE / rel).exists():
                continue
            with self.subTest(file=rel):
                self.assertIn('<main class="frame ', (BASE / rel).read_text(encoding="utf-8"))
        for rel in ("aso/templates/aso/partials/top_bar.html", "aso/templates/aso/partials/notices.html",
                    "aso/templates/aso/partials/popularity_banner.html"):
            with self.subTest(file=rel):
                self.assertIn('class="frame ', (BASE / rel).read_text(encoding="utf-8"))

    def test_no_template_sets_the_width_by_hand(self):
        bad = [rel for rel, text in _templates() if "1600px" in text]
        self.assertEqual(bad, [])

    def test_no_page_narrows_itself(self):
        bad = []
        for rel, text in _templates():
            if rel in CENTRED_ALLOWED or rel.endswith(("_standalone.html", "500.html")):
                continue
            for match in CENTRED.finditer(text):
                bad.append(f"{rel}:{text.count(chr(10), 0, match.start()) + 1}: {match.group(0)}")
        self.assertEqual(bad, [], "Every page shares the one frame; keep text readable by placing it "
                                  "beside something or with max-w-prose on the text block alone.")

    def test_every_allowed_centred_block_still_exists(self):
        texts = dict(_templates())
        dead = [rel for rel in CENTRED_ALLOWED if (BASE / rel).exists() and not CENTRED.search(texts.get(rel, ""))]
        self.assertEqual(dead, [])
