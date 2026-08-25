"""Per-install UI state that must survive a restart (dismissed notices).

Server-side by design: the desktop edition runs inside pywebview's WebKit
view, where `localStorage` is off-limits (desktop-compat.instructions.md),
and the state has to behave identically in the native, Docker and browser
editions. Kept in its own small JSON file rather than in settings.json,
which holds secrets (API keys, Apple credentials) and is written by two
other modules - a dismissal is not worth touching that file for.

Ships in the free-tier `aso` app, so it must not import from aso_pro or
licensing.
"""

import json
import logging
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

_FILENAME = "ui_state.json"

# Dismissible notices, by key. One entry per notice so a future one does
# not need a new file.
RESPECTLYTICS_BANNER = "respectlytics_banner"


def _path() -> Path:
    return Path(settings.DATA_DIR) / _FILENAME


def _load() -> dict:
    try:
        data = json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def is_dismissed(key: str) -> bool:
    """True once the user has dismissed the notice named `key`."""
    return bool(_load().get("dismissed", {}).get(key))


def dismiss(key: str) -> None:
    """Record that the user dismissed the notice named `key` - for good."""
    data = _load()
    dismissed = data.get("dismissed")
    if not isinstance(dismissed, dict):
        dismissed = {}
    dismissed[key] = True
    data["dismissed"] = dismissed
    try:
        path = _path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as e:  # A failed write must never break a page render.
        logger.debug("Could not persist UI state: %s", e)
