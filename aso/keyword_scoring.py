"""The per-keyword scoring pipeline, written once.

``score_country`` scores one keyword in one storefront and stores NOTHING but
the day's read: today's read of the keyword (aso/day_reads.py, one App Store
search of up to 200 apps per keyword, storefront and day), difficulty from
its first 25 apps, the tracked app's rank from the order of all of them,
popularity from both sources, the download estimates. It is the unit of work
of the Country Opportunity Finder, which deliberately keeps its results out
of the search history until the user saves them. It is two halves:
``read_keyword``, what belongs to the search and is the same for every app,
and ``app_standing``, the app's own rank and profile.

``score_keyword_pair`` is ``score_country`` plus today's ``SearchResult``:
what a keyword search, a single-keyword Refresh and the daily ranking refresh
all run. It used to exist three times (``search_view``,
``keyword_refresh_view`` and ``scheduler._refresh_pair``), and its scoring
half used to exist twice more inside the opportunity views and once again in
the MCP scan; no-duplicate-logic.instructions.md lists this module as the
single source. Splitting it is what guarantees that a keyword search and a
country scan score a keyword identically.

``store_read`` writes one read of a keyword in a storefront into today's row
of that keyword and of its twins: the same keyword tracked in the same
storefront for another app, or for none. Popularity, difficulty and the
competitors belong to the search, so twin rows must never disagree on them;
they did when each was read at its own time (the owner saw 52 and 53 for
"reduce screen time" on 2026-09-30, one row refreshed by hand at 10:22 and
its twin read at 04:50). Each twin keeps its own app's rank.

``result_payload`` builds the dict the dashboard's result cards render from
a stored row, so a search that finished hours ago (or in a previous app
session) shows exactly what a live one did.
"""

from __future__ import annotations

import copy

from . import day_reads
from .models import SearchResult
from .popularity import popularity_fields, reported_by_apple, resolve_popularity
from .scoring import calc_opportunity, display_competitors, targeting_payload
from .services import display_snippets


def read_keyword(kw_text, country, *, itunes_service, difficulty_calc, download_est, force=False) -> dict:
    """What belongs to the search itself, the same for every app: the
    competitors, the difficulty, the popularity from both sources and the
    download estimates, from today's read of the keyword (aso/day_reads.py:
    from the table when it was read today, from Apple otherwise, from Apple
    in any case with ``force``). Stores nothing but that read.

    ``day_read`` in the answer is the read itself: every app's rank comes
    from it (``day_reads.rank_of``), at no cost.

    Raises ``SearchAPIUnavailableError`` and ``ITunesRateLimited`` to the
    caller.
    """
    day_read = day_reads.get_or_fetch(kw_text, country, itunes_service=itunes_service, force=force)
    competitors = day_read.competitors
    difficulty_score, breakdown = difficulty_calc.calculate(competitors, keyword=kw_text)

    # Popularity from both sources; ``pop.effective`` feeds all math.
    pop = resolve_popularity(competitors, kw_text, country)
    breakdown["download_estimates"] = {**download_est.estimate(pop.effective or 0, country=country),
                                       "searches_reported": reported_by_apple(pop)}
    return {
        "country": country,
        "competitors": competitors,
        "difficulty_score": difficulty_score,
        "difficulty_breakdown": breakdown,
        "popularity": pop,
        "day_read": day_read,
    }


def app_standing(read, app, *, itunes_service) -> tuple:
    """What belongs to one app: (its rank in the read's search, its profile
    in the storefront). (None, None) for no app. ``read`` is what
    ``read_keyword`` answered; the rank costs nothing, it is the app's place
    in the same list the competitors come from."""
    if app is None:
        return None, None

    app_rank = day_reads.rank_of(read["day_read"], app.track_id)

    # The app's own profile in this storefront (ratings, stars, age),
    # refreshed at most daily. Every score for this app here reads the same
    # cached value.
    from .app_profiles import profile_in_storefront

    return app_rank, profile_in_storefront(app, read["country"], itunes_service=itunes_service)


