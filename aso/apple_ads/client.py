"""HTTP client for the Apple Ads keyword-planner popularity endpoint.

Endpoint (the same API the ads.apple.com keyword-planner UI calls):

    POST https://app-ads.apple.com/cm/api/v2/keywords/popularities?adamId={id}
    body: {"storefronts": [...], "terms": [...]}   (max 100 terms per call)
    auth: Apple web-session cookies (captured by aso.apple_ads.auth)

Response: {"status": "success", "data": [{"name", "popularity"}, ...]}
where popularity is a granular 5-100 integer, or null when the term is
below Apple's reporting threshold.

Rate-limit policy (constants below are the single tuning point):
  Layer 1 - proactive pacing: callers (sync.py) space calls sequentially.
  Layer 2 - reactive backoff: honor Retry-After on 429 (seconds or
            HTTP-date form) with jitter; otherwise jittered exponential
            backoff, larger base for 429 than for transient errors.
  Layer 3 - adaptive slow-down: RateLimitedError signals the sync run to
            double its pacing delay and, when retries are exhausted,
            abort gracefully and resume next tick.
  Layer 4 - self-imposed ceilings: enforced by sync.py via the request
            log in storage.py.

This module is used ONLY from background threads (sync, settings test
fetch) - never from the request path of scoring views.
"""

import logging
import random
import time
from email.utils import parsedate_to_datetime

import requests

logger = logging.getLogger(__name__)

POPULARITY_URL = "https://app-ads.apple.com/cm/api/v2/keywords/popularities"
ORIGIN = "https://app-ads.apple.com"

MAX_TERMS_PER_CALL = 100
REQUEST_TIMEOUT = 30

MAX_ATTEMPTS = 4
TRANSIENT_BASE_DELAY = 1.0     # 1s -> 2s -> 4s -> 8s
RATE_LIMIT_BASE_DELAY = 5.0    # 5s -> 10s -> 20s -> 40s
MAX_RETRY_DELAY = 60.0
JITTER_FACTOR = 0.25

# Error message codes observed from the endpoint.
MSG_NO_USER_OWNED_APPS = "NO_USER_OWNED_APPS_FOUND_CODE"
INTERNAL_ERROR_REFRESH = "REFRESH"
KWS_PREFIX = "KWS_"


class AppleAdsError(Exception):
    """Base class for Apple Ads client errors."""


class AppleAdsAuthError(AppleAdsError):
    """Session cookies are missing, expired, or rejected - re-sign-in needed."""


class AppleAdsAppAccessError(AppleAdsError):
    """The configured Primary App ID is not accessible to this account."""


class AppleAdsRateLimitedError(AppleAdsError):
    """Retries exhausted on 429 - the caller should slow down and resume later."""


class AppleAdsAPIError(AppleAdsError):
    """Any other non-recoverable response from the endpoint."""


def _first_error(payload: dict) -> tuple[str, str]:
    """Return (messageCode, message) from an error payload, tolerating shape."""
    try:
        first = (payload.get("error") or {}).get("errors") or [{}]
        first = first[0] or {}
        return str(first.get("messageCode") or ""), str(first.get("message") or "")
    except (AttributeError, IndexError, TypeError):
        return "", ""


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
        dt = parsedate_to_datetime(raw)
        return max(0.0, dt.timestamp() - time.time())
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


def _classify_response(status_code: int, payload: dict) -> str:
    """Map a response to: ok | auth | app_access | rate_limited | transient | error."""
    if status_code == 200 and payload.get("status") == "success":
        return "ok"
    if status_code == 401:
        return "auth"
    if status_code == 429:
        return "rate_limited"
    message_code, message = _first_error(payload)
    if status_code == 403:
        if payload.get("internalErrorCode") == INTERNAL_ERROR_REFRESH:
            return "auth"
        if message_code == MSG_NO_USER_OWNED_APPS or (
            "no user owned apps found" in message.lower()
        ):
            return "app_access"
        if message_code.startswith(KWS_PREFIX):
            return "transient"
        return "auth"  # Unknown 403s are treated as session problems.
    if status_code >= 500:
        return "transient"
    return "error"


def _report_contract_change(detail: str, payload: dict) -> None:
    """Log loudly (once per detail per process) when Apple changes the response shape."""
    key = detail[:80]
    if key not in _reported_contract_changes:
        _reported_contract_changes.add(key)
        logger.warning(
            "Apple popularity response contract change detected: %s "
            "(status=%s requestID=%s). Values may be incomplete until "
            "RespectASO is updated.",
            detail,
            payload.get("status"),
            payload.get("requestID"),
        )


