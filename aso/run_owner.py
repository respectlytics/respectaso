"""Which process works on a run, and whether that process is still there.

Two kinds of process share the run lane on one database: the app (the Mac
app, or the Docker server) and the MCP server an AI assistant such as Claude
Desktop starts. A run's row says "running" while a thread of one of them
works on it. When that process ends mid-run (the assistant was closed, a
crash, a quit), nothing is left to finish the row, and it held the lane for
everyone until RespectASO restarted.

Each process that starts a run writes its owner token on the row
(``run_owner``) and holds an exclusive lock on
``DATA_DIR/run-owners/<token>.lock`` for as long as it lives. The operating
system releases that lock when the process ends, however it ends, so a row
whose owner's lock can be taken belongs to a process that is gone
(aso.run_queue.release_dead_runs). No clock is involved: a Mac asleep for an
hour never makes a live run look dead, and a slow run is never taken from
the process still working on it.
"""

from __future__ import annotations

import fcntl
import os
import threading
import uuid
from pathlib import Path

from django.conf import settings

_lock = threading.Lock()
_token: str | None = None
_handle = None      # the open, locked file, kept for the life of the process


def _folder() -> Path:
    return Path(settings.DATA_DIR) / "run-owners"


def _process_kind() -> str:
    """"mcp" in the MCP server (aso_pro/mcp/bootstrap.py says so), else "app"."""
    return "mcp" if os.environ.get("RESPECTASO_PROCESS") == "mcp" else "app"


def kind(owner: str) -> str:
    """Which kind of process an owner token names: "mcp" or "app"."""
    return "mcp" if (owner or "").startswith("mcp-") else "app"


def token() -> str:
    """This process's owner token, its lock taken on first use.

    The file is locked under a temporary name and only then renamed, so a
    process tidying the folder never takes a lock file that is not held yet
    for one whose owner has gone.
    """
    global _token, _handle
    with _lock:
        if _token is None:
            folder = _folder()
            folder.mkdir(parents=True, exist_ok=True)
            candidate = f"{_process_kind()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"
            temporary = folder / f"{candidate}.starting"
            handle = open(temporary, "w")  # noqa: SIM115 (it stays open, and locked, for the life of the process)
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            os.replace(temporary, folder / f"{candidate}.lock")
            _token, _handle = candidate, handle
            _forget_ended(folder)
        return _token


def alive(owner: str) -> bool:
    """Whether the process named by ``owner`` still runs.

    True for this process, and for an empty owner (a row from before owners
    were recorded): nothing is released that cannot be proven ended. False
    when the owner's lock can be taken, or its file is gone.
    """
    if not owner or owner == _token:
        return True
    try:
        fd = os.open(_folder() / f"{owner}.lock", os.O_RDWR)
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    except OSError:
        return True
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)


def _forget_ended(folder: Path) -> None:
    """Remove the lock files of processes that have ended, so the folder
    holds about one file per running process. A row that still names a
    removed owner reads as ended (alive() finds no file)."""
    for path in folder.glob("*.lock"):
        if path.stem != _token and not alive(path.stem):
            try:
                path.unlink()
            except OSError:
                pass
