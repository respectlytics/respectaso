"""One run at a time. The database is the queue: a row with status "queued"
waits, "running" executes. This module claims the first queued row when the
lane is free and runs it on a daemon thread.

The lane is global across every long-running job that talks to Apple: the
three Pro AI features (Niche Researcher, Competitor Analyzer, ASO Score
Simulator) and keyword searches from the Keyword Research tab. They all hit
Apple's API through their own rate limiter, so two concurrent runs would
double the request rate with no shared budget. Serializing them keeps every
run inside Apple's limits and keeps the process-global Local AI cancel flag
unambiguous. The Free build has only keyword searches; the lane exists there
too and only ever holds one of them.

The queue is ordered by ``queue_rank`` (smallest first, NULL = "append") and
the user can reorder it: move a run up or down, put it next, or run it now.
"Run now" pauses the executing run when its feature allows that
(``Feature.can_yield`` - keyword searches do, AI runs do not) and the paused
run comes back first once the run that went ahead of it is done.

Features register themselves at import time: ``aso.search_jobs`` (keyword
search, both editions) and ``aso_pro.views`` (the AI features, Pro build).
This module never imports either at module level - they import this one.
"""

import logging
import os
import threading
from dataclasses import dataclass
from typing import Callable

from django.apps import apps as django_apps
from django.utils import timezone

logger = logging.getLogger(__name__)

INTERRUPTED_MESSAGE = ("Interrupted - RespectASO was closed while this run was "
                       "in progress. Retry to run it again.")
YIELDED_MESSAGE = "Paused for a moment while another run goes first"


@dataclass(frozen=True)
class Feature:
    """One queue participant: a tab and the model rows it owns."""

    key: str                 # "researcher" | "competitor" | "simulator" | "keyword_search"
    label: str               # "AI Niche Researcher" | ... | "Keyword Research"
    model: type              # AIResearchSession | SimulatorSession | KeywordSearchJob
    filter_kwargs: dict      # {"session_type": "keyword"} | ... | {}
    execute: Callable        # execute(pk) -> None; sets the terminal status itself
    describe: Callable       # describe(row) -> {"label", "detail", "country", "is_refinement"}
    progress: Callable | None = None   # progress(row) -> the dict the feature's page polls
    open_url: str = "/"                # where the feature lives ("Open" links)
    # Called by resume_after_startup() with the queryset of rows left
    # "running" by a crash or a quit. None = mark them failed (AI runs).
    # Keyword search re-queues them at the front instead.
    interrupted: Callable | None = None
    # Called by remove_queued() / clear_queued() with a queued row. None =
    # delete the row. Returns True when the row left the queue.
    on_remove: Callable | None = None
    # The executing run may be paused so another run goes first, and the
    # model carries auto_resume / yielded_for_* columns for that.
    can_yield: bool = False


_features: dict = {}
_registrars_loaded = False
_lock = threading.Lock()      # guards the claim, the ranks and _active
_active: set = set()          # (feature.key, pk) whose worker thread is alive IN THIS PROCESS

# Callables that answer "what else is using the Apple budget right now?" with
# a short label ("the daily ranking refresh") or None. While any of them
# answers, the lane does not claim a new run. The scheduler registers one.
busy_probes: list = []


def register(feature: Feature) -> None:
    """Register a queue participant. Called by the feature modules at import
    time. Must NOT call ``_ensure_features()``: it runs while that module is
    still being imported."""
    _features[feature.key] = feature


def _ensure_features():
    """Import the modules that register the features, once (e.g. when the
    queue is kicked from a startup hook before any view was imported)."""
    global _registrars_loaded
    if _registrars_loaded:
        return
    _registrars_loaded = True
    from . import search_jobs  # noqa: F401  (keyword search, both editions)
    if django_apps.is_installed("aso_pro"):
        from aso_pro import views  # noqa: F401  (the three AI features)


def features():
    """All registered features, in registration order."""
    _ensure_features()
    return list(_features.values())


def get_feature(key):
    """The registered feature for ``key``, or None."""
    _ensure_features()
    return _features.get(key)


