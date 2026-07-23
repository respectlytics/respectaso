"""Embedded Apple Ads sign-in and session-cookie management.

Native (pywebview) mode: opens a second WebKit window at the Apple Ads
dashboard. The user signs in with their Apple ID (including 2FA) exactly
as in a browser. When the window reaches the authenticated dashboard, the
session cookies are harvested from the webview cookie store, persisted via
storage.py (600-perms settings file), and the window closes itself.

Browser/Docker mode (IS_NATIVE_APP False): there is no webview to open -
start_signin() reports "unavailable" and the settings page tells the user
to sign in from the desktop app (both modes share the same data directory
conventions, so a desktop sign-in also serves a browser session on the
same machine).

The Django server runs in a thread of the same process as the pywebview
main loop (see desktop/main.py), so webview.create_window() is callable
directly from view code through start_signin().
"""

import logging
import threading
import time

from django.conf import settings

from . import storage

logger = logging.getLogger(__name__)

# The dashboard URL - Apple redirects to its sign-in flow when the session
# is missing, and back here once authentication completes.
DASHBOARD_URL = "https://app-ads.apple.com/cm/app"
_DASHBOARD_PREFIX = "https://app-ads.apple.com/cm/"

SIGNIN_TIMEOUT_SECONDS = 600  # Give 2FA plenty of room.

# The session cookie that must be present for the harvest to count - the
# dashboard sets its cookies progressively after login, and harvesting too
# early captures an incomplete set (symptom: KWS_NO_ORG_CONTENT_PROVIDERS
# on every later request until re-sign-in).
ESSENTIAL_COOKIE = "myacinfo"
HARVEST_SETTLE_SECONDS = 2.5   # Let the dashboard finish setting cookies.
HARVEST_MAX_TRIES = 12         # Re-collect until the set looks complete.
HARVEST_RETRY_SECONDS = 1.5

# After login, the dashboard SPA performs Apple's org-context bootstrap in
# the background. A session captured before that finishes fails every API
# call with KWS_NO_ORG_CONTENT_PROVIDERS. So after harvesting, the session
# is VERIFIED with a real popularity call. The window stays open only for
# a short bounded grace period (helping the SPA bootstrap), then closes
# itself; verification continues headless. The session is SAVED as soon as
# it is complete - closing the window early can never lose it.
VERIFY_TERM = "fitness"
VERIFY_MAX_TRIES = 6
VERIFY_RETRY_SECONDS = 4.0
SPA_BOOT_GRACE_SECONDS = 20.0   # Max time the window stays open after login.
# Cap the client's internal retry backoff during verification/testing so a
# single call stays fast (the webview's fetch aborts after ~60s).
FAST_SLEEPER = lambda s: time.sleep(min(s, 1.0))  # noqa: E731

_state_lock = threading.Lock()
_signin_state = {
    # idle | active | finalizing | success | cancelled | error | unavailable
    "status": "idle",
    "error": "",
}
# Current attempt's window + completion event, so a NEW sign-in click can
# cleanly discard a stuck/abandoned previous attempt instead of dead-ending.
_signin_window = None
_signin_done: threading.Event | None = None


def is_native() -> bool:
    return bool(getattr(settings, "IS_NATIVE_APP", False))


def get_signin_status() -> dict:
    with _state_lock:
        return dict(_signin_state)


def _set_state(status: str, error: str = "") -> None:
    with _state_lock:
        _signin_state["status"] = status
        _signin_state["error"] = error


def cookie_header() -> str:
    """Assemble the stored session cookies into a Cookie header value.

    Expired cookies are dropped. Empty string means no usable session.
    """
    now = time.time()
    parts = []
    for cookie in storage.load_apple_settings()["apple_ads"]["cookies"]:
        expires = cookie.get("expires")
        if expires and float(expires) > 0 and float(expires) < now:
            continue
        name, value = cookie.get("name"), cookie.get("value")
        if name and value is not None:
            parts.append(f"{name}={value}")
    return "; ".join(parts)


def session_valid() -> bool:
    return bool(cookie_header())


def sign_out() -> None:
    """Discard the stored session and require a fresh sign-in + test."""
    storage.save_apple_settings(
        apple_ads={"cookies": [], "tested_ok": False, "session_expired": False}
    )


