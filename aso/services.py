"""
Service classes for App Store search, keyword difficulty calculation,
and popularity estimation.

Primary data source: iTunes Search API (itunes.apple.com/search).
Fallback: App Store SSR scraping (apps.apple.com search pages) when
the iTunes API is unavailable.  Both produce identical output via
the same _parse_app() dict format.

All API calls are made from the user's local machine — no central
server is involved.
"""

import json as _json
import logging
import math
import re
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import ClassVar

import requests

from aso import countries

# The rating volume curve lives in aso/strength.py with every other factor
# curve, shared by difficulty (the field) and the opportunity score (your
# app). rating_volume_score stays importable from here.
from .strength import volume_score as rating_volume_score
from .throttle import BASE_DELAY, AdaptiveITunesRateLimiter
from .words import tokenize_words

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class ITunesAPIError(Exception):
    """Base class for App Store data retrieval errors: Apple could not be
    reached or is busy. Never "Apple has no such app": a lookup says that by
    returning None."""


class SearchAPIUnavailableError(ITunesAPIError):
    """Apple's App Store could not be reached, did not answer in time,
    answered with an error or sent an answer that could not be read. For a
    search: both the iTunes API and the App Store page (SSR) failed."""


# What a person reads when Apple's App Store does not answer: what happened
# and what to do, never which of Apple's services failed (the owner's rule:
# screens show value, not mechanics). The logs keep the detail.
APP_STORE_UNAVAILABLE = "Apple's App Store is not answering right now. Try again in a few minutes."


# Why a request was turned away when Apple said it is busy: the reason a failed
# run's report gives (aso_pro/run_failures.py). The logs keep the status.
APP_STORE_BUSY = "Apple's App Store asked RespectASO to wait before asking again."

# What a person, or an assistant over MCP, reads when Apple says it is busy:
# the twin of APP_STORE_UNAVAILABLE, with the same next step.
APP_STORE_BUSY_NOW = "Apple's App Store is busy right now. Try again in a few minutes."


class ITunesRateLimited(ITunesAPIError):
    """The iTunes API returned a rate-limit signal (429 or 503 with Retry-After;
    the Lookup also answers a burst with 403, LOOKUP_BUSY_STATUSES).

    `retry_after` is the server-suggested wait in seconds, or None if the
    server didn't include the header. Runners feed this into the adaptive
    rate limiter so the next call waits at least this long before retrying.
    """

    def __init__(self, message: str = APP_STORE_BUSY, retry_after: float | None = None):
        super().__init__(message)
        self.retry_after = retry_after


# The statuses with which Apple's Lookup says it is busy: it answers a burst
# with 403 as well as 429 and 503 (docs/development/COUNTRY_COVERAGE_PLAN.md).
LOOKUP_BUSY_STATUSES = (403, 429, 503)
# A lookup a background run depends on is asked up to this many times while
# Apple cannot be reached or is busy, paced by aso/throttle.py's limiter.
LOOKUP_ATTEMPTS = 3


_FINANCE_INTENT_TOKENS = {
    "option",
    "options",
    "trading",
    "trade",
    "stock",
    "stocks",
    "call",
    "put",
    "signal",
    "signals",
    "invest",
    "investing",
}

_FINANCE_STRONG_CONTEXT_TOKENS = {
    "finance",
    "financial",
    "stock",
    "stocks",
    "trading",
    "trade",
    "portfolio",
    "broker",
    "invest",
    "investing",
    "market",
    "markets",
    "futures",
    "derivative",
    "derivatives",
    "forex",
    "etf",
}

_TOKEN_NORMALIZATION = {
    "options": "option",
    "stocks": "stock",
    "signals": "signal",
    "markets": "market",
}


def _tokenize(text: str) -> list[str]:
    r"""Tokenize into lowercase words for robust title matching.

    Words as everywhere else in RespectASO (aso/words.py): accents, other
    scripts and their combining marks stay part of the token.
    """
    raw_tokens = tokenize_words(text)
    return [_TOKEN_NORMALIZATION.get(tok, tok) for tok in raw_tokens]


def _has_finance_intent(keyword_tokens: set[str]) -> bool:
    return bool(keyword_tokens & _FINANCE_INTENT_TOKENS)


def _has_finance_context(title_tokens: set[str], genre: str) -> bool:
    genre_lower = (genre or "").lower()
    if "finance" in genre_lower:
        return True
    return bool(title_tokens & _FINANCE_STRONG_CONTEXT_TOKENS)


def compound_form(tokens: list[str]) -> str:
    """Run-together spelling of a multi-word keyword: "scroll less" -> "scrollless".

    Apple matches "ScrollLess" and "Scroll Less" the same way, and a
    developer reading the results list sees the same keyword in both, so
    a title that runs the words together counts as using the exact
    phrase. Single-word keywords have no compound form (empty string).
    Shared with the title highlighter (aso_tags.highlight_keyword) so the
    list and the scores agree on what "in the title" means.
    """
    return "".join(tokens) if len(tokens) > 1 else ""


def _has_compound_form(kw_tokens: list[str], title_tokens: list[str]) -> bool:
    """True when a title token starts with the keyword's run-together form.

    Anchored to the start of a token so "smartapp" never matches "art app",
    while "scrolllessapp" still matches "scroll less".
    """
    compound = compound_form(kw_tokens)
    return bool(compound) and any(
        tok.startswith(compound) for tok in title_tokens
    )


def _keyword_title_evidence(keyword: str, title: str, genre: str = "") -> dict[str, float | bool]:
    """
    Match hierarchy: exact phrase > all words(any order) > partial overlap(weak).

    The exact-phrase tier also accepts the run-together spelling of the
    keyword ("ScrollLess" for "scroll less"), see compound_form().

    Returns a normalized evidence score in [0, 1] plus match flags.
    """
    kw = (keyword or "").lower().strip()
    title_lower = (title or "").lower()
    kw_token_list = _tokenize(kw)
    kw_tokens = set(kw_token_list)
    title_tokens_list = _tokenize(title_lower)
    title_tokens = set(title_tokens_list)

    if not kw_tokens or not title_tokens:
        return {
            "exact_phrase": False,
            "all_words": False,
            "partial_overlap": 0.0,
            "proximity": 0.0,
            "evidence": 0.0,
        }

    exact_phrase = bool(kw and kw in title_lower) or _has_compound_form(
        kw_token_list, title_tokens_list
    )
    all_words = all(tok in title_tokens for tok in kw_tokens)
    overlap = len(kw_tokens & title_tokens) / len(kw_tokens)

    # Proximity rewards compact all-word matches while still accepting
    # reverse order and words-in-between as strong evidence.
    proximity = 0.0
    if all_words and len(kw_tokens) > 1:
        positions = []
        for token in kw_tokens:
            for idx, title_token in enumerate(title_tokens_list):
                if title_token == token:
                    positions.append(idx)
                    break
        if positions:
            span = max(1, max(positions) - min(positions) + 1)
            proximity = min(1.0, len(kw_tokens) / span)

    # Ambiguity guard: finance-intent keywords should not get strong
    # relevance from non-finance titles (e.g. generic "call" apps).
    finance_intent = _has_finance_intent(kw_tokens)
    finance_context = _has_finance_context(title_tokens, genre)
    if finance_intent and not finance_context and (exact_phrase or all_words):
        exact_phrase = False
        all_words = False
        overlap = min(overlap, 0.5)
    if finance_intent and not finance_context and not (exact_phrase or all_words):
        overlap = 0.0

    strong_score = 0.0
    if exact_phrase:
        strong_score = 1.0
    elif all_words:
        strong_score = 0.85 + 0.15 * proximity

    partial_score = 0.0
    if not exact_phrase and not all_words and overlap > 0:
        partial_score = min(0.5, overlap * 0.5)

    return {
        "exact_phrase": exact_phrase,
        "all_words": all_words,
        "partial_overlap": overlap,
        "proximity": proximity,
        "evidence": max(strong_score, partial_score),
    }


