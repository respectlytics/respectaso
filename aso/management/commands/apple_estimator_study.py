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
import random
import time

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

    def handle(self, *args, **options):
        import django.apps  # noqa: F401 (ensure app registry ready)

        from aso.apple_ads import storage
        from aso.models import AppleTopTerm
        from aso.services import (
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

        itunes = ITunesSearchService()
        estimator = PopularityEstimator()
        measured = []
        work = ([(r["term"], r["popularity"]) for r in sample]
                + [(t, None) for t in negatives])
        random.shuffle(work)
        for index, (term, label) in enumerate(work):
            try:
                competitors = itunes.search_apps(term, country=country, limit=25)
            except SearchAPIUnavailableError:
                time.sleep(20)
                continue
            if not competitors:
                continue
            components = estimator.signal_components(competitors, term)
            measured.append({
                "term": term, "apple": label,
                "est": estimator.estimate(competitors, term),
                **components,
            })
            if index and index % 50 == 0:
                self.stdout.write(f"  ...{index}/{len(work)}")
            time.sleep(1.1)

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
