"""A JSON file in the data folder, written whole or not at all.

settings.json is read and written by several owners at once: the AI settings (aso_pro/settings_storage.py),
the Apple Ads integration (aso/apple_ads/storage.py), the scheduler's threads and the MCP server's process.
Each writer used to truncate the file and then write it, so a reader in between found an empty file,
fell back to the defaults, and the next save wrote those defaults back: a Test model press while
another thread saved settings wiped every saved API key and the chosen provider (found before the
3.2.2 release, 2026-11-22, in the Docker edition).

``write_json`` writes a temporary file beside the target and renames it over the target, so a reader,
in this process or another, sees the old file or the new one, never half of one. ``SETTINGS_LOCK`` is
held by every read, merge and write of settings.json in this process, so two owners never write over
each other's keys. aso/tests/test_settings_file_writes.py proves both.
"""

import contextlib
import json
import os
import tempfile
import threading
from pathlib import Path

# Every read-merge-write of settings.json in this process, whichever owner makes it.
SETTINGS_LOCK = threading.RLock()


def write_json(path: Path, data) -> None:
    """Write ``data`` to ``path`` as indented JSON, readable by the owner only (600).

    The bytes reach the disk before the rename, so a power cut leaves the old file or the new
    one. Raises OSError when the folder cannot be written; the temporary file never stays behind.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temporary)
        raise