def _is_brand_keyword(
    keyword: str, leader: dict, competitors: list[dict],
) -> tuple[bool, str | None]:
    """
    Detect whether a keyword is a brand/company name.

    Uses signals from the search results (no dictionary needed):

    Signal A — Seller name match (required):
        All keyword tokens appear in the #1 app's sellerName.
        e.g. "spotify" ∈ ["spotify", "ab"] → match.
        e.g. "nasdaq" ∈ ["nasdaq", "inc"] → match.
        e.g. "stocks" ∉ ["apple", "inc"] → no match.

    Signal B — Review disparity (required only for weak leaders):
        When the leader has < 1,000 reviews, also require that
        positions #2-5 have a median ≥ 10,000 reviews.  This
        confirms the weak leader is an Apple rank-boost, not a
        genuinely weak keyword.

    For strong leaders (≥ 1,000 reviews), Signal A alone is
    sufficient — a well-established app whose seller matches the
    keyword IS the brand (e.g. Spotify with 39M reviews).

    Returns:
        (is_brand, brand_name) — brand_name is the sellerName when
        detected, None otherwise.
    """
    kw_tokens = set(_tokenize(keyword))
    if not kw_tokens:
        return False, None

    seller = leader.get("sellerName", "")
    seller_tokens = set(_tokenize(seller))
    if not seller_tokens:
        return False, None

    # Signal A: every keyword token appears in the seller name
    if not kw_tokens.issubset(seller_tokens):
        return False, None

    # For strong leaders, seller-name match alone is definitive.
    leader_reviews = leader.get("userRatingCount", 0)
    if leader_reviews >= 1_000:
        return True, seller

    # Signal B: weak leader — also require strong field behind it
    # to confirm Apple rank-boosted the brand app.
    # Exclude same-seller apps (brand's own portfolio) from runner-up
    # assessment — they don't represent independent competition.
    leader_seller_lower = seller.strip().lower()
    independent = [
        c for c in competitors[1:]
        if c.get("sellerName", "").strip().lower() != leader_seller_lower
    ][:4]
    if not independent:
        return False, None
    runner_reviews = sorted(c.get("userRatingCount", 0) for c in independent)
    n_ru = len(runner_reviews)
    if n_ru % 2 == 1:
        median_ru = runner_reviews[n_ru // 2]
    else:
        median_ru = (runner_reviews[n_ru // 2 - 1] + runner_reviews[n_ru // 2]) / 2

    if median_ru < 10_000:
        return False, None

    return True, seller


# --------------------------------------------------------------------------- #
# Keyword Popularity Estimator
# --------------------------------------------------------------------------- #


# Leader strength, 0-30, from the strongest leading app's rating count: log
# interpolation between the points, so no count is a step.
_LEADER_BANDS = [
    (10, 1),
    (100, 5),
    (1_000, 10),
    (10_000, 17),
    (100_000, 24),
    (1_000_000, 30),
]


def _leader_band_score(max_reviews: float) -> float:
    if max_reviews <= 0:
        return 0
    if max_reviews >= 1_000_000:
        return 30
    for i, (threshold, score) in enumerate(_LEADER_BANDS):
        if max_reviews < threshold:
            if i == 0:
                return (max_reviews / threshold) * score
            prev_t, prev_s = _LEADER_BANDS[i - 1]
            ratio = math.log(max_reviews / prev_t) / math.log(threshold / prev_t)
            return prev_s + ratio * (score - prev_s)
    return 30


# How many positions the leader gate takes to fade, around the middle of the
# results. The top app always counts fully; at 25 results #13 counts half,
# #16 a quarter and #25 almost nothing.
LEADER_GATE_WIDTH = 3.0


def _leader_gate(index: int, n: int) -> float:
    """How much the app at 0-based ``index`` of ``n`` results counts as a
    possible leader, 1 for the first app, fading smoothly past the middle."""
    def logistic(i):
        return 1.0 / (1.0 + math.exp((i + 0.5 - n / 2) / LEADER_GATE_WIDTH))

    return logistic(index) / logistic(0)


class PopularityEstimator:
    """
    Estimates keyword search popularity from iTunes Search competitor data.

    Apple removed the Search Ads keyword recommendations endpoint
    (v4 deprecated 2025, v5 never included it), so we derive
    popularity from the competitor landscape that iTunes Search returns.

    Core insight: keywords with high search volume attract strong apps.
    If heavyweight players rank for a keyword, users are searching for it.

    Score range: 5–100 (matches legacy Apple Search Ads scale).

    Signals:
      1. Result count (0–25 pts) — More results = broader/popular topic
      2. Leader strength (0–30 pts) — Top apps' review counts proxy search volume
      3. Title match density (0–20 pts) — Developers optimizing = evidence of demand
      4. Market depth (0–10 pts) — Median review count across the field
      5. Keyword specificity (-5 to -30 pts) — Longer/more-specific queries
         have lower search volume; penalizes long-tail keywords
      6. Exact phrase match bonus (0–15 pts) — If many competitors have the
         exact phrase in their title, the keyword itself is a known search term
    """

    # Calibrated weights (estimate v2) - fitted 2026-08-17 against 327
    # official Apple searchPopularity1to100 values (US, week of
    # 2026-08-09) plus 90 below-top-terms negatives, via
    # `manage.py apple_estimator_study`. Validation on a 30% holdout:
    # Spearman 0.61, Pearson 0.65, MAE 8.6; brand-like subset Spearman
    # 0.71 (previous hand-tuned weights: 0.09); head-vs-tail AUC 0.98.
    # NEVER hand-tune these: refit with the study command under its
    # pre-registered gates (see scoring-principles instructions).
    V2_WEIGHTS: ClassVar[dict[str, float]] = {
        "intercept": 3.6672,
        "f_result": 1.0404,
        "f_leader": -1.9714,
        "f_title": 0.6129,
        "f_depth": 2.9001,
        "f_spec": 0.8644,
        "f_exact": -0.338,
        "x_top1_exact": 2.1709,
        "x_leader_mag": 8.1524,
    }

    def estimate(self, competitors: list[dict], keyword: str) -> int | None:
        """
        Estimate popularity for a keyword, calibrated to Apple's scale.

        The estimate is a weighted sum of observable signal components
        (see signal_components), with weights fitted against Apple's
        official search popularity values - so the output speaks the
        same 1-100 language as the Apple Ads source: reported head
        keywords land around 40+, below-top-terms keywords land lower.

        Returns:
            Estimated popularity (1-100), or None if no data.
        """
        components = self.signal_components(competitors, keyword)
        if components is None:
            return None
        raw = self.V2_WEIGHTS["intercept"] + sum(
            weight * components[name]
            for name, weight in self.V2_WEIGHTS.items()
            if name != "intercept"
        )
        return round(max(1, min(100, raw)))

    def signal_components(self, competitors: list[dict], keyword: str) -> dict | None:
        """Observable signal components feeding estimate() - and the ONLY
        feature extractor `manage.py apple_estimator_study` uses, so the
        fitted weights and the shipped code can never drift apart.

        Returns the six classic components (post-dampening, as before)
        plus two brand-aware magnitudes:
          x_top1_exact  - log10 review count of the strongest exact-title
                          match in the top 5 (separates popular brands
                          like "indeed" from unknown names)
          x_leader_mag  - log10 review count of the strongest top-half app
        or None when there are no competitors.
        """
        if not competitors:
            return None

        n = len(competitors)
        kw_lower = keyword.lower().strip()
        word_count = len(kw_lower.split()) if kw_lower else 1

        # Signal 1: Result count (0-25 points)
        # More results = broader / more-popular search term
        result_score = min(25, n * 2.5)

        # Signal 2: Leader strength (0-30 points)
        # Only consider apps in the top half - tail apps are often
        # backfill from broader terms (e.g. Pokémon GO at #24).
        # Smooth log interpolation avoids cliff effects between bands.
        top_half = competitors[: max(n // 2, 1)]
        max_reviews = max(c.get("userRatingCount", 0) for c in top_half)
        leader_score = _leader_band_score(max_reviews)

        # The same leader, found smoothly: each app counts by a gate that
        # fades over a few positions around the middle of the results instead
        # of cutting at it, so an app moving one place never takes the leader
        # with it. Measured by apple_estimator_study (candidates C and D); a
        # signal is only used when V2_WEIGHTS names it.
        smooth_leader_mag = max(
            _leader_gate(index, n) * math.log10(1 + c.get("userRatingCount", 0))
            for index, c in enumerate(competitors)
        )
        smooth_leader_score = _leader_band_score(10 ** smooth_leader_mag - 1)

        # Signal 3: Title match density (0-20 points)
        # Strong title targeting (exact/all-word) is demand evidence.
        # Also collects the strongest exact-match leader in the top 5
        # for the x_top1_exact brand signal.
        title_matches = 0
        exact_phrase_matches = 0
        relevance_sum = 0.0
        best_exact_reviews = 0
        for index, c in enumerate(competitors):
            evidence = _keyword_title_evidence(
                kw_lower,
                c.get("trackName", ""),
                c.get("primaryGenreName", ""),
            )
            relevance_sum += float(evidence["evidence"])
            if evidence["exact_phrase"]:
                title_matches += 1
                exact_phrase_matches += 1
                if index < 5:
                    best_exact_reviews = max(
                        best_exact_reviews, c.get("userRatingCount", 0)
                    )
            elif evidence["all_words"]:
                title_matches += 1
        match_ratio = title_matches / n if n > 0 else 0
        title_score = min(20, match_ratio * 40)

        # How much of the top results' weight targets the keyword, 0 to 1.
        # Each app weighs by its position (a smooth decay, so moving from #5
        # to #6 changes little) and by the log of its ratings; its title
        # counts by the graded evidence. One app changes the share only by
        # its own part, never from nothing to everything.
        share_num = share_den = 0.0
        # The brand signal, built the same way: how big the apps carrying the
        # keyword in their title are, weighted by position. Each app adds its
        # own position weight's share of its magnitude, so one title gaining
        # or losing the keyword moves it by that app's part and no more. It
        # replaces x_top1_exact, the ratings of the single strongest exact
        # match in the top five, which one title could switch from nothing to
        # everything (STRENGTH_AND_RANK_CALIBRATION_PLAN.md, D7b).
        mag_num = mag_den = 0.0
        for index, c in enumerate(competitors):
            position_weight = math.exp(-index / 3)
            magnitude = math.log10(1 + c.get("userRatingCount", 0))
            evidence = float(_keyword_title_evidence(
                kw_lower, c.get("trackName", ""), c.get("primaryGenreName", ""),
            )["evidence"])
            share_num += position_weight * magnitude * evidence
            share_den += position_weight * magnitude
            mag_num += position_weight * magnitude * evidence
            mag_den += position_weight
        exact_share = share_num / share_den if share_den > 0 else 0.0
        exact_mag = mag_num / mag_den if mag_den > 0 else 0.0

        # Signal 4: Market depth - median reviews (0-10 points)
        sorted_counts = sorted(
            c.get("userRatingCount", 0) for c in competitors
        )
        if n % 2 == 1:
            median = sorted_counts[n // 2]
        else:
            median = (
                sorted_counts[n // 2 - 1] + sorted_counts[n // 2]
            ) / 2
        depth_bands = [(10, 0.5), (100, 3), (1_000, 5), (10_000, 8), (50_000, 10)]
        if median <= 0:
            depth_score = 0
        elif median >= 50_000:
            depth_score = 10
        else:
            depth_score = 0
            for i, (threshold, score) in enumerate(depth_bands):
                if median < threshold:
                    if i == 0:
                        depth_score = (median / threshold) * score
                    else:
                        prev_t, prev_s = depth_bands[i - 1]
                        ratio = math.log(median / prev_t) / math.log(
                            threshold / prev_t
                        )
                        depth_score = prev_s + ratio * (score - prev_s)
                    break
            else:
                depth_score = 10

        # Signal 5: Keyword specificity penalty (-5 to -30 points)
        # Long-tail keywords (more words) have inherently lower volume.
        _sp_points = [(1, 0), (2, -3), (3, -8), (4, -15), (5, -22), (6, -28)]
        if word_count <= 1:
            specificity_penalty = 0
        elif word_count >= 6:
            specificity_penalty = -28
        else:
            specificity_penalty = -28
            for i in range(len(_sp_points) - 1):
                lo_w, lo_v = _sp_points[i]
                hi_w, hi_v = _sp_points[i + 1]
                if lo_w <= word_count <= hi_w:
                    t = (word_count - lo_w) / (hi_w - lo_w)
                    specificity_penalty = lo_v + t * (hi_v - lo_v)
                    break

        # Signal 6: Exact phrase match ratio (0-15 points)
        exact_ratio = exact_phrase_matches / n if n > 0 else 0
        exact_bonus = min(15, exact_ratio * 50)

        # Small-sample dampening: ratio signals are unreliable with few
        # results; smooth ramp to full strength at n = 10.
        sample_dampening = min(1.0, n / 10)
        title_score *= sample_dampening
        exact_bonus *= sample_dampening

        # Backfill-aware dampening: when few competitors carry the
        # keyword in their title, Apple padded the results - volume
        # signals count at reduced weight.
        relevance_ratio = relevance_sum / n if n > 0 else 0
        relevance = max(0.3, min(1.0, relevance_ratio * 2.6))
        result_score *= relevance
        leader_score *= relevance
        smooth_leader_score *= relevance
        depth_score *= relevance

        return {
            "f_result": result_score,
            "f_leader": leader_score,
            "f_title": title_score,
            "f_depth": depth_score,
            "f_spec": specificity_penalty,
            "f_exact": exact_bonus,
            "x_top1_exact": math.log10(1 + best_exact_reviews),
            "x_leader_mag": math.log10(1 + max_reviews),
            # The smooth replacements for x_top1_exact, measured by
            # apple_estimator_study (STRENGTH_AND_RANK_CALIBRATION_PLAN.md D7).
            # A signal is only used when V2_WEIGHTS names it.
            "x_exact_share": exact_share,
            "x_exact_mag": exact_mag,
            "x_exact_brand": exact_share * math.log10(1 + max_reviews),
            "f_leader_smooth": smooth_leader_score,
            "x_leader_mag_smooth": smooth_leader_mag,
            "x_exact_brand_smooth": exact_share * smooth_leader_mag,
        }


# --------------------------------------------------------------------------- #
# iTunes Search API
# --------------------------------------------------------------------------- #


# One App Store search per keyword (aso/day_reads.py): its first SCORING_POOL
# apps are the competitors every score reads, and the order of all of them is
# every app's rank.
RANKED_SEARCH_LIMIT = 200
SCORING_POOL = 25


@dataclass(frozen=True)
class RankedSearch:
    """One App Store search for a keyword in a storefront.

    ranked_ids    every app's trackId in Apple's order, up to 200
    apps          the first SCORING_POOL of them as full app dicts, in the
                  same order: apps[i] is the app at ranked_ids[i]
    result_count  how many apps the search returned (len(ranked_ids))
    source        "itunes", or "appstore_ssr" when the fallback answered
    """

    ranked_ids: list
    apps: list
    result_count: int
    source: str


class ITunesSearchService:
    """
    Searches for iOS apps by keyword.

    Primary: iTunes Search API (fast, lightweight JSON).
    Fallback: App Store SSR scraping — extracts ranked app IDs from the
    web search page, then batch-fetches full data via the Lookup API.

    Both paths produce identical output (same _parse_app() dict format,
    same number of results) so scoring is deterministic regardless of
    which data source was used.

    No authentication required. Calls are made from the user's local network.
    """

    SEARCH_URL = "https://itunes.apple.com/search"
    LOOKUP_URL = "https://itunes.apple.com/lookup"
    SSR_SEARCH_URL = "https://apps.apple.com/{country}/iphone/search"

    _SSR_HEADERS: ClassVar[dict[str, str]] = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) "
            "Version/17.0 Safari/605.1.15"
        ),
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": "en-US,en;q=0.9",
    }

    # Timestamp of last SSR request — rate-limit to 1 req/sec minimum.
    _last_ssr_request: float = 0.0

    def lookup_by_id(
        self, track_id: int, country: str = "us", timeout: int = 30, *, retry: bool = True,
    ) -> dict | None:
        """
        Look up a single app by its iTunes trackId. The one lookup of one app
        that every feature uses (lookup_full_description reads it too).

        Returns the app dict, or None when Apple answered and has no app with
        this ID in this storefront. Raises ITunesRateLimited when Apple says
        it is busy and SearchAPIUnavailableError when it cannot be reached,
        does not answer in time or answers with an error, so no caller takes
        a moment of trouble at Apple for a missing app. Until 2.28.1 every
        failure returned None, and the AI Competitor reported a valid ID
        (Forest, 866450515) as "not found" and blamed the AI service.

        ``retry`` (the default, for background runs): a failed call is asked
        again, up to LOOKUP_ATTEMPTS calls in all, paced by the adaptive
        limiter every App Store loop uses (aso/throttle.py, which honours
        Retry-After), and an empty answer is asked again once after a short
        pause, because Apple's Lookup sometimes answers empty for a moment
        for an app it lists. ``retry=False`` is for a request a person waits
        on: one call, with a shorter ``timeout`` when the caller would rather
        give up than keep the user waiting.
        """
        attempts = LOOKUP_ATTEMPTS if retry else 1
        empty_asks = 1 if retry else 0
        limiter = AdaptiveITunesRateLimiter()
        attempt = 0
        while True:
            attempt += 1
            try:
                found = self.lookup_raw_chunk([track_id], country=country, timeout=timeout)
            except ITunesAPIError as exc:
                if attempt >= attempts:
                    logger.warning("iTunes lookup of %s (%s) failed: %s", track_id, country, exc)
                    raise
                limiter.record_failure(retry_after=getattr(exc, "retry_after", None))
                logger.info("iTunes lookup of %s (%s) failed (%s), asking again in %.1fs",
                            track_id, country, exc, limiter.current_delay)
                limiter.wait()
                continue
            raw = found.get(int(track_id))
            if raw is not None:
                return self._parse_app(raw)
            if empty_asks <= 0:
                return None
            empty_asks -= 1
            logger.info("iTunes lookup of %s (%s) answered empty, asking once more", track_id, country)
            time.sleep(BASE_DELAY)

    def lookup_full_description(self, track_id: int, country: str = "us") -> dict:
        """Look up an app and return its description, genre, and context metadata.

        Returns a dict with keys: description, genre, rating, rating_count,
        release_date, update_date, price, version, seller. The description is
        optional for its callers, so when Apple has no such app or cannot be
        reached (after lookup_by_id's retries) the defaults stand in.
        """
        defaults = {
            "description": "",
            "genre": "",
            "rating": 0,
            "rating_count": 0,
            "release_date": "",
            "update_date": "",
            "price": "Free",
            "version": "",
            "seller": "",
        }
        try:
            app = self.lookup_by_id(track_id, country=country)
        except ITunesAPIError:
            return defaults
        if not app:
            return defaults
        return {
            "description": app.get("description", ""),
            "genre": app.get("primaryGenreName", ""),
            "rating": app.get("averageUserRating", 0),
            "rating_count": app.get("userRatingCount", 0),
            "release_date": app.get("releaseDate", ""),
            "update_date": app.get("currentVersionReleaseDate", ""),
            "price": app.get("formattedPrice", "Free"),
            "version": app.get("version", ""),
            "seller": app.get("sellerName", ""),
        }

    def lookup_raw_chunk(self, track_ids, country: str = "us", timeout: int = 30) -> dict[int, dict]:
        """One Lookup API call for up to 50 apps: {trackId: Apple's result as
        Apple sent it}, with every field (version, release notes, screenshots,
        the full description). The one call to Apple's Lookup behind every
        lookup in the app.

        An app Apple does not list is left out. Raises ITunesRateLimited when
        Apple says it is busy (LOOKUP_BUSY_STATUSES, with its Retry-After),
        and SearchAPIUnavailableError when it cannot be reached, does not
        answer in time, answers with another error or sends something that
        is not its JSON: a caller with a rate limiter paces itself, and no
        caller takes a failure for an empty answer.
        """
        ids = [int(t) for t in track_ids][:50]
        if not ids:
            return {}
        try:
            response = requests.get(
                self.LOOKUP_URL,
                params={"id": ",".join(str(t) for t in ids), "country": country},
                timeout=timeout,
            )
        except requests.Timeout as exc:
            raise SearchAPIUnavailableError("Apple's App Store did not answer in time.") from exc
        except requests.RequestException as exc:
            raise SearchAPIUnavailableError("Apple's App Store could not be reached.") from exc
        if response.status_code in LOOKUP_BUSY_STATUSES:
            logger.info("Lookup API answered %s (Retry-After=%s)",
                        response.status_code, response.headers.get("Retry-After"))
            raise ITunesRateLimited(
                retry_after=self._parse_retry_after(response.headers.get("Retry-After")),
            )
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            logger.warning("Lookup API answered %s", response.status_code)
            raise SearchAPIUnavailableError("Apple's App Store answered with an error.") from exc
        try:
            results = response.json().get("results", [])
        except (ValueError, AttributeError) as exc:
            raise SearchAPIUnavailableError(
                "Apple's App Store sent an answer that could not be read.",
            ) from exc
        found: dict[int, dict] = {}
        for raw in results:
            track_id = raw.get("trackId") if isinstance(raw, dict) else None
            if track_id:
                found[int(track_id)] = raw
        return found

    _APP_STORE_ID = re.compile(r"/id(\d+)")
    _APP_STORE_COUNTRY = re.compile(r"apps\.apple\.com/([a-z]{2})/", re.IGNORECASE)

    @classmethod
    def storefront_in_link(cls, text: str) -> str | None:
        """The storefront an App Store link names ("se" for
        apps.apple.com/se/app/...), or None when it names none."""
        match = cls._APP_STORE_COUNTRY.search(text or "")
        return match.group(1).lower() if match else None

    def find_apps(self, query: str, country: str = "us", limit: int = 5) -> list[dict]:
        """The apps a person means by what they typed. An App Store link or
        a bare App Store ID finds that one app (in the link's own storefront
        when it names one); anything else is an App Store search. Parsed app
        dicts, best match first; [] when nothing matches.

        A person waits on the answer, so a lookup is one call (no retries).
        Raises ITunesAPIError (SearchAPIUnavailableError or ITunesRateLimited)
        when Apple cannot be reached or is busy, never [] for that.
        """
        text = (query or "").strip()
        if len(text) < 2:
            return []
        match = self._APP_STORE_ID.search(text)
        if match or text.isdigit():
            track_id = int(match.group(1) if match else text)
            found = self.lookup_by_id(
                track_id, country=self.storefront_in_link(text) or country, retry=False,
            )
            return [found] if found else []
        return self.search_apps(text, country=country, limit=limit)

    # ── Primary: iTunes Search API ──────────────────────────────────────

    def _search_itunes(
        self, keyword: str, country: str = "us", limit: int = 10
    ) -> list[dict]:
        """Search via iTunes API. Tighter timeout + 429/503 detection +
        single in-line retry on transient errors before giving up.

        Raises:
            ITunesRateLimited: when the response is 429 or 503. Carries the
                server's `Retry-After` value if present.
            HTTPError / RequestException: on other failures (caller falls
                back to SSR scraping).
        """
        params = {
            "term": keyword,
            "country": country,
            "entity": "software",
            "limit": limit,
        }
        # Two attempts: the second one only fires on transient (5xx) errors,
        # waiting Retry-After (or 5s default) before retrying. Permanent
        # errors (4xx) raise immediately so SSR fallback can take over.
        for attempt in range(2):
            # A timeout raises at once, with no second attempt: it usually
            # means Apple's API is overloaded, and the caller's SSR fallback
            # may answer sooner.
            response = requests.get(self.SEARCH_URL, params=params, timeout=15)

            status = response.status_code
            if status in (429, 503):
                retry_after = self._parse_retry_after(response.headers.get("Retry-After"))
                # First attempt: wait Retry-After (capped at 5s) and try once more.
                if attempt == 0:
                    wait = min(5.0, retry_after) if retry_after is not None else 2.0
                    logger.info(
                        "iTunes API returned %d; waiting %.1fs before retry "
                        "(Retry-After=%s, keyword=%r)",
                        status, wait, retry_after, keyword,
                    )
                    time.sleep(wait)
                    continue
                # Second attempt also rate-limited: surface it so the runner
                # can record the failure on the limiter and consider SSR. The
                # message is the plain one a failed run's report gives.
                logger.info("iTunes API returned %d again (keyword=%r)", status, keyword)
                raise ITunesRateLimited(retry_after=retry_after)

            # Any other 5xx: retry once with a brief pause; otherwise raise.
            if 500 <= status < 600:
                if attempt == 0:
                    time.sleep(1.5)
                    continue
                response.raise_for_status()  # raises HTTPError

            response.raise_for_status()
            data = response.json()
            return [self._parse_app(r) for r in data.get("results", [])]

        # Should be unreachable — both attempts return or raise above.
        raise SearchAPIUnavailableError(APP_STORE_UNAVAILABLE)

    @staticmethod
    def _parse_retry_after(value: str | None) -> float | None:
        """Parse the HTTP `Retry-After` header.

        The header may be either a delta in seconds (e.g. "30") or an
        HTTP-date. We support the delta form (the common case for iTunes);
        if it's an HTTP-date we conservatively return None (caller falls
        back to its own backoff).
        """
        if not value:
            return None
        try:
            return float(value.strip())
        except (TypeError, ValueError):
            return None

    # ── Fallback: App Store SSR ─────────────────────────────────────────

    def _fetch_ssr_page(self, keyword: str, country: str = "us") -> dict:
        """Fetch the App Store search page and extract the embedded JSON.

        Returns the parsed top-level dict from the serialized-server-data
        script tag.  Retries up to 3 times with exponential backoff on
        transient failures (5xx, timeouts, connection errors).
        """
        max_retries = 3
        last_exc: Exception | None = None

        for attempt in range(max_retries):
            # Rate limit: at least 1 second between SSR requests
            elapsed = time.time() - self.__class__._last_ssr_request
            if elapsed < 1.0:
                time.sleep(1.0 - elapsed)

            url = self.SSR_SEARCH_URL.format(country=country.lower())
            try:
                response = requests.get(
                    url,
                    params={"term": keyword},
                    headers=self._SSR_HEADERS,
                    timeout=30,
                )
                self.__class__._last_ssr_request = time.time()

                # Don't retry client errors (4xx) — they won't succeed
                if 400 <= response.status_code < 500:
                    response.raise_for_status()

                response.raise_for_status()

                # Extract JSON from <script id="serialized-server-data">
                match = re.search(
                    r'<script[^>]*id="serialized-server-data"[^>]*>(.*?)</script>',
                    response.text,
                    re.DOTALL,
                )
                if not match:
                    raise ITunesAPIError(
                        "App Store SSR: serialized-server-data script tag not found"
                    )
                return _json.loads(match.group(1))

            except (requests.exceptions.ConnectionError,
                    requests.exceptions.Timeout) as e:
                last_exc = e
                if attempt < max_retries - 1:
                    delay = 2 ** attempt  # 1s, 2s
                    logger.warning(
                        f"SSR fetch attempt {attempt + 1} failed "
                        f"(connection/timeout), retrying in {delay}s: {e}"
                    )
                    time.sleep(delay)
            except requests.exceptions.HTTPError as e:
                if response.status_code >= 500 and attempt < max_retries - 1:
                    delay = 2 ** attempt
                    logger.warning(
                        f"SSR fetch attempt {attempt + 1} got {response.status_code}, "
                        f"retrying in {delay}s"
                    )
                    time.sleep(delay)
                    last_exc = e
                else:
                    raise

        raise last_exc  # type: ignore[misc]

    @staticmethod
    def _extract_ssr_app_ids(ssr_data: dict) -> list[int]:
        """Extract an ordered list of app IDs from SSR JSON.

        Combines lockup items (top shelf) with nextPage results,
        preserving Apple's search ranking order.  Deduplicates by ID.
        """
        app_ids: list[int] = []
        seen: set[int] = set()

        try:
            inner = ssr_data["data"][0]["data"]
        except (KeyError, IndexError, TypeError):
            return app_ids

        # 1. Lockup items from shelves (top ~12 results)
        for shelf in inner.get("shelves", []):
            for item in shelf.get("items", []):
                lockup = item.get("lockup", {})
                adam_id = lockup.get("adamId")
                if adam_id and int(adam_id) not in seen:
                    app_ids.append(int(adam_id))
                    seen.add(int(adam_id))

        # 2. nextPage results — type "apps" only (skip editorial, bundles)
        next_page = inner.get("nextPage", {})
        for r in next_page.get("results", []):
            if r.get("type") != "apps":
                continue
            rid = r.get("id")
            if rid and int(rid) not in seen:
                app_ids.append(int(rid))
                seen.add(int(rid))

        return app_ids

    def _batch_lookup(
        self, track_ids: list[int], country: str = "us"
    ) -> dict[int, dict]:
        """Batch-fetch apps via Lookup API.  Returns {trackId: app_dict}.

        The Lookup API accepts comma-separated IDs (tested up to 200).
        We chunk in groups of 50 for reliability.
        """
        result: dict[int, dict] = {}
        chunk_size = 50

        for start in range(0, len(track_ids), chunk_size):
            chunk = track_ids[start : start + chunk_size]
            try:
                for track_id, raw in self.lookup_raw_chunk(chunk, country=country).items():
                    result[track_id] = self._parse_app(raw)
            except Exception as e:  # noqa: BLE001 (one failed chunk must not lose the others)
                logger.warning(f"Batch lookup failed for chunk: {e}")
                # Continue with next chunk: partial data is better than none

        return result

    def _search_ssr(
        self, keyword: str, country: str = "us", limit: int = 10
    ) -> list[dict]:
        """Fallback search via App Store SSR + Lookup API.

        1. Fetch SSR page → extract ordered app IDs (ranking order)
        2. Take first `limit` IDs
        3. Batch-fetch full data via Lookup API
        4. Return in ranking order with all scoring fields populated

        Raises SearchAPIUnavailableError if SSR fetch or Lookup both fail.
        """
        ssr_data = self._fetch_ssr_page(keyword, country=country)
        all_ids = self._extract_ssr_app_ids(ssr_data)

        if not all_ids:
            # SSR returned no results — this is valid (niche keyword)
            return []

        # Take first `limit` IDs and batch-fetch via Lookup API
        target_ids = all_ids[:limit]
        lookup_map = self._batch_lookup(target_ids, country=country)

        if not lookup_map:
            raise SearchAPIUnavailableError(APP_STORE_UNAVAILABLE)

        # Return in ranking order, only apps that Lookup returned
        apps = []
        for tid in target_ids:
            if tid in lookup_map:
                app_dict = lookup_map[tid]
                app_dict["_data_source"] = "appstore_ssr"
                apps.append(app_dict)

        return apps

    # ── Public API ──────────────────────────────────────────────────────

    def search_ranked(self, keyword: str, country: str = "us") -> RankedSearch:
        """The one App Store search behind every keyword score and rank.

        Asks the iTunes Search API for up to RANKED_SEARCH_LIMIT apps. The
        first SCORING_POOL are the competitors every score reads; the order
        of all of them is every app's rank, so a row's rank and its
        competitor list come from one list and never disagree. Asking Apple
        for 25 and for 200 returns different orders, and the 200 list is the
        one closer to the App Store's own (probe of 2026-09-30,
        docs/development/ONE_READ_PER_DAY_PLAN.md).

        Only aso/day_reads.py calls this: every other path reads through it,
        so a keyword costs Apple one search per storefront per day.

        Falls back to the App Store search page (ordered ids) plus one Lookup
        call for the first SCORING_POOL ids when the iTunes API fails. An id
        among those that the Lookup does not return is dropped from the
        order too, so apps[i] is always the app at ranked_ids[i].

        Raises ITunesRateLimited (no fallback: Apple would throttle the page
        too) and SearchAPIUnavailableError when both sources fail.
        """
        try:
            results = self._search_itunes(keyword, country=country, limit=RANKED_SEARCH_LIMIT)
        except ITunesRateLimited:
            raise
        except Exception as e:  # noqa: BLE001 (any failure of the API falls back to the App Store page)
            logger.warning(
                f"iTunes Search API failed for '{keyword}' ({country}), "
                f"falling back to SSR: {e}"
            )
        else:
            seen = set()
            ordered = []
            for app in results:
                track_id = app.get("trackId")
                if track_id and track_id not in seen:
                    seen.add(track_id)
                    ordered.append(app)
            apps = ordered[:SCORING_POOL]
            for app in apps:
                app["_data_source"] = "itunes"
            return RankedSearch(
                ranked_ids=[int(app["trackId"]) for app in ordered],
                apps=apps,
                result_count=len(ordered),
                source="itunes",
            )

        try:
            page_ids = self._extract_ssr_app_ids(self._fetch_ssr_page(keyword, country=country))
        except Exception as e:
            logger.error(f"SSR fallback also failed for '{keyword}' ({country}): {e}")
            raise SearchAPIUnavailableError(APP_STORE_UNAVAILABLE) from e
        page_ids = [int(track_id) for track_id in page_ids[:RANKED_SEARCH_LIMIT]]
        if not page_ids:
            return RankedSearch(ranked_ids=[], apps=[], result_count=0, source="appstore_ssr")
        head = page_ids[:SCORING_POOL]
        lookup_map = self._batch_lookup(head, country=country)
        if not lookup_map:
            raise SearchAPIUnavailableError(APP_STORE_UNAVAILABLE)
        apps = []
        for track_id in head:
            app = lookup_map.get(track_id)
            if app is not None:
                app["_data_source"] = "appstore_ssr"
                apps.append(app)
        ranked_ids = [int(app["trackId"]) for app in apps] + page_ids[SCORING_POOL:]
        logger.info(f"SSR fallback returned {len(ranked_ids)} apps for '{keyword}' ({country})")
        return RankedSearch(
            ranked_ids=ranked_ids, apps=apps, result_count=len(ranked_ids), source="appstore_ssr",
        )

    def search_apps(
        self, keyword: str, country: str = "us", limit: int = 10
    ) -> list[dict]:
        """
        Search for iOS apps matching a keyword.

        Not for keyword scores or ranks: those read through
        aso.day_reads.get_or_fetch (one search per keyword, storefront and
        day). This serves the app name search (views.app_lookup_view), the
        storefront probe and the rank calibration study.

        Tries the iTunes Search API first.  If it fails (HTTP error,
        timeout, etc.), falls back to App Store SSR scraping + Lookup
        API batch fetch.  If both sources fail, raises
        SearchAPIUnavailableError.

        Args:
            keyword: The search term.
            country: Two-letter country code (default: us).
            limit: Max results to return (default: 10).

        Returns:
            List of dicts with app data (same format regardless of source).

        Raises:
            SearchAPIUnavailableError: When both data sources are down.
            ITunesRateLimited: When Apple says it is busy (no fallback).
        """
        # Try primary source: iTunes Search API
        try:
            apps = self._search_itunes(keyword, country=country, limit=limit)
            for app in apps:
                app["_data_source"] = "itunes"
            return apps
        except ITunesRateLimited:
            # Rate-limit signal must propagate so the runner's adaptive
            # limiter records it and waits the right amount of time before
            # the next call. SSR isn't a sensible fallback here — Apple
            # would rate-limit the SSR endpoint too.
            raise
        except Exception as e:  # noqa: BLE001 (any failure of the API falls back to the App Store page)
            logger.warning(
                f"iTunes Search API failed for '{keyword}' ({country}), "
                f"falling back to SSR: {e}"
            )

        # Fallback: App Store SSR + Lookup API
        try:
            apps = self._search_ssr(keyword, country=country, limit=limit)
            logger.info(
                f"SSR fallback returned {len(apps)} apps for '{keyword}' ({country})"
            )
            return apps
        except SearchAPIUnavailableError:
            raise
        except Exception as e:
            logger.error(
                f"SSR fallback also failed for '{keyword}' ({country}): {e}"
            )
            raise SearchAPIUnavailableError(APP_STORE_UNAVAILABLE) from e

    @staticmethod
    def _parse_app(result: dict) -> dict:
        """Parse an iTunes API result into a standardized app dict.

        The description stays whole, because the AI tabs read it. The one
        place that shortens it is ``display_snippet``, which makes the form
        stored beside keywords for the screens.
        """
        return {
            "trackId": result.get("trackId"),
            "trackName": result.get("trackName", ""),
            "artworkUrl100": result.get("artworkUrl100", ""),
            "averageUserRating": result.get("averageUserRating", 0),
            "userRatingCount": result.get("userRatingCount", 0),
            "releaseDate": result.get("releaseDate", ""),
            "currentVersionReleaseDate": result.get(
                "currentVersionReleaseDate", ""
            ),
            "primaryGenreName": result.get("primaryGenreName", ""),
            "formattedPrice": result.get("formattedPrice", "Free"),
            "description": result.get("description", ""),
            "version": result.get("version", ""),
            "sellerName": result.get("sellerName", ""),
            "bundleId": result.get("bundleId", ""),
            "trackViewUrl": result.get("trackViewUrl", ""),
        }


# --------------------------------------------------------------------------- #
# Display form of an app, for storage beside keywords
# --------------------------------------------------------------------------- #

# The keys a stored app dict has always carried: the fields _parse_app
# returned until it kept the full description, plus the "_data_source" tag
# search_apps adds. Keys the parser gains (such as "version") stay out of
# storage unless a screen needs them.
STORED_APP_KEYS = (
    "trackId",
    "trackName",
    "artworkUrl100",
    "averageUserRating",
    "userRatingCount",
    "releaseDate",
    "currentVersionReleaseDate",
    "primaryGenreName",
    "formattedPrice",
    "description",
    "sellerName",
    "bundleId",
    "trackViewUrl",
    "_data_source",
)

DESCRIPTION_SNIPPET_CHARS = 200


def display_snippet(app):
    """The form of ``app`` that is stored beside a keyword for the screens.

    A new dict with the ``STORED_APP_KEYS`` of ``app`` and its description
    cut to ``DESCRIPTION_SNIPPET_CHARS`` characters plus "...". No screen
    shows a stored description, and 25 full descriptions per keyword per day
    would grow the database about twentyfold, so storage keeps the form it
    always had while the AI reads every word. Never changes ``app``;
    applying it twice gives the same result as once; anything that is not a
    dict comes back as it is.
    """
    if not isinstance(app, dict):
        return app
    snippet = {key: app[key] for key in STORED_APP_KEYS if key in app}
    description = snippet.get("description")
    if isinstance(description, str) and len(description) > DESCRIPTION_SNIPPET_CHARS:
        snippet["description"] = description[:DESCRIPTION_SNIPPET_CHARS] + "..."
    return snippet


def display_snippets(apps):
    """``display_snippet`` for every app of a list; an empty list or None comes back as it is."""
    if not apps:
        return apps
    return [display_snippet(app) for app in apps]


# --------------------------------------------------------------------------- #
# Download Estimator
# --------------------------------------------------------------------------- #


class DownloadEstimator:
    """
    Estimates daily downloads per ranking position for a keyword.

    Combines three models:
      1. Popularity → estimated daily searches (derived from industry
         benchmarks on Apple Search Ads popularity scores).
      2. Position → tap-through rate (TTR) — percentage of searchers who
         tap on the result at each position.  Follows a well-documented
         power-law decay curve.
      3. Conversion rate: the share of taps that become installs, 5%.

    Every figure is shown as a range from a tenth of the estimate to the
    estimate (RANGE_LOW_SHARE below).
    """

    # Popularity → estimated daily searches: the canonical curve lives
    # in aso.scoring.POP_TO_SEARCHES (threshold-anchored to Apple's
    # official dataset; see the derivation comment there). Both
    # popularity sources speak the same calibrated 1-100 scale since
    # estimate v2, so one curve serves everything - no source switching.

    def _daily_searches(self, popularity: int) -> float:
        """Interpolate daily search volume from popularity score."""
        from .scoring import POP_TO_SEARCHES

        if popularity is None or popularity <= 0:
            return 0
        pts = POP_TO_SEARCHES
        if popularity <= pts[0][0]:
            return pts[0][1] * (popularity / pts[0][0])
        if popularity >= pts[-1][0]:
            return pts[-1][1]
        for i in range(1, len(pts)):
            p0, s0 = pts[i - 1]
            p1, s1 = pts[i]
            if popularity <= p1:
                ratio = (popularity - p0) / (p1 - p0)
                return s0 + ratio * (s1 - s0)
        return pts[-1][1]

    # Position → tap-through rate (fraction of searchers who tap).
    #
    # App Store search shows 2–3 full app cards per screen (icon,
    # title, subtitle, screenshots, GET button).  Position #1 is
    # always fully visible and dominates attention.
    #
    # References:
    #   - Apple Search Ads average TTR ~7.5 % for *paid* placements;
    #     organic #1 should be well above that.
    #   - Google web-search #1 CTR is 27–32 % with plain text links;
    #     App Store's visual cards give #1 even more prominence.
    #   - ASO industry studies (Phiture, StoreMaven) report 25–50 %
    #     engagement for the top organic result.
    #
    # Decay follows a power-law: steep drop from #1 to #5 (all on
    # first screen), then gradual tail for positions requiring scroll.
    _TTR: ClassVar[dict[int, float]] = {
        1: 0.30,
        2: 0.18,
        3: 0.12,
        4: 0.085,
        5: 0.060,
        6: 0.045,
        7: 0.033,
        8: 0.025,
        9: 0.019,
        10: 0.013,
        11: 0.009,
        12: 0.007,
        13: 0.0055,
        14: 0.0042,
        15: 0.0033,
        16: 0.0025,
        17: 0.0019,
        18: 0.0014,
        19: 0.0010,
        20: 0.0007,
    }

    # Install rate (tap to install) behind every estimate: 5%, the low end of
    # what published studies report for free apps (an unknown app with a weak
    # listing). Every score and tag is built on it.
    _CVR_LOW = 0.05

    # Every download range runs from a tenth of the estimate to the estimate.
    # Two things RespectASO cannot know move real downloads that much: how
    # many people search terms Apple does not report (its dataset only says
    # they are below its reporting floor), and how many of the people who see
    # the app install it. Checked on 2026-09-27 against one app's App Store
    # Connect figures (Options Trading AI, US, June 28 to September 25): about
    # 14 search downloads a day estimated, about 2 a day real from every
    # source. It replaced a 5% to 20% install range, whose high end sat four
    # times further from that app than the estimate.
    RANGE_LOW_SHARE = 0.1

    # Market size scales search volumes relative to the US App Store, which
    # is what POP_TO_SEARCHES is calibrated for. The factor per storefront
    # lives in aso/countries.py, the one place that defines a country: 48 of
    # them were measured against observed App Store data, the rest are derived
    # from population and regional iOS share. A storefront that is genuinely
    # tiny gets a tiny number rather than one shared default, which used to
    # credit Andorra with roughly five million iPhones.

    # Past #20 the tap-through rate keeps falling as a power law of the rank,
    # with the exponent the table itself shows between #10 and #20
    # (log(TTR10/TTR20) / log(2), about 4.2). #40 then gets about 5% of what
    # #20 gets and #100 about 0.1%: little, but never a cliff to zero.
    TAIL_EXPONENT = math.log(_TTR[10] / _TTR[20]) / math.log(20 / 10)

    @classmethod
    def ttr_at(cls, rank: float) -> float:
        """Share of searchers who tap the result at ``rank``, for any real rank.

        Whole ranks 1 to 20 return exactly the table. Between them the rate
        is interpolated on a log scale, and past #20 it follows the power law
        above, so the curve is continuous from #1 to any depth and every
        figure built on it moves smoothly.
        """
        if rank <= 1:
            return cls._TTR[1]
        if rank <= 20:
            low = math.floor(rank)
            if low >= 20:
                return cls._TTR[20]
            fraction = rank - low
            if fraction == 0:
                return cls._TTR[low]
            return math.exp(
                math.log(cls._TTR[low]) * (1 - fraction)
                + math.log(cls._TTR[low + 1]) * fraction
            )
        return cls._TTR[20] * (20 / rank) ** cls.TAIL_EXPONENT

    def _downloads(self, searches: float, position: float, cvr: float) -> float:
        """Downloads a day at one rank. The one place this is multiplied.

        Searches x how many searchers tap that rank x how many of those
        install. Everything that quotes a download figure, the chart, the
        table and the opportunity score, comes through here.
        """
        return searches * self.ttr_at(position) * cvr

    def downloads_at(
        self, popularity: int, rank: float, country: str = "us",
    ) -> float:
        """The conservative daily downloads at one rank, UNROUNDED.

        What the opportunity score is built on. ``rank`` is a real number
        (the reachable rank is), and any depth is allowed: past #20 the
        downloads keep shrinking along ttr_at() instead of dropping to zero.
        estimate() rounds the same figures at whole ranks 1 to 20 for the
        chart, which is right for a chart and wrong for a score.
        """
        from .scoring import daily_searches

        if not popularity or popularity <= 0 or rank < 1:
            return 0.0
        return self._downloads(
            daily_searches(popularity, country or "us"), rank, self._CVR_LOW,
        )

    def range_at(
        self, popularity: int, rank: float, country: str = "us",
    ) -> tuple[float, float]:
        """Daily downloads at one rank, low and high, UNROUNDED: the range
        estimate() rounds into its positions, for a sentence that quotes it."""
        from .scoring import daily_searches

        if not popularity or popularity <= 0 or rank < 1:
            return 0.0, 0.0
        estimate = self._downloads(daily_searches(popularity, country or "us"), rank, self._CVR_LOW)
        return estimate * self.RANGE_LOW_SHARE, estimate

    def estimate(
        self,
        popularity: int,
        country: str = "us",
    ) -> dict:
        """
        Estimate daily downloads for each position 1–20.

        Model: Downloads = Searches × TTR(position) × CVR

        Returns dict with:
          - daily_searches: estimated daily searches for this keyword
          - positions: list of dicts with pos, ttr, downloads_low,
            downloads_high for positions 1–20
          - tiers: summary for Top 5, Top 6-10, Top 11-20
        """
        from .scoring import daily_searches

        entry = countries.get(country or "us")
        market_source = entry.market_source if entry else "derived"
        # One place does popularity times market size, so the number behind a
        # download range and the number behind a classification are the same.
        searches = daily_searches(popularity, country or "us")

        positions = []
        for pos in range(1, 21):
            ttr = self._TTR.get(pos, 0.001)
            dl_high = self._downloads(searches, pos, self._CVR_LOW)
            dl_low = dl_high * self.RANGE_LOW_SHARE
            positions.append({
                "pos": pos,
                "ttr": round(ttr * 100, 2),
                "downloads_low": round(dl_low, 2),
                "downloads_high": round(dl_high, 2),
            })

        # Tier summaries (average daily downloads across positions in tier)
        def _tier_avg(start, end):
            subset = [p for p in positions if start <= p["pos"] <= end]
            if not subset:
                return {"low": 0, "high": 0}
            return {
                "low": round(sum(p["downloads_low"] for p in subset) / len(subset), 2),
                "high": round(sum(p["downloads_high"] for p in subset) / len(subset), 2),
            }

        tiers = {
            "top_5": _tier_avg(1, 5),
            "top_6_10": _tier_avg(6, 10),
            "top_11_20": _tier_avg(11, 20),
        }

        return {
            "daily_searches": round(searches, 2),
            "positions": positions,
            "tiers": tiers,
            # Two honesty flags the renderers read. `below_threshold` marks a
            # market so small that the keyword sees under one search a day,
            # where a download range of zeros would read as a bug rather than
            # as a fact. `market_source` says whether the market size behind
            # these numbers was measured or derived.
            "below_threshold": searches < 1.0,
            "market_source": market_source,
        }


# --------------------------------------------------------------------------- #
# Keyword Difficulty Calculator
# --------------------------------------------------------------------------- #


# The weak leader cap: a #1 app with few reviews means the keyword is easy,
# whatever backfill sits below it. Full strength below LEADER_CAP_FULL
# reviews, fading out on a log scale to nothing at LEADER_CAP_GONE, so a
# leader crossing 1,000 reviews no longer lifts difficulty by up to 20
# points in one step.
LEADER_CAP_FULL = 1_000
LEADER_CAP_GONE = 3_000


def leader_cap(leader_reviews: float) -> float:
    """The ceiling a weak #1 app puts on difficulty: 15 at 0 reviews, 50 at
    about 1,000, rising slowly beyond."""
    return 15 + 35 * math.log10(leader_reviews + 1) / math.log10(1001)


def leader_cap_weight(leader_reviews: float) -> float:
    """How much of the weak leader cap applies, 1 down to 0."""
    if leader_reviews <= LEADER_CAP_FULL:
        return 1.0
    if leader_reviews >= LEADER_CAP_GONE:
        return 0.0
    return 1.0 - math.log10(leader_reviews / LEADER_CAP_FULL) / math.log10(
        LEADER_CAP_GONE / LEADER_CAP_FULL
    )


def small_result_cap(result_count: int) -> int | None:
    """The ceiling a tiny result set puts on difficulty.

    Ten points per app Apple returns, up to nine apps; from ten apps on,
    no ceiling. It used to be 10, 20, 31, 40 for one to four apps and then
    nothing at five, a step of up to 60 points for one more app.
    """
    if 1 <= result_count <= 9:
        return 10 * result_count
    return None


# The fields of a difficulty breakdown that hold an App Store name, and the
# bracketed names its sentences quote: "The #1 app (Brand - Tagline) has".
_BREAKDOWN_NAME_KEYS = ("weakest_app", "brand_name")
_NAME_IN_BRACKETS = re.compile(r"\([^()]*\)")


def readable_breakdown(breakdown: dict | None) -> dict:
    """A difficulty breakdown stored by an older version, with its sentences
    read by today's dash rule (aso/copy_rules.py). App names are App Store
    data and stay exactly as written. Search History and the Opportunity
    Finder were rewritten once (DIFFICULTY_VERSION 3); an AI run's rows are
    a snapshot, so they are read through this (aso_pro/explained_rows.py)."""
    from .copy_rules import no_dash_punctuation_keeping, walk_prose

    return walk_prose(breakdown or {},
                      lambda text: no_dash_punctuation_keeping(text, _NAME_IN_BRACKETS),
                      keep=_BREAKDOWN_NAME_KEYS)


class DifficultyCalculator:
    """
    Calculates keyword difficulty score (1-100) from competitor data.

    All sub-scores are normalized to 0-100 before applying weights,
    ensuring the full 1-100 range is usable.

    Scoring Components (weighted):
      - Rating Volume (30%): log-scale of MEDIAN userRatingCount
      - Review Velocity (10%): median reviews-per-year across competitors.
        Distinguishes active, growing markets from stagnant ones.
      - Dominant Players (20%): proportion with serious traction + mega boosts
      - Rating Quality (10%): average star rating (smooth interpolation)
      - Market Age (10%): average age of top apps
      - Publisher Diversity (10%): unique publishers in top results
      - Title Relevance (10%): how many competitors have keyword in title

    All 7 sub-scores use data from ALL competitors in the result set,
    not just the #1 app. Uses MEDIAN for volume and velocity to prevent
    a single outlier from skewing the field assessment.

    Post-processing overrides correct for Apple's "generic backfill":
      - Small Result Set Cap: if Apple returns ≤3 apps, there's
        objectively little competition regardless of app strength.
      - Weak Leader Cap: if #1 has very few reviews, cap the score.
        The #1 app's strength is the single strongest difficulty signal.
      - Backfill Discount: if few competitors match the keyword in title
        AND the leader is weak, most results are generic backfill from
        broader terms. Discount the score accordingly.

    Additionally computes opportunity signals (not part of score):
      - Title gap: are competitors optimized for this exact keyword?
      - Weak spots: any competitors with <1,000 reviews in the top results?
      - Fresh entrants: any apps released in the last 12 months?
      - Cross-genre: does the keyword span multiple app categories?
    """

    def _compute_raw_difficulty(
        self, competitors: list[dict], keyword: str,
        full_result_count: int | None = None,
    ) -> tuple[int, dict]:
        """
        Core difficulty calculation from competitor data.

        Computes the 7 weighted sub-scores and returns the raw total
        (before post-processing overrides) plus a dict of sub-scores.
        This method is reused by both the overall difficulty score
        and individually for each ranking tier.

        Args:
            full_result_count: The total number of results Apple
                returned for this keyword (used for sample dampening).
                When None, defaults to len(competitors).  For tier
                computation, pass the FULL result count so that
                dampening reflects keyword popularity, not the
                deliberate tier slice size.

        Returns:
            Tuple of (raw_score, sub_scores_dict)
        """
        n = len(competitors)
        if n == 0:
            return 0, {
                "rating_volume": 0,
                "review_velocity": 0,
                "dominant_players": 0,
                "rating_quality": 0,
                "market_age": 0,
                "publisher_diversity": 0,
                "title_relevance": 0,
                "title_match_count": 0,
                "median_reviews": 0,
                "avg_reviews": 0,
            }

        kw_lower = keyword.lower().strip()

        # --- Rating Volume (30%) — 0-100 normalized using MEDIAN ---
        rating_counts = [c.get("userRatingCount", 0) for c in competitors]
        sorted_counts = sorted(rating_counts)
        if n % 2 == 1:
            median_ratings = sorted_counts[n // 2]
        else:
            median_ratings = (
                sorted_counts[n // 2 - 1] + sorted_counts[n // 2]
            ) / 2
        avg_ratings = sum(rating_counts) / n
        rating_volume = self._rating_volume_score(median_ratings)

        # --- Review Velocity (10%) — 0-100 normalized ---
        review_velocity = self._review_velocity_score(competitors)

        # --- Dominant Players (20%) — 0-100 normalized ---
        # Each competitor contributes a continuous dominance signal
        # based on its review count via smooth log interpolation,
        # replacing the old hard 10K/100K/1M step cutoffs.
        #
        # Per-app dominance (0-1): log10(reviews)/log10(10_000_000)
        # clamped to [0, 1].  This gives:
        #   100 reviews → 0.29, 1K → 0.43, 10K → 0.57,
        #   100K → 0.71, 1M → 0.86, 10M → 1.0
        #
        # The top half of competitors is weighted 2× to reflect that
        # high-ranking dominant players matter more than bottom-rankers.
        log_ceiling = math.log10(10_000_000)  # 7.0
        top_half_size = max(n // 2, 1)
        dominance_total = 0.0
        for i, r in enumerate(rating_counts):
            if r <= 0:
                continue
            app_dominance = min(1.0, math.log10(max(r, 1)) / log_ceiling)
            weight = 2.0 if i < top_half_size else 1.0
            dominance_total += app_dominance * weight
        weight_sum = 2.0 * top_half_size + 1.0 * max(n - top_half_size, 0)
        dominant_players = min(100, (dominance_total / max(weight_sum, 1)) * 100)

        # --- Rating Quality (10%) — 0-100 normalized ---
        # Review-weighted average: a 5.0★ app with 1 review shouldn’t
        # weigh the same as a 4.0★ app with 2,000 reviews.  Apps with
        # negligible review counts are noise, not quality evidence.
        weighted_sum = 0.0
        weight_total = 0.0
        for c in competitors:
            rating = c.get("averageUserRating", 0)
            reviews = c.get("userRatingCount", 0)
            if rating > 0 and reviews > 0:
                # log1p weight: 1 review→0.7, 10→2.4, 100→4.6,
                # 1000→6.9, 10000→9.2.  Prevents mega-apps from
                # completely dominating while still respecting volume.
                w = math.log1p(reviews)
                weighted_sum += rating * w
                weight_total += w
        avg_quality = (
            weighted_sum / weight_total if weight_total > 0 else 0
        )
        rating_quality = self._rating_quality_score(avg_quality)

        # --- Market Age (10%) — 0-100 normalized ---
        market_age = self._market_age_score(competitors)

        # --- Publisher Diversity (10%) — 0-100 normalized ---
        unique_publishers = len(
            {
                c.get("sellerName", "").lower()
                for c in competitors
                if c.get("sellerName")
            }
        )
        publisher_diversity = min(100, (unique_publishers / max(n, 1)) * 100)

        # --- Title Relevance (10%) — 0-100 normalized ---
        # Strong match only: exact phrase or all words(any order).
        title_match_count = 0
        relevance_sum = 0.0
        for c in competitors:
            evidence = _keyword_title_evidence(
                kw_lower,
                c.get("trackName", ""),
                c.get("primaryGenreName", ""),
            )
            relevance_sum += float(evidence["evidence"])
            if evidence["exact_phrase"] or evidence["all_words"]:
                title_match_count += 1
        title_relevance = min(100, (title_match_count / max(n, 1)) * 100)

        # --- Small sample dampening ---
        # Ratio-based sub-scores are unreliable with few competitors
        # (e.g. 1/1 = 100% → "Very High" is a math artifact).
        # Smooth linear ramp reaches full strength at n = 10.
        #
        # Uses full_result_count (the total Apple returned for this
        # keyword) rather than len(competitors), so that tier slices
        # (top-5/10/20) inherit dampening from the keyword's overall
        # search volume instead of the deliberate slice size.
        dampening_n = full_result_count if full_result_count is not None else n
        sample_dampening = min(1.0, dampening_n / 10)
        publisher_diversity *= sample_dampening
        title_relevance *= sample_dampening
        dominant_players *= sample_dampening
        rating_quality *= sample_dampening

        # --- Backfill-aware dampening ---
        # When few competitors have the keyword in their title, most
        # results are Apple backfill from broader terms.  Sub-scores
        # computed on irrelevant apps are misleading (e.g. high
        # publisher diversity from 11 random unrelated apps).
        #
        # ALWAYS applied (including tiers): if most apps in a slice
        # are backfill, their sub-scores are inflated regardless of
        # whether the slice is intentional.
        relevance_ratio = relevance_sum / max(n, 1)
        relevance = max(0.3, min(1.0, relevance_ratio * 2.6))
        publisher_diversity *= relevance
        rating_quality *= relevance
        market_age *= relevance

        # --- Weighted Total ---
        from .strength import WEIGHTS

        raw_total = int(
            rating_volume * WEIGHTS["volume"]
            + review_velocity * WEIGHTS["momentum"]
            + dominant_players * WEIGHTS["dominance"]
            + rating_quality * WEIGHTS["rating"]
            + market_age * WEIGHTS["age"]
            + publisher_diversity * WEIGHTS["publishers"]
            + title_relevance * WEIGHTS["title"]
        )
        raw_total = max(1, min(100, raw_total))

        sub_scores = {
            "rating_volume": round(rating_volume, 1),
            "review_velocity": round(review_velocity, 1),
            "dominant_players": round(dominant_players, 1),
            "rating_quality": round(rating_quality, 1),
            "market_age": round(market_age, 1),
            "publisher_diversity": round(publisher_diversity, 1),
            "title_relevance": round(title_relevance, 1),
            "title_match_count": title_match_count,
            "median_reviews": int(median_ratings),
            "avg_reviews": int(avg_ratings),
            "rating_counts": rating_counts,
            "avg_quality": avg_quality,
        }

        return raw_total, sub_scores

    def calculate(
        self, competitors: list[dict], keyword: str = ""
    ) -> tuple[int, dict]:
        """
        Calculate difficulty score from competitor app data.

        Args:
            competitors: List of app dicts from ITunesSearchService.
            keyword: The search keyword (used for title relevance analysis).

        Returns:
            Tuple of (total_score, breakdown_dict).
        """
        if not competitors:
            return 0, {
                "total_score": 0,
                "rating_volume": 0,
                "review_velocity": 0,
                "dominant_players": 0,
                "rating_quality": 0,
                "market_age": 0,
                "publisher_diversity": 0,
                "title_relevance": 0,
                "interpretation": "No Data",
                "insights": [],
                "opportunity_signals": [],
            }

        n = len(competitors)
        kw_lower = keyword.lower().strip()

        # --- Core difficulty (same algo used for tiers) ---
        raw_total, sub_scores = self._compute_raw_difficulty(
            competitors, keyword
        )
        total = raw_total

        # Extract values needed for post-processing and insights
        rating_counts = sub_scores["rating_counts"]
        title_match_count = sub_scores["title_match_count"]
        median_ratings = sub_scores["median_reviews"]
        avg_ratings = sub_scores["avg_reviews"]
        avg_quality = sub_scores["avg_quality"]

        # Compute counts for insight text (no longer used for scoring,
        # but still useful for human-readable explanations).
        top_half = rating_counts[: max(n // 2, 1)]
        serious_count = sum(1 for r in rating_counts if r > 10_000)
        mega_count = sum(1 for r in top_half if r > 100_000)
        ultra_count = sum(1 for r in top_half if r > 1_000_000)

        # ------------------------------------------------------------------ #
        # Post-processing: Correct for Apple's Generic Backfill
        #
        # When Apple's search can't find enough apps matching a specific
        # keyword, it backfills with popular apps from broader terms.
        # E.g., "lan invoice" → Apple places 1 exact match at #1, then
        # fills #2-10 with giant "invoice" apps. This inflates difficulty.
        #
        # Three signals correct for this:
        # ------------------------------------------------------------------ #
        override_reason = None
        leader_reviews = competitors[0].get("userRatingCount", 0) if competitors else 0
        match_ratio = title_match_count / n if n > 0 else 0

        # Brand keyword detection: skip weak-leader adjustments when
        # the keyword matches the #1 app's publisher (e.g. "nasdaq"
        # → Nasdaq, Inc.). The competitors aren't backfill — Apple
        # ranked them intentionally for this brand query.
        is_brand_keyword, brand_name = (
            _is_brand_keyword(kw_lower, competitors[0], competitors)
            if competitors and kw_lower
            else (False, None)
        )

        # Signal 0: Small Result Set Cap
        # If Apple returns very few results, there's objectively little
        # competition for this keyword regardless of how strong those few
        # apps are. Ten points of ceiling per app returned, up to nine.
        small_cap = small_result_cap(n)
        if small_cap is not None and total > small_cap:
            total = small_cap
            override_reason = "small_result_set"

        # Signals 1 and 2: the weak leader cap and the backfill discount,
        # shared with every ranking tier.
        if kw_lower and n >= 2:
            total, leader_reason = self._leader_corrections(
                total, leader_reviews=leader_reviews, match_ratio=match_ratio,
                is_brand=is_brand_keyword,
            )
            if leader_reason:
                override_reason = leader_reason

        total = max(1, min(100, total))

        # Interpretation (based on adjusted total)
        if total <= 15:
            interpretation = "Very Easy"
        elif total <= 35:
            interpretation = "Easy"
        elif total <= 55:
            interpretation = "Moderate"
        elif total <= 75:
            interpretation = "Hard"
        elif total <= 90:
            interpretation = "Very Hard"
        else:
            interpretation = "Extreme"

        # --- Generate Insights (explain the score) ---
        insights = self._generate_insights(
            rating_counts,
            median_ratings,
            avg_ratings,
            serious_count,
            mega_count,
            ultra_count,
            title_match_count,
            n,
            avg_quality,
        )

        # Add brand keyword insight (even when score is NOT adjusted)
        if is_brand_keyword:
            leader_name = competitors[0].get("trackName", "#1 app")
            insights.insert(
                0,
                {
                    "icon": "🏷️",
                    "type": "info",
                    "text": (
                        f"Brand keyword: '{kw_lower}' matches publisher "
                        f"{brand_name}. The #1 app ({leader_name}) has few "
                        f"ratings because it's a brand companion app, not "
                        f"because the keyword is easy. Difficulty reflects "
                        f"the full competitive landscape."
                    ),
                },
            )

        # Add override insight at the top if score was adjusted
        if override_reason and raw_total != total:
            if override_reason == "small_result_set":
                override_text = (
                    f"Only {n} app{'s' if n > 1 else ''} found for this "
                    f"keyword: very little competition exists."
                )
            else:
                leader_name = competitors[0].get("trackName", "#1 app")
                if match_ratio > 0.3:
                    # Many competitors target this keyword — not backfill,
                    # just a weak leader in a competitive field.
                    override_text = (
                        f"The #1 app ({leader_name}) has only "
                        f"{leader_reviews:,} "
                        f"rating{'s' if leader_reviews != 1 else ''}, "
                        f"but {title_match_count} of {n} competitors "
                        f"target this keyword: real competition exists."
                    )
                else:
                    override_text = (
                        f"The #1 app ({leader_name}) has only "
                        f"{leader_reviews:,} "
                        f"rating{'s' if leader_reviews != 1 else ''}. "
                        f"The remaining results are generic backfill "
                        f"from broader search terms, not real "
                        f"competition for this specific keyword."
                    )

            # When backfill is detected, recontextualize misleading
            # insights that describe backfill apps as if they were
            # real competitors (e.g. "100K+ reviews — strong incumbents"
            # when those apps aren't competing for this keyword).
            # Only recontextualize when most results are actually
            # backfill. If match_ratio is high, the insights are valid.
            if override_reason in ("weak_leader", "backfill") and match_ratio <= 0.3:
                recontextualized = []
                for insight in insights:
                    text = insight["text"]
                    if any(
                        phrase in text
                        for phrase in [
                            "strong incumbents",
                            "dominated by major brands",
                            "High quality bar",
                            "Users expect excellence",
                        ]
                    ):
                        # Add backfill context
                        insight = dict(insight)
                        insight["text"] = (
                            text + " (but most are backfill, "
                            "not targeting this keyword)"
                        )
                        insight["type"] = "info"
                    recontextualized.append(insight)
                insights = recontextualized
            insights.insert(
                0,
                {
                    "icon": "📍",
                    "type": "opportunity",
                    "text": override_text,
                },
            )

        # --- Opportunity Signals ---
        opportunity_signals = self._find_opportunities(
            competitors, kw_lower, title_match_count, rating_counts, n
        )

        # --- Ranking Tier Analysis ---
        ranking_tiers = self._compute_ranking_tiers(
            competitors, keyword,
            overall_score=total,
            overall_match_ratio=match_ratio,
            overall_leader_reviews=leader_reviews,
            is_brand_keyword=is_brand_keyword,
        )

        breakdown = {
            "total_score": total,
            "raw_total": raw_total,
            "override_reason": override_reason,
            "is_brand_keyword": is_brand_keyword,
            "brand_name": brand_name,
            "rating_volume": sub_scores["rating_volume"],
            "review_velocity": sub_scores["review_velocity"],
            "dominant_players": sub_scores["dominant_players"],
            "rating_quality": sub_scores["rating_quality"],
            "market_age": sub_scores["market_age"],
            "publisher_diversity": sub_scores["publisher_diversity"],
            "title_relevance": sub_scores["title_relevance"],
            "interpretation": interpretation,
            "title_match_count": title_match_count,
            "median_reviews": int(median_ratings),
            "avg_reviews": int(avg_ratings),
            "insights": insights,
            "opportunity_signals": opportunity_signals,
            "ranking_tiers": ranking_tiers,
        }

        return total, breakdown

    def _leader_corrections(
        self, total: int, *, leader_reviews: float, match_ratio: float,
        is_brand: bool,
    ) -> tuple[int, str | None]:
        """The weak leader cap and the backfill discount, in one place.

        Used by the overall score and by every ranking tier, which used to
        carry their own copies.

        Weak leader cap: a #1 app with few reviews caps difficulty. When many
        competitors target the keyword the weak leader just means no dominant
        player yet, so the cap bites in proportion to the backfill:
        ``cap + (total - cap) * match_ratio``. It applies in full below
        LEADER_CAP_FULL reviews and fades out by LEADER_CAP_GONE. It used to
        switch off at exactly 1,000 reviews and to apply in full below a 20%
        match ratio, both of which made difficulty jump.

        Backfill discount: when few competitors have the keyword in their
        title and the leader is weak, most results are backfill from broader
        terms. Unchanged: it was already continuous at both edges.

        Brand keywords get neither: Apple ranked those apps on purpose.
        """
        reason = None
        if is_brand:
            return total, reason

        weight = leader_cap_weight(leader_reviews)
        if weight > 0:
            cap = leader_cap(leader_reviews)
            if total > cap:
                capped = cap + (total - cap) * match_ratio
                adjusted = int(total - weight * (total - capped))
                if adjusted < total:
                    total = adjusted
                    reason = "weak_leader"

        if match_ratio < 0.2 and leader_reviews < LEADER_CAP_FULL:
            ratio_factor = min(1.0, 0.6 + 2.0 * match_ratio)
            leader_factor = math.log10(leader_reviews + 1) / math.log10(1001)
            discount = ratio_factor + (1.0 - ratio_factor) * leader_factor
            discount = max(0.6, min(1.0, discount))
            discounted = max(1, int(total * discount))
            if discounted < total:
                total = discounted
                reason = "backfill"
        return total, reason

    def _compute_ranking_tiers(
        self,
        competitors: list[dict],
        keyword: str,
        overall_score: int = 0,
        overall_match_ratio: float = 0.0,
        overall_leader_reviews: int = 0,
        is_brand_keyword: bool = False,
    ) -> dict:
        """
        Compute ranking tier analysis for Top 5, Top 10, and Top 20.

        Uses the SAME difficulty algorithm (_compute_raw_difficulty) on
        each tier's competitor subset so tier labels are naturally
        consistent with the overall difficulty score.

        For each tier, produces user-friendly data:
          - min_reviews: review count of the weakest app
          - weakest_app: name of that app
          - median_reviews: median review count within the tier
          - weak_count: apps with <1K reviews
          - fresh_count: apps released in last 12 months
          - title_keyword_count: apps with keyword in title
          - total_apps: actual number of apps in this tier
          - tier_score: raw difficulty score for this tier (0-100)
          - label: Easy / Moderate / Hard / Very Hard
          - highlights: list of plain-English bullet strings
        """
        now = datetime.now(UTC)
        kw_lower = keyword.lower().strip()
        tiers = {}

        for tier_name, tier_size in [("top_5", 5), ("top_10", 10), ("top_20", 20)]:
            tier_apps = competitors[:tier_size]
            n = len(tier_apps)
            if n == 0:
                tiers[tier_name] = {
                    "min_reviews": 0,
                    "weakest_app": "—",
                    "median_reviews": 0,
                    "weak_count": 0,
                    "fresh_count": 0,
                    "title_keyword_count": 0,
                    "total_apps": 0,
                    "tier_score": 0,
                    "label": "Easy",
                    "highlights": ["No competitors found: wide open."],
                }
                continue

            # --- Use the same difficulty algorithm ---
            # full_result_count = total Apple results for this keyword,
            # so that sample dampening reflects keyword popularity
            # (not the deliberate tier slice size).
            full_n = len(competitors)
            tier_score, tier_sub = self._compute_raw_difficulty(
                tier_apps, keyword, full_result_count=full_n
            )

            # --- Post-processing using OVERALL context ---
            # The weak leader cap and backfill discount are keyword-
            # level signals (is the keyword dominated by backfill?).
            # Use the OVERALL match_ratio and leader_reviews so that
            # tiers inherit the same corrections the overall score gets.
            # This prevents e.g. "lan signer" tiers showing Hard
            # while overall shows Very Easy.
            if kw_lower and full_n >= 2:
                tier_score, _ = self._leader_corrections(
                    tier_score, leader_reviews=overall_leader_reviews,
                    match_ratio=overall_match_ratio, is_brand=is_brand_keyword,
                )

            tier_score = max(1, min(100, tier_score))

            # Map score to label — same thresholds as overall interpretation
            if tier_score <= 15:
                label = "Very Easy"
            elif tier_score <= 35:
                label = "Easy"
            elif tier_score <= 55:
                label = "Moderate"
            elif tier_score <= 75:
                label = "Hard"
            elif tier_score <= 90:
                label = "Very Hard"
            else:
                label = "Extreme"

            # Weakest app in the tier
            reviews = tier_sub["rating_counts"]
            min_reviews = min(reviews)
            min_idx = reviews.index(min_reviews)
            barrier_app = tier_apps[min_idx].get("trackName", "Unknown")

            # Median reviews
            sorted_reviews = sorted(reviews)
            if n % 2 == 1:
                median = sorted_reviews[n // 2]
            else:
                median = (sorted_reviews[n // 2 - 1] + sorted_reviews[n // 2]) / 2

            # Weak spots (<1K reviews)
            weak = sum(1 for r in reviews if r < 1_000)

            # Fresh entrants (last 12 months)
            fresh = 0
            for c in tier_apps:
                release_date = c.get("releaseDate", "")
                if release_date:
                    try:
                        released = datetime.fromisoformat(
                            release_date
                        )
                        if (now - released).days < 365:
                            fresh += 1
                    except (ValueError, TypeError):
                        pass

            # Title keyword matches
            title_opt = tier_sub["title_match_count"]

            # Human-friendly highlight bullets
            highlights = self._tier_highlights(
                tier_size, n, min_reviews, barrier_app, median,
                weak, fresh, title_opt
            )

            tiers[tier_name] = {
                "min_reviews": min_reviews,
                "weakest_app": barrier_app,
                "median_reviews": int(median),
                "weak_count": weak,
                "fresh_count": fresh,
                "title_keyword_count": title_opt,
                "total_apps": n,
                "tier_score": tier_score,
                "label": label,
                "highlights": highlights,
            }

        # --- Floor: every tier must be ≥ overall difficulty ---
        # Since Top-5 ⊂ Top-10 ⊂ Top-20 ⊂ All, it's always at least
        # as hard to break into a tier as to compete at all.  Without
        # this floor, tiers can appear inconsistently easier than the
        # overall score (e.g. overall "Very Hard" but tiers "Moderate").
        if overall_score > 0:
            for tier in tiers.values():
                tier["tier_score"] = max(tier["tier_score"], overall_score)

        # Re-label after floor enforcement
        def _score_to_label(s):
            if s <= 15:
                return "Very Easy"
            elif s <= 35:
                return "Easy"
            elif s <= 55:
                return "Moderate"
            elif s <= 75:
                return "Hard"
            elif s <= 90:
                return "Very Hard"
            return "Extreme"

        for tier in tiers.values():
            tier["label"] = _score_to_label(tier["tier_score"])

        # --- Enforce monotonicity: larger tiers can never be harder ---
        # If Top 5 is Hard, Top 10 and Top 20 must also be Hard-or-easier.
        # This is natural: more slots = easier to break in.
        difficulty_order = [
            "Very Easy", "Easy", "Moderate", "Hard",
            "Very Hard", "Extreme",
        ]

        def _cap_label(label, ceiling):
            if difficulty_order.index(label) > difficulty_order.index(ceiling):
                return ceiling
            return label

        if "top_5" in tiers and "top_10" in tiers:
            tiers["top_10"]["tier_score"] = min(tiers["top_10"]["tier_score"], tiers["top_5"]["tier_score"])
            tiers["top_10"]["label"] = _cap_label(
                tiers["top_10"]["label"], tiers["top_5"]["label"]
            )
        if "top_10" in tiers and "top_20" in tiers:
            tiers["top_20"]["tier_score"] = min(tiers["top_20"]["tier_score"], tiers["top_10"]["tier_score"])
            tiers["top_20"]["label"] = _cap_label(
                tiers["top_20"]["label"], tiers["top_10"]["label"]
            )

        return tiers

    def _tier_highlights(
        self,
        tier_size: int,
        n: int,
        min_reviews: int,
        weakest_app: str,
        median: float,
        weak: int,
        fresh: int,
        title_opt: int,
    ) -> list[str]:
        """Generate plain-English highlight bullets for a tier card."""
        highlights = []

        # Open positions
        if n < tier_size:
            open_spots = tier_size - n
            highlights.append(
                f"Only {n} app{'s' if n != 1 else ''} rank here, "
                f"so {open_spots} spot{'s are' if open_spots != 1 else ' is'} open."
            )
            return highlights

        # Review barrier
        if min_reviews < 100:
            highlights.append(
                f"The easiest app to beat has just {min_reviews:,} ratings."
            )
        elif min_reviews < 1_000:
            highlights.append(
                f"You need ~{min_reviews:,}+ ratings to compete "
                f"(weakest: {weakest_app})."
            )
        elif min_reviews < 10_000:
            highlights.append(
                f"You need ~{min_reviews:,}+ ratings to break in."
            )
        else:
            highlights.append(
                f"Requires ~{min_reviews:,}+ ratings: an established market."
            )

        # Weak spots
        if weak > 0:
            highlights.append(
                f"{weak} of {n} apps have under 1K ratings: beatable."
            )
        else:
            highlights.append("Every app here has 1K+ ratings: no easy targets.")

        # Fresh entrants
        if fresh > 0:
            highlights.append(
                f"{fresh} app{'s' if fresh != 1 else ''} "
                f"broke in within the last year."
            )

        # Title keyword usage
        if title_opt == 0:
            highlights.append(
                "No app uses this exact keyword in its title: an ASO opportunity!"
            )
        elif title_opt < n // 2:
            highlights.append(
                f"Only {title_opt} of {n} apps use this keyword "
                f"in their title."
            )
        else:
            highlights.append(
                f"{title_opt} of {n} apps already target this keyword "
                f"in their title."
            )

        return highlights

    def _generate_insights(
        self,
        rating_counts: list,
        median: float,
        avg: float,
        serious: int,
        mega: int,
        ultra: int,
        title_matches: int,
        n: int,
        avg_quality: float,
    ) -> list[dict]:
        """Generate human-readable insights explaining the score."""
        insights = []

        # Review volume insight
        if ultra > 0:
            insights.append(
                {
                    "icon": "🏢",
                    "type": "barrier",
                    "text": (
                        f"{ultra} app{'s' if ultra > 1 else ''} with 1M+ "
                        "ratings: dominated by major brands"
                    ),
                }
            )
        elif mega > 0:
            insights.append(
                {
                    "icon": "⚠️",
                    "type": "barrier",
                    "text": (
                        f"{mega} app{'s' if mega > 1 else ''} with 100K+ "
                        "ratings: strong incumbents"
                    ),
                }
            )

        # Median vs mean skew
        if avg > 0 and median > 0 and avg > median * 3:
            insights.append(
                {
                    "icon": "📊",
                    "type": "info",
                    "text": (
                        f"Rating distribution is skewed: the median "
                        f"({median:,.0f}) is much lower than mean "
                        f"({avg:,.0f}). A few giants inflate the average."
                    ),
                }
            )

        # Title relevance
        if title_matches == 0 and n > 0:
            insights.append(
                {
                    "icon": "🎯",
                    "type": "opportunity",
                    "text": (
                        "No competitors have this exact keyword in their "
                        "title: a possible title optimization gap"
                    ),
                }
            )
        elif title_matches <= 2:
            insights.append(
                {
                    "icon": "🎯",
                    "type": "opportunity",
                    "text": (
                        f"Only {title_matches} of {n} competitors use this "
                        "keyword in their title"
                    ),
                }
            )
        else:
            insights.append(
                {
                    "icon": "🔒",
                    "type": "barrier",
                    "text": (
                        f"{title_matches} of {n} competitors already have "
                        "this keyword in their title"
                    ),
                }
            )

        # Quality insight
        if avg_quality >= 4.5:
            insights.append(
                {
                    "icon": "⭐",
                    "type": "barrier",
                    "text": (
                        f"High quality bar: the average rating is "
                        f"{avg_quality:.1f} stars. Users expect excellence."
                    ),
                }
            )

        # Weak competitor count
        weak_count = sum(1 for r in rating_counts if r < 1_000)
        if weak_count >= 3:
            insights.append(
                {
                    "icon": "💡",
                    "type": "opportunity",
                    "text": (
                        f"{weak_count} of {n} competitors have <1,000 "
                        "ratings: beatable with a quality app"
                    ),
                }
            )

        return insights

    def _find_opportunities(
        self,
        competitors: list[dict],
        keyword: str,
        title_matches: int,
        rating_counts: list,
        n: int,
    ) -> list[dict]:
        """Find actionable opportunity signals for the developer."""
        signals = []

        # Title gap signal
        if title_matches == 0:
            signals.append(
                {
                    "signal": "Title Gap",
                    "icon": "🎯",
                    "strength": "Strong",
                    "detail": (
                        "No top app has this keyword in its title. "
                        "Exact-match title optimization could give you "
                        "an edge in search rankings."
                    ),
                }
            )
        elif title_matches <= n // 3:
            signals.append(
                {
                    "signal": "Title Gap",
                    "icon": "🎯",
                    "strength": "Moderate",
                    "detail": (
                        f"Only {title_matches} of {n} competitors have "
                        "this keyword in their title. There's room for "
                        "title optimization."
                    ),
                }
            )

        # Weak spots
        weak_apps = [
            c for c in competitors if c.get("userRatingCount", 0) < 1_000
        ]
        if weak_apps:
            weakest = min(
                weak_apps, key=lambda x: x.get("userRatingCount", 0)
            )
            signals.append(
                {
                    "signal": "Weak Competitors",
                    "icon": "📉",
                    "strength": (
                        "Strong" if len(weak_apps) >= 3 else "Moderate"
                    ),
                    "detail": (
                        f"{len(weak_apps)} of {n} apps have <1,000 ratings."
                        f" The weakest ({weakest.get('trackName', 'Unknown')})"
                        f" has only "
                        f"{weakest.get('userRatingCount', 0):,} ratings: "
                        "these positions are displaceable."
                    ),
                }
            )

        # Fresh entrants (released in last 12 months)
        now = datetime.now(UTC)
        fresh_apps = []
        for c in competitors:
            release_date = c.get("releaseDate", "")
            if release_date:
                try:
                    released = datetime.fromisoformat(
                        release_date
                    )
                    if (now - released).days < 365:
                        fresh_apps.append(c)
                except (ValueError, TypeError):
                    pass

        if fresh_apps:
            signals.append(
                {
                    "signal": "Active Market",
                    "icon": "🆕",
                    "strength": "Moderate",
                    "detail": (
                        f"{len(fresh_apps)} "
                        f"app{'s' if len(fresh_apps) > 1 else ''} launched "
                        "in the last 12 months: this market is still "
                        "attracting new entrants."
                    ),
                }
            )

        # Niche genre diversity
        genres = {
            c.get("primaryGenreName", "")
            for c in competitors
            if c.get("primaryGenreName")
        }
        if len(genres) >= 3:
            genre_list = ", ".join(sorted(genres)[:3])
            suffix = "..." if len(genres) > 3 else ""
            signals.append(
                {
                    "signal": "Cross-Genre",
                    "icon": "🔀",
                    "strength": "Moderate",
                    "detail": (
                        f"Results span {len(genres)} genres "
                        f"({genre_list}{suffix}). The keyword isn't locked "
                        "to one category: a well-positioned app in any "
                        "genre could rank."
                    ),
                }
            )

        return signals

    def _rating_volume_score(self, median_ratings: float) -> float:
        """The rating volume sub-score. See rating_volume_score()."""
        return rating_volume_score(median_ratings)

    def _review_velocity_score(self, competitors: list[dict]) -> float:
        """The field's momentum: median ratings per year across competitors,
        on the shared momentum curve (aso.strength.momentum_score)."""
        from .strength import _utcnow, momentum_score, ratings_per_year, years_since

        now = _utcnow()
        velocities = []
        for c in competitors:
            reviews = c.get("userRatingCount", 0)
            years = years_since(c.get("releaseDate", ""), now)
            if years is not None and reviews > 0:
                velocities.append(ratings_per_year(reviews, years))

        if not velocities:
            return 50  # default mid-range

        # Use median velocity (robust to outliers)
        velocities.sort()
        vn = len(velocities)
        if vn % 2 == 1:
            median_vel = velocities[vn // 2]
        else:
            median_vel = (velocities[vn // 2 - 1] + velocities[vn // 2]) / 2
        return momentum_score(median_vel)

    def _rating_quality_score(self, avg_quality: float) -> float:
        """The field's rating, on the shared curve (aso.strength.rating_score)."""
        from .strength import rating_score

        return rating_score(avg_quality)

    def _market_age_score(self, competitors: list[dict]) -> float:
        """The field's age: mean years on the store, on the shared curve
        (aso.strength.age_score). Older fields are more entrenched."""
        from .strength import _utcnow, age_score, years_since

        now = _utcnow()
        ages = [
            years for years in (years_since(c.get("releaseDate", ""), now) for c in competitors)
            if years is not None
        ]
        if not ages:
            return 50  # default mid-range
        return age_score(sum(ages) / len(ages))

