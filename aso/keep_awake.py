"""Keep the Mac from idle sleep while a run works (the run queue's worker).

A store draft runs the Simulator once per language, and on the owner's Mac
one Simulator run took 8 to 22 minutes (2026-11-14), so a draft of nine
languages outlasts the Mac's idle sleep. While at least one run works,
macOS's own ``caffeinate -i`` holds an idle-sleep assertion; it is tied to
this process (``-w``), so it ends with RespectASO even after a crash. A lid
that closes still sleeps the Mac, as it should. Anywhere but macOS (the
Docker edition) this does nothing.
"""

from __future__ import annotations

import contextlib
import logging
import os
import subprocess
import sys
import threading

logger = logging.getLogger(__name__)

CAFFEINATE = "/usr/bin/caffeinate"

_lock = threading.Lock()
_holders = 0
_process: subprocess.Popen | None = None


def _available() -> bool:
    return sys.platform == "darwin" and os.path.exists(CAFFEINATE) and not os.environ.get("RESPECTASO_NO_KEEP_AWAKE")


@contextlib.contextmanager
def awake():
    """Hold the Mac awake for the ``with`` block; nested and parallel holds share one assertion."""
    _take()
    try:
        yield
    finally:
        _give()


def _take() -> None:
    global _holders, _process
    with _lock:
        _holders += 1
        if _holders == 1 and _available():
            try:
                _process = subprocess.Popen([CAFFEINATE, "-i", "-w", str(os.getpid())],
                                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError as exc:      # no caffeinate: the run goes on, the Mac may sleep
                logger.info("Could not keep the Mac awake: %s", exc)
                _process = None


def _give() -> None:
    global _holders, _process
    with _lock:
        _holders = max(0, _holders - 1)
        if _holders == 0 and _process is not None:
            with contextlib.suppress(OSError):
                _process.terminate()
            _process = None


def holding() -> bool:
    """True while a run holds the Mac awake (for tests)."""
    return _holders > 0
