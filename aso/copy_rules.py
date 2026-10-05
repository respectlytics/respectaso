"""No dash stands between words in anything RespectASO writes for a person.

The owner's rule, for every project (settled 2026-09-15): not an em dash, not
an en dash, not a hyphen doing a dash's job, in any text a person reads. A
reader who has learned to spot machine writing spots the dash, whichever
length it is. What a dash was doing, a comma, a colon, brackets or two
sentences can do.

ONE predicate both fixes and checks, so a check can never disagree with the
fix: ``no_dash_punctuation`` rewrites a text, ``dash_punctuation_in`` finds
the first offending dash, and both read the same ``_dash_role``. Every writer
of text a person reads goes through it: the AI's written feedback and
suggestions (aso_pro), the markdown exports, and the rendered pages, which a
test scans (aso/tests/test_no_dashes.py). Ported from the reference
implementation in thereseai (apps/reports/copy_rules.py), with one addition
for RespectASO's data tables: a dash that is the whole content of a table
cell is the missing-value glyph, not punctuation.

Five dashes are NOT that dash and survive, or the rule would do damage of its
own:
  * a hyphen inside a word ("long-tail", "Covid-19") and a tight number range
    ("2025-2026", "0.9-3.8/day");
  * the minus sign on a number ("-12 percent");
  * the list marker opening a line;
  * the hanging hyphen of a compound ("pre- and post-war");
  * the missing-value glyph alone in a table cell ("| — |") or named in
    quotes ('a rank of "—"').

The owner's second copy rule (2026-10-01), that screens show what a person
needs and never how the tool works inside, has its predicate at the end of
this module: ``mechanics_in``.
"""

from __future__ import annotations

import re

from .words import has_word

_DASH_CHARS = "-‐‑‒–—―−﹘﹣－"
# Tight between two characters, these are a compound, a range or a quoted
# compound, never punctuation. The em dash is deliberately absent from both.
_HYPHENS = "-‐‑"
_TIGHT_DASHES = _HYPHENS + "–"
# The dash run plus the spaces around it, so the substitution sees what it is
# replacing. Only spaces and tabs are eaten: a line break is content.
_DASH_RUN = re.compile(f"[ \t]*[{_DASH_CHARS}]+[ \t]*")
_OPENERS = "([{«“\"'"
# "}" is not here: in RespectASO's prompts a closing brace ends a placeholder
# ("{language_label} — do not"), which is a word, not a closing bracket.
_CLOSERS = ",;:.!?…)]»”"
# Punctuation that already separates two clauses. A closing bracket or quote
# does not: "(Top 5) — the most visible" reads "(Top 5), the most visible".
_SEPARATORS = ",;:.!?…"
# A straight quote opens or closes. Followed by a space and then the dash
# (the dash run starts with that space), it closed a quotation.
_STRAIGHT_QUOTES = "\"'"


def _dash_role(m: re.Match) -> str:
    """What the dash run at ``m`` is doing in its line: 'word', 'hanging',
    'range', 'minus', 'marker', 'arrow', 'flag', 'cell' or 'punct' (the one
    the rule bans). Reads the line around the match."""
    s, raw = m.string, m.group()
    core = raw.strip(" \t")
    space_left, space_right = raw[:1] in (" ", "\t"), raw[-1:] in (" ", "\t")
    before = s[m.start() - 1] if m.start() else ""
    after = s[m.end()] if m.end() < len(s) else ""
    if not space_left and not space_right and len(core) == 1 and core in _TIGHT_DASHES \
            and before and after:
        return "word"
    if not before and not space_right and after and core in ("-", "--"):
        return "flag"                        # "-created_at", "-d": a token of code, never prose
    if not after and not space_left and before.isalnum() and core in _HYPHENS:
        return "flag"                        # "gpt-", "data-": a prefix, never prose
    if not space_left and space_right and len(core) == 1 and core in _HYPHENS \
            and before.isalnum() and after:
        return "hanging"
    if len(core) == 1 and before.isdigit() and after.isdigit():
        return "range"
    if core == "-" and after.isdigit() and not space_right \
            and (space_left or not before or before in "(["):
        return "minus"
    if len(core) >= 3 and set(core) == {"-"}:
        return "flag"                        # "--- RETRY": a separator, not punctuation
    if not before and space_right:
        return "marker"
    if after == ">" or before == "<" or s[m.end():m.end() + 4] == "&gt;":
        return "arrow"                       # "->", "<-" and the escaped "-&gt;"
    if core in ("-", "--") and space_left and not space_right and after.isalpha():
        return "flag"                        # "up -d", "--no-cache": a command's option
    if before == "|" and after in ("|", ""):
        return "cell"
    if len(core) == 1 and not space_left and not space_right \
            and before in "\"'“‘`" and after in "\"'”’`":
        return "cell"                        # the glyph named in quotes: a rank of "—"
    return "punct"