# ---------------------------------------------------------------------------
# Reading the lane
# ---------------------------------------------------------------------------

def running_run():
    """``(feature, row)`` of the executing run, or None.

    Newest ``created_at`` wins when several rows are running - that can happen
    when an MCP-started run (its own process, no queue) overlaps a stale row.
    """
    _ensure_features()
    best = None
    for feature in _features.values():
        row = (feature.model.objects.filter(status="running", **feature.filter_kwargs)
               .order_by("-created_at", "-pk").first())
        if row is None:
            continue
        if best is None or (row.created_at, row.pk) > (best[1].created_at, best[1].pk):
            best = (feature, row)
    return best


def _order_key(row):
    """Queue order: rank first (unranked rows sort last), then creation."""
    rank = row.queue_rank if row.queue_rank is not None else float("inf")
    return (rank, row.created_at, row.pk)


def queued_runs():
    """Every queued run of every feature, in execution order."""
    _ensure_features()
    rows = []
    for feature in _features.values():
        for row in feature.model.objects.filter(status="queued", **feature.filter_kwargs):
            rows.append((feature, row))
    rows.sort(key=lambda pair: _order_key(pair[1]))
    return rows


def busy_reason():
    """Label of whatever holds the Apple budget outside the queue (a daily
    or manual ranking refresh), or None."""
    for probe in busy_probes:
        try:
            label = probe()
        except Exception:  # a probe must never break the lane
            logger.exception("Busy probe failed")
            continue
        if label:
            return label
    return None


def lane_state():
    """"running" while a run executes, "winding_down" while a cancelled run's
    thread is still finishing its current step, else "idle"."""
    if running_run() is not None:
        return "running"
    with _lock:
        winding = bool(_active)
    return "winding_down" if winding else "idle"


def queue_position(row):
    """1-based position of ``row`` in the queue, or None when it is not
    queued. Matched by (model, pk), never by object identity."""
    if row is None or row.status != "queued":
        return None
    key = (type(row), row.pk)
    for index, (_feature, queued) in enumerate(queued_runs(), start=1):
        if (type(queued), queued.pk) == key:
            return index
    return None


# ---------------------------------------------------------------------------
# Editing the queue
# ---------------------------------------------------------------------------

def _leave_queue(feature, row):
    """Take one queued row out of the queue the way its feature wants."""
    if feature.on_remove is not None:
        return bool(feature.on_remove(row))
    deleted, _ = feature.model.objects.filter(pk=row.pk, status="queued").delete()
    return bool(deleted)


def remove_queued(feature_key, pk):
    """Take one queued run out of the queue. True when it left.

    Never touches a running row - the caller answers 400 in that case. A
    feature's ``on_remove`` may pause instead of delete (a keyword search
    that already has progress keeps it).
    """
    _ensure_features()
    feature = _features.get(feature_key)
    if feature is None:
        return False
    row = feature.model.objects.filter(pk=pk, status="queued", **feature.filter_kwargs).first()
    if row is None:
        return False
    return _leave_queue(feature, row)


def clear_queued():
    """Take every queued run of every feature out of the queue. Returns how
    many left."""
    _ensure_features()
    removed = 0
    for feature in _features.values():
        for row in list(feature.model.objects.filter(status="queued", **feature.filter_kwargs)):
            if _leave_queue(feature, row):
                removed += 1
    return removed


def _renumber(rows):
    """Write ranks 1..n over ``rows`` (a list of (feature, row))."""
    for position, (feature, row) in enumerate(rows, start=1):
        feature.model.objects.filter(pk=row.pk, status="queued").update(queue_rank=position)


def _rank_unranked():
    """Give every queued row without a rank the next rank after the current
    maximum, in creation order. Called under ``_lock`` at the start of a kick,
    so every launch path can create rows with ``queue_rank=None``."""
    rows = queued_runs()
    ranked = [r for _f, r in rows if r.queue_rank is not None]
    next_rank = max((r.queue_rank for r in ranked), default=0) + 1
    for feature, row in rows:
        if row.queue_rank is None:
            feature.model.objects.filter(pk=row.pk, status="queued").update(queue_rank=next_rank)
            next_rank += 1


