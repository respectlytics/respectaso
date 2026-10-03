"""Every text the app hands a person, gathered once for the guards that read it all.

aso/tests/test_no_dashes.py (no dash between words) and
aso/tests/test_value_not_mechanics.py (no account of the tool's internals, help
text kept short) read the same templates, scripts and Python strings with
different rules. Two ways of gathering them would drift apart the day one
learned a new kind of text, so both take them from here, as respectaso.com's
guards take theirs from core/public_surfaces.py.

- ``sources``: this repository's own files of one kind;
- ``pages`` and ``visible_text``: every page served at a fixed address, and
  what a person reads on it;
- ``template_texts``, ``script_strings`` and ``python_strings``: the pieces of
  text a template, a script or a module can show, including the branches no
  fixture renders.
"""

import html
import io
import re
import tokenize
from pathlib import Path

from django.conf import settings
from django.urls import URLPattern, URLResolver, get_resolver

SKIP_DIRS = {".venv", "venv", "node_modules", "staticfiles", "data", ".git", "docs", "dist", "build",
             "tests", "migrations", "vendor", "__pycache__"}
# Text no person reads as copy: comments, styles, code samples.
PROTECT = re.compile(
    r"{%\s*comment\s*%}.*?{%\s*endcomment\s*%}|<!--.*?-->|<style\b.*?</style>"
    r"|<code\b[^>]*>.*?</code>|<pre\b.*?</pre>|{#.*?#}", re.DOTALL)
SCRIPT = re.compile(r"<script\b[^>]*>(.*?)</script>", re.DOTALL)
JS_STRING = re.compile(r"""'(?:[^'\\\n]|\\.)*'|"(?:[^"\\\n]|\\.)*"|`(?:[^`\\]|\\.)*`""", re.DOTALL)
TEMPLATE_EXPR = re.compile(r"\$\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}")
# The attributes a browser shows a person: tooltips, placeholders, labels.
HUMAN_ATTR = re.compile(r'(?:title|placeholder|aria-label|alt|data-tip|content)="([^"]*)"')
# The (i) beside a card title: its text is the tag's argument.
INFO_TIP = re.compile(r'{%\s*info_tip\s+"([^"]*)"\s*%}')


def sources(suffix):
    """This repository's own files of one kind: no dependencies, build
    output, tests or migrations."""
    root = Path(settings.BASE_DIR)
    for path in sorted(root.rglob(f"*{suffix}")):
        if not SKIP_DIRS & set(path.relative_to(root).parts):
            yield path


def relative(path) -> str:
    return Path(path).relative_to(settings.BASE_DIR).as_posix()


def visible_text(markup: str) -> str:
    """What a person reads on a page: its text and the attributes a browser
    shows (tooltips, placeholders, labels), without scripts and styles."""
    body = re.sub(r"<script\b.*?</script>|<style\b.*?</style>|<!--.*?-->|<code\b.*?</code>|<pre\b.*?</pre>",
                  "\n", markup, flags=re.DOTALL)
    attrs = HUMAN_ATTR.findall(body)
    return "\n".join([html.unescape(t) for t in re.sub(r"<[^>]+>", "\n", body).split("\n")]
                     + [html.unescape(a) for a in attrs])


def pages():
    """Every page the app serves at a fixed address."""
    def walk(patterns, prefix=""):
        for p in patterns:
            if isinstance(p, URLResolver):
                yield from walk(p.url_patterns, prefix + str(p.pattern))
            elif isinstance(p, URLPattern):
                yield prefix + str(p.pattern)

    for route in walk(get_resolver().url_patterns):
        if "<" in route or "(?P" in route or route.startswith(("admin", "static", "media", "^")):
            continue
        yield "/" + route


_JS_ESCAPE = re.compile(r"\\u([0-9a-fA-F]{4})")


_RANGE_END = re.compile(r"(?<=[-\u2013])\$\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}|\$\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}(?=[-\u2013])")
# Where a "/" opens a regular expression rather than dividing.
_BEFORE_REGEX = set("(,=:[!&|?{};+-*%<>~^")


def _js_text(literal: str) -> str:
    """A literal's text as the page shows it: a ``\\u2014`` escape is the
    character it draws, a ``${...}`` stands as a word, or as a number where
    it ends a range ("${low}\u2013${high}")."""
    text = _JS_ESCAPE.sub(lambda m: chr(int(m.group(1), 16)), literal[1:-1])
    return TEMPLATE_EXPR.sub(" x ", _RANGE_END.sub("9", text))


def _regex_end(code: str, i: int) -> int:
    """The index after the regular expression literal opening at ``i``."""
    j, in_class = i + 1, False
    while j < len(code) and code[j] != "\n":
        if code[j] == "\\":
            j += 1
        elif code[j] == "[":
            in_class = True
        elif code[j] == "]":
            in_class = False
        elif code[j] == "/" and not in_class:
            return j + 1
        j += 1
    return j


