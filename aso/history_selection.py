"""The Dashboard's multi-select on Search History, kept on the server.

A selection survives paging, filtering, sorting, the table updating itself,
a reload and leaving the Dashboard for another tab, so a set that spans
pages can be copied, labelled, added to the Rival Tracker or deleted in one
action (docs/development/DASHBOARD_MULTI_SELECT_DELETE_PLAN.md).

It used to live in the browser's sessionStorage, with a copy of each
keyword's text taken when it was ticked. desktop-compat keeps browser
storage out of the app, and the text is the database's to say, so it lives
in the Django session instead, like the Top Search Terms filters and the
Rival Tracker's app and country (docs/development/CLEANUPS_PLAN.md, item 2).

A selected row is a (keyword id, storefront) pair, written "12:us": a
search result's id changes at every refresh (SearchResult.upsert_today),
the pair never does. A row deleted anywhere (its trash icon, Delete
selected, Delete All, the Apps page, MCP) leaves the selection at the next
read, so the bar never counts a row that is gone.

Ships in the free-tier ``aso`` app: no ``aso_pro`` or ``licensing`` imports.
"""

from __future__ import annotations

from . import countries
from .models import SearchResult

SESSION_KEY = "history_selection"
# Counts the user's changes, so a page can tell an answer drawn before its
# latest click from one drawn after it (the table swaps itself in the
# background while a refresh runs).
SEQ_KEY = "history_selection_seq"


def parse_pair(text) -> tuple[int, str] | None:
    """(keyword id, storefront) from "12:us"; None for anything else."""
    if not isinstance(text, str) or text.count(":") != 1:
        return None
    keyword_id, country = text.split(":")
    if not keyword_id.isdigit() or int(keyword_id) <= 0:
        return None
    country = country.strip().lower()
    if not countries.is_valid(country):
        return None
    return int(keyword_id), country


def _key(pair: tuple[int, str]) -> str:
    return f"{pair[0]}:{pair[1]}"


def _stored(request) -> list[tuple[int, str]]:
    pairs = []
    for text in request.session.get(SESSION_KEY) or ():
        pair = parse_pair(text)
        if pair and pair not in pairs:
            pairs.append(pair)
    return pairs


def _store(request, pairs) -> None:
    keys = [_key(pair) for pair in pairs]
    if keys:
        request.session[SESSION_KEY] = keys
    else:
        request.session.pop(SESSION_KEY, None)


def entries(request) -> list[dict]:
    """The selected rows that still exist, in the order they were selected:
    [{"pair": "12:us", "kw": "habit tracker"}, ...]. Rows that are gone
    leave the stored selection here."""
    pairs = _stored(request)
    if not pairs:
        return []
    alive = {
        (keyword_id, country): text
        for keyword_id, country, text in SearchResult.objects
        .filter(keyword_id__in={keyword_id for keyword_id, _ in pairs})
        .values_list("keyword_id", "country", "keyword__keyword")
        .distinct()
    }
    kept = [pair for pair in pairs if pair in alive]
    if len(kept) != len(pairs):
        _store(request, kept)
    return [{"pair": _key(pair), "kw": alive[pair]} for pair in kept]


def state(request) -> dict:
    """What the page draws from: {"seq": <changes so far>, "entries": ...}."""
    return {"seq": int(request.session.get(SEQ_KEY) or 0), "entries": entries(request)}


def change(request, *, add=(), remove=(), clear=False) -> dict:
    """Clear, then remove, then add (a row already selected keeps its place),
    and answer with ``state()``. Anything that is not a pair is ignored."""
    pairs = [] if clear else _stored(request)
    dropped = {pair for pair in map(parse_pair, remove) if pair}
    pairs = [pair for pair in pairs if pair not in dropped]
    for pair in map(parse_pair, add):
        if pair and pair not in pairs:
            pairs.append(pair)
    _store(request, pairs)
    request.session[SEQ_KEY] = int(request.session.get(SEQ_KEY) or 0) + 1
    return state(request)