def mark_session_expired() -> None:
    """Called by sync/client paths on auth failures - drives the banners.

    The timestamp keys the dismissible staleness notice (internal source):
    a new expiry event re-shows a previously dismissed notice.
    """
    storage.save_apple_settings(
        apple_ads={
            "session_expired": True,
            "session_expired_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
    )


def _cookies_complete(cookies: list[dict]) -> bool:
    """A harvested cookie set counts only once the essential session cookie
    is present - the dashboard sets cookies progressively after login."""
    return any(c.get("name") == ESSENTIAL_COOKIE for c in cookies)


def start_signin() -> dict:
    """Open the embedded sign-in window (native mode). Returns the state dict.

    A new call while a previous attempt is still open (or stuck) discards
    that attempt and starts fresh - the button must always work. The heavy
    lifting (waiting for the dashboard, harvesting cookies) runs on webview
    event handlers plus worker threads; this call returns immediately.
    """
    global _signin_window, _signin_done

    if not is_native():
        _set_state(
            "unavailable",
            "Apple sign-in needs the RespectASO desktop app. Open the app "
            "and sign in there - the session is stored in your data folder.",
        )
        return get_signin_status()

    # Discard any previous attempt: mark it finished FIRST so its closed
    # handler and watchdog become no-ops, then destroy its window.
    old_done, old_window = _signin_done, _signin_window
    if old_done is not None:
        old_done.set()
    if old_window is not None:
        _destroy(old_window)
    _set_state("active")

    try:
        import webview
    except ImportError:
        _set_state("unavailable", "The embedded browser is not available.")
        return get_signin_status()

    try:
        window = webview.create_window(
            "Sign in to Apple Ads",
            DASHBOARD_URL,
            width=980,
            height=760,
            on_top=True,
        )
    except Exception as e:
        logger.warning("Could not open the Apple sign-in window: %s", e)
        _set_state("error", "Could not open the sign-in window. Try again.")
        return get_signin_status()

    done = threading.Event()
    _signin_window, _signin_done = window, done
    harvest_started = threading.Event()
    # Set once the session is harvested and saved: from that point on,
    # closing the window (by the user or by us) must never cancel anything.
    verifying = threading.Event()

    def _collect_cookies():
        try:
            return _serialize_cookies(window.get_cookies())
        except Exception as e:  # window closing, webview quirk, ...
            logger.warning("Cookie harvest attempt failed: %s", e)
            return []

    def _harvest_worker():
        """Collect the session, SAVE it immediately, then verify it with a
        real popularity call (which IS the connection test).

        Design rules learned the hard way:
        - The session is saved the moment it is complete - the user
          closing the window early can never lose a finished sign-in.
        - The window closes itself after a short bounded grace period
          (it only stays open to let the dashboard SPA finish Apple's
          org-context bootstrap); verification continues headless.
        - Verification calls use a capped sleeper so each attempt stays
          fast.
        """
        time.sleep(HARVEST_SETTLE_SECONDS)

        # Phase 1: wait for the essential session cookie.
        cookies = []
        for _ in range(HARVEST_MAX_TRIES):
            if done.is_set():
                return
            cookies = _collect_cookies()
            if _cookies_complete(cookies):
                break
            time.sleep(HARVEST_RETRY_SECONDS)
        if not _cookies_complete(cookies):
            if not done.is_set():
                logger.warning("Sign-in harvest never saw a complete cookie set")
                _set_state(
                    "error",
                    "Signed in, but the Apple session never completed. "
                    "Close the window and try signing in again.",
                )
                done.set()
                _destroy(window)
            return

        # Save NOW - whatever happens next, the sign-in itself is banked.
        storage.save_apple_settings(
            apple_ads={"cookies": cookies, "session_expired": False}
        )
        verifying.set()

        # Phase 2: verify against the real endpoint. The window stays open
        # only within the SPA-boot grace period, then closes itself.
        primary_app_id = storage.load_apple_settings()["apple_ads"]["primary_app_id"]
        if not primary_app_id:
            # First-ever sign-in: nothing to verify with yet.
            _set_state("success")
            done.set()
            _destroy(window)
            return

        from .client import AppleAdsError, fetch_popularities

        _set_state("finalizing")
        window_open = True
        phase2_start = time.monotonic()
        for attempt in range(1, VERIFY_MAX_TRIES + 1):
            if done.is_set():
                return
            if window_open and time.monotonic() - phase2_start > SPA_BOOT_GRACE_SECONDS:
                _destroy(window)
                window_open = False
            if window_open:
                fresh = _collect_cookies()
                if _cookies_complete(fresh):
                    cookies = fresh
                    storage.save_apple_settings(
                        apple_ads={"cookies": cookies, "session_expired": False}
                    )
            header = "; ".join(
                f"{c['name']}={c['value']}" for c in cookies if c.get("name")
            )
            try:
                fetch_popularities(
                    [VERIFY_TERM], "us", primary_app_id, header,
                    sleeper=FAST_SLEEPER,
                )
            except AppleAdsError as e:
                logger.info(
                    "Sign-in verification attempt %d/%d not ready yet: %s",
                    attempt, VERIFY_MAX_TRIES, e,
                )
                time.sleep(VERIFY_RETRY_SECONDS)
                continue
            except Exception as e:
                logger.warning("Sign-in verification crashed: %s", e)
                break
            # Verified: this WAS the connection test.
            storage.save_apple_settings(
                apple_ads={"session_expired": False, "tested_ok": True}
            )
            _set_state("success")
            done.set()
            _destroy(window)
            return

        # Verification never succeeded: the session is saved regardless;
        # be explicit about the remaining step.
        if not done.is_set():
            _set_state(
                "error",
                "Signed in, but Apple's session isn't serving keyword "
                "data yet. Wait a minute, then click Test connection.",
            )
            done.set()
            _destroy(window)

    def harvest_if_signed_in():
        """On each page load, start the harvest once we reach the dashboard."""
        try:
            url = window.get_current_url() or ""
        except Exception:
            return
        if not url.startswith(_DASHBOARD_PREFIX):
            return  # Still on Apple's sign-in / 2FA pages.
        if harvest_started.is_set():
            return
        harvest_started.set()
        threading.Thread(
            target=_harvest_worker, daemon=True, name="apple-signin-harvest"
        ).start()

    def on_closed():
        if done.is_set():
            return
        if verifying.is_set():
            # The session is already saved; verification continues headless.
            return
        _set_state("cancelled", "Sign-in window was closed before finishing.")
        done.set()

    def watchdog():
        if not done.wait(SIGNIN_TIMEOUT_SECONDS):
            _set_state("error", "Sign-in timed out. Try again.")
            done.set()
            _destroy(window)

    window.events.loaded += harvest_if_signed_in
    window.events.closed += on_closed
    threading.Thread(target=watchdog, daemon=True, name="apple-signin-watchdog").start()
    return get_signin_status()


def _serialize_cookies(raw_cookies) -> list[dict]:
    """Convert pywebview's SimpleCookie list into storable dicts."""
    cookies = []
    for jar in raw_cookies or []:
        for name, morsel in jar.items():
            expires = _expiry_timestamp(morsel.get("expires"))
            cookies.append(
                {
                    "name": name,
                    "value": morsel.value,
                    "domain": morsel.get("domain", ""),
                    "path": morsel.get("path", "/"),
                    "expires": expires,
                }
            )
    return cookies


def _expiry_timestamp(raw) -> float:
    """Cookie expiry as a Unix timestamp; 0.0 means session cookie / unknown.

    pywebview's backends disagree on the expires type: the macOS (Cocoa)
    backend stores a native NSDate object on the morsel, others an HTTP-date
    string. Never let a parse problem break the harvest - an unknown expiry
    just means the cookie is kept until Apple rejects it (session_expired
    then drives re-sign-in).
    """
    if not raw:
        return 0.0
    interval = getattr(raw, "timeIntervalSince1970", None)  # NSDate
    if callable(interval):
        try:
            return float(interval())
        except Exception:
            return 0.0
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(str(raw)).timestamp()
    except Exception:
        return 0.0


def _destroy(window) -> None:
    try:
        window.destroy()
    except Exception:
        pass
