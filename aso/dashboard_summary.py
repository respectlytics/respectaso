"""App Summary aggregation for the Dashboard.

Computes per-app, per-country aggregates from the user's tracked keyword
results so the Dashboard can show a 30-second read of ASO posture without
duplicating anything the Search History table already shows.

Design rules (see docs/development/DASHBOARD_APP_SUMMARY_PLAN.md):
- No bucket-based value judgments anywhere (no "Sweet Spot is good" copy).
- Estimated downloads are always intervals, never point estimates.
- Hide entirely when no app selected, or app has zero rankings anywhere.
- Anti-duplication: nothing here may repeat what the History table shows
  on a per-keyword basis. Only aggregates, distributions, and top-N picks.
"""

from collections import defaultdict
from datetime import timedelta
from django.utils import timezone

from .models import SearchResult


def _safe_estimates(result):
    """Download estimates for a SearchResult (effective-popularity based).

    Recomputed from the effective popularity so summary numbers always agree
    with the popularity column, including right after a source switch.
    """
    est = result.effective_download_estimates
    if not est or not est.get("positions"):
        return None
    return est


def _current_dl_interval(result):
    """(low, high) estimated daily downloads at the keyword's CURRENT rank.

    Unranked keywords return (0, 0) — no false floor of "you're already
    getting downloads from this".
    """
    rank = result.app_rank
    if rank is None or rank < 1:
        return 0.0, 0.0
    est = _safe_estimates(result)
    if est is None:
        return 0.0, 0.0
    positions = est["positions"]
    if rank > len(positions):
        # Ranked outside our model's tracked positions (#1–#20). Below #20
        # we treat as effectively zero traffic — same as unranked.
        return 0.0, 0.0
    p = positions[rank - 1]
    return float(p["downloads_low"]), float(p["downloads_high"])


def _potential_dl_interval(result):
    """(low, high) estimated daily downloads at rank #1 — the ceiling."""
    est = _safe_estimates(result)
    if est is None:
        return 0.0, 0.0
    pos = est["positions"][0]
    return float(pos["downloads_low"]), float(pos["downloads_high"])


def _bucket_for_rank(rank):
    """Map a rank to one of T5 / T10 / T20 / T50 / T100 / Unranked."""
    if rank is None:
        return "unranked"
    if rank <= 5:
        return "t5"
    if rank <= 10:
        return "t10"
    if rank <= 20:
        return "t20"
    if rank <= 50:
        return "t50"
    if rank <= 100:
        return "t100"
    return "t200"  # 101–200; we still track these but they contribute ~0 traffic


def _stale_state(last_refresh):
    """Return 'fresh', 'stale_24h', or 'stale_7d' based on last_refresh."""
    if last_refresh is None:
        return "fresh"
    age = timezone.now() - last_refresh
    if age > timedelta(days=7):
        return "stale_7d"
    if age > timedelta(hours=24):
        return "stale_24h"
    return "fresh"


def _aggregate_country(results):
    """Aggregate the per-country row from a list of SearchResult.

    Returns a dict with the columns the per-country strip renders.
    """
    total_low = 0.0
    total_high = 0.0
    potential_low = 0.0
    potential_high = 0.0
    ranks = []
    in_t20 = 0
    best_dl_high = -1.0
    top_performer = None
    biggest_gap = None
    biggest_gap_score = -1.0

    for r in results:
        cur_low, cur_high = _current_dl_interval(r)
        pot_low, pot_high = _potential_dl_interval(r)
        total_low += cur_low
        total_high += cur_high
        potential_low += pot_low
        potential_high += pot_high

        if r.app_rank is not None:
            ranks.append(r.app_rank)
            if r.app_rank <= 20:
                in_t20 += 1

        # Top performer: highest current downloads (high-end of interval as tiebreaker).
        # Celebrates whatever's working — competitive or niche, both qualify.
        if cur_high > best_dl_high:
            best_dl_high = cur_high
            if cur_high > 0:
                top_performer = {
                    "keyword": r.keyword.keyword,
                    "rank": r.app_rank,
                    "downloads_low": cur_low,
                    "downloads_high": cur_high,
                    "popularity": r.effective_popularity,
                }

        # Biggest gap: keyword with the largest potential headroom (downloads at #1
        # minus downloads at current rank). Pure number — no bucket judgment.
        gap = pot_high - cur_high
        # Need either popularity or just a meaningful gap to qualify
        pop = r.effective_popularity
        if gap > biggest_gap_score and pop is not None and pop >= 5:
            biggest_gap_score = gap
            biggest_gap = {
                "keyword": r.keyword.keyword,
                "rank": r.app_rank,
                "popularity": pop,
                "headroom_low": max(0.0, pot_low - cur_high),
                "headroom_high": gap,
            }

    # Headroom for the country = potential - current. Clipped to >= 0 to avoid
    # weird negative ranges when intervals overlap.
    headroom_low = max(0.0, potential_low - total_high)
    headroom_high = max(0.0, potential_high - total_low)

    return {
        "downloads_low": total_low,
        "downloads_high": total_high,
        "headroom_low": headroom_low,
        "headroom_high": headroom_high,
        "best_rank": min(ranks) if ranks else None,
        "in_top_20": in_t20,
        "total_keywords": len(results),
        "ranking_keywords": len(ranks),
        "top_performer": top_performer,
        "biggest_gap": biggest_gap,
    }


