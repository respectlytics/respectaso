"""settings.json is written whole or not at all, by one writer at a time (aso/json_file.py).

Before the 3.2.2 release a Test model press in the Docker edition wiped every saved API key: a thread
saving settings truncated the file and then wrote it, the request read it in between, found it empty,
fell back to the defaults and saved them back. These tests write and read the file from several
threads at once and check that no reader ever meets an empty or half written file and that no
owner's keys are lost.
"""

import json
import os
import tempfile
import threading
from pathlib import Path
from unittest import mock

from django.test import SimpleTestCase, override_settings

from aso.apple_ads import storage
from aso.json_file import write_json

PADDING = {f"note_{i}": "x" * 200 for i in range(400)}  # about 90 KB: a write takes long enough to be caught


class SettingsFileWritesTest(SimpleTestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "settings.json"
        self.path.write_text(json.dumps({"api_keys": {"openai": "sk-kept"}, **PADDING}))
        override = override_settings(DATA_DIR=Path(self._tmp.name))
        override.enable()
        self.addCleanup(override.disable)
        storage.reset_cache()
        self.addCleanup(storage.reset_cache)

    def test_a_reader_never_meets_a_half_written_file(self):
        stop = threading.Event()
        torn = []

        def read():
            while not stop.is_set():
                try:
                    data = json.loads(self.path.read_text(encoding="utf-8"))
                except ValueError:
                    torn.append(1)
                    continue
                if data.get("api_keys", {}).get("openai") != "sk-kept":
                    torn.append(data)

        reader = threading.Thread(target=read)
        reader.start()
        try:
            for i in range(150):
                storage.save_apple_settings(apple_ads={"last_sync_status": f"run {i}"})
        finally:
            stop.set()
            reader.join(10)
        self.assertEqual(torn, [])

    def test_two_owners_writing_at_once_keep_each_others_keys(self):
        def apple():
            for i in range(60):
                storage.save_apple_settings(apple_ads={"last_sync_status": f"run {i}"})

        def ui():
            for i in range(60):
                storage.save_block("ui", {"choice": i})

        threads = [threading.Thread(target=apple), threading.Thread(target=ui)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(30)
        data = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(data["api_keys"], {"openai": "sk-kept"})
        self.assertEqual(data["apple_ads"]["last_sync_status"], "run 59")
        self.assertEqual(data["ui"], {"choice": 59})

    def test_the_file_is_the_owners_alone_and_no_temporary_file_stays(self):
        write_json(self.path, {"a": 1})
        self.assertEqual(os.stat(self.path).st_mode & 0o777, 0o600)
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["settings.json"])

    def test_a_failed_write_leaves_the_old_file_and_no_temporary_file(self):
        with mock.patch("aso.json_file.os.replace", side_effect=OSError("disk full")), \
                self.assertRaises(OSError):
            write_json(self.path, {"a": 1})
        self.assertEqual(json.loads(self.path.read_text())["api_keys"], {"openai": "sk-kept"})
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["settings.json"])
