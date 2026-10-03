"""What a word is, in every language the App Store serves (aso/words.py).

On 2026-10-01 a French Rival Tracker suggestion came back as "produits
drivs", "volatilit implicite" and "options dachat": keyword cleaning kept
a-z only. The App Store finds none of those ("produits drivs" shares no
top 10 app with "produits dérivés", "options dachat" finds nothing), and
Japanese, Korean, Russian and Arabic keywords were emptied altogether. The
word splitter cut Thai and Hindi words apart at their vowel marks.
"""

import ast
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from aso.words import clean_keyword, has_word, is_word_char, tokenize_words

BASE_DIR = Path(settings.BASE_DIR)
WORD_REGEX = re.compile(r"\\[wW]|a-z0-9|\[\^a-z")
NOT_WORDS = {
    # Patterns that match an API key's shape, not words.
    "aso_pro/run_failures.py": ("A-Za-z0-9",),
}


class CleanKeywordTest(SimpleTestCase):
    def test_accents_stay(self):
        self.assertEqual(clean_keyword("Produits dérivés"), "produits dérivés")
        self.assertEqual(clean_keyword("volatilité implicite"), "volatilité implicite")
        self.assertEqual(clean_keyword("Ernährung"), "ernährung")
        self.assertEqual(clean_keyword("señales de trading"), "señales de trading")

    def test_an_apostrophe_between_letters_stays(self):
        self.assertEqual(clean_keyword("options d'achat"), "options d'achat")
        self.assertEqual(clean_keyword("options d’achat"), "options d'achat")
        self.assertEqual(clean_keyword("kid's games"), "kid's games")

    def test_an_apostrophe_at_a_word_edge_goes(self):
        self.assertEqual(clean_keyword("'quoted' words"), "quoted words")
        self.assertEqual(clean_keyword("players' guide"), "players guide")

    def test_every_script_stays(self):
        for keyword in ("株式投資", "주식 투자", "инвестиции", "استثمار", "สวัสดี การลงทุน", "शेयर बाजार", "Đầu tư"):
            with self.subTest(keyword=keyword):
                self.assertEqual(clean_keyword(keyword), keyword.lower())

    def test_symbols_that_are_part_of_a_search_stay(self):
        """Live, 2026-10-02: the App Store answers each spelling differently
        ("sp 500" shares 4 of the top 10 apps with "s&p 500", "c" none with
        "c++"), and Claude Desktop got "sp 500" back for "S&P 500"."""
        for typed, kept in (("S&P 500", "s&p 500"), ("Wi-Fi", "wi-fi"), ("covid-19", "covid-19"),
                            ("node.js", "node.js"), ("24/7", "24/7"), ("C++", "c++"), ("c#", "c#"),
                            ("Disney+", "disney+"),
                            ("e\u2011mail", "e-mail")):
            with self.subTest(typed=typed):
                self.assertEqual(clean_keyword(typed), kept)

    def test_other_characters_go_without_a_space(self):
        self.assertEqual(clean_keyword("  Screen-Time & Focus!  "), "screen-time focus")
        self.assertEqual(clean_keyword("rock & roll"), "rock roll")  # a lone & would count as a word
        self.assertEqual(clean_keyword('"screen time."'), "screen time")
        self.assertEqual(clean_keyword("- screen time"), "screen time")
        self.assertEqual(clean_keyword("a - b"), "a b")
        self.assertEqual(clean_keyword("& roll"), "roll")
        self.assertEqual(clean_keyword("\U0001f4a1 ideas"), "ideas")
        self.assertEqual(clean_keyword("café 2"), "café 2")
        self.assertEqual(clean_keyword("a__b"), "ab")

    def test_decomposed_accents_become_one_character(self):
        self.assertEqual(clean_keyword("de\u0301rive\u0301s"), "dérivés")

    def test_nothing_left(self):
        self.assertEqual(clean_keyword("!!! — ???"), "")
        self.assertEqual(clean_keyword(""), "")


