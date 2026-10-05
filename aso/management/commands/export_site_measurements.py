"""Measure the keywords respectaso.com's guide pages show, from public App Store results only.

    DATA_DIR=<new empty folder> RESPECTASO_DISABLE_SCHEDULER=1 .venv/bin/python manage.py migrate
    DATA_DIR=<same folder> RESPECTASO_DISABLE_SCHEDULER=1 \\
        .venv/bin/python manage.py export_site_measurements \\
        ../respectaso-web/guides/data/measurements/keywords.json \\
        ../respectaso-web/guides/data/measurements/measured-<YYYY-MM-DD>.json

The website may publish RespectASO's own numbers and never Apple's (the Apple
Ads Terms of Service count what Apple Ads returns as confidential). So this
reads nothing Apple Ads gave: it refuses to run on a data folder that holds
Apple Ads credentials, synced popularity or Top Search Terms, or on the
owner's real folders, and it computes each number straight from the day's
public App Store search, through the same code the app's Keywords page uses:

- difficulty: DifficultyCalculator, which reads only the search results;
- popularity: PopularityEstimator's own estimate, never the resolved value
  (resolve_popularity can return Apple's number or one capped by it);
- the top ten: how many carry the keyword in their name (the difficulty's own
  title test) and their median rating count.

Download estimates, Opportunity and Insight tags are left out: they read as
promises on a marketing page.

It ships in the free edition so respectaso-web's scheduled workflow
(.github/workflows/measure-keywords.yml) can run it from the public repository
with no access to this one; ``--only`` measures some pages, for a quick check.
Free-tier code: no aso_pro import. Plan: respectaso-web
docs/development/MEASURED_KEYWORD_DATA_PLAN.md.
"""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from aso import day_reads
from aso.apple_ads.storage import has_credentials
from aso.data_folders import is_real_data_dir
from aso.local_day import local_date
from aso.models import AppleSearchPopularity, AppleTopTerm
from aso.popularity import ESTIMATOR_VERSION
from aso.scoring import difficulty_label
from aso.services import (
    DifficultyCalculator,
    ITunesRateLimited,
    ITunesSearchService,
    PopularityEstimator,
    SearchAPIUnavailableError,
    _keyword_title_evidence,
)
from aso.throttle import AdaptiveITunesRateLimiter

# Every field a measured keyword may carry; respectaso-web's reader accepts
# exactly these and refuses anything else.
FIELDS = ("keyword", "bucket", "difficulty", "difficulty_label", "popularity_estimate",
          "top10_title_matches", "top10_median_ratings")
ATTEMPTS = 6


def measure(keyword: str, bucket: str, competitors: list[dict], *, popularity: bool) -> dict:
    """One keyword's published numbers from its search results."""
    if not competitors:
        row = {"keyword": keyword, "bucket": bucket, "difficulty": None, "difficulty_label": "No results",
               "top10_title_matches": 0, "top10_median_ratings": 0}
        if popularity:
            row["popularity_estimate"] = None
        return row
    score, _ = DifficultyCalculator().calculate(competitors, keyword=keyword)
    top10 = competitors[:10]
    kw_lower = keyword.lower().strip()
    matches = 0
    for app in top10:
        evidence = _keyword_title_evidence(kw_lower, app.get("trackName", ""), app.get("primaryGenreName", ""))
        if evidence["exact_phrase"] or evidence["all_words"]:
            matches += 1
    row = {
        "keyword": keyword,
        "bucket": bucket,
        "difficulty": score,
        "difficulty_label": difficulty_label(score),
        "top10_title_matches": matches,
        "top10_median_ratings": int(statistics.median(app.get("userRatingCount", 0) or 0 for app in top10)),
    }
    if popularity:
        row["popularity_estimate"] = PopularityEstimator().estimate(competitors, keyword)
    return row


def refuse_unless_clean() -> None:
    """Stop unless this data folder has never held anything from Apple Ads."""
    data_dir = Path(settings.DATA_DIR).resolve()
    if is_real_data_dir(data_dir):
        raise CommandError(f"{data_dir} is a real data folder. Point DATA_DIR at a new, empty folder.")
    if has_credentials():
        raise CommandError("This data folder holds Apple Ads credentials. Use a new, empty folder.")
    if AppleSearchPopularity.objects.exists() or AppleTopTerm.objects.exists():
        raise CommandError("This data folder holds Apple Ads data. Use a new, empty folder.")


class Command(BaseCommand):
    help = "Measure respectaso.com's guide page keywords from public App Store results (never Apple Ads data)"

    def add_arguments(self, parser):
        parser.add_argument("keywords", help="respectaso-web guides/data/measurements/keywords.json")
        parser.add_argument("output", help="where to write the measurements")
        parser.add_argument("--only", nargs="+", default=None, help="measure only these pages, e.g. country/japan")

    def handle(self, *args, **options):
        refuse_unless_clean()
        pages = json.loads(Path(options["keywords"]).read_text(encoding="utf-8"))["pages"]
        if options["only"]:
            unknown = set(options["only"]) - set(pages)
            if unknown:
                raise CommandError(f"no such pages: {', '.join(sorted(unknown))}")
            pages = {page: spec for page, spec in pages.items() if page in options["only"]}
        service = ITunesSearchService()
        limiter = AdaptiveITunesRateLimiter()
        pacer = day_reads.Pacer(limiter.wait)
        out = {
            "measured_on": local_date(timezone.now()).isoformat(),
            "app_version": settings.VERSION,
            "estimator_version": ESTIMATOR_VERSION,
            "popularity_kind": "respectaso_estimate",
            "pages": {},
        }
        for page, spec in pages.items():
            storefront, popularity = spec["storefront"], spec["popularity"]
            rows = []
            for entry in spec["keywords"]:
                read = self.read(entry["keyword"], storefront, service, limiter, pacer)
                rows.append(measure(entry["keyword"], entry["bucket"], read.competitors, popularity=popularity))
            out["pages"][page] = {"storefront": storefront, "popularity": popularity, "keywords": rows}
            self.stdout.write(f"{page}: {len(rows)} keywords in {storefront}")
        Path(options["output"]).write_text(json.dumps(out, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        self.stdout.write(f"Wrote {options['output']}")

    def read(self, keyword, storefront, service, limiter, pacer):
        """Today's read of a keyword, at the app's pace; a throttled or failed
        search waits longer and tries again. A rerun the same day reuses every
        read already stored, so an interrupted run resumes where it stopped."""
        for attempt in range(ATTEMPTS):
            pacer.before(keyword, storefront)
            try:
                read = day_reads.get_or_fetch(keyword, storefront, itunes_service=service)
            except ITunesRateLimited as error:
                limiter.record_failure(retry_after=getattr(error, "retry_after", None))
            except SearchAPIUnavailableError:
                limiter.record_failure()
            else:
                limiter.record_success()
                return read
            self.stderr.write(f"{keyword!r} ({storefront}): attempt {attempt + 1} failed, waiting longer")
        raise CommandError(f"{keyword!r} ({storefront}) could not be read; run again to resume")
