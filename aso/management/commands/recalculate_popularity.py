"""Recompute stored popularity estimates with the current estimator.

Every SearchResult stores its full competitor snapshot
(`competitors_data`), so historical rows can be re-scored with the
CURRENT estimator - after the estimate-v2 calibration this brings the
entire history, trends, and deltas onto one consistent scale (no
mixed-scale rows). Also infers and stores each row's Apple genre bucket
(the genre-aware fallback cap needs it) and recomputes classifications.

Runs automatically once per install when the estimator version changes
(aso.apps triggers it in a background thread); this command exists for
manual reruns and support.

Rows without competitor data are left untouched (the daily auto-refresh
re-scores tracked pairs naturally).
"""

from django.core.management.base import BaseCommand

from aso.popularity import ESTIMATOR_VERSION, recalculate_stored_popularity


class Command(BaseCommand):
    help = "Re-score stored history with the current popularity estimator."

    def handle(self, *args, **options):
        stats = recalculate_stored_popularity()
        self.stdout.write(self.style.SUCCESS(
            f"Estimator v{ESTIMATOR_VERSION}: rewrote {stats['rewritten']} of "
            f"{stats['total']} stored rows "
            f"({stats['skipped_no_competitors']} without competitor data "
            f"skipped), reclassified {stats['reclassified']} rows."
        ))
