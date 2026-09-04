"""Shared test helpers for the aso suite (re-exported by aso_pro.tests.helpers)."""


class _SyncThread:
    """Runs the target inline on start() so background work is deterministic.

    Stands in for ``threading.Thread`` wherever a test needs the worker body to
    actually execute (the run queue's dispatcher, the Local AI test job). The
    signature mirrors the keyword arguments those call sites pass.
    """

    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self._target = target
        self._args = args
        self._kwargs = kwargs or {}
        self.daemon = daemon
        self.name = name

    def start(self):
        if self._target:
            self._target(*self._args, **self._kwargs)
