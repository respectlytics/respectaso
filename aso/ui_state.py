"""Per-install UI state that must survive a restart (dismissed notices, the
time zone the reader's browser reports).

Server-side by design: the desktop edition runs inside pywebview's WebKit
view, where `localStorage` is off-limits (desktop-compat.instructions.md),
and the state has to behave identically in the native, Docker and browser
editions. Kept in its own small JSON file rather than in settings.json,
which holds secrets (API keys, Apple credentials) and is written by two
other modules - a dismissal is not worth touching that file for.

Ships in the free-tier `aso` app, so it must not import from aso_pro or
licensing.
"""

import contextlib
import json
import logging
import os
import tempfile
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

_FILENAME = "ui_state.json"

# One change at a time, read and written whole: two requests at once (a notice's
# X sends one per key) once wrote over each other and left a file nobody could read.
_lock = threading.RLock()

# Dismissible notices, by key. One entry per notice so a future one does
# not need a new file.
KEYWORD_CLEANUP_BANNER = "keyword_cleanup_banner"
# The soft notice that Apple rejected credentials which used to work, under
# the RespectASO estimate (partials/popularity_banner.html). Dismissed per
# rejection: a later rejection has a new time and shows the notice again.
APPLE_STALE_BANNER = "apple_stale_banner"

# The Mac app's one-time question and notice (desktop/mac_integration.py):
# whether to open at login, and what closing the window does.
LOGIN_ITEM_QUESTION = "login_item_question"
FIRST_CLOSE_NOTICE = "first_close_notice"


# A notice closed with its X (aso/partials/notice_close.html): kept as
# "notice:<key>", where the key names the state the notice said ("store-run:12",
# "store-page:abc:draft"), so a new state shows a new notice. The newest
# NOTICE_KEEP are kept; an older one is long gone from every screen.
NOTICE_PREFIX = "notice:"
NOTICE_KEEP = 300
NOTICE_KEY = r"^[a-z][a-z-]{0,30}:[A-Za-z0-9:._-]{1,100}$"


def notice_dismissed(key: str) -> bool:
    """Whether the notice named ``key`` was closed with its X."""
    return bool(key) and is_dismissed(NOTICE_PREFIX + key)


def dismissed_notices() -> set[str]:
    """The names of every notice closed with its X, read from the file once."""
    dismissed = _load().get("dismissed")
    if not isinstance(dismissed, dict):
        return set()
    return {key[len(NOTICE_PREFIX):] for key, value in dismissed.items() if value and key.startswith(NOTICE_PREFIX)}


def dismiss_notice(*keys: str) -> None:
    """Keep notices closed; raises ValueError, keeping none, for a key no notice uses."""
    import re

    if not keys or not all(isinstance(key, str) and re.match(NOTICE_KEY, key) for key in keys):
        raise ValueError("No notice has that name.")
    with _lock:
        data = _load()
        dismissed = data.get("dismissed")
        if not isinstance(dismissed, dict):
            dismissed = {}
        for key in keys:
            dismissed.pop(NOTICE_PREFIX + key, None)
            dismissed[NOTICE_PREFIX + key] = True
        notices = [k for k in dismissed if k.startswith(NOTICE_PREFIX)]
        for old in notices[:-NOTICE_KEEP]:
            del dismissed[old]
        data["dismissed"] = dismissed
        _save(data)


def apple_stale_banner_key(rejected_at) -> str:
    """The dismissal key of the Apple staleness notice for one rejection."""
    return f"{APPLE_STALE_BANNER}:{rejected_at or 'unknown'}"


def _path() -> Path:
    return Path(settings.DATA_DIR) / _FILENAME


def _load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def is_dismissed(key: str) -> bool:
    """True once the user has dismissed the notice named `key` for good, or
    snoozed it and the snooze has not run out yet."""
    data = _load()
    dismissed = data.get("dismissed")
    if isinstance(dismissed, dict) and dismissed.get(key):
        return True
    snoozed = data.get("snoozed")
    until = snoozed.get(key) if isinstance(snoozed, dict) else None
    if not until:
        return False
    try:
        return datetime.fromisoformat(until) > datetime.now(UTC)
    except (TypeError, ValueError):
        return False


def snooze(key: str, days: int) -> None:
    """Hide the notice named `key` for `days` days; it comes back after."""
    with _lock:
        data = _load()
        snoozed = data.get("snoozed")
        if not isinstance(snoozed, dict):
            snoozed = {}
        snoozed[key] = (datetime.now(UTC) + timedelta(days=days)).isoformat()
        data["snoozed"] = snoozed
        _save(data)


def dismiss(key: str) -> None:
    """Record that the user dismissed the notice named `key` - for good."""
    with _lock:
        data = _load()
        dismissed = data.get("dismissed")
        if not isinstance(dismissed, dict):
            dismissed = {}
        dismissed[key] = True
        data["dismissed"] = dismissed
        _save(data)


# Bumped by every write from this process. The file's modification time
# alone is too coarse on some filesystems (Docker's overlay): two writes in
# one tick looked like none, and a reader kept a stale time zone.
_writes = 0


def stamp():
    """Changes whenever the file does (a write from this process, or a new
    modification time from another), so a reader can cache what it read."""
    return (_writes, _mtime())


def _mtime():
    try:
        return _path().stat().st_mtime_ns
    except OSError:
        return None


def time_zone() -> str | None:
    """The time zone the reader's browser last reported (aso/local_day.py)."""
    value = _load().get("time_zone")
    return value if isinstance(value, str) else None


def set_time_zone(name: str) -> None:
    with _lock:
        data = _load()
        if data.get("time_zone") != name:
            data["time_zone"] = name
            _save(data)


def _save(data: dict) -> None:
    global _writes
    _writes += 1
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        # Whole or not at all: a reader never sees half a file, even across processes.
        fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".ui_state.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(data, indent=2))
            os.replace(tmp, path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise
    except OSError as e:  # A failed write must never break a page render.
        logger.debug("Could not persist UI state: %s", e)
