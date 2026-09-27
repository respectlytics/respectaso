"""The history re-score starts only once the schema is migrated.

The Mac app calls django.setup() (which runs AsoConfig.ready()) before its
own migrate, so a re-score started from ready() raced the migration: on a
new install every step failed with "no such column" and waited for the next
launch. The Mac app now starts it after migrate; every other process (the
Docker image migrates first, in a separate process) starts it from ready().
"""

import inspect
import os
from unittest import mock

from django.apps import apps
from django.test import SimpleTestCase, override_settings

from aso import popularity


def _run_ready(argv):
    with mock.patch("sys.argv", argv), \
         mock.patch("aso.apple_ads.storage.migrate_legacy_settings"), \
         mock.patch("aso.scheduler.start_scheduler"), \
         mock.patch.dict(os.environ, {"RESPECTASO_DISABLE_SCHEDULER": "1"}), \
         mock.patch("aso.popularity.start_history_upgrade") as start:
        apps.get_app_config("aso").ready()
    return start


class StartupOrderTest(SimpleTestCase):
    @override_settings(IS_NATIVE_APP=True)
    def test_the_mac_app_leaves_it_to_main(self):
        self.assertFalse(_run_ready(["RespectASO"]).called)

    @override_settings(IS_NATIVE_APP=False)
    def test_a_server_starts_it_from_ready(self):
        self.assertEqual(_run_ready(["gunicorn"]).call_count, 1)

    def test_the_mac_app_starts_it_after_migrate(self):
        from desktop import main

        source = inspect.getsource(main.main)
        self.assertLess(source.index('call_command("migrate"'), source.index("start_history_upgrade()"))

    def test_the_scratch_gate_keeps_it_off(self):
        with mock.patch.dict(os.environ, {"RESPECTASO_DISABLE_SCHEDULER": "1"}), \
             mock.patch("threading.Thread") as thread:
            popularity.start_history_upgrade()
        self.assertFalse(thread.called)

    def test_it_runs_every_step_on_one_thread(self):
        with mock.patch.dict(os.environ, {"RESPECTASO_DISABLE_SCHEDULER": "0"}), \
             mock.patch("threading.Thread") as thread:
            popularity.start_history_upgrade()
        thread.assert_called_once_with(target=popularity.upgrade_stored_history, daemon=True,
                                       name="history-upgrade")
        thread.return_value.start.assert_called_once_with()
