"""Thin client for the official Apple Ads Platform API v1.

Auth (see keys.py for the key pair):

    ES256 JWT client secret (signed locally with the stored private key)
    -> POST https://appleid.apple.com/auth/oauth2/token
       grant_type=client_credentials, scope=searchadsorg
    -> 1-hour Bearer access token, cached in memory only.

Endpoints used (base https://api.ads.apple.com/v1):

    GET  /me                                        - userId, orgId
    GET  /acls                                      - ad accounts + roles
    POST /insights/apps/search-term-popularity/query - weekly top-terms dataset
    POST /insights/apps/impression-share/query       - per-app impression share

The popularity endpoint is a DATASET, not a lookup: it returns Apple's top
search terms per (country, genre) for a completed week; there is no
searchTerm filter. sync.py downloads it per country and all keyword
lookups happen against the local tables.

Rate-limit policy (constants below are the single tuning point):
  Layer 1 - proactive pacing: sync.py spaces page requests sequentially
            and honors the RateLimit-* headers captured here.
  Layer 2 - reactive backoff: this module honors Retry-After on 429
            (seconds or HTTP-date form) with jitter; otherwise jittered
            exponential backoff, capped per Apple's guidance.
  Layer 3 - adaptive slow-down: AppleAdsRateLimitedError signals the sync
            run to double its pacing delay and abort gracefully.
  Layer 4 - self-imposed ceilings: enforced by sync.py via the request
            log in storage.py.

This module is used ONLY from background threads and the bounded
first-time-country path - never from scoring views directly.
"""

import datetime as dt
import logging
import random
import threading
import time
from email.utils import parsedate_to_datetime

import jwt as pyjwt
import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.ads.apple.com/v1"
TOKEN_URL = "https://appleid.apple.com/auth/oauth2/token"
JWT_AUDIENCE = "https://appleid.apple.com"
OAUTH_SCOPE = "searchadsorg"

CLIENT_SECRET_TTL = 3600       # fresh secret per token request; cheap to sign
TOKEN_REFRESH_MARGIN = 120     # refresh the access token 2 minutes early
REQUEST_TIMEOUT = 30

MAX_ATTEMPTS = 4
TRANSIENT_BASE_DELAY = 1.0     # 1s -> 2s -> 4s
RATE_LIMIT_BASE_DELAY = 2.0    # 2s -> 4s -> 8s
MAX_RETRY_DELAY = 16.0         # Apple's documented backoff cap guidance
JITTER_FACTOR = 0.25

PAGE_SIZE = 5000               # API maximum
LOW_REMAINING_THRESHOLD = 5    # Layer 1 trigger for header-aware pacing

# Capped sleeper for request-path calls (settings verification): keeps
# retry waits short so the browser/webview fetch never times out.
FAST_SLEEPER = lambda seconds: time.sleep(min(seconds, 1.0))  # noqa: E731

# Weekly datasets are generated Mondays at 07:00 UTC for the preceding
# Sunday-Saturday week, retained rolling 65 weeks.
PUBLICATION_WEEKDAY = 0        # Monday
PUBLICATION_HOUR_UTC = 7
WEEKS_RETAINED_BY_APPLE = 65

POPULARITY_FIELDS = [
    "rankInGenre",
    "searchPopularityInGenre",
    "searchPopularity1to100",
    "searchPopularity1to5",
]


class AppleAdsError(Exception):
    """Base class for Apple Ads Platform API errors."""


class AppleAdsAuthError(AppleAdsError):
    """Credentials rejected (bad/revoked key or ids) - reconnect needed."""


class AppleAdsAccessError(AppleAdsError):
    """The token works but lacks access (role/scope) or the resource is unknown."""


class AppleAdsRateLimitedError(AppleAdsError):
    """Retries exhausted on 429 - the caller should slow down and resume later."""


class AppleAdsAPIError(AppleAdsError):
    """Any other non-recoverable response from the API."""


# --------------------------------------------------------------------------- #
# Client secret + access token lifecycle
# --------------------------------------------------------------------------- #

_token_lock = threading.Lock()
_token_cache = {"token": None, "expires_at": 0.0}

