import sys

from django.apps import AppConfig


class AsoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "aso"
    verbose_name = "ASO Keyword Research"

    def ready(self):
        # Don't start the scheduler during management commands
        skip_commands = {"migrate", "makemigrations", "collectstatic", "createsuperuser", "shell"}
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
