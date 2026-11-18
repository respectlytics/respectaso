"""Adaptive rate limiter for iTunes / App Store API calls.

Single source of truth for inter-call pacing across every tool that reads
the App Store keyword by keyword: the three AI runners (Simulator,
Researcher, Competitor), keyword searches, opportunity scans, the
scheduled refresh and the Rival Tracker. One instance lives per run; each
keyword scoring iteration calls `wait()` before the network request and
then `record_success()` or `record_failure()` afterwards.

Every instance waits at one shared gate (`AppleGate`, below), across every
thread and process of the app (the MCP server is a second process on the
same data folder): one paced request at a time, and the next starts no
sooner than its run's current delay after the previous paced request of
ANY run ended, the pace one run always kept on its own. So several tools
at once never send more requests a minute than one tool alone. A run that
backs off (a failure, Retry-After) sets the shared pace for a while too,
since Apple's answer is about the app, not one run.

Design:
- Floor: never less than `BASE_DELAY` from the end of one request to the
  start of the next (the historical 3 s pace, about 18 requests a minute,
  under the 20 a minute Apple documents for its Search API).
- Ceiling: never sleep more than `MAX_DELAY` (Apple usually un-throttles inside this window).
- Growth: each consecutive failure multiplies the delay by `GROWTH_FACTOR`.
- Decay: after `DECAY_AFTER_OK` consecutive successes, the delay decays back toward base.
- `Retry-After` aware: if the server tells us how long to wait, we honour it (capped).
"""

from __future__ import annotations

import fcntl
import json
import logging
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)


# Module-level constants — runners import these so the historical
# `API_DELAY_SECONDS = 3` literal is no longer scattered across modules.
BASE_DELAY = 3.0          # floor — same as the previous fixed pace
MAX_DELAY = 20.0          # ceiling — most Apple throttles clear inside this window
GROWTH_FACTOR = 1.6       # multiplier on each consecutive failure
DECAY_AFTER_OK = 3        # decay back toward base after N consecutive ok calls
DECAY_FACTOR = 0.7        # how much to shrink the delay on decay
SLOWED_DOWN_RATIO = 1.5   # current_delay > BASE_DELAY * this → "we're slowed down"

# Failure thresholds used by the runners to decide UI banner state.
PAUSED_AFTER_FAILURES = 5     # show "we're being throttled" banner
ABORT_AFTER_FAILURES = 10     # stop the loop with a partial-result message
ABORT_FAILURE_RATE = 0.5      # OR ≥50% of attempted keywords failed → abort

# How long a single keyword's network work is allowed to take before the
# runner gives up on it and moves on. Bounds the worst-case hang.
KEYWORD_DEADLINE_SECONDS = 90.0


# A shared back-off is honoured this long after the run that set it last
# reported, so a finished run's back-off never slows tomorrow's first run.
SHARED_BACKOFF_SECONDS = 60.0
# A slot further ahead than this is a clock that jumped, not a queue.
MAX_AHEAD_SECONDS = 300.0
# A request whose end was never reported (a process that died mid-request)
# stops holding the gate after this long: longer than a search with its
# fallback and retries can take.
OPEN_REQUEST_SECONDS = 120.0
# How often a request waits to see whether another one has ended.
TURN_POLL_SECONDS = 0.05


