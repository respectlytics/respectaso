"""What a word is, in every language the App Store serves.

Every place that cleans a keyword or splits text into words uses these, in
both editions. A word is a run of letters, combining marks and digits in any
script (Unicode categories L, M and N).

Two rules came before this one and both lost real words. An a-z rule
(aso_pro.constraints.clean_keyword until 2026-10-01) turned "produits
dérivés" into "produits drivs", which the App Store does not find, and
emptied every Japanese, Korean, Russian and Arabic keyword. Python's ``\\w``
leaves out combining marks, which cut Thai and Hindi words apart: "สวัสดี"
became "สว" and "สด".
"""

from __future__ import annotations

import unicodedata

# Apostrophes and hyphens people and AI models type, as the ones the App
# Store reads.
_APOSTROPHES = str.maketrans({"\u2019": "'", "\u2018": "'", "\u02bc": "'", "`": "'",
                              "\u2010": "-", "\u2011": "-"})


def is_word_char(char: str) -> bool:
    """A letter, a combining mark or a digit, in any script."""
    return unicodedata.category(char)[0] in "LMN"


def is_letter(char: str) -> bool:
    """A letter or a combining mark, in any script: a word character that is
    not a digit."""
    return unicodedata.category(char)[0] in "LM"


def _lower(text: str) -> str:
    return unicodedata.normalize("NFC", text or "").lower()


def tokenize_words(text: str, min_length: int = 1, *, letters_only: bool = False) -> list[str]:
    """The lowercase words of ``text``, in order.

    Anything that is not a word character separates words, so apostrophes
    and hyphens split ("l'anglais" gives "l" and "anglais", "well-being"
    gives "well" and "being"). ``letters_only`` leaves out every word with a
    digit in it ("mp3", "4k").
    """
    found, current = [], []
    for char in _lower(text):
        if is_word_char(char):
            current.append(char)
        elif current:
            found.append("".join(current))
            current = []
    if current:
        found.append("".join(current))
    return [
        word for word in found
        if len(word) >= min_length and (not letters_only or all(map(is_letter, word)))
    ]


def word_spans(text: str) -> list[tuple[int, int]]:
    """(start, end) of every word of ``text`` as written, for code that marks
    words in place (the title highlighter). The same words as
    ``tokenize_words``; its JavaScript twin is ``[\\p{L}\\p{M}\\p{N}]+``."""
    spans, start = [], None
    for index, char in enumerate(text or ""):
        if is_word_char(char):
            if start is None:
                start = index
        elif start is not None:
            spans.append((start, index))
            start = None
    if start is not None:
        spans.append((start, len(text)))
    return spans


def has_word(text: str) -> bool:
    """True when ``text`` holds at least one word character."""
    return any(is_word_char(char) for char in text or "")


def clean_keyword(text: str) -> str:
    """A keyword as RespectASO stores, scores and shows it.

    Lowercase; the letters, marks and digits of every script stay, and so
    do the symbols that are part of a search, because the App Store answers
    each spelling differently (live, 2026-10-02: "sp 500" shares 4 of the
    top 10 apps with "s&p 500", "c" none with "c++", "wifi" 6 with "wi-fi"):

    - ' & - . / between two word characters ("options d'achat", "s&p 500",
      "wi-fi", "node.js", "24/7");
    - + and # right after a word ("c++", "c#", "disney+").

    Every other character goes without leaving a space (quotes, a trailing
    period, list markers, emoji, an "&" on its own, which would count as a
    word), and runs of spaces become one.
    """
    text = _lower(text).strip().translate(_APOSTROPHES)
    kept = [char for index, char in enumerate(text)
            if is_word_char(char) or char.isspace() or _part_of_a_word(text, index)]
    return " ".join("".join(kept).split())


_JOINERS = "'&-./"
_SUFFIXES = "+#"


def _part_of_a_word(text: str, index: int) -> bool:
    char = text[index]
    before = text[index - 1] if index > 0 else ""
    after = text[index + 1] if index + 1 < len(text) else ""
    if char in _JOINERS and before and after and is_word_char(before) and is_word_char(after):
        return True
    if char in _SUFFIXES:
        run_start = index
        while run_start > 0 and text[run_start - 1] == char:
            run_start -= 1
        return run_start > 0 and is_word_char(text[run_start - 1])
    return False
