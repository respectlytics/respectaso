"""Popularity source resolution - the single choke point for popularity.

RespectASO has two popularity sources:

  internal - PopularityEstimator's 6-signal estimate from iTunes competitor
             data (5-100). Works for every keyword in every storefront.
  apple    - Apple's own granular search popularity (5-100) from the Apple
             Ads keyword planner, synced into the AppleSearchPopularity
             table by aso.apple_ads.sync.

The user picks which source powers ALL calculations (opportunity,
classification, downloads, AI tabs, MCP). Both values are stored and
displayed side by side; exactly one - the "effective" value - feeds math.

Rules enforced here (and guarded by aso_pro/tests/test_scoring_consistency.py):
  * resolve_popularity() is the ONLY caller of PopularityEstimator.estimate().
  * Resolution math itself does no network I/O - Apple values come from the
    local table. Terms the table does not know yet are fetched synchronously
    through aso.apple_ads.sync.ensure_apple_values() BEFORE resolution, so a
    newly added keyword scores with its real Apple value (and the opportunity
    derived from it) in the same request. Loops that score many keywords must
    call prefetch_apple_values() with the whole list first - that costs one
    batched request per 100 terms instead of one request per term.
  * When the Apple source is active but a keyword has no Apple value (below
    threshold, or the fetch failed - offline, rate limited, session expired),
    the internal estimate is used with is_fallback=True (never a missing
    score) and the term is queued for the background sync.
"""

import logging
from typing import NamedTuple

from .apple_ads.storage import (  # noqa: F401 (re-exported for convenience)
    SOURCE_APPLE,
    SOURCE_INTERNAL,
    SOURCE_UNSET,
    get_popularity_source,
)

logger = logging.getLogger(__name__)


class PopularityResolution(NamedTuple):
    effective: int | None   # value that feeds ALL downstream calculations
    internal: int | None    # PopularityEstimator output
    apple: int | None       # granular Apple value (5-100), None if no data
    source: str             # "internal" | "apple" - source of `effective`
    is_fallback: bool       # True iff apple selected but this term had no data


def effective_from_pair(internal, apple, source_setting):
    """Pure resolution of (internal, apple, setting) -> (effective, source, fallback).

    Shared by resolve_popularity() and the SearchResult model properties so
    stored rows and live scoring can never disagree.
    """
    if source_setting == SOURCE_APPLE:
        if apple is not None:
            return apple, SOURCE_APPLE, False
        return internal, SOURCE_INTERNAL, True
    # Unset behaves as internal (current behavior until the user chooses).
    return internal, SOURCE_INTERNAL, False


def resolve_popularity(competitors, keyword, country, apple_lookup=None):
    """Resolve popularity for a keyword from both sources.

    Args:
        competitors: App dicts from ITunesSearchService (limit=25 canonical).
        keyword: The search keyword.
        country: Storefront country code (e.g. "us").
        apple_lookup: Optional pre-fetched {normalized_term: popularity} dict
            from AppleSearchPopularity.bulk_lookup() - pass it in loops that
            score many keywords to avoid one query per keyword.

    Returns:
        PopularityResolution.
    """
    from .services import PopularityEstimator

    internal = PopularityEstimator().estimate(competitors, keyword)
    apple = _apple_value(keyword, country, apple_lookup)
    source_setting = get_popularity_source()
    effective, source, is_fallback = effective_from_pair(
        internal, apple, source_setting
    )
    if is_fallback:
        _enqueue_missing(keyword, country)
    return PopularityResolution(
        effective=effective,
        internal=internal,
        apple=apple,
        source=source,
        is_fallback=is_fallback,
    )


POPULARITY_PROVENANCE_KEYS = (
    "popularity_internal",
    "popularity_apple",
    "popularity_source",
    "popularity_fallback",
)


def popularity_fields(resolution: PopularityResolution) -> dict:
    """Standard dual-source fields for result dicts (AI tabs, MCP, views).

    Spread into any payload whose "popularity" key carries the effective
    value, so every renderer receives both sources plus provenance.
    """
    return {
        "popularity_internal": resolution.internal,
        "popularity_apple": resolution.apple,
        "popularity_source": resolution.source,
        "popularity_fallback": resolution.is_fallback,
    }