def _move_locked(feature_key, pk, direction):
    """``move()`` with ``_lock`` already held. Returns the new 1-based
    position, or None when the row is not waiting in the queue."""
    rows = queued_runs()
    index = next((i for i, (f, r) in enumerate(rows)
                  if f.key == feature_key and r.pk == pk), None)
    if index is None:
        return None
    if direction == "up" and index > 0:
        rows[index - 1], rows[index] = rows[index], rows[index - 1]
        index -= 1
    elif direction == "down" and index < len(rows) - 1:
        rows[index + 1], rows[index] = rows[index], rows[index + 1]
        index += 1
    elif direction == "top" and index > 0:
        rows.insert(0, rows.pop(index))
        index = 0
    _renumber(rows)
    return index + 1


def move(feature_key, pk, direction):
    """Reorder the queue: ``direction`` is "up", "down" or "top". Returns the
    row's new 1-based position, or None when it is not waiting in the queue
    (the view answers 400)."""
    _ensure_features()
    if direction not in ("up", "down", "top"):
        return None
    with _lock:
        return _move_locked(feature_key, pk, direction)


def _yield_running(for_feature, for_row):
    """Pause the executing run so ``for_row`` can go first. True when a run
    stepped aside; False when nothing runs or the executing run cannot
    yield (an AI run), in which case the target simply waits at position 1."""
    running = running_run()
    if running is None:
        return False
    run_feature, run_row = running
    if not run_feature.can_yield:
        return False
    if (run_feature.key, run_row.pk) == (for_feature.key, for_row.pk):
        return False
    described = for_feature.describe(for_row)
    label = described["label"]
    if described.get("country"):
        label = f"{label} ({described['country']})"
    flipped = run_feature.model.objects.filter(pk=run_row.pk, status="running").update(
        status="paused", auto_resume=True,
        yielded_for_feature=for_feature.key, yielded_for_id=for_row.pk,
        yielded_for_label=label[:220], progress_message=YIELDED_MESSAGE,
    )
    return bool(flipped)


def run_now(feature_key, pk):
    """Put a queued run first and, when the executing run can yield, pause
    that one so this run starts at once. Returns ``{"position": 1,
    "yielded": bool}``, or None when the row is not waiting in the queue."""
    _ensure_features()
    feature = _features.get(feature_key)
    if feature is None:
        return None
    with _lock:
        row = feature.model.objects.filter(pk=pk, status="queued", **feature.filter_kwargs).first()
        if row is None:
            return None
        if _move_locked(feature_key, pk, "top") is None:
            return None
        yielded = _yield_running(feature, row)
    kick()   # the yielded worker's own kick() also starts the target when it exits
    return {"position": 1, "yielded": yielded}


def _run_pending(feature_key, pk):
    """Whether the run another run stepped aside for is still ahead of it:
    queued, running, or itself paused for a third run (a chain)."""
    feature = _features.get(feature_key)
    if feature is None or pk is None:
        return False
    row = feature.model.objects.filter(pk=pk).first()
    if row is None:
        return False
    if row.status in ("queued", "running"):
        return True
    return row.status == "paused" and bool(getattr(row, "auto_resume", False))


def _restore_yielded():
    """Put runs that stepped aside back at the front of the queue once the
    run that went first is done (or gone). Called under ``_lock`` right
    before a claim, so an interrupted run resumes ahead of everything that
    merely waits."""
    for feature in _features.values():
        if not feature.can_yield:
            continue
        for row in feature.model.objects.filter(status="paused", auto_resume=True, **feature.filter_kwargs):
            if _run_pending(row.yielded_for_feature, row.yielded_for_id):
                continue
            ranks = [r.queue_rank for _f, r in queued_runs() if r.queue_rank is not None]
            front = (min(ranks) if ranks else 1) - 1
            feature.model.objects.filter(pk=row.pk, status="paused", auto_resume=True).update(
                status="queued", auto_resume=False, queue_rank=front,
                yielded_for_feature="", yielded_for_id=None, yielded_for_label="",
                progress_message="Resuming...",
            )


# ---------------------------------------------------------------------------
# The dispatcher
# ---------------------------------------------------------------------------

