"""Every sentence names a storefront's App Store as "the App Store in X".

The phrase used to be "the Argentina App Store", which is not English: most
country names are not adjectives. aso.countries.store_phrase is now the one
way to say it, "the App Store in Argentina", "the App Store in the United
States", with "the" where English puts it. aso.scoring._store_name (every
Python sentence) and the country catalog (every script, through
CountryPicker.storeName) both read it.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from aso import countries, country_picker
from aso.scoring import _store_name

BASE_DIR = Path(settings.BASE_DIR)

# The storefronts English names with "the" inside a sentence, reviewed by hand.
WITH_THE = {
    "Bahamas", "British Virgin Islands", "Cayman Islands", "Dominican Republic", "Gambia",
    "Maldives", "Netherlands", "Philippines", "Solomon Islands", "Turks & Caicos", "UAE",
    "United Kingdom", "United States",
}
# Names that end like a plural and still take no article.
PLURAL_WITHOUT_THE = {"Barbados", "Belarus", "Cyprus", "Honduras", "Laos", "Mauritius", "Seychelles",
                      "St. Kitts & Nevis"}


class StorePhraseTest(SimpleTestCase):
    def test_every_storefront_reads_the_app_store_in_the_country(self):
        for country in countries.COUNTRIES.values():
            with self.subTest(country=country.name):
                phrase = countries.store_phrase(country.code)
                where = f"the {country.name}" if country.name in WITH_THE else country.name
                self.assertEqual(phrase, f"the App Store in {where}")
                self.assertNotIn(f"{country.name} App Store", phrase)
                self.assertNotIn("the the", phrase)

    def test_the_article_goes_exactly_where_english_puts_it(self):
        names = {c.name for c in countries.COUNTRIES.values()}
        self.assertLessEqual(WITH_THE, names, "a reviewed name is no longer a storefront")
        given_the = {c.name for c in countries.COUNTRIES.values() if countries.sentence_name(c.code).startswith("the ")}
        self.assertEqual(given_the, WITH_THE)

    def test_a_new_storefront_that_may_need_the_is_decided_by_hand(self):
        """A plural name, an island group, a republic or a union: a person
        decides whether it takes "the", and this list records the answer."""
        maybe = re.compile(r"s$|Islands|Republic|United|Kingdom|Union|Federation|Emirates")
        undecided = [c.name for c in countries.COUNTRIES.values()
                     if maybe.search(c.name) and c.name not in WITH_THE | PLURAL_WITHOUT_THE]
        self.assertEqual(undecided, [])

    def test_unknown_and_empty_codes(self):
        self.assertEqual(countries.store_phrase(""), "the App Store in the United States")
        self.assertEqual(countries.store_phrase("US"), "the App Store in the United States")
        self.assertEqual(countries.store_phrase("zz"), "the App Store in ZZ")

    def test_every_python_sentence_reads_it_through_one_helper(self):
        for code in countries.CODES:
            with self.subTest(code=code):
                self.assertEqual(_store_name(code), countries.store_phrase(code))
        self.assertEqual(_store_name(None), "the App Store in the United States")

    def test_the_catalog_carries_it_for_every_storefront(self):
        for entry in country_picker.catalog()["countries"]:
            with self.subTest(code=entry["code"]):
                self.assertEqual(entry["store"], countries.store_phrase(entry["code"]))

    def test_the_script_says_what_python_says(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not installed")
        catalog = json.dumps(country_picker.catalog())
        script = (
            "global.window = {};"
            "const catalog = require('fs').readFileSync(0, 'utf8');"
            "global.document = {readyState: 'loading', addEventListener: function () {},"
            " getElementById: function (id) { return id === 'country-catalog-data' ? {textContent: catalog} : null; },"
            " querySelectorAll: function () { return []; }};"
            f"require({json.dumps(str(BASE_DIR / 'static/js/country-picker.js'))});"
            "const codes = JSON.parse(catalog).countries.map(c => c.code);"
            "process.stdout.write(JSON.stringify(codes.map(c => [c, window.CountryPicker.storeName(c)])));"
        )
        out = subprocess.run([node, "-e", script], input=catalog, capture_output=True,
                             text=True, check=True, timeout=30).stdout
        pairs = json.loads(out)
        self.assertEqual(len(pairs), len(countries.CODES))
        for code, phrase in pairs:
            with self.subTest(code=code):
                self.assertEqual(phrase, countries.store_phrase(code))

    def test_no_source_builds_the_country_app_store(self):
        """No template, script or Python sentence puts a country before "App
        Store" by itself. aso_pro/prompts.py is left out: its text is read by
        the AI model, never shown, and "the German App Store" names a
        language there. A count before "App Store countries" or "App Store
        storefronts" ("Any of the 175 App Store countries.") is a number, not
        a country, and passes."""
        pattern = re.compile(r"the (\{\{?[^}]+\}\}?|' \+ [^;]+? \+ ') App Store(?! countries| storefronts)")
        offenders = []
        for directory in ("aso", "aso_pro", "static/js", "core", "licensing"):
            for path in (BASE_DIR / directory).rglob("*"):
                if path.suffix not in (".py", ".html", ".js") or "tests" in path.parts:
                    continue
                if path.relative_to(BASE_DIR).as_posix() == "aso_pro/prompts.py":
                    continue
                for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                    if pattern.search(line):
                        offenders.append(f"{path.relative_to(BASE_DIR)}:{number}: {line.strip()}")
        self.assertEqual(offenders, [], "use aso.countries.store_phrase (or _store_name, or CountryPicker.storeName)")