def copy_popularity_fields(row: dict) -> dict:
    """Popularity value plus source provenance copied from a scored row dict.

    Use whenever a scored row is copied into a derived dict (score caches,
    coverage rows, combo reuse). A copy that keeps only "popularity" strips
    the ASA/EST provenance, so the renderer shows the value as an internal
    estimate with "no Apple data" even when it came from Apple.
    """
    row = row or {}
    return {
        "popularity": row.get("popularity", 0),
        **{k: row.get(k) for k in POPULARITY_PROVENANCE_KEYS},
    }


def prompt_source_note() -> str:
    """One-line source declaration for LLM prompts that include popularity.

    Keyword tables sent to the LLM stay effective-only (token economy); this
    single note tells the model which source produced those numbers. The ONE
    place this sentence is defined - all AI-tab prompt builders use it.
    """
    if get_popularity_source() == SOURCE_APPLE:
        return (
            "Popularity source: Apple Ads search popularity (5-100); keywords "
            "Apple has no value for use RespectASO's internal estimate instead. "
            "5 is the lowest value on Apple's scale and Apple's data does not "
            "differentiate between keywords reported at 5; treat clusters of 5s "
            "as limited measurement resolution, not as evidence the keywords or "
            "metadata are irrelevant. RespectASO's internal estimate may rate "
            "such keywords much higher; treat strong disagreement between the "
            "sources as uncertainty worth flagging, not as an error."
        )
    return "Popularity source: RespectASO's internal estimate (5-100)."


def refresh_rows_effective(rows, country):
    """Re-resolve stored result-row dicts under the CURRENT source setting.

    AI sessions freeze their scores at run time (correct for reports - the
    numbers must keep matching the AI's written analysis). But when a stored
    session seeds a refinement or re-simulation, its cached scores become
    inputs to a NEW run, and a run must never mix popularity sources: the
    effective value, opportunity, classification, and downloads are
    recomputed here - no network needed. Rows are copied; the stored
    session is untouched.

    Apple values prefer the local sync table over the row's snapshot: the
    table is fresher, and it covers keywords whose parent run predates the
    Apple connection. Terms Apple is still missing are queued for the next
    enrichment sync when the Apple source is active.

    Only keys a row already has are recomputed (row shapes vary per tab).
    The "source" key is keyword provenance (title/ai_generated/...), NOT the
    popularity source, and is never touched.
    """
    from .models import AppleSearchPopularity
    from .scoring import calc_opportunity, classify_keyword

    source_setting = get_popularity_source()
    terms = [
        normalize_term(row.get("keyword") or row.get("term") or "")
        for row in rows or []
        if isinstance(row, dict)
    ]
    prefetch_apple_values([t for t in terms if t], (country or "").lower())
    table_values = AppleSearchPopularity.bulk_lookup(
        [t for t in terms if t], (country or "").lower()
    )
    refreshed = []
    for row in rows or []:
        if not isinstance(row, dict):
            refreshed.append(row)
            continue
        new_row = dict(row)
        term = normalize_term(new_row.get("keyword") or new_row.get("term") or "")
        internal = new_row.get("popularity_internal", new_row.get("popularity"))
        if term in table_values:
            apple = table_values[term]
        else:
            apple = new_row.get("popularity_apple")
        effective, source, is_fallback = effective_from_pair(
            internal, apple, source_setting
        )
        if is_fallback and term:
            _enqueue_missing(term, country)
        effective_int = effective or 0
        new_row["popularity"] = effective_int
        new_row["popularity_internal"] = internal
        new_row["popularity_apple"] = apple
        new_row["popularity_source"] = source
        new_row["popularity_fallback"] = is_fallback
        difficulty = new_row.get("difficulty") or 0
        if "opportunity" in new_row:
            new_row["opportunity"] = calc_opportunity(effective_int, difficulty)
        if "classification" in new_row:
            new_row["classification"] = classify_keyword(effective_int, difficulty)
        if "downloads" in new_row:
            from .services import DownloadEstimator

            new_row["downloads"] = DownloadEstimator().estimate(
                effective_int, country=country
            )
        refreshed.append(new_row)
    return refreshed


