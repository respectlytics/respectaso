"""Measure where apps actually rank, to set the rank model from data.

The opportunity score needs one thing it cannot see directly: where an app
of a given strength lands for a keyword of a given difficulty. Every App
Store search answers that for up to 200 apps at once, so this study asks it
directly instead of assuming it.

For a sample of real search terms (the synced Apple top-terms dataset, plus
long-tail negatives) in several storefronts it records, for every app in the
200 results whose title contains all of the keyword's words (it targets the
keyword): the keyword's difficulty from its top 25, exactly as the app
computes it; the app's strength on the shared yardstick (aso.strength), with
the title factor at 100; and the rank Apple gave it.

It then fits the rank model on 70% of the terms and judges it on the other
30%, against gates fixed in docs/development/STRENGTH_AND_RANK_CALIBRATION_PLAN.md
before any data was seen:

    R1  held-out Spearman(predicted, observed) >= 0.25, and above the
        current model's
    R2  held-out median |log2 predicted - log2 observed| below the current
        model's

The first run failed R1, and the analysis of why (plan, D3c) replaced the
median rank with the expected tap-through of apps at a gap, expressed as the
rank that pays the same. That model is judged by its own gates, fixed before
it was fitted:

    E1  held-out Spearman(predicted, realized tap-through) >= 0.25
    E2  held-out calibration: median over five gap groups of
        |log2(mean predicted / mean realized tap-through)| below 0.5, and
        below the current model's

It also describes the spread the effective rank averages over, for the
words on screen (TYPICAL_BY_GAP in aso/scoring.py): per gap, the rank the
middle app lands at and the share that reach the top 10, over every
observation. A score counts downloads, so it uses the effective rank; a
person asks where an app will land, so the screens say the typical rank.

Read-only for the product: it prints the fitted tables and the gate results;
shipping the table is a code change made by hand. ``--cache`` keeps the raw
observations, so a refit never refetches.

    manage.py rank_calibration_study --cache PATH
        [--countries us,br,fr,ca,mx,it] [--per-country 50] [--negatives 10]
"""

import datetime as dt
import json
import math
import os
import random
import time
import zlib

from django.core.management.base import BaseCommand, CommandError

BUCKETS = [(40, 45), (45, 50), (50, 55), (55, 60), (60, 65),
           (65, 70), (70, 80), (80, 101)]
NEGATIVE_SUFFIXES = ["for seniors", "with widget", "no ads", "log book",
                     "offline free", "for couples", "dark mode",
                     "for small business", "voice notes", "printable"]
GAP_BIN = 5.0
MIN_BIN = 25
HELD_OUT_SHARE = 0.30
R1_MIN_SPEARMAN = 0.25
E1_MIN_SPEARMAN = 0.25
E2_MAX_LOG2 = 0.5
E2_GROUPS = 5


