"""No second country list, anywhere.

The app carried six of them before aso/countries.py: aso/forms.py, two in
aso/locale_data.py, aso/templatetags/aso_tags.py, and two hardcoded JavaScript
objects inside dashboard.html. They had already drifted: the dashboard's label
map knew about Poland and Malaysia while its picker could not select them, and
one spelled Türkiye where the others said Turkey.

These tests fail the build when a seventh appears, and when a page types a
storefront count that the registry can answer.
"""

import os
import re

from django.test import SimpleTestCase

from aso import countries

BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SEARCH_DIRS = ("aso", "aso_pro", "static/js")
SEARCH_SUFFIXES = (".py", ".js", ".html")

# Where a country list legitimately lives, and the tests that read one.
ALLOWED = {
    "aso/countries.py",
    # Search aliases keyed by code, not a country list: it says what a person
    # might type to find a storefront, and test_country_picker.py asserts its
    # keys are all real, so it cannot invent one.
    "aso/country_picker.py",
    "aso/tests/test_countries.py",
    "aso/tests/test_no_hardcoded_countries.py",
    "aso/tests/test_opportunity_scans.py",
    "aso/management/commands/probe_storefronts.py",
    "docs",
}

# Any run holding this many distinct codes is a list, not an example.
LIST_THRESHOLD = 8
# How far apart two quoted codes may sit and still count as the same run.
MAX_GAP = 40
# ISO codes that are also everyday English words. A stop-word set in a script
# is full of them and is not a country list, so they do not count towards the
# threshold; a real list carries plenty of de, fr, jp, br, mx besides.
ENGLISH_COLLISIONS = {
    "am", "an", "as", "at", "be", "by", "do", "he", "id", "if", "in", "is",
    "it", "la", "me", "my", "no", "of", "on", "or", "pa", "re", "sh", "so",
    "to", "tv", "up", "us", "we",
}


def _source_files():
    for directory in SEARCH_DIRS:
        root = os.path.join(BASE_DIR, directory)
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "node_modules")]
            for filename in filenames:
                if not filename.endswith(SEARCH_SUFFIXES):
                    continue
                full = os.path.join(dirpath, filename)
                rel = os.path.relpath(full, BASE_DIR)
                if rel in ALLOWED:
                    continue
                yield rel, full


class NoSecondCountryListTest(SimpleTestCase):
    def test_no_file_carries_a_country_list_of_its_own(self):
        """A dense run of quoted storefront codes is a list, not prose.

        Density is the signal: a list writes "us", "gb", "de" a few
        characters apart, while prose happens to contain 'it' and 'no' pages
        apart. Modelled on the check in aso_pro/tests/test_mcp.py that stops a
        tool file defining its own VALID_COUNTRIES.
        """
        quoted = re.compile(r"""['"]([a-z]{2})['"]""")
        offenders = []
        for rel, full in _source_files():
            with open(full, encoding="utf-8") as f:
                content = f.read()
            hits = [
                m for m in quoted.finditer(content)
                if m.group(1) in countries.CODES
                and m.group(1) not in ENGLISH_COLLISIONS
            ]
            run = []
            for match in hits:
                if run and match.start() - run[-1].end() > MAX_GAP:
                    run = []
                run.append(match)
                if len({m.group(1) for m in run}) >= LIST_THRESHOLD:
                    offenders.append(
                        f"{rel}: {LIST_THRESHOLD}+ storefront codes together "
                        f"near character {run[0].start()}"
                    )
                    break
        self.assertEqual(
            offenders, [],
            "These files look like they carry their own country list. The "
            "registry is aso/countries.py:\n" + "\n".join(offenders),
        )

    def test_forms_re_exports_rather_than_defining(self):
        with open(os.path.join(BASE_DIR, "aso", "forms.py")) as f:
            source = f.read()
        self.assertIn("COUNTRY_CHOICES = countries.choices()", source)
        self.assertNotIn("COUNTRY_CHOICES = [", source)

    def test_no_template_defines_a_country_name_map(self):
        offenders = []
        for rel, full in _source_files():
            if not rel.endswith((".html", ".js")):
                continue
            with open(full, encoding="utf-8") as f:
                content = f.read()
            if re.search(r"(COUNTRY_NAMES|SUPPORTED_COUNTRIES)\s*=", content):
                offenders.append(rel)
        self.assertEqual(
            offenders, [],
            "Country data belongs in the catalog the base template emits "
            "(aso/country_picker.py): " + ", ".join(offenders),
        )


class NoTypedStorefrontCountTest(SimpleTestCase):
    """The count is read from the registry, never typed into copy.

    It was typed in nine places and every one of them would have been wrong
    the moment Apple opened a storefront.
    """

    STALE = ("all 30", "30 countries", "30 storefronts", "30 markets",
             "30 App Store")

    def test_no_page_states_a_storefront_count_of_its_own(self):
        offenders = []
        for rel, full in _source_files():
            copy_file = (
                rel.endswith((".html", ".js"))
                or rel.startswith("aso_pro/mcp/")   # docstrings an assistant reads
            )
            if not copy_file:
                continue
            with open(full, encoding="utf-8") as f:
                content = f.read()
            for phrase in self.STALE:
                if phrase in content:
                    offenders.append(f"{rel}: {phrase!r}")
        self.assertEqual(
            offenders, [],
            "Use the storefront_count template tag or len(countries.CODES):\n"
            + "\n".join(offenders),
        )