def rows_source_used(rows) -> str:
    """Popularity source a stored run used, derived from its row dicts.

    Server-side mirror of formatRunSourceNote() in popularity-display.js:
    a run used Apple when any row was apple-sourced or fell back from it.
    Rows predating the dual-source fields derive to internal (accurate -
    those runs existed before the Apple source did).
    """
    for row in rows or []:
        if isinstance(row, dict) and (
            row.get("popularity_source") == SOURCE_APPLE
            or row.get("popularity_fallback")
        ):
            return SOURCE_APPLE
    return SOURCE_INTERNAL


def normalize_term(keyword: str) -> str:
    """Canonical keyword form for Apple lookups (matches sync-side storage)."""
    return (keyword or "").lower().strip()


def prefetch_apple_values(keywords, country) -> None:
    """Batch-fetch Apple values for keywords not yet cached locally.

    Call this before any loop that scores many keywords, so the whole list
    costs one batched Apple request per 100 terms instead of one per term.
    Always safe to call: no-op when the Apple connection is not configured,
    bounded by the shared request budget, and never raises - a failed fetch
    just leaves scoring on the internal-estimate fallback as before.
    """
    try:
        from .apple_ads.sync import ensure_apple_values

        ensure_apple_values(
            [normalize_term(k) for k in keywords or []], country
        )
    except Exception as e:  # Fetch problems must never affect scoring.
        logger.debug("Apple prefetch skipped: %s", e)


def _apple_value(keyword, country, apple_lookup):
    term = normalize_term(keyword)
    if not term:
        return None
    if apple_lookup is not None:
        return apple_lookup.get(term)
    from .models import AppleSearchPopularity

    values = AppleSearchPopularity.bulk_lookup([term], country)
    if term in values:
        return values[term]
    # No row at all: this term has never been asked of Apple. Fetch it now
    # (single batched call; no-op when the connection is not ready) so the
    # first score of a new keyword already carries the real Apple value.
    prefetch_apple_values([term], country)
    return AppleSearchPopularity.bulk_lookup([term], country).get(term)


def _enqueue_missing(keyword, country):
    """Queue a term the Apple source lacked so the next sync tick fetches it."""
    try:
        from .apple_ads.sync import enqueue_term

        enqueue_term(normalize_term(keyword), country)
    except Exception as e:  # Never let enrichment plumbing affect scoring.
        logger.debug("Apple enrichment enqueue skipped: %s", e)


def annotate_effective_popularity(queryset, name="effective_pop"):
    """Annotate a SearchResult queryset with the effective popularity.

    Database mirror of effective_from_pair(): with the Apple source active,
    the effective value is COALESCE(apple, internal); otherwise internal.
    Use this for DB-level filtering/sorting so query results always agree
    with the model properties.
    """
    from django.db.models import F
    from django.db.models.functions import Coalesce

    if get_popularity_source() == SOURCE_APPLE:
        return queryset.annotate(
            **{name: Coalesce("apple_popularity_score", "popularity_score")}
        )
    return queryset.annotate(**{name: F("popularity_score")})


def recompute_all_classifications():
    """Recompute the stored classification column for every SearchResult.

    Called when the user switches popularity source (classification is a
    stored, filterable column derived from the effective popularity) and
    after a sync patches Apple values into existing rows.
    """
    from .models import SearchResult
    from .scoring import classify_keyword

    source_setting = get_popularity_source()
    updated = 0
    batch = []
    qs = SearchResult.objects.all().only(
        "id",
        "popularity_score",
        "apple_popularity_score",
        "difficulty_score",
        "classification",
    )
    for result in qs.iterator(chunk_size=500):
        effective, _, _ = effective_from_pair(
            result.popularity_score, result.apple_popularity_score, source_setting
        )
        new_label = classify_keyword(effective or 0, result.difficulty_score)
        if new_label != result.classification:
            result.classification = new_label
            batch.append(result)
        if len(batch) >= 500:
            SearchResult.objects.bulk_update(batch, ["classification"])
            updated += len(batch)
            batch = []
    if batch:
        SearchResult.objects.bulk_update(batch, ["classification"])
        updated += len(batch)
    if updated:
        logger.info("Recomputed classification for %d results.", updated)
    return updated