_reported_contract_changes: set[str] = set()


def _parse_popularities(payload: dict, requested_terms: list[str]) -> dict:
    """Extract {term: popularity_or_None} from a success payload.

    Terms Apple did not echo back at all are absent from the result
    (distinct from an explicit null, which means below-threshold).
    """
    data = payload.get("data")
    if not isinstance(data, list):
        _report_contract_change(
            f"data is {type(data).__name__}, expected list", payload
        )
        return {}
    requested = set(requested_terms)
    result = {}
    invalid = 0
    for item in data:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            invalid += 1
            continue
        name = item["name"].lower().strip()
        popularity = item.get("popularity")
        if popularity is not None and not isinstance(popularity, (int, float)):
            invalid += 1
            continue
        if name in requested:
            result[name] = int(popularity) if popularity is not None else None
    if invalid:
        _report_contract_change(f"{invalid} malformed data[] items", payload)
    return result


def fetch_popularities(
    terms: list[str],
    country: str,
    primary_app_id: str,
    cookie_header: str,
    sleeper=time.sleep,
) -> dict:
    """Fetch Apple popularity for up to MAX_TERMS_PER_CALL normalized terms.

    Args:
        terms: Normalized keywords (lower().strip()).
        country: Storefront country code (e.g. "us"); sent uppercase.
        primary_app_id: App id used as request context (adamId).
        cookie_header: Assembled "name=value; ..." session cookie header.
        sleeper: Injection point for tests (replaces time.sleep).

    Returns:
        {term: popularity_or_None} for every term Apple echoed back.

    Raises:
        AppleAdsAuthError, AppleAdsAppAccessError, AppleAdsRateLimitedError,
        AppleAdsAPIError, ValueError (too many terms).
    """
    if len(terms) > MAX_TERMS_PER_CALL:
        raise ValueError(
            f"At most {MAX_TERMS_PER_CALL} terms per call (got {len(terms)})."
        )
    if not terms:
        return {}
    if not cookie_header.strip():
        raise AppleAdsAuthError("No Apple session - sign in first.")

    url = f"{POPULARITY_URL}?adamId={primary_app_id}"
    body = {"storefronts": [country.upper()] if country else [], "terms": terms}
    headers = {
        "Origin": ORIGIN,
        "Referer": f"{ORIGIN}/",
        "Content-Type": "application/json",
        "Cookie": cookie_header,
    }

    last_status = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = requests.post(
                url, json=body, headers=headers, timeout=REQUEST_TIMEOUT
            )
            status_code = response.status_code
            try:
                payload = response.json()
            except ValueError:
                payload = {}
        except requests.RequestException as e:
            if attempt >= MAX_ATTEMPTS:
                raise AppleAdsAPIError(f"Network error: {e}") from e
            sleeper(_retry_delay(0, None, attempt))
            continue

        kind = _classify_response(status_code, payload)
        last_status = status_code
        if kind == "ok":
            return _parse_popularities(payload, terms)
        if kind == "auth":
            raise AppleAdsAuthError(
                "Apple rejected the session (status "
                f"{status_code}). Sign in again from Settings."
            )
        if kind == "app_access":
            raise AppleAdsAppAccessError(
                f"Primary App ID {primary_app_id} is not accessible to this "
                "Apple Ads account. Set an app id your account can access."
            )
        if kind in ("rate_limited", "transient"):
            if attempt >= MAX_ATTEMPTS:
                if kind == "rate_limited":
                    raise AppleAdsRateLimitedError(
                        "Apple is rate limiting popularity requests."
                    )
                message_code, message = _first_error(payload)
                raise AppleAdsAPIError(
                    f"Apple popularity request failed after retries "
                    f"(status {status_code}, {message_code or message or 'no detail'})."
                )
            sleeper(_retry_delay(status_code, response.headers, attempt))
            continue
        message_code, message = _first_error(payload)
        raise AppleAdsAPIError(
            f"Apple popularity request failed (status {status_code}, "
            f"{message_code or message or 'no detail'})."
        )

    raise AppleAdsAPIError(
        f"Apple popularity request retry loop exhausted (last status {last_status})."
    )
