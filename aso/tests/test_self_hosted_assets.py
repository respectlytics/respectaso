"""The app loads its stylesheet, scripts, fonts and icons from itself.

A CDN once rendered the whole app unstyled for users whose network blocked
it (public issue #13), and the owner's rule is that every front-end asset is
self-hosted. App Store artwork in an <img> is content the app shows, not an
asset of the app, and stays out of scope here.

Free-tier test: no aso_pro import; it runs in the public repository too.
Plan: docs/development/SELF_HOSTED_FONTS_PLAN.md, section 5.7.
"""

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase

from aso.tests.test_no_browser_storage import app_files

BASE = Path(settings.BASE_DIR)
CDN_HOSTS = (
    "fonts.googleapis.com", "fonts.gstatic.com", "use.typekit.net", "fonts.bunny.net",
    "cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com", "code.jquery.com",
    "ajax.googleapis.com", "stackpath.bootstrapcdn.com", "maxcdn.bootstrapcdn.com",
    "cdn.tailwindcss.com", "kit.fontawesome.com", "use.fontawesome.com", "esm.sh", "cdn.skypack.dev",
)
_DJANGO_COMMENT = re.compile(r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}|{#.*?#}", re.DOTALL)
_ASSET = re.compile(r'<link\b[^>]*\brel="(?:stylesheet|preload|icon|shortcut icon|apple-touch-icon|manifest)"[^>]*>'
                    r'|<script\b[^>]*\bsrc="[^"]*"[^>]*>')
_ADDRESS = re.compile(r'\b(?:href|src)="([^"]*)"')


class NoCdnTest(SimpleTestCase):
    def test_no_template_script_or_stylesheet_source_names_a_cdn(self):
        files = app_files() + [BASE / "static" / "css" / "tailwind.source.css"]
        self.assertGreater(len(files), 40)
        found = [f"{p.relative_to(BASE)}: {host}" for p in files for host in CDN_HOSTS
                 if host in _DJANGO_COMMENT.sub("", p.read_text(encoding="utf-8"))]
        self.assertEqual(found, [])


class RenderedPagesTest(TestCase):
    PAGES = ("/", "/apps/", "/opportunity/", "/methodology/", "/setup/", "/settings/popularity/")

    def test_every_stylesheet_script_font_and_icon_comes_from_this_server(self):
        foreign = {}
        for path in self.PAGES:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, path)
            tags = _ASSET.findall(response.content.decode())
            self.assertGreaterEqual(len(tags), 4, path)       # the stylesheet, two fonts, the icons
            offenders = [a for tag in tags for a in _ADDRESS.findall(tag) if "//" in a]
            if offenders:
                foreign[path] = offenders
        self.assertEqual(foreign, {})