def score_country(kw_text, country, *, app=None, itunes_service,
                  difficulty_calc, download_est) -> dict:
    """Score one keyword in one storefront WITHOUT persisting anything.

    Raises ``SearchAPIUnavailableError`` and ``ITunesRateLimited`` to the
    caller, which decides what a failure means (a view answers 503, a
    background job records the country as not checked and carries on).

    Returns a dict with ``country``, ``competitors``, ``difficulty_score``,
    ``difficulty_breakdown``, ``popularity`` (the resolution object),
    ``app_rank`` and ``app_profile``.
    """
    read = read_keyword(kw_text, country, itunes_service=itunes_service,
                        difficulty_calc=difficulty_calc, download_est=download_est)
    app_rank, app_profile = app_standing(read, app, itunes_service=itunes_service)
    return {**read, "app_rank": app_rank, "app_profile": app_profile}


# The fields of a ``SearchResult`` that belong to the search, not the app:
# twin rows of the same day hold the same values in all of them.
SEARCH_FIELDS = (
    "popularity_score", "inferred_genre", "apple_popularity_score",
    "difficulty_score", "difficulty_breakdown", "competitors_data",
)


def row_fields(read) -> dict:
    """The ``SEARCH_FIELDS`` of a ``SearchResult``, from a read."""
    pop = read["popularity"]
    return {
        "popularity_score": pop.internal,
        "inferred_genre": pop.genre_hint,
        "apple_popularity_score": pop.apple,
        "difficulty_score": read["difficulty_score"],
        "difficulty_breakdown": read["difficulty_breakdown"],
        "competitors_data": display_snippets(read["competitors"]),
    }


def twins(keyword_obj, country):
    """The same keyword tracked in this storefront for another app, or for
    none: the rows that must show the same search."""
    from .models import Keyword

    return (
        Keyword.objects.filter(keyword__iexact=keyword_obj.keyword, results__country=country)
        .exclude(pk=keyword_obj.pk)
        .select_related("app")
        .distinct()
    )


def store_read(keyword_obj, country, fields, *, app_rank, twin_rank) -> SearchResult:
    """Write one read of a keyword in a storefront into today's row of the
    keyword, and into today's row of each of its twins with the rank
    ``twin_rank(twin_keyword)`` gives. Returns the keyword's own row.

    ``fields`` are the read's row fields (``row_fields``), without the rank.
    """
    from django.db import transaction

    with transaction.atomic():
        row = SearchResult.upsert_today(
            keyword=keyword_obj, country=country, app_rank=app_rank, **copy.deepcopy(fields),
        )
        for twin in twins(keyword_obj, country):
            SearchResult.upsert_today(
                keyword=twin, country=country, app_rank=twin_rank(twin), **copy.deepcopy(fields),
            )
    return row


def align_twin_history() -> int:
    """Give twin rows of the same day the newest read of that day, each
    keeping its own rank. Returns how many rows it changed.

    One read has written every twin since 2026-09-30 (``store_read``). Rows
    stored before, or by an older version of the app, still disagree: the
    owner's two "reduce screen time" rows showed 52 and 53 after the fix,
    and their trend charts different histories. Runs at every start
    (popularity.upgrade_stored_history) and changes nothing once rows agree.
    The read time moves with the values, so the time under each row's date
    stays the time of the read it shows. ``save()`` relabels each changed
    row for its own app.
    """
    from collections import defaultdict

    from .local_day import local_date

    days = defaultdict(list)
    for row in SearchResult.objects.values("id", "keyword_id", "keyword__keyword", "country", "searched_at"):
        # The reader's day, the day upsert_today keeps one row per keyword for.
        days[(row["keyword__keyword"].lower(), row["country"], local_date(row["searched_at"]))].append(row)

    changed = 0
    for rows in days.values():
        if len({row["keyword_id"] for row in rows}) < 2:
            continue
        newest = max(rows, key=lambda row: (row["searched_at"], row["id"]))
        source = SearchResult.objects.get(pk=newest["id"])
        values = {field: getattr(source, field) for field in SEARCH_FIELDS}
        values["searched_at"] = source.searched_at
        for row in rows:
            if row["keyword_id"] == newest["keyword_id"]:
                continue
            twin = SearchResult.objects.select_related("keyword__app").get(pk=row["id"])
            if all(getattr(twin, field) == value for field, value in values.items()):
                continue
            for field, value in values.items():
                setattr(twin, field, copy.deepcopy(value))
            twin.save()
            changed += 1
    return changed


