import sys

from django.apps import AppConfig


class AsoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "aso"
    verbose_name = "ASO Keyword Research"

    def ready(self):
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

        # One-time history re-score when the estimator version bumps
        # (estimate v2 calibration). Background thread: DB work that must
        # never delay startup; idempotent via the stored version marker.
        # Shares the scheduler's env gate so scratch/E2E servers never
        # mutate seeded data.
        import os
        import threading

        if os.environ.get("RESPECTASO_DISABLE_SCHEDULER") != "1":
            from .popularity import maybe_upgrade_estimator_version

            threading.Thread(
                target=maybe_upgrade_estimator_version,
                daemon=True,
                name="estimator-upgrade",
            ).start()

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
        from django.conf import settings

        from . import run_queue

        if (os.environ.get("RESPECTASO_DISABLE_SCHEDULER") != "1"
                and run_queue.should_resume_on_ready(sys.argv, os.environ, settings.IS_NATIVE_APP)):
            # On a thread: resuming imports the feature modules (in Pro the
            # LLM SDKs), and startup must not wait for that.
            threading.Thread(target=run_queue.resume_after_startup, daemon=True,
                             name="run-queue-resume").start()