class AppleGate:
    """The one queue every paced App Store request waits in.

    State lives in ``DATA_DIR/apple_pace.json`` under an exclusive file
    lock, so threads and processes (the app, the MCP server) share it:
    ``last`` is when the latest request ended (or the start reserved for
    the next one), ``open`` the request under way and whose it is, and
    ``delay``/``noted`` the latest pace a run reported. A request waits
    while another one is under way, then reserves the start
    ``max(now, last + interval)`` and sleeps until it, where ``interval``
    is the larger of its run's own delay and a shared back-off noted in the
    last SHARED_BACKOFF_SECONDS. ``ended()`` (aso/day_reads.py calls it
    after every search) records the end, from which the next pace counts.
    """

    FILE_NAME = "apple_pace.json"

    def __init__(self):
        self._lock = threading.Lock()
        self._memory: dict = {}     # the state where there is no data folder (_path)
        # A callable giving the data folder, set by the app at start (aso/apps.py), so this file
        # imports no Django: respectaso.com runs it unedited (vendor/respectaso_engine) and leaves
        # it unset, so there the gate paces the requests of its own process, as before.
        self.folder = None

    def _path(self):
        """The shared file, or None where no data folder was given."""
        folder = self.folder() if self.folder else None
        return Path(folder) / self.FILE_NAME if folder else None

    @staticmethod
    def _me() -> str:
        import os

        return f"{os.getpid()}:{threading.get_ident()}"

    def _update(self, change):
        """Run ``change(state, now) -> result`` under both locks and save."""
        with self._lock:
            path = self._path()
            if path is None:
                return change(self._memory, time.time())
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, "a+", encoding="utf-8") as handle:
                fcntl.flock(handle, fcntl.LOCK_EX)
                try:
                    handle.seek(0)
                    try:
                        state = json.loads(handle.read() or "{}")
                    except ValueError:
                        state = {}
                    result = change(state, time.time())
                    handle.seek(0)
                    handle.truncate()
                    handle.write(json.dumps(state))
                    handle.flush()
                finally:
                    fcntl.flock(handle, fcntl.LOCK_UN)
        return result

    def reserve(self, interval: float) -> float | None:
        """Reserve the next start for a request paced at ``interval`` seconds
        and return how long to sleep until it, or None while another
        caller's request is still under way."""
        me = self._me()

        def change(state, now):
            last = float(state.get("last", 0))
            under_way = state.get("open")
            if under_way:
                if under_way.get("owner") != me and now - float(under_way.get("slot", 0)) < OPEN_REQUEST_SECONDS:
                    return None
                # This caller's own earlier request (it is asking again, so
                # that one is over), or one whose end never came: either
                # ended by now at the latest.
                last = now
            pace = interval
            if now - float(state.get("noted", 0)) <= SHARED_BACKOFF_SECONDS:
                pace = max(pace, float(state.get("delay", 0)))
            if last > now + MAX_AHEAD_SECONDS:
                last = now
            slot = max(now, last + pace) if last else now
            state["last"] = slot
            state["open"] = {"owner": me, "slot": slot}
            return slot - now
        return self._update(change)

    def wait(self, interval: float) -> None:
        """Wait for this caller's turn: no request under way, and ``interval``
        since the last one ended."""
        idle = threading.Event()
        while (delay := self.reserve(interval)) is None:
            idle.wait(TURN_POLL_SECONDS)    # not time.sleep: a test's faked sleep must not spin here
        if delay > 0:
            time.sleep(delay)

    def ended(self) -> None:
        """This caller's request is over: the next pace counts from now."""
        me = self._me()

        def change(state, now):
            under_way = state.get("open")
            if under_way and under_way.get("owner") == me:
                state["open"] = None
                state["last"] = now
        self._update(change)

    def note(self, delay: float, *, failed: bool = False) -> None:
        """A run's current pace after a success or a failure. A failure also
        counts as the latest request, so the back-off runs from it."""
        def change(state, now):
            state["delay"] = delay
            state["noted"] = now
            if failed:
                state["last"] = max(float(state.get("last", 0)), now)
        self._update(change)

    def reset(self) -> None:
        """Forget the queue and any back-off (the test runner, between tests)."""
        with self._lock:
            self._memory.clear()
            path = self._path()
            if path is not None:
                path.unlink(missing_ok=True)


apple_gate = AppleGate()


