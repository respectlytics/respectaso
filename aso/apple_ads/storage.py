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
    # ── Apple Ads Platform API v1 credentials (the private key lives in
    #    its own file, see aso.apple_ads.keys) ──────────────────────────
    "client_id": "",
    "team_id": "",
    "key_id": "",
    # Ad account chosen from GET /v1/acls during verification.
    "ad_account_id": "",
    "ad_account_name": "",
    "org_id": "",
    # True once verification (acls + a real popularity probe) succeeded.
    "tested_ok": False,
    # Set when Apple rejects the credentials (revoked key, bad ids);
    # cleared by a successful re-verification.
    "credentials_rejected": False,
    # ISO timestamp of the last rejection event - keys the dismissible
    # "Apple values stopped refreshing" notice shown under the internal
    # source, so each NEW rejection re-shows a previously dismissed notice.
    "credentials_rejected_at": "",
    # Explicit "I'll stay on the estimate" choice (hides the recommend
    # banner); reversible in Settings.
    "estimate_opt_out": False,
    # Set by the legacy migration for cookie-era users who had Apple
    # active; cleared when they verify the new connection once.
    "legacy_upgrade_pending": False,
    # Applied estimator version marker (see aso.popularity
    # ESTIMATOR_VERSION); < current triggers the one-time history
    # re-score at app start.
    "est_version": 1,
    # ── Weekly dataset sync state ────────────────────────────────────
    "last_sync_at": "",
    "last_sync_status": "",  # "" | completed | partial | rate_limited | error
    "last_sync_error": "",
    # Per-country ACTIVE week (ISO Sunday): the week serving lookups.
    # Only advanced after a week passes the ingest sanity checks.
    "active_weeks": {},
    # Per-country backfill cursor/progress:
    # {"us": {"cursor": "<ISO Sunday last ingested>", "done": bool}}
    "backfill": {},
    # Impression-share sync state (per-install, not per-app).
    "impression_share": {
        "last_sync_at": "",
        "last_week": "",
        "status": "",
        "error": "",
        "has_data": False,
    },
    # Coverage snapshot for the settings page.
    "coverage": {"terms": 0, "tracked_matched": 0, "tracked_total": 0, "week": ""},
    # Rolling log of request timestamps (ISO) for the 24h self-imposed cap.
    "request_log": [],
}

# Cookie-era keys (pre-2.22 embedded sign-in). Purged from settings.json
# by migrate_legacy_settings(); listed here only for that purge.
LEGACY_KEYS = ("cookies", "primary_app_id", "session_expired", "session_expired_at")

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
        for nested in ("coverage", "impression_share"):
            if isinstance(saved.get(nested), dict):
                block[nested] = {
                    **APPLE_ADS_DEFAULTS[nested],
                    **saved[nested],
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
        # Fresh installs default to the internal estimate (the unset state
        # is retired; the recommend banner nudges toward Apple instead).
        source = raw.get("popularity_source", SOURCE_INTERNAL)
        if source not in VALID_SOURCES:
            source = SOURCE_INTERNAL
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
    """True when the Apple source is fully usable (verified, not rejected)."""
    block = load_apple_settings()["apple_ads"]
    return bool(block["tested_ok"]) and not block["credentials_rejected"]


def migrate_legacy_settings() -> None:
    """One-time upgrade of cookie-era settings to the v1 connection model.

    Idempotent (runs at every app start, no-ops once clean):
    - Purges the embedded-sign-in cookie session and its state keys - the
      cookie mechanism is removed; v1 uses OAuth credentials.
    - A cookie-era install that had Apple verified gets
      legacy_upgrade_pending=True (drives the "reconnect once" banner)
      and tested_ok reset (the old test proved a session that no longer
      exists).
    - Retires the unset popularity_source: "" becomes "internal" (the
      new-install default; the recommend banner does the nudging now).
    """
    with _lock:
        raw = _read_raw()
        saved = raw.get("apple_ads")
        changed = False
        if isinstance(saved, dict) and any(k in saved for k in LEGACY_KEYS):
            if saved.get("tested_ok"):
                saved["legacy_upgrade_pending"] = True
                saved["tested_ok"] = False
            for key in LEGACY_KEYS:
                saved.pop(key, None)
            raw["apple_ads"] = saved
            changed = True
            logger.info(
                "Apple Ads settings migrated to the official API model "
                "(cookie session removed)."
            )
        # Only rewrite an EXPLICIT legacy unset value; an absent key
        # already defaults to internal on read (v1-era files untouched).
        if raw.get("popularity_source") == "":
            raw["popularity_source"] = SOURCE_INTERNAL
            changed = True
        if changed:
            _write_raw(raw)
            _cache["mtime"] = None
            _cache["data"] = None


def has_credentials() -> bool:
    """True when the three Apple API ids and the private key file exist."""
    from . import keys

    block = load_apple_settings()["apple_ads"]
    return bool(
        block["client_id"]
        and block["team_id"]
        and block["key_id"]
        and keys.has_private_key()
    )


def api_credentials() -> dict | None:
    """Assembled credentials dict for aso.apple_ads.api, or None."""
    from . import keys

    if not has_credentials():
        return None
    block = load_apple_settings()["apple_ads"]
    return {
        "client_id": block["client_id"],
        "team_id": block["team_id"],
        "key_id": block["key_id"],
        "private_key_pem": keys.load_private_key_pem(),
    }


def mark_credentials_rejected() -> None:
    """Record a credential rejection (called on AppleAdsAuthError)."""
    from django.utils import timezone

    save_apple_settings(apple_ads={
        "credentials_rejected": True,
        "credentials_rejected_at": timezone.now().isoformat(),
        "tested_ok": False,
    })
    logger.warning("Apple Ads API credentials were rejected by Apple.")
