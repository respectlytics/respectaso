"""Tests for aso.locale_data — country→locale mapping behaviour."""

from __future__ import annotations

import unittest

from aso.locale_data import (
    COUNTRY_LOCALES,
    is_english_locale,
    is_valid_locale_for_country,
    label_for,
    locale_country_locales_for_template,
    locales_for,
    primary_locale,
)


class LocalesForTests(unittest.TestCase):
    def test_single_language_country(self):
        result = locales_for("fr")
        codes = [c for c, _ in result]
        # France's primary locale must be first; English is appended as fallback.
        self.assertEqual(codes[0], "fr-FR")
        self.assertIn("en-US", codes)

    def test_multilingual_country(self):
        result = locales_for("ca")
        codes = [c for c, _ in result]
        # Canada's first option is English (Canada); French (Canada) follows.
        self.assertEqual(codes[0], "en-CA")
        self.assertIn("fr-CA", codes)

    def test_unknown_country_returns_english(self):
        result = locales_for("zz")
        self.assertEqual(result, [("en-US", "English (US)")])

    def test_english_country_no_duplicate_fallback(self):
        # US already starts with English — fallback must not be appended twice.
        result = locales_for("us")
        codes = [c for c, _ in result]
        self.assertEqual(codes.count("en-US"), 1)


class PrimaryLocaleTests(unittest.TestCase):
    def test_primary_for_each_supported_country(self):
        # Every supported country must yield a non-empty primary locale.
        for code in COUNTRY_LOCALES:
            self.assertTrue(primary_locale(code))


class LabelForTests(unittest.TestCase):
    def test_round_trip_label(self):
        self.assertEqual(label_for("fr-FR"), "French")
        self.assertEqual(label_for("es-MX"), "Spanish (Mexico)")

    def test_unknown_locale_returns_code(self):
        self.assertEqual(label_for("xx-YY"), "xx-YY")


class IsEnglishLocaleTests(unittest.TestCase):
    def test_english_variants(self):
        self.assertTrue(is_english_locale("en-US"))
        self.assertTrue(is_english_locale("en-GB"))
        self.assertTrue(is_english_locale("en-CA"))

    def test_non_english(self):
        self.assertFalse(is_english_locale("fr-FR"))
        self.assertFalse(is_english_locale("es-MX"))
        self.assertFalse(is_english_locale("ja"))


class ValidationTests(unittest.TestCase):
    def test_valid_locale(self):
        self.assertTrue(is_valid_locale_for_country("fr", "fr-FR"))
        # English fallback must be accepted for any country.
        self.assertTrue(is_valid_locale_for_country("fr", "en-US"))

    def test_invalid_locale(self):
        self.assertFalse(is_valid_locale_for_country("fr", "de-DE"))


class TemplateExportTests(unittest.TestCase):
    def test_json_serialisable_shape(self):
        out = locale_country_locales_for_template()
        # Must be a dict of country → list of [code, label] pairs.
        for country, entries in out.items():
            self.assertIsInstance(country, str)
            self.assertIsInstance(entries, list)
            for pair in entries:
                self.assertEqual(len(pair), 2)
                self.assertIsInstance(pair[0], str)
                self.assertIsInstance(pair[1], str)


if __name__ == "__main__":
    unittest.main()
