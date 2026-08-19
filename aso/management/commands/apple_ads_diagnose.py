"""Live diagnostics for the Apple Ads Platform API v1 integration.

Phase 0 spike tool and permanent support tooling. Everything is read-only
against Apple and never writes to the local database - safe to run any
time. Typical flows:

    # One-time setup
    manage.py apple_ads_diagnose --generate-keys
    manage.py apple_ads_diagnose --client-id ... --team-id ... --key-id ... --save

    # Diagnostics (uses saved credentials)
    manage.py apple_ads_diagnose
    manage.py apple_ads_diagnose --full-week --country us
    manage.py apple_ads_diagnose --probe-storefronts
    manage.py apple_ads_diagnose --impression-share
"""

import datetime as dt
import math
import time

from django.core.management.base import BaseCommand, CommandError

from aso.apple_ads import api, keys, storage

STOREFRONTS = [
    "us", "gb", "ca", "au", "de", "fr", "jp", "kr", "cn", "br",
    "in", "mx", "es", "it", "nl", "se", "no", "dk", "fi", "pt",
    "ru", "tr", "sa", "ae", "sg", "th", "id", "ph", "vn", "tw",
]

APPLE_UI_STEPS = """\
Next steps (one time, about 5 minutes):
  1. Sign in at https://ads.apple.com with the account that holds the
     Admin role.
  2. Open Account Settings > API.
  3. Paste the PUBLIC key printed above (including the BEGIN/END lines)
     and save.
  4. Apple shows three values: Client ID, Team ID, Key ID. Save them here:

     manage.py apple_ads_diagnose --client-id SEARCHADS.xxx \\
         --team-id SEARCHADS.yyy --key-id zzz --save
"""


