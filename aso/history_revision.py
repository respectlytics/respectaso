"""Whether the data the Dashboard shows changed since a page drew it.

The Search History table and the App Summary are drawn from search results,
keywords, their labels and apps. The page used to fetch its table again only when its
five second poll happened to see the ranking refresh running and then stop.
A refresh the page did not watch (a Mac asleep, a hidden window, a refresh
shorter than one poll) never reached the screen, and the other writers of
those rows (a search in another window, Apple's popularity arriving later,
the MCP server) were not noticed at all.

Now SQLite counts every change itself: triggers on those tables add one
to the single HistoryRevision row on each insert, update and delete. A
counter kept in Python would miss writers in another process and every
bulk update, and a list of call sites would miss the next writer somebody
adds.

    ensure_triggers(using)  create the triggers that are missing. Runs after
                            every migrate (aso/apps.py), because a migration
                            that rebuilds one of the tables drops its
                            triggers with it.
    watch(model)            count another app's model too (Pro registers
                            the Rival Tracker's history in its ready()).
    current()               the token a page compares: the count, plus the
                            settings that change how the same rows read.

Ships in the free-tier `aso` app, so it must not import from aso_pro or
licensing.
"""

import json
import zlib

from django.db import DEFAULT_DB_ALIAS, connections

# Insert, update and delete on each of these add one to the count.
OPERATIONS = ("INSERT", "UPDATE", "DELETE")


# Models of other apps whose rows a screen draws and must follow live,
# registered by those apps' ready() through watch(): the free-tier aso app
# cannot import them. KeywordDayRead is deliberately not watched: no screen
# draws from it, and an AI run writes one read per keyword
# (docs/development/ONE_READ_PER_DAY_PLAN.md, D14).
_WATCHED_ELSEWHERE = []


def watch(model) -> None:
    """Count every insert, update and delete of this model too."""
    if model not in _WATCHED_ELSEWHERE:
        _WATCHED_ELSEWHERE.append(model)


def watched_tables() -> list[str]:
    """The tables whose rows the Dashboard draws, and those registered
    through watch()."""
    from .models import App, Keyword, KeywordLabel, SearchResult

    models = (SearchResult, Keyword, KeywordLabel, App, *_WATCHED_ELSEWHERE)
    return [model._meta.db_table for model in models]


def trigger_name(table: str, operation: str) -> str:
    return f"{table}_history_revision_{operation.lower()}"


def ensure_triggers(using: str = DEFAULT_DB_ALIAS) -> int:
    """Create every missing trigger; returns how many are in place.

    Idempotent. Skips a table that does not exist yet, so a partial migrate
    is harmless: the next migrate adds what is still missing.
    """
    from .models import HistoryRevision

    connection = connections[using]
    if connection.vendor != "sqlite":
        return 0
    revision_table = HistoryRevision._meta.db_table
    existing_tables = set(connection.introspection.table_names())
    if revision_table not in existing_tables:
        return 0
    in_place = 0
    with connection.cursor() as cursor:
        for table in watched_tables():
            if table not in existing_tables:
                continue
            for operation in OPERATIONS:
                # The row is created by the first change, so a table emptied
                # by a test flush counts on from one.
                cursor.execute(
                    f"CREATE TRIGGER IF NOT EXISTS {trigger_name(table, operation)} "
                    f"AFTER {operation} ON {table} "
                    f"BEGIN "
                    f"INSERT INTO {revision_table} (id, value) VALUES (1, 1) "
                    f"ON CONFLICT(id) DO UPDATE SET value = value + 1; "
                    f"END"
                )
                in_place += 1
    return in_place


def count() -> int:
    """How many changes the watched tables have seen."""
    from .models import HistoryRevision

    return HistoryRevision.objects.filter(pk=1).values_list("value", flat=True).first() or 0


def current() -> str:
    """The token a page compares with the one its sections were drawn from.

    The same rows read differently when the popularity source changes or
    Apple's active week moves (the cap for terms Apple does not list), and
    neither writes a row, so both are part of the token.
    """
    from .apple_ads.storage import load_apple_settings

    apple = load_apple_settings()
    weeks = json.dumps(apple["apple_ads"].get("active_weeks") or {}, sort_keys=True)
    return f"{count()}.{apple['popularity_source']}.{zlib.crc32(weeks.encode()):08x}"
