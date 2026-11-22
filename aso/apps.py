import sys

from django.apps import AppConfig
from django.db.models.signals import post_migrate


def after_apps_ready(target, timeout: float = 60.0):
    """``target``, to run on a thread started from ``ready()``: it waits until
    Django has finished loading every app before it touches the database.

    A thread started in ``ready()`` runs while the other apps are still
    loading, and its first query made Django warn "Accessing the database
    during app initialization is discouraged" in the Docker edition's log
    (the history upgrade, 2026-11-22). Waits at most ``timeout`` seconds.
    """
    def run():
        import time

        from django.apps import apps

        deadline = time.monotonic() + timeout
        while not apps.ready and time.monotonic() < deadline:
            time.sleep(0.05)
        target()

    return run


def _ensure_history_triggers(sender, using, **kwargs):
    """After every migrate, the Dashboard's change count keeps counting
    (aso/history_revision.py): a migration that rebuilds a table drops its
    triggers, and this puts them back."""
    from .history_revision import ensure_triggers

    ensure_triggers(using)


class AsoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "aso"
    verbose_name = "ASO Keyword Research"

    def ready(self):
        # The one App Store pace of every thread and process lives in the data folder
        # (aso/throttle.py AppleGate, which imports no Django so respectaso.com can run it).
        from django.conf import settings

        from .throttle import apple_gate

        apple_gate.folder = lambda: settings.DATA_DIR

        # Before the early return below: migrate and test are exactly the
        # commands whose migrate must create the triggers.
        post_migrate.connect(_ensure_history_triggers, sender=self)

        # Deleted data gives its disk space back (aso/disk_space.py): the
        # database is converted after migrate, and a deleted keyword's
        # earlier day reads go with it.
        from django.db.models.signals import post_delete

        from . import day_reads, disk_space
        from .models import Keyword

        post_migrate.connect(disk_space.convert_after_migrate, sender=self)
        if day_reads.forget_untracked not in disk_space.after_keywords_deleted:
            disk_space.after_keywords_deleted.append(day_reads.forget_untracked)
        post_delete.connect(disk_space.keywords_deleted, sender=Keyword, dispatch_uid="aso_keywords_deleted")

        # Don't start background work during management commands. "test" is
        # in the set because these hooks fire before the test database exists:
        # the estimator upgrade thread would log "no such table" on every run
        # and the scheduler thread would hit the DB mid-suite. Tests that
        # cover the hooks call them directly.
        skip_commands = {"migrate", "makemigrations", "collectstatic", "createsuperuser", "shell", "test"}
        if any(cmd in sys.argv for cmd in skip_commands):
            return

        # One-time settings.json upgrade (cookie-era Apple Ads -> v1 API).
        # Idempotent and file-only; must run before any request reads the
        # connection state.
        from .apple_ads.storage import migrate_legacy_settings

        migrate_legacy_settings()

        import os
        import threading

        # The MCP server (aso_pro/mcp/bootstrap.py) is a second process on
        # the same database. It must never do the day's background work: its
        # scheduler refreshed every keyword a second time next to the Mac
        # app's (each process only sees its own "running" flag), and it raced
        # the history upgrade. The Mac app or the Docker server does that work.
        if os.environ.get("RESPECTASO_PROCESS") == "mcp":
            return

        # One-time history re-score when a version marker bumps, in one
        # background thread. The Mac app starts it itself once its migrations
        # have run (desktop/main.py): started from here it raced them, and on
        # a new install or an upgrade that adds a column every step failed
        # with "no such column" until the next launch. The Docker image
        # migrates in a separate process before gunicorn starts.
        if not settings.IS_NATIVE_APP:
            from .popularity import start_history_upgrade

            start_history_upgrade()

        from .scheduler import start_scheduler

        start_scheduler()

        # Resume the run queue (keyword searches in both editions, the AI runs
        # in Pro): a search that was executing when the app was last closed
        # continues from the first keyword that was not finished, and
        # anything still queued starts again. Only a server's worker process
        # may do this - see run_queue.should_resume_on_ready. The native app
        # calls resume_after_startup() itself, after migrations have run.
        # Shares the scheduler's env gate so the /verify scratch server never
        # starts real Apple traffic on launch.
        from . import run_queue

        if (os.environ.get("RESPECTASO_DISABLE_SCHEDULER") != "1"
                and run_queue.should_resume_on_ready(sys.argv, os.environ, settings.IS_NATIVE_APP)):
            # On a thread: resuming imports the feature modules (in Pro the
            # LLM SDKs), and startup must not wait for that.
            threading.Thread(target=after_apps_ready(run_queue.resume_after_startup), daemon=True,
                             name="run-queue-resume").start()