def _rank_distribution(results):
    """Count keywords in each rank tier across the given results."""
    dist = {"t5": 0, "t10": 0, "t20": 0, "t50": 0, "t100": 0, "t200": 0, "unranked": 0}
    for r in results:
        dist[_bucket_for_rank(r.app_rank)] += 1
    return dist


def _bucket_distribution(results):
    """Count keywords per insight classification. No value judgments — diagnostic shape only."""
    dist = defaultdict(int)
    for r in results:
        dist[r.classification or "Moderate"] += 1
    return dict(dist)


def _net_movement(results):
    """Count gainers vs losers since the previous refresh, and the earliest 'since'.

    Each result already carries `rank_delta` annotated by the view, but the
    summary needs the full set, not just the page. For unannotated results
    we fall back to querying the previous SearchResult.
    """
    up = 0
    down = 0
    earliest_prev = None

    for r in results:
        delta = getattr(r, "rank_delta", None)
        prev_searched_at = None

        if delta is None and not hasattr(r, "_summary_prev_checked"):
            prev = (
                SearchResult.objects.filter(
                    keyword_id=r.keyword_id,
                    country=r.country,
                    searched_at__lt=r.searched_at,
                )
                .order_by("-searched_at")
                .only("app_rank", "searched_at")
                .first()
            )
            if prev and prev.app_rank is not None and r.app_rank is not None:
                delta = prev.app_rank - r.app_rank
            prev_searched_at = prev.searched_at if prev else None
            r._summary_prev_checked = True
        elif hasattr(r, "prev_searched_at"):
            prev_searched_at = r.prev_searched_at

        if delta is not None:
            if delta > 0:
                up += 1
            elif delta < 0:
                down += 1

        if prev_searched_at and (earliest_prev is None or prev_searched_at > earliest_prev):
            earliest_prev = prev_searched_at

    return {"up": up, "down": down, "since": earliest_prev}


def _build_callouts(country_rows, total_keywords, total_countries, bucket_dist, all_results):
    """Generate up to 3 fact-based callouts. Never normative about buckets."""
    callouts = []

    # 1) Top 3 keywords driving most of estimated downloads
    by_dl = sorted(
        all_results,
        key=lambda r: _current_dl_interval(r)[1],
        reverse=True,
    )
    by_dl = [r for r in by_dl if _current_dl_interval(r)[1] > 0]
    if len(by_dl) >= 3:
        total_high = sum(_current_dl_interval(r)[1] for r in by_dl) or 1.0
        top3_high = sum(_current_dl_interval(r)[1] for r in by_dl[:3])
        share_pct = round(top3_high / total_high * 100)
        if share_pct >= 60:  # only flag when concentration is meaningful
            names = ", ".join(f"`{r.keyword.keyword}`" for r in by_dl[:3])
            callouts.append(
                f"Your top 3 keywords drive ~{share_pct}% of estimated downloads: {names}."
            )

    # 2) Largest single rank gap — quantified, no bucket label
    biggest_gap_overall = None
    for row in country_rows:
        g = row.get("biggest_gap")
        if g and (
            biggest_gap_overall is None
            or g["headroom_high"] > biggest_gap_overall["headroom_high"]
        ):
            biggest_gap_overall = {**g, "country": row["country"]}
    if biggest_gap_overall and biggest_gap_overall["headroom_high"] >= 5:
        rank_part = (
            f"#{biggest_gap_overall['rank']}"
            if biggest_gap_overall["rank"] is not None
            else "not ranking"
        )
        gap_n = _format_dl_number(biggest_gap_overall["headroom_high"])
        callouts.append(
            f"`{biggest_gap_overall['keyword']}` has the largest rank gap "
            f"({rank_part}, popularity {biggest_gap_overall['popularity']}) — "
            f"closing it could add up to ~{gap_n}/day."
        )

    # 3) Single-country prompt — descriptive, not prescriptive
    if total_countries == 1 and total_keywords >= 5:
        callouts.append(
            "You track 1 country. Same keywords in 2–3 storefronts "
            "often add comparable headroom."
        )

    # 4) House-cleaning — keywords contributing zero downloads
    zero_dl_count = sum(
        1 for r in all_results
        if _current_dl_interval(r) == (0.0, 0.0) and r.app_rank is None
    )
    if zero_dl_count >= 5 and len(callouts) < 3:
        callouts.append(
            f"{zero_dl_count} tracked keywords contribute ~0 downloads "
            f"(not ranking) — consider pruning or running AI Researcher to find replacements."
        )

    return callouts[:3]