class AdaptiveITunesRateLimiter:
    """Adaptive backoff between iTunes API calls.

    Usage in a runner loop::

        limiter = AdaptiveITunesRateLimiter()
        for kw in keywords:
            try:
                read = day_reads.get_or_fetch(kw, country, itunes_service=itunes)
                limiter.record_success()
            except ITunesRateLimited as exc:
                limiter.record_failure(retry_after=exc.retry_after)
            except (RequestException, SearchAPIUnavailableError):
                limiter.record_failure()
            limiter.wait()   # sleeps current_delay seconds before the next call
    """

    def __init__(
        self,
        base_delay: float = BASE_DELAY,
        max_delay: float = MAX_DELAY,
        growth_factor: float = GROWTH_FACTOR,
        decay_after_ok: int = DECAY_AFTER_OK,
        decay_factor: float = DECAY_FACTOR,
    ) -> None:
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.growth_factor = growth_factor
        self.decay_after_ok = decay_after_ok
        self.decay_factor = decay_factor
        self.current_delay = base_delay
        self._consecutive_failures = 0
        self._consecutive_successes = 0

    # ----- Pacing ----------------------------------------------------------

    def wait(self) -> None:
        """Wait for this request's turn at the shared gate: no other paced
        request under way, and `current_delay` since the last one ended
        (none when the App Store has not been asked for that long)."""
        if self.current_delay > 0:
            apple_gate.wait(self.current_delay)

    def back_off(self) -> None:
        """Sleep `current_delay` before asking again for the same thing (one
        request's retries, such as a Lookup that failed): a pause after a
        failure, not a turn in the queue of keyword reads."""
        if self.current_delay > 0:
            time.sleep(self.current_delay)

    # ----- State updates ---------------------------------------------------

    def record_success(self) -> None:
        """A network call returned cleanly. Decay back toward the floor after
        N consecutive successes."""
        self._consecutive_failures = 0
        self._consecutive_successes += 1
        if (
            self._consecutive_successes >= self.decay_after_ok
            and self.current_delay > self.base_delay
        ):
            self.current_delay = max(
                self.base_delay, self.current_delay * self.decay_factor
            )
            self._consecutive_successes = 0
            apple_gate.note(self.current_delay)

    def record_failure(self, retry_after: float | None = None) -> None:
        """A network call failed (timeout / 429 / 5xx / SSR-also-failed).
        If the server provided a `Retry-After` value, honour it (capped at
        `max_delay`); otherwise multiply the current delay by `growth_factor`.
        """
        self._consecutive_failures += 1
        self._consecutive_successes = 0
        if retry_after is not None and retry_after > 0:
            # Server told us how long to wait. Take whichever is larger between
            # the server's hint and our current pace, capped at max_delay.
            self.current_delay = min(
                self.max_delay,
                max(self.current_delay, float(retry_after)),
            )
        else:
            self.current_delay = min(
                self.max_delay,
                self.current_delay * self.growth_factor,
            )
        apple_gate.note(self.current_delay, failed=True)
        logger.info(
            "iTunes throttle: failure #%d, current_delay now %.1fs",
            self._consecutive_failures,
            self.current_delay,
        )

    # ----- Inspection ------------------------------------------------------

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def is_slowed_down(self) -> bool:
        """True when the current delay has grown noticeably above base.
        Runners check this to decide whether to surface the slowdown to the UI.
        """
        return self.current_delay > self.base_delay * SLOWED_DOWN_RATIO


def classify_throttle_state(
    limiter: AdaptiveITunesRateLimiter,
    *,
    attempted: int,
    failed: int,
) -> str:
    """Classify the current run state for the UI progress banner.

    Returns one of:
      "normal"       — no slowdown, no failures
      "slowed_down"  — pacing is elevated above base
      "paused"       — too many consecutive failures, banner advised
      "aborted"      — run should stop early with partial results
    """
    if limiter.consecutive_failures >= ABORT_AFTER_FAILURES:
        return "aborted"
    if attempted > 0 and failed / max(attempted, 1) >= ABORT_FAILURE_RATE and failed >= ABORT_AFTER_FAILURES // 2:
        return "aborted"
    if limiter.consecutive_failures >= PAUSED_AFTER_FAILURES:
        return "paused"
    if limiter.is_slowed_down:
        return "slowed_down"
    return "normal"


# ---- what a person reads while Apple slows down --------------------------------
#
# Why they wait and that nothing needs doing, never the pace, the delay or how
# many requests failed (the owner's rule: screens show value, not mechanics,
# aso/copy_rules.mechanics_in). One wording for the keyword searches, the
# country scans and the three AI tabs, whose banner shows the same sentence.
WAIT_MESSAGES = {
    "slowed_down": "Apple's App Store is answering slowly. It carries on by itself.",
    "paused": "Apple's App Store is not answering right now. This carries on by itself once it does.",
    "aborted": ("Apple's App Store stopped answering, so this run finishes with the keywords scored so far. "
                "Run it again in a few minutes for the full picture."),
    "stopped": "Apple's App Store is still not answering. Wait a few minutes, then press Resume.",
}


def wait_message(state: str, cooldown_seconds: float = 0) -> str:
    """The sentence for a throttle state, or "" for a normal one. A queued
    keyword search or country scan does not end when Apple keeps refusing: it
    cools down and carries on, so it passes how long it waits, and its
    "aborted" says when it carries on instead of that the run is finishing."""
    if state == "aborted" and cooldown_seconds:
        from .opportunity_scans import duration_text

        return f"Apple's App Store is busy. This carries on by itself in {duration_text(cooldown_seconds)}."
    return WAIT_MESSAGES.get(state, "")