# Last seen RateLimit-* headers (ints; -1 when never seen) for Layer 1 pacing.
_rate_headers_lock = threading.Lock()
_last_rate_headers = {"limit": -1, "remaining": -1, "reset": -1}


def build_client_secret(private_key_pem, client_id, team_id, key_id) -> str:
    """Sign a short-lived ES256 JWT used as the OAuth client secret.

    Apple allows up to 180 days of validity; we mint a fresh 1-hour secret
    per token request instead, so nothing long-lived is ever persisted.
    """
    now = int(time.time())
    payload = {
        "sub": client_id,
        "iss": team_id,
        "aud": JWT_AUDIENCE,
        "iat": now,
        "exp": now + CLIENT_SECRET_TTL,
    }
    return pyjwt.encode(
        payload, private_key_pem, algorithm="ES256", headers={"kid": key_id}
    )


def fetch_access_token(credentials, sleeper=time.sleep) -> tuple[str, float]:
    """Exchange a signed client secret for a Bearer token.

    Args:
        credentials: dict with client_id, team_id, key_id, private_key_pem.

    Returns:
        (access_token, expires_at_unix_ts)

    Raises:
        AppleAdsAuthError: Apple rejected the credentials.
        AppleAdsAPIError: network/unexpected failures after retries.
    """
    secret = build_client_secret(
        credentials["private_key_pem"],
        credentials["client_id"],
        credentials["team_id"],
        credentials["key_id"],
    )
    body = {
        "grant_type": "client_credentials",
        "client_id": credentials["client_id"],
        "client_secret": secret,
        "scope": OAUTH_SCOPE,
    }
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                TOKEN_URL,
                data=body,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            last_error = e
            if attempt >= MAX_ATTEMPTS:
                raise AppleAdsAPIError(f"Network error fetching token: {e}") from e
            sleeper(_retry_delay(0, None, attempt))
            continue
        if response.status_code == 200:
            try:
                payload = response.json()
                token = payload["access_token"]
                expires_in = float(payload.get("expires_in", 3600))
            except (ValueError, KeyError, TypeError) as e:
                raise AppleAdsAPIError(
                    "Apple's token response had an unexpected shape."
                ) from e
            return token, time.time() + expires_in
        if response.status_code in (400, 401, 403):
            raise AppleAdsAuthError(
                "Apple rejected the API credentials (status "
                f"{response.status_code}). Check the Client ID, Team ID, "
                "Key ID, and that the public key is still uploaded in the "
                "Apple Ads UI."
            )
        if attempt >= MAX_ATTEMPTS:
            raise AppleAdsAPIError(
                f"Token request failed after retries (status {response.status_code})."
            )
        sleeper(_retry_delay(response.status_code, response.headers, attempt))
    raise AppleAdsAPIError(f"Token request retry loop exhausted ({last_error}).")


def _bearer(credentials, sleeper=time.sleep) -> str:
    """Return a valid cached access token, refreshing when near expiry."""
    with _token_lock:
        if (
            _token_cache["token"]
            and time.time() < _token_cache["expires_at"] - TOKEN_REFRESH_MARGIN
        ):
            return _token_cache["token"]
        token, expires_at = fetch_access_token(credentials, sleeper=sleeper)
        _token_cache["token"] = token
        _token_cache["expires_at"] = expires_at
        return token


def invalidate_token_cache() -> None:
    with _token_lock:
        _token_cache["token"] = None
        _token_cache["expires_at"] = 0.0


def get_last_rate_headers() -> dict:
    """Last seen RateLimit-* header values (ints, -1 = never seen)."""
    with _rate_headers_lock:
        return dict(_last_rate_headers)


def _capture_rate_headers(headers) -> None:
    if not headers:
        return
    with _rate_headers_lock:
        for key, name in (
            ("limit", "RateLimit-Limit"),
            ("remaining", "RateLimit-Remaining"),
            ("reset", "RateLimit-Reset"),
        ):
            raw = headers.get(name)
            if raw is not None:
                try:
                    _last_rate_headers[key] = int(raw)
                except (TypeError, ValueError):
                    pass


# --------------------------------------------------------------------------- #
# Request plumbing
# --------------------------------------------------------------------------- #