def _dash_sub(m: re.Match) -> str:
    """The text that replaces one dash run: itself for the dashes that
    survive, and a comma (or the punctuation already there) for the one that
    does not."""
    raw, role = m.group(), _dash_role(m)
    if role in ("word", "range"):
        return "-"
    if role in ("minus", "hanging", "arrow", "flag", "cell"):
        return raw
    if role == "marker":
        return raw[:len(raw) - len(raw.lstrip(" \t"))] + "- "
    s = m.string
    before = s[m.start() - 1] if m.start() else ""
    after = s[m.end()] if m.end() < len(s) else ""
    if not before or not after:
        return ""
    if s[max(0, m.start() - 2):m.start()] == "**":
        return ": "                                 # "**Label** — text" is a label
    if before in _STRAIGHT_QUOTES and raw[:1] in (" ", "\t"):
        return ", "                                 # '"Savings" — each' closes a quotation
    if before in _OPENERS:
        return ""
    if before in _SEPARATORS or after in _CLOSERS:
        return " "
    head = s[:m.start()]
    if head.count("“") > head.count("”") or head.count('"') % 2:
        return ": "                                 # a quoted name: “Brand - Tagline”
    return ", "


def no_dash_punctuation(text: str) -> str:
    """``text`` with every dash that stands between words or clauses replaced.
    Word hyphens, number ranges, minus signs, list markers, hanging hyphens
    and the empty-cell glyph are left alone. Idempotent, and a no-op on text
    that never had one."""
    lines, changed = [], 0
    for line in (text or "").split("\n"):
        # A line with no letter or digit in it is structure, not prose: a
        # Markdown rule or table separator, an ASCII box. It never carries a
        # dash a reader would read as punctuation, so it is left alone.
        if not has_word(line):
            lines.append(line)
            continue
        out, n = _DASH_RUN.subn(_dash_sub, line)
        lines.append(out)
        changed += n
    text = "\n".join(lines)
    if not changed:
        return text
    text = re.sub(r"[^\S\n]+([,.;:!?)\]}])", r"\1", text)   # no space before punctuation
    text = re.sub(r",(?:[^\S\n]*,)+", ",", text)            # never a doubled comma
    return re.sub(r"(?<=\S)[^\S\n]{2,}", " ", text)         # collapse doubled spaces, keep indents


def dash_punctuation_in(text: str) -> str:
    """The first dash standing between words in ``text``, with the words
    either side, '' when there is none. One predicate with
    ``no_dash_punctuation`` (the same ``_dash_role``)."""
    for line in (text or "").split("\n"):
        if not has_word(line):
            continue
        for m in _DASH_RUN.finditer(line):
            if _dash_role(m) == "punct":
                return line[max(0, m.start() - 25):m.end() + 25].strip()
    return ""