def _build_cta(total_keywords, total_countries, country_rows, bucket_dist):
    """Pick the most-relevant AI CTA based on the data shape.

    Returns dict with `message` and `buttons` (list of {label, url, primary}).
    """
    if total_keywords == 0:
        return None  # show/hide gate will hide the whole panel anyway

    if total_keywords < 20:
        return {
            "headline": "Expand your keyword coverage",
            "message": (
                "Most apps need 40–80 keywords to see a representative picture. "
                "Discover new ones with AI Researcher, or mine your existing "
                "metadata for missed combinations with AI Simulator."
            ),
            "buttons": [
                {"label": "AI Researcher", "url": "ai_researcher", "primary": True},
                {"label": "AI Simulator", "url": "simulator", "primary": False},
            ],
        }

    if total_countries == 1:
        return {
            "headline": "Try new storefronts",
            "message": (
                "You're tracking one country. AI Researcher's country opportunity "
                "finder surfaces underserved markets where the same keywords convert."
            ),
            "buttons": [
                {"label": "AI Researcher", "url": "ai_researcher", "primary": True},
            ],
        }

    # Flat ranks heuristic — many keywords tracked but most outside Top 50
    in_t50 = sum(
        1 for row in country_rows
        for _ in range(1) if row["ranking_keywords"]  # only meaningful if any rankings
    )
    deep_count = sum(
        1 for row in country_rows
        if row["total_keywords"] and row["ranking_keywords"] < row["total_keywords"] * 0.4
    )
    if deep_count >= len(country_rows) / 2:
        return {
            "headline": "Your coverage is wide — try moving the ranks",
            "message": (
                "You track plenty of keywords but most aren't ranking. "
                "AI Simulator lets you test metadata combinations from your "
                "current title, subtitle and keyword field to find what moves the needle."
            ),
            "buttons": [
                {"label": "AI Simulator", "url": "simulator", "primary": True},
            ],
        }

    # Healthy default
    return {
        "headline": "Looking solid",
        "message": (
            "Use AI Simulator to stress-test metadata changes before shipping "
            "an update — see how new wording would shift your ranks."
        ),
        "buttons": [
            {"label": "AI Simulator", "url": "simulator", "primary": True},
        ],
    }


def _format_dl_number(value):
    """Render a downloads number for the App Summary.

    Mirrors ``aso.templatetags.aso_tags._fmt_dl`` (the History table's number
    formatter) so the same underlying value renders identically in both panels.
    Without this alignment, a keyword's #1 download estimate showing as ``27``
    in History would round to ``30`` in the Summary's biggest-opportunity cell
    and create a confusing visible mismatch.

    Rules (matching ``_fmt_dl``):
      - >= 1000 → "1.2K" style (one decimal, trailing .0 stripped)
      - < 1     → one decimal ("0.4")
      - 1..10   → one decimal, trailing .0 stripped ("7.5", "8")
      - >= 10   → nearest integer ("27", "109")
    """
    try:
        n = float(value) if value is not None else 0.0
    except (TypeError, ValueError):
        return "0"
    if n <= 0:
        return "0"
    if n >= 1000:
        s = f"{n / 1000:.1f}"
        return (s[:-2] if s.endswith(".0") else s) + "K"
    if n < 1:
        return f"{n:.1f}"
    if n < 10:
        s = f"{n:.1f}"
        return s[:-2] if s.endswith(".0") else s
    return str(round(n))


def format_interval(low, high):
    """Render a (low, high) interval as e.g. '~120–180' (no '/day' suffix)."""
    if (low is None or low <= 0) and (high is None or high <= 0):
        return "—"
    lo = _format_dl_number(low)
    hi = _format_dl_number(high)
    if lo == hi or low <= 0:
        return f"~{hi}"
    return f"~{lo}–{hi}"