def _parse_retry_after(headers) -> float | None:
    """Parse a Retry-After header in delta-seconds or HTTP-date form."""
    raw = headers.get("Retry-After") if headers else None
    if not raw:
        return None
    try:
        return max(0.0, float(int(raw)))
    except (TypeError, ValueError):
        pass
    try:
        parsed = parsedate_to_datetime(raw)
        return max(0.0, parsed.timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def _jittered(delay: float) -> float:
    return min(MAX_RETRY_DELAY, delay * (1 + random.random() * JITTER_FACTOR))


def _retry_delay(status_code: int, headers, attempt: int) -> float:
    """Delay before retry `attempt` (1-based), honoring Retry-After on 429."""
    if status_code == 429:
        retry_after = _parse_retry_after(headers)
        if retry_after is not None:
            return _jittered(retry_after)
        base = RATE_LIMIT_BASE_DELAY
    else:
        base = TRANSIENT_BASE_DELAY
    return _jittered(base * (2 ** (attempt - 1)))


def _error_detail(payload: dict) -> str:
    """Extract a readable message from the v1 {"error": {...}} envelope."""
    try:
        error = payload.get("error") or {}
        code = str(error.get("code") or "")
        message = str(error.get("message") or "")
        details = error.get("details") or []
        first_detail = ""
        if isinstance(details, list) and details and isinstance(details[0], dict):
            first_detail = str(
                details[0].get("message") or details[0].get("code") or ""
            )
        parts = [p for p in (code, message, first_detail) if p]
        return "; ".join(parts) or "no detail"
    except (AttributeError, TypeError):
        return "no detail"


_reported_contract_changes: set[str] = set()


def _report_contract_change(detail: str) -> None:
    """Log loudly (once per detail per process) when Apple changes a shape."""
    key = detail[:80]
    if key not in _reported_contract_changes:
        _reported_contract_changes.add(key)
        logger.warning(
            "Apple Ads API response contract change detected: %s. "
            "Data may be incomplete until RespectASO is updated.",
            detail,
        )


def _request(
    method: str,
    path: str,
    credentials: dict,
    *,
    json_body=None,
    ad_account_id=None,
    sleeper=time.sleep,
) -> dict:
    """Perform one API call with auth, retries, and error mapping.

    Returns the parsed JSON payload.

    Raises:
        AppleAdsAuthError, AppleAdsAccessError, AppleAdsRateLimitedError,
        AppleAdsAPIError.
    """
    url = f"{API_BASE}{path}"
    retried_auth = False
    attempt = 0
    while True:
        attempt += 1
        headers = {"Authorization": f"Bearer {_bearer(credentials, sleeper=sleeper)}"}
        if ad_account_id:
            headers["X-AP-Context"] = f"adAccountId={ad_account_id}"
        try:
            response = requests.request(
                method,
                url,
                json=json_body,
                headers=headers,
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as e:
            if attempt >= MAX_ATTEMPTS:
                raise AppleAdsAPIError(f"Network error calling {path}: {e}") from e
            sleeper(_retry_delay(0, None, attempt))
            continue

        _capture_rate_headers(response.headers)
        status = response.status_code
        try:
            payload = response.json()
        except ValueError:
            payload = {}

        if status == 200:
            return payload
        if status == 401:
            # The cached token may have just crossed its 1-hour expiry:
            # refresh once before treating it as a credential problem.
            if not retried_auth:
                retried_auth = True
                invalidate_token_cache()
                continue
            raise AppleAdsAuthError(
                "Apple rejected the API session (status 401). Reconnect "
                "from Settings if this keeps happening."
            )
        if status in (403, 404):
            raise AppleAdsAccessError(
                f"Apple denied access to {path} (status {status}: "
                f"{_error_detail(payload)}). Check the API role and the "
                "selected ad account."
            )
        if status == 429 or status >= 500:
            if attempt >= MAX_ATTEMPTS:
                if status == 429:
                    raise AppleAdsRateLimitedError(
                        "Apple is rate limiting API requests."
                    )
                raise AppleAdsAPIError(
                    f"Apple API request to {path} failed after retries "
                    f"(status {status}, {_error_detail(payload)})."
                )
            sleeper(_retry_delay(status, response.headers, attempt))
            continue
        raise AppleAdsAPIError(
            f"Apple API request to {path} failed (status {status}, "
            f"{_error_detail(payload)})."
        )


# --------------------------------------------------------------------------- #
# Discovery endpoints (no X-AP-Context)
# --------------------------------------------------------------------------- #

def get_me(credentials, sleeper=time.sleep) -> dict:
    """Return {"user_id": ..., "org_id": ...} for the authenticated caller."""
    payload = _request("GET", "/me", credentials, sleeper=sleeper)
    result = payload.get("result") or payload.get("data") or {}
    if not isinstance(result, dict):
        _report_contract_change("/me result is not an object")
        result = {}
    return {
        "user_id": result.get("userId"),
        "org_id": result.get("orgId"),
    }


def list_acls(credentials, sleeper=time.sleep) -> list[dict]:
    """Return the ad accounts and roles this token can access.

    Each entry: {"ad_account_id", "ad_account_name", "org_id", "roles"}.
    """
    payload = _request("GET", "/acls", credentials, sleeper=sleeper)
    raw = payload.get("result") or payload.get("data") or []
    if isinstance(raw, dict):
        # Live shape (2026-08): {"success": true, "result": {"acls": [...]}}
        raw = raw.get("acls") or raw.get("userAcls") or []
    if not isinstance(raw, list):
        _report_contract_change("/acls result is not a list")
        return []
    entries = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        account = item.get("adAccount") or {}
        if not isinstance(account, dict):
            account = {}
        entries.append({
            "ad_account_id": account.get("id"),
            "ad_account_name": account.get("name") or "",
            "org_id": account.get("orgId"),
            "roles": [str(r) for r in (item.get("roles") or [])],
        })
    return entries


def get_org(credentials, org_id, sleeper=time.sleep) -> dict:
    payload = _request("GET", f"/orgs/{org_id}", credentials, sleeper=sleeper)
    result = payload.get("result") or payload.get("data") or {}
    return result if isinstance(result, dict) else {}


# --------------------------------------------------------------------------- #
# Insights: search term popularity
# --------------------------------------------------------------------------- #

def query_search_term_popularity(
    credentials,
    ad_account_id,
    *,
    country: str,
    week_start: dt.date,
    genre: str | None = None,
    offset: int = 0,
    page_size: int = PAGE_SIZE,
    sleeper=time.sleep,
) -> tuple[list[dict], int]:
    """Fetch one page of the weekly top-terms dataset for a country.

    Returns:
        (rows, total_count) - rows are raw API row dicts; total_count is -1
        when Apple omits it.
    """
    filters = [{
        "field": "countryOrRegion",
        "operator": "EQUALS",
        "value": country.upper(),
    }]
    if genre:
        filters.append({"field": "genre", "operator": "EQUALS", "value": genre})
    body = {
        "fields": POPULARITY_FIELDS,
        "filters": filters,
        "timeRange": {
            "start": week_start.isoformat(),
            "end": (week_start + dt.timedelta(days=6)).isoformat(),
            "granularity": "WEEKLY_SUN_SAT",
        },
        # No explicit sorting and no fetchTotalCount: the live endpoint
        # rejects both documented properties ("Unrecognized property",
        # 2026-08). Its default sort (genre ASC, rankInGenre ASC) is
        # deterministic, and the pager terminates on a short page instead
        # of relying on totalCount.
        "pagination": {"offset": offset, "pageSize": page_size},
    }
    payload = _request(
        "POST",
        "/insights/apps/search-term-popularity/query",
        credentials,
        json_body=body,
        ad_account_id=ad_account_id,
        sleeper=sleeper,
    )
    return _parse_rows(payload, "search-term-popularity")


def iter_search_term_popularity(
    credentials,
    ad_account_id,
    *,
    country: str,
    week_start: dt.date,
    page_size: int = PAGE_SIZE,
    between_pages=None,
    max_pages: int | None = None,
    sleeper=time.sleep,
):
    """Yield (page_rows, total_count, page_index) over the full dataset.

    The caller owns pacing: `between_pages()` is invoked before every page
    after the first (sync.py sleeps and enforces budgets there).
    """
    offset = 0
    page_index = 0
    while True:
        if page_index > 0 and between_pages is not None:
            between_pages()
        rows, total_count = query_search_term_popularity(
            credentials,
            ad_account_id,
            country=country,
            week_start=week_start,
            offset=offset,
            page_size=page_size,
            sleeper=sleeper,
        )
        yield rows, total_count, page_index
        page_index += 1
        offset += len(rows)
        if not rows or len(rows) < page_size:
            return
        if total_count >= 0 and offset >= total_count:
            return
        if max_pages is not None and page_index >= max_pages:
            return


def _parse_rows(payload: dict, label: str) -> tuple[list[dict], int]:
    result = payload.get("result") or {}
    rows = result.get("rows") if isinstance(result, dict) else None
    if rows is None and isinstance(result, list):
        rows = result  # tolerate a flat result list
    if not isinstance(rows, list):
        _report_contract_change(f"{label}: rows is not a list")
        rows = []
    pagination = payload.get("pagination") or {}
    try:
        total_count = int(pagination.get("totalCount"))
    except (TypeError, ValueError):
        total_count = -1
    return [r for r in rows if isinstance(r, dict)], total_count


# --------------------------------------------------------------------------- #
# Insights: impression share
# --------------------------------------------------------------------------- #

def query_impression_share(
    credentials,
    ad_account_id,
    *,
    promoted_object_id: str,
    week_start: dt.date,
    weeks: int = 1,
    country: str | None = None,
    report_type: str = "ALL_SLOTS",
    offset: int = 0,
    page_size: int = PAGE_SIZE,
    sleeper=time.sleep,
) -> tuple[list[dict], int]:
    """Fetch impression-share rows for one app (WEEKLY_SUN_SAT, max 4 weeks).

    week_start MUST be a Sunday (asserted - callers compute it with the
    week helpers below). The country filter only supports EQUALS, so
    callers query per country when they need more than one; omitting it
    returns all countries where the app's ads served.
    """
    if week_start.weekday() != 6:
        raise ValueError(f"week_start {week_start} is not a Sunday.")
    weeks = max(1, min(4, weeks))
    # The live endpoint rejects EQUALS for promotedObjectId (2026-08,
    # despite the docs) - it requires IN with a list.
    filters = [{
        "field": "promotedObjectId",
        "operator": "IN",
        "value": [str(promoted_object_id)],
    }]
    if country:
        filters.append({
            "field": "countryOrRegion",
            "operator": "EQUALS",
            "value": country.upper(),
        })
    body = {
        "filters": filters,
        "options": {"impressionShareReportType": report_type},
        "timeRange": {
            "start": week_start.isoformat(),
            "end": (week_start + dt.timedelta(days=7 * weeks - 1)).isoformat(),
            "granularity": "WEEKLY_SUN_SAT",
        },
        "pagination": {"offset": offset, "pageSize": page_size},
    }
    payload = _request(
        "POST",
        "/insights/apps/impression-share/query",
        credentials,
        json_body=body,
        ad_account_id=ad_account_id,
        sleeper=sleeper,
    )
    return _parse_rows(payload, "impression-share")


# --------------------------------------------------------------------------- #
# Week math (pure helpers, UTC-fixed by the API contract)
# --------------------------------------------------------------------------- #

def week_start_sunday(day: dt.date) -> dt.date:
    """Return the Sunday starting the Sun-Sat week that contains `day`."""
    return day - dt.timedelta(days=(day.weekday() + 1) % 7)


def latest_available_week(now: dt.datetime | None = None) -> dt.date:
    """Sunday of the newest completed week Apple has published.

    Weekly data for the preceding Sun-Sat week is generated Mondays at
    07:00 UTC.
    """
    if now is None:
        now = dt.datetime.now(dt.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=dt.timezone.utc)
    today = now.date()
    current_week_start = week_start_sunday(today)
    publication = dt.datetime.combine(
        current_week_start + dt.timedelta(days=1),  # Monday of current week
        dt.time(PUBLICATION_HOUR_UTC, 0),
        tzinfo=dt.timezone.utc,
    )
    if now >= publication:
        return current_week_start - dt.timedelta(days=7)
    return current_week_start - dt.timedelta(days=14)


def weeks_back(week: dt.date, n: int) -> dt.date:
    """The Sunday `n` weeks before `week`."""
    return week - dt.timedelta(days=7 * n)