def _first_queued():
    """(feature, row) at the front of the queue, or None."""
    rows = queued_runs()
    return rows[0] if rows else None


def _claim_next():
    """Call with ``_lock`` held. Atomically flips the first queued row to
    running and returns (feature, pk), or None when the lane is busy or
    nothing waits."""
    if _active:                           # a worker of this process is still finishing
        return None
    if busy_reason() is not None:         # a ranking refresh holds the Apple budget
        return None
    for feature in _features.values():    # global lane: any running row blocks (MCP included)
        if feature.model.objects.filter(status="running").exists():
            return None
    while True:
        nxt = _first_queued()
        if nxt is None:
            return None
        feature, row = nxt
        # The atomic claim; a row removed meanwhile yields 0. Progress is
        # left alone so a resumed keyword search keeps its place.
        claimed = feature.model.objects.filter(pk=row.pk, status="queued").update(
            status="running", started_at=timezone.now(),
        )
        if claimed:
            return feature, row.pk


def kick():
    """Start the next queued run if the lane is free. Safe to call any time.

    Returns the pk it started, or None.
    """
    _ensure_features()
    with _lock:
        _rank_unranked()
        _restore_yielded()
        claimed = _claim_next()
    if claimed is None:
        return None
    feature, pk = claimed
    # Start OUTSIDE the lock: threading.Lock is not reentrant, and the test
    # idiom _SyncThread runs the worker inline inside start().
    thread = threading.Thread(target=_worker, args=(feature, pk),
                              daemon=True, name=f"run-{feature.key}-{pk}")
    thread.start()
    return pk


def _worker(feature, pk):
    with _lock:
        _active.add((feature.key, pk))
    try:
        feature.execute(pk)
    except Exception:                     # execute() handles its own errors; this is the backstop
        logger.exception("Run %s/%s crashed", feature.key, pk)
        feature.model.objects.filter(pk=pk, status="running").update(
            status="failed", error_message="Unexpected error - see the app log.",
            progress_message="Error",
        )
    finally:
        with _lock:
            _active.discard((feature.key, pk))
        kick()                            # chain to the next queued run; no lock held here


# ---------------------------------------------------------------------------
# Startup
# ---------------------------------------------------------------------------

def should_resume_on_ready(argv, environ, is_native) -> bool:
    """Whether ``AppConfig.ready()`` should resume the queue in this process.

    Only a server's worker process qualifies:
    - the native app resumes explicitly after migrations (``desktop/main.py``);
    - "runserver" in argv excludes every management command, the test runner,
      AND the MCP process (whose Django bootstrap also runs ready());
    - the autoreloader's parent process must not execute runs, so we require
      either --noreload or RUN_MAIN=true (set in the reloader's child);
    - gunicorn (the Docker image) qualifies because ``Dockerfile`` runs it
      with exactly one worker, so there is one process to own the lane.
    """
    if is_native:
        return False
    argv = list(argv or [])
    if argv and os.path.basename(str(argv[0])) == "gunicorn":
        return True
    if "runserver" not in argv:
        return False
    if "--noreload" in argv:
        return True
    return (environ or {}).get("RUN_MAIN") == "true"


def resume_after_startup():
    """Continue what the previous process left behind, then start the queue.

    A row left "running" has no thread behind it any more (the process that
    owned it is gone). AI runs are marked failed with a message that invites
    a retry; a keyword search goes back to the front of the queue and
    continues from the first keyword that was not finished (its feature's
    ``interrupted`` hook). Runs that were still queued simply resume.

    Runs on every start in every edition (native app, runserver worker,
    gunicorn worker). Never raises: startup must not be able to crash here.
    """
    try:
        _ensure_features()
        for feature in _features.values():
            stale = feature.model.objects.filter(status="running", **feature.filter_kwargs)
            if feature.interrupted is not None:
                feature.interrupted(stale)
            else:
                stale.update(
                    status="failed",
                    error_message=INTERRUPTED_MESSAGE,
                    progress_message="Interrupted",
                )
        kick()
    except Exception:
        logger.exception("Could not resume the run queue after startup")
