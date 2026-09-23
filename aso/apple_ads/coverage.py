"""Which storefronts Apple Ads publishes search-term popularity for.

Apple Ads does not operate everywhere the App Store does, and Apple publishes
no list we can fetch. So we ask: one cheap page request per storefront against
the same insights endpoint the sync uses. A storefront that answers with rows
is covered; one that answers with nothing is not.

The answer is a dated fact about the owner's account, not a property of a
country, which is why it lives here and in APPLE_ADS_STOREFRONTS rather than
as a column in aso/countries.py.

Both `manage.py probe_storefronts --apple-ads` and
`manage.py apple_ads_diagnose --probe-storefronts` call `probe()`, so there is
one implementation of it.
"""

from __future__ import annotations

import time

from . import api, keys, storage


class CredentialsMissing(RuntimeError):
    """No usable Apple Ads credentials on this machine."""


PACE_SECONDS = 0.5


def credentials_or_raise() -> tuple[dict, str]:
    """Return (credentials, ad_account_id) from saved settings.

    Raises CredentialsMissing with a sentence the caller can print.
    """
    if not keys.has_private_key():
        raise CredentialsMissing(
            "no private key on this machine, run apple_ads_diagnose "
            "--generate-keys first"
        )
    block = storage.load_apple_settings()["apple_ads"]
    missing = [k for k in ("client_id", "team_id", "key_id") if not block.get(k)]
    if missing:
        raise CredentialsMissing(
            "missing saved credentials: " + ", ".join(missing)
        )
    ad_account_id = str(block.get("ad_account_id", ""))
    if not ad_account_id:
        raise CredentialsMissing("no ad account id saved")
    return (
        {
            "client_id": block["client_id"],
            "team_id": block["team_id"],
            "key_id": block["key_id"],
            "private_key_pem": keys.load_private_key_pem(),
        },
        ad_account_id,
    )


def probe(codes, *, credentials=None, ad_account_id="", week=None, log=None):
    """Ask Apple which of `codes` it reports popularity for.

    Returns (available, unavailable, errors), where errors is a list of
    (code, message). Never raises for a per-storefront failure; raises
    CredentialsMissing only when it cannot start at all.
    """
    if credentials is None:
        credentials, ad_account_id = credentials_or_raise()
    if week is None:
        week = api.latest_available_week()

    available: list[str] = []
    unavailable: list[str] = []
    errors: list[tuple[str, str]] = []

    for index, code in enumerate(codes):
        if index:
            time.sleep(PACE_SECONDS)
        try:
            rows, total = api.query_search_term_popularity(
                credentials, ad_account_id,
                country=code, week_start=week, page_size=1,
            )
        except api.AppleAdsAuthError as exc:
            # Rejected credentials fail identically for every storefront, so
            # asking 175 times would tell us nothing and cost Apple 175
            # requests. Say it once and stop.
            raise CredentialsMissing(
                f"Apple rejected the saved credentials ({exc}). Reconnect "
                "Apple Ads in Settings, then run this again."
            ) from exc
        except api.AppleAdsError as exc:
            errors.append((code, str(exc)[:80]))
            if log:
                log(f"  {code}: ERROR {str(exc)[:80]}")
            continue
        if rows:
            available.append(code)
            if log:
                log(f"  {code}: available (totalCount={total})")
        else:
            unavailable.append(code)
            if log:
                log(f"  {code}: no data")

    return available, unavailable, errors
