"""Validate (or refit) the popularity estimator against Apple's official data.

THE ONLY sanctioned way to evaluate or retune PopularityEstimator's
calibrated weights (scoring-principles rules): it measures the shipped
estimator against ground truth from the locally synced Apple top-terms
dataset, using the estimator's own `signal_components()` - so the
features used for fitting and the features used in production are the
same code by construction.

Read-only for the product (fits are printed, never applied). Requires a
synced Apple dataset (connect Apple Ads first) and makes live iTunes
competitor fetches for the sampled terms (~1 request/second).

    manage.py apple_estimator_study [--country us] [--per-bucket 40]
                                    [--negatives 90] [--fit]
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


class Command(BaseCommand):
    help = "Measure the popularity estimator against Apple's official values."

    def add_arguments(self, parser):
        parser.add_argument("--country", default="us")
        parser.add_argument("--per-bucket", type=int, default=40)
        parser.add_argument("--negatives", type=int, default=90)
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--fit", action="store_true",
                            help="Also fit and print candidate weights "
                                 "(never applied automatically).")
        parser.add_argument("--cache", default="",
                            help="JSON file of the measured terms and their raw "
                                 "competitors; reused if present, so a refit never refetches.")
        parser.add_argument("--judge", default="",
                            help="country=cache.json pairs, comma separated: judge the "
                                 "second study's candidates (plan D7d) on those cached "
                                 "samples together, without fetching anything.")

    def handle(self, *args, **options):
        import django.apps  # noqa: F401 (ensure app registry ready)

        if options["judge"]:
            pairs = [item.split("=", 1) for item in options["judge"].split(",") if item.strip()]
            judge_second_study({c.strip().lower(): path.strip() for c, path in pairs},
                               options["seed"], self.stdout.write)
            return

        from aso.apple_ads import storage
        from aso.models import AppleTopTerm
        from aso.services import (
            ITunesRateLimited,
            ITunesSearchService,
            PopularityEstimator,
            SearchAPIUnavailableError,
        )

        random.seed(options["seed"])
        country = options["country"].lower()
        active = storage.load_apple_settings()["apple_ads"][
            "active_weeks"
        ].get(country)
        if not active:
            raise CommandError(
                f"No synced Apple dataset for '{country}' - connect Apple "
                "Ads and let the weekly sync run first."
            )
        week = dt.date.fromisoformat(active)

        rows = list(
            AppleTopTerm.objects.filter(country=country, week=week)
            .values("term", "popularity")
        )
        best = {}
        for row in rows:
            if (row["term"] not in best
                    or row["popularity"] > best[row["term"]]["popularity"]):
                best[row["term"]] = row
        dataset_terms = set(best)
        sample = []
        for lo, hi in BUCKETS:
            bucket = [r for r in best.values() if lo <= r["popularity"] < hi]
            random.shuffle(bucket)
            sample.extend(bucket[:options["per_bucket"]])

        negatives = []
        short_terms = [t for t in dataset_terms if len(t.split()) <= 2]
        random.shuffle(short_terms)
        for term in short_terms:
            for suffix in NEGATIVE_SUFFIXES:
                candidate = f"{term} {suffix}"
                if candidate not in dataset_terms:
                    negatives.append(candidate)
                    break
            if len(negatives) >= options["negatives"]:
                break

        self.stdout.write(
            f"Sampling {len(sample)} head terms + {len(negatives)} "
            f"below-top-terms negatives ({country}, week of {week})..."
        )

        estimator = PopularityEstimator()
        cache = options.get("cache")
        if cache and os.path.exists(cache):
            raw = json.load(open(cache))
            self.stdout.write(f"Using {len(raw)} cached terms from {cache}")
        else:
            itunes = ITunesSearchService()
            # A partial cache from an interrupted run is resumed, not refetched.
            partial = f"{cache}.partial" if cache else ""
            raw = json.load(open(partial)) if partial and os.path.exists(partial) else []
            done = {item["term"] for item in raw}
            work = ([(r["term"], r["popularity"]) for r in sample]
                    + [(t, None) for t in negatives])
            random.shuffle(work)
            for index, (term, label) in enumerate(work):
                if term in done:
                    continue
                competitors = None
                for attempt in range(4):
                    try:
                        competitors = itunes.search_apps(term, country=country, limit=25)
                        break
                    except (SearchAPIUnavailableError, ITunesRateLimited):
                        # Apple throttles bursts; back off and try again.
                        time.sleep(30 * (attempt + 1))
                if not competitors:
                    continue
                raw.append({"term": term, "apple": label, "competitors": competitors})
                if index and index % 25 == 0:
                    self.stdout.write(f"  ...{index}/{len(work)}")
                    if partial:
                        json.dump(raw, open(partial, "w"))
                time.sleep(1.6)
            if cache:
                json.dump(raw, open(cache, "w"))
                if os.path.exists(partial):
                    os.remove(partial)
        measured = []
        for item in raw:
            components = estimator.signal_components(item["competitors"], item["term"])
            measured.append({
                "term": item["term"], "apple": item["apple"],
                "competitors": item["competitors"],
                "est": estimator.estimate(item["competitors"], item["term"]),
                **components,
            })

        head = [m for m in measured if m["apple"] is not None]
        tail = [m for m in measured if m["apple"] is None]
        if len(head) < 30:
            raise CommandError("Too few head samples measured - aborting.")

        est_values = [m["est"] for m in head]
        apple_values = [m["apple"] for m in head]
        tail_est = [m["est"] for m in tail]
        self.stdout.write(self.style.MIGRATE_HEADING("\n== Shipped estimator vs Apple =="))
        self.stdout.write(
            f"  head n={len(head)}: pearson={_pearson(est_values, apple_values):.3f} "
            f"spearman={_spearman(est_values, apple_values):.3f} "
            f"mae={sum(abs(e - a) for e, a in zip(est_values, apple_values)) / len(head):.2f}"
        )
        if tail_est:
            self.stdout.write(
                f"  tail n={len(tail_est)}: mean={sum(tail_est) / len(tail_est):.1f} "
                f">40: {sum(v > 40 for v in tail_est)} "
                f">60: {sum(v > 60 for v in tail_est)}"
            )
        self.stdout.write(
            f"  head-vs-tail AUC: {_auc(est_values, tail_est):.3f}"
        )

        if options["fit"]:
            judge_candidates(measured, estimator, options["seed"], self.stdout.write)
            feature_names = [k for k in estimator.V2_WEIGHTS if k != "intercept"]
            X = [[1.0] + [m[f] for f in feature_names] for m in head]
            weights = _ridge_fit(X, apple_values)
            self.stdout.write(self.style.MIGRATE_HEADING("\n== Candidate refit (NOT applied) =="))
            self.stdout.write(json.dumps(
                {"intercept": round(weights[0], 4), **{
                    f: round(w, 4) for f, w in zip(feature_names, weights[1:])
                }}, indent=1))
            self.stdout.write(
                "  To ship a refit: update PopularityEstimator.V2_WEIGHTS, "
                "bump aso.popularity.ESTIMATOR_VERSION, rerun this command "
                "as validation, and update the methodology pages."
            )


HELD_OUT_SHARE = 0.30
SHIPPED_TOLERANCE = 0.02
MAX_FLIP = 6.0
MAX_SWAP = 6.0
CANDIDATES = {
    # D7 of STRENGTH_AND_RANK_CALIBRATION_PLAN.md: x_top1_exact (the ratings
    # of the strongest top-five exact match) is replaced by the smooth share.
    "A": ["f_result", "f_leader", "f_title", "f_depth", "f_spec", "f_exact",
          "x_exact_share", "x_leader_mag"],
    "B": ["f_result", "f_leader", "f_title", "f_depth", "f_spec", "f_exact",
          "x_exact_share", "x_leader_mag", "x_exact_brand"],
    # Registered 2026-09-22, before the estimator data was fetched: the same
    # two with the leader found through a smooth gate instead of a cut at the
    # middle of the results, where one app moving one place removed it.
    "C": ["f_result", "f_leader_smooth", "f_title", "f_depth", "f_spec", "f_exact",
          "x_exact_share", "x_leader_mag_smooth"],
    "D": ["f_result", "f_leader_smooth", "f_title", "f_depth", "f_spec", "f_exact",
          "x_exact_share", "x_leader_mag_smooth", "x_exact_brand_smooth"],
}

# Registered 2026-09-23 (plan, D7b), after A to D failed P2 and P3: keep the
# shipped fit and replace only what jumps. Each maps a shipped signal to its
# replacement; the replacements under "free" are fitted with the intercept,
# every other weight stays at its shipped value.
PARTIAL_CANDIDATES = {
    "E": {"replace": {"x_top1_exact": "x_exact_mag"}, "free": ["x_exact_mag"]},
    "F": {"replace": {"x_top1_exact": "x_exact_mag", "f_leader": "f_leader_smooth",
                      "x_leader_mag": "x_leader_mag_smooth"},
          "free": ["x_exact_mag"]},
}


def _partial_fit(shipped, spec, train):
    """The shipped weights with the replacements in place; only the intercept
    and the free signals fitted, on what the frozen weights leave over."""
    frozen = {spec["replace"].get(name, name): w for name, w in shipped.items()
              if name != "intercept" and spec["replace"].get(name, name) not in spec["free"]}
    residual = [m["apple"] - sum(w * m[name] for name, w in frozen.items()) for m in train]
    fitted = _ridge_fit([[1.0] + [m[f] for f in spec["free"]] for m in train], residual)
    return {"intercept": fitted[0], **frozen, **dict(zip(spec["free"], fitted[1:]))}


# The second study (plan D7d), registered before its data was read.
SECOND_STUDY_CANDIDATES = {
    "S": ["f_result", "f_leader", "f_title", "f_depth", "f_spec", "f_exact",
          "x_top1_exact", "x_leader_mag"],
    "G": ["f_result", "f_leader_smooth", "f_title", "f_depth", "f_spec", "f_exact",
          "x_exact_mag", "x_leader_mag_smooth"],
    "H": ["f_result", "f_leader_smooth", "f_title", "f_depth", "f_spec", "f_exact",
          "x_exact_mag", "x_leader_mag_smooth", "x_exact_share"],
}
CENSORED_FIT_ROUNDS = 50


def _week_floors(countries):
    """The lowest popularity Apple reported in each storefront's active week:
    an unreported term sits below it."""
    from aso.apple_ads import storage
    from aso.models import AppleTopTerm
    from django.db.models import Min

    weeks = storage.load_apple_settings()["apple_ads"]["active_weeks"]
    floors = {}
    for country in countries:
        week = dt.date.fromisoformat(weeks[country])
        floors[country] = AppleTopTerm.objects.filter(
            country=country, week=week,
        ).aggregate(low=Min("popularity"))["low"]
    return floors


def _censored_fit(features, heads, negatives, floors):
    """Ridge on the head terms, plus each negative that the fit puts above
    its storefront's floor, targeted at that floor; repeated until the set
    of such negatives stops changing (plan D7d)."""
    active = set()
    weights = None
    for _ in range(CENSORED_FIT_ROUNDS):
        rows = heads + [n for i, n in enumerate(negatives) if i in active]
        targets = [m["apple"] for m in heads] + [
            floors[n["country"]] for i, n in enumerate(negatives) if i in active
        ]
        fitted = _ridge_fit([[1.0] + [m[f] for f in features] for m in rows], targets)
        weights = {"intercept": fitted[0], **dict(zip(features, fitted[1:]))}
        above = {
            i for i, n in enumerate(negatives)
            if _estimate_with(weights, n) > floors[n["country"]]
        }
        if above == active:
            break
        active = above
    return weights


def judge_second_study(caches, seed, write):
    """Fit S, G and H on the training terms of every storefront together and
    judge them against the shipped estimator on the held-out terms, by the
    gates P1 to P4 (plan D7d)."""
    from aso.services import PopularityEstimator

    estimator = PopularityEstimator()
    floors = _week_floors(list(caches))
    measured = []
    for country, path in caches.items():
        for item in json.load(open(path)):
            components = estimator.signal_components(item["competitors"], item["term"])
            if components is None:
                continue
            measured.append({"country": country, "term": item["term"], "apple": item["apple"],
                             "competitors": item["competitors"], **components})

    def held_out(m):
        key = f"{seed}:{m['country']}:{m['term']}"
        return (zlib.crc32(key.encode()) % 1000) / 1000 < HELD_OUT_SHARE

    train = [m for m in measured if not held_out(m)]
    test = [m for m in measured if held_out(m)]
    train_heads = [m for m in train if m["apple"] is not None]
    train_negatives = [m for m in train if m["apple"] is None]
    test_heads = [m for m in test if m["apple"] is not None]
    test_negatives = [m for m in test if m["apple"] is None]

    models = {"shipped": dict(estimator.V2_WEIGHTS)}
    for name, features in SECOND_STUDY_CANDIDATES.items():
        models[name] = _censored_fit(features, train_heads, train_negatives, floors)

    write(f"\n== Second study (D7d): {len(caches)} storefronts, floors {floors} ==")
    write(f"  train {len(train_heads)} head terms and {len(train_negatives)} negatives; "
          f"held out {len(test_heads)} and {len(test_negatives)}")
    results = {}
    for name, weights in models.items():
        head_est = [_estimate_with(weights, m) for m in test_heads]
        tail_est = [_estimate_with(weights, m) for m in test_negatives]
        results[name] = {
            "spearman": _spearman(head_est, [m["apple"] for m in test_heads]),
            "auc": _auc(head_est, tail_est),
            "flip": _largest_flip(weights, test, estimator),
            "swap": _largest_swap(weights, test, estimator),
            "negatives_high": sum(
                _estimate_with(weights, m) > floors[m["country"]] for m in test_negatives
            ),
            "per_country": {
                c: _spearman([_estimate_with(weights, m) for m in test_heads if m["country"] == c],
                             [m["apple"] for m in test_heads if m["country"] == c])
                for c in caches
            },
            "weights": weights,
        }
    base = results["shipped"]
    for name, r in results.items():
        line = (f"  {name:8} spearman={r['spearman']:.3f} auc={r['auc']:.3f} "
                f"largest title flip={r['flip']} largest neighbour swap={r['swap']} "
                f"negatives above their floor={r['negatives_high']}/{len(test_negatives)}")
        if name != "shipped":
            p1 = r["spearman"] >= base["spearman"] - SHIPPED_TOLERANCE
            p2 = r["auc"] >= base["auc"] - SHIPPED_TOLERANCE
            p3 = r["flip"] <= MAX_FLIP
            p4 = r["swap"] <= MAX_SWAP
            r["passes"] = p1 and p2 and p3 and p4
            line += (f"  P1 {'PASS' if p1 else 'FAIL'}  P2 {'PASS' if p2 else 'FAIL'}"
                     f"  P3 {'PASS' if p3 else 'FAIL'}  P4 {'PASS' if p4 else 'FAIL'}")
        write(line)
        write("           per storefront: " + ", ".join(
            f"{c} {v:.3f}" for c, v in r["per_country"].items()))
    passing = [n for n in SECOND_STUDY_CANDIDATES if results[n].get("passes")]
    if passing:
        winner = max(passing, key=lambda n: results[n]["spearman"])
        write(f"\n  Ships: candidate {winner}")
        write(json.dumps({k: round(v, 4) for k, v in results[winner]["weights"].items()}, indent=1))
    else:
        write("\n  No candidate passes all four gates: the shipped estimator stays.")
    return results


def _estimate_with(weights, components):
    raw = weights["intercept"] + sum(
        w * components[name] for name, w in weights.items() if name != "intercept"
    )
    return int(round(max(1, min(100, raw))))


def _flip_title(competitor, keyword):
    """The same app with its title's match to the keyword switched."""
    from aso.services import _keyword_title_evidence

    flipped = dict(competitor)
    evidence = _keyword_title_evidence(
        keyword.lower(), competitor.get("trackName", ""), competitor.get("primaryGenreName", ""),
    )
    if evidence["exact_phrase"] or evidence["all_words"] or evidence["evidence"] > 0:
        flipped["trackName"] = "Qzx Utility"
    else:
        flipped["trackName"] = keyword.title()
    return flipped


