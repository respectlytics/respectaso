"""Popularity source resolution - the single choke point for popularity.

RespectASO has two popularity sources:

  internal - PopularityEstimator's signal-based estimate from iTunes
             competitor data, calibrated to Apple's 1-100 scale (estimate
             v2; see ESTIMATOR_VERSION below). Works for every keyword in
             every storefront.
  apple    - Apple's official searchPopularity1to100 (observed floor ~40)
             from the Apple Ads Platform API v1 weekly top-terms dataset,
             synced into local tables by aso.apple_ads.sync.

The user picks which source powers ALL calculations (opportunity,
classification, downloads, AI tabs, MCP). Both values are stored and
displayed side by side; exactly one - the "effective" value - feeds math.

Rules enforced here (and guarded by aso_pro/tests/test_scoring_consistency.py):
  * resolve_popularity() is the ONLY caller of PopularityEstimator.estimate().
  * Resolution math itself does no network I/O - Apple values come from the
    local AppleSearchPopularity cache, materialized from the weekly
    AppleTopTerm dataset (a table read). The ONLY synchronous network path
    is the bounded first download of a never-synced country
    (sync.ensure_country_dataset). Loops that score many keywords must
    still call prefetch_apple_values() with the whole list first - it
    materializes the cache rows in one pass.
  * When the Apple source is active but a keyword has no Apple value
    (absent from the dataset = not among its category's top 500 terms,
    or the country has no dataset), the internal estimate is used with
    is_fallback=True - never a missing score - capped just below the
    keyword's category floor (absent_cap). Absence is definitive for the
    active week; there is no enrichment queue.
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
    internal: int | None    # PopularityEstimator output (raw, never capped)
    apple: int | None       # Apple searchPopularity1to100, None if no data
    source: str             # "internal" | "apple" - source of `effective`
    is_fallback: bool       # True iff apple selected but this term had no data
    genre_hint: str = ""    # Apple genre bucket inferred from competitors
    absent_ceiling: int | None = None  # cap applied on fallback (None = uncapped)


def effective_from_pair(internal, apple, source_setting, absent_ceiling=None):
    """Pure resolution of (internal, apple, setting) -> (effective, source, fallback).

    Shared by resolve_popularity() and the SearchResult model properties so
    stored rows and live scoring can never disagree.

    absent_ceiling implements the genre-aware fallback CAP (consistency
    guarantee under the Apple source): when Apple is selected and the
    term is absent from Apple's dataset, the estimate is capped just
    below the least-reported term of the keyword's own category (its
    genre floor - 1; the country's global floor - 1 when the category is
    unknown). Absence only proves "not top-500 in its category", so the
    estimate keeps its calibrated value below that bound instead of
    being compressed. Callers obtain the ceiling from
    absent_cap(country, genre); None means no dataset for the country
    (Tier A: Apple says nothing about that storefront) and the raw
    estimate is used, exactly as under the internal source.
    """
    if source_setting == SOURCE_APPLE:
        if apple is not None:
            return apple, SOURCE_APPLE, False
        if absent_ceiling and internal is not None:
            return min(internal, absent_ceiling), SOURCE_INTERNAL, True
        return internal, SOURCE_INTERNAL, True
    # Unset behaves as internal (current behavior until the user chooses).
    return internal, SOURCE_INTERNAL, False


def absent_cap(country, genre=""):
    """Fallback cap for a keyword absent from the country's dataset.

    The cap is (its category's genre floor - 1) when the category is
    known, else (the country's global floor - 1) - fully data-driven per
    country and week, recomputed at week activation (US example, week of
    2026-08-09: SPORTS floor 40 -> cap 39 ... GAMES floor 56 -> cap 55).
    None means the country has no active dataset week (Apple Ads not
    connected, or a storefront Apple does not cover, e.g. ru) - Tier A,
    where the raw estimate stands uncapped. Never raises.
    """
    try:
        from datetime import date

        from .apple_ads import storage as apple_storage
        from .models import AppleTopTerm

        active = apple_storage.load_apple_settings()["apple_ads"][
            "active_weeks"
        ].get((country or "").lower())
        if not active:
            return None
        floor = AppleTopTerm.genre_floor(
            (country or "").lower(), date.fromisoformat(active),
            genre or None,
        )
        return max(1, floor - 1) if floor else None
    except Exception as e:  # Cap plumbing must never break scoring.
        logger.debug("Fallback cap lookup failed: %s", e)
        return None


def make_absent_cap_lookup():
    """A memoized ``absent_cap`` for loops over many stored rows: one lookup
    per (country, genre) instead of one per row. Fresh per call, so a weekly
    dataset sync is never served from a stale memo."""
    cache: dict = {}

    def lookup(country, genre):
        key = ((country or "").lower(), genre or "")
        if key not in cache:
            cache[key] = absent_cap(*key)
        return cache[key]

    return lookup


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

    from .apple_ads.genres import infer_genre

    internal = PopularityEstimator().estimate(competitors, keyword)
    apple = _apple_value(keyword, country, apple_lookup)
    source_setting = get_popularity_source()
    genre_hint = infer_genre(competitors) or ""
    ceiling = (
        absent_cap(country, genre_hint)
        if source_setting == SOURCE_APPLE else None
    )
    effective, source, is_fallback = effective_from_pair(
        internal, apple, source_setting, absent_ceiling=ceiling
    )
    return PopularityResolution(
        effective=effective,
        internal=internal,
        apple=apple,
        source=source,
        is_fallback=is_fallback,
        genre_hint=genre_hint,
        absent_ceiling=ceiling,
    )


POPULARITY_PROVENANCE_KEYS = (
    "popularity_internal",
    "popularity_apple",
    "popularity_source",
    "popularity_fallback",
    "popularity_cap",
    "popularity_genre",
)


def popularity_fields(resolution: PopularityResolution) -> dict:
    """Standard dual-source fields for result dicts (AI tabs, MCP, views).

    Spread into any payload whose "popularity" key carries the effective
    value, so every renderer receives both sources plus provenance.
    popularity_cap/popularity_genre feed the badge popover's per-row
    explanation on fallback rows (the cap applied and the display label
    of the category it came from); both are empty on non-fallback rows.
    """
    from .apple_ads.genres import genre_label

    return {
        "popularity_internal": resolution.internal,
        "popularity_apple": resolution.apple,
        "popularity_source": resolution.source,
        "popularity_fallback": resolution.is_fallback,
        "popularity_cap": (
            resolution.absent_ceiling if resolution.is_fallback else None
        ),
        "popularity_genre": (
            genre_label(resolution.genre_hint)
            if resolution.is_fallback else ""
        ),
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
            "Popularity source: Apple's official search popularity (1-100) "
            "from the Apple Ads top search terms dataset (the top 500 terms "
            "per category and storefront, roughly 500+ weekly searches). "
            "Keywords NOT among Apple's top terms are scored from "
            "RespectASO's calibrated estimate, capped just below their "
            "category's least-reported Apple value - absence from the top "
            "terms bounds a keyword's popularity, it does not make the "
            "keyword or the metadata irrelevant. Apple-reported values "
            "start around 40 on this scale. Treat strong disagreement "
            "between Apple's data and the estimate as uncertainty worth "
            "flagging, never as proof either source is wrong."
        )
    return "Popularity source: RespectASO's internal estimate (5-100)."


def apple_rank_context(rows, country) -> str:
    """Compact prompt appendix: Apple dataset context for covered keywords.

    Lists only keywords present in Apple's active dataset week, with their
    genre, rank, and tier - richer signal than the bare popularity number,
    for AI analyses run under the Apple source. Returns "" when nothing is
    covered or the dataset is unavailable. Never raises.
    """
    try:
        from datetime import date

        from .apple_ads import storage as apple_storage
        from .models import AppleTopTerm

        active = apple_storage.load_apple_settings()["apple_ads"][
            "active_weeks"
        ].get((country or "").lower())
        if not active:
            return ""
        terms = sorted({
            normalize_term(row.get("keyword") or row.get("term") or "")
            for row in rows or []
            if isinstance(row, dict)
        } - {""})
        if not terms:
            return ""
        entries = AppleTopTerm.objects.filter(
            term__in=terms,
            country=(country or "").lower(),
            week=date.fromisoformat(active),
        ).order_by("genre", "rank_in_genre").values_list(
            "term", "genre", "rank_in_genre", "popularity_tier"
        )
        lines = [
            f"{term}: {genre.replace('_', ' ').title()} rank #{rank} of 500, "
            f"tier {tier}/5"
            for term, genre, rank, tier in entries[:40]
        ]
        if not lines:
            return ""
        return (
            "Apple top-search-terms context (rank within the storefront's "
            "genre, week of " + active + "): " + "; ".join(lines)
        )
    except Exception as e:  # Prompt enrichment must never break analyses.
        logger.debug("Apple rank context skipped: %s", e)
        return ""


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
    # Dict rows carry no stored genre: the country's GLOBAL floor cap is
    # the conservative bound (DB-backed paths use the genre-aware cap).
    ceiling = (
        absent_cap(country) if source_setting == SOURCE_APPLE else None
    )
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
            internal, apple, source_setting, absent_ceiling=ceiling
        )
        effective_int = effective or 0
        new_row["popularity"] = effective_int
        new_row["popularity_internal"] = internal
        new_row["popularity_apple"] = apple
        new_row["popularity_source"] = source
        new_row["popularity_fallback"] = is_fallback
        # Dict rows carry no genre, so the popover reports the global cap.
        new_row["popularity_cap"] = ceiling if is_fallback else None
        new_row["popularity_genre"] = ""
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
    """Materialize Apple values for keywords not yet cached locally.

    Call this before any loop that scores many keywords. Since the v1
    migration this is a LOCAL operation: the weekly dataset already sits
    in AppleTopTerm, so materializing a keyword's current value is a
    table read - a term absent from the active week is definitively
    below Apple's reporting threshold and gets an explicit null row.
    The one exception is a country with no dataset at all (first use of
    a new storefront), which triggers the single bounded synchronous
    download (sync.ensure_country_dataset).

    Always safe to call: no-op when Apple is not connected, and never
    raises - any failure leaves scoring on the internal-estimate
    fallback as before.
    """
    try:
        _materialize_apple_rows(
            [normalize_term(k) for k in keywords or []], country
        )
    except Exception as e:  # Lookup problems must never affect scoring.
        logger.debug("Apple prefetch skipped: %s", e)


def _materialize_apple_rows(terms, country) -> None:
    """Ensure an AppleSearchPopularity row exists for every given term."""
    from .apple_ads import storage as apple_storage
    from .apple_ads import sync as apple_sync
    from .models import AppleSearchPopularity, AppleTopTerm

    country = (country or "").lower()
    wanted = sorted({t for t in terms if t})
    if not wanted or not country:
        return
    if not (apple_storage.apple_source_ready() and apple_storage.has_credentials()):
        return
    known = set(
        AppleSearchPopularity.objects.filter(
            term__in=wanted, country=country
        ).values_list("term", flat=True)
    )
    missing = [t for t in wanted if t not in known]
    if not missing:
        return
    active = apple_storage.load_apple_settings()["apple_ads"][
        "active_weeks"
    ].get(country)
    if not active:
        apple_sync.ensure_country_dataset(country)
        active = apple_storage.load_apple_settings()["apple_ads"][
            "active_weeks"
        ].get(country)
        if not active:
            return  # Download failed/deferred: fallback covers scoring.
    from datetime import date

    values = AppleTopTerm.values_for_week(
        missing, country, date.fromisoformat(active)
    )
    for term in missing:
        AppleSearchPopularity.objects.update_or_create(
            term=term,
            country=country,
            defaults={"popularity": values.get(term)},
        )


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
    # No row yet: materialize it from the local dataset (a table read;
    # only a never-synced country costs a bounded download) so the first
    # score of a new keyword already carries the real Apple value.
    prefetch_apple_values([term], country)
    return AppleSearchPopularity.bulk_lookup([term], country).get(term)


def annotate_effective_popularity(queryset, name="effective_pop"):
    """Annotate a SearchResult queryset with the effective popularity.

    Database mirror of effective_from_pair(), INCLUDING the genre-aware
    fallback cap: with the Apple source active, rows with an Apple value
    use it, and rows without one use the internal estimate capped just
    below their inferred category's genre floor (the country's global
    floor when uninferred). Countries without a dataset (Tier A) keep
    the raw estimate. Use this for DB-level filtering/sorting so query
    results always agree with the model properties.
    """
    from django.db.models import Case, F, IntegerField, When
    from django.db.models.functions import Least

    if get_popularity_source() != SOURCE_APPLE:
        return queryset.annotate(**{name: F("popularity_score")})

    pairs = (
        queryset.values_list("country", "inferred_genre")
        .distinct().order_by()
    )
    capped_whens = []
    country_defaults = {}
    for country, genre in pairs:
        if country not in country_defaults:
            country_defaults[country] = absent_cap(country)
        if genre:
            ceiling = absent_cap(country, genre)
            if ceiling:
                capped_whens.append(When(
                    country=country, inferred_genre=genre,
                    then=Least(F("popularity_score"), ceiling),
                ))
    # Genre-specific Whens first (Case is first-match-wins), then the
    # per-country global-floor default, then Tier A raw.
    for country, ceiling in country_defaults.items():
        if ceiling:
            capped_whens.append(When(
                country=country,
                then=Least(F("popularity_score"), ceiling),
            ))
    fallback_expr = (
        Case(*capped_whens, default=F("popularity_score"),
             output_field=IntegerField())
        if capped_whens else F("popularity_score")
    )
    return queryset.annotate(**{name: Case(
        When(apple_popularity_score__isnull=False,
             then=F("apple_popularity_score")),
        default=fallback_expr,
        output_field=IntegerField(),
    )})


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
    ceilings: dict[tuple, int | None] = {}
    qs = SearchResult.objects.all().only(
        "id",
        "popularity_score",
        "apple_popularity_score",
        "difficulty_score",
        "classification",
        "country",
        "inferred_genre",
    )
    for result in qs.iterator(chunk_size=500):
        if source_setting == SOURCE_APPLE:
            key = (result.country, result.inferred_genre)
            if key not in ceilings:
                ceilings[key] = absent_cap(
                    result.country, result.inferred_genre
                )
            ceiling = ceilings[key]
        else:
            ceiling = None
        effective, _, _ = effective_from_pair(
            result.popularity_score, result.apple_popularity_score,
            source_setting, absent_ceiling=ceiling,
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


# ── Estimator versioning and history recompute ──────────────────────────

# Bumped when the estimator's calibrated logic changes in a way that
# requires re-scoring stored history. Version 2 = the 2026-08 calibration
# against Apple's official dataset (see PopularityEstimator.V2_WEIGHTS).
ESTIMATOR_VERSION = 2


def recalculate_stored_popularity() -> dict:
    """Re-score every stored SearchResult with the current estimator.

    Uses each row's own frozen competitors_data snapshot, so history
    stays true to what was observed at the time - only the estimation
    FUNCTION changes. Also stores the inferred genre bucket (feeds the
    genre-aware fallback cap) and recomputes classifications.

    Returns {"total", "rewritten", "skipped_no_competitors",
    "reclassified"}.
    """
    from .apple_ads.genres import infer_genre
    from .models import SearchResult
    from .services import PopularityEstimator

    estimator = PopularityEstimator()
    total = rewritten = skipped = 0
    batch = []
    qs = SearchResult.objects.all().only(
        "id", "popularity_score", "inferred_genre", "competitors_data",
        "keyword__keyword",
    ).select_related("keyword")
    for result in qs.iterator(chunk_size=200):
        total += 1
        competitors = result.competitors_data or []
        if not competitors:
            skipped += 1
            continue
        new_score = estimator.estimate(competitors, result.keyword.keyword)
        new_genre = infer_genre(competitors) or ""
        if (new_score != result.popularity_score
                or new_genre != result.inferred_genre):
            result.popularity_score = new_score
            result.inferred_genre = new_genre
            batch.append(result)
            rewritten += 1
        if len(batch) >= 200:
            SearchResult.objects.bulk_update(
                batch, ["popularity_score", "inferred_genre"]
            )
            batch = []
    if batch:
        SearchResult.objects.bulk_update(
            batch, ["popularity_score", "inferred_genre"]
        )
    reclassified = recompute_all_classifications()
    logger.info(
        "Estimator v%d recompute: %d/%d rows rewritten, %d reclassified.",
        ESTIMATOR_VERSION, rewritten, total, reclassified,
    )
    return {
        "total": total,
        "rewritten": rewritten,
        "skipped_no_competitors": skipped,
        "reclassified": reclassified,
    }


def maybe_upgrade_estimator_version() -> None:
    """Run the one-time history recompute when the estimator version bumps.

    Called from AsoConfig.ready() in a background thread. Idempotent:
    a stored marker in settings.json records the applied version. Fresh
    installs (no stored rows) just record the current version.
    """
    from .apple_ads import storage as apple_storage

    block = apple_storage.load_apple_settings()["apple_ads"]
    if int(block.get("est_version") or 1) >= ESTIMATOR_VERSION:
        return
    try:
        stats = recalculate_stored_popularity()
        logger.info("Estimator upgraded to v%d: %s", ESTIMATOR_VERSION, stats)
    except Exception as e:  # Never block app start; retried next boot.
        logger.error("Estimator v%d recompute failed: %s", ESTIMATOR_VERSION, e)
        return
    apple_storage.save_apple_settings(
        apple_ads={"est_version": ESTIMATOR_VERSION}
    )
