"""Settings storage for the Apple Ads popularity feature.

Reads and writes ONLY the keys this feature owns ("popularity_source" and
"apple_ads") inside the shared DATA_DIR/settings.json. The file is shared
with aso_pro.settings_storage (LLM configuration); both modules preserve
each other's keys by doing read-modify-write of the full JSON document.

This module lives in the free-tier `aso` app (it must ship in the public
edition, which has no aso_pro), so it cannot import from aso_pro.

File permissions: 600 - the file holds Apple session cookies (secrets).
"""

import json
import logging
import os
import stat
import threading
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

SOURCE_UNSET = ""
SOURCE_INTERNAL = "internal"
SOURCE_APPLE = "apple"
VALID_SOURCES = (SOURCE_INTERNAL, SOURCE_APPLE)

APPLE_ADS_DEFAULTS = {
    # Apple web-session cookies captured by the embedded sign-in window.
    # List of {"name", "value", "domain", "path", "expires"} dicts.
    "cookies": [],
    # Any app id the user's Apple Ads account can access; request context
    # for the popularity endpoint.
    "primary_app_id": "",
    # True once a sign-in + one-term test fetch succeeded (test-gated source).
    "tested_ok": False,
    # Set by the sync/client on auth failures; cleared by a successful sign-in.
    "session_expired": False,
    # ISO timestamp of the last expiry event - keys the dismissible
    # "Apple values stopped refreshing" notice shown under the internal
    # source, so each NEW expiry re-shows a previously dismissed notice.
    "session_expired_at": "",
    "last_sync_at": "",
    "last_sync_status": "",  # "" | completed | partial | rate_limited | error
    "last_sync_error": "",
    # Coverage snapshot for the settings page.
    "coverage": {"terms": 0, "tracked_matched": 0, "tracked_total": 0},
    # Rolling log of request timestamps (ISO) for the 24h self-imposed cap.
    "request_log": [],
}

_lock = threading.Lock()
_cache = {"mtime": None, "data": None}


def _settings_path() -> Path:
    return settings.DATA_DIR / "settings.json"


def _read_raw() -> dict:
    """Read the full settings.json (all owners' keys), tolerating absence."""
    path = _settings_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as e:
        logger.warning("Failed to read settings file: %s", e)
        return {}


def _write_raw(data: dict) -> None:
    """Write the full settings.json with 600 permissions."""
    path = _settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)


def _merged_apple_block(raw: dict) -> dict:
    block = dict(APPLE_ADS_DEFAULTS)
    saved = raw.get("apple_ads")
    if isinstance(saved, dict):
        block.update(saved)
        if isinstance(saved.get("coverage"), dict):
            block["coverage"] = {
                **APPLE_ADS_DEFAULTS["coverage"],
                **saved["coverage"],
            }
    return block


def load_apple_settings() -> dict:
    """Return {"popularity_source": str, "apple_ads": dict} with defaults merged.

    Cached on file mtime so the per-request context processor and the
    per-keyword resolution path never pay repeated disk reads.
    """
    path = _settings_path()
    try:
        mtime = path.stat().st_mtime_ns
    except OSError:
        mtime = None
    with _lock:
        if _cache["data"] is not None and _cache["mtime"] == mtime:
            return _cache["data"]
        raw = _read_raw()
        source = raw.get("popularity_source", SOURCE_UNSET)
        if source not in VALID_SOURCES:
            source = SOURCE_UNSET
        data = {
            "popularity_source": source,
            "apple_ads": _merged_apple_block(raw),
        }
        _cache["mtime"] = mtime
        _cache["data"] = data
        return data


def save_apple_settings(*, popularity_source=None, apple_ads=None) -> None:
    """Update only this feature's keys, preserving everything else in the file.

    apple_ads is merged key-by-key over the stored block, so callers can
    update e.g. only sync status without touching cookies.
    """
    with _lock:
        raw = _read_raw()
        if popularity_source is not None:
            if popularity_source not in VALID_SOURCES + (SOURCE_UNSET,):
                raise ValueError(f"Invalid popularity source: {popularity_source!r}")
            raw["popularity_source"] = popularity_source
        if apple_ads is not None:
            block = _merged_apple_block(raw)
            block.update(apple_ads)
            raw["apple_ads"] = block
        _write_raw(raw)
        _cache["mtime"] = None
        _cache["data"] = None


def reset_cache() -> None:
    """Drop the mtime cache - used by tests that swap DATA_DIR."""
    with _lock:
        _cache["mtime"] = None
        _cache["data"] = None


def get_popularity_source() -> str:
    """Return the user's chosen source: "", "internal", or "apple"."""
    return load_apple_settings()["popularity_source"]


def apple_source_ready() -> bool:
    """True when the Apple source is fully usable (signed in and test-passed)."""
    block = load_apple_settings()["apple_ads"]
    return bool(block["tested_ok"]) and not block["session_expired"]
