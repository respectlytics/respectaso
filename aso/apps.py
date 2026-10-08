import sys

from django.apps import AppConfig
from django.db.models.signals import post_migrate


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

        from django.conf import settings

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
            threading.Thread(target=run_queue.resume_after_startup, daemon=True,
                             name="run-queue-resume").start()