def _largest_flip(weights, items, estimator):
    """The most any single app's title can move the estimate, over the
    given terms and each one's top ten apps."""
    worst = 0
    for item in items:
        base = _estimate_with(weights, estimator.signal_components(item["competitors"], item["term"]))
        for index in range(min(10, len(item["competitors"]))):
            competitors = list(item["competitors"])
            competitors[index] = _flip_title(competitors[index], item["term"])
            moved = _estimate_with(weights, estimator.signal_components(competitors, item["term"]))
            worst = max(worst, abs(moved - base))
    return worst


def _largest_swap(weights, items, estimator):
    """The most swapping two neighbouring apps can move the estimate, over
    the given terms and their whole result lists: one app moving one place."""
    worst = 0
    for item in items:
        base = _estimate_with(weights, estimator.signal_components(item["competitors"], item["term"]))
        for index in range(len(item["competitors"]) - 1):
            competitors = list(item["competitors"])
            competitors[index], competitors[index + 1] = competitors[index + 1], competitors[index]
            moved = _estimate_with(weights, estimator.signal_components(competitors, item["term"]))
            worst = max(worst, abs(moved - base))
    return worst


def judge_candidates(measured, estimator, seed, write):
    """Fit the candidates on the training terms and judge them against the
    shipped estimator on the held-out terms, by the pre-registered gates:

        P1  Spearman at least the shipped value minus 0.02
        P2  head-versus-tail AUC at least the shipped value minus 0.02
        P3  one app's title match flipped moves the estimate at most 6
        P4  two neighbouring apps swapped move the estimate at most 6
    """
    def held_out(term):
        return (zlib.crc32(f"{seed}:{term}".encode()) % 1000) / 1000 < HELD_OUT_SHARE

    head = [m for m in measured if m["apple"] is not None]
    tail = [m for m in measured if m["apple"] is None]
    train = [m for m in head if not held_out(m["term"])]
    test = [m for m in head if held_out(m["term"])]
    test_tail = [m for m in tail if held_out(m["term"])]
    apple = [m["apple"] for m in test]

    shipped = dict(estimator.V2_WEIGHTS)
    models = {"shipped": shipped}
    for name, features in CANDIDATES.items():
        X = [[1.0] + [m[f] for f in features] for m in train]
        fitted = _ridge_fit(X, [m["apple"] for m in train])
        models[name] = {"intercept": fitted[0], **dict(zip(features, fitted[1:]))}
    for name, spec in PARTIAL_CANDIDATES.items():
        models[name] = _partial_fit(shipped, spec, train)

    results = {}
    for name, weights in models.items():
        head_est = [_estimate_with(weights, m) for m in test]
        tail_est = [_estimate_with(weights, m) for m in test_tail]
        results[name] = {
            "spearman": _spearman(head_est, apple),
            "auc": _auc(head_est, tail_est),
            "flip": _largest_flip(weights, test + test_tail, estimator),
            "swap": _largest_swap(weights, test + test_tail, estimator),
            "weights": weights,
        }
    write(f"\n== Candidates on {len(test)} held-out head terms, {len(test_tail)} held-out negatives ==")
    base = results["shipped"]
    for name, r in results.items():
        negatives_high = sum(_estimate_with(r["weights"], m) > 40 for m in test_tail)
        line = (f"  {name:8} spearman={r['spearman']:.3f} auc={r['auc']:.3f} "
                f"largest title flip={r['flip']} largest neighbour swap={r['swap']} "
                f"negatives above 40={negatives_high}")
        if name != "shipped":
            p1 = r["spearman"] >= base["spearman"] - SHIPPED_TOLERANCE
            p2 = r["auc"] >= base["auc"] - SHIPPED_TOLERANCE
            p3 = r["flip"] <= MAX_FLIP
            p4 = r["swap"] <= MAX_SWAP
            r["passes"] = p1 and p2 and p3 and p4
            line += (f"  P1 {'PASS' if p1 else 'FAIL'}  P2 {'PASS' if p2 else 'FAIL'}"
                     f"  P3 {'PASS' if p3 else 'FAIL'}  P4 {'PASS' if p4 else 'FAIL'}")
        write(line)
    passing = [n for n in [*CANDIDATES, *PARTIAL_CANDIDATES] if results[n].get("passes")]
    if passing:
        winner = max(passing, key=lambda n: results[n]["spearman"])
        write(f"\n  Ships: candidate {winner}")
        write(json.dumps({k: round(v, 4) for k, v in results[winner]["weights"].items()}, indent=1))
    else:
        write("\n  No candidate passes all four gates: the shipped estimator stays.")
    return results


