"""A run's process, and whether it is still there (aso/run_owner.py).

The owner's lock is held by the operating system for as long as the process
lives, so these tests use a real second process: a running one holds its
lock, an ended one has released it, whatever the clock says.
"""

import subprocess
import sys
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

from aso import run_owner

HOLD = """
import fcntl, sys, time
handle = open(sys.argv[1], "w")
fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
print("holding", flush=True)
time.sleep(120)
"""


class OtherProcess:
    """A process that holds the lock file of ``owner`` until it is stopped."""

    def __init__(self, owner):
        self.owner = owner
        folder = Path(settings.DATA_DIR) / "run-owners"
        folder.mkdir(parents=True, exist_ok=True)
        self.proc = subprocess.Popen([sys.executable, "-c", HOLD, str(folder / f"{owner}.lock")],
                                     stdout=subprocess.PIPE, text=True)
        assert self.proc.stdout.readline().strip() == "holding"

    def end(self):
        """The process ends, as a closed AI assistant ends its MCP server."""
        self.proc.kill()
        self.proc.wait()


class RunOwnerTest(SimpleTestCase):
    def _fresh_token(self):
        """This process's token taken now, under this test's DATA_DIR. The
        process keeps its first token for life, and a test that overrides
        DATA_DIR may have been the one to take it, in a folder gone since: the
        suite flaked on that depending on which worker ran what (2026-10-08)."""
        kept = (run_owner._token, run_owner._handle)
        run_owner._token = run_owner._handle = None

        def restore():
            if run_owner._handle is not None:
                run_owner._handle.close()
            run_owner._token, run_owner._handle = kept

        self.addCleanup(restore)
        return run_owner.token()

    def test_this_process_holds_its_own_lock(self):
        token = self._fresh_token()
        self.assertEqual(run_owner.token(), token)
        self.assertTrue(token.startswith("app-"))
        self.assertTrue((Path(settings.DATA_DIR) / "run-owners" / f"{token}.lock").exists())
        self.assertTrue(run_owner.alive(token))

    def test_a_running_process_is_alive_and_an_ended_one_is_not(self):
        other = OtherProcess("mcp-424242-feedbeef")
        try:
            self.assertTrue(run_owner.alive(other.owner))
        finally:
            other.end()
        self.assertFalse(run_owner.alive(other.owner))

    def test_an_owner_without_a_file_has_ended(self):
        self.assertFalse(run_owner.alive("mcp-1-00000000"))

    def test_a_row_from_before_owners_is_never_released(self):
        self.assertTrue(run_owner.alive(""))

    def test_the_kind_of_process(self):
        self.assertEqual(run_owner.kind("mcp-123-abcd1234"), "mcp")
        self.assertEqual(run_owner.kind("app-123-abcd1234"), "app")
        self.assertEqual(run_owner.kind(""), "app")

    def test_the_folder_keeps_only_live_processes(self):
        folder = Path(settings.DATA_DIR) / "run-owners"
        other = OtherProcess("mcp-525252-cafef00d")
        ended = OtherProcess("mcp-626262-deadbeef")
        ended.end()
        try:
            run_owner._forget_ended(folder)
            self.assertTrue((folder / f"{other.owner}.lock").exists())
            self.assertFalse((folder / f"{ended.owner}.lock").exists())
        finally:
            other.end()
