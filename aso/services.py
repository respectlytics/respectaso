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
from datetime import datetime, timezone

import requests

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# Exceptions
# --------------------------------------------------------------------------- #


class ITunesAPIError(Exception):
    """Base class for App Store data retrieval errors."""
    pass


class SearchAPIUnavailableError(ITunesAPIError):
    """Both primary (iTunes API) and fallback (SSR) search are down."""
    pass


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
    """Tokenize into lowercase words for robust title matching.

    Uses ``[^\W_]+`` (Unicode-aware \\w minus underscore) so accented
    characters like é, ü, ñ are preserved as part of the token.
    """
    raw_tokens = re.findall(r"[^\W_]+", (text or "").lower())
    return [_TOKEN_NORMALIZATION.get(tok, tok) for tok in raw_tokens]


def _has_finance_intent(keyword_tokens: set[str]) -> bool:
    return bool(keyword_tokens & _FINANCE_INTENT_TOKENS)


def _has_finance_context(title_tokens: set[str], genre: str) -> bool:
    genre_lower = (genre or "").lower()
    if "finance" in genre_lower:
        return True
    if title_tokens & _FINANCE_STRONG_CONTEXT_TOKENS:
        return True
    return False


def _keyword_title_evidence(keyword: str, title: str, genre: str = "") -> dict[str, float | bool]:
    """
    Match hierarchy: exact phrase > all words(any order) > partial overlap(weak).

    Returns a normalized evidence score in [0, 1] plus match flags.
    """
    kw = (keyword or "").lower().strip()
    title_lower = (title or "").lower()
    kw_tokens = set(_tokenize(kw))
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

    exact_phrase = bool(kw and kw in title_lower)
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

    def estimate(self, competitors: list[dict], keyword: str) -> int | None:
        """
        Estimate popularity score for a keyword based on competitor data.

        Args:
            competitors: List of app dicts from ITunesSearchService.
            keyword: The search keyword.

        Returns:
            Estimated popularity score (5–100), or None if no data.
        """
        if not competitors:
            return None

        n = len(competitors)
        kw_lower = keyword.lower().strip()
        word_count = len(kw_lower.split()) if kw_lower else 1

        # Signal 1: Result count (0–25 points)
        # More results = broader / more-popular search term
        result_score = min(25, n * 2.5)

        # Signal 2: Leader strength (0–30 points)
        # Only consider apps in the top half — tail apps are often
        # backfill from broader terms (e.g. Pokémon GO at #24).
        # Uses smooth log interpolation (same approach as difficulty
        # sub-scores) to avoid cliff effects between bands.
        top_half = competitors[: max(n // 2, 1)]
        max_reviews = max(c.get("userRatingCount", 0) for c in top_half)
        if max_reviews <= 0:
            leader_score = 0
        elif max_reviews >= 1_000_000:
            leader_score = 30
        else:
            leader_bands = [
                (10, 1),
                (100, 5),
                (1_000, 10),
                (10_000, 17),
                (100_000, 24),
                (1_000_000, 30),
            ]
            leader_score = 0
            for i, (threshold, score) in enumerate(leader_bands):
                if max_reviews < threshold:
                    if i == 0:
                        leader_score = (max_reviews / threshold) * score
                    else:
                        prev_t, prev_s = leader_bands[i - 1]
                        ratio = math.log(max_reviews / prev_t) / math.log(
                            threshold / prev_t
                        )
                        leader_score = prev_s + ratio * (score - prev_s)
                    break
            else:
                leader_score = 30

        # Signal 3: Title match density (0–20 points)
        # Strong title targeting (exact/all-word) is demand evidence.
        title_matches = 0
        exact_phrase_matches = 0
        relevance_sum = 0.0
        for c in competitors:
            evidence = _keyword_title_evidence(
                kw_lower,
                c.get("trackName", ""),
                c.get("primaryGenreName", ""),
            )
            relevance_sum += float(evidence["evidence"])
            if evidence["exact_phrase"]:
                title_matches += 1
                exact_phrase_matches += 1
            elif evidence["all_words"]:
                title_matches += 1
        match_ratio = title_matches / n if n > 0 else 0
        title_score = min(20, match_ratio * 40)

        # Signal 4: Market depth — median reviews (0–10 points)
        # Uses smooth log interpolation to avoid cliff effects.
        sorted_counts = sorted(
            c.get("userRatingCount", 0) for c in competitors
        )
        if n % 2 == 1:
            median = sorted_counts[n // 2]
        else:
            median = (
                sorted_counts[n // 2 - 1] + sorted_counts[n // 2]
            ) / 2

        if median <= 0:
            depth_score = 0
        elif median >= 50_000:
            depth_score = 10
        else:
            depth_bands = [
                (10, 0.5),
                (100, 3),
                (1_000, 5),
                (10_000, 8),
                (50_000, 10),
            ]
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
        # Long-tail keywords (more words) have inherently lower search
        # volume. "card value scanner" (3 words) gets more searches
        # than "card value scanner for pokemon" (5 words). This is the
        # strongest differentiator between head and long-tail terms.
        #
        # Smooth interpolation between calibration points instead of
        # hard step function.  Calibration: 1→0, 2→-3, 3→-8, 4→-15,
        # 5→-22, 6+→-28.
        _sp_points = [(1, 0), (2, -3), (3, -8), (4, -15), (5, -22), (6, -28)]
        if word_count <= 1:
            specificity_penalty = 0
        elif word_count >= 6:
            specificity_penalty = -28
        else:
            # Linear interpolation between the two bracketing points
            for i in range(len(_sp_points) - 1):
                lo_w, lo_v = _sp_points[i]
                hi_w, hi_v = _sp_points[i + 1]
                if lo_w <= word_count <= hi_w:
                    t = (word_count - lo_w) / (hi_w - lo_w)
                    specificity_penalty = lo_v + t * (hi_v - lo_v)
                    break
            else:
                specificity_penalty = -28

        # Signal 6: Exact phrase match bonus (0–15 points)
        # If competitors have the EXACT keyword phrase in their title,
        # it's a known, established search term that developers target.
        # "card value scanner" appears in many titles → real search term.
        # "card value scanner for pokemon" appears in zero → niche query.
        exact_ratio = exact_phrase_matches / n if n > 0 else 0
        exact_bonus = min(15, exact_ratio * 50)

        # --- Small sample dampening ---
        # Ratio-based signals (title_score, exact_bonus) are unreliable
        # when there are few results: 1/1 = 100% is a math artifact,
        # not real demand evidence.  Instead of a hard cutoff, we use
        # a smooth linear ramp that reaches full strength at n = 10.
        sample_dampening = min(1.0, n / 10)
        title_score *= sample_dampening
        exact_bonus *= sample_dampening

        # --- Backfill-aware dampening ---
        # When few competitors have the keyword in their title, Apple
        # is padding results with unrelated apps.  The high result
        # count is artificial and shouldn’t count at full weight.
        #
        # Partial overlap is allowed only as weak evidence.
        relevance_ratio = relevance_sum / n if n > 0 else 0
        relevance = max(0.3, min(1.0, relevance_ratio * 2.6))
        result_score *= relevance
        leader_score *= relevance
        depth_score *= relevance

        total = int(
            result_score
            + leader_score
            + title_score
            + depth_score
            + specificity_penalty
            + exact_bonus
        )
        return max(5, min(100, total))


# --------------------------------------------------------------------------- #
# iTunes Search API
# --------------------------------------------------------------------------- #


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

    _SSR_HEADERS = {
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

    def lookup_by_id(self, track_id: int, country: str = "us") -> dict | None:
        """
        Look up a single app by its iTunes trackId.

        Returns app dict or None.
        """
        try:
            response = requests.get(
                self.LOOKUP_URL,
                params={"id": track_id, "country": country},
                timeout=30,
            )
            response.raise_for_status()
            results = response.json().get("results", [])
            if results:
                r = results[0]
                return self._parse_app(r)
            return None
        except Exception as e:
            logger.error(f"iTunes lookup failed for id {track_id}: {e}")
            return None

    def lookup_full_description(self, track_id: int, country: str = "us") -> dict:
        """Look up an app and return its description, genre, and context metadata.

        Returns a dict with keys: description, genre, rating, rating_count,
        release_date, update_date, price, version, seller.
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
            response = requests.get(
                self.LOOKUP_URL,
                params={"id": track_id, "country": country},
                timeout=30,
            )
            response.raise_for_status()
            results = response.json().get("results", [])
            if results:
                r = results[0]
                return {
                    "description": r.get("description", ""),
                    "genre": r.get("primaryGenreName", ""),
                    "rating": r.get("averageUserRating", 0),
                    "rating_count": r.get("userRatingCount", 0),
                    "release_date": r.get("releaseDate", ""),
                    "update_date": r.get("currentVersionReleaseDate", ""),
                    "price": r.get("formattedPrice", "Free"),
                    "version": r.get("version", ""),
                    "seller": r.get("sellerName", ""),
                }
            return defaults
        except Exception:
            return defaults

    # ── Primary: iTunes Search API ──────────────────────────────────────

    def _search_itunes(
        self, keyword: str, country: str = "us", limit: int = 10
    ) -> list[dict]:
        """Search via iTunes API.  Raises on any failure."""
        response = requests.get(
            self.SEARCH_URL,
            params={
                "term": keyword,
                "country": country,
                "entity": "software",
                "limit": limit,
            },
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return [self._parse_app(r) for r in data.get("results", [])]

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
                    timeout=45,
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
            ids_str = ",".join(str(tid) for tid in chunk)
            try:
                resp = requests.get(
                    self.LOOKUP_URL,
                    params={"id": ids_str, "country": country},
                    timeout=30,
                )
                resp.raise_for_status()
                for r in resp.json().get("results", []):
                    tid = r.get("trackId")
                    if tid:
                        result[tid] = self._parse_app(r)
            except Exception as e:
                logger.warning(f"Batch lookup failed for chunk: {e}")
                # Continue with next chunk — partial data is better than none

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
            raise SearchAPIUnavailableError(
                "App Store search data is temporarily unavailable. "
                "Both the iTunes Search API and the Lookup API failed. "
                "Please try again in a few minutes."
            )

        # Return in ranking order, only apps that Lookup returned
        apps = []
        for tid in target_ids:
            if tid in lookup_map:
                app_dict = lookup_map[tid]
                app_dict["_data_source"] = "appstore_ssr"
                apps.append(app_dict)

        return apps

    # ── Public API ──────────────────────────────────────────────────────

    def search_apps(
        self, keyword: str, country: str = "us", limit: int = 10
    ) -> list[dict]:
        """
        Search for iOS apps matching a keyword.

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
        """
        # Try primary source: iTunes Search API
        try:
            apps = self._search_itunes(keyword, country=country, limit=limit)
            for app in apps:
                app["_data_source"] = "itunes"
            return apps
        except Exception as e:
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
            raise SearchAPIUnavailableError(
                "App Store search data is temporarily unavailable. "
                "Both the iTunes Search API and the App Store website "
                "returned errors. Please try again in a few minutes."
            ) from e

    def find_app_rank(
        self, keyword: str, track_id: int, country: str = "us"
    ) -> int | None:
        """
        Find where a specific app ranks for a keyword.

        Searches up to 200 results (iTunes API max) and returns the
        1-based position of the app, or None if not found.

        If the iTunes API fails, falls back to checking the position
        in the SSR ordered ID list (200+ apps, no full hydration needed).

        Args:
            keyword: The search term.
            track_id: The iTunes trackId to look for.
            country: Two-letter country code.

        Returns:
            1-based rank position, or None if not in top 200.

        Raises:
            SearchAPIUnavailableError: When both data sources are down.
        """
        # Try primary: iTunes Search API
        try:
            results = self._search_itunes(
                keyword, country=country, limit=200
            )
            for i, app in enumerate(results):
                if app.get("trackId") == track_id:
                    return i + 1
            return None
        except Exception as e:
            logger.warning(
                f"iTunes API failed for rank lookup '{keyword}' ({country}), "
                f"falling back to SSR: {e}"
            )

        # Fallback: SSR ID list (no full hydration — lightweight)
        try:
            return self._find_rank_in_ssr(keyword, track_id, country=country)
        except SearchAPIUnavailableError:
            raise
        except Exception as e:
            logger.error(
                f"SSR rank fallback also failed for '{keyword}' ({country}): {e}"
            )
            raise SearchAPIUnavailableError(
                "App Store search data is temporarily unavailable. "
                "Cannot determine app ranking."
            ) from e

    def _find_rank_in_ssr(
        self, keyword: str, track_id: int, country: str = "us"
    ) -> int | None:
        """Check app rank using the ordered SSR ID list.

        No full app hydration — just position in the list.
        """
        ssr_data = self._fetch_ssr_page(keyword, country=country)
        all_ids = self._extract_ssr_app_ids(ssr_data)
        try:
            return all_ids.index(track_id) + 1
        except ValueError:
            return None

    @staticmethod
    def _parse_app(result: dict) -> dict:
        """Parse an iTunes API result into a standardized app dict."""
        desc = result.get("description", "")
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
            "description": (desc[:200] + "...") if len(desc) > 200 else desc,
            "sellerName": result.get("sellerName", ""),
            "bundleId": result.get("bundleId", ""),
            "trackViewUrl": result.get("trackViewUrl", ""),
        }


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
      3. Conversion rate — percentage of taps that become installs.
         Varies by category/listing quality; 5–20 % range used.

    All numbers are rough estimates shown as ranges (low–high).
    """

    # Popularity score → estimated daily searches.
    # Piecewise-linear mapping from ASO industry data.
    # Calibrated against real App Store data.
    # Apple does not publish search volumes; these are conservative
    # estimates cross-checked with real download / rank observations.
    # Anchor: popularity 68, rank #8  →  low single-digit downloads/day
    # for early-stage apps in competitive categories.
    #
    # The growth rate accelerates above pop 75 because Apple's
    # popularity scale is roughly logarithmic — each point at
    # the top represents a much larger absolute search increment
    # than at the bottom.
    _POP_TO_SEARCHES = [
        # (popularity, daily_searches)
        (5, 1),
        (10, 3),
        (15, 5),
        (20, 10),
        (25, 20),
        (30, 35),
        (35, 55),
        (40, 90),
        (45, 140),
        (50, 200),
        (55, 290),
        (60, 400),
        (65, 550),
        (70, 750),
        (75, 1_100),
        (80, 2_000),
        (85, 4_000),
        (90, 8_000),
        (95, 16_000),
        (100, 32_000),
    ]

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
    _TTR = {
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

    # Conversion rate (tap → install): range for free apps.
    #
    # Low end (5 %): unknown indie app, weak listing, few ratings.
    # High end (20 %): category leader, strong brand, 100 K+ ratings.
    _CVR_LOW = 0.05
    _CVR_HIGH = 0.20

    # Market-size multiplier: scales search volumes relative to US.
    # _POP_TO_SEARCHES is calibrated for the US App Store (~180 M iPhones).
    # Smaller markets have proportionally fewer searches for the same
    # popularity score.  Factors derived from estimated active-iPhone
    # installed base per country relative to the US.
    _MARKET_SIZE = {
        "us": 1.0,
        # Tier 2 — large markets (30 M+ iPhones)
        "cn": 0.45,
        "jp": 0.35,
        "gb": 0.30,
        "de": 0.25,
        "fr": 0.22,
        "kr": 0.20,
        "br": 0.18,
        "in": 0.15,
        "ca": 0.15,
        "au": 0.12,
        "ru": 0.12,
        "it": 0.12,
        "es": 0.10,
        "mx": 0.10,
        # Tier 3 — mid-size markets (5–30 M iPhones)
        "tw": 0.08,
        "nl": 0.07,
        "se": 0.06,
        "ch": 0.06,
        "pl": 0.05,
        "tr": 0.05,
        "th": 0.05,
        "id": 0.05,
        "be": 0.04,
        "at": 0.04,
        "no": 0.04,
        "dk": 0.04,
        "sg": 0.04,
        "il": 0.04,
        "ae": 0.04,
        "sa": 0.04,
        "ph": 0.04,
        "my": 0.04,
        "za": 0.03,
        "ie": 0.03,
        "fi": 0.03,
        "pt": 0.03,
        "nz": 0.03,
        "cl": 0.03,
        "ar": 0.03,
        "co": 0.03,
        "ng": 0.03,
        "eg": 0.03,
        "pk": 0.02,
        "ke": 0.02,
        "gh": 0.02,
        "tz": 0.02,
        "ug": 0.02,
    }
    _MARKET_SIZE_DEFAULT = 0.03



    def _daily_searches(self, popularity: int) -> float:
        """Interpolate daily search volume from popularity score."""
        if popularity is None or popularity <= 0:
            return 0
        pts = self._POP_TO_SEARCHES
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
        searches = self._daily_searches(popularity)

        # Scale search volume by relative App Store market size.
        market_mult = self._MARKET_SIZE.get(
            (country or "us").lower(), self._MARKET_SIZE_DEFAULT
        )
        searches *= market_mult

        positions = []
        for pos in range(1, 21):
            ttr = self._TTR.get(pos, 0.001)
            dl_low = searches * ttr * self._CVR_LOW
            dl_high = searches * ttr * self._CVR_HIGH
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
        }


# --------------------------------------------------------------------------- #
# Keyword Difficulty Calculator
# --------------------------------------------------------------------------- #


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
            set(
                c.get("sellerName", "").lower()
                for c in competitors
                if c.get("sellerName")
            )
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
            if evidence["exact_phrase"]:
                title_match_count += 1
            elif evidence["all_words"]:
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
        raw_total = int(
            rating_volume * 0.30
            + review_velocity * 0.10
            + dominant_players * 0.20
            + rating_quality * 0.10
            + market_age * 0.10
            + publisher_diversity * 0.10
            + title_relevance * 0.10
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
        kw_words = set(kw_lower.split()) if kw_lower else set()

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
        # competition for this keyword regardless of how strong those
        # few apps are. Sub-scores like publisher diversity and title
        # relevance become statistically meaningless with tiny samples.
        #
        # Smooth curve instead of step function:
        # Explicit caps keep tiny samples from looking competitive
        # while preserving moderate markets at n=5.
        #   n=1→10, n=2→20, n=3→31, n=4→40
        # Only applies when n ≤ 4 (n=5 keeps the raw score).
        small_caps = {1: 10, 2: 20, 3: 31, 4: 40}
        if n in small_caps:
            small_cap = small_caps[n]
            if total > small_cap:
                total = small_cap
                override_reason = "small_result_set"

        if kw_lower and n >= 2:
            # Signal 1: Weak Leader Cap
            # The #1 app's review count is the ultimate reality check.
            # If #1 is easy to outrank, the keyword is objectively easy
            # regardless of what backfill apps appear below.
            #
            # Smooth log interpolation instead of step thresholds:
            #   cap = 15 + 35 * log10(reviews+1) / log10(1001)
            #     0 reviews → 15,  10 → 27,  50 → 35,
            #   100 → 39, 500 → 47, 999 → 50
            # Only applies when leader < 1000; above that no cap.
            leader_cap = None
            if leader_reviews < 1_000 and not is_brand_keyword:
                leader_cap = int(
                    15 + 35 * math.log10(leader_reviews + 1) / math.log10(1001)
                )

            if leader_cap is not None and total > leader_cap:
                # When many competitors target this keyword (high
                # match_ratio), the weak leader just means no dominant
                # player *yet* — the field is still competitive.
                # Blend between raw and capped score so the cap bites
                # less when title matches are high:
                #   match_ratio=0   → full cap (pure backfill)
                #   match_ratio=0.5 → midpoint between raw and cap
                #   match_ratio=1.0 → raw score kept as-is
                if match_ratio > 0.2:
                    total = int(leader_cap + (total - leader_cap) * match_ratio)
                else:
                    total = leader_cap
                override_reason = "weak_leader"

            # Signal 2: Backfill Discount
            # When few competitors have the keyword in their title AND
            # the leader is genuinely weak (< 1000 reviews), most
            # results are generic backfill from broader search terms.
            #
            # Smooth ramp instead of binary 0.6× cutoff:
            #   discount = 0.6 + 2.0 * match_ratio  (clamped to [0.6, 1.0])
            # So match_ratio=0 → 0.6×, 0.1 → 0.8×, 0.2 → 1.0× (no discount).
            # Leader strength further modulates: stronger leaders
            # mean less discount via log interpolation.
            if match_ratio < 0.2 and leader_reviews < 1_000 and not is_brand_keyword:
                # Base discount from match ratio (smooth ramp)
                ratio_factor = min(1.0, 0.6 + 2.0 * match_ratio)
                # Leader strength factor: stronger leaders = less discount
                leader_factor = math.log10(leader_reviews + 1) / math.log10(1001)
                # Blend: at leader_factor=0 use full ratio_factor,
                # at leader_factor=1 use no discount
                discount = ratio_factor + (1.0 - ratio_factor) * leader_factor
                discount = max(0.6, min(1.0, discount))
                discounted = max(1, int(total * discount))
                if discounted < total:
                    total = discounted
                    override_reason = "backfill"

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
                        f"Brand keyword — '{kw_lower}' matches publisher "
                        f"{brand_name}. The #1 app ({leader_name}) has few "
                        f"reviews because it's a brand companion app, not "
                        f"because the keyword is easy. Difficulty reflects "
                        f"the full competitive landscape."
                    ),
                },
            )

        # Add override insight at the top if score was adjusted
        if override_reason and raw_total != total:
            if override_reason == "small_result_set":
                override_text = (
                    f"Score adjusted from {raw_total} → {total}. "
                    f"Only {n} app{'s' if n > 1 else ''} found for this "
                    f"keyword — very little competition exists."
                )
            else:
                leader_name = competitors[0].get("trackName", "#1 app")
                if match_ratio > 0.3:
                    # Many competitors target this keyword — not backfill,
                    # just a weak leader in a competitive field.
                    override_text = (
                        f"Score adjusted from {raw_total} → {total}. "
                        f"The #1 app ({leader_name}) has only "
                        f"{leader_reviews:,} "
                        f"review{'s' if leader_reviews != 1 else ''}, "
                        f"but {title_match_count} of {n} competitors "
                        f"target this keyword — real competition exists."
                    )
                else:
                    override_text = (
                        f"Score adjusted from {raw_total} → {total}. "
                        f"The #1 app ({leader_name}) has only "
                        f"{leader_reviews:,} "
                        f"review{'s' if leader_reviews != 1 else ''}. "
                        f"The remaining results are generic backfill "
                        f"from broader search terms — not real "
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
        now = datetime.now(timezone.utc)
        kw_lower = keyword.lower().strip()
        kw_words = set(kw_lower.split()) if kw_lower else set()
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
                    "highlights": ["No competitors found — wide open."],
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
                # Weak leader cap (based on overall leader)
                tier_cap = None
                if overall_leader_reviews < 1_000 and not is_brand_keyword:
                    tier_cap = int(
                        15 + 35 * math.log10(overall_leader_reviews + 1) / math.log10(1001)
                    )
                if tier_cap is not None and tier_score > tier_cap:
                    if overall_match_ratio > 0.2:
                        tier_score = int(
                            tier_cap
                            + (tier_score - tier_cap) * overall_match_ratio
                        )
                    else:
                        tier_score = tier_cap

                # Backfill discount (based on overall match ratio)
                if overall_match_ratio < 0.2 and overall_leader_reviews < 1_000 and not is_brand_keyword:
                    ratio_factor = min(1.0, 0.6 + 2.0 * overall_match_ratio)
                    leader_factor = math.log10(overall_leader_reviews + 1) / math.log10(1001)
                    discount = ratio_factor + (1.0 - ratio_factor) * leader_factor
                    discount = max(0.6, min(1.0, discount))
                    tier_score = max(1, int(tier_score * discount))

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
                            release_date.replace("Z", "+00:00")
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
            for tier_key in tiers:
                if tiers[tier_key]["tier_score"] < overall_score:
                    tiers[tier_key]["tier_score"] = overall_score

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

        for tier_key in tiers:
            tiers[tier_key]["label"] = _score_to_label(
                tiers[tier_key]["tier_score"]
            )

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
            if tiers["top_10"]["tier_score"] > tiers["top_5"]["tier_score"]:
                tiers["top_10"]["tier_score"] = tiers["top_5"]["tier_score"]
            tiers["top_10"]["label"] = _cap_label(
                tiers["top_10"]["label"], tiers["top_5"]["label"]
            )
        if "top_10" in tiers and "top_20" in tiers:
            if tiers["top_20"]["tier_score"] > tiers["top_10"]["tier_score"]:
                tiers["top_20"]["tier_score"] = tiers["top_10"]["tier_score"]
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
                f"Only {n} app{'s' if n != 1 else ''} rank here "
                f"— {open_spots} open spot{'s' if open_spots != 1 else ''}."
            )
            return highlights

        # Review barrier
        if min_reviews < 100:
            highlights.append(
                f"The easiest app to beat has just {min_reviews:,} reviews."
            )
        elif min_reviews < 1_000:
            highlights.append(
                f"You need ~{min_reviews:,}+ reviews to compete "
                f"(weakest: {weakest_app})."
            )
        elif min_reviews < 10_000:
            highlights.append(
                f"You need ~{min_reviews:,}+ reviews to break in."
            )
        else:
            highlights.append(
                f"Requires ~{min_reviews:,}+ reviews — established market."
            )

        # Weak spots
        if weak > 0:
            highlights.append(
                f"{weak} of {n} apps have under 1K reviews — beatable."
            )
        else:
            highlights.append("Every app here has 1K+ reviews — no easy targets.")

        # Fresh entrants
        if fresh > 0:
            highlights.append(
                f"{fresh} app{'s' if fresh != 1 else ''} "
                f"broke in within the last year."
            )

        # Title keyword usage
        if title_opt == 0:
            highlights.append(
                "No app uses this exact keyword in its title — ASO opportunity!"
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
                        "reviews — dominated by major brands"
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
                        "reviews — strong incumbents"
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
                        f"Review distribution is skewed — median "
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
                        "title — potential title optimization gap"
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
                        f"High quality bar — avg rating is "
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
                        "reviews — beatable with a quality app"
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
                        f"{len(weak_apps)} of {n} apps have <1,000 reviews."
                        f" The weakest ({weakest.get('trackName', 'Unknown')})"
                        f" has only "
                        f"{weakest.get('userRatingCount', 0):,} reviews — "
                        "these positions are displaceable."
                    ),
                }
            )

        # Fresh entrants (released in last 12 months)
        now = datetime.now(timezone.utc)
        fresh_apps = []
        for c in competitors:
            release_date = c.get("releaseDate", "")
            if release_date:
                try:
                    released = datetime.fromisoformat(
                        release_date.replace("Z", "+00:00")
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
                        "in the last 12 months — this market is still "
                        "attracting new entrants."
                    ),
                }
            )

        # Niche genre diversity
        genres = set(
            c.get("primaryGenreName", "")
            for c in competitors
            if c.get("primaryGenreName")
        )
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
                        "to one category — a well-positioned app in any "
                        "genre could rank."
                    ),
                }
            )

        return signals

    def _rating_volume_score(self, median_ratings: float) -> float:
        """
        Map median rating count to a 0-100 score using log scale.

        Uses median instead of mean to prevent outliers from skewing.
        Calibrated for realistic App Store competition:
          <50 median  → ~5   (ghost town)
          <200 median → ~15  (very low competition)
          <500 median → ~30  (indie-friendly)
          <2,000      → ~50  (moderate, needs effort)
          <5,000      → ~65  (competitive)
          <10,000     → ~78  (hard)
          <25,000     → ~88  (very hard)
          <100,000    → ~95  (dominated)
          ≥100,000    → 100  (impossible for indie)
        """
        if median_ratings <= 0:
            return 0
        if median_ratings >= 100_000:
            return 100

        # Logarithmic interpolation between calibration points
        bands = [
            (50, 5),
            (200, 15),
            (500, 30),
            (2_000, 50),
            (5_000, 65),
            (10_000, 78),
            (25_000, 88),
            (100_000, 95),
        ]

        for i, (threshold, score) in enumerate(bands):
            if median_ratings < threshold:
                if i == 0:
                    # Linear interpolation from 0 to first band
                    return (median_ratings / threshold) * score
                prev_threshold, prev_score = bands[i - 1]
                ratio = math.log(median_ratings / prev_threshold) / math.log(
                    threshold / prev_threshold
                )
                return prev_score + ratio * (score - prev_score)

        return 100

    def _review_velocity_score(self, competitors: list[dict]) -> float:
        """
        Calculate difficulty component from review velocity (reviews/year).

        Uses median velocity across competitors, log-scaled.
        Distinguishes active, growing markets from stagnant ones.

        Calibration:
          <10/yr   → ~5   (dead apps)
          <50/yr   → ~15  (minimal traction)
          <200/yr  → ~30  (slow growth)
          <1000/yr → ~50  (moderate, indie-level)
          <5000/yr → ~70  (solid growth)
          <20000/yr→ ~85  (strong growth)
          <50000/yr→ ~95  (top-charts velocity)
          ≥50000/yr→ 100  (viral / premium)
        """
        now = datetime.now(timezone.utc)
        velocities = []
        for c in competitors:
            reviews = c.get("userRatingCount", 0)
            release_date = c.get("releaseDate", "")
            if release_date and reviews > 0:
                try:
                    released = datetime.fromisoformat(
                        release_date.replace("Z", "+00:00")
                    )
                    age_years = max(0.5, (now - released).days / 365.25)
                    velocities.append(reviews / age_years)
                except (ValueError, TypeError):
                    pass

        if not velocities:
            return 50  # default mid-range

        # Use median velocity (robust to outliers)
        velocities.sort()
        vn = len(velocities)
        if vn % 2 == 1:
            median_vel = velocities[vn // 2]
        else:
            median_vel = (velocities[vn // 2 - 1] + velocities[vn // 2]) / 2

        if median_vel <= 0:
            return 0
        if median_vel >= 50_000:
            return 100

        bands = [
            (10, 5),
            (50, 15),
            (200, 30),
            (1_000, 50),
            (5_000, 70),
            (20_000, 85),
            (50_000, 95),
        ]

        for i, (threshold, score) in enumerate(bands):
            if median_vel < threshold:
                if i == 0:
                    return (median_vel / threshold) * score
                prev_threshold, prev_score = bands[i - 1]
                ratio = math.log(median_vel / prev_threshold) / math.log(
                    threshold / prev_threshold
                )
                return prev_score + ratio * (score - prev_score)

        return 100

    def _rating_quality_score(self, avg_quality: float) -> float:
        """
        Map average rating quality to a 0-100 normalized score.

        Higher avg ratings = harder to compete = higher score.
        Uses smooth linear interpolation between calibration points
        instead of discrete steps.

        Calibration:
          0.0 → 0
          3.0 → 20
          3.5 → 35
          4.0 → 50
          4.3 → 70
          4.5 → 85
          5.0 → 100
        """
        if avg_quality <= 0:
            return 0
        if avg_quality >= 5.0:
            return 100

        bands = [
            (0.0, 0),
            (3.0, 20),
            (3.5, 35),
            (4.0, 50),
            (4.3, 70),
            (4.5, 85),
            (5.0, 100),
        ]

        for i in range(1, len(bands)):
            threshold, score = bands[i]
            if avg_quality < threshold:
                prev_threshold, prev_score = bands[i - 1]
                ratio = (avg_quality - prev_threshold) / (
                    threshold - prev_threshold
                )
                return prev_score + ratio * (score - prev_score)

        return 100

    def _market_age_score(self, competitors: list[dict]) -> float:
        """
        Map average market age to a 0-100 normalized score.

        Older markets = more entrenched = harder to enter.
          <1 year → 20
          1-2 years → 35
          2-3 years → 50
          3-5 years → 70
          5-8 years → 85
          >8 years → 100
        """
        now = datetime.now(timezone.utc)
        ages = []
        for c in competitors:
            release_date = c.get("releaseDate", "")
            if release_date:
                try:
                    released = datetime.fromisoformat(
                        release_date.replace("Z", "+00:00")
                    )
                    age_years = (now - released).days / 365.25
                    ages.append(age_years)
                except (ValueError, TypeError):
                    pass

        if not ages:
            return 50  # default mid-range

        avg_age = sum(ages) / len(ages)

        # Smooth linear interpolation between calibration points
        # (consistent with _rating_volume_score, _review_velocity_score).
        if avg_age <= 0:
            return 0
        if avg_age >= 10:
            return 100

        age_bands = [
            (0.5, 10),
            (1.0, 20),
            (2.0, 35),
            (3.0, 50),
            (5.0, 70),
            (8.0, 85),
            (10.0, 100),
        ]
        for i, (threshold, score) in enumerate(age_bands):
            if avg_age < threshold:
                if i == 0:
                    return (avg_age / threshold) * score
                prev_t, prev_s = age_bands[i - 1]
                ratio = (avg_age - prev_t) / (threshold - prev_t)
                return prev_s + ratio * (score - prev_s)
        return 100
