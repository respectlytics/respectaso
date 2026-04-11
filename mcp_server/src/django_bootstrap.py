"""
Utilities for initializing Django inside MCP tool execution.

Why this exists:
- The Django app is normally initialized by `manage.py`, Gunicorn, or ASGI/WSGI.
- MCP tools run in a standalone Python process, so we must bootstrap Django
  manually before importing project modules like `aso.models` or `aso.services`.

This module provides an idempotent `bootstrap_django()` function that is safe
to call at the top of every tool.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path


# Repo root: <repo>/mcp_server/src/django_bootstrap.py -> parents[2] == <repo>
_REPO_ROOT = Path(__file__).resolve().parents[2]

# Guard to make setup idempotent across repeated tool calls.
_BOOTSTRAP_LOCK = threading.Lock()
_BOOTSTRAPPED = False


def _ensure_repo_on_path() -> None:
    """Ensure the repository root is importable (for `core`, `aso`, etc.)."""
    repo_root_str = str(_REPO_ROOT)
    if repo_root_str not in sys.path:
        sys.path.insert(0, repo_root_str)


def bootstrap_django(
    *,
    settings_module: str = "core.settings",
    data_dir: str | Path | None = None,
) -> Path:
    """
    Initialize Django for MCP tool execution (idempotent).

    Args:
        settings_module: Django settings module, defaulting to `core.settings`.
        data_dir: Optional explicit data directory path. If omitted, we use:
            1) Existing `DATA_DIR` env var (if already set), otherwise
            2) `RESPECTASO_DATA_DIR` env var (if provided), otherwise
            3) Django's own default from `core.settings`.

    Returns:
        The repository root path used for module import resolution.
    """
    global _BOOTSTRAPPED

    # Fast path: avoid lock overhead after first successful setup.
    if _BOOTSTRAPPED:
        return _REPO_ROOT

    with _BOOTSTRAP_LOCK:
        if _BOOTSTRAPPED:
            return _REPO_ROOT

        _ensure_repo_on_path()
        os.environ.setdefault("DJANGO_SETTINGS_MODULE", settings_module)

        if "DATA_DIR" not in os.environ:
            resolved_data_dir: Path | None = None

            if data_dir is not None:
                resolved_data_dir = Path(data_dir).expanduser()
            else:
                env_data_dir = os.environ.get("RESPECTASO_DATA_DIR")
                if env_data_dir:
                    resolved_data_dir = Path(env_data_dir).expanduser()

            if resolved_data_dir is not None:
                resolved_data_dir.mkdir(parents=True, exist_ok=True)
                os.environ["DATA_DIR"] = str(resolved_data_dir)

        import django

        django.setup()
        _BOOTSTRAPPED = True
        return _REPO_ROOT


def is_bootstrapped() -> bool:
    """Return whether Django has already been initialized by this helper."""
    return _BOOTSTRAPPED


__all__ = ["bootstrap_django", "is_bootstrapped"]