def _pearson(a, b):
    n = len(a)
    mean_a, mean_b = sum(a) / n, sum(b) / n
    cov = sum((x - mean_a) * (y - mean_b) for x, y in zip(a, b))
    var_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    var_b = math.sqrt(sum((y - mean_b) ** 2 for y in b))
    return cov / (var_a * var_b) if var_a and var_b else 0.0


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
    return _pearson(_ranks(a), _ranks(b))


def _auc(head_scores, tail_scores):
    if not head_scores or not tail_scores:
        return 0.0
    wins = ties = 0
    for h in head_scores:
        for t in tail_scores:
            if h > t:
                wins += 1
            elif h == t:
                ties += 1
    return (wins + ties / 2) / (len(head_scores) * len(tail_scores))


def _ridge_fit(X, y, lam=1.0):
    n = len(X[0])
    XtX = [[sum(r[i] * r[j] for r in X) for j in range(n)] for i in range(n)]
    for i in range(n):
        XtX[i][i] += lam
    Xty = [sum(r[i] * yi for r, yi in zip(X, y)) for i in range(n)]
    for col in range(n):
        pivot = max(range(col, n), key=lambda r: abs(XtX[r][col]))
        XtX[col], XtX[pivot] = XtX[pivot], XtX[col]
        Xty[col], Xty[pivot] = Xty[pivot], Xty[col]
        div = XtX[col][col] or 1e-9
        XtX[col] = [v / div for v in XtX[col]]
        Xty[col] /= div
        for row in range(n):
            if row != col and XtX[row][col]:
                factor = XtX[row][col]
                XtX[row] = [a - factor * b for a, b in zip(XtX[row], XtX[col])]
                Xty[row] -= factor * Xty[col]
    return Xty