def walk_prose(obj, fn, *, keep=()):
    """``obj`` with ``fn`` applied to every string inside it, recursively.
    ``keep`` names dict keys whose values pass through untouched."""
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, dict):
        return {k: (v if k in keep else walk_prose(v, fn, keep=keep)) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(walk_prose(v, fn, keep=keep) for v in obj)
    return obj


def dashless(obj, *, keep=()):
    """``obj`` with ``no_dash_punctuation`` applied to every string in it."""
    return walk_prose(obj, no_dash_punctuation, keep=keep)


def no_dash_in_name(text: str) -> str:
    """An App Store title or subtitle with a separating dash written as a
    colon: "Pausely - Digital Wellbeing" reads "Pausely: Digital Wellbeing",
    the way App Store names separate a brand from what the app does. Word
    hyphens stay ("Long-Tail"). Never longer than the input."""
    def sub(m: re.Match) -> str:
        if _dash_role(m) != "punct":
            return m.group()
        s = m.string
        before = s[m.start() - 1] if m.start() else ""
        after = s[m.end()] if m.end() < len(s) else ""
        if not before or not after:
            return ""
        return ": "
    return re.sub(r"[^\S\n]+", " ", _DASH_RUN.sub(sub, text or "")).strip()


_HELD_MARK = re.compile("\u2060?\ue000(\\d+)\ue001\u2060?")


def no_dash_punctuation_keeping(text: str, keep: re.Pattern) -> str:
    """``no_dash_punctuation`` with every span ``keep`` matches left exactly
    as written: tags and code in markup, App Store names in a sentence."""
    held = []

    def hold(m: re.Match) -> str:
        held.append(m.group())
        return f"\u2060\ue000{len(held) - 1}\ue001\u2060"

    body = no_dash_punctuation(keep.sub(hold, text or ""))
    return _HELD_MARK.sub(lambda m: held[int(m.group(1))], body)


# A bold or code label closed right before the dash: "<strong>Label</strong> — text".
_MARKUP_LABEL = re.compile(r"(</(?:strong|b|em|code)>)[ \t]+[—–-][ \t]+")
_MARKUP_HELD = re.compile(r"<code\b[^>]*>.*?</code>|<[^>]+>", re.DOTALL)


def no_dash_in_markup(text: str) -> str:
    """``no_dash_punctuation`` for text that carries inline HTML (the
    release notes): a dash after a closing bold or code tag is a label's
    colon, and tags and ``<code>`` are left exactly as they are."""
    return no_dash_punctuation_keeping(_MARKUP_LABEL.sub(r"\1: ", text or ""), _MARKUP_HELD)


# ---- the second rule: screens show value, not mechanics ---------------------
#
# The owner's rule (2026-10-01), for the app and respectaso.com alike: a
# person reads what a number means for their app, what to do next, how long
# they will wait and what went wrong, never how the tool works inside. These
# are the ways RespectASO has told people about its own plumbing: how often a
# keyword is read, that a read is reused or costs no App Store search, counts
# of AI requests, searches or calls, caching, pacing, throttling,
# deduplication, storage. ``mechanics_in`` finds the first one in a text; the
# guards (aso/tests/test_value_not_mechanics.py, and core/test_value_not_mechanics.py
# on respectaso.com, which keeps a copy of this list) read every surface
# through it. A demand rate ("about 110 searches a day") is about the people
# searching, not about the tool, and passes. The same list as
# respectaso.com's core/copy_rules.py; change both together.
MECHANICS = tuple(re.compile(p, re.IGNORECASE) for p in (
    r"\bcosts? no (?:App Store )?search",
    r"\bAI requests?\b",
    r"\brequests? to (?:your|the) AI provider\b",
    r"\bApp Store searches\b",
    r"\balready read\b",
    r"\bread (?:from (?:Apple|the App Store) )?once\b",
    r"\bre-?reads?\b|\breads? (?:\w+ ){0,2}again\b",
    r"\breus(?:e|ed|es|ing)\b",
    r"\b(?:API|LLM|AI) calls?\b",
    r"\bcach(?:e|ed|es|ing)\b",
    r"\bpacing\b|\bpaces? (?:the )?requests\b",
    r"\bthrottl\w*",
    r"\bdedup\w*",
    r"\b(?:from|in|by) (?:the same|one|a single) App Store search\b|\bfrom (?:the same|one) search\b",
    r"\bone search of\b|\b(?:single|double) (?:App Store )?search(?:es)?\b",
    (r"\b\d[\d,]*\s+(?:to\s+\d[\d,]*\s+)?(?:AI\s+|App Store\s+|iTunes\s+|API\s+)?(?:requests|searches|calls|lookups)\b"
     r"(?!\s+(?:a|an|per|each|every)\s+(?:day|week|month|year))"),
    r"\bApp Store API\b",
    r"\bs/keyword\b|\bseconds? per (?:keyword|country|call|request)\b|\bper (?:API )?call\b",
    r"\blocal ?storage\b|\bsession ?storage\b|\bIndexedDB\b",
    r"\brequest (?:budget|ceiling)\b|\bquota\b",
    r"\bcollect(?:s|ed)? (?:\w+ ){0,4}(?:daily|every day)\b|\bchecked in the App Store\b",
))


def mechanics_in(text: str) -> str:
    """The first account of the tool's own internals in ``text`` (the words
    that matched), or an empty string when the text tells a person only
    what they need."""
    flat = re.sub(r"\s+", " ", text or "")
    for pattern in MECHANICS:
        if found := pattern.search(flat):
            return found.group()
    return ""


# Never promise what the terms disclaim (the owner's rule, 2026-10-02). The
# terms (respectaso.com/terms/, section 10) give no warranty that keyword
# data, rankings, scores or AI-generated content are accurate, reliable or
# complete, none that the Service will improve an app's rankings or
# downloads, and none that it runs uninterrupted or error free; Apple alone
# decides App Review and ranking; and what leaves a Mac (App Store searches,
# the AI provider a user picks) is the privacy policy's to state, so nothing
# says "nothing leaves". Each entry is the shape of a sentence someone could
# quote back as a promise, named by what it promises. ``overclaim_in`` finds
# the first; the claims guards (aso/tests/test_claims.py, and
# core/test_claims.py on respectaso.com, which keeps a copy of this list)
# read every surface through it. Whether a sentence is something we can stand
# behind stays a person's reading against the terms at every copy change; the
# guards make the known shapes impossible to ship again. The same list as
# respectaso.com's core/copy_rules.py; change both together.
OVERCLAIMS = {
    "says the numbers or the AI are right": re.compile(
        r"\b(?:100%|always|perfectly|completely|fully|guaranteed to be) "
        r"(?:accurate|correct|right|precise|reliable|exact)\b"
        r"|(?<!no )(?<!not )(?<!share )(?<!publish )(?<!publishes )(?<!know )(?<!knows )\b(?:exact|precise) (?:search volumes?|download (?:counts|numbers))\b"
        r"|\baccurate (?:keyword data|data|downloads?|download (?:estimates|numbers)|search volumes?|"
        r"popularity|rankings?|ranks|scores?|estimates?|metadata|results?)\b"
        r"|\bnever (?:wrong|inaccurate|hallucinates?|makes (?:anything|things|facts) up)\b"
        r"|\b(?:not|no|without|instead of|won'?t|doesn'?t|don'?t) hallucinat(?:es?|ed|ing|ions?)\b|\banswers? accurately\b"
        r"|\bverified (?:results?|data|numbers|scores?|answers?|keywords?|metadata)\b"
        r"|\b(?:error|hallucination|mistake)[ -]free\b|\bfact[ -]?checked\b",
        re.IGNORECASE),
    "promises more downloads or higher ranks": re.compile(
        r"\b(?:boosts?|increases?|doubles?|triples?|skyrockets?) (?:your )?(?:app'?s? )?"
        r"(?:downloads|installs|rankings?|ranks|visibility|revenue|organic traffic)\b"
        r"|\b(?:will|guaranteed to) (?:rank|reach #?1|get (?:you )?to #?1)\b"
        r"|\bget (?:you |your app )?(?:to )?#1\b",
        re.IGNORECASE),
    "promises a guarantee": re.compile(r"\bguarantee(?:s|d|ing)?\b|\bwarrant(?:y|ies)\b", re.IGNORECASE),
    "promises nothing is missed": re.compile(
        r"\bnever miss(?:es|ed|ing)?\b|\bnothing (?:slips|gets) (?:through|past)\b|\bmiss(?:es)? nothing\b"
        r"|\bevery (?:keyword|opportunity) (?:that matters|there is)\b|\bcomplete (?:keyword )?coverage\b",
        re.IGNORECASE),
    "speaks for Apple's review": re.compile(
        r"\bapple[ -]approved\b|\bapproved by apple\b|\b(?:pass|passes|passing) (?:app )?review\b"
        r"|\bapp store[ -]compliant\b|\bcompliant with (?:the )?app store\b|\bnever (?:be )?rejected\b",
        re.IGNORECASE),
    "says nothing leaves the Mac": re.compile(
        r"\b100% (?:private|local|offline)\b|\bnothing (?:ever )?leaves your (?:mac|machine|device|computer)\b"
        r"|\b(?:no|zero) data (?:ever )?leav(?:es|ing)\b|\bnever leaves your (?:mac|machine|device|computer)\b"
        r"|\bcompletely (?:private|offline)\b",
        re.IGNORECASE),
    "promises a time saving nothing measures": re.compile(
        r"\bsav(?:e|es|ing) (?:you )?(?:\d+ )?(?:hours|days|weeks|minutes)\b|\b\d+x faster\b|\bhours? saved\b",
        re.IGNORECASE),
}


def overclaim_in(text: str) -> str:
    """The first promise the terms take back in ``text``, as "what it
    promises: the words that matched", or an empty string."""
    flat = re.sub(r"\s+", " ", text or "")
    for name, pattern in OVERCLAIMS.items():
        if found := pattern.search(flat):
            return f"{name}: {found.group()}"
    return ""