def keep_last_read_per_day() -> int:
    """One row per keyword, storefront and day, the last read of the day, as
    ``upsert_today`` keeps it. Returns how many earlier reads it removed.

    Days used to end at midnight UTC and are now the reader's own
    (aso/local_day.py), so two stored reads can fall on one local day (in
    Sweden, 23:30 UTC and 08:00 UTC the next UTC day are the same morning).
    A trend chart would show that day twice. Runs at every start and when
    the reader's time zone changes; changes nothing once each day has one.
    """
    from collections import defaultdict

    from .local_day import local_date

    days = defaultdict(list)
    for row in SearchResult.objects.values("id", "keyword_id", "country", "searched_at"):
        days[(row["keyword_id"], row["country"], local_date(row["searched_at"]))].append(row)
    earlier = [
        row["id"]
        for rows in days.values() if len(rows) > 1
        for row in sorted(rows, key=lambda r: (r["searched_at"], r["id"]))[:-1]
    ]
    removed = 0
    for start in range(0, len(earlier), 500):
        removed += SearchResult.objects.filter(pk__in=earlier[start:start + 500]).delete()[0]
    return removed


def latest_rank(keyword_obj, country):
    """The rank the keyword's newest row in this storefront holds."""
    return (
        SearchResult.objects.filter(keyword=keyword_obj, country=country)
        .order_by("-searched_at").values_list("app_rank", flat=True).first()
    )


def score_keyword_pair(keyword_obj, country, *, app=None, itunes_service,
                       difficulty_calc, download_est, force=False) -> SearchResult:
    """Score one keyword in one country and store today's result, for it
    and for its twins (``store_read``), each with its own app's rank.

    One read serves them all (aso/day_reads.py), and every rank comes from
    it; a keyword read today anywhere in the app costs Apple nothing.
    ``force=True`` (a user's Refresh) reads it from Apple again and gives
    every twin the new read. ``app`` defaults to the keyword's own app.
    """
    if app is None:
        app = keyword_obj.app

    read = read_keyword(keyword_obj.keyword, country, itunes_service=itunes_service,
                        difficulty_calc=difficulty_calc, download_est=download_est, force=force)
    app_rank, _ = app_standing(read, app, itunes_service=itunes_service)
    return store_read(
        keyword_obj, country, row_fields(read), app_rank=app_rank,
        twin_rank=lambda twin: app_standing(read, twin.app, itunes_service=itunes_service)[0],
    )


def result_payload(search_result, app=None) -> dict:
    """The dict one dashboard result card renders, from a stored row.

    Field for field what the old blocking search answered with, so
    ``createResultCard`` / ``renderResultTabs`` / ``renderOpportunityRanking``
    and ``static/js/popularity-display.js`` need no change. ``app`` defaults
    to the keyword's own app.
    """
    if app is None:
        app = search_result.keyword.app
    pop = search_result.popularity_resolution()
    popularity = pop.effective
    return {
        "keyword": search_result.keyword.keyword,
        "country": search_result.country,
        "popularity_score": popularity,
        **popularity_fields(pop),
        "difficulty_score": search_result.difficulty_score,
        "opportunity_score": calc_opportunity(
            popularity or 0, search_result.difficulty_score, search_result.country,
            app=search_result.app_profile, app_rank=search_result.app_rank, keyword=search_result.keyword_text,
        ),
        "difficulty_label": search_result.difficulty_label,
        "difficulty_color": search_result.difficulty_color,
        "difficulty_breakdown": search_result.difficulty_breakdown,
        "competitors": display_competitors(search_result.competitors_data),
        "result_id": search_result.id,
        "app_rank": search_result.app_rank,
        "app_name": app.name if app else None,
        "app_icon": app.icon_url if app else None,
        "classification": search_result.classification,
        # Icon, wording, colours and the line under the score, composed on
        # the server so no renderer carries a copy.
        "targeting": targeting_payload(
            popularity, search_result.difficulty_score, search_result.country,
            app=search_result.app_profile, app_rank=search_result.app_rank, keyword=search_result.keyword_text,
        ),
    }