class Command(BaseCommand):
    help = "Measure where apps rank for keywords, to fit the rank model."

    def add_arguments(self, parser):
        parser.add_argument("--countries", default="us,br,fr,ca,mx,it")
        parser.add_argument("--per-country", type=int, default=50)
        parser.add_argument("--negatives", type=int, default=10)
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--cache", required=True,
                            help="JSON file for the raw observations; reused if present.")

    def handle(self, *args, **options):
        random.seed(options["seed"])
        cache = options["cache"]
        if os.path.exists(cache):
            searches = json.load(open(cache))
            self.stdout.write(f"Using {len(searches)} cached searches from {cache}")
        else:
            searches = self._fetch(options)
            json.dump(searches, open(cache, "w"))
            self.stdout.write(f"Cached {len(searches)} searches to {cache}")
        self._fit_and_judge(searches, options["seed"])

    # ── collecting ────────────────────────────────────────────────────────

    def _fetch(self, options):
        from aso.apple_ads import storage
        from aso.models import AppleTopTerm
        from aso.services import ITunesSearchService, SearchAPIUnavailableError

        weeks = storage.load_apple_settings()["apple_ads"]["active_weeks"]
        work = []
        for country in [c.strip() for c in options["countries"].split(",") if c.strip()]:
            week = weeks.get(country)
            if not week:
                raise CommandError(f"No synced Apple dataset for '{country}'.")
            rows = list(AppleTopTerm.objects.filter(
                country=country, week=dt.date.fromisoformat(week),
            ).values("term", "popularity"))
            best = {}
            for row in rows:
                if row["term"] not in best or row["popularity"] > best[row["term"]]["popularity"]:
                    best[row["term"]] = row
            per_bucket = max(1, options["per_country"] // len(BUCKETS))
            for lo, hi in BUCKETS:
                bucket = [r["term"] for r in best.values() if lo <= r["popularity"] < hi]
                random.shuffle(bucket)
                work.extend((country, term) for term in bucket[:per_bucket])
            short = [t for t in best if len(t.split()) <= 2]
            random.shuffle(short)
            negatives = []
            for term in short:
                for suffix in NEGATIVE_SUFFIXES:
                    candidate = f"{term} {suffix}"
                    if candidate not in best:
                        negatives.append(candidate)
                        break
                if len(negatives) >= options["negatives"]:
                    break
            work.extend((country, term) for term in negatives)

        random.shuffle(work)
        self.stdout.write(f"Fetching {len(work)} searches of up to 200 results...")
        itunes = ITunesSearchService()
        searches = []
        for index, (country, term) in enumerate(work):
            for attempt in range(3):
                try:
                    results = itunes.search_apps(term, country=country, limit=200)
                    break
                except SearchAPIUnavailableError:
                    time.sleep(30 * (attempt + 1))
            else:
                continue
            if results:
                searches.append({
                    "country": country, "term": term,
                    "apps": [{
                        "trackId": a.get("trackId"), "trackName": a.get("trackName", ""),
                        "userRatingCount": a.get("userRatingCount", 0),
                        "averageUserRating": a.get("averageUserRating"),
                        "releaseDate": a.get("releaseDate", ""),
                        "currentVersionReleaseDate": a.get("currentVersionReleaseDate", ""),
                        "primaryGenreName": a.get("primaryGenreName", ""),
                        "sellerName": a.get("sellerName", ""),
                    } for a in results],
                })
            if index and index % 25 == 0:
                self.stdout.write(f"  ...{index}/{len(work)}")
            time.sleep(1.6)
        return searches

    # ── fitting ───────────────────────────────────────────────────────────

    def _observations(self, searches):
        from aso.scoring import _new_app_strength
        from aso.services import DifficultyCalculator, _keyword_title_evidence
        from aso.strength import AppProfile, volume_score

        calc = DifficultyCalculator()
        obs = []
        for search in searches:
            apps = search["apps"]
            difficulty = calc.calculate(apps[:25], keyword=search["term"])[0]
            for index, app in enumerate(apps):
                evidence = _keyword_title_evidence(
                    search["term"].lower(), app.get("trackName", ""), app.get("primaryGenreName", ""),
                )
                if not (evidence["exact_phrase"] or evidence["all_words"]):
                    continue
                profile = AppProfile.from_result(app)
                strength = profile.strength(targeted=True)
                obs.append({
                    "term": f"{search['country']}:{search['term']}",
                    "difficulty": difficulty,
                    "strength": strength,
                    "gap": difficulty - strength,
                    "rank": index + 1,
                    # The model this study may replace: rank from the rating
                    # count alone (strength = volume / 100), on the hand-set
                    # curve.
                    "current": min(200.0, _previous_rank(
                        difficulty / 100 - volume_score(profile.ratings) / 100)),
                    # The fallback if the fit fails: the same hand-set curve,
                    # fed by the full strength instead of the count alone.
                    "fallback": min(200.0, _previous_rank(
                        (difficulty - strength + _new_app_strength()) / 100)),
                })
        return obs

    def _fit_and_judge(self, searches, seed):
        obs = self._observations(searches)
        if len(obs) < 500:
            raise CommandError(f"Only {len(obs)} observations; too few to fit.")

        def held_out(term):
            return (zlib.crc32(f"{seed}:{term}".encode()) % 1000) / 1000 < HELD_OUT_SHARE

        train = [o for o in obs if not held_out(o["term"])]
        test = [o for o in obs if held_out(o["term"])]
        knots = _fit_knots(train)
        for o in test:
            o["fitted"] = min(200.0, _predict(knots, o["gap"]))

        observed = [o["rank"] for o in test]
        fitted_rho = _spearman([o["fitted"] for o in test], observed)
        current_rho = _spearman([o["current"] for o in test], observed)
        fitted_err = _median([abs(math.log2(o["fitted"]) - math.log2(o["rank"])) for o in test])
        current_err = _median([abs(math.log2(o["current"]) - math.log2(o["rank"])) for o in test])
        strength_rho = _spearman([-o["strength"] for o in test], observed)
        fallback_rho = _spearman([o["fallback"] for o in test], observed)
        fallback_err = _median([abs(math.log2(o["fallback"]) - math.log2(o["rank"])) for o in test])

        terms = {o["term"] for o in obs}
        self.stdout.write(self.style.MIGRATE_HEADING("\n== Rank calibration =="))
        self.stdout.write(f"  searches={len(searches)} terms={len(terms)} observations={len(obs)} "
                          f"train={len(train)} held-out={len(test)}")
        self.stdout.write(f"  strength alone vs rank, held-out Spearman: {strength_rho:.3f}")
        self.stdout.write(f"  current model: Spearman {current_rho:.3f}, median |log2 error| {current_err:.3f}")
        self.stdout.write(f"  fallback:      Spearman {fallback_rho:.3f}, median |log2 error| {fallback_err:.3f}"
                          "  (hand curve, full strength; information only)")
        self.stdout.write(f"  fitted model:  Spearman {fitted_rho:.3f}, median |log2 error| {fitted_err:.3f}")
        r1 = fitted_rho >= R1_MIN_SPEARMAN and fitted_rho > current_rho
        r2 = fitted_err < current_err
        self.stdout.write(f"  R1 (Spearman >= {R1_MIN_SPEARMAN} and above current): {'PASS' if r1 else 'FAIL'}")
        self.stdout.write(f"  R2 (median log2 error below current): {'PASS' if r2 else 'FAIL'}")
        self.stdout.write(self.style.MIGRATE_HEADING("\n== Median-rank knots (gap, rank), not shipped =="))
        for gap, log_rank, count in knots:
            self.stdout.write(f"    ({gap:.1f}, {math.exp(log_rank):.2f}),  # n={count}")
        self._judge_expected(train, test)
        self._typical(obs)

    def _typical(self, obs):
        """Where the middle app lands at a gap, and how many reach the top
        10, over every observation. Monotone like the rank itself: the middle
        rank never improves and the top-10 share never grows as the gap
        widens."""
        bins = {}
        for o in obs:
            bins.setdefault(math.floor(o["gap"] / GAP_BIN), []).append(o)
        rows = []
        for key in sorted(bins):
            members = bins[key]
            if len(members) < MIN_BIN:
                continue
            ranks = [m["rank"] for m in members]
            rows.append([sum(m["gap"] for m in members) / len(members), _median(ranks),
                         sum(1 for r in ranks if r <= 10) / len(ranks), len(members)])
        for i in range(1, len(rows)):
            rows[i][1] = max(rows[i][1], rows[i - 1][1])
            rows[i][2] = min(rows[i][2], rows[i - 1][2])
        self.stdout.write(self.style.MIGRATE_HEADING(
            "\n== Typical rank and top-10 share (gap, middle rank, share in the top 10) =="))
        self.stdout.write("TYPICAL_BY_GAP = [")
        for gap, rank, share, count in rows:
            self.stdout.write(f"    ({gap:.1f}, {rank:.1f}, {share:.3f}),  # n={count}")
        self.stdout.write("]")

    def _judge_expected(self, train, test):
        """D3c: the expected tap-through model and its gates E1, E2."""
        from aso.services import DownloadEstimator

        ttr = DownloadEstimator.ttr_at
        knots = _fit_ttr_knots(train)
        for o in test:
            o["realized"] = ttr(o["rank"])
            o["expected"] = ttr(_predict_rank(knots, o["gap"]))
            o["current_ttr"] = ttr(o["current"])
        realized = [o["realized"] for o in test]
        rho = _spearman([o["expected"] for o in test], realized)
        fitted_cal = _calibration(test, "expected")
        current_cal = _calibration(test, "current_ttr")
        e1 = rho >= E1_MIN_SPEARMAN
        e2 = fitted_cal < E2_MAX_LOG2 and fitted_cal < current_cal
        self.stdout.write(self.style.MIGRATE_HEADING("\n== Expected tap-through (plan D3c) =="))
        self.stdout.write(f"  held-out Spearman(predicted, realized tap-through): {rho:.3f}")
        self.stdout.write(f"  held-out calibration, median |log2 ratio| over {E2_GROUPS} gap groups: "
                          f"fitted {fitted_cal:.3f}, current {current_cal:.3f}")
        self.stdout.write(f"  E1 (Spearman >= {E1_MIN_SPEARMAN}): {'PASS' if e1 else 'FAIL'}")
        self.stdout.write(f"  E2 (calibration below {E2_MAX_LOG2} and below current): {'PASS' if e2 else 'FAIL'}")
        self.stdout.write(self.style.MIGRATE_HEADING("\n== Effective-rank knots (gap, rank) =="))
        self.stdout.write("RANK_BY_GAP = [")
        for gap, rank, count in knots:
            self.stdout.write(f"    ({gap:.1f}, {rank:.2f}),  # n={count}")
        self.stdout.write("]")


def _fit_knots(train):
    """Median log rank per gap bin, made non-decreasing (pool adjacent
    violators, weighted by bin size)."""
    bins = {}
    for o in train:
        bins.setdefault(math.floor(o["gap"] / GAP_BIN), []).append(o)
    rows = []
    for key in sorted(bins):
        members = bins[key]
        if len(members) < MIN_BIN:
            continue
        centre = sum(m["gap"] for m in members) / len(members)
        rows.append([centre, _median([math.log(m["rank"]) for m in members]), len(members)])
    # Pool adjacent violators.
    blocks = [[r[0] * r[2], r[1] * r[2], r[2]] for r in rows]
    merged = []
    for block in blocks:
        merged.append(block)
        while len(merged) > 1 and merged[-2][1] / merged[-2][2] > merged[-1][1] / merged[-1][2]:
            last = merged.pop()
            merged[-1] = [merged[-1][0] + last[0], merged[-1][1] + last[1], merged[-1][2] + last[2]]
    return [(b[0] / b[2], b[1] / b[2], b[2]) for b in merged]


# The rank curve this study replaced, kept here as its baseline: one smooth
# curve of the shortfall s (difficulty/100 less the app's strength/100):
# rank = 1 + 25 s up to s = 0.5, then 13.5 exp(g (s - 0.5) + h (s - 0.5)^2),
# meeting at the same slope, #250 at 1. It was set by hand, never measured.
_LINEAR_SPAN = 25.0
_RANK_KNEE = 0.5
_DEEPEST_RANK = 250.0
_RANK_AT_KNEE = 1 + _LINEAR_SPAN * _RANK_KNEE
_RANK_GROWTH = _LINEAR_SPAN / _RANK_AT_KNEE
_RANK_ACCELERATION = (
    math.log(_DEEPEST_RANK / _RANK_AT_KNEE) - _RANK_GROWTH * (1 - _RANK_KNEE)
) / (1 - _RANK_KNEE) ** 2


def _previous_rank(shortfall: float) -> float:
    s = max(0.0, min(1.0, shortfall))
    if s <= _RANK_KNEE:
        return 1 + _LINEAR_SPAN * s
    x = s - _RANK_KNEE
    return _RANK_AT_KNEE * math.exp(_RANK_GROWTH * x + _RANK_ACCELERATION * x * x)


def _rank_for_ttr(target):
    """The real-valued rank whose tap-through is ``target`` (ttr_at falls
    strictly with the rank, so bisection finds it)."""
    from aso.services import DownloadEstimator

    ttr = DownloadEstimator.ttr_at
    if target >= ttr(1):
        return 1.0
    lo, hi = 1.0, 100_000.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if ttr(mid) > target:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def _fit_ttr_knots(train):
    """Mean realized tap-through per gap bin, made non-increasing (pool
    adjacent violators, weighted by bin size), as (gap, effective rank, n)."""
    from aso.services import DownloadEstimator

    ttr = DownloadEstimator.ttr_at
    bins = {}
    for o in train:
        bins.setdefault(math.floor(o["gap"] / GAP_BIN), []).append(o)
    blocks = []
    for key in sorted(bins):
        members = bins[key]
        if len(members) < MIN_BIN:
            continue
        n = len(members)
        blocks.append([sum(m["gap"] for m in members), sum(ttr(m["rank"]) for m in members), n])
    merged = []
    for block in blocks:
        merged.append(block)
        while len(merged) > 1 and merged[-2][1] / merged[-2][2] < merged[-1][1] / merged[-1][2]:
            last = merged.pop()
            merged[-1] = [merged[-1][0] + last[0], merged[-1][1] + last[1], merged[-1][2] + last[2]]
    return [(b[0] / b[2], _rank_for_ttr(b[1] / b[2]), b[2]) for b in merged]


DEEPEST_RANK = 250.0


def _predict_rank(knots, gap):
    """Log-linear interpolation of the effective rank; flat below the first
    point, and past the last one the last segment's slope continues up to
    DEEPEST_RANK (plan, D3d), exactly as aso.scoring.rank_from_gap does."""
    (g0, r0, _), (g1, r1, _) = knots[-2], knots[-1]
    if gap > g1:
        slope = (math.log(r1) - math.log(r0)) / (g1 - g0)
        return min(DEEPEST_RANK, r1 * math.exp(slope * (gap - g1)))
    return _predict([(g, math.log(r), n) for g, r, n in knots], gap)


def _calibration(rows, key):
    """Median over gap groups of |log2(mean predicted / mean realized)|."""
    ordered = sorted(rows, key=lambda o: o["gap"])
    size = len(ordered) / E2_GROUPS
    errors = []
    for i in range(E2_GROUPS):
        group = ordered[int(i * size):int((i + 1) * size)]
        predicted = sum(o[key] for o in group) / len(group)
        realized = sum(o["realized"] for o in group) / len(group)
        errors.append(abs(math.log2(predicted / realized)))
    return _median(errors)


def _predict(knots, gap):
    """Log-linear interpolation between knots, flat beyond the ends."""
    if gap <= knots[0][0]:
        return math.exp(knots[0][1])
    if gap >= knots[-1][0]:
        return math.exp(knots[-1][1])
    for (g0, r0, _), (g1, r1, _) in zip(knots, knots[1:]):
        if g0 <= gap <= g1:
            t = (gap - g0) / (g1 - g0) if g1 > g0 else 0.0
            return math.exp(r0 + t * (r1 - r0))
    return math.exp(knots[-1][1])


def _median(values):
    values = sorted(values)
    n = len(values)
    if not n:
        return 0.0
    return values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2


def _ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def _spearman(a, b):
    ra, rb = _ranks(a), _ranks(b)
    n = len(ra)
    mean_a, mean_b = sum(ra) / n, sum(rb) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(ra, rb))
    var_a = math.sqrt(sum((x - mean_a) ** 2 for x in ra))
    var_b = math.sqrt(sum((y - mean_b) ** 2 for y in rb))
    return cov / (var_a * var_b) if var_a and var_b else 0.0
