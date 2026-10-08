"""Every image the app draws is sized to its slot and comes in AVIF and WebP.

{% picture name size %} (aso_tags) draws static/images/<name>-<px> at 1x and
2x of its slot, AVIF first, WebP second, the PNG as the fallback, all made by
scripts/build_images.py from one master. A 512 px logo in a 32 px slot was
329 KB on every page until 2026-10-07; drawing an image any other way, or a
variant that is missing or not the size its slot asks for, fails here.

Free-tier test: no aso_pro import; it runs in the public repository too.
aso_pro/tests/test_image_masters.py checks the masters, which stay in Pro.
"""

import re
import struct
from pathlib import Path

from django.conf import settings
from django.template import Context, Template
from django.test import SimpleTestCase

from aso.tests.test_no_browser_storage import app_files

BASE = Path(settings.BASE_DIR)
IMAGES = BASE / "static" / "images"
PICTURE = re.compile(r'{%\s*picture\s+"([\w-]+)"\s+(\d+)')
BARE_IMG = re.compile(r"<img\b[^>]*{%\s*static\s+['\"]images/")
BUDGET = 120 * 1024  # bytes, the most any one variant may weigh


def pictures():
    """(name, CSS px) of every {% picture %} in the templates."""
    return sorted({(m[1], int(m[2])) for path in app_files() for m in PICTURE.finditer(path.read_text(encoding="utf-8"))})


def png_size(path: Path) -> tuple[int, int]:
    return struct.unpack(">II", path.read_bytes()[16:24])


class ImagesTest(SimpleTestCase):
    def test_the_templates_draw_the_two_logos(self):
        self.assertEqual(pictures(), [("respectaso-logo", 32), ("respectlytics-mark", 16)])

    def test_every_variant_exists_at_its_slot_size_within_the_budget(self):
        for name, size in pictures():
            for px in (size, size * 2):
                for ext in ("avif", "webp", "png"):
                    path = IMAGES / f"{name}-{px}.{ext}"
                    with self.subTest(path=path.name):
                        self.assertTrue(path.is_file())
                        self.assertLessEqual(path.stat().st_size, BUDGET)
                with self.subTest(png=f"{name}-{px}"):
                    self.assertEqual(png_size(IMAGES / f"{name}-{px}.png"), (px, px))

    def test_no_template_draws_an_image_without_picture(self):
        found = [f"{path.relative_to(BASE)}: {m[0]}" for path in app_files()
                 for m in BARE_IMG.finditer(path.read_text(encoding="utf-8"))]
        self.assertEqual(found, [])

    def test_picture_offers_avif_then_webp_then_png_at_1x_and_2x(self):
        html = Template('{% load aso_tags %}{% picture "respectlytics-mark" 16 classes="h-4 w-4" %}').render(Context())
        self.assertEqual(re.findall(r'type="(image/\w+)"', html), ["image/avif", "image/webp"])
        self.assertIn("respectlytics-mark-16.avif 1x, /static/images/respectlytics-mark-32.avif 2x", html)
        self.assertIn('src="/static/images/respectlytics-mark-16.png"', html)
        self.assertIn('width="16" height="16" alt="" class="h-4 w-4"', html)
