"""Asks GitHub for the newest RespectASO release, at most once per interval.

Every page load asks the server whether a newer version exists so the
update banner can appear. GitHub allows only 60 unauthenticated requests
per hour per IP address, so asking on every page load exhausted that
quota within minutes of active use, after which every page showed
"Unable to check for updates" until the hour rolled over.

The outcome of each attempt - success or failure - is kept for
CHECK_INTERVAL_SECONDS and served to every page in between, so the app
contacts GitHub at most six times an hour. A restart clears the cache,
so a freshly launched app still learns about an update immediately.
"""

import json
import logging
import threading
import time
import urllib.request

from django.conf import settings

logger = logging.getLogger(__name__)

RELEASES_URL = "https://api.github.com/repos/respectlytics/respectaso/releases/latest"
CHECK_INTERVAL_SECONDS = 10 * 60
REQUEST_TIMEOUT_SECONDS = 5

_lock = threading.Lock()
_last_attempt = None  # time.monotonic() of the last call to GitHub.
_last_result = None  # Payload from that call, success or failure.


def check_for_update():
    """Return the update payload the page renders.

    GitHub is contacted only when no attempt has been made in the last
    CHECK_INTERVAL_SECONDS; otherwise the previous outcome is returned.
    The lock also makes concurrent page loads share one attempt instead
    of each starting their own.
    """
    global _last_attempt, _last_result
    with _lock:
        now = time.monotonic()
        if _last_result is None or now - _last_attempt >= CHECK_INTERVAL_SECONDS:
            _last_result = _fetch_latest_release()
            _last_attempt = now
        return dict(_last_result)


def _fetch_latest_release():
    current = settings.VERSION
    is_native = getattr(settings, "IS_NATIVE_APP", False)
    try:
        req = urllib.request.Request(
            RELEASES_URL, headers={"Accept": "application/vnd.github.v3+json"},
        )
        with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode())
        latest = data.get("tag_name", "").lstrip("v")
        if not latest:
            return {"update_available": False, "current": current, "is_native": is_native}
        current_parts = [int(x) for x in current.split(".")]
        latest_parts = [int(x) for x in latest.split(".")]
        download_url = ""
        for asset in data.get("assets", []):
            if asset.get("name", "").endswith(".dmg"):
                download_url = asset.get("browser_download_url", "")
                break
        return {
            "update_available": latest_parts > current_parts,
            "current": current,
            "latest": latest,
            "release_url": data.get("html_url", ""),
            "release_notes": data.get("body", ""),
            "download_url": download_url,
            "is_native": is_native,
        }
    except Exception as e:
        logger.warning("Update check failed: %s: %s", type(e).__name__, e)
        return {
            "update_available": False,
            "error": type(e).__name__,
            "current": current,
            "is_native": is_native,
        }