class Command(BaseCommand):
    help = "Diagnose the Apple Ads Platform API v1 connection (read-only)."

    def add_arguments(self, parser):
        parser.add_argument("--generate-keys", action="store_true",
                            help="Generate the local EC P-256 key pair and "
                                 "print the public key to upload at Apple.")
        parser.add_argument("--force-new-keys", action="store_true",
                            help="Allow --generate-keys to REPLACE an "
                                 "existing key (invalidates the uploaded one).")
        parser.add_argument("--show-public-key", action="store_true")
        parser.add_argument("--client-id", default="")
        parser.add_argument("--team-id", default="")
        parser.add_argument("--key-id", default="")
        parser.add_argument("--save", action="store_true",
                            help="Persist the given credential ids.")
        parser.add_argument("--key-file", default="",
                            help="Read the private key from this PEM file "
                                 "instead of DATA_DIR (lets diagnostics run "
                                 "against another data dir read-only).")
        parser.add_argument("--ad-account-id", default="",
                            help="Override the ad account (default: single "
                                 "ACL entry, or the saved one).")
        parser.add_argument("--country", default="us")
        parser.add_argument("--page-size", type=int, default=50)
        parser.add_argument("--full-week", action="store_true",
                            help="Download one complete country-week and "
                                 "report size, distribution, match rate, "
                                 "and EST correlation (not persisted).")
        parser.add_argument("--probe-storefronts", action="store_true",
                            help="Probe all supported storefronts for "
                                 "dataset availability.")
        parser.add_argument("--impression-share", action="store_true",
                            help="Probe impression-share data for every "
                                 "tracked app.")

    # ------------------------------------------------------------------ #

    def handle(self, *args, **options):
        if options["generate_keys"]:
            self._generate_keys(force=options["force_new_keys"])
            return
        if options["show_public_key"]:
            self._require_key()
            self.stdout.write(keys.public_key_pem())
            return
        if options["save"]:
            self._save_credentials(options)
            return

        credentials = self._credentials(options)
        self._banner("Token")
        started = time.monotonic()
        token, expires_at = api.fetch_access_token(credentials)
        self._ok(f"Access token obtained in {time.monotonic() - started:.2f}s "
                 f"(expires in {int(expires_at - time.time())}s).")

        self._banner("Identity")
        me = api.get_me(credentials)
        self._ok(f"userId={me['user_id']} orgId={me['org_id']}")

        self._banner("Ad accounts (/acls)")
        acls = api.list_acls(credentials)
        if not acls:
            raise CommandError(
                "No ad accounts visible to these credentials - check the "
                "API user's role in the Apple Ads UI."
            )
        for entry in acls:
            self.stdout.write(
                f"  - id={entry['ad_account_id']} "
                f"name={entry['ad_account_name']!r} "
                f"org={entry['org_id']} roles={entry['roles']}"
            )
        ad_account_id = self._pick_ad_account(options, acls)
        self._ok(f"Using adAccountId={ad_account_id}")
        self._rate_headers()

        week = api.latest_available_week()
        country = options["country"].lower()
        self._banner(
            f"Search term popularity - {country.upper()}, week of {week}"
        )
        rows, total_count = api.query_search_term_popularity(
            credentials, ad_account_id,
            country=country, week_start=week,
            page_size=options["page_size"],
        )
        self._ok(f"{len(rows)} rows returned (totalCount={total_count}).")
        pages = math.ceil(total_count / api.PAGE_SIZE) if total_count > 0 else "?"
        self.stdout.write(f"  Full week would need ~{pages} page request(s) "
                          f"at pageSize={api.PAGE_SIZE}.")
        for row in rows[:5]:
            self.stdout.write(f"  sample: {row}")
        genres = sorted({str(r.get("genre")) for r in rows if r.get("genre")})
        self.stdout.write(f"  genre values seen on this page: {genres[:10]}")
        self._rate_headers()

        if options["probe_storefronts"]:
            self._probe_storefronts(credentials, ad_account_id, week)
        if options["full_week"]:
            self._full_week(credentials, ad_account_id, country, week)
        if options["impression_share"]:
            self._impression_share(credentials, ad_account_id)

        self._banner("Done")
        self._ok("Diagnostics completed.")

    # ------------------------------------------------------------------ #

    def _generate_keys(self, force: bool):
        if keys.has_private_key() and not force:
            raise CommandError(
                "A private key already exists. Re-run with --force-new-keys "
                "to replace it (this invalidates the key uploaded to Apple)."
            )
        public_pem = keys.generate_key_pair()
        self._ok("Key pair generated. PUBLIC key (paste this at Apple):\n")
        self.stdout.write(public_pem)
        self.stdout.write(APPLE_UI_STEPS)

    def _save_credentials(self, options):
        for name in ("client_id", "team_id", "key_id"):
            if not options[name]:
                raise CommandError(f"--save requires --{name.replace('_', '-')}.")
        storage.save_apple_settings(apple_ads={
            "client_id": options["client_id"].strip(),
            "team_id": options["team_id"].strip(),
            "key_id": options["key_id"].strip(),
        })
        self._ok("Credentials saved. Run the command without arguments "
                 "to test the connection.")

    def _require_key(self):
        if not keys.has_private_key():
            raise CommandError(
                "No private key yet - run with --generate-keys first."
            )

    def _credentials(self, options) -> dict:
        if options["key_file"]:
            private_key_pem = open(options["key_file"], encoding="ascii").read()
        else:
            self._require_key()
            private_key_pem = keys.load_private_key_pem()
        block = storage.load_apple_settings()["apple_ads"]
        credentials = {
            "client_id": options["client_id"] or block.get("client_id", ""),
            "team_id": options["team_id"] or block.get("team_id", ""),
            "key_id": options["key_id"] or block.get("key_id", ""),
            "private_key_pem": private_key_pem,
        }
        missing = [k for k in ("client_id", "team_id", "key_id")
                   if not credentials[k]]
        if missing:
            raise CommandError(
                "Missing credentials: " + ", ".join(missing) + ". Pass them "
                "as options or persist them with --save."
            )
        return credentials

    def _pick_ad_account(self, options, acls) -> str:
        if options["ad_account_id"]:
            return options["ad_account_id"]
        saved = storage.load_apple_settings()["apple_ads"].get("ad_account_id", "")
        if saved:
            return str(saved)
        if len(acls) == 1:
            return str(acls[0]["ad_account_id"])
        raise CommandError(
            "Multiple ad accounts - pick one with --ad-account-id."
        )

    # ------------------------------------------------------------------ #

    def _probe_storefronts(self, credentials, ad_account_id, week):
        self._banner("Storefront availability probe")
        available, empty, errors = [], [], []
        for country in STOREFRONTS:
            try:
                rows, total = api.query_search_term_popularity(
                    credentials, ad_account_id,
                    country=country, week_start=week, page_size=1,
                )
            except api.AppleAdsError as e:
                errors.append((country, str(e)))
                self.stdout.write(f"  {country}: ERROR {e}")
                continue
            if rows:
                available.append(country)
                self.stdout.write(f"  {country}: available "
                                  f"(totalCount={total})")
            else:
                empty.append(country)
                self.stdout.write(f"  {country}: NO DATA")
            time.sleep(0.5)
        self._ok(f"available={len(available)} empty={empty} "
                 f"errors={[c for c, _ in errors]}")
        self._rate_headers()

    def _full_week(self, credentials, ad_account_id, country, week):
        self._banner(f"Full week download - {country.upper()}, week of {week}")
        started = time.monotonic()
        all_rows, requests_made = [], 0
        pager = api.iter_search_term_popularity(
            credentials, ad_account_id, country=country, week_start=week,
            between_pages=lambda: time.sleep(1.0),
        )
        total_count = -1
        for rows, total_count, page_index in pager:
            requests_made += 1
            all_rows.extend(rows)
            self.stdout.write(f"  page {page_index}: {len(rows)} rows")
        elapsed = time.monotonic() - started
        terms = {}
        for row in all_rows:
            term = str(row.get("searchTerm") or "").lower().strip()
            value = row.get("searchPopularity1to100")
            if term and isinstance(value, (int, float)):
                terms[term] = max(terms.get(term, 0), int(value))
        genres = {str(r.get("genre")) for r in all_rows if r.get("genre")}
        self._ok(
            f"{len(all_rows)} rows, {len(terms)} unique terms, "
            f"{len(genres)} genres, {requests_made} requests, "
            f"{elapsed:.1f}s (totalCount={total_count})."
        )
        est_bytes = len(all_rows) * 120
        self.stdout.write(f"  Estimated storage if persisted: "
                          f"~{est_bytes / 1_000_000:.1f} MB/week.")
        self._distribution(list(terms.values()))
        self._match_rate(country, terms)
        self._rate_headers()

    def _distribution(self, values):
        if not values:
            return
        values = sorted(values)

        def pct(p):
            return values[min(len(values) - 1, int(p / 100 * len(values)))]

        self.stdout.write(
            "  searchPopularity1to100 distribution (unique terms): "
            f"min={values[0]} p1={pct(1)} p5={pct(5)} p10={pct(10)} "
            f"p25={pct(25)} p50={pct(50)} p75={pct(75)} p90={pct(90)} "
            f"p99={pct(99)} max={values[-1]}"
        )
        self.stdout.write(
            f"  Suggested band ceiling B = min(25, p10) = {min(25, pct(10))}"
        )

    def _match_rate(self, country, apple_terms):
        from aso.models import SearchResult

        latest_est = {}
        rows = (
            SearchResult.objects.filter(country=country)
            .order_by("keyword_id", "-searched_at")
            .values_list("keyword__keyword", "popularity_score", "searched_at")
        )
        for text, est, _at in rows:
            term = (text or "").lower().strip()
            if term and term not in latest_est:
                latest_est[term] = est
        if not latest_est:
            self.stdout.write(
                f"  No tracked keywords for {country} - match rate skipped."
            )
            return
        matched = [t for t in latest_est if t in apple_terms]
        self._ok(
            f"Tracked-keyword match rate for {country}: "
            f"{len(matched)}/{len(latest_est)} "
            f"({100 * len(matched) / len(latest_est):.0f}%)."
        )
        pairs = [
            (latest_est[t], apple_terms[t])
            for t in matched
            if isinstance(latest_est[t], int)
        ]
        if len(pairs) >= 3:
            self._correlation(pairs)
        for term in matched[:10]:
            self.stdout.write(
                f"    {term!r}: EST={latest_est[term]} "
                f"ASA={apple_terms[term]}"
            )

    def _correlation(self, pairs):
        n = len(pairs)
        est_values = [p[0] for p in pairs]
        asa_values = [p[1] for p in pairs]
        mean_est = sum(est_values) / n
        mean_asa = sum(asa_values) / n
        cov = sum((e - mean_est) * (a - mean_asa)
                  for e, a in pairs)
        var_est = sum((e - mean_est) ** 2 for e in est_values)
        var_asa = sum((a - mean_asa) ** 2 for a in asa_values)
        if var_est and var_asa:
            pearson = cov / math.sqrt(var_est * var_asa)
            self.stdout.write(
                f"  EST vs ASA on {n} covered terms: pearson={pearson:.2f} "
                f"mean bias={mean_est - mean_asa:+.1f} "
                f"(EST mean {mean_est:.1f} vs ASA mean {mean_asa:.1f})"
            )

    def _impression_share(self, credentials, ad_account_id):
        from aso.models import App

        self._banner("Impression share probe")
        week = api.latest_available_week()
        apps = list(App.objects.exclude(track_id__isnull=True))
        if not apps:
            self.stdout.write("  No tracked apps with a track_id - skipped.")
            return
        for app in apps:
            try:
                rows, total = api.query_impression_share(
                    credentials, ad_account_id,
                    promoted_object_id=str(app.track_id),
                    week_start=week, page_size=25,
                )
            except api.AppleAdsError as e:
                self.stdout.write(f"  {app.name} ({app.track_id}): {e}")
                continue
            self._ok(f"{app.name} ({app.track_id}): {len(rows)} rows "
                     f"(totalCount={total}).")
            for row in rows[:5]:
                self.stdout.write(f"    sample: {row}")
            time.sleep(0.5)
        self._rate_headers()

    # ------------------------------------------------------------------ #

    def _rate_headers(self):
        headers = api.get_last_rate_headers()
        self.stdout.write(
            f"  [RateLimit] limit={headers['limit']} "
            f"remaining={headers['remaining']} reset={headers['reset']}s"
        )

    def _banner(self, title):
        self.stdout.write(self.style.MIGRATE_HEADING(f"\n== {title} =="))

    def _ok(self, message):
        self.stdout.write(self.style.SUCCESS(f"  {message}"))
