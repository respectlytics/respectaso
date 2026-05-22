"""Adaptive rate limiter for iTunes / App Store API calls.

Single source of truth for inter-call pacing across all three AI runners
(Simulator, Researcher, Competitor). One instance lives per run; each
keyword scoring iteration calls `wait()` before the network request and
then `record_success()` or `record_failure()` afterwards.

Design:
- Floor: never sleep less than `BASE_DELAY` (matches the historical 3s pace).
- Ceiling: never sleep more than `MAX_DELAY` (Apple usually un-throttles inside this window).
- Growth: each consecutive failure multiplies the delay by `GROWTH_FACTOR`.
- Decay: after `DECAY_AFTER_OK` consecutive successes, the delay decays back toward base.
- `Retry-After` aware: if the server tells us how long to wait, we honour it (capped).
"""

from __future__ import annotations

import logging
import time

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


class AdaptiveITunesRateLimiter:
    """Adaptive backoff between iTunes API calls.

    Usage in a runner loop::

        limiter = AdaptiveITunesRateLimiter()
        for kw in keywords:
            try:
                result = itunes.search_apps(kw, ...)
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
        """Sleep for `current_delay` seconds. Cheap when current_delay == base."""
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
