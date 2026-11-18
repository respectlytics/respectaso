"""Asks respectaso.com for the newest RespectASO version, at most once a day.

Both editions run this (it is synced to the free edition). The contract,
from docs/development/STORE_AUTOPILOT_PLAN.md step 14:

- At most one call per UTC calendar day. The answer and the day are kept
  in DATA_DIR/update_check.json; every other page load that day gets the
  stored answer, so a restart does not ask again.
- The call carries exactly three values: the version (``v``), the edition
  (``e``: free, pro or docker) and the macOS major version (``os``, empty
  off macOS). No identifier, no stored random value. "Check now" adds
  ``manual=1`` so the server does not count that copy twice the same day.
- Offline, the stored answer stands and no error shows. A call that got no
  reply at all (no network, timeout), or any error status from the server
  (it could not reach GitHub, or is not there yet), is tried again after
  RETRY_AFTER_FAILURE_SECONDS; any other reply counts as that day's call.
  Only Check now, or an unusable reply, reports an error.

The server answers ``{"version", "release_url", "download_url", "notes"}``.
Whether that version is newer is decided here, on every read, against the
running VERSION, so a stored answer is right again the moment the app is
updated.
"""

import json
import logging
import os
import platform
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

from django.conf import settings

from .copy_rules import no_long_dash
from .user_agent import user_agent

logger = logging.getLogger(__name__)

UPDATE_URL = "https://respectaso.com/api/app/latest/"
REQUEST_TIMEOUT_SECONDS = 5
RETRY_AFTER_FAILURE_SECONDS = 60 * 60
STATE_FILENAME = "update_check.json"


_VERSION_RX = re.compile(r"^\d+(\.\d+){1,3}$")
# Hosts the banner and the download button may open. Any other link in an
# answer is dropped, so a bad answer can never send a click elsewhere.
_LINK_HOSTS = ("github.com", "objects.githubusercontent.com", "respectaso.com")

_lock = threading.Lock()
_retry_after = None  # time.monotonic() before which an unreached call is not retried.


# ── What the call sends ──────────────────────────────────────────────────


def edition() -> str:
    """pro when the Pro app is installed, docker in the Docker image, else free."""
    from django.apps import apps as django_apps

    if django_apps.is_installed("aso_pro"):
        return "pro"
    if os.environ.get("RESPECTASO_DOCKER") == "1":
        return "docker"
    return "free"


def macos_major() -> str:
    """The macOS major version ("15"), or "" off macOS."""
    release = platform.mac_ver()[0]
    return release.split(".")[0] if release else ""


def query(manual: bool = False) -> dict:
    """The exact query of one call: v, e, os, and manual=1 for Check now."""
    params = {"v": settings.VERSION, "e": edition(), "os": macos_major()}
    if manual:
        params["manual"] = "1"
    return params


# ── Stored answer ────────────────────────────────────────────────────────


def _state_path() -> Path:
    return Path(settings.DATA_DIR) / STATE_FILENAME


def _load_state() -> dict:
    try:
        data = json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _save_state(state: dict) -> None:
    path = _state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(state), encoding="utf-8")
        os.replace(tmp, path)
    except OSError as e:
        logger.warning("Could not store the update check: %s", e)


def _today() -> str:
    return datetime.now(UTC).date().isoformat()


# ── The call ─────────────────────────────────────────────────────────────


def _safe_link(value) -> str:
    if not isinstance(value, str):
        return ""
    parts = urllib.parse.urlsplit(value)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        return ""
    if not any(host == allowed or host.endswith("." + allowed) for allowed in _LINK_HOSTS):
        return ""
    return value


def _clean_answer(data) -> dict | None:
    """The server's answer reduced to its four fields, or None when unusable."""
    if not isinstance(data, dict):
        return None
    version = str(data.get("version") or "").strip().lstrip("v")
    if not _VERSION_RX.match(version):
        return None
    notes = data.get("notes")
    return {
        "version": version,
        "release_url": _safe_link(data.get("release_url")),
        "download_url": _safe_link(data.get("download_url")),
        "notes": notes if isinstance(notes, str) else "",
    }


def _fetch(manual: bool):
    """One call to respectaso.com: (answer or None, outcome).

    outcome is "ok", "answered" (a 200 reply without a usable answer) or
    "unreached" (no reply at all, or any error status: the same wait for us).
    """
    url = f"{UPDATE_URL}?{urllib.parse.urlencode(query(manual))}"
    request = urllib.request.Request(
        url, headers={"User-Agent": user_agent(), "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as resp:
            answer = _clean_answer(json.loads(resp.read().decode()))
    except urllib.error.HTTPError as e:
        # Any error status is ours to fix, never the person's (503: GitHub not reached yet; 404:
        # the site not deployed; 5xx from the network in front): quiet, tried again in an hour.
        logger.info("Update check: respectaso.com answered %s", e.code)
        return None, "unreached"
    except (ValueError, UnicodeDecodeError) as e:
        logger.warning("Update check: unreadable answer: %s", e)
        return None, "answered"
    except Exception as e:  # noqa: BLE001 (no network, timeout: the stored answer stands)
        logger.info("Update check: respectaso.com not reached: %s: %s", type(e).__name__, e)
        return None, "unreached"
    return answer, ("ok" if answer else "answered")


def _is_newer(latest: str, current: str) -> bool:
    try:
        return [int(x) for x in latest.split(".")] > [int(x) for x in current.split(".")]
    except ValueError:
        return False


def _payload(answer: dict | None, error: bool = True) -> dict:
    """What the page renders, from a stored or fresh answer. Without an
    answer it carries ``error`` unless ``error`` is False (offline: quiet)."""
    current = settings.VERSION
    payload = {
        "update_available": False,
        "current": current,
        "is_native": getattr(settings, "IS_NATIVE_APP", False),
    }
    if answer:
        payload.update({
            "update_available": _is_newer(answer["version"], current),
            "latest": answer["version"],
            "release_url": answer["release_url"],
            "download_url": answer["download_url"],
            "release_notes": no_long_dash(answer["notes"]),
        })
    elif error:
        payload["error"] = "unavailable"
    return payload


def check_for_update(force: bool = False) -> dict:
    """Return the update payload the page renders.

    ``force`` is "Check now": it always calls, with ``manual=1``. Otherwise
    respectaso.com is called only when no call was made today (UTC). The
    lock makes concurrent page loads share one call.
    """
    global _retry_after
    with _lock:
        state = _load_state()
        stored = _clean_answer(state.get("answer"))
        if not force:
            if state.get("date") == _today():
                return _payload(stored)
            if _retry_after is not None and time.monotonic() < _retry_after:
                return _payload(stored, error=False)
        answer, outcome = _fetch(manual=force)
        if outcome == "unreached":
            _retry_after = time.monotonic() + RETRY_AFTER_FAILURE_SECONDS
            return _payload(stored, error=force)
        _retry_after = None
        kept = answer or stored
        _save_state({"date": _today(), "answer": kept})
        return _payload(kept)