class TokenizeWordsTest(SimpleTestCase):
    def test_lowercase_words(self):
        self.assertEqual(tokenize_words("Fitness"), ["fitness"])
        self.assertEqual(tokenize_words(""), [])
        self.assertEqual(tokenize_words(None), [])

    def test_accents_stay(self):
        self.assertEqual(tokenize_words("Résumé müller señor"), ["résumé", "müller", "señor"])
        self.assertEqual(tokenize_words("Für Gesundheit und Übungen"), ["für", "gesundheit", "und", "übungen"])

    def test_apostrophes_hyphens_and_underscores_split(self):
        self.assertEqual(tokenize_words("l'anglais"), ["l", "anglais"])
        self.assertEqual(tokenize_words("aujourd'hui"), ["aujourd", "hui"])
        self.assertEqual(tokenize_words("well-being"), ["well", "being"])
        self.assertEqual(tokenize_words("test_word"), ["test", "word"])
        self.assertEqual(tokenize_words("méditation — bien-être & relaxation"),
                         ["méditation", "bien", "être", "relaxation"])

    def test_thai_and_hindi_words_stay_whole(self):
        self.assertEqual(tokenize_words("สวัสดี การลงทุน"), ["สวัสดี", "การลงทุน"])
        self.assertEqual(tokenize_words("शेयर बाजार"), ["शेयर", "बाजार"])

    def test_other_scripts(self):
        self.assertEqual(tokenize_words("日本語"), ["日本語"])
        self.assertEqual(tokenize_words("주식 투자"), ["주식", "투자"])
        self.assertEqual(tokenize_words("Инвестиции, акции"), ["инвестиции", "акции"])

    def test_min_length(self):
        self.assertEqual(tokenize_words("L'app pour l'éducation", min_length=2), ["app", "pour", "éducation"])

    def test_letters_only_leaves_out_words_with_a_digit(self):
        self.assertEqual(tokenize_words("mp3 player 4k video", letters_only=True), ["player", "video"])
        self.assertEqual(tokenize_words("สวัสดี 2024", letters_only=True), ["สวัสดี"])


class WordCharTest(SimpleTestCase):
    def test_letters_marks_and_digits(self):
        for char in ("a", "é", "日", "ั", "ि", "٣", "7"):
            with self.subTest(char=char):
                self.assertTrue(is_word_char(char))
        for char in (" ", "-", "_", "'", "&", "—"):
            with self.subTest(char=char):
                self.assertFalse(is_word_char(char))

    def test_has_word(self):
        self.assertTrue(has_word("| ั |"))
        self.assertFalse(has_word("|---|---|"))


class OneWordRuleTest(SimpleTestCase):
    """Every keyword cleaner and word splitter goes through aso/words.py: a
    second rule is how the a-z one survived in one place for six months
    after the rest of the pipeline learned accents."""

    def test_no_other_word_regex(self):
        offenders = []
        for package in ("aso", "aso_pro", "licensing", "llm_providers"):
            root = BASE_DIR / package
            if not root.exists():
                continue
            for path in root.rglob("*.py"):
                rel = path.relative_to(BASE_DIR).as_posix()
                if "/tests/" in rel or "/migrations/" in rel or "/management/" in rel:
                    continue
                for node in ast.walk(ast.parse(path.read_text())):
                    if not (isinstance(node, ast.Call) and getattr(node.func, "attr", "") in
                            ("sub", "findall", "split", "compile", "match", "search", "fullmatch", "finditer")
                            and node.args and isinstance(node.args[0], ast.Constant)
                            and isinstance(node.args[0].value, str)):
                        continue
                    pattern = node.args[0].value
                    if WORD_REGEX.search(pattern) and not any(a in pattern for a in NOT_WORDS.get(rel, ())):
                        offenders.append(f"{rel}:{node.lineno} {pattern}")
        self.assertEqual(offenders, [], "Use aso.words (tokenize_words, clean_keyword) instead.")