def _js_literals(code: str):
    """The string literals of a script in the order they stand, comments
    skipped. A template literal is read to its closing backtick, across lines
    and past the strings and templates nested in its ``${...}``."""
    i, n = 0, len(code)
    while i < n:
        if code.startswith("//", i):
            end = code.find("\n", i)
            i = n if end < 0 else end
        elif code.startswith("/*", i):
            end = code.find("*/", i + 2)
            i = n if end < 0 else end + 2
        elif code[i] == "/" and (code[:i].rstrip()[-1:] in _BEFORE_REGEX or code[:i].rstrip().endswith("return")):
            i = _regex_end(code, i)
        elif code[i] in "'\"":
            j = i + 1
            while j < n and code[j] not in (code[i], "\n"):
                j += 2 if code[j] == "\\" else 1
            yield code[i:j + 1]
            i = j + 1
        elif code[i] == "`":
            j, depth = i + 1, 0
            while j < n and (code[j] != "`" or depth):
                if code[j] == "\\":
                    j += 1
                elif code.startswith("${", j):
                    depth += 1
                    j += 1
                elif code[j] == "{" and depth:
                    depth += 1
                elif code[j] == "}" and depth:
                    depth -= 1
                j += 1
            yield code[i:j + 1]
            i = j + 1
        else:
            i += 1


def script_strings(code: str):
    """The text of every string literal of a script, comments left out."""
    for literal in _js_literals(code):
        yield re.sub(r"\s+", " ", _js_text(literal))


def template_texts(src: str):
    """Every piece of text a template can show, whichever branch renders:
    its scripts' strings, the attributes a browser shows, the (i) tips, and
    the text between its template tags, each ``{{ value }}`` standing as a
    word and each character reference (``&mdash;``) read as the character a
    browser draws. Comments, styles and code samples are left out."""
    for script in SCRIPT.findall(src):
        yield from script_strings(script)
    body = PROTECT.sub("\n", SCRIPT.sub("\n", src))
    for value in HUMAN_ATTR.findall(body):
        yield html.unescape(re.sub(r"{%.*?%}|{{.*?}}", "", value))
    for tip in INFO_TIP.findall(body):
        yield html.unescape(tip)
    for node in re.split(r"{%.*?%}", body):
        # A browser draws a run of white space, line breaks included, as one
        # space: "journey\n    &mdash; follow" is one line of prose.
        yield re.sub(r"\s+", " ", html.unescape(re.sub(r"{{.*?}}", "x", node)))


_LOGGERS = {"logger", "log", "logging"}
_LOG_LEVELS = {"debug", "info", "warning", "warn", "error", "exception", "critical"}


def _log_call_ends(tokens) -> dict[int, int]:
    """Where each ``logger.info(...)`` style call starts and ends, as token
    indexes: a log line is read by the people who run the app, not on a
    screen."""
    ends, i = {}, 0
    while i + 3 < len(tokens):
        name, dot, level, paren = tokens[i:i + 4]
        if name.type == tokenize.NAME and name.string in _LOGGERS and dot.string == "." \
                and level.string in _LOG_LEVELS and paren.string == "(":
            depth, j = 1, i + 4
            while j < len(tokens) and depth:
                if tokens[j].type == tokenize.OP and tokens[j].string in "([{":
                    depth += 1
                elif tokens[j].type == tokenize.OP and tokens[j].string in ")]}":
                    depth -= 1
                j += 1
            ends[i] = j
        i += 1
    return ends


def _python_pieces(tokens, log_calls):
    """(first token, last token, text) of every string a person may read."""
    starts = (tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING)
    fstring_start = getattr(tokenize, "FSTRING_START", None)
    i = 0
    while i < len(tokens):
        if i in log_calls:
            i = log_calls[i]
            continue
        tok, first = tokens[i], i
        if tok.type == tokenize.STRING:
            docstring = (i == 0 or tokens[i - 1].type in starts) and tokens[i + 1].type == tokenize.NEWLINE
            m = re.match(r"^([rbuRBU]*)('\'\'|\"\"\"|'|\")(.*)\2$", tok.string, re.DOTALL)
            if m and not docstring and "r" not in m.group(1).lower():
                yield first, i, m.group(3)
        elif fstring_start is not None and tok.type == fstring_start:
            text, depth = "", 1
            while depth:
                i += 1
                kind = tokens[i].type
                if kind == fstring_start:
                    depth += 1
                elif kind == tokenize.FSTRING_END:
                    depth -= 1
                elif kind == tokenize.FSTRING_MIDDLE and depth == 1:
                    text += tokens[i].string
                elif kind == tokenize.OP and tokens[i].string == "{" and depth == 1:
                    text += "x"
            if "r" not in tok.string.lower():
                yield first, i, text
        i += 1


def python_strings(src: str, *, skip_logs: bool = False, join: bool = False):
    """The strings of a module a person may read. Docstrings document code
    for the people who change it, and a raw string is a pattern, so both are
    left out; an f-string is read whole, each placeholder standing as a word.
    With ``skip_logs``, the messages of log calls are left out too; with
    ``join``, the pieces of an implicit concatenation are read as the one
    sentence a person sees."""
    tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    pieces = list(_python_pieces(tokens, _log_call_ends(tokens) if skip_logs else {}))
    if not join:
        for _first, _last, text in pieces:
            yield text
        return
    between = (tokenize.NL, tokenize.COMMENT)
    sentence, last = None, -2
    for first, end, text in pieces:
        if sentence is not None and all(tokens[k].type in between for k in range(last + 1, first)):
            sentence += text
        else:
            if sentence is not None:
                yield sentence
            sentence = text
        last = end
    if sentence is not None:
        yield sentence
