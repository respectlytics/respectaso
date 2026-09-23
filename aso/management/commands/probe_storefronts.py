"""Ask Apple which storefronts really exist, instead of assuming.

Read-only. It searches a common term in every candidate storefront in
``aso.countries`` and reports which ones answer with apps, which answer with
nothing, and which error. The output is a paste-ready list of the codes that
work, so ``aso/countries.py`` records observed storefronts rather than a
guess.

    manage.py probe_storefronts
    manage.py probe_storefronts --codes bg,il,rs
    manage.py probe_storefronts --apple-ads

Apple throttles fast probes hard (403 and 429 after roughly 50 rapid
requests), so this paces itself with the app's own adaptive limiter. A full
run over ~190 storefronts therefore takes 10 to 15 minutes. That is expected;
it runs once per release that changes the list.

``--apple-ads`` additionally asks the Apple Ads Platform API whether it
publishes search-term popularity for each storefront, which is what fills
APPLE_ADS_STOREFRONTS. It needs the owner's Apple Ads credentials and shares
its implementation with `apple_ads_diagnose --probe-storefronts`.
"""

import datetime as dt

from django.core.management.base import BaseCommand

from aso import countries
from aso.services import (
    ITunesRateLimited,
    ITunesSearchService,
    SearchAPIUnavailableError,
)
from aso.throttle import AdaptiveITunesRateLimiter

# Two unrelated everyday terms. A storefront that answers neither is either
# not a storefront or too small to carry apps for common searches.
PROBE_TERMS = ("photo", "music")


class Command(BaseCommand):
    help = "Probe every candidate App Store storefront (read-only)."

    def add_arguments(self, parser):
        parser.add_argument(
            "--codes", default="",
            help="Comma-separated subset to probe instead of the whole table.",
        )
        parser.add_argument(
            "--apple-ads", action="store_true",
            help="Also probe Apple Ads popularity coverage per storefront.",
        )

    def handle(self, *args, **options):
        codes = self._codes(options["codes"])
        self.stdout.write(
            f"Probing {len(codes)} storefronts against the iTunes Search API. "
            f"Paced, so expect roughly {len(codes) * 4 // 60} minutes.\n"
        )

        itunes = ITunesSearchService()
        limiter = AdaptiveITunesRateLimiter()
        working, empty, errored = [], [], []

        for index, code in enumerate(codes):
            if index:
                limiter.wait()
            found, error = self._probe_one(itunes, limiter, code)
            if error:
                errored.append((code, error))
                mark, detail = "ERROR", error
            elif found:
                working.append(code)
                mark, detail = "ok", f"{found} apps"
            else:
                empty.append(code)
                mark, detail = "EMPTY", "no results for either term"
            self.stdout.write(
                f"  [{index + 1:>3}/{len(codes)}] {code}  "
                f"{countries.name(code):<26} {mark:<6} {detail}"
            )

        self._report(working, empty, errored)

        if options["apple_ads"]:
            self._probe_apple_ads(working)

    # ---------------------------------------------------------------- #

    def _codes(self, raw):
        if raw:
            picked = countries.clean(raw.split(","))
            if picked:
                return picked
        return sorted(countries.CODES)

    def _probe_one(self, itunes, limiter, code):
        """Return (result_count, error_message). Never raises."""
        last_error = ""
        for term_index, term in enumerate(PROBE_TERMS):
            if term_index:
                limiter.wait()
            try:
                results = itunes.search_apps(term, country=code, limit=5)
            except ITunesRateLimited as exc:
                limiter.record_failure(retry_after=getattr(exc, "retry_after", None))
                last_error = "rate limited"
                continue
            except SearchAPIUnavailableError as exc:
                limiter.record_failure()
                last_error = str(exc)[:60]
                continue
            except Exception as exc:  # a storefront Apple does not serve
                limiter.record_failure()
                last_error = f"{type(exc).__name__}: {str(exc)[:50]}"
                continue
            limiter.record_success()
            if results:
                return len(results), ""
        return 0, last_error

    def _report(self, working, empty, errored):
        self.stdout.write("\n" + "=" * 68)
        self.stdout.write(
            f"working {len(working)}   empty {len(empty)}   errored {len(errored)}"
        )
        if empty:
            self.stdout.write(f"\nEMPTY (no apps for either term): {', '.join(empty)}")
        if errored:
            self.stdout.write("\nERRORED:")
            for code, error in errored:
                self.stdout.write(f"  {code}: {error}")

        missing = sorted(set(countries.CODES) - set(working))
        if missing:
            self.stdout.write(
                "\nCandidates to REMOVE from aso/countries.py, with today's date "
                "and the reason, in the probe log at the bottom of that file:"
            )
            self.stdout.write("  " + ", ".join(missing))

        self.stdout.write(f"\nConfirmed storefronts ({len(working)}), paste-ready:")
        line = "    "
        for code in working:
            if len(line) > 70:
                self.stdout.write(line)
                line = "    "
            line += f'"{code}", '
        if line.strip():
            self.stdout.write(line)
        self.stdout.write(f"\nProbed {dt.date.today().isoformat()}.")

    def _probe_apple_ads(self, codes):
        """Which storefronts Apple Ads publishes search popularity for."""
        from aso.apple_ads import coverage

        self.stdout.write("\n" + "=" * 68)
        self.stdout.write("Apple Ads coverage probe\n")
        try:
            available, unavailable, errors = coverage.probe(
                codes, log=self.stdout.write
            )
        except coverage.CredentialsMissing as exc:
            self.stdout.write(f"  skipped: {exc}")
            return

        self.stdout.write(
            f"\nAPPLE_ADS_STOREFRONTS, probed {dt.date.today().isoformat()} "
            f"({len(available)} storefronts):"
        )
        line = "    "
        for code in available:
            if len(line) > 70:
                self.stdout.write(line)
                line = "    "
            line += f'"{code}", '
        if line.strip():
            self.stdout.write(line)
        if unavailable:
            self.stdout.write(f"\nNo Apple data: {', '.join(unavailable)}")
        if errors:
            self.stdout.write(f"\nErrors: {', '.join(c for c, _ in errors)}")
