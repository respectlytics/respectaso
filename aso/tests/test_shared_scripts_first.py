"""A page's own script finds the shared libraries already loaded.

Settings → AI drew its Tested badge while the page loaded by calling
``window.ProductIcon.say``, but base.html loaded icons.js after the page
content, so the page stopped with "Cannot read properties of undefined
(reading 'say')" and the badge never showed (found 2026-11-22, also in 3.2.1).
base.html now loads the shared libraries and the catalogues they read at the
top of the body, before any page content.

This renders every page served at a fixed address, without and with a licence
(the Pro pages render only with one), and checks on each that the libraries
and their catalogues come before the page content and that every inline script
naming a library's global comes after that library's script tag.
"""

import re
from unittest.mock import patch

from django.apps import apps
from django.test import TestCase, override_settings

from aso.tests.surfaces import pages

# Each shared library's script and the global it defines.
LIBRARIES = {
    "js/icons.js": "ProductIcon",
    "js/classification-badge.js": "ClassificationBadge",
    "js/difficulty-factors.js": "DifficultyFactors",
    "js/markdown.js": "AsoMarkdown",
}
CATALOGUES = ("country-catalog-data", "classification-legend-data", "product-icons-data",
              "difficulty-factors-data")
INLINE_SCRIPT = re.compile(r"<script\b(?![^>]*\bsrc=)(?![^>]*application/json)[^>]*>(.*?)</script>", re.DOTALL)


def _order_problems(markup: str) -> list[str]:
    problems = []
    main = markup.find("<main")
    if main < 0:
        return problems
    loaded_at = {}
    for path, name in LIBRARIES.items():
        tag = re.search(r'<script src="[^"]*' + re.escape(path) + r'[^"]*"', markup)
        if not tag:
            problems.append(f"{path} is not loaded")
            continue
        loaded_at[name] = tag.start()
        if tag.start() > main:
            problems.append(f"{path} loads after the page content")
    for node in CATALOGUES:
        at = markup.find(f'id="{node}"')
        if at < 0 or at > main:
            problems.append(f"{node} is missing or after the page content")
    for script in INLINE_SCRIPT.finditer(markup):
        for name, at in loaded_at.items():
            if re.search(r"\b" + name + r"\b", script.group(1)) and script.start() < at:
                problems.append(f"a script naming {name} runs before it loads: {script.group(1).strip()[:60]!r}")
    return problems


class SharedScriptsFirstTest(TestCase):
    maxDiff = None

    @patch("aso.update_check.check_for_update", return_value={"update_available": False})
    def _scan(self, _update_check):
        problems, checked = {}, set()
        for url in pages():
            response = self.client.get(url)
            if response.status_code != 200 or "text/html" not in response.get("Content-Type", ""):
                continue
            checked.add(url)
            found = _order_problems(response.content.decode())
            if found:
                problems[url] = found
        return problems, checked

    def test_every_page_loads_the_shared_libraries_first(self):
        problems, checked = self._scan()
        self.assertIn("/", checked)
        self.assertEqual(problems, {})

    @override_settings(DEBUG_SKIP_LICENSE=True)
    def test_every_page_loads_the_shared_libraries_first_with_a_licence(self):
        problems, checked = self._scan()
        if apps.is_installed("aso_pro"):  # the free edition has no Settings → AI
            self.assertIn("/pro/settings/api-keys/", checked)
        self.assertEqual(problems, {})

    def test_the_check_sees_a_script_that_runs_too_early(self):
        markup = ('<body><main><script>window.ProductIcon.say(b, "check", "Tested");</script></main>'
                  '<script src="/static/js/icons.js"></script></body>')
        self.assertTrue(any("ProductIcon runs before it loads" in p for p in _order_problems(markup)))
