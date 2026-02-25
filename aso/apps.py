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

        from .scheduler import start_scheduler

        start_scheduler()
