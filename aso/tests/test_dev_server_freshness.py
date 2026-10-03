"""A development server never mixes old pages with a new stylesheet.

On 2026-10-02 the owner's test server, started without the autoreloader
(--noreload, as scripts/dev_scratch.sh does), kept serving the templates it
had cached at start while the stylesheet on disk had changed under it: the
pages lost their padding, gaps and score rings. In development the templates
are now read at every request and the stylesheet's address carries its last
change, so a browser fetches a new copy. The Mac app and Docker run with
DEBUG off and are untouched.
"""

from pathlib import Path

from django.conf import settings
from django.test import RequestFactory, SimpleTestCase, override_settings

from core.context_processors import version

BASE = Path(settings.BASE_DIR)
SETTINGS = ("core/settings.py", "_public_overrides/core/settings.py")


class DevServerFreshnessTest(SimpleTestCase):
    def test_development_reads_templates_at_every_request(self):
        for rel in SETTINGS:
            path = BASE / rel
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8")
            with self.subTest(settings=rel):
                block = text[text.index("if DEBUG:\n    TEMPLATES[0]"):]
                self.assertIn('TEMPLATES[0]["APP_DIRS"] = False', block)
                self.assertIn('"django.template.loaders.filesystem.Loader"', block)
                self.assertNotIn("cached.Loader", block)

    def test_the_stylesheet_address_carries_its_last_change_in_development(self):
        request = RequestFactory().get("/")
        with override_settings(DEBUG=True):
            stamp = version(request)["STYLESHEET_VERSION"]
        self.assertTrue(stamp.isdigit())
        with override_settings(DEBUG=False):
            self.assertEqual(version(request)["STYLESHEET_VERSION"], "")
        # The public repo has no _public_overrides folder: its base.html is the one in aso/.
        for rel in ("aso/templates/aso/base.html", "_public_overrides/aso/templates/aso/base.html"):
            path = BASE / rel
            if not path.is_file():
                continue
            with self.subTest(template=rel):
                self.assertIn("?v={{ STYLESHEET_VERSION }}", path.read_text(encoding="utf-8"))