def compute_app_summary(selected_app, selected_app_name, last_refresh=None):
    """Compute the App Summary panel data for the selected app.

    Returns None if the summary should be hidden (no app selected, or no
    ranking keywords anywhere). Otherwise returns a dict the template can
    iterate over.

    ``last_refresh`` is accepted for compatibility but recomputed internally
    so it always reflects the full app set, never a country-filtered view.
    """
    if not selected_app:
        return None

    # Pull the latest result for every keyword+country pair for this app —
    # NOT scoped by country filter, NOT scoped by other filters. The summary
    # always reflects the full tracked set for the app.
    from django.db.models import Max

    latest_ids = list(
        SearchResult.objects
        .filter(keyword__app_id=selected_app)
        .values("keyword_id", "country")
        .annotate(latest_id=Max("id"))
        .values_list("latest_id", flat=True)
    )
    if not latest_ids:
        return None

    results = list(
        SearchResult.objects
        .filter(id__in=latest_ids)
        .select_related("keyword")
    )

    # Recompute last_refresh app-scoped so a user filtering History to one
    # country doesn't make the summary report a stale-looking timestamp.
    last_refresh = max((r.searched_at for r in results), default=None)

    # Show/hide gate: at least one keyword must actually rank somewhere.
    if not any(r.app_rank is not None for r in results):
        return None

    # Group by country
    by_country = defaultdict(list)
    for r in results:
        by_country[r.country].append(r)

    countries_sorted = sorted(by_country.keys())
    country_rows = []
    for cc in countries_sorted:
        row = _aggregate_country(by_country[cc])
        row["country"] = cc
        country_rows.append(row)

    # Sort per-country rows by current downloads_high desc so the country
    # contributing most traffic appears first.
    country_rows.sort(key=lambda r: r["downloads_high"], reverse=True)

    # Cap at 5 visible; collapse rest behind an expander in the template.
    visible_rows = country_rows[:5]
    overflow_count = max(0, len(country_rows) - 5)

    bucket_dist = _bucket_distribution(results)
    rank_dist = _rank_distribution(results)
    movement = _net_movement(results)
    stale_state = _stale_state(last_refresh)

    # Aggregate totals across countries (for the panel header chip only —
    # never displayed as a single "downloads" KPI to avoid cross-storefront sums).
    total_keywords = len(results)
    total_countries = len(by_country)

    is_multi_country = total_countries > 1

    # Single-country headline KPIs (only computed when one country tracked)
    headline = None
    if not is_multi_country:
        row = country_rows[0]
        headline = {
            "downloads_low": row["downloads_low"],
            "downloads_high": row["downloads_high"],
            "headroom_low": row["headroom_low"],
            "headroom_high": row["headroom_high"],
            "movers_up": movement["up"],
            "movers_down": movement["down"],
            "since": movement["since"],
            "rank_dist": rank_dist,
            "ranking_keywords": row["ranking_keywords"],
            "total_keywords": total_keywords,
            "best_rank": row["best_rank"],
        }

    callouts = _build_callouts(country_rows, total_keywords, total_countries, bucket_dist, results)
    cta = _build_cta(total_keywords, total_countries, country_rows, bucket_dist)

    return {
        "app_name": selected_app_name,
        "countries": countries_sorted,
        "country_count": total_countries,
        "total_keywords": total_keywords,
        "is_multi_country": is_multi_country,
        "headline": headline,
        "country_rows": visible_rows,
        "overflow_count": overflow_count,
        "rank_dist": rank_dist,
        "bucket_dist": bucket_dist,
        "movement": movement,
        "stale_state": stale_state,  # 'fresh' | 'stale_24h' | 'stale_7d'
        "last_refresh": last_refresh,
        "callouts": callouts,
        "cta": cta,
        # Apple Ads impression share (None when the app has no rows -
        # Apple only reports terms where the app's own ads served, so the
        # template renders the section only when data exists).
        "impression_share": _impression_share_summary(selected_app),
    }


def _impression_share_summary(app_id):
    """Latest-week impression-share rows for the app, display-ready.

    Returns None when no rows exist (the common case: no ads serving) -
    no dead UI ever renders for it.
    """
    from datetime import timedelta

    from .models import AppleImpressionShare

    latest_week = (
        AppleImpressionShare.objects.filter(app_id=app_id)
        .order_by("-week")
        .values_list("week", flat=True)
        .first()
    )
    if latest_week is None:
        return None
    rows = list(
        AppleImpressionShare.objects.filter(app_id=app_id, week=latest_week)
        .order_by("-low_share")[:8]
    )
    previous = {
        (r.search_term, r.country): r.low_share
        for r in AppleImpressionShare.objects.filter(
            app_id=app_id, week=latest_week - timedelta(days=7)
        )
    }

    def share_label(row):
        if row.high_share == 0:
            return "under 1%"
        if row.low_share >= 0.91:
            return "91-100%"
        return f"{round(row.low_share * 100)}%"

    display = []
    for row in rows:
        prev_share = previous.get((row.search_term, row.country))
        delta = None
        if prev_share is not None:
            moved = round((row.low_share - prev_share) * 100)
            delta = moved if abs(moved) >= 2 else None
        display.append({
            "term": row.search_term,
            "country": row.country.upper(),
            "share": share_label(row),
            "rank": row.rank,
            "tier": row.popularity_tier,
            "delta": delta,
        })
    return {"week": latest_week, "rows": display}
